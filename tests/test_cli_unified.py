"""Tests for the unified DisasterMind CLI (``disastermind.cli``).

This wave WIRES the full toolchain into the existing argparse CLI as new
subcommands (``train``, ``eval``, ``doctor``, ``serve``) while keeping the
original ``run`` / ``simulate`` / ``verify-audit`` commands working unchanged.

All tests are stdlib-only and offline (PRD HARD RULE 2):

  * ``train`` / ``eval`` exercise the deterministic synthetic ML seam — no network,
    seeded, and the wrappers fall back to a heuristic when no optional ML library
    is installed.
  * ``doctor`` runs the offline self-check.
  * ``serve`` is *never* actually started — we patch the lazily-imported server
    ``run`` so we assert on parse + dispatch (and the uvicorn-absent path) without
    binding a socket or blocking.
"""
from __future__ import annotations

import io
import json
import os

import pytest

from disastermind.cli import build_parser, main


# --------------------------------------------------------------------- helpers
def _run_cli(argv: list[str]) -> tuple[int, str]:
    """Invoke a CLI handler capturing the ``out`` stream (mirrors test_scenarios)."""
    buf = io.StringIO()
    parser = build_parser()
    args = parser.parse_args(argv)
    code = int(args.func(args, out=buf))
    return code, buf.getvalue()


# ------------------------------------------------------------------- doctor
def test_doctor_returns_zero_on_healthy_system() -> None:
    """``doctor`` runs the self-check and returns 0 on a healthy offline system."""
    code = main(["doctor"])
    assert code == 0


def test_doctor_prints_markdown_report() -> None:
    code, text = _run_cli(["doctor"])
    assert code == 0
    assert "DisasterMind doctor" in text
    assert "exit code" in text


def test_doctor_json_flag_emits_parseable_json() -> None:
    code, text = _run_cli(["doctor", "--json"])
    assert code == 0
    payload = json.loads(text)
    assert payload["exit_code"] == 0
    assert "checks" in payload


def test_doctor_with_audit_path_verifies_chain(tmp_path) -> None:
    """A real (intact) audit log is accepted; doctor still reports healthy."""
    audit = tmp_path / "audit.jsonl"
    rc, _ = _run_cli(["run", "--max-cycles", "1", "--audit", str(audit)])
    assert rc == 0
    code, text = _run_cli(["doctor", "--audit", str(audit)])
    assert code == 0
    assert "DisasterMind doctor" in text


# -------------------------------------------------------------------- train
def test_train_writes_artifacts_and_returns_zero(tmp_path) -> None:
    """``train --out <dir>`` writes per-module artefacts + a manifest and returns 0."""
    out = tmp_path / "models"
    code, text = _run_cli(["train", "--out", str(out)])
    assert code == 0
    assert os.path.exists(out / "manifest.json")
    for module in ("A", "B", "C"):
        assert os.path.exists(out / f"model_{module}.json")
    assert "train:" in text
    # The printed manifest path is real and parseable.
    manifest = json.loads((out / "manifest.json").read_text())
    assert {e["module"] for e in manifest["models"]} == {"A", "B", "C"}


def test_train_via_main_entry_point(tmp_path) -> None:
    out = tmp_path / "m"
    code = main(["train", "--out", str(out)])
    assert code == 0
    assert os.path.exists(out / "model_A.json")


def test_train_is_deterministic_in_seed(tmp_path) -> None:
    """Same seed -> byte-identical artefacts (no wall-clock / network nondeterminism)."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    assert _run_cli(["train", "--out", str(a), "--seed", "7"])[0] == 0
    assert _run_cli(["train", "--out", str(b), "--seed", "7"])[0] == 0
    assert (a / "model_A.json").read_bytes() == (b / "model_A.json").read_bytes()


# --------------------------------------------------------------------- eval
def test_eval_returns_zero_and_prints_metrics() -> None:
    """``eval`` (no --out) backtests every module and prints per-module metrics."""
    code, text = _run_cli(["eval"])
    assert code == 0
    assert "per-module metrics:" in text
    assert "AUC=" in text and "Brier=" in text
    for module in ("A", "B", "C"):
        assert f"[{module}]" in text


def test_eval_with_out_writes_cards(tmp_path) -> None:
    out = tmp_path / "report"
    code, text = _run_cli(["eval", "--out", str(out)])
    assert code == 0
    assert os.path.exists(out / "backtest.json")
    for module in ("A", "B", "C"):
        assert os.path.exists(out / f"card_{module}.md")
    assert "artifacts written to:" in text


def test_eval_via_main_entry_point() -> None:
    assert main(["eval"]) == 0


# -------------------------------------------------------------------- serve
def test_serve_dispatches_without_starting_server(monkeypatch) -> None:
    """``serve`` parses + dispatches; we patch the server ``run`` so nothing binds."""
    import disastermind.api.server as server

    called: dict[str, object] = {}

    def fake_run(host="127.0.0.1", port=8000, **kwargs):
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr(server, "run", fake_run)
    code, text = _run_cli(["serve", "--host", "0.0.0.0", "--port", "9123"])
    assert code == 0
    assert called == {"host": "0.0.0.0", "port": 9123}
    assert "serve:" in text


def test_serve_reports_nonzero_when_server_unavailable(monkeypatch) -> None:
    """If the server backend (uvicorn) is absent, ``serve`` reports a nonzero exit."""
    import disastermind.api.server as server

    def boom(host="127.0.0.1", port=8000, **kwargs):
        raise RuntimeError("uvicorn is not installed")

    monkeypatch.setattr(server, "run", boom)
    code, _ = _run_cli(["serve"])
    assert code != 0


# ---------------------------------------------------- existing commands intact
def test_existing_simulate_still_parses_and_runs() -> None:
    """An existing subcommand (simulate B) still parses and runs to a result."""
    code, text = _run_cli(["simulate", "B"])
    assert code == 0
    assert "DISPATCH orders:" in text
    assert "topic counts:" in text


def test_existing_run_still_executes_cycles(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    code, text = _run_cli(["run", "--max-cycles", "2", "--audit", str(audit)])
    assert code == 0
    assert "cycle(s) executed" in text


def test_existing_verify_audit_still_works(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    assert _run_cli(["run", "--max-cycles", "1", "--audit", str(audit)])[0] == 0
    code, text = _run_cli(["verify-audit", str(audit)])
    assert code == 0
    assert "audit chain OK" in text


def test_no_command_prints_help_and_returns_zero() -> None:
    assert main([]) == 0


def test_new_subcommands_registered_in_parser() -> None:
    """The new subcommands are wired into the parser alongside the originals."""
    parser = build_parser()
    for cmd in ("run", "simulate", "verify-audit", "train", "eval", "doctor", "serve"):
        # parse_args succeeds for known commands (train/eval/serve need no required
        # positional beyond their options; train needs --out).
        if cmd == "train":
            args = parser.parse_args([cmd, "--out", "x"])
        elif cmd in ("simulate",):
            args = parser.parse_args([cmd, "A"])
        elif cmd == "verify-audit":
            args = parser.parse_args([cmd, "x"])
        else:
            args = parser.parse_args([cmd])
        assert args.command == cmd
        assert callable(args.func)


def test_train_requires_out() -> None:
    """``train`` without --out is a parse error (argparse exits with code 2)."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["train"])
    assert exc.value.code == 2
