# Deploying DisasterMind Group B to Vercel

## Prerequisites
- Vercel account (free tier is fine): https://vercel.com
- Vercel CLI: npm install -g vercel
- Far-Away repo pushed to GitHub

## First-time deploy (5 minutes)

1. cd into disastermind-unified:
   cd ~/japan\ baby/group-b/disastermind-unified

2. Login to Vercel:
   vercel login

3. Deploy:
   vercel --cwd . --prod

   When prompted:
   - Set up and deploy? → Y
   - Which scope? → your personal account
   - Link to existing project? → N
   - Project name? → disastermind-group-b
   - Directory? → ./  (just press Enter)
   - Override settings? → N

4. Vercel will print a live URL. That's your deployment.

## Setting environment variables in Vercel

After first deploy, go to https://vercel.com/dashboard → your project → Settings → Environment Variables.

Add these for Production:
  VITE_API_URL        = https://your-group-a-backend.railway.app
  VITE_WS_URL         = wss://your-group-a-backend.railway.app/ws
  VITE_ENV            = production

Leave Ollama vars blank for production — Ollama is local dev only.
For LLM in production, add one of:
  VITE_ANTHROPIC_API_KEY  = sk-ant-...
  VITE_GEMINI_API_KEY     = AIza...

After adding vars, redeploy:
  vercel --cwd . --prod

## Subsequent deploys

Any push to main on GitHub will auto-deploy if you connect the repo in Vercel dashboard
(Settings → Git → Connect Git Repository).

Manual deploy anytime:
  vercel --cwd . --prod

## Verify deployment

1. Open the Vercel URL
2. You should see the DisasterMind splash screen
3. Commander tab → status bar should show ● GROUP A OFFLINE (expected, backend not deployed yet)
4. START DEMO button should be visible and functional
5. ESCALATION tab → click a scenario → click GENERATE → Ollama won't work (it's local), 
   but the UI should load without errors

## Troubleshooting

Build fails on Vercel:
  - Run npm run build locally first, fix all errors, then redeploy
  - Check that .env.example has all variables (Vercel reads this for hints)

Blank page after deploy:
  - vercel.json rewrites rule fixes React Router — make sure vercel.json is committed

Environment variables not working:
  - Vercel env vars must start with VITE_ to be accessible in the browser
  - After adding/changing vars in Vercel dashboard, always redeploy
