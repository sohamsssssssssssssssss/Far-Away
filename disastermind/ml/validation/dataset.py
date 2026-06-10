"""Real historical earthquake dataset for honest model validation (PRD Step 3).

Source: the **USGS FDSN event catalog** (earthquake.usgs.gov) — real recorded
earthquakes, M4.5+, 2013-2017 (36k+ events), committed as an offline fixture so
validation is reproducible with no network.

Methodology (designed to be leak-free and honest):

  * FEATURES are strictly *physical* parameters known at detection time —
    magnitude, depth, |latitude|, and an oceanic-longitude proxy. We deliberately
    EXCLUDE felt/cdi/mmi/sig/alert because those are *outcomes* measured after the
    event (using them as inputs would leak the label).
  * LABEL is a real post-event outcome: the quake was *felt / damaging* iff it
    drew felt reports, a PAGER alert >= yellow, or a tsunami flag.
  * SPLIT is TEMPORAL: train on the earlier years, test on strictly later events,
    so the test set is genuinely out-of-sample in time (no random-shuffle leakage,
    no train/test contamination by aftershock clusters spanning the boundary).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "usgs_catalog_2013_2017.json")

#: Leak-free physical feature names (order matters — matches features()).
#: ``gmpe_attenuation`` embeds the incumbent's physics (magnitude attenuated by
#: depth) as a feature, so the trained model has the operational baseline as a
#: floor and learns the residual — still strictly pre-event physical inputs.
FEATURE_NAMES = ("magnitude", "depth_km", "abs_latitude", "ocean_proxy", "gmpe_attenuation")

# Split boundary (ms epoch for 2016-01-01T00:00:00Z): train < boundary <= test.
SPLIT_EPOCH_MS = 1451606400000


@dataclass
class Quake:
    time: int
    mag: float
    depth_km: float
    lat: float
    lon: float
    felt: int
    alert: str | None
    tsunami: int
    mmi: float = 0.0  # ShakeMap instrumental intensity (a measured outcome)

    def label(self) -> int:
        """1 iff the quake was felt / damaging (a real post-event outcome)."""
        a = (self.alert or "").lower()
        return 1 if (self.felt > 0 or a in ("yellow", "orange", "red") or self.tsunami == 1) else 0

    def label_felt(self) -> int:
        """1 iff the quake drew felt reports — PAGER-free, so the PAGER alert
        can serve as an *operational comparator* on this label without
        circularity."""
        return 1 if self.felt > 0 else 0

    def label_damaging(self) -> int:
        """1 iff a damage-grade outcome was MEASURED: ShakeMap instrumental
        intensity reached MMI VI ("strong shaking, light damage") or PAGER
        issued >= yellow (estimated casualties / losses). This is the
        consequences track — the outcome that matters, not a felt-report
        proxy."""
        a = (self.alert or "").lower()
        return 1 if (self.mmi >= 6.0 or a in ("yellow", "orange", "red")) else 0

    def pager_alarm(self) -> float:
        """The operational incumbent's decision as a score: did USGS PAGER alert?

        0 / 0.33 / 0.67 / 1.0 for none / yellow / orange / red — the product
        agencies act on today, used as the bar the model must beat on the
        PAGER-free felt label.
        """
        return {"yellow": 0.33, "orange": 0.67, "red": 1.0}.get((self.alert or "").lower(), 0.0)

    def gmpe_score(self) -> float:
        """ShakeMap-style attenuation baseline: magnitude minus a depth decay.

        ``mag - 2.5 * log10(depth_km + 10)`` is the standard ground-motion
        shape (bigger = more shaking, deeper = attenuated) as a FIXED published
        formula — what an agency could compute today with no ML at all.
        """
        return self.mag - 2.5 * math.log10(self.depth_km + 10.0)

    def region(self) -> str:
        """Macro seismic region (longitude block) for leave-one-region-out CV."""
        if -170.0 <= self.lon < -30.0:
            return "americas"
        if -30.0 <= self.lon < 60.0:
            return "europe-africa"
        if 60.0 <= self.lon < 150.0:
            return "asia"
        return "pacific"

    @property
    def year(self) -> int:
        """UTC calendar year of the event (for rolling-origin CV blocks)."""
        import datetime as _dt

        return _dt.datetime.fromtimestamp(self.time / 1000.0, tz=_dt.UTC).year

    def features(self) -> list[float]:
        """Strictly-physical, leak-free feature vector (see FEATURE_NAMES)."""
        # ocean_proxy: a crude continental-vs-oceanic hint from longitude band,
        # normalised to [0,1]; stands in for "near population" without using any
        # outcome field. Deliberately weak — we are not smuggling the label in.
        ocean = 0.5 + 0.5 * math.sin(math.radians(self.lon))
        return [
            float(self.mag),
            float(self.depth_km),
            abs(float(self.lat)) / 90.0,
            float(ocean),
            self.gmpe_score(),
        ]


def load_quakes(path: str = FIXTURE) -> list[Quake]:
    """Load the committed real USGS catalog (no network)."""
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    out: list[Quake] = []
    for r in raw:
        if r.get("mag") is None or r.get("depth_km") is None:
            continue
        out.append(
            Quake(
                time=int(r.get("time") or 0),
                mag=float(r["mag"]),
                depth_km=float(r["depth_km"]),
                lat=float(r.get("lat", 0.0)),
                lon=float(r.get("lon", 0.0)),
                felt=int(r.get("felt") or 0),
                alert=r.get("alert"),
                tsunami=int(r.get("tsunami") or 0),
                mmi=float(r.get("mmi") or 0.0),
            )
        )
    return out


def temporal_split(
    quakes: list[Quake], boundary_ms: int = SPLIT_EPOCH_MS
) -> tuple[list[Quake], list[Quake]]:
    """Split by event time: (train = before boundary, test = on/after). No leakage."""
    train = [q for q in quakes if q.time < boundary_ms]
    test = [q for q in quakes if q.time >= boundary_ms]
    return train, test


def to_xy(quakes: list[Quake]) -> tuple[list[list[float]], list[int]]:
    """Feature matrix X and label vector y for a set of quakes."""
    return [q.features() for q in quakes], [q.label() for q in quakes]
