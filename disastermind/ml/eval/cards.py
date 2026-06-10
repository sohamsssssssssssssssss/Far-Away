"""Model cards (PRD Step 9 / Step 10 — explainability + honest provenance).

A model card is a short, structured document that travels with a model so an
operator never deploys a black box: what it predicts, what it was trained on, how
it scored on held-out data, what it is *for*, and — critically for a disaster
system — what its **limitations** are. Production models are trained on REAL
historical fixtures (:mod:`disastermind.ml.training.real`) and the full
real-data evidence (baselines with significance, POD/FAR at the operating
point, blocked CV, calibration, fairness, tail, drift) lives in
:mod:`disastermind.ml.validation`; the card still states plainly that
probabilities inform but never replace a human commander.

:func:`model_card` builds a JSON-serialisable ``dict``; :func:`to_markdown`
renders it to human-readable Markdown. Both are pure stdlib.
"""
from __future__ import annotations

from typing import Any

from ...core.contracts import Module
from ..features import FEATURE_NAMES
from .metrics import Metrics

#: Human-readable names per module value (A/B/C).
MODULE_TITLES: dict[str, str] = {
    "A": "Cyclone / Flood risk",
    "B": "Earthquake damage risk",
    "C": "Urban fire / structural-collapse risk",
}

#: Per-module one-line statement of what the model's probability means.
INTENDED_USE: dict[str, str] = {
    "A": (
        "Estimate per-asset inundation/impact risk from rainfall, storm surge and "
        "river level to prioritise evacuation and resource pre-positioning."
    ),
    "B": (
        "Estimate per-structure damage risk from event magnitude, epicentral "
        "distance and construction class to triage search-and-rescue."
    ),
    "C": (
        "Estimate per-zone fire-spread / collapse risk from fire intensity, wind "
        "speed and fuel load to sequence suppression and shoring."
    ),
}

#: Limitations every card carries, regardless of module.
BASE_LIMITATIONS: tuple[str, ...] = (
    "Trained on real historical fixtures via schema mappings that approximate "
    "the runtime features (see disastermind.ml.training.real) — full real-data "
    "validation evidence lives in disastermind.ml.validation, and the system "
    "must clear shadow-mode review (disastermind.ml.shadow) before outputs "
    "influence operations.",
    "Outputs are decision support only and never override a human commander.",
    "Falls back to a deterministic heuristic when no ML backend is installed; "
    "metrics then reflect the heuristic, not a learned model.",
    "Backtest metrics in this card are produced by the synthetic harness when "
    "invoked from disastermind.ml.eval.backtest and do not substitute for the "
    "real-data validation report.",
)


def model_card(
    module: Module | str,
    model: Any,
    metrics: Metrics,
    *,
    n_train: int | None = None,
) -> dict[str, Any]:
    """Build a JSON-serialisable model card for a fitted ``model``.

    ``model`` is a :class:`disastermind.ml.models.RiskModel` (or anything exposing
    ``backend`` / ``fitted`` / ``feature_names``); ``metrics`` is the held-out
    :class:`Metrics`. ``n_train`` overrides the training size when the caller knows
    it (the backtest passes the post-split train count).
    """
    module = module if isinstance(module, Module) else Module(module)
    mv = module.value
    backend = getattr(model, "backend", "heuristic")
    backend_active = getattr(model, "_backend_obj", None) is not None
    features = list(getattr(model, "feature_names", FEATURE_NAMES.get(module, ())))
    n_train = int(n_train) if n_train is not None else int(getattr(model, "_n_train", 0))

    limitations = list(BASE_LIMITATIONS)
    if not backend_active:
        limitations.insert(
            0,
            f"Active scorer is the deterministic heuristic (no '{backend}' backend "
            "available in this environment).",
        )

    return {
        "format": "disastermind.ml.eval.card/1",
        "module": mv,
        "title": MODULE_TITLES.get(mv, f"Module {mv} risk"),
        "backend": backend,
        "backend_active": backend_active,
        "fitted": bool(getattr(model, "fitted", False)),
        "features": features,
        "n_train": n_train,
        "intended_use": INTENDED_USE.get(mv, "Per-asset risk estimation."),
        "metrics": metrics.to_dict(),
        "limitations": limitations,
    }


def _fmt(x: float) -> str:
    return f"{x:.4f}"


def to_markdown(card: dict[str, Any]) -> str:
    """Render a model card ``dict`` (from :func:`model_card`) to Markdown."""
    m = card.get("metrics", {})
    lines: list[str] = []
    lines.append(f"# Model Card — {card.get('title', 'risk model')} (Module {card.get('module', '?')})")
    lines.append("")
    lines.append(f"- **Backend:** {card.get('backend', 'heuristic')} "
                 f"({'active' if card.get('backend_active') else 'heuristic fallback'})")
    lines.append(f"- **Fitted:** {card.get('fitted', False)}")
    lines.append(f"- **Training rows:** {card.get('n_train', 0)}")
    lines.append("")
    lines.append("## Intended use")
    lines.append("")
    lines.append(card.get("intended_use", ""))
    lines.append("")
    lines.append("## Features")
    lines.append("")
    for f in card.get("features", []):
        lines.append(f"- `{f}`")
    lines.append("")
    lines.append("## Held-out metrics")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("| --- | --- |")
    lines.append(f"| test rows | {m.get('n', 0)} |")
    lines.append(f"| prevalence | {_fmt(float(m.get('prevalence', 0.0)))} |")
    lines.append(f"| AUC | {_fmt(float(m.get('auc', 0.0)))} |")
    lines.append(f"| Brier | {_fmt(float(m.get('brier', 0.0)))} |")
    lines.append(f"| accuracy@{_fmt(float(m.get('threshold', 0.5)))} | {_fmt(float(m.get('accuracy', 0.0)))} |")
    lines.append(f"| ECE | {_fmt(float(m.get('ece', 0.0)))} |")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    for lim in card.get("limitations", []):
        lines.append(f"- {lim}")
    lines.append("")
    return "\n".join(lines)
