"""Throughput-report rendering for the benchmark harness (PRD Step 9/10).

Turns a :class:`~disastermind.benchmarks.harness.BenchmarkResult` (or a plain
metrics ``dict``) into (a) a normalised report dict and (b) a deterministic
Markdown table. Stdlib-only; no timing, so the rendered text is stable and
diff-friendly for CI snapshots / dashboards.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .harness import BenchmarkResult


def report(metrics: BenchmarkResult | Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a result into a JSON-serialisable report dict with derived rates.

    Accepts either a :class:`BenchmarkResult` or a raw metrics mapping (so callers
    can persist results and re-render later). Derived fields are *ratios of
    counts* — dispatches-per-incident, messages-per-incident, mean per-cycle
    messages — never throughput-per-second, keeping everything deterministic.
    """
    d: dict[str, Any] = (
        metrics.to_dict() if isinstance(metrics, BenchmarkResult) else dict(metrics)
    )

    incidents = int(d.get("incidents", 0) or 0)
    cycles = int(d.get("cycles", 0) or 0)
    messages = int(d.get("messages_processed", 0) or 0)
    dispatches = int(d.get("dispatches", 0) or 0)
    escalations = int(d.get("escalations", 0) or 0)
    per_cycle = list(d.get("per_cycle_messages", []) or [])

    def _ratio(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    derived = {
        "messages_per_incident": _ratio(messages, incidents),
        "dispatches_per_incident": _ratio(dispatches, incidents),
        "escalations_per_incident": _ratio(escalations, incidents),
        "mean_messages_per_cycle": (
            round(sum(per_cycle) / len(per_cycle), 4) if per_cycle else 0.0
        ),
        "max_messages_in_a_cycle": max(per_cycle) if per_cycle else 0,
        # Whether the bus ring buffer hit its cap (history bounded — Step 10).
        "history_at_cap": int(d.get("bus_history_len", 0) or 0)
        >= int(d.get("bus_history_cap", 0) or 0)
        and bool(d.get("bus_history_cap")),
    }

    out: dict[str, Any] = {
        "incidents": incidents,
        "cycles": cycles,
        "modules": list(d.get("modules", []) or []),
        "messages_processed": messages,
        "dispatches": dispatches,
        "escalations": escalations,
        "per_cycle_messages": per_cycle,
        "bus_history_len": int(d.get("bus_history_len", 0) or 0),
        "bus_history_cap": int(d.get("bus_history_cap", 0) or 0),
        "topic_counts": dict(d.get("topic_counts", {}) or {}),
        "derived": derived,
    }
    return out


def to_markdown(metrics: BenchmarkResult | Mapping[str, Any]) -> str:
    """Render a benchmark result as a deterministic Markdown report.

    Pure function of the (count-based) metrics, so the output is stable across
    machines — safe to snapshot in CI.
    """
    r = report(metrics)
    der = r["derived"]
    lines: list[str] = []
    lines.append("# DisasterMind throughput benchmark")
    lines.append("")
    lines.append(f"- incidents: **{r['incidents']}**")
    lines.append(f"- cycles: **{r['cycles']}**")
    lines.append(f"- modules: {', '.join(r['modules']) or '(none)'}")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("| --- | --- |")
    lines.append(f"| messages_processed | {r['messages_processed']} |")
    lines.append(f"| dispatches | {r['dispatches']} |")
    lines.append(f"| escalations | {r['escalations']} |")
    lines.append(f"| messages_per_incident | {der['messages_per_incident']} |")
    lines.append(f"| dispatches_per_incident | {der['dispatches_per_incident']} |")
    lines.append(f"| mean_messages_per_cycle | {der['mean_messages_per_cycle']} |")
    lines.append(f"| max_messages_in_a_cycle | {der['max_messages_in_a_cycle']} |")
    lines.append(
        f"| bus_history_len / cap | {r['bus_history_len']} / {r['bus_history_cap']} |"
    )
    lines.append(f"| history_at_cap | {str(der['history_at_cap']).lower()} |")
    lines.append("")
    lines.append(f"per-cycle messages: {r['per_cycle_messages']}")
    lines.append("")
    lines.append("## topic counts")
    lines.append("")
    lines.append("| topic | count |")
    lines.append("| --- | --- |")
    for topic, count in r["topic_counts"].items():
        lines.append(f"| {topic} | {count} |")
    return "\n".join(lines) + "\n"
