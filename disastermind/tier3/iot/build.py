"""Factory for the Tier 3 IoT gateway module (PRD Step 2).

The orchestration layer calls :func:`build_agents` to instantiate every IoT
gateway in this module. Gateways are pure edge producers: they sample fixture
sensor meshes, aggregate threshold/cluster breaches, and emit telemetry on
:data:`~disastermind.core.contracts.Topic.IOT_TELEMETRY` from their ``tick()``.
No network and no decision authority.
"""
from __future__ import annotations

from ...audit.decision_log import DecisionLogger
from ...core.bus import MessageBus
from ...core.config import Settings
from .gateways import (
    GpsBeaconGateway,
    IoTGateway,
    SmokeHeatGateway,
    StructuralGateway,
    WaterloggingGateway,
)


def build_agents(
    bus: MessageBus,
    logger: DecisionLogger,
    settings: Settings,
    incident_id: str | None = None,
) -> list[IoTGateway]:
    """Instantiate and return all IoT gateway agents (PRD Step 2).

    Returns one of each gateway type: municipal smoke/heat, urban waterlogging,
    structural arrays, and field-team GPS beacons. The orchestration loop drives
    them via ``run_tick()`` each coordination cycle (PRD Step 10).
    """
    return [
        SmokeHeatGateway(
            "iot.smoke_heat", bus, logger, incident_id=incident_id
        ),
        WaterloggingGateway(
            "iot.waterlogging", bus, logger, incident_id=incident_id
        ),
        StructuralGateway(
            "iot.structural", bus, logger, incident_id=incident_id
        ),
        GpsBeaconGateway(
            "iot.gps_beacon", bus, logger, incident_id=incident_id
        ),
    ]
