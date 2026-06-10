"""Committed recorded fixtures of real external feed API responses (PRD Step 2).

These files capture the *real* on-the-wire response shapes of the public hazard
feeds so the ``parse()`` of each Tier 3 adapter is validated against genuine API
schemas **without any network call** (PRD Step 10). They are loaded by the live
``poll_once(live=True, transport=...)`` test seam (with a recorded transport) and
by direct ``parse()`` unit tests.

  * ``usgs_all_hour.geojson``  — USGS all_hour summary GeoJSON FeatureCollection.
  * ``open_meteo_forecast.json`` — Open-Meteo ``/v1/forecast`` hourly response.
  * ``firms_viirs.csv``        — NASA FIRMS area-API CSV (key-gated source).

Helpers below read them with stdlib only so importing/using fixtures never pulls
a third-party dependency.
"""
from __future__ import annotations

import json
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def fixture_path(name: str) -> Path:
    """Return the absolute path to a committed fixture file."""
    return _DIR / name


def load_text(name: str) -> str:
    """Read a committed fixture as text (UTF-8)."""
    return fixture_path(name).read_text(encoding="utf-8")


def load_json(name: str):
    """Read and JSON-decode a committed fixture."""
    return json.loads(load_text(name))
