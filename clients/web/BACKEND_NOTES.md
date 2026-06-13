# Notes for the backend-data session (from the frontend/clients-web lane)

## ✅ RESOLVED — degraded-mode demo endpoint (frontend item #3)

The backend already shipped this — thank you. The Resilience tab
(`clients/web/src/modules/evidence/components/Resilience.tsx`) is now wired to the
**real** contract you implemented in `disastermind/api/app.py`:

```
GET  /demo/status
     → { degraded_components: string[], operational: true,
         mode: "nominal"|"degraded", known_components: string[] }
POST /demo/degrade?component=<id>&active=true|false
POST /demo/degrade?reset=true
     → returns the same status shape
```

The UI reads `known_components` and renders a toggle per component (grouped into
Data feeds / Broker & storage / Models), reflects live `/demo/status`, shows the
"degraded ≠ down — still operational" state, and gracefully shows a "backend not
reachable" message when the API isn't running (never fakes success).

Base URL comes from `config.api.baseUrl` (`VITE_API_URL`, default
`http://localhost:8000`); the demo routes are consumed at `/demo/*` (not `/v1`).

No further backend work needed for item #3.

---
*Frontend consumes contracts only; nothing in the backend lane was edited.*
