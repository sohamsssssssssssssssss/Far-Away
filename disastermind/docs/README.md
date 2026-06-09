# DisasterMind — Documentation

Operator- and developer-facing docs for the DisasterMind autonomous disaster
coordination platform. All facts are derived from `README.md` and the code
(topic names, endpoints, CLI flags, and model names match the source).

| Doc | What it covers |
|-----|----------------|
| [architecture.md](./architecture.md) | The three-tier authority model, hazard modules + activation triggers, the single MessageBus + topic catalogue, the full agent roster, autonomy/escalation model, equity & priority, audit/explainability, and graceful degradation. |
| [openapi.yaml](./openapi.yaml) | OpenAPI 3.1 spec for the Commander Dashboard HTTP API (`/health`, `/topics`, `/incidents`, `/escalations`, approve/reject) plus the `WS /ws` stream protocol. Swagger-UI renderable. |
| [sequence-diagrams.md](./sequence-diagrams.md) | Mermaid diagrams: the autonomous coordination loop, the escalation flow (approve / reject / timeout / human-only), the field-device round-trip, and the topic dataflow graph. |
| [runbook.md](./runbook.md) | Operator runbook: install, bring up backends, drive the loop, simulate scenarios, serve the dashboard, handle escalations, verify the audit chain, and interpret graceful degradation. |

## Related surfaces

- **Operator console** — `clients/operator-console/` (React/Vite dashboard).
- **Field app** — `clients/field-app/` (React Native device client).
- **Integration tests** — `tests/integration/` (docker-compose-backed).

## Render the OpenAPI spec

```bash
npx @redocly/cli preview-docs docs/openapi.yaml      # or paste into editor.swagger.io
```
