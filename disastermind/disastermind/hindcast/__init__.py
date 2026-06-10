"""Hindcast harness — replay REAL named disasters through the pipeline.

Trust in life-safety software comes from proven performance on real, named
events measured against what actually happened. This package replays a real
cyclone (Fani, 2019) from its committed NOAA IBTrACS best-track using ONLY the
data available before a forecast cutoff (strictly leak-free), drives the
DisasterMind activation + coordination pipeline, and scores the result against
the *documented* outcome (IMD / EM-DAT / Odisha SRC).

What is being tested is honest about scope: DisasterMind is a COORDINATION
system, not a track-forecast model (track forecasting is IMD's role). So the
hindcast measures (a) activation lead time, (b) a transparent track-extrapolation
landfall error, (c) whether the at-risk coastal belt is identified, and (d)
whether an evacuation/resource plan would have been produced in time — the plan
that, in reality, enabled the mass evacuation that held Fani's toll to 64 deaths
despite a Category-4 landfall.
"""
from __future__ import annotations

from .fani import load_fani
from .pipeline_backtest import backtest_event, run_backtest
from .replay import HindcastResult, extrapolate_landfall, run_hindcast
from .report import to_markdown

__all__ = [
    "load_fani",
    "run_hindcast",
    "extrapolate_landfall",
    "HindcastResult",
    "to_markdown",
    "run_backtest",
    "backtest_event",
]
