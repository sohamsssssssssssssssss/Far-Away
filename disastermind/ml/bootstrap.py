"""disastermind.ml.bootstrap — wire trained artefacts into the prediction seam.

PRD Step 10 (operational integration). The tier-2 prediction agents consume
per-module risk models via :func:`disastermind.ml.get_model`, and they only
engage their real backend paths (``_try_ensemble`` / ``_try_hazus`` / ``_try_ca``)
when the resolved model has a live backend object (``RiskModel._backend_obj``).
That object is set when a *fitted* artefact — produced by
:func:`disastermind.ml.training.train_all` — is restored. This module is the glue
that, at boot, finds those artefacts and registers them so ``get_model`` returns
the trained model instead of a fresh (heuristic-only) wrapper.

The default is **offline-safe**: if no models directory is configured, or it has
no artefacts, :func:`ensure_models_loaded` does nothing and the system stays on
its deterministic stdlib heuristics — exactly the behaviour the existing tests
assume. Loading is opt-in (a configured directory) and inert otherwise.

``ensure_models_loaded`` is **idempotent** and safe to call repeatedly at boot:
re-registering the same artefact is harmless. It is stdlib-only on the import and
happy path and performs no network I/O — it only reads local JSON artefacts.
"""
from __future__ import annotations

import os
from typing import Any

from ..core.contracts import Module
from .registry import register_model
from .training import artifact_path, load_trained

#: Modules considered at boot, in stable A/B/C order.
_MODULES: tuple[Module, ...] = (
    Module.CYCLONE_FLOOD,
    Module.EARTHQUAKE,
    Module.FIRE_COLLAPSE,
)

#: Environment variable naming the directory that holds trained artefacts.
MODELS_DIR_ENV = "DM_MODELS_DIR"


def resolve_models_dir(settings: Any = None, models_dir: str | None = None) -> str | None:
    """Resolve the trained-artefacts directory, or ``None`` if unconfigured.

    Resolution order (first hit wins):

      1. the explicit ``models_dir`` argument,
      2. a ``models_dir`` attribute on ``settings`` (if the Settings object
         exposes one — looked up defensively so a frozen Settings without it is
         fine),
      3. the :data:`MODELS_DIR_ENV` (``DM_MODELS_DIR``) environment variable.

    Blank/whitespace values are treated as unset. Returns ``None`` when nothing
    is configured — the offline-safe default that keeps the system on heuristics.
    """
    candidate = models_dir
    if not (candidate and candidate.strip()):
        candidate = getattr(settings, "models_dir", None) if settings is not None else None
    if not (candidate and candidate.strip()):
        candidate = os.environ.get(MODELS_DIR_ENV)
    if candidate and candidate.strip():
        return os.path.abspath(candidate.strip())
    return None


def ensure_models_loaded(
    settings: Any = None, models_dir: str | None = None
) -> dict[str, bool]:
    """Load + register any trained artefacts found in the models directory.

    For each module A/B/C, if a trained artefact exists in the resolved directory
    it is restored via :func:`disastermind.ml.training.load_trained` and installed
    with :func:`disastermind.ml.register_model`, so a subsequent
    :func:`disastermind.ml.get_model` returns the trained model (whose
    ``_backend_obj`` is live, engaging the prediction agents' real backend paths).

    Returns a ``{module_value: loaded}`` map (``"A"``/``"B"``/``"C"`` -> bool):
    ``True`` where a trained artefact was registered this call, ``False`` where
    none was found (that module keeps its existing/heuristic wrapper).

    Offline-safe by default: with no configured directory (or an absent one) this
    is a no-op returning all-``False`` and the registry is left untouched, so the
    system stays on its deterministic heuristics. Idempotent and safe to call at
    boot — re-registering an already-loaded artefact is harmless.
    """
    result: dict[str, bool] = {m.value: False for m in _MODULES}

    directory = resolve_models_dir(settings, models_dir)
    if directory is None or not os.path.isdir(directory):
        return result

    for module in _MODULES:
        path = artifact_path(directory, module)
        if not os.path.isfile(path):
            continue
        try:
            model = load_trained(directory, module)
            register_model(module, model)
        except Exception:
            # An unreadable / malformed artefact must never break boot: leave the
            # module on its existing wrapper (heuristic fallback) and move on.
            continue
        result[module.value] = True

    return result
