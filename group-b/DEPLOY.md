# Group B — Deployment Guide

## Vercel (Frontend)

### First Deploy
1. Go to https://vercel.com/new
2. Import the `Far-Away` GitHub repository
3. Set Root Directory to: `group-b/disastermind-unified`
4. Framework Preset: Vite
5. Build Command: `npm run build`
6. Output Directory: `dist`
7. Add environment variables (see below)
8. Click Deploy

### Environment Variables (Vercel Dashboard)
Set these under Project Settings → Environment Variables:

| Variable | Value |
|----------|-------|
| VITE_API_URL | https://your-group-a-backend.railway.app |
| VITE_WS_URL | wss://your-group-a-backend.railway.app/ws |
| VITE_OLLAMA_URL | (leave blank — Ollama is local only) |
| VITE_ENV | production |

### Subsequent Deploys
Every push to `main` auto-deploys via Vercel GitHub integration.

## Local Development
```bash
cd group-b/disastermind-unified
npm install
cp .env.example .env
npm run dev
```

## Production Preview (local)
```bash
npm run build
npx vite preview
```
