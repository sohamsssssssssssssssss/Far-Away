"""Model wrappers — the real model layer behind the prediction heuristics.

PRD Step 3 (Prediction & Assessment). Each module's risk model is a thin wrapper
with a uniform interface:

    fit(X, y)          -> self           (train, if a real backend is available)
    predict(X)         -> list[float]    (probabilities in [0, 1], one per row)
    predict_one(fv)    -> float          (single FeatureVector convenience)
    save(path) / load(path)              (persist / restore artefacts locally)

Backends are tried lazily and degrade gracefully (PRD Step 10):

  1. XGBoost (``XGBoostRiskModel``) — gradient-boosted trees, lazy ``import
     xgboost``; used by Module A/B (tabular drivers).
  2. scikit-learn (``SklearnRiskModel``) — logistic-regression baseline, lazy
     ``import sklearn``.
  3. Deterministic stdlib HEURISTIC (always available) — a logistic over a
     fixed per-module weight vector. This is the fallback when the optional
     library is absent OR when the wrapper has not been fitted, and its OUTPUT
     SHAPE matches what the tier-2 prediction agents already produce:
     probabilities in [0, 1].

The heuristic weights are tuned so each module's dominant driver behaves like the
agents' heuristics (rainfall/surge/river for floods; magnitude/proximity for
quakes; intensity/wind for fire), giving deterministic, monotone, in-range output
with NO optional dependency and NO network.

Artefacts persist as stdlib JSON (``save``/``load``) so a fitted model — real or
heuristic — round-trips without pickle/network. Real boosters additionally
serialise their native model file alongside the JSON manifest when present.
"""
from __future__ import annotations

import json
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..core.contracts import Module
from .features import FEATURE_NAMES, FeatureVector


# --------------------------------------------------------------------------- math
def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _logistic(x: float) -> float:
    """Numerically-safe logistic squash to (0, 1) (mirrors tier-2 agents)."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


# Per-module heuristic logistic parameters. Each entry is (intercept, weights,
# scales): probability = logistic(intercept + sum(w_i * value_i / scale_i)).
# Scales normalise raw drivers into a comparable range so the logistic is well
# conditioned; weights set each driver's pull toward collapse/inundation/burn.
@dataclass(frozen=True)
class _HeuristicParams:
    intercept: float
    weights: tuple[float, ...]
    scales: tuple[float, ...]


_HEURISTICS: dict[Module, _HeuristicParams] = {
    # magnitude up (more shaking), distance up (less shaking -> negative),
    # construction ordinal up (more resilient -> negative).
    Module.EARTHQUAKE: _HeuristicParams(
        intercept=-2.0, weights=(2.6, -1.8, -1.1), scales=(9.0, 80.0, 2.0)
    ),
    # rainfall, surge, river level all push inundation up.
    Module.CYCLONE_FLOOD: _HeuristicParams(
        intercept=-1.6, weights=(1.9, 1.3, 1.1), scales=(300.0, 6.0, 8.0)
    ),
    # intensity, wind and fuel all push burn probability up.
    Module.FIRE_COLLAPSE: _HeuristicParams(
        intercept=-1.4, weights=(1.7, 1.2, 0.8), scales=(3.0, 25.0, 3.0)
    ),
}


def heuristic_probability(module: Module, values: Sequence[float]) -> float:
    """Deterministic logistic fallback probability in [0, 1] for one row.

    PRD Step 10 graceful degradation: no optional dependency, no network, fully
    deterministic. Output shape matches the tier-2 agents (a probability).
    """
    p = _HEURISTICS.get(module)
    if p is None:
        raise ValueError(f"no heuristic for module {module!r}")
    z = p.intercept
    for w, s, v in zip(p.weights, p.scales, values):
        z += w * (float(v) / (s or 1.0))
    return _clamp01(_logistic(z))


# --------------------------------------------------------------------------- base
class RiskModel:
    """Common interface + always-available heuristic fallback.

    Subclasses override :meth:`_fit_backend` / :meth:`_predict_backend` to wire a
    real library, returning ``None`` from the predict hook to signal "fall back".
    """

    backend = "heuristic"

    def __init__(self, module: Module) -> None:
        if module not in FEATURE_NAMES:
            raise ValueError(f"unsupported module {module!r}")
        self.module = module
        self.feature_names: tuple[str, ...] = FEATURE_NAMES[module]
        self.fitted: bool = False
        self._n_train: int = 0
        self._backend_obj: Any = None

    # -- training ---------------------------------------------------------
    def fit(self, X: Sequence[Sequence[float]], y: Sequence[float]) -> RiskModel:
        """Fit the real backend if available; otherwise mark heuristic-fitted.

        Always succeeds: an absent/failing backend leaves the deterministic
        heuristic in place (still usable), so the system never hard-fails on a
        missing ML library (PRD Step 10).
        """
        rows = [list(map(float, r)) for r in X]
        labels = [float(v) for v in y]
        if rows and len(rows) != len(labels):
            raise ValueError("X and y length mismatch")
        for r in rows:
            if len(r) != len(self.feature_names):
                raise ValueError(
                    f"expected {len(self.feature_names)} features, got {len(r)}"
                )
        self._n_train = len(rows)
        obj = self._fit_backend(rows, labels)
        if obj is not None:
            self._backend_obj = obj
        self.fitted = True
        return self

    # -- inference --------------------------------------------------------
    def predict(self, X: Sequence[Sequence[float]]) -> list[float]:
        """Return one probability in [0, 1] per input row."""
        rows = [list(map(float, r)) for r in X]
        backend = self._predict_backend(rows) if self._backend_obj is not None else None
        if backend is not None:
            return [_clamp01(float(p)) for p in backend]
        return [heuristic_probability(self.module, r) for r in rows]

    def predict_one(self, fv: FeatureVector | Sequence[float]) -> float:
        values = fv.as_list() if isinstance(fv, FeatureVector) else list(fv)
        return self.predict([values])[0]

    # -- backend hooks (overridden by real wrappers) ----------------------
    def _fit_backend(self, X: list[list[float]], y: list[float]) -> Any | None:
        return None

    def _predict_backend(self, X: list[list[float]]) -> list[float] | None:
        return None

    # -- persistence ------------------------------------------------------
    def manifest(self) -> dict[str, Any]:
        """JSON-serialisable description of this model (artefact header)."""
        return {
            "format": "disastermind.ml/1",
            "backend": self.backend,
            "module": self.module.value,
            "feature_names": list(self.feature_names),
            "fitted": self.fitted,
            "n_train": self._n_train,
        }

    def save(self, path: str) -> str:
        """Persist the model manifest as JSON to ``path``. Returns ``path``.

        Real backends additionally drop a native artefact next to the manifest
        (see :meth:`_save_backend`); the heuristic needs only the manifest.
        """
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        manifest = self.manifest()
        sidecar = self._save_backend(path)
        if sidecar:
            manifest["backend_artifact"] = sidecar
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, sort_keys=True, indent=2)
        return path

    @classmethod
    def load(cls, path: str) -> RiskModel:
        """Restore a model from a JSON manifest written by :meth:`save`.

        Dispatches to the wrapper class named in the manifest's ``backend``; if
        that backend's library is unavailable the loaded model still answers
        ``predict`` via the heuristic (graceful degradation).
        """
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        module = Module(manifest["module"])
        backend = manifest.get("backend", "heuristic")
        wrapper_cls = _BACKEND_REGISTRY.get(backend, RiskModel)
        model = wrapper_cls(module)
        model.fitted = bool(manifest.get("fitted", False))
        model._n_train = int(manifest.get("n_train", 0))
        artifact = manifest.get("backend_artifact")
        if artifact:
            try:
                model._backend_obj = model._load_backend(os.path.join(
                    os.path.dirname(os.path.abspath(path)), os.path.basename(artifact)
                ))
            except Exception:
                model._backend_obj = None  # degrade to heuristic
        return model

    def _save_backend(self, path: str) -> str | None:
        return None

    def _load_backend(self, artifact_path: str) -> Any | None:
        return None


# --------------------------------------------------------------------------- xgboost
class XGBoostRiskModel(RiskModel):
    """Gradient-boosted-tree risk model (PRD Step 3, Modules A/B primary).

    Lazy ``import xgboost``; if absent or unfitted, inference uses the
    deterministic heuristic so callers always get probabilities in [0, 1].
    """

    backend = "xgboost"

    def _fit_backend(self, X: list[list[float]], y: list[float]) -> Any | None:
        try:
            import numpy as np  # type: ignore
            import xgboost as xgb  # type: ignore
        except Exception:
            return None
        if not X:
            return None
        try:
            clf = xgb.XGBRegressor(
                n_estimators=64, max_depth=3, learning_rate=0.1, objective="reg:logistic"
            )
            clf.fit(np.asarray(X, dtype=float), np.asarray(y, dtype=float))
            return clf
        except Exception:
            return None

    def _predict_backend(self, X: list[list[float]]) -> list[float] | None:
        try:
            import numpy as np  # type: ignore
        except Exception:
            return None
        try:
            preds = self._backend_obj.predict(np.asarray(X, dtype=float))
            return [float(p) for p in preds]
        except Exception:
            return None

    def _save_backend(self, path: str) -> str | None:  # pragma: no cover - needs xgboost
        if self._backend_obj is None:
            return None
        try:
            artifact = path + ".xgb.json"
            self._backend_obj.save_model(artifact)
            return os.path.basename(artifact)
        except Exception:
            return None

    def _load_backend(self, artifact_path: str) -> Any | None:  # pragma: no cover
        try:
            import xgboost as xgb  # type: ignore

            clf = xgb.XGBRegressor()
            clf.load_model(artifact_path)
            return clf
        except Exception:
            return None


# --------------------------------------------------------------------------- sklearn
class SklearnRiskModel(RiskModel):
    """Logistic-regression baseline risk model (PRD Step 3 alternative backend).

    Lazy ``import sklearn``; same graceful-degradation contract as the XGBoost
    wrapper — heuristic fallback keeps output in [0, 1].
    """

    backend = "sklearn"

    def _fit_backend(self, X: list[list[float]], y: list[float]) -> Any | None:
        try:
            import numpy as np  # type: ignore
            from sklearn.linear_model import LogisticRegression  # type: ignore
        except Exception:
            return None
        if not X:
            return None
        try:
            clf = LogisticRegression(max_iter=500)
            # Logistic regression needs >=2 classes; binarise continuous risk.
            labels = [1 if v >= 0.5 else 0 for v in y]
            if len(set(labels)) < 2:
                return None
            clf.fit(np.asarray(X, dtype=float), np.asarray(labels, dtype=int))
            return clf
        except Exception:
            return None

    def _predict_backend(self, X: list[list[float]]) -> list[float] | None:
        try:
            import numpy as np  # type: ignore
        except Exception:
            return None
        try:
            proba = self._backend_obj.predict_proba(np.asarray(X, dtype=float))
            return [float(row[1]) for row in proba]
        except Exception:
            return None

    def _save_backend(self, path: str) -> str | None:  # pragma: no cover - needs sklearn
        if self._backend_obj is None:
            return None
        try:
            import pickle

            artifact = path + ".sk.pkl"
            with open(artifact, "wb") as fh:
                pickle.dump(self._backend_obj, fh)
            return os.path.basename(artifact)
        except Exception:
            return None

    def _load_backend(self, artifact_path: str) -> Any | None:  # pragma: no cover
        try:
            import pickle

            with open(artifact_path, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            return None


class HeuristicRiskModel(RiskModel):
    """Explicit stdlib-only model (no optional backend). Always available."""

    backend = "heuristic"


# Backend name -> wrapper class, used by :meth:`RiskModel.load`.
_BACKEND_REGISTRY: dict[str, type[RiskModel]] = {
    "xgboost": XGBoostRiskModel,
    "sklearn": SklearnRiskModel,
    "heuristic": HeuristicRiskModel,
}
