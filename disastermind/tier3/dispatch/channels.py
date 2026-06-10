"""Notification-dispatch channel adapters (PRD Step 8, Tier 3).

Tier 3 dispatch agents have **no decision authority** — they only EXECUTE the
notification orders that the Commander (Tier 1) publishes on ``Topic.DISPATCH``.
Each channel knows how to physically deliver a message over one transport:

  * :class:`SmsChannel`        — Twilio / Airtel / BSNL SMS gateway.
  * :class:`FcmPushChannel`    — Firebase Cloud Messaging push to the field app.
  * :class:`IridiumChannel`    — Iridium satellite short-burst messaging for
                                 no-coverage zones.
  * :class:`CapChannel`        — Common Alerting Protocol XML emergency broadcast.
  * :class:`FieldRadioChannel` — field-radio gateway adapter (stub transport).

Hard rules honoured here (see module CLAUDE.md):
  * Heavy SDKs (``twilio``, ``httpx``) are imported **lazily inside ``send``**,
    wrapped in ``try/except``, with a deterministic stdlib FALLBACK.
  * **No network calls** at import time or on any path the tests hit. Real
    delivery only fires when credentials are present *and* ``dry_run`` is False;
    otherwise the channel records the would-be send and returns a receipt dict.
  * Pure ``build()`` helpers expose the serialised payload so callers/tests can
    inspect what *would* be sent without any transport.

Every ``send(payload) -> receipt`` returns a JSON-able receipt dict with a
stable shape so the router can audit and aggregate results:

    {"channel","status","recipients","provider","dry_run","detail",
     "message_id","sent_at"}

``status`` is one of ``"sent"`` | ``"recorded"`` | ``"failed"``.
"""
from __future__ import annotations

import abc
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

from ...core.config import Settings
from ...core.contracts import utcnow_iso

log = logging.getLogger("disastermind.dispatch.channels")


# --------------------------------------------------------------------- live flag
def _live_from_env() -> bool:
    """Read the global live-dispatch switch (``DM_DISPATCH_LIVE``).

    Default is False — real sends are OFF unless explicitly opted into, so the
    package imports and the suite runs network-free (PRD Step 10).
    """
    return os.environ.get("DM_DISPATCH_LIVE", "").lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------- helpers
def _recipients(payload: dict[str, Any]) -> list[str]:
    """Normalise the recipient list out of a DISPATCH payload."""
    rec = payload.get("recipients") or []
    if isinstance(rec, str):
        return [rec]
    return [str(r) for r in rec]


def _receipt(
    channel: str,
    status: str,
    recipients: list[str],
    provider: str,
    dry_run: bool,
    detail: str,
    message_id: str | None = None,
) -> dict[str, Any]:
    """Build the canonical, JSON-able receipt dict returned by every channel."""
    return {
        "kind": "dispatch_receipt",
        "channel": channel,
        "status": status,
        "recipients": recipients,
        "provider": provider,
        "dry_run": dry_run,
        "detail": detail,
        "message_id": message_id or f"dm-{uuid.uuid4().hex[:12]}",
        "sent_at": utcnow_iso(),
    }


# ----------------------------------------------------------------- base channel
@dataclass
class Channel(abc.ABC):
    """Base notification channel (PRD Step 8).

    ``dry_run`` forces the record-only path regardless of credentials, which is
    the mode the test-suite and any degraded/offline operation runs in. When
    ``dry_run`` is False but credentials are missing the channel *still* degrades
    gracefully to a recorded receipt rather than attempting (and failing) a call.

    Going LIVE
    ----------
    Real delivery only fires when ``dry_run`` is False **and** the global live
    switch is on — either passed explicitly via ``live`` or, when ``live`` is
    ``None``, read from ``DM_DISPATCH_LIVE`` (default off). This double gate keeps
    the default offline so the existing suite stays green.

    Resilience
    ----------
    When not in dry-run the real send is driven through :meth:`_send_live`, which
    lazily wraps :meth:`_deliver` in an :mod:`disastermind.ops` ``CircuitBreaker``
    (+ optional ``retry``) so a flapping SMS/push/satellite provider fails fast
    instead of hammering a sick dependency. ``ops`` is imported lazily; if it is
    absent the call is made plainly. A per-channel breaker can be injected (tests
    pass one with a low threshold to assert it opens on repeated failure).

    Testing seam
    ------------
    ``transport`` is an optional injected callable ``(payload) -> receipt`` used
    *instead of* the SDK path in :meth:`_deliver` implementations. Tests inject a
    stub to assert a live send calls it exactly once — with no real socket.
    """

    #: stable channel key matched against ``payload["channel"]`` by the router.
    name: str = "channel"
    settings: Settings = field(default_factory=Settings)
    dry_run: bool = True
    #: tri-state live switch; ``None`` defers to ``DM_DISPATCH_LIVE`` (default off).
    live: bool | None = None
    #: optional injected transport ``(payload) -> receipt`` (test/seam override).
    transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    #: optional injected CircuitBreaker; created lazily on first live send if None.
    breaker: Any | None = None
    #: how many tries each live send gets (1 == no retry); used if ops.retry present.
    send_attempts: int = 1
    #: in-memory trail of every recorded/sent receipt (handy for tests/audit).
    outbox: list[dict[str, Any]] = field(default_factory=list)

    # -- transport hook ----------------------------------------------------
    @abc.abstractmethod
    def _deliver(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Attempt real delivery; only called when live.

        Implementations lazily import their SDK inside this method and must fall
        back to a recorded receipt (never raise) when the SDK/creds are absent.
        When ``self.transport`` is set they delegate to it instead of the SDK so
        tests can drive the live path without a network.
        """

    @abc.abstractmethod
    def build(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Pure helper: render the wire form of ``payload`` without sending."""

    # -- live / resilience -------------------------------------------------
    def is_live(self) -> bool:
        """True iff this channel should attempt a real send for the next call.

        Dry-run always wins (record-only). Otherwise an explicit ``live`` flag
        wins; when unset we consult ``DM_DISPATCH_LIVE`` (default False).
        """
        if self.dry_run:
            return False
        if self.live is not None:
            return bool(self.live)
        return _live_from_env()

    def _ensure_breaker(self) -> Any | None:
        """Lazily build (and cache) a CircuitBreaker; None if ops is absent."""
        if self.breaker is not None:
            return self.breaker
        try:
            from ...ops import CircuitBreaker  # lazy — ops is optional
        except Exception:  # pragma: no cover - ops always present in-repo
            return None
        # Three consecutive transport failures open the breaker by default.
        self.breaker = CircuitBreaker(failure_threshold=3, reset_timeout=30.0)
        return self.breaker

    def _send_live(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run :meth:`_deliver` through the breaker (+retry) if ops is available.

        A failed *transport* is signalled to the breaker by ``_deliver`` raising;
        a recorded/degraded receipt (returned, not raised) does **not** trip it.
        If ``ops`` is unavailable the call is made plainly.
        """
        call = self._deliver
        # Optional retry wrapper (deterministic; no real sleep injected here as a
        # single attempt is the default — opt into more via send_attempts).
        if self.send_attempts > 1:
            try:
                from ...ops import retry  # lazy

                call = retry(attempts=self.send_attempts, sleep=lambda _d: None)(call)
            except Exception:  # pragma: no cover - ops always present in-repo
                pass
        breaker = self._ensure_breaker()
        if breaker is None:
            return call(payload)
        return breaker.call(call, payload)

    # -- public API --------------------------------------------------------
    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Deliver ``payload`` (or record the would-be send when not live)."""
        recipients = _recipients(payload)
        if not self.is_live():
            receipt = _receipt(
                self.name,
                "recorded",
                recipients,
                provider="dry-run",
                dry_run=True,
                detail=f"would send {len(payload.get('body', '') or '')} chars to "
                f"{len(recipients)} recipient(s)",
            )
            receipt["wire"] = self.build(payload)
            self.outbox.append(receipt)
            return receipt
        try:
            receipt = self._send_live(payload)
        except Exception as exc:  # never let a transport/breaker error escape (Step 10)
            log.warning("%s live send failed; recording failure (%s)", self.name, exc)
            receipt = _receipt(
                self.name,
                "failed",
                recipients,
                provider="unknown",
                dry_run=False,
                detail=f"{type(exc).__name__}: {exc}",
            )
        self.outbox.append(receipt)
        return receipt


# ------------------------------------------------------------------------- SMS
@dataclass
class SmsChannel(Channel):
    """SMS via Twilio (Airtel/BSNL share the same record-only fallback).

    PRD Step 8: SMS is the lowest-common-denominator channel for the public.
    """

    name: str = "sms"

    def build(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = str(payload.get("body", ""))
        return {
            "transport": "sms",
            "segments": max(1, (len(body) + 152) // 153),  # GSM concat estimate
            "to": _recipients(payload),
            "text": body,
        }

    def _deliver(self, payload: dict[str, Any]) -> dict[str, Any]:
        recipients = _recipients(payload)
        body = str(payload.get("body", ""))
        # Injected transport seam (tests / custom gateway): a raised exception is
        # a real transport failure and is allowed to propagate so the breaker
        # trips; a returned receipt is taken as-is.
        if self.transport is not None:
            return self.transport(self.build(payload))
        sid = self.settings.twilio_sid
        token = self.settings.twilio_token
        from_no = payload.get("from") or "DISASTERMIND"
        if not (sid and token):
            return _receipt(
                self.name, "recorded", recipients, provider="twilio",
                dry_run=False, detail="no twilio credentials; recorded only",
            )
        try:
            from twilio.rest import Client  # type: ignore
        except Exception as exc:
            # SDK simply not installed — degrade, do NOT trip the breaker.
            log.warning("twilio SDK absent (%s); recording send", exc)
            return _receipt(
                self.name, "recorded", recipients, provider="twilio",
                dry_run=False, detail=f"twilio sdk absent: {type(exc).__name__}",
            )
        # A genuine network/auth error inside the SDK is allowed to raise so the
        # circuit breaker counts it; the base send() turns it into a "failed".
        client = Client(sid, token)
        last_sid = None
        for to in recipients:
            msg = client.messages.create(body=body, from_=str(from_no), to=to)
            last_sid = getattr(msg, "sid", None)
        return _receipt(
            self.name, "sent", recipients, provider="twilio",
            dry_run=False, detail=f"sent {len(recipients)} SMS",
            message_id=last_sid,
        )


# ------------------------------------------------------------------- FCM push
@dataclass
class FcmPushChannel(Channel):
    """Firebase Cloud Messaging push to the NDRF/SDRF field app (PRD Step 8)."""

    name: str = "push"

    def build(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "transport": "fcm",
            "registration_ids": _recipients(payload),
            "notification": {
                "title": payload.get("title", "DisasterMind Alert"),
                "body": str(payload.get("body", "")),
            },
            "data": payload.get("data", {"order": payload.get("order")}),
        }

    def _deliver(self, payload: dict[str, Any]) -> dict[str, Any]:
        recipients = _recipients(payload)
        if self.transport is not None:
            return self.transport(self.build(payload))
        key = self.settings.fcm_key
        if not key:
            return _receipt(
                self.name, "recorded", recipients, provider="fcm",
                dry_run=False, detail="no FCM server key; recorded only",
            )
        try:
            import httpx  # type: ignore
        except Exception as exc:
            log.warning("httpx absent (%s); recording FCM send", exc)
            return _receipt(
                self.name, "recorded", recipients, provider="fcm",
                dry_run=False, detail=f"httpx absent: {type(exc).__name__}",
            )
        # POST to FCM; a transport error raises (trips the breaker), an HTTP >=400
        # is a soft "failed" receipt (server rejected us, not a flapping link).
        resp = httpx.post(
            "https://fcm.googleapis.com/fcm/send",
            headers={"Authorization": f"key={key}", "Content-Type": "application/json"},
            json=self.build(payload),
            timeout=10.0,
        )
        ok = resp.status_code < 400
        return _receipt(
            self.name, "sent" if ok else "failed", recipients, provider="fcm",
            dry_run=False, detail=f"http {resp.status_code}",
        )


# --------------------------------------------------------------------- Iridium
@dataclass
class IridiumChannel(Channel):
    """Iridium satellite short-burst messaging for no-coverage zones (Step 8).

    Used where terrestrial SMS/data is unavailable (e.g. cyclone-flattened
    coastal districts). Real delivery POSTs to a gateway endpoint via httpx;
    absent an endpoint it records the would-be burst.
    """

    name: str = "iridium"

    def build(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = str(payload.get("body", ""))
        return {
            "transport": "iridium-sbd",
            "imeis": _recipients(payload),
            # SBD mobile-terminated payloads are tightly capped; truncate safely.
            "payload": body[:270],
            "truncated": len(body) > 270,
        }

    def _deliver(self, payload: dict[str, Any]) -> dict[str, Any]:
        recipients = _recipients(payload)
        if self.transport is not None:
            return self.transport(self.build(payload))
        endpoint = self.settings.iridium_endpoint
        if not endpoint:
            return _receipt(
                self.name, "recorded", recipients, provider="iridium",
                dry_run=False, detail="no iridium endpoint; recorded only",
            )
        try:
            import httpx  # type: ignore
        except Exception as exc:
            log.warning("httpx absent (%s); recording iridium burst", exc)
            return _receipt(
                self.name, "recorded", recipients, provider="iridium",
                dry_run=False, detail=f"httpx absent: {type(exc).__name__}",
            )
        resp = httpx.post(endpoint, json=self.build(payload), timeout=15.0)
        ok = resp.status_code < 400
        return _receipt(
            self.name, "sent" if ok else "failed", recipients, provider="iridium",
            dry_run=False, detail=f"http {resp.status_code}",
        )


# ------------------------------------------------------------------------- CAP
@dataclass
class CapChannel(Channel):
    """Common Alerting Protocol (CAP 1.2) emergency broadcast (PRD Step 8).

    Renders a standards-compliant CAP XML alert. The XML is built purely
    (stdlib only) so it is always available; real broadcast to an aggregator is
    attempted via httpx only when an endpoint is configured on the payload.
    """

    name: str = "cap"
    #: CAP <sender> identity used when rendering via build_cap_alert.
    cap_sender: str = "disastermind@ndma.gov.in"

    def build(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"transport": "cap", "xml": self.to_xml(payload)}

    # ---------------------------------------------------------- public_alert path
    def to_xml(self, payload: dict[str, Any]) -> str:
        """Render a CAP 1.2 XML document for ``payload`` (no network, stdlib only).

        When the payload is a *public evacuation alert* — i.e. it carries a
        :class:`~disastermind.models.domain.DisasterEvent` (under ``"event"`` or
        ``"disaster_event"``) plus the multi-language
        :class:`~disastermind.llm.PublicAlert` copy (under ``"public_alerts"``)
        — the standards-grade :func:`disastermind.alerting.build_cap_alert`
        renderer is used (one ``<info>`` per language, hazard-mapped levels,
        optional ``<polygon>``). Otherwise we fall back to the lightweight inline
        serialiser below. Both paths are stdlib-only and always available.
        """
        xml = self._cap_from_event(payload)
        if xml is not None:
            return xml
        return self._inline_xml(payload)

    def _cap_from_event(self, payload: dict[str, Any]) -> str | None:
        """Use ``build_cap_alert`` when a DisasterEvent + PublicAlerts are present.

        Returns ``None`` (so callers fall back to the inline serialiser) when the
        payload is not a public_alert, the objects are missing/malformed, or the
        alerting package is unavailable.
        """
        if payload.get("kind") not in (None, "public_alert") and "public_alerts" not in payload:
            return None
        event = payload.get("event") or payload.get("disaster_event")
        alerts = payload.get("public_alerts")
        # We need the real DisasterEvent object (with a .kind) and PublicAlert copy.
        if event is None or alerts is None or isinstance(event, str):
            return None
        try:
            from ...alerting import build_cap_alert  # lazy; stdlib-only renderer
        except Exception as exc:  # pragma: no cover - alerting always present in-repo
            log.warning("alerting.build_cap_alert unavailable (%s); inline CAP", exc)
            return None
        area_desc = str(
            payload.get("area_desc")
            or payload.get("title")
            or (payload.get("areas") or ["Affected Zone"])[0]
        )
        polygon = payload.get("polygon")  # optional Sequence[LatLon]
        identifier = payload.get("order") or payload.get("identifier")
        try:
            cap = build_cap_alert(
                event,
                alerts,
                sender=str(payload.get("sender") or self.cap_sender),
                area_desc=area_desc,
                polygon=polygon,
                identifier=identifier,
            )
            return cap.to_xml()
        except Exception as exc:
            log.warning("build_cap_alert failed (%s); falling back to inline CAP", exc)
            return None

    def _inline_xml(self, payload: dict[str, Any]) -> str:
        """Pure CAP 1.2 XML serialiser (no network, stdlib only)."""
        identifier = payload.get("order") or f"dm-cap-{uuid.uuid4().hex[:12]}"
        severity = str(payload.get("severity", "Severe"))
        urgency = str(payload.get("urgency", "Immediate"))
        certainty = str(payload.get("certainty", "Likely"))
        event = str(payload.get("event", payload.get("title", "Disaster Alert")))
        headline = str(payload.get("title", event))
        body = str(payload.get("body", ""))
        areas = payload.get("areas") or _recipients(payload) or ["Affected Zone"]
        area_xml = "".join(
            f"    <area><areaDesc>{escape(str(a))}</areaDesc></area>\n" for a in areas
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">\n'
            f"  <identifier>{escape(str(identifier))}</identifier>\n"
            "  <sender>disastermind@ndma.gov.in</sender>\n"
            f"  <sent>{utcnow_iso()}</sent>\n"
            "  <status>Actual</status>\n"
            "  <msgType>Alert</msgType>\n"
            "  <scope>Public</scope>\n"
            "  <info>\n"
            "    <category>Met</category>\n"
            f"    <event>{escape(event)}</event>\n"
            f"    <urgency>{escape(urgency)}</urgency>\n"
            f"    <severity>{escape(severity)}</severity>\n"
            f"    <certainty>{escape(certainty)}</certainty>\n"
            f"    <headline>{escape(headline)}</headline>\n"
            f"    <description>{escape(body)}</description>\n"
            f"{area_xml}"
            "  </info>\n"
            "</alert>\n"
        )

    def _deliver(self, payload: dict[str, Any]) -> dict[str, Any]:
        recipients = _recipients(payload)
        xml = self.to_xml(payload)
        # Injected transport seam (tests / custom aggregator): a raised exception
        # propagates so the breaker trips; a returned receipt is taken as-is. The
        # rendered XML is attached to the receipt for the audit trail.
        if self.transport is not None:
            r = self.transport({"transport": "cap", "xml": xml})
            if isinstance(r, dict):
                r.setdefault("wire", {"transport": "cap", "xml": xml})
            return r
        endpoint = payload.get("cap_endpoint")
        if not endpoint:
            r = _receipt(
                self.name, "recorded", recipients or ["broadcast"], provider="cap",
                dry_run=False, detail="no CAP aggregator endpoint; XML rendered only",
            )
            r["wire"] = {"transport": "cap", "xml": xml}
            return r
        try:
            import httpx  # type: ignore
        except Exception as exc:
            log.warning("httpx absent (%s); recording CAP broadcast", exc)
            r = _receipt(
                self.name, "recorded", recipients or ["broadcast"], provider="cap",
                dry_run=False, detail=f"httpx absent: {type(exc).__name__}",
            )
            r["wire"] = {"transport": "cap", "xml": xml}
            return r
        resp = httpx.post(
            endpoint, content=xml.encode("utf-8"),
            headers={"Content-Type": "application/xml"}, timeout=15.0,
        )
        ok = resp.status_code < 400
        r = _receipt(
            self.name, "sent" if ok else "failed",
            recipients or ["broadcast"], provider="cap",
            dry_run=False, detail=f"http {resp.status_code}",
        )
        r["wire"] = {"transport": "cap", "xml": xml}
        return r


# ------------------------------------------------------------------ field radio
@dataclass
class FieldRadioChannel(Channel):
    """Field-radio gateway adapter (PRD Step 8, stub transport).

    Bridges to a VHF/HF radio gateway for teams off all data networks. No public
    SDK is assumed; this records a structured radio-traffic entry that an
    operator console can read out. Always degrades to a recorded receipt.
    """

    name: str = "radio"

    def build(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = str(payload.get("body", ""))
        return {
            "transport": "field-radio",
            "net": payload.get("net", "DM-PRIMARY"),
            "callsigns": _recipients(payload),
            "traffic": body,
            "prowords": "BREAK BREAK" if payload.get("priority") in (1, "1") else "MESSAGE",
        }

    def _deliver(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Injected gateway transport (tests / real radio bridge): a raised
        # exception propagates so the breaker trips; a receipt is taken as-is.
        if self.transport is not None:
            return self.transport(self.build(payload))
        # Stub gateway: there is no SDK/network path, so we always record the
        # radio traffic for an operator. This is deliberate (PRD Step 8 stub).
        recipients = _recipients(payload)
        r = _receipt(
            self.name, "recorded", recipients or ["NET"], provider="radio-gateway-stub",
            dry_run=False, detail="queued for radio operator readout",
        )
        r["wire"] = self.build(payload)
        return r
