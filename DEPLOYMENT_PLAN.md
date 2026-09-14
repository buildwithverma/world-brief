> **Implementation status:** The PostgreSQL adapter, pgvector retrieval, hosted owner authentication, static build, Docker image, and Actions workflows have been implemented. See [DEPLOYMENT.md](DEPLOYMENT.md) for the actual setup and current limitations. Direct browser reads during backend cold starts are deferred; the current frontend waits for the API. Cloud accounts have not been provisioned. The plan below records the original design.

# World Brief: free-tier deployment with pgvector

Updated 2026-09-14. Supersedes the paid single-container plan.
Baseline: `74f3a754bcdb147e9bbd114654598e67efb079bb`.
Status: revised plan only; no hosting resources or database migrations have been applied.

## Goal and platform choices

Target $0 hosting within provider quotas, retain Python, preserve the React interface, and store temporary news articles plus their embeddings in PostgreSQL with pgvector. PostgreSQL, pgvector and the app libraries are open-source components. GitHub Actions, Cloudflare, Render and hosted Supabase are hosted services with free tiers; their infrastructure is not made open source by using them. Self-hosting PostgreSQL/pgvector is another option if an existing machine is available, but hardware and network access still have costs.

| Responsibility | Proposed service | Constraint |
|---|---|---|
| Source, tests and release automation | Existing private GitHub repository + GitHub Actions | Private-repository minutes are quota-limited |
| Static React frontend | Cloudflare Pages Free | Adapt current Vinext SSR packaging to a static client build; preserve components and design |
| Interactive Python API | Render Free, Python-only container | Sleeps when idle, ephemeral disk, limited CPU/RAM; no always-on guarantee |
| Durable database and temporary article vectors | Supabase Free PostgreSQL + pgvector | 500 MB database allowance, shared resources, inactivity pausing |
| Article/query embeddings | Existing local MiniLM ONNX model | No embedding API charge; resource-fit validation required |
| Summarization | Existing Groq free API allowance | Provider rate/token limits remain |

GitHub Actions is not an always-on web server. It can execute finite test, deployment, collection, embedding and cleanup jobs. Do not run a permanent server or a keep-alive workflow to simulate paid hosting.

## Interactive request flow

1. Static frontend loads without waiting for Python to wake. Authenticate the owner with Supabase Auth; protect API requests with validated access tokens and an owner allowlist. Public static files contain no private news state or service credentials.
2. Fetch authorized stored articles through narrowly scoped database access (RLS-protected REST/RPC) so stored results remain usable during a Python cold start. Never expose a database password or service-role key in JavaScript.
3. Typing performs immediate browser BM25 matching. Python concurrently fetches new subject coverage when awake, embeds new/changed articles, and queries pgvector to refine the BM25 shortlist.
4. Keep location constraints such as Uttar Pradesh separate from embedding similarity. Geography must not be inferred solely from a weak cosine score.
5. Rank recent relevant articles first, extending through older daily windows to seven days only when needed. Return fewer than 50 if evidence is insufficient.
6. Persist accepted summaries and coverage metadata in PostgreSQL. Background work is resumable, not dependent on a free web service staying awake.

A cold start can delay fresh fetches and semantic refinement even though stored results appear immediately. If the free Python instance cannot fit the model under measured peak load, stop and revise the architecture; do not silently enable a paid plan. Splitting out Node SSR materially reduces the combined workload but does not prove that MiniLM fits.

## Temporary article storage

Use ordinary PostgreSQL tables with explicit retention, not SQL TEMP tables: articles must be shared across requests and scheduled jobs.

Proposed `articles` fields:
- `id`, unique normalized `canonical_url`, publisher identity, title and excerpt.
- `published_at`, `fetched_at`, `expires_at` as timezone-aware timestamps.
- `content_hash`, `embedding vector(384)`, `embedding_model`, `embedding_version`.
- Image URL only, not image binaries or full scraped article bodies.

Keep article-country/topic associations in a separate table: one article can serve multiple country editions. Keep selected outlets, saved story snapshots and owner settings outside the temporary article lifecycle. Store summary/coverage snapshots separately with source references and expiry. Coverage is an outlet count, not a verified-truth verdict.

Retention:
- Search only publication dates from the last seven days (or an explicitly narrower requested window).
- Keep temporary articles for nine days from publication to allow operational cleanup margin; fetching an old article again must not extend its lifetime indefinitely.
- Cleanup expired summaries and jobs before deleting dependent article records. Preserve saved story snapshots independently, including their source links and displayed summary.
- Run bounded, idempotent cleanup daily; batch deletes and monitor PostgreSQL table/index bloat. DELETE does not guarantee an immediate reduction in allocated disk space.
- Embed only new or changed content. Store model/version metadata so incompatible embeddings are rebuilt rather than mixed.

Use the standard pgvector extension, not Supabase's separate vector-storage alpha. Start with exact cosine comparison over the small filtered BM25 shortlist; add an approximate index only after measurements justify its storage and filtering tradeoffs. PostgreSQL full-text rank is not BM25: keep the tested Python/JavaScript BM25 implementation rather than pretending `ts_rank` is equivalent.

Proposed pgvector selection order: authorized country/outlets + time + location constraints -> BM25 shortlist -> cosine similarity -> recent-first grouping/backfill. PostgreSQL stores the vectors; it does not generate embeddings automatically.

## Remove dependence on server-local files

Move hosted articles, preferences, selections, saved stories, summaries, usage counters and job state from SQLite into PostgreSQL. Migrate the hosted query vector cache into pgvector or disable it initially; do not leave Qdrant as a durable disk requirement on Render Free. Keep the existing SQLite/Qdrant path for local development if useful through a storage adapter.

This is a real storage migration, not a connection-string-only change. Rewrite SQLite placeholders, JSON expressions and conflict handling; add migrations, transactions and uniqueness constraints. Use a small SSL database connection pool compatible with the provider's connection mode. Do not use long-lived session assumptions with transaction pooling.

Build the MiniLM files into the container image. Treat in-memory article vectors and BM25 indexes as disposable caches. Health checks must distinguish database reachability, model readiness and Groq availability without leaking secrets.

## GitHub Actions responsibilities and budget

- On pull requests: run Python and frontend tests, validate migrations against disposable PostgreSQL with pgvector, and build the static frontend and API container.
- On an approved release: deploy the tested commit; use least-privilege repository/environment secrets and avoid running secret-bearing steps for untrusted pull-request code.
- Scheduled collector: start conservatively at every two hours for the owner's selected country. Fetch bounded RSS feeds, upsert only new/changed data, generate missing embeddings, and process a bounded summary batch. Do not launch one job per search keystroke or per country worldwide.
- Cleanup: run once daily or as a bounded step in collection. Use PostgreSQL advisory locks or leased jobs so overlapping scheduled and interactive work does not double-charge Groq or duplicate processing.
- Cache dependencies/model files within GitHub's storage allowance; avoid retaining article data as build artifacts. Set timeouts, concurrency controls, short artifact retention, and usage alerts.

GitHub Free currently includes 2,000 private-repository runner minutes/month. At 12 runs/day and an assumed 3 minutes/run, 30 days cost approximately 1,080 minutes, leaving room for CI. This is a planning estimate: measure actual job duration and include other repositories' usage. Scheduled jobs can be delayed and require the workflow on the default branch. Preserve the baseline on main until an explicit release/default-branch decision is made; do not change repository visibility for free minutes.

## Authentication and secrets

Still assumes one owner; separate user data is required before inviting independent users. Use an allowlisted owner identity, validate tokens server-side, and enforce RLS for any browser database access. Restrict backend mutation endpoints and CORS to the deployed frontend origin.

Store DATABASE_URL and GROQ_API_KEY only in the Python host and scoped Actions secrets. Public Supabase URL/publishable key may be in the frontend only with tested RLS. Hosted Groq settings must not allow unauthenticated replacement of the shared key. Add request limits and per-owner token limits. Do not upload the local .env or notebooks automatically.

## Storage and free-tier guardrails

A 384-dimensional float vector alone is approximately 1.5 KB; 20,000 vectors are about 30 MB before article text, indexes and database overhead. Do not treat that as total database size. Keep excerpts bounded and measure actual database size regularly. Set a warning below the 500 MB quota (for example 350 MB), and pause new ingestion before exhausting it. Saved data must not be silently deleted to stay free.

Free Supabase may pause for inactivity and does not include automatic backups. Export owner settings/saved snapshots through an explicit backup procedure; temporary article caches can be rebuilt. Paid upgrades require a new decision, not automatic fallback.

## Implementation order and acceptance gates

1. Add PostgreSQL/pgvector migrations, storage adapter and regression tests; preserve the current local app.
2. Add owner authentication/RLS, hosted secrets handling and quota controls.
3. Convert frontend packaging to static React; preserve appearance, search and saved-story behavior. Add authenticated direct stored-result loading for cold starts.
4. Package Python alone and measure peak RAM/CPU under embedding, fetching and summary workloads using free-tier-like limits.
5. Add CI, bounded collection and cleanup entry points. Validate duplicate-job safety, credential boundaries and estimated monthly minutes.
6. Provision only free plans after connecting the chosen accounts; apply migrations to a fresh hosted database. Local database import is a separate explicit step.
7. Test the deployed app: unauthorized access, cold start, persisted saved stories after restart, source selection, Uttar Pradesh precision, seven-day backfill, missing model, provider failures, retention and backups.
8. Record URLs, deployed commit and measured resource usage. Release only after those gates pass. Roll back code with schema compatibility; retain saved data.

No deployment or pgvector migration has been performed by this planning update.

## Sources checked 2026-09-14

- GitHub Actions billing: https://docs.github.com/en/actions/concepts/billing-and-usage
- GitHub quotas: https://docs.github.com/en/actions/reference/limits
- Scheduled workflow behavior: https://docs.github.com/en/enterprise-cloud@latest/actions/reference/workflows-and-actions/events-that-trigger-workflows
- Supabase free database quota and pauses: https://supabase.com/pricing
- PostgreSQL vector columns: https://supabase.com/docs/guides/ai/vector-columns
- Render free-service limitations: https://render.com/docs/free
- Render memory/CPU plans: https://render.com/docs/compute-plans
- Cloudflare Pages limits: https://developers.cloudflare.com/pages/platform/limits/
