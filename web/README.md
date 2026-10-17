# PayPilot website

Next.js (App Router) + TypeScript + Tailwind CSS v4 + shadcn/ui-style components.

```bash
cp .env.example .env.local     # BACKEND_URL, default http://127.0.0.1:8000
npm install
npm run dev                    # http://localhost:3000
```

The browser only talks to this site: `/api/*` is proxied to the FastAPI backend (see `next.config.ts`), so
the backend's cookies stay first-party. Start the backend in the repo root with
`uvicorn api.index:app --reload --port 8000`.
