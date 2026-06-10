"""Tier 3 IoT gateway agents (PRD Step 2).

Edge sensor gateways with no decision authority. They aggregate municipal
smoke/heat detectors, urban waterlogging meshes, structural arrays, and
field-team GPS beacons, emitting telemetry on ``Topic.IOT_TELEMETRY``.
"""
from __future__ import annotations

from .build import build_agents
from .gateways import (
    GpsBeaconGateway,
    IoTGateway,
    SensorSite,
    SmokeHeatGateway,
    StructuralGateway,
    WaterloggingGateway,
)

__all__ = [
    "build_agents",
    "IoTGateway",
    "SensorSite",
    "SmokeHeatGateway",
    "WaterloggingGateway",
    "StructuralGateway",
    "GpsBeaconGateway",
]
