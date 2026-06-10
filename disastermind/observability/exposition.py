"""Prometheus-style text exposition (PRD Step 9/10 monitoring) — stdlib only.

We render the :class:`~disastermind.observability.collector.MetricsCollector`
counters in the Prometheus text exposition format
(https://prometheus.io/docs/instrumenting/exposition_formats/) **without** any
client library: each metric family is preceded by ``# HELP`` and ``# TYPE`` lines
and every sample is ``name{labels} value``. A Prometheus scraper (or the Grafana
dashboard behind PRD Step 9) can ingest this directly; with no scraper present
the function is a pure string builder, so it is import-safe and network-free.
"""
from __future__ import annotations

from .collector import MetricsCollector

_PREFIX = "disastermind"


def _escape(label_value: str) -> str:
    """Escape a Prometheus label value (backslash, quote, newline)."""
    return (
        str(label_value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _family(lines: list[str], name: str, help_text: str, mtype: str = "counter") -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {mtype}")


def _fmt(value: float) -> str:
    """Render a numeric sample value as a valid Prometheus float.

    Integers come out without a trailing ``.0`` (Prometheus accepts both); other
    floats use a compact ``repr`` that round-trips. ``inf`` maps to ``+Inf``.
    """
    f = float(value)
    if f == float("inf"):
        return "+Inf"
    if f == float("-inf"):
        return "-Inf"
    if f == int(f):
        return str(int(f))
    return repr(f)


def render(collector: MetricsCollector) -> str:
    """Render ``collector`` as a Prometheus text-exposition document.

    Returns a newline-terminated string. Counters use the ``counter`` type and a
    single ``gauge`` (uptime) is emitted so liveness is scrapeable too.
    """
    snap = collector.snapshot()
    lines: list[str] = []

    total = f"{_PREFIX}_messages_total"
    _family(lines, total, "Total messages observed across all topics.")
    lines.append(f"{total} {snap['total']}")

    by_topic = f"{_PREFIX}_messages_by_topic_total"
    _family(lines, by_topic, "Messages observed per topic.")
    for topic, count in sorted(snap["by_topic"].items()):
        lines.append(f'{by_topic}{{topic="{_escape(topic)}"}} {count}')

    by_type = f"{_PREFIX}_messages_by_type_total"
    _family(lines, by_type, "Messages observed per message type.")
    for mtype, count in sorted(snap["by_type"].items()):
        lines.append(f'{by_type}{{type="{_escape(mtype)}"}} {count}')

    by_priority = f"{_PREFIX}_messages_by_priority_total"
    _family(lines, by_priority, "Messages observed per priority level.")
    for prio, count in sorted(snap["by_priority"].items(), key=lambda kv: int(kv[0])):
        lines.append(f'{by_priority}{{priority="{_escape(prio)}"}} {count}')

    escalations = f"{_PREFIX}_escalations_total"
    _family(lines, escalations, "Escalation messages observed (PRD Step 7).")
    lines.append(f"{escalations} {snap['escalations']}")

    by_trigger = f"{_PREFIX}_escalations_by_trigger_total"
    _family(lines, by_trigger, "Escalations observed per escalation trigger.")
    for trigger, count in sorted(snap["by_trigger"].items()):
        lines.append(f'{by_trigger}{{trigger="{_escape(trigger)}"}} {count}')

    dispatches = f"{_PREFIX}_dispatches_total"
    _family(lines, dispatches, "Real dispatch orders executed (PRD Step 8).")
    lines.append(f"{dispatches} {snap['dispatches']}")

    # ---- error / failure counters (additive; zero on healthy traffic) -------
    errors = f"{_PREFIX}_errors_total"
    _family(lines, errors, "Messages observed carrying an error/failure marker.")
    lines.append(f"{errors} {snap.get('errors', 0)}")

    by_error_kind = f"{_PREFIX}_errors_by_kind_total"
    _family(lines, by_error_kind, "Error observations broken down per kind.")
    for kind, count in sorted(snap.get("by_error_kind", {}).items()):
        lines.append(f'{by_error_kind}{{kind="{_escape(kind)}"}} {count}')

    # ---- per-topic message-processing latency histograms --------------------
    # One Prometheus histogram family per topic: cumulative ``_bucket{le=...}``
    # samples plus ``_sum`` and ``_count``. The observed value is a logical tick
    # delta (or an injected clock's delta), never wall-clock seconds in tests.
    latency = f"{_PREFIX}_message_processing_latency_seconds"
    _family(
        lines,
        latency,
        "Message-processing latency per topic (logical tick delta).",
        mtype="histogram",
    )
    for topic, hist in sorted(snap.get("latency_by_topic", {}).items()):
        topic_label = _escape(topic)
        for bucket in hist["buckets"]:
            le = bucket["le"]
            le_str = "+Inf" if le == "+Inf" else _fmt(le)
            lines.append(
                f'{latency}_bucket{{topic="{topic_label}",le="{le_str}"}} '
                f'{bucket["count"]}'
            )
        lines.append(f'{latency}_sum{{topic="{topic_label}"}} {_fmt(hist["sum"])}')
        lines.append(f'{latency}_count{{topic="{topic_label}"}} {hist["count"]}')

    uptime = f"{_PREFIX}_collector_uptime_seconds"
    _family(lines, uptime, "Seconds since the metrics collector started.", mtype="gauge")
    lines.append(f"{uptime} {snap['uptime_seconds']:.3f}")

    return "\n".join(lines) + "\n"
