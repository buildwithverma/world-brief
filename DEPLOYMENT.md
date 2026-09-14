# Free hosting setup

The implementation is prepared locally. No hosting account has been provisioned and nothing has been published by this change.

## What runs where

| Component | Target | Purpose |
| --- | --- | --- |
| React website | Cloudflare Pages Free | Static files; existing news UI plus owner sign-in |
| Python API | Render Free | News retrieval, BM25, MiniLM embeddings, summaries |
| PostgreSQL + pgvector | Supabase Free | Articles, vectors, settings, saved snapshots, caches, summary jobs |
| GitHub Actions | Private repository | Tests, manual deployment, optional bounded collection every two hours |
| Groq | Your existing account | Summaries, subject to your account's free limits |

The application components are open source; these hosting services are commercial services with free tiers. GitHub Actions runs finite jobs, not the permanent web server. Free quotas and availability can change. Render can sleep, so the first request may take longer. This version waits for the Python API; direct browser reads of Supabase news tables are deliberately not enabled. Supabase may pause inactive free projects. No paid resource is defined in the deployment files.

## 1. Create the database and owner account

1. Create a **free Supabase project**. Save its database password in your password manager.
2. In Authentication, create your own email/password user and copy its user UUID. Disable public sign-ups for this private app.
3. Copy the project URL and **publishable key**. Do not use a service-role key as the publishable key.
4. Copy a PostgreSQL connection string from Connect. Use the IPv4-compatible pooler connection for GitHub/Render if direct IPv6 is unavailable. URL-encode special characters in the password and require TLS with `sslmode=require`.
5. The database migration creates private `worldbrief` tables and the vector extension. Do not add `worldbrief` to Supabase's exposed API schemas. The browser only calls Supabase Auth; all news reads go through the owner-protected Python API.

No local SQLite data is uploaded automatically. The hosted edition fetches fresh articles. Existing local saved stories stay on your computer.

## 2. Create the backend

Create a Render Blueprint from this private repository and the branch containing these changes. Use `render.yaml`; confirm **Free** is selected. Set:

| Render environment setting | Value |
| --- | --- |
| `APP_ENV` | `production` |
| `DATABASE_URL` | Supabase PostgreSQL connection string, with TLS |
| `PUBLIC_ORIGIN` | Exact website origin, e.g. `https://world-brief.pages.dev` |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_PUBLISHABLE_KEY` | Publishable key |
| `OWNER_USER_ID` | Your Supabase user's UUID |
| `GROQ_API_KEY` | Your existing Groq key |
| `GROQ_DAILY_TOKEN_BUDGET` | `30000` initially |
| `MAX_STORED_ARTICLES` | Optional; default `5000` |

Run the migration before the backend can start. The manual deployment workflow does this, or run `python -m scripts.migrate` from an environment with `DATABASE_URL` securely set. Do not place the connection string in a committed file. The first Blueprint build may fail until this migration runs; rerun deployment afterward.

The backend uses one worker, offline MiniLM inference, and no persistent Render disk. Set or rotate Groq keys in Render's environment settings, then redeploy. The hosted website cannot overwrite them.

## 3. Create the website

Create a Cloudflare Pages **Direct Upload** project with production branch `main`. Here `main` names the Pages production channel; it does not merge or change the repository's Git branch. Note the project name, account ID, and resulting `pages.dev` origin. Set that exact origin as Render's `PUBLIC_ORIGIN`.

Create a scoped Cloudflare API token with Pages edit permission for this account. No custom domain is required.

For a manual build, copy `web/.env.production.example` to `web/.env.production` and fill in the public values, then run:

```powershell
pnpm --dir web install --frozen-lockfile
pnpm --dir web run build:static
```

Publish `web/dist-static`. The build refuses missing API/Auth configuration. `VITE_*` values are public. Never put Groq keys, database passwords, or Supabase service-role credentials in them.

## 4. Configure GitHub Actions

First push and review these changes. GitHub requires workflow files on the repository default branch for manual dispatch and schedules. Merge the reviewed implementation or deliberately change the default branch before using those controls; this change has not done that automatically. Push-triggered CI can run on the feature branch.

Create a GitHub environment named `production` and add these environment secrets:

- `DATABASE_URL`
- `GROQ_API_KEY`
- `RENDER_API_KEY` (used to request deployment and wait for it)
- `CLOUDFLARE_API_TOKEN`

Add these environment variables:

- `RENDER_SERVICE_ID`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_PROJECT_NAME`
- `VITE_API_URL`: Render's HTTPS URL
- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_PUBLISHABLE_KEY`
- `NEWS_COUNTRY`: initial country, e.g. `IN`; saved app preference takes precedence

The **Deploy free hosting** workflow tests the selected commit, migrates the DB, deploys that backend commit, waits for Render to report it live, then publishes the static website. It refuses a Render service that is not on the free plan. The backend and frontend are separate deployments, not an atomic release.

The optional **Refresh temporary articles** workflow is disabled unless you set the **repository-level** variable `ENABLE_SCHEDULED_COLLECTION=true`. Enable it after the first successful deployment. Scheduled workflows only run from GitHub's default branch. Currently the app is on a feature branch; activating the schedule requires separately merging or changing the default branch. This implementation does neither automatically.

Review Actions usage before enabling the schedule. Each collection has a 12-minute job timeout and a 9-minute application timeout; these are ceilings, not expected billing or a guarantee of staying inside your free monthly allowance. The model and pip caches reduce subsequent setup work. Disable the repository variable to stop scheduled collection.

## Storage and search

- Country and publisher filters constrain candidates first. Python BM25 selects a shortlist; pgvector exact cosine similarity ranks its MiniLM embeddings. Existing geographic checks and seven-day backfill remain.
- The scheduled worker prepares up to 500 article embeddings per run. Hosted searches spend about three seconds preparing missing vectors per date window, then show only checked matches and an indexing notice if work remains. Existing vectors are reused; the per-window budget cannot interrupt an embedding already running.
- Embeddings have 384 dimensions and a model ID plus content hash. Changing article text regenerates its vector. Models with another dimension require a schema change.
- Temporary articles expire nine days after publication, with an additional newest-5000 cap by default. Cleanup removes associated images, country links and vectors. This cap may shorten available history in busy countries.
- Expired query and story caches are deleted. Saved stories are independent snapshots and survive article cleanup. Settings and source catalogs also persist.
- Transaction advisory locks prevent overlapping collection and summary jobs. Groq requests use a shared lock and shared minute reservations. Daily token usage is stored centrally. Provider-side quotas still apply, including use from other apps.
- Article images remain remote URLs, not downloaded blobs. Coverage badges count reporting outlets; they do not certify an article as true.

## Local use and verification

Leave `APP_ENV` and `DATABASE_URL` unset to keep using `./start.ps1` with SQLite/Qdrant and the existing local `.env` key. Hosted mode never reads that file.

Tests use a disposable PostgreSQL database. **Never point `TEST_DATABASE_URL` at your live database: the test fixture clears application tables.** CI creates its own database automatically.

```powershell
.venv\Scripts\python.exe -m pytest tests -q
node --test tests/media-selection.test.mjs tests/news-search.test.mjs
node web/node_modules/typescript/bin/tsc --noEmit --project web/tsconfig.json
```

Without `TEST_DATABASE_URL`, PostgreSQL cases are skipped; SQLite and auth checks still run. Production readiness additionally requires testing real Supabase sign-in, TLS/database access, CORS, a country switch, a relevant search, saved-story persistence after a restart, and the collection workflow in the deployed accounts.

References: [Render free services](https://render.com/docs/free), [Supabase pricing](https://supabase.com/pricing), [Cloudflare Pages CI uploads](https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/), [GitHub Actions billing](https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions).

## Implementation checks

Local verification: 66 Python tests passed across SQLite, real PostgreSQL/pgvector, and hosted access controls; six frontend retrieval/selection tests passed. TypeScript, the original local frontend build, and the hosted static build passed. The Linux Docker image builds with the model bundled. Its production-mode HTTP smoke test passed under the resource cap: the health endpoint responded successfully and anonymous API access returned 401.

A Docker embedding-only stress check at 512 MiB and 0.1 CPU processed 150 long inputs in 220 seconds, with peak process memory of 226.8 MiB. This supports moving routine embedding to Actions; it is not a production load test or a guarantee of hosted response time. Actual account sign-in and provider deployment still need end-to-end verification after configuration.
