"""Social-media NLP agent (PRD Step 1 Module C trigger + PRD Step 2).

PRD Step 1 (Module C, Urban Fire / Structural Collapse) lists *social-media
distress signals* among the activation triggers, and PRD Step 2 ("Real-Time
Data Ingestion") calls for a "social media NLP agent [that] detects geo-tagged
collapse keyword clusters". This module implements that edge-tier agent.

:class:`SocialNLPAgent` (``tier = Tier.EDGE``, ``decision_authority = False``)
ingests geo-tagged posts and runs a stdlib NLP pipeline:

  1. **normalise / tokenise** — lower-case, strip punctuation/URLs/mentions,
     keep Devanagari and Latin word characters so multilingual distress terms
     (e.g. ``बचाओ`` "save us") survive.
  2. **score** each post against a collapse/disaster keyword lexicon
     (collapse, trapped, building fell, fire, flood, बचाओ, …), weighting
     high-signal phrases above ambient noise.
  3. **cluster** scored posts by geo bucket
     (:meth:`disastermind.models.geo.GridCell.from_latlon`) within a sliding
     time window.
  4. when a cluster clears the activation threshold (enough corroborating posts
     with high enough mean confidence), **emit** a :data:`Topic.RAW_FEED` ALERT
     carrying a Module-C :class:`~disastermind.models.domain.DisasterEvent` at
     the cluster centroid with a confidence score. The prediction tier (Tier 2)
     interprets the signal — the edge agent observes and reports only.

Like every Tier 3 adapter the agent ships an offline :meth:`sample` fixture and
a lazy :meth:`fetch` stub (never exercised in tests, degrades to ``sample``), so
the package imports and the test-suite runs with the **standard library only**
and **no network** (PRD Step 10, graceful degradation). An optional transformers
sentiment model is imported lazily inside :meth:`_sentiment`; when it is absent
(the default) the deterministic keyword scorer is used.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ...audit.decision_log import DecisionLogger
from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
    utcnow_iso,
)
from ...models.domain import DisasterEvent, EventKind
from ...models.geo import LatLon

log = logging.getLogger("disastermind.social")

#: Informational RAW_FEED recipient (the bus is topic-routed; this mirrors the
#: ingestion adapters which address the prediction tier).
SOCIAL_RAW_FEED_RECIPIENT = "tier2.prediction"

# --------------------------------------------------------------------------- lexicon
#: Collapse/disaster keyword lexicon (PRD Step 2). Each entry maps a normalised
#: keyword/phrase to a signal weight: rescue-grade distress terms score highest,
#: generic hazard nouns lower, so corroborating high-signal posts dominate the
#: cluster confidence. Multilingual (English + Hindi/Devanagari) per the India
#: deployment context (PRD Step 1).
COLLAPSE_KEYWORDS: dict[str, float] = {
    # --- structural collapse (Module C headline trigger) ----------------
    "collapse": 1.0,
    "collapsed": 1.0,
    "building fell": 1.2,
    "building collapse": 1.2,
    "building down": 1.0,
    "wall fell": 0.9,
    "rubble": 0.9,
    "debris": 0.7,
    "trapped": 1.2,
    "stuck inside": 1.0,
    "buried": 1.1,
    "people inside": 0.9,
    # --- rescue / distress ----------------------------------------------
    "help": 0.6,
    "rescue": 0.9,
    "save us": 1.1,
    "emergency": 0.7,
    "बचाओ": 1.2,         # "save (us)"
    "मदद": 0.9,          # "help"
    "फंस": 1.0,          # "stuck/trapped" stem (फंसे/फंस गए)
    "इमारत": 0.8,        # "building"
    "गिर": 0.9,          # "fell/collapse" stem (गिर गई/गिरी)
    "मलबे": 0.9,         # "rubble/debris"
    # --- co-hazards (also valid Module A/C disaster signal) -------------
    "fire": 0.8,
    "smoke": 0.6,
    "flames": 0.8,
    "burning": 0.8,
    "flood": 0.8,
    "flooding": 0.8,
    "earthquake": 0.9,
    "tremor": 0.7,
    "injured": 0.7,
    "casualties": 0.8,
}

#: Multi-word phrases checked against the normalised text before single tokens,
#: so "building fell" scores as one strong phrase, not two weak tokens.
_PHRASES: tuple[str, ...] = tuple(
    sorted((k for k in COLLAPSE_KEYWORDS if " " in k), key=len, reverse=True)
)
#: Single-token lexicon entries (matched as substrings to catch Devanagari
#: inflectional stems and to be whitespace/punctuation tolerant).
_TOKENS: dict[str, float] = {k: v for k, v in COLLAPSE_KEYWORDS.items() if " " not in k}

#: Strip URLs and @mentions before tokenising; keep #hashtag *text*.
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_MENTION_RE = re.compile(r"[@]\w+")
#: Keep Latin + Devanagari word characters; collapse everything else to space.
_NONWORD_RE = re.compile(r"[^0-9a-zऀ-ॿ]+")


def normalise(text: str) -> str:
    """Lower-case and strip URLs/mentions/punctuation (PRD Step 2 pipeline).

    Keeps Latin and Devanagari word characters so multilingual distress terms
    survive tokenisation; everything else collapses to single spaces.
    """
    t = (text or "").lower()
    t = _URL_RE.sub(" ", t)
    t = _MENTION_RE.sub(" ", t)
    t = t.replace("#", " ")
    t = _NONWORD_RE.sub(" ", t)
    return " ".join(t.split())


def score_text(text: str) -> tuple[float, list[str]]:
    """Score normalised text against the lexicon (PRD Step 2 keyword scorer).

    Returns ``(raw_score, matched)`` where ``raw_score`` is the summed signal
    weight of every matched phrase/token and ``matched`` lists the hits. Pure,
    deterministic and stdlib-only — this is the fallback when no ML model is
    available.
    """
    norm = normalise(text)
    if not norm:
        return 0.0, []
    score = 0.0
    matched: list[str] = []
    remaining = f" {norm} "
    # Multi-word phrases first (higher signal); consume so tokens don't double-count.
    for phrase in _PHRASES:
        if phrase in norm:
            score += COLLAPSE_KEYWORDS[phrase]
            matched.append(phrase)
            remaining = remaining.replace(phrase, " ")
    # Single tokens / stems (substring match handles Devanagari inflection).
    for token, weight in _TOKENS.items():
        if token in remaining:
            score += weight
            matched.append(token)
    return round(score, 4), matched


def _confidence(raw_score: float) -> float:
    """Squash a raw lexicon score to a 0..1 confidence (saturating).

    A single strong term (~1.0) already yields a meaningful signal; multiple
    corroborating terms push confidence toward 1.0 without ever exceeding it.
    """
    if raw_score <= 0.0:
        return 0.0
    return round(min(1.0, raw_score / (raw_score + 1.0) * 2.0), 4)


# --------------------------------------------------------------------------- model
@dataclass
class SocialPost:
    """A geo-tagged social-media post the agent ingests (PRD Step 2)."""

    post_id: str
    text: str
    lat: float
    lon: float
    created_at: str  # ISO 8601
    lang: str = ""
    source: str = "social"

    def location(self) -> LatLon:
        return LatLon(self.lat, self.lon)


@dataclass
class ScoredPost:
    """A :class:`SocialPost` annotated by the NLP pipeline."""

    post: SocialPost
    raw_score: float
    confidence: float
    matched: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- agent
class SocialNLPAgent(BaseAgent):
    """Geo-tagged social-media collapse-keyword cluster detector (PRD Step 2).

    Edge-tier (Tier 3) producer with **no decision authority**. Its :meth:`tick`
    pulls a batch of posts (offline :meth:`sample` fixtures by default), scores
    each against the collapse lexicon, clusters the scored posts by geo bucket
    within a time window, and emits one :data:`Topic.RAW_FEED` ALERT per cluster
    that clears the activation threshold — carrying a Module-C
    :class:`~disastermind.models.domain.DisasterEvent` at the cluster centroid.
    """

    #: Tier 3 edge agents observe & report only — no autonomous decisions.
    tier: Tier = Tier.EDGE
    decision_authority: bool = False

    module: Module = Module.FIRE_COLLAPSE

    #: A post must clear this confidence to count toward a cluster (noise gate).
    post_confidence_floor: float = 0.45
    #: Minimum corroborating posts in one geo bucket to fire an alert (PRD
    #: Step 2 — a *cluster*, not a single tweet).
    min_cluster_size: int = 3
    #: The cluster's mean confidence must clear this to fire (activation).
    cluster_confidence_threshold: float = 0.6
    #: Geo bucket size (m) for clustering (PRD Step 3 grid convention).
    grid_size_m: int = 500

    def __init__(
        self,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        settings: Any = None,
        live: bool = False,
        name: str | None = None,
        grid_size_m: int | None = None,
        min_cluster_size: int | None = None,
        cluster_confidence_threshold: float | None = None,
        post_confidence_floor: float | None = None,
    ) -> None:
        # Pure producer: subscribes to nothing inbound.
        super().__init__(
            name=name or "social.nlp",
            bus=bus,
            logger=logger,
            subscriptions=[],
        )
        self.settings = settings
        #: ``live`` enables the (lazy, network) fetch path; tests keep it False
        #: so :meth:`sample` fixtures drive the pipeline (PRD Step 10).
        self.live = live
        if grid_size_m is not None:
            self.grid_size_m = grid_size_m
        if min_cluster_size is not None:
            self.min_cluster_size = min_cluster_size
        if cluster_confidence_threshold is not None:
            self.cluster_confidence_threshold = cluster_confidence_threshold
        if post_confidence_floor is not None:
            self.post_confidence_floor = post_confidence_floor
        self._tick_count = 0

    # ------------------------------------------------------------------ hooks
    def handle(self, message: Message) -> list[Message]:
        """Pure producer — reacts to nothing inbound (Tier 3, PRD Step 2)."""
        return []

    # ----------------------------------------------------------------- ingest
    def sample(self) -> list[dict[str, Any]]:
        """Offline fixture: a tight geo-temporal collapse cluster + noise.

        Three corroborating distress posts within one ~500 m grid bucket
        (a building collapse), plus an unrelated off-topic post and a lone
        far-away post that must NOT, on its own, trip an alert.
        """
        return [
            {
                "post_id": "tw-001",
                "text": "Building collapsed near Lajpat Nagar market! people trapped under rubble #help",
                "lat": 28.5670,
                "lon": 77.2430,
                "created_at": "2026-06-08T07:10:00+00:00",
                "lang": "en",
            },
            {
                "post_id": "tw-002",
                "text": "इमारत गिर गई, लोग मलबे में फंसे हैं बचाओ!!",  # building fell, people trapped, save us
                "lat": 28.5675,
                "lon": 77.2438,
                "created_at": "2026-06-08T07:11:30+00:00",
                "lang": "hi",
            },
            {
                "post_id": "tw-003",
                "text": "Whole building came down, debris everywhere, need rescue teams now",
                "lat": 28.5681,
                "lon": 77.2442,
                "created_at": "2026-06-08T07:12:45+00:00",
                "lang": "en",
            },
            {
                "post_id": "tw-004",
                "text": "loving this sunny weather, perfect for a walk in the park",
                "lat": 28.6139,
                "lon": 77.2090,
                "created_at": "2026-06-08T07:13:00+00:00",
                "lang": "en",
            },
            {
                "post_id": "tw-005",
                "text": "thought I heard a loud crash somewhere, hope everyone is ok",
                "lat": 19.0760,
                "lon": 72.8777,
                "created_at": "2026-06-08T07:14:00+00:00",
                "lang": "en",
            },
        ]

    def fetch(self) -> list[dict[str, Any]]:  # pragma: no cover - network path
        """Live pull of geo-tagged posts (lazy ``httpx``). Degrades to sample.

        Stub for a real social-firehose / search API. Any failure (no key, no
        network, library absent) falls back to :meth:`sample` so the edge node
        degrades gracefully rather than crashing (PRD Step 10).
        """
        base = getattr(self.settings, "social_search_url", None)
        if not base:
            return self.sample()
        try:
            import httpx  # type: ignore

            resp = httpx.get(base, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            posts = data if isinstance(data, list) else data.get("posts", [])
            return posts or self.sample()
        except Exception:
            log.exception("social fetch failed; using sample()")
            return self.sample()

    def _pull(self) -> list[dict[str, Any]]:
        """Acquire a raw post batch: live fetch when enabled, else sample."""
        if not self.live:
            return self.sample()
        try:  # pragma: no cover - network path excluded from tests
            return self.fetch()
        except Exception:  # pragma: no cover
            log.exception("%s live fetch failed; degrading to sample()", self.name)
            return self.sample()

    @staticmethod
    def _coerce(raw: list[dict[str, Any]]) -> list[SocialPost]:
        """Normalise raw post dicts (provider-agnostic) into :class:`SocialPost`."""
        posts: list[SocialPost] = []
        for r in raw or []:
            if not isinstance(r, dict):
                continue
            geo = r.get("geo") or r.get("coordinates") or {}
            lat = r.get("lat", geo.get("lat") if isinstance(geo, dict) else None)
            lon = r.get("lon", geo.get("lon") if isinstance(geo, dict) else None)
            if lat is None or lon is None:
                continue  # not geo-tagged → unusable for clustering
            posts.append(
                SocialPost(
                    post_id=str(r.get("post_id") or r.get("id") or f"post:{lat},{lon}"),
                    text=str(r.get("text") or r.get("body") or ""),
                    lat=float(lat),
                    lon=float(lon),
                    created_at=str(r.get("created_at") or r.get("timestamp") or utcnow_iso()),
                    lang=str(r.get("lang") or r.get("language") or ""),
                    source=str(r.get("source") or "social"),
                )
            )
        return posts

    # ------------------------------------------------------------------ score
    def _sentiment(self, text: str) -> float | None:
        """Optional transformers distress sentiment (lazy). ``None`` if absent.

        When ``transformers`` is importable we *could* boost confidence with a
        learned negative-sentiment signal; in the default stdlib environment the
        import fails and we return ``None`` so the keyword scorer is used
        verbatim (PRD Step 10 graceful degradation). Never invoked in tests.
        """
        if not getattr(self, "use_transformers", False):
            # Offline-by-default: NEVER auto-load a remote model or hit the network
            # inside the coordination loop. Opt in explicitly (set
            # ``agent.use_transformers = True``) to blend a learned sentiment
            # signal; otherwise the deterministic keyword scorer is used verbatim
            # (PRD Step 10 graceful degradation).
            return None
        try:  # pragma: no cover - optional heavy dependency, never in tests
            from transformers import pipeline  # type: ignore

            clf = getattr(self, "_clf", None)
            if clf is None:
                clf = pipeline("sentiment-analysis")
                self._clf = clf
            res = clf(text[:512])[0]
            neg = float(res.get("score", 0.0))
            return neg if str(res.get("label", "")).upper().startswith("NEG") else 1.0 - neg
        except Exception:
            return None

    def score_post(self, post: SocialPost) -> ScoredPost:
        """Run the NLP pipeline on one post (PRD Step 2).

        Keyword scorer (always) optionally blended with a transformers
        sentiment signal when the library is present.
        """
        raw, matched = score_text(post.text)
        confidence = _confidence(raw)
        sent = self._sentiment(post.text)
        if sent is not None and confidence > 0.0:  # pragma: no cover - optional path
            # Blend: keep the keyword signal dominant, nudge by sentiment.
            confidence = round(min(1.0, 0.7 * confidence + 0.3 * sent), 4)
        return ScoredPost(post=post, raw_score=raw, confidence=confidence, matched=matched)

    # ---------------------------------------------------------------- cluster
    def _cluster(self, scored: list[ScoredPost]) -> list[dict[str, Any]]:
        """Cluster above-floor posts by geo bucket (PRD Step 2 / Step 3 grid).

        Uses :meth:`GridCell.from_latlon` to bucket each post, then summarises
        each bucket (size, mean confidence, centroid, matched keywords). The
        time window is applied by the caller via :meth:`_within_window`.
        """
        from ...models.geo import GridCell  # local import keeps module top tidy

        buckets: dict[str, list[ScoredPost]] = {}
        for sp in scored:
            if sp.confidence < self.post_confidence_floor:
                continue
            cell = GridCell.from_latlon(sp.post.location(), size_m=self.grid_size_m)
            buckets.setdefault(cell.id, []).append(sp)

        clusters: list[dict[str, Any]] = []
        for cell_id, members in buckets.items():
            n = len(members)
            mean_conf = sum(m.confidence for m in members) / n
            lat = sum(m.post.lat for m in members) / n
            lon = sum(m.post.lon for m in members) / n
            keywords = sorted({kw for m in members for kw in m.matched})
            clusters.append(
                {
                    "cell_id": cell_id,
                    "size": n,
                    "mean_confidence": round(mean_conf, 4),
                    "centroid": {"lat": round(lat, 6), "lon": round(lon, 6)},
                    "post_ids": [m.post.post_id for m in members],
                    "keywords": keywords,
                }
            )
        clusters.sort(key=lambda c: (c["size"], c["mean_confidence"]), reverse=True)
        return clusters

    @staticmethod
    def _within_window(
        posts: list[SocialPost], window_seconds: int, now: str | None
    ) -> list[SocialPost]:
        """Keep posts whose timestamp is within ``window_seconds`` of ``now``.

        ``now`` defaults to the most recent post timestamp in the batch, so the
        offline fixtures (with fixed past timestamps) cluster correctly without
        depending on wall-clock time. Unparseable timestamps are kept (fail open
        — better to consider a post than silently drop a distress signal).
        """
        from datetime import datetime

        def _parse(ts: str) -> datetime | None:
            try:
                return datetime.fromisoformat(ts)
            except Exception:
                return None

        stamped = [(p, _parse(p.created_at)) for p in posts]
        valid = [dt for _, dt in stamped if dt is not None]
        if not valid:
            return posts
        ref = _parse(now) if now else None
        if ref is None:
            ref = max(valid)
        kept: list[SocialPost] = []
        for p, dt in stamped:
            if dt is None:
                kept.append(p)
                continue
            if abs((ref - dt).total_seconds()) <= window_seconds:
                kept.append(p)
        return kept

    # ------------------------------------------------------------------ detect
    def detect(
        self,
        raw: list[dict[str, Any]],
        window_seconds: int = 1800,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full pipeline on a raw batch → alertable clusters (PRD Step 2).

        Pure (no emit): normalise/score every post, restrict to the time window,
        cluster by geo bucket, and return only clusters that clear the
        activation thresholds (``min_cluster_size`` and
        ``cluster_confidence_threshold``). Exposed so tests can assert
        threshold behaviour directly.
        """
        posts = self._coerce(raw)
        posts = self._within_window(posts, window_seconds, now)
        scored = [self.score_post(p) for p in posts]
        clusters = self._cluster(scored)
        return [
            c
            for c in clusters
            if c["size"] >= self.min_cluster_size
            and c["mean_confidence"] >= self.cluster_confidence_threshold
        ]

    # ------------------------------------------------------------------- event
    def _build_event(self, cluster: dict[str, Any]) -> dict[str, Any]:
        """Mint a JSON-able Module-C collapse :class:`DisasterEvent` (Step 2)."""
        c = cluster["centroid"]
        ev = DisasterEvent(
            incident_id=f"social:{cluster['cell_id']}",
            kind=EventKind.STRUCTURAL_COLLAPSE,
            epicentre=LatLon(c["lat"], c["lon"]),
            # Severity scaled by corroboration: more posts + higher confidence
            # ⇒ stronger collapse signal (kept in a domain-scaled 0..10 band).
            severity=round(
                min(10.0, cluster["mean_confidence"] * (5.0 + cluster["size"])), 2
            ),
            detected_at=utcnow_iso(),
            source="social_nlp",
            meta={
                "confidence": cluster["mean_confidence"],
                "cluster_size": cluster["size"],
                "keywords": cluster["keywords"],
                "post_ids": cluster["post_ids"],
                "cell_id": cluster["cell_id"],
            },
        )
        d = asdict(ev)
        d["kind"] = ev.kind.value
        return d

    # ------------------------------------------------------------------- tick
    def tick(self) -> list[Message]:
        """Periodic poll → NLP → cluster → emit RAW_FEED alerts (PRD Step 2/10).

        Emits one ALERT :class:`Message` per cluster that clears the activation
        threshold; stays silent (returns ``[]``) when no cluster qualifies.
        """
        self._tick_count += 1
        raw = self._pull()
        try:
            clusters = self.detect(raw)
        except Exception:
            log.exception("%s failed to run social NLP batch", self.name)
            return []
        if not clusters:
            return []

        out: list[Message] = []
        for cluster in clusters:
            event = self._build_event(cluster)
            conf = cluster["mean_confidence"]
            priority = Priority.CRITICAL if conf >= 0.8 else Priority.HIGH
            reasoning = [
                "social_nlp: geo-tagged collapse keyword cluster detected "
                "(PRD Step 1 Module C, PRD Step 2)",
                f"{cluster['size']} corroborating posts in cell {cluster['cell_id']} "
                f"(>= {self.min_cluster_size}), mean confidence {conf:.2f} "
                f"(>= {self.cluster_confidence_threshold:.2f})",
                f"keywords: {', '.join(cluster['keywords'])}",
            ]
            payload: dict[str, Any] = {
                "kind": self.module.value,  # "C" — discriminates the feed source family
                "source": "social_nlp",
                "event": event,
                "cluster": cluster,
                "confidence": conf,
            }
            out.append(
                Message(
                    sender=self.name,
                    recipient=SOCIAL_RAW_FEED_RECIPIENT,
                    type=MessageType.ALERT,
                    priority=priority,
                    payload=payload,
                    reasoning=reasoning,
                    topic=Topic.RAW_FEED,
                    module=self.module,
                    incident_id=event["incident_id"],
                )
            )
        return out


def score_post(text: str) -> tuple[float, list[str]]:
    """Module-level convenience: keyword-score a raw post string (PRD Step 2)."""
    return score_text(text)
