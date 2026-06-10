"""CAP 1.2 emergency-broadcast alerting (PRD Step 8).

Stdlib-only Common Alerting Protocol output. Importing this package is inert
(no bus wiring, no network); call :func:`build_cap_alert` to render a
:class:`CapAlert` whose :meth:`CapAlert.to_xml` is a well-formed CAP document.
"""
from __future__ import annotations

from .cap import CAP_NAMESPACE, CapAlert, build_cap_alert

__all__ = ["CAP_NAMESPACE", "CapAlert", "build_cap_alert"]
