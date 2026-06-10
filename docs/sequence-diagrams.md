# DisasterMind — Sequence & Dataflow Diagrams

Mermaid diagrams for the autonomous coordination loop, the human-escalation
flow, and the field-device round-trip. Message labels are the real topic names
from `core/contracts.py::Topic`. These render natively on GitHub.

> Source of truth: `orchestration/loop.py`, `tier1/commander/`, `tier2/*`,
> `tier3/*`, `llm/narrator.py`, `api/`, and `fieldapp/contracts.py`.

---

## 1. Autonomous coordination loop (Steps 1–10)

One 30-second cycle of `CoordinationLoop.run_once()`. Every hop is a publish to a
topic on the single `MessageBus`; agents never call one another directly.

```mermaid
sequenceDiagram
    autonumber
    participant Trig as Triggers (Step 1)
    participant Edge as Tier 3 ingestion + IoT
    participant Pred as Tier 2 prediction
    participant Casc as Tier 2 cascade
    participant Res as Tier 2 resource (equity LP)
    participant Route as Tier 2 routing (VRP)
    participant Field as Tier 2 field coordinator
    participant Cmd as Tier 1 Commander
    participant Disp as Tier 3 dispatch router

    Trig->>Trig: should_activate(Signals) → disaster_active
    Edge->>Pred: tier3.raw_feed / tier3.iot_telemetry
    Pred->>Casc: tier2.prediction (risk cells + SHAP)
    Casc->>Res: tier2.cascade (route cutoffs / aftershock windows)
    Casc->>Route: tier2.cascade
    Res->>Route: tier2.resource_plan (deploy orders)
    Res->>Field: tier2.resource_plan
    Route->>Field: tier2.routing_plan (priority-ordered evac)
    Field->>Cmd: tier2.field_order
    alt within autonomous authority
        Cmd->>Disp: tier3.dispatch (immediate)
        Disp-->>Cmd: dispatch_ack
    else crosses an authority threshold
        Cmd->>Cmd: tier1.escalation (await human / 5-min timeout)
    end
    Note over Edge,Disp: loop repeats every DM_LOOP_INTERVAL (default 30s) while disaster_active
```

---

## 2. Escalation flow (Step 7)

How a threshold-crossing order reaches a human, the advisory LLM brief, and the
two terminal paths (auto-execute on timeout vs. human-only hold).

```mermaid
sequenceDiagram
    autonumber
    participant Field as Field coordinator
    participant Cmd as Commander (authority matrix)
    participant Narr as EscalationNarrator (LLM, advisory)
    participant Dash as Dashboard API (/escalations, /ws)
    participant Human as Human commander
    participant Disp as Dispatch router

    Field->>Cmd: tier2.field_order
    Cmd->>Cmd: classify(order) → Decision
    alt autonomous (no trigger)
        Cmd->>Disp: tier3.dispatch
    else escalation trigger
        Cmd->>Narr: tier1.escalation
        Narr->>Dash: tier1.escalation_narrative (5-section brief)
        Dash-->>Human: GET /escalations + /ws push
        alt human approves in time
            Human->>Dash: POST /escalations/{id}/approve
            Dash->>Cmd: approve(report_id)
            Cmd->>Disp: tier3.dispatch (via=human_approved)
        else human rejects
            Human->>Dash: POST /escalations/{id}/reject
            Dash->>Cmd: reject(report_id)
            Cmd-->>Dash: rejection ACK
        else 5-min timeout, NOT human-only
            Cmd->>Disp: tier3.dispatch (via=auto_execute_on_timeout)
        else 5-min timeout, human-only
            Cmd->>Cmd: keep escalation open (never auto-acts)
        end
    end
```

The human-only triggers (`international_aid_request`, `declare_state_of_emergency`,
`armed_forces_in_civil_situation`, `critical_national_infrastructure`) are the
`HUMAN_ONLY_TRIGGERS` frozenset — the Commander never auto-executes them.

---

## 3. Field-device round-trip (Steps 6 & 8)

The web console's Field Ops module (`clients/web/`, `src/modules/field/`)
exercises the field-device contracts in `fieldapp/contracts.py`. Emissions ride
the durable outbox: terrestrial first, then Iridium satellite fallback, then
offline queue (Step 10).

```mermaid
sequenceDiagram
    autonumber
    participant Disp as Dispatch router
    participant App as Field app (device)
    participant Out as Outbox (terrestrial→Iridium→queue)
    participant Field as Field coordinator (Tier 2)

    Disp->>App: DeploymentOrderMsg (push / SMS / Iridium)
    App->>Out: OrderAck{accepted} (topic fieldapp.order_ack)
    Out->>Field: deliver (or queue offline, drain on reconnect)
    loop every 60s (PRD Step 6)
        App->>Out: TeamStatusUpdate.to_reading() {idle→enroute→onsite}
        Out->>Field: gps_beacon reading
    end
    App->>Out: SiteOverCapacityReport.to_reading() {site_over_capacity:true}
    Out->>Field: over-capacity reading
    Field->>Field: autonomous reinforcement → tier2.resource_plan
```

---

## Topic dataflow

The full publish/subscribe graph (compact form of the ASCII diagram in
[`architecture.md`](./architecture.md#5-topic-dataflow)).

```mermaid
flowchart TD
    subgraph T3in[Tier 3 — edge, no authority]
        ING[ingestion feeds<br/>USGS·NCS·CWC·IMD·Bhuvan·Open-Meteo·FIRMS·OWM]
        SOC[social-NLP]
        IOT[IoT gateways<br/>smoke/heat·waterlogging·structural·GPS@60s]
    end
    subgraph T2[Tier 2 — specialist, autonomous]
        PRED[prediction A/B/C]
        CASC[cascade<br/>flood cutoff · Omori-Utsu]
        RES[resource<br/>equity LP]
        ROUTE[routing<br/>multi-depot VRP]
        FIELD[field coordinator]
    end
    subgraph T1[Tier 1 — commander]
        CMD[CommanderAgent<br/>authority matrix]
        NARR[EscalationNarrator<br/>LLM, advisory]
    end
    subgraph T3out[Tier 3 — edge]
        DISP[dispatch router<br/>sms·push·iridium·cap·radio]
        WS[dashboard /ws + human]
    end

    ING -->|tier3.raw_feed| PRED
    SOC -->|tier3.raw_feed| PRED
    IOT -->|tier3.iot_telemetry| PRED
    IOT -->|tier3.iot_telemetry| FIELD
    PRED -->|tier2.prediction| CASC
    PRED -->|tier2.prediction| RES
    CASC -->|tier2.cascade| RES
    CASC -->|tier2.cascade| ROUTE
    RES -->|tier2.resource_plan| ROUTE
    RES -->|tier2.resource_plan| FIELD
    ROUTE -->|tier2.routing_plan| FIELD
    FIELD -->|tier2.field_order| CMD
    CMD -->|tier3.dispatch| DISP
    CMD -->|tier1.escalation| NARR
    NARR -->|tier1.escalation_narrative| WS
```
