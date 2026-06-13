"""Tests for the narrated hero-demo walkthrough (`disastermind.hindcast.walkthrough`).

The walkthrough is a presentation layer over the leak-free replay engine; these
lock in that it renders for both storms, tells the three-part command story, and
keeps the honesty boundary and the human-in-the-loop framing visible.
"""
from __future__ import annotations

import pytest

from disastermind.hindcast import walkthrough


@pytest.mark.parametrize("storm", ["fani", "amphan"])
def test_renders_for_both_storms(storm):
    text = walkthrough.render(storm, color=False)
    assert "COMMAND WALKTHROUGH" in text
    # The three questions a commander asks must all be present.
    assert "WHAT WE KNOW" in text
    assert "WHAT THE SYSTEM RECOMMENDS" in text
    assert "THE COST OF WAITING" in text
    # Every forecast cutoff appears.
    for lead in (72, 48, 36, 24, 12):
        assert f"T − {lead} h" in text


def test_keeps_human_in_the_loop_framing():
    text = walkthrough.render("fani", color=False)
    # A mass evacuation is recommended, never autonomous.
    assert "RECOMMENDED" in text
    assert "awaits human commander" in text
    assert "authority threshold" in text


def test_states_the_honest_boundary_and_scores_reality():
    text = walkthrough.render("fani", color=False)
    assert "SCORED AGAINST REALITY" in text
    assert "HONEST BOUNDARY" in text
    assert "does NOT re-forecast" in text


def test_plain_mode_has_no_ansi_codes():
    text = walkthrough.render("fani", color=False)
    assert "\033[" not in text


def test_cli_main_runs(capsys):
    rc = walkthrough.main(["--storm", "fani", "--plain"])
    assert rc == 0
    assert "COMMAND WALKTHROUGH" in capsys.readouterr().out
