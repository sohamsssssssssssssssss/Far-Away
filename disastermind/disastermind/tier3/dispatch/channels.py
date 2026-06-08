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
import uuid
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

from ...core.config import Settings
from ...core.contracts import utcnow_iso

log = logging.getLogger("disastermind.dispatch.channels")


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
    gracefully to a recorded send rather than attempting (and failing) a call.
    """

    #: stable channel key matched against ``payload["channel"]`` by the router.
    name: str = "channel"
    settings: Settings = field(default_factory=Settings)
    dry_run: bool = True
    #: in-memory trail of every recorded/sent receipt (handy for tests/audit).
    outbox: list[dict[str, Any]] = field(default_factory=list)

    # -- transport hook ----------------------------------------------------
    @abc.abstractmethod
    def _deliver(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Attempt real delivery; only called when not in dry-run.

        Implementations lazily import their SDK inside this method and must fall
        back to a recorded receipt (never raise) when the SDK/creds are absent.
        """

    @abc.abstractmethod
    def build(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Pure helper: render the wire form of ``payload`` without sending."""

    # -- public API --------------------------------------------------------
    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Deliver ``payload`` (or record the would-be send in dry-run)."""
        recipients = _recipients(payload)
        if self.dry_run:
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
            receipt = self._deliver(payload)
        except Exception as exc:  # never let a transport error escape (Step 10)
            log.exception("%s delivery raised; recording failure", self.name)
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
        sid = self.settings.twilio_sid
        token = self.settings.twilio_token
        from_no = payload.get("from") or self.settings.twilio_sid[:0] or "DISASTERMIND"
        if not (sid and token):
            return _receipt(
                self.name, "recorded", recipients, provider="twilio",
                dry_run=False, detail="no twilio credentials; recorded only",
            )
        try:
            from twilio.rest import Client  # type: ignore

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
        except Exception as exc:
            log.warning("twilio unavailable (%s); recording send", exc)
            return _receipt(
                self.name, "recorded", recipients, provider="twilio",
                dry_run=False, detail=f"twilio degraded: {type(exc).__name__}",
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
        key = self.settings.fcm_key
        if not key:
            return _receipt(
                self.name, "recorded", recipients, provider="fcm",
                dry_run=False, detail="no FCM server key; recorded only",
            )
        try:
            import httpx  # type: ignore

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
        except Exception as exc:
            log.warning("fcm/httpx unavailable (%s); recording send", exc)
            return _receipt(
                self.name, "recorded", recipients, provider="fcm",
                dry_run=False, detail=f"fcm degraded: {type(exc).__name__}",
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
        endpoint = self.settings.iridium_endpoint
        if not endpoint:
            return _receipt(
                self.name, "recorded", recipients, provider="iridium",
                dry_run=False, detail="no iridium endpoint; recorded only",
            )
        try:
            import httpx  # type: ignore

            resp = httpx.post(endpoint, json=self.build(payload), timeout=15.0)
            ok = resp.status_code < 400
            return _receipt(
                self.name, "sent" if ok else "failed", recipients, provider="iridium",
                dry_run=False, detail=f"http {resp.status_code}",
            )
        except Exception as exc:
            log.warning("iridium gateway unavailable (%s); recording send", exc)
            return _receipt(
                self.name, "recorded", recipients, provider="iridium",
                dry_run=False, detail=f"iridium degraded: {type(exc).__name__}",
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

    def build(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"transport": "cap", "xml": self.to_xml(payload)}

    def to_xml(self, payload: dict[str, Any]) -> str:
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
        endpoint = payload.get("cap_endpoint")
        xml = self.to_xml(payload)
        if not endpoint:
            r = _receipt(
                self.name, "recorded", recipients or ["broadcast"], provider="cap",
                dry_run=False, detail="no CAP aggregator endpoint; XML rendered only",
            )
            r["wire"] = {"transport": "cap", "xml": xml}
            return r
        try:
            import httpx  # type: ignore

            resp = httpx.post(
                endpoint, content=xml.encode("utf-8"),
                headers={"Content-Type": "application/xml"}, timeout=15.0,
            )
            ok = resp.status_code < 400
            return _receipt(
                self.name, "sent" if ok else "failed",
                recipients or ["broadcast"], provider="cap",
                dry_run=False, detail=f"http {resp.status_code}",
            )
        except Exception as exc:
            log.warning("CAP aggregator unavailable (%s); recording send", exc)
            return _receipt(
                self.name, "recorded", recipients or ["broadcast"], provider="cap",
                dry_run=False, detail=f"cap degraded: {type(exc).__name__}",
            )


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
        # Stub gateway: there is no SDK/network path, so we always record the
        # radio traffic for an operator. This is deliberate (PRD Step 8 stub).
        recipients = _recipients(payload)
        r = _receipt(
            self.name, "recorded", recipients or ["NET"], provider="radio-gateway-stub",
            dry_run=False, detail="queued for radio operator readout",
        )
        r["wire"] = self.build(payload)
        return r
