"""Topic-DAG precision tests for the diagnostics doctor.

The doctor's DAG-balance probe must distinguish two structurally different
situations that both look like "a subscribed topic that nobody produces":

  * a genuine **wiring break** — a *single-purpose* agent (one that subscribes to
    a single functional edge, not the whole contract) is waiting on a topic
    nobody produces; and
  * a benign **reserved** topic — declared contract surface that has no producer
    yet and is only "subscribed" because the all-topic collectors (the
    observability ``MetricsCollector``, the tracing ``TraceCollector`` and the
    state persistor) fan out over *every* ``Topic.*`` constant by design.

The canonical reserved case is :data:`Topic.COMMANDER_REVIEW`
(``tier1.commander_review``): it has no producer and is only watched by the
all-topic collectors, so it must be reported under ``reserved`` and NOT counted
as a DAG break.

Offline, stdlib-only, deterministic (PRD HARD RULE 2). These tests complement —
and never weaken — ``tests/test_diagnostics.py``.
"""
from __future__ import annotations

from disastermind.diagnostics import (
    Status,
    all_topic_subscriber_names,
    analyse_dag,
    run_diagnostics,
    subscribers_by_topic,
)
from disastermind.diagnostics import checks as dchecks


# --------------------------------------------------------------- healthy system
def test_healthy_build_has_zero_real_dag_breaks_and_lists_reserved() -> None:
    """A clean wired system: 0 real breaks, commander_review under 'reserved'."""
    report = run_diagnostics()

    by_name = {c.name: c for c in report.checks}
    # No real DAG break of either kind.
    assert by_name["dag.orphan_producers"].status is Status.OK
    assert by_name["dag.dead_subscribers"].status is Status.OK
    assert report.failures() == [], [c.detail for c in report.failures()]

    # commander_review is reported SEPARATELY as reserved (an OK, not a FAIL).
    reserved_chk = by_name["dag.reserved_topics"]
    assert reserved_chk.status is Status.OK
    assert "tier1.commander_review" in reserved_chk.data["topics"]
    assert "tier1.commander_review" in report.meta["dag_reserved"]


def test_commander_review_is_only_watched_by_all_topic_collectors() -> None:
    """Ground-truth: commander_review has no producer; only collectors watch it."""
    from disastermind.orchestration.build import build_system

    loop = build_system()
    bus = loop.bus

    subs_map = subscribers_by_topic(bus)
    collectors = all_topic_subscriber_names(bus)

    # The all-topic collectors are exactly the fan-out agents (observability /
    # tracing / state persistor) — each subscribes to the full Topic set.
    assert {"observability.metrics", "tracing.collector"} <= collectors

    watchers = subs_map["tier1.commander_review"]
    assert watchers, "commander_review should have collector subscribers"
    # Every subscriber on commander_review is an all-topic collector.
    assert watchers <= collectors

    # And nothing produces it: it is absent from the (pre-run) history.
    from disastermind.diagnostics import produced_topics

    assert "tier1.commander_review" not in produced_topics(bus)


def test_escalation_narrative_is_not_a_break() -> None:
    """escalation_narrative is produced + consumed via history (a terminal sink)."""
    from disastermind.diagnostics.checks import (
        _seed_and_run,
        produced_topics,
        subscribed_topics,
    )
    from disastermind.orchestration.build import build_system

    loop = build_system()
    bus = loop.bus
    _seed_and_run(loop)

    produced = produced_topics(bus)
    subscribed = subscribed_topics(bus)
    # It IS produced (the narrator emits it)...
    assert "tier1.escalation_narrative" in produced
    # ...consumed out-of-band via history, so it has no in-bus subscriber...
    assert "tier1.escalation_narrative" not in subscribed
    # ...yet it is a declared terminal sink, so NOT an orphan producer.
    assert dchecks._is_terminal_sink("tier1.escalation_narrative")

    analysis = analyse_dag(
        produced,
        subscribed,
        subscribers_by_topic=subscribers_by_topic(bus),
        all_topic_subscribers=all_topic_subscriber_names(bus),
    )
    assert "tier1.escalation_narrative" not in analysis["orphan_producers"]
    assert "tier1.escalation_narrative" not in analysis["dead_subscribers"]


# ------------------------------------------------------- reserved vs real break
def test_reserved_topic_is_not_a_break_when_only_collectors_subscribe() -> None:
    """A contract topic watched ONLY by all-topic collectors is reserved, not dead."""
    analysis = analyse_dag(
        produced={"tier2.prediction"},
        subscribed={"tier2.prediction", "tier1.commander_review"},
        subscribers_by_topic={
            "tier2.prediction": {"resource.allocator", "observability.metrics"},
            "tier1.commander_review": {"observability.metrics", "tracing.collector"},
        },
        all_topic_subscribers={"observability.metrics", "tracing.collector"},
    )
    assert analysis["reserved"] == ["tier1.commander_review"]
    assert analysis["dead_subscribers"] == []
    assert analysis["orphan_producers"] == []


def test_single_purpose_subscriber_on_unproduced_topic_is_a_real_break() -> None:
    """A single-purpose agent waiting on an unproduced topic is still flagged.

    Even when the topic is ALSO watched by an all-topic collector, the presence of
    a single-purpose subscriber makes it a genuine dead-subscriber break.
    """
    analysis = analyse_dag(
        produced={"tier2.prediction"},
        subscribed={"tier2.prediction", "tier2.cascade"},
        subscribers_by_topic={
            "tier2.prediction": {"resource.allocator", "observability.metrics"},
            # cascade has a SINGLE-PURPOSE subscriber (resource.allocator) plus a
            # collector — nobody produces tier2.cascade in this synthetic run.
            "tier2.cascade": {"resource.allocator", "observability.metrics"},
        },
        all_topic_subscribers={"observability.metrics", "tracing.collector"},
    )
    assert analysis["dead_subscribers"] == ["tier2.cascade"]
    assert analysis["reserved"] == []


def test_real_break_flagged_even_for_declared_contract_topic() -> None:
    """Precision: a contract topic with a single-purpose subscriber is NOT forgiven.

    Historically the set-only analysis treated every declared ``Topic`` constant as
    producible, hiding a genuine break. With subscriber info we flag it.
    """
    from disastermind.diagnostics import known_contract_topics

    # commander_review is a real contract topic...
    assert "tier1.commander_review" in known_contract_topics()
    analysis = analyse_dag(
        produced=set(),
        subscribed={"tier1.commander_review"},
        subscribers_by_topic={"tier1.commander_review": {"commander"}},  # single-purpose
        all_topic_subscribers={"observability.metrics", "tracing.collector"},
    )
    # A single-purpose 'commander' waiting on an unproduced contract topic IS a break.
    assert analysis["dead_subscribers"] == ["tier1.commander_review"]
    assert analysis["reserved"] == []


def test_set_only_call_preserves_historic_behaviour() -> None:
    """Without subscriber info, the historic contract-topic fallback still holds."""
    # Undeclared phantom topic -> dead (real break) even with no subscriber info.
    a = analyse_dag(
        produced={"tier2.prediction"},
        subscribed={"tier2.prediction", "phantom.never_emitted"},
    )
    assert a["dead_subscribers"] == ["phantom.never_emitted"]
    assert a["reserved"] == []
    # A declared contract topic with no subscriber info is NOT flagged dead.
    b = analyse_dag(produced=set(), subscribed={"tier1.commander_review"})
    assert b["dead_subscribers"] == []
    assert b["reserved"] == []


# --------------------------------------------------------- end-to-end check_dag
def test_check_dag_classifies_synthetic_reserved_and_real_break(monkeypatch) -> None:
    """End-to-end: a reserved-only topic stays OK; a single-purpose break FAILs.

    We monkeypatch the bus introspection so ``check_dag`` sees:
      * ``reserved.only`` — watched solely by an all-topic collector (reserved),
      * ``dead.single`` — watched by a single-purpose agent (a real break).
    without touching any frozen agent code.
    """
    from disastermind.diagnostics.report import Report

    monkeypatch.setattr(
        dchecks, "produced_topics", lambda bus: {"tier2.prediction"}
    )
    monkeypatch.setattr(
        dchecks,
        "subscribed_topics",
        lambda bus: {"tier2.prediction", "reserved.only", "dead.single"},
    )
    monkeypatch.setattr(
        dchecks,
        "subscribers_by_topic",
        lambda bus: {
            "tier2.prediction": {"resource.allocator", "collector.all"},
            "reserved.only": {"collector.all"},
            "dead.single": {"some.single_purpose_agent"},
        },
    )
    monkeypatch.setattr(
        dchecks, "all_topic_subscriber_names", lambda bus: {"collector.all"}
    )

    report = Report()
    dchecks.check_dag(report, {"settings": None})
    by_name = {c.name: c for c in report.checks}

    # The real break is FAILed and names the single-purpose topic.
    assert by_name["dag.dead_subscribers"].status is Status.FAIL
    assert "dead.single" in by_name["dag.dead_subscribers"].detail
    # The reserved-only topic is reported separately as OK (not a break).
    assert by_name["dag.reserved_topics"].status is Status.OK
    assert "reserved.only" in by_name["dag.reserved_topics"].data["topics"]
    # The reserved topic is NOT counted among the dead subscribers.
    assert "reserved.only" not in by_name["dag.dead_subscribers"].detail
    assert report.exit_code == 1
