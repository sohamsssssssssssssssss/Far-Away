"""Tier 3 notification-dispatch module (PRD Step 8).

Executes ``Topic.DISPATCH`` orders over SMS, FCM push, Iridium satellite, CAP
emergency broadcast and a field-radio gateway. NO decision authority.
"""
from __future__ import annotations

from .build import build_agents, build_channels
from .channels import (
    CapChannel,
    Channel,
    FcmPushChannel,
    FieldRadioChannel,
    IridiumChannel,
    SmsChannel,
)
from .router import DispatchRouter

__all__ = [
    "build_agents",
    "build_channels",
    "Channel",
    "SmsChannel",
    "FcmPushChannel",
    "IridiumChannel",
    "CapChannel",
    "FieldRadioChannel",
    "DispatchRouter",
]
