"""Factory for the dispatch module (PRD Step 8, Tier 3).

The orchestration layer calls :func:`build_agents` to instantiate every agent in
this module. Dispatch consists of a single :class:`DispatchRouter` agent that
owns all five notification channels (SMS, FCM push, Iridium satellite, CAP
broadcast, field-radio). Only the router subscribes to the bus — channels are
plain executors the router drives — so the returned list contains the router.

All channels default to ``dry_run=True`` so the package imports and the
test-suite runs with stdlib only and **no network calls** (graceful degradation,
PRD Step 10). Set ``DM_DISPATCH_LIVE=1`` (or ``settings`` flags) to go live.
"""
from __future__ import annotations

import os

from ...audit.decision_log import DecisionLogger
from ...core.bus import MessageBus
from ...core.config import Settings
from .channels import (
    CapChannel,
    Channel,
    FcmPushChannel,
    FieldRadioChannel,
    IridiumChannel,
    SmsChannel,
)
from .router import DispatchRouter


def build_channels(
    settings: Settings, dry_run: bool, *, live: bool | None = None
) -> list[Channel]:
    """Instantiate every notification channel (PRD Step 8).

    ``dry_run`` is the master gate (default-on keeps the suite offline). ``live``
    is an optional explicit override of the per-channel live switch; when left as
    ``None`` each channel consults ``DM_DISPATCH_LIVE`` (default off).
    """
    return [
        SmsChannel(settings=settings, dry_run=dry_run, live=live),
        FcmPushChannel(settings=settings, dry_run=dry_run, live=live),
        IridiumChannel(settings=settings, dry_run=dry_run, live=live),
        CapChannel(settings=settings, dry_run=dry_run, live=live),
        FieldRadioChannel(settings=settings, dry_run=dry_run, live=live),
    ]


def build_agents(bus: MessageBus, logger: DecisionLogger, settings: Settings) -> list:
    """Construct and return the dispatch module's agents (the router).

    The router internally holds the channels; the channels are not bus
    subscribers themselves, so only the router is returned to the orchestrator.
    """
    # Default to dry-run unless explicitly opted into live delivery — keeps tests
    # and degraded operation network-free (PRD Step 10). The same switch arms the
    # per-channel ``live`` flag so real sends actually fire when opted in.
    live = os.environ.get("DM_DISPATCH_LIVE", "").lower() in {"1", "true", "yes", "on"}
    channels = build_channels(settings, dry_run=not live, live=live)
    router = DispatchRouter(bus=bus, logger=logger, channels=channels, settings=settings)
    return [router]
