"""DisasterMind command-line interface (PRD Group A, Step 10 operator surface).

Pure stdlib (``argparse``) so ``python -m disastermind`` runs with no optional
dependency and no network (PRD HARD RULE 2). Subcommands:

  * ``run``                 — build the full agent DAG (:func:`build_system`) and
                              drive the coordination loop for ``--max-cycles``.
  * ``simulate {A|B|C}``    — inject a synthetic cyclone/earthquake/fire scenario
                              and print the resulting DISPATCH / ESCALATION
                              summary plus per-topic message counts.
  * ``verify-audit <path>`` — re-walk a JSONL decision log's hash-chain via
                              :meth:`DecisionLogger.verify_chain` (PRD Step 9).
  * ``train --out <dir>``   — fit + persist the per-module risk models via
                              :func:`disastermind.ml.training.train_all` and print
                              the resulting manifest.
  * ``eval [--out <dir>]``  — backtest every module via
                              :func:`disastermind.ml.eval.backtest`, print per-module
                              metrics and (with ``--out``) write model cards.
  * ``doctor [--audit <p>]``— run the system self-check via
                              :func:`disastermind.diagnostics.run_diagnostics` and
                              return its exit code.
  * ``serve [--host --port]``— serve the dashboard via
                              :func:`disastermind.api.server.run` (lazy uvicorn).

Each heavier subcommand imports its target *inside* its handler so ``--help`` and
the lightweight commands keep working with stdlib only and no optional dependency.
Every command returns a conventional process exit code (0 = success) so the CLI
composes in scripts/CI.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

from .audit.decision_log import DecisionLogger
from .core.config import Settings
from .core.contracts import Message

_PROG = "disastermind"

_MODULE_KEYS = {
    "A": "Cyclone / Flood",
    "B": "Earthquake",
    "C": "Urban Fire / Collapse",
}


# --------------------------------------------------------------------------- run
def cmd_run(args: argparse.Namespace, out=sys.stdout) -> int:
    """Build the full DAG and drive the coordination loop (PRD Step 10)."""
    # Imported lazily so ``--help`` and ``verify-audit`` work even if a heavy
    # optional module in the DAG were unavailable (graceful degradation).
    from .orchestration.build import build_system

    settings = Settings()
    audit_path = args.audit or settings.audit_log_path
    logger = DecisionLogger.null() if args.no_audit else DecisionLogger(path=audit_path)

    loop = build_system(logger=logger, settings=settings)
    if loop.degraded_modules:
        print(
            f"[warn] running degraded without: {', '.join(loop.degraded_modules)}",
            file=sys.stderr,
        )

    # Deterministic, non-blocking clock so ``run`` terminates cleanly for the
    # requested number of cycles (no wall-clock sleeping in the CLI path).
    cycles = loop.run(max_cycles=args.max_cycles, clock=lambda: 0.0, sleep=lambda _s: None)

    counts = _topic_counts(loop.bus.history)
    dispatches = _real_dispatches(loop.bus.history)
    escalations = _escalations(loop.bus.history)

    print(f"{_PROG} run: {cycles} cycle(s) executed", file=out)
    _print_topic_counts(counts, out)
    print(
        f"DISPATCH orders: {len(dispatches)}    ESCALATIONS: {len(escalations)}",
        file=out,
    )
    if not args.no_audit and logger.path:
        print(f"audit log: {logger.path}", file=out)
    return 0


# ---------------------------------------------------------------------- simulate
def cmd_simulate(args: argparse.Namespace, out=sys.stdout) -> int:
    """Inject a synthetic scenario for one module and print its outcome."""
    from . import scenarios

    key = args.module.strip().upper()
    if key not in scenarios.SCENARIO_GENERATORS:
        print(f"unknown module {args.module!r}; choose A, B or C", file=sys.stderr)
        return 2

    result = scenarios.run_scenario(key, escalate=args.escalate)

    print(f"{_PROG} simulate {key}: {result.label}", file=out)
    _print_topic_counts(result.topic_counts, out)
    print(
        f"DISPATCH orders: {len(result.dispatches)}    "
        f"ESCALATIONS: {len(result.escalations)}",
        file=out,
    )

    for d in result.dispatches[: args.limit]:
        body = (d.payload or {}).get("body") or _order_brief(d)
        print(f"  DISPATCH [{d.module.value}] {body}", file=out)
    for e in result.escalations[: args.limit]:
        trig = e.escalation_trigger.value if e.escalation_trigger else "escalation"
        report = (e.payload or {}).get("report") or {}
        summary = report.get("summary") or (e.reasoning[0] if e.reasoning else "")
        ho = " (HUMAN-ONLY)" if (e.payload or {}).get("human_only") else ""
        print(f"  ESCALATION [{trig}]{ho} {summary}", file=out)

    if not result.succeeded:
        print("[error] scenario did not reach DISPATCH or ESCALATION", file=sys.stderr)
        return 1
    return 0


# ------------------------------------------------------------------- verify-audit
def cmd_verify_audit(args: argparse.Namespace, out=sys.stdout) -> int:
    """Verify a decision-log hash-chain on disk (PRD Step 9)."""
    import os

    if not os.path.exists(args.path):
        print(f"audit log not found: {args.path}", file=sys.stderr)
        return 2
    logger = DecisionLogger(path=args.path)
    ok = logger.verify_chain()
    n = _count_records(args.path)
    if ok:
        print(f"audit chain OK: {n} record(s) verified, hash-chain intact", file=out)
        return 0
    print(f"audit chain TAMPERED: hash-chain broken in {args.path}", file=out)
    return 1


# ------------------------------------------------------------------------- train
def cmd_train(args: argparse.Namespace, out=sys.stdout) -> int:
    """Fit + persist the per-module risk models and print the manifest.

    Lazily imports :func:`disastermind.ml.training.train_all` so the dependency is
    paid only when training; runs fully offline (the wrappers fall back to a
    deterministic heuristic when no optional ML library is installed).
    """
    from .ml.training import train_all

    manifest = train_all(args.out, seed=args.seed)
    print(f"{_PROG} train: wrote {len(manifest['models'])} model(s)", file=out)
    print(f"out_dir: {manifest['out_dir']}", file=out)
    print(f"manifest: {os.path.join(manifest['out_dir'], 'manifest.json')}", file=out)
    for entry in manifest["models"]:
        backend = entry["backend"]
        active = "active" if entry.get("backend_active") else "heuristic"
        print(
            f"  [{entry['module']}] {entry['path']} "
            f"(backend={backend}/{active}, n_train={entry['n_train']})",
            file=out,
        )
    return 0


# -------------------------------------------------------------------------- eval
def cmd_eval(args: argparse.Namespace, out=sys.stdout) -> int:
    """Backtest every module and print per-module metrics (PRD Step 3).

    Lazily imports :func:`disastermind.ml.eval.backtest`. With ``--out`` the result
    JSON and per-module model cards are written under that directory.
    """
    from .ml.eval import backtest

    out_dir = args.out or None
    result = backtest(out_dir, seed=args.seed)
    print(f"{_PROG} eval: backtested {len(result['modules'])} module(s)", file=out)
    print("per-module metrics:", file=out)
    for entry in result["modules"]:
        m = entry["metrics"]
        print(
            f"  [{entry['module']}] "
            f"AUC={m['auc']:.3f}  Brier={m['brier']:.3f}  "
            f"acc={m['accuracy']:.3f}  ECE={m['ece']:.3f}  "
            f"(n_test={entry['n_test']}, backend={entry['backend']})",
            file=out,
        )
    if out_dir:
        print(f"artifacts written to: {result.get('out_dir', out_dir)}", file=out)
    return 0


# ------------------------------------------------------------------------ doctor
def cmd_doctor(args: argparse.Namespace, out=sys.stdout) -> int:
    """Run the system self-check and return its exit code (PRD Step 10).

    Lazily imports :func:`disastermind.diagnostics.run_diagnostics`; prints the
    Markdown report (or JSON with ``--json``) and returns the report's exit code
    (0 when nothing FAILED, 1 otherwise).
    """
    from .diagnostics import run_diagnostics

    report = run_diagnostics(audit_path=args.audit or None)
    if getattr(args, "json", False):
        print(report.to_json(), file=out)
    else:
        print(report.to_markdown(), file=out)
    return int(report.exit_code)


# ------------------------------------------------------------------------- serve
def cmd_serve(args: argparse.Namespace, out=sys.stdout) -> int:
    """Serve the dashboard over HTTP/WebSocket (PRD Step 10).

    Lazily imports :func:`disastermind.api.server.run` which itself lazily imports
    uvicorn. If uvicorn is absent we print a clear message and return a nonzero exit
    code rather than blocking. This call *does* block while serving, so tests never
    invoke it (they assert on parsing/dispatch only).
    """
    from .api.server import run as run_server

    print(f"{_PROG} serve: starting dashboard on {args.host}:{args.port}", file=out)
    try:
        run_server(host=args.host, port=args.port)
    except RuntimeError as exc:
        print(f"[error] cannot serve: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
        print(f"{_PROG} serve: stopped", file=out)
    return 0


# --------------------------------------------------------------------- helpers
def _topic_counts(history: Sequence[Message]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in history:
        counts[m.topic] = counts.get(m.topic, 0) + 1
    return dict(sorted(counts.items()))


def _real_dispatches(history: Sequence[Message]) -> list[Message]:
    from .core.contracts import MessageType, Topic

    out: list[Message] = []
    for m in history:
        if m.topic != Topic.DISPATCH:
            continue
        if m.type is MessageType.ACK:
            continue
        if (m.payload or {}).get("kind") == "dispatch_ack":
            continue
        out.append(m)
    return out


def _escalations(history: Sequence[Message]) -> list[Message]:
    from .core.contracts import MessageType, Topic

    return [
        m for m in history if m.topic == Topic.ESCALATION and m.type is MessageType.ESCALATION
    ]


def _order_brief(msg: Message) -> str:
    order = (msg.payload or {}).get("order") or {}
    team = order.get("team_id", "team")
    site = order.get("site", "?")
    return f"{team} -> {site}"


def _print_topic_counts(counts: dict[str, int], out) -> None:
    print("topic counts:", file=out)
    if not counts:
        print("  (none)", file=out)
        return
    width = max(len(t) for t in counts)
    for topic, n in counts.items():
        print(f"  {topic.ljust(width)}  {n}", file=out)


def _count_records(path: str) -> int:
    n = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    n += 1
    except OSError:
        return 0
    return n


# ------------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse CLI (stdlib only)."""
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="DisasterMind — autonomous multi-agent disaster coordination CLI.",
    )
    sub = parser.add_subparsers(
        dest="command",
        metavar="{run,simulate,verify-audit,train,eval,doctor,serve}",
    )

    p_run = sub.add_parser("run", help="build the agent DAG and drive the coordination loop")
    p_run.add_argument(
        "--max-cycles",
        type=int,
        default=1,
        help="number of coordination cycles to execute (default: 1)",
    )
    p_run.add_argument(
        "--audit",
        default="",
        help="path for the JSONL decision log (default: settings.audit_log_path)",
    )
    p_run.add_argument(
        "--no-audit",
        action="store_true",
        help="use an in-memory null audit logger (do not write to disk)",
    )
    p_run.set_defaults(func=cmd_run)

    p_sim = sub.add_parser(
        "simulate", help="inject a synthetic scenario for module A, B or C"
    )
    p_sim.add_argument("module", choices=["A", "B", "C", "a", "b", "c"], help="hazard module")
    p_sim.add_argument(
        "--escalate",
        action="store_true",
        help="inject an order requiring human escalation (PRD Step 7)",
    )
    p_sim.add_argument(
        "--limit",
        type=int,
        default=5,
        help="max DISPATCH/ESCALATION lines to print (default: 5)",
    )
    p_sim.set_defaults(func=cmd_simulate)

    p_audit = sub.add_parser(
        "verify-audit", help="verify a decision-log hash-chain (PRD Step 9)"
    )
    p_audit.add_argument("path", help="path to the JSONL audit log")
    p_audit.set_defaults(func=cmd_verify_audit)

    p_train = sub.add_parser(
        "train", help="fit + persist the per-module risk models (PRD Step 3)"
    )
    p_train.add_argument(
        "--out",
        required=True,
        help="output directory for the trained artefacts + manifest.json",
    )
    p_train.add_argument(
        "--seed",
        type=int,
        default=0,
        help="random seed for the synthetic training data (default: 0)",
    )
    p_train.set_defaults(func=cmd_train)

    p_eval = sub.add_parser(
        "eval", help="backtest the per-module risk models and print metrics (PRD Step 3)"
    )
    p_eval.add_argument(
        "--out",
        default="",
        help="optional directory for backtest.json + per-module model cards",
    )
    p_eval.add_argument(
        "--seed",
        type=int,
        default=0,
        help="random seed for the deterministic backtest (default: 0)",
    )
    p_eval.set_defaults(func=cmd_eval)

    p_doctor = sub.add_parser(
        "doctor", help="run the system self-check and report health (PRD Step 10)"
    )
    p_doctor.add_argument(
        "--audit",
        default="",
        help="JSONL audit log whose hash-chain to verify (default: none)",
    )
    p_doctor.add_argument(
        "--json",
        action="store_true",
        help="emit the diagnostics report as JSON instead of Markdown",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    p_serve = sub.add_parser(
        "serve", help="serve the dashboard over HTTP/WebSocket (PRD Step 10)"
    )
    p_serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="host/interface to bind (default: 127.0.0.1)",
    )
    p_serve.add_argument(
        "--port",
        type=int,
        default=8000,
        help="TCP port to bind (default: 8000)",
    )
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py
    raise SystemExit(main())
