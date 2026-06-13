# DisasterMind — Documentation

Operator- and developer-facing docs for the DisasterMind multi-agent disaster
coordination platform. All facts are derived from `README.md` and the code
(topic names, endpoints, CLI flags, and model names match the source).

| Doc | What it covers |
|-----|----------------|
| [architecture.md](./architecture.md) | The three-tier authority model, hazard modules + activation triggers, the single MessageBus + topic catalogue, the full agent roster, autonomy/escalation model, equity & priority, audit/explainability, and graceful degradation. |
| [openapi.yaml](./openapi.yaml) | OpenAPI 3.1 spec for the Commander Dashboard HTTP API (`/health`, `/topics`, `/incidents`, `/escalations`, approve/reject) plus the `WS /ws` stream protocol. Swagger-UI renderable. |
| [sequence-diagrams.md](./sequence-diagrams.md) | Mermaid diagrams: the autonomous coordination loop, the escalation flow (approve / reject / timeout / human-only), the field-device round-trip, and the topic dataflow graph. |
| [validation.md](./validation.md) | Real-data model validation: leak-free multi-hazard datasets (USGS / GloFAS-ERA5 / FPA-FOD), operational baselines with significance, POD/FAR at the operating point, blocked spatial+temporal CV, calibrated uncertainty (isotonic + conformal), fairness audit, rare-severe tail, drift + retraining, and the shadow-mode trust gate. |
| [runbook.md](./runbook.md) | Operator runbook: install, bring up backends, drive the loop, simulate scenarios, serve the dashboard, handle escalations, verify the audit chain, and interpret graceful degradation. |

## Related surfaces

- **Web console** — `clients/web/` (Vite + React unified command-and-control UI:
  Commander Dashboard, Escalation, Field Ops, Post-Incident Report).
- **Integration tests** — `tests/integration/` (docker-compose-backed).

## Render the OpenAPI spec

```bash
npx @redocly/cli preview-docs docs/openapi.yaml      # or paste into editor.swagger.io
```
