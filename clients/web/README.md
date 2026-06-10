# DisasterMind — Web Console (`clients/web`)

The unified human command-and-control interface for DisasterMind (Vite + React 19
+ TypeScript, MapLibre GL, Recharts). Four modules: **Commander Dashboard**,
**Escalation**, **Field Ops**, and **Post-Incident Report**. This single web app
replaces the earlier split `operator-console` (commander dashboard) and `field-app`
(field device) clients.

## Develop

```bash
cd clients/web
npm install
cp .env.example .env      # set VITE_API_BASE_URL (defaults to the deployed backend)
npm run dev               # http://localhost:5173
```

## Build / preview

```bash
npm run build             # type-check (tsc -b) + vite build -> dist/
npm run preview
```

## Backend wiring

`src/services/backendService.ts` talks to the DisasterMind API
(`/healthz`, `/escalations`, `/recent`, `/escalations/{id}/approve|reject`).
The base URL comes from `VITE_API_BASE_URL` and defaults to the deployed Railway
instance. Point it at a local backend (`python -m disastermind.api`) for dev.
