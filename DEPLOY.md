# Deployment

This is what I did to put the system online. The whole stack runs on free tiers.

## What goes where

```
┌────────────────────┐        ┌─────────────────────┐
│ Vercel             │        │ Railway             │
│ Next.js frontend   │ HTTPS  │ FastAPI backend     │
│ static + edge      │ ─────▶ │ Docker container    │
└────────────────────┘        └──────────┬──────────┘
                                         │
                       ┌─────────────────┼─────────────────┐
                       ▼                 ▼                 ▼
                ┌────────────┐    ┌────────────┐    ┌────────────┐
                │ Supabase   │    │ Qdrant     │    │ Groq API   │
                │ Postgres   │    │ Cloud      │    │ Llama 3.3  │
                │ (filings,  │    │ (vectors)  │    │ free tier  │
                │  XBRL,     │    └────────────┘    └────────────┘
                │  chunks)   │
                └────────────┘
```

Ingestion (downloading filings, embedding chunks) runs on my local machine, not in production. Once data is in Supabase + Qdrant, the production API is read-only.

## Prereqs

Five accounts (all free):

- GitHub: https://github.com (repo host)
- Railway: https://railway.app (backend)
- Vercel: https://vercel.com (frontend)
- Supabase: https://supabase.com (Postgres)
- Qdrant Cloud: https://cloud.qdrant.io (vector DB)
- Groq: https://console.groq.com (LLM, free key)

## Step 1: Push the repo

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<your-username>/financial-filings-agent.git
git push -u origin main
```

GitHub will ask for a Personal Access Token (Settings → Developer settings → Personal access tokens → Tokens classic, with `repo` scope).

## Step 2: Provision data infrastructure

### Supabase Postgres

1. New Project → name `filings-db` → save the database password.
2. Wait for provisioning to finish (1-2 min).
3. Settings → Database → Connection string (URI). Copy it. Looks like
   `postgresql://postgres:[PASSWORD]@db.xxx.supabase.co:5432/postgres`

Then apply the schema:

```bash
psql "<your-connection-string>" -f backend/db/schema.sql
```

This creates tables and seeds the 20 companies.

### Qdrant Cloud

1. Create Free Cluster → name `filings-chunks` → AWS, any region.
2. Once running, click the cluster → API Keys → create one. Copy URL + key.

## Step 3: Run ingestion locally (one-time)

This is the only step that doesn't run in production. The corpus has to be built once and persisted.

```bash
# Point local code at the cloud DB and Qdrant
cat > .env.prod-ingest <<EOF
GROQ_API_KEY=<your-groq-key>
SEC_USER_AGENT="Your Name your.email@example.com"
DATABASE_URL=postgresql+asyncpg://postgres:[PASSWORD]@db.xxx.supabase.co:5432/postgres
QDRANT_URL=https://xxx.cloud.qdrant.io
QDRANT_API_KEY=<your-qdrant-key>
EOF

# Run ingestion + embedding using the prod env
DOTENV_FILE=.env.prod-ingest uv run python -m backend.ingestion.run --tickers NVDA AAPL MSFT --years 2023 2024
DOTENV_FILE=.env.prod-ingest uv run python -m backend.indexing.embed_corpus
DOTENV_FILE=.env.prod-ingest uv run python -m backend.indexing.build_indexes
```

Start with 3 tickers × 2 years (~24 filings) for a working demo. Full 20-ticker × 5-year ingest is ~3-4 hours and produces ~60k chunks.

## Step 4: Deploy backend to Railway

1. Railway dashboard → New Project → Deploy from GitHub repo → pick `financial-filings-agent`.
2. Railway will detect the `Dockerfile` and start building.
3. Once first build finishes (it'll fail on missing env vars), open the project Variables tab and add:

```
GROQ_API_KEY            (your Groq key)
SEC_USER_AGENT          "Your Name your.email@example.com"
DATABASE_URL            (Supabase connection string with asyncpg driver)
QDRANT_URL              (Qdrant cluster URL)
QDRANT_API_KEY          (Qdrant API key)
ENVIRONMENT             production
LOG_LEVEL               INFO
GROQ_MODEL_PRIMARY      llama-3.3-70b-versatile
GROQ_MODEL_CHEAP        llama-3.1-8b-instant
LLM_PRIMARY_PROVIDER    groq
LLM_FALLBACK_PROVIDER   none
QDRANT_COLLECTION       filings_chunks
```

Important: `LLM_FALLBACK_PROVIDER=none` in prod since Ollama isn't available. The wrapper will surface a clear error if Groq is unavailable instead of trying to fall back to a missing local model.

Trigger a redeploy. It should come up green within ~2 min.

4. Settings → Networking → Generate Domain. Copy the public URL (e.g. `https://ffa-backend-production.up.railway.app`).

5. Smoke test:
```bash
curl https://<your-railway-url>/health
curl https://<your-railway-url>/tickers
```

## Step 5: Deploy frontend to Vercel

1. Vercel dashboard → Add New Project → Import the GitHub repo.
2. Set Root Directory to `frontend`.
3. Framework: Next.js (auto-detected).
4. Environment Variables:

```
NEXT_PUBLIC_BACKEND_URL    https://<your-railway-url>
```

5. Deploy. First build is ~1-2 min.
6. Once green, you'll get a URL like `https://financial-filings-agent.vercel.app`.

## Step 6: Test end-to-end

Open the Vercel URL. The header should show backend status as `ok`. Try one of the sample questions; the streaming chat should produce a cited answer in 5-15 seconds.

## Optional: Custom domain

Vercel → Project → Settings → Domains → add your custom domain. Same for Railway if you want a branded backend URL. CORS settings in `backend/api/main.py` are permissive (`allow_origins=["*"]`) for the demo; tighten if you ship anything sensitive.

## Optional: GitHub Actions secrets

For the CI eval-on-PR step to actually run the eval (not just the import smoke test), add these to GitHub repo Settings → Secrets and variables → Actions:

```
GROQ_API_KEY
SEC_USER_AGENT
```

The CI job is gated on `if: env.GROQ_API_KEY != ''` so it stays inert until secrets are set.

## Troubleshooting

**Backend cold start is slow.** Railway free tier sleeps the container after inactivity. First request takes 10-20s. Acceptable for a demo; add a paid plan if you want always-on.

**Database connection error from Railway.** Check the connection string uses `postgresql+asyncpg://` (note the `+asyncpg`). Supabase gives you a plain `postgresql://` URL by default; you have to add the driver suffix.

**Empty answers.** No data has been ingested. Ingestion is a separate local step (see Step 3).

**Qdrant 404 on collection.** Run `uv run python -m backend.indexing.build_indexes` against the prod env after ingestion. It creates the collection if missing.

**Vercel build fails.** Check that `NEXT_PUBLIC_BACKEND_URL` is set as an env var, not a secret. Next.js requires `NEXT_PUBLIC_*` vars at build time.

## Costs

Zero. All free tiers. The constraint is rate limits and cold starts, not money.
