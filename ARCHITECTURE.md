## Hosted implementation (September 2026)

The free hosting implementation is documented in [DEPLOYMENT.md](DEPLOYMENT.md). Local mode described below remains available.

`backend.postgres.create_store()` selects PostgreSQL when `DATABASE_URL` is configured, otherwise SQLite. `backend/postgres.py` translates the small set of SQLite-specific statements while keeping values bound as parameters. `migrations/001_postgres.sql` creates private application tables and pgvector columns. Hosted durable state is entirely in PostgreSQL, including saved snapshots and pending summary jobs.

`backend/pgvector.py` loads the free local MiniLM model, caches 384-dimensional article embeddings in PostgreSQL by content hash/model, and uses exact cosine distance on the BM25 shortlist. Query embeddings are also supported by the legacy query-cache path; the interactive progressive-search path primarily reuses stored articles and article vectors. Nine-day expiry and a configurable article cap bound temporary storage; saved snapshots survive cleanup.

`APP_ENV=production` enables owner-only Supabase access-token verification, exact frontend-origin checks and server-managed Groq credentials. No news database table is exposed directly to the browser. `web/static/main.tsx` adds sign-in around the existing UI; `web/vite.static.config.ts` produces static files for Pages. The local Vinext frontend remains available.

GitHub CI tests both storage engines. The manual deployment workflow migrates, deploys the tested Python commit to a free Render service, waits for it, and uploads the website. Optional scheduled Actions jobs run `scripts/collect.py`, with bounded runtime and shared PostgreSQL locks. The repository default-branch schedule and cloud account setup require activation; no external deployment was performed by this implementation.

---

# World Brief architecture

This document describes the implemented local application. The backend is entirely Python. The existing React/Vinext frontend and Apple-inspired visual design remain in place.

## Components and responsibilities

| Component | File or directory | Responsibility |
| --- | --- | --- |
| Local launcher | `start.ps1`, `scripts/run.py` | Starts and stops the Python API and web dev server together; writes service logs. |
| Web application | `web/app/world-brief.tsx` | Country onboarding, question submission, article-count control, story cards, saved stories, settings and evidence drawer. |
| Media controls | `web/app/media-settings.tsx` | Displays the country source library and India catalogue attributes; saves selections and adds websites. |
| Country picker | `web/app/country-picker.tsx` | Searchable country selector. |
| API client | `web/lib/brief-api.ts` | Typed requests, readable errors, cancellation and a 70-second timeout. |
| Python HTTP API | `backend/main.py` | Validates inputs, serves preferences and media, handles local Groq settings and bookmarks, limits queries to 65 seconds. |
| Briefing engine | `backend/engine.py` | Resolves intent, checks caches, filters and groups articles, ranks stories, requests summaries, counts outlets and saves results. |
| India catalogue | `backend/catalog.py`, `backend/catalogs/india_news_sources_ranked.json` | Imports the supplied source attributes and ordering into SQLite. |
| Country news collection | `backend/media.py` | Collects Google News RSS for the country and query, discovers publisher identities and records country associations. |
| Publisher collection | `backend/publisher_feeds.py` | Collects configured publisher RSS feeds for selected outlets, extracting publisher excerpts and original article URLs. |
| Groq adapter | `backend/groq.py` | Reads the key, tests connectivity, validates JSON responses and applies request/token budgets. |
| Persistence | `backend/store.py` | SQLite schema, transactional writes, source revisions, exact-query caching and bookmarks. |
| Semantic cache | `backend/vector.py` | Generates local MiniLM embeddings and searches embedded Qdrant. |
| Configuration | `backend/config.py`, `.env` | Environment settings and defaults. |
| Shared RSS helpers | `backend/feeds.py` | URL cleanup and HTML-to-text helpers. Its old region feed collector is retained legacy code, not the active country collection path. |

## Startup and first use

1. `start.ps1` calls `scripts/run.py` with the local Python virtual environment.
2. The supervisor checks ports, starts FastAPI with Uvicorn on `127.0.0.1:8000`, and starts the web server on `127.0.0.1:3000`.
3. Vite proxies browser `/api/` requests to Python. All application data processing lives in Python; Node runs the frontend tooling and rendering.
4. Python opens SQLite and initializes the local vector model in the background. Exact caching still works if vectors are unavailable.
5. The browser loads countries and preferences. A new user must choose a country. The choice is stored in SQLite.
6. Choosing India imports the bundled JSON. A new India setup selects the highest-ranked 50 supported website identities. Existing selections are preserved. The user can explicitly apply the ranked top 50 in Settings.
7. Country changes cancel the old browser request and start a fresh briefing. Request IDs prevent a slower old response from replacing the current country.

## A question's journey

1. The UI sends the question, country, topic, time range, timezone and requested article count to `POST /api/query`.
2. Python validates the country and count. The count defaults to 50 and permits 1–100. A question such as “give me 5 stories” can override the chosen count for that request.
3. An exact cache lookup considers normalized question, country, filters, count, previous intent, selected sources, time bucket, model/key configuration and source revision.
4. A cache miss triggers collection if needed. Refresh forces collection except for a short 10-second guard against repeated clicks. Specific subject queries also search the country news index.
5. A local parser understands basic topics, dates, counts and keywords. With a key, Groq can refine the intent. It cannot change the explicitly selected country.
6. When enabled, semantic search looks for a similar question. Reuse still requires matching scope, source revision and expiry; similarity alone is insufficient.
7. SQL selects only articles associated with the country and enabled media outlets, inside the time window. Topic and keyword filters narrow the candidates.
8. Headlines are grouped using shared words, similarity, compatible numbers and a 36-hour event window. This is a heuristic, so separate events may occasionally group together or equivalent stories remain separate.
9. Ranking favors country coverage, freshness, matching outlet count and source/topic diversity. India catalogue priority adds a small ranking bonus of up to four points; it does not override the country preference or establish truth.
10. Up to the requested count of story groups are returned. Fewer stories are legitimate when the selected sources/time range contain fewer matches; the UI displays the requested and returned totals.
11. Each story uses available publisher excerpts and original links. Stories with only indexed headlines remain explicitly labeled. Groq receives headlines and excerpts in batches of five and is asked for a brief of at most 60 words grounded in that material.
12. Summaries must cite supplied source IDs. Invalid output, unavailable Groq, exhausted budget or the bounded 20-second summary window leaves the publisher excerpt/headline fallback visible. A 50-story briefing need not contain 50 AI summaries on the free tier.
13. Results, source links, coverage counts and summaries are cached. The browser shows the answer and refreshes status.

## India JSON integration

The original supplied JSON is copied into `backend/catalogs/`; the app does not depend on a file remaining in Downloads. It contains 128 entries across four categories. Multiple entries for the same website are merged into one selectable publisher identity, while every original category entry and attribute is preserved in `source_catalog.metadata`.

The UI exposes score, tier, category, language, region, focus, format, type and catalogue date when supplied. Search includes these attributes. Domain aliases are normalized, while distinct brands such as Times of India and Economic Times remain distinct. YouTube handles retain separate identities instead of collapsing into one YouTube outlet. YouTube-only entries are external reference links and cannot be selected for article ingestion; video ingestion is not implemented.

The JSON's ranking-method prose and recommended policies are data, not executable instructions. This import does not enable an automatic fact-check pass. A priority score is the file author's heuristic, not an independently verified reliability score. The major-outlet count continues to use the separate curated identity list.

Edit the bundled JSON to update metadata and ranking, then reload the media library or request a briefing. Its content hash triggers re-import and cache invalidation. Selections survive re-import. Removing entries from the file is not currently a deletion operation; existing source records are retained and can be deselected.

## More detail and original articles

The country index supplies headlines and publisher identities, not full article bodies. Supported publisher RSS feeds add actual excerpts. The configured feed list is in `backend/publisher_feeds.py`; only selected publishers contribute. Feed collection is bounded by time, concurrency and response size. Failure of one feed does not fail the briefing.

Story cards show the available summary/excerpt below the headline and a visible article link. The evidence drawer includes per-source excerpts and links. Direct publisher RSS links are labeled “Read original article.” If only an index URL is available, the UI labels it “Open article via news index”; the app does not invent an original URL. It does not scrape full articles, bypass paywalls or guarantee that every publisher exposes an excerpt. Add a known publisher feed to the Python feed list to extend excerpt coverage.

## Outlet coverage indicator

For each story group, publishers are deduplicated by source identity. The app displays total selected outlets and the subset on its curated major-outlet list, along with names, URLs and a timestamp. The count is limited to collected reporting from selected outlets. Syndicated stories can appear under several publishers. It is not independent verification, and does not label claims true or false. No Google Fact Check API is used.

## Local storage and cache invalidation

| SQLite table | Contents |
| --- | --- |
| `articles` | URLs, titles, excerpts, publisher, timestamps and content hashes. |
| `article_images` | Publisher-provided image URLs associated with articles. |
| `media` | Per-country publisher identity, ordering and selection. |
| `source_catalog` | Imported India metadata, including duplicate category entries. |
| `country_articles` | Article-to-country/source links, topic and country-priority signal. |
| `story_cache` | Story summaries, source evidence and outlet counts, versioned and expiring. |
| `query_cache` | Query, scope, complete response, source revision and expiry. |
| `saved` | Explicitly bookmarked story snapshots. |
| `meta` | Preferences, collection timestamps, catalogue hash and source revision. |
| `usage` | Daily Groq token usage. |
| `feeds` | Legacy feed inventory; not the active country collector. |

SQLite is `data/brief.sqlite3`; Qdrant is `data/qdrant`; the embedding model is under `data/models`. Embeddings are generated locally. Qdrant requires no account or API key. Expired query records are removed on later cache writes after 30 days; vectors are pruned in maintenance. Articles and bookmarks do not currently have automatic retention deletion in the active country path.

The default briefing expiry is 20 minutes. Source revisions change for article corrections and changed country associations. Country, selected sources, count and processing configuration separate cache entries. Saved stories are snapshots and can have older evidence than a refreshed briefing. A background task collects the stored country every 30 minutes while the server runs; it does not push a new screen into an open browser automatically.

## Settings and keys

```dotenv
GROQ_API_KEY=your_key
GROQ_MODEL=openai/gpt-oss-20b
NEWS_ARTICLE_COUNT=50
ENABLE_SEMANTIC_CACHE=true
NEWS_REFRESH_MINUTES=30
CACHE_MINUTES=20
GROQ_DAILY_TOKEN_BUDGET=120000
```

Enter the Groq key in Settings → Connect Groq → Save & check key, or edit `.env`. Credentials are re-read without restarting. Other environment defaults require restarting the backend. The UI's article-count choice persists in that browser's local storage and overrides the environment default. Edit the Articles field and press Enter or leave the field to load the new count.

The key stays in `.env`, which Git ignores. It is sent only to Groq by the backend; API responses do not return it. Query text and headline/excerpt data go to Groq when enabled. Google receives news search terms, and configured publishers receive RSS requests. There are no public-hosting credentials or remote vector services.

## API reference

| Endpoint | Purpose |
| --- | --- |
| `GET /api/countries` | Supported country names/codes. |
| `GET, POST /api/preferences` | Read the country/default count; save country. |
| `GET /api/status` | Connection/cache/collection status; no secret values. |
| `POST /api/settings/groq` | Save and test the local Groq key. |
| `POST /api/settings/groq/test` | Test the saved key. |
| `GET /api/media?country=IN` | Ordered source library with catalogue metadata. |
| `POST /api/media/select` | Save a country's selected source IDs. |
| `POST /api/media/add` | Add a public website for index search. |
| `POST /api/query` | Generate or retrieve a briefing. |
| `GET /api/saved` | Read saved story snapshots. |
| `POST, DELETE /api/saved/{id}` | Bookmark or remove a story. |

## Running, testing and extending

Run `./start.ps1` in PowerShell from the project root and open http://127.0.0.1:3000. Keep the terminal open. Ctrl+C stops both services. Restart after Python code changes. Logs are in `data/logs/`.

Backend checks: `.venv/Scripts/python.exe -m pytest tests -q`. Frontend checks: `node web/node_modules/typescript/bin/tsc --noEmit --project web/tsconfig.json`, then run `node node_modules/vinext/dist/cli.js build` from `web/`.

To change ranking or grouping, edit `backend/engine.py`. To extend publisher excerpts, add configured feeds in `backend/publisher_feeds.py` and test their date, excerpt and URL parsing. To support another ranked country catalogue, extend `backend/catalog.py`. The frontend consumes Python API responses and does not contain news ingestion, AI or cache logic.

## Short-news feed update

The Inshorts-inspired reading surface keeps the original React frontend and Python backend. Cards now show a headline, publisher/time, a brief of at most 60 words, source-coverage badge, bookmark and full-reporting link. Search and filters sit above the single-column feed. Mobile cards stack publisher images above text; desktop cards place them beside it. Missing or failed images collapse to text-only cards.

`backend/shorts.py` enforces the summary word limit without an additional AI request, preferring complete sentences when shortening. Groq is prompted to use fewer words when evidence is thin and to avoid inventing context. Publisher excerpts are also shortened for cards; source excerpts remain accessible in the evidence drawer. Legacy saved stories receive a 56-word frontend display limit while retaining their saved detail.

Publisher RSS image metadata and embedded excerpt images are stored in `article_images` and included in story cache versions. Only HTTPS image URLs with public-looking domain names are accepted. Images load directly from publisher/CDN hosts with no referrer; this does not download or rehost them. No synthetic or unrelated stock images are used. Feed availability determines image coverage.

The GitHub `main` branch and `pre-inshorts-redesign` tag preserve the earlier interface. The short-news work is isolated on `codex/short-news-feed`.

## Automatic completion of summaries

`backend/summary_queue.py` persists unfinished summary jobs in SQLite's `summary_jobs` table. The API can return a briefing promptly while a separate Python task completes remaining summaries in batches of three. Rate-limit responses defer the job instead of caching an unfinished card permanently. Pending jobs survive service restarts. Invalid output is retried up to three times; daily-budget and credential failures pause processing until they can be resolved.

`POST /api/briefing-updates` returns current versions of the requested cards and pending/failed counts. The frontend polls every eight seconds while summaries are pending and updates cards and the open evidence panel without fetching the news again. Existing exact/semantic briefing caches also overlay repaired story summaries when read. Version checks prevent an older job from overwriting newly collected evidence. Bookmarked snapshots remain unchanged.

The first response still has a bounded summary window, but that window no longer abandons the remainder. With free-tier limits, completing 50 summaries can take several minutes. Source headlines alone do not establish the details of a full article; generated briefs remain limited to supplied reporting.


## Consistent images and 40–56-word summaries

Cards retain a fixed media panel when imagery is absent or fails. Publisher photos use contain sizing to avoid cropping. Each story can carry multiple publisher image URLs; the frontend tries the next URL on error or when an image is too small to be useful. With no usable image, a neutral newspaper icon, publisher name and topic appear with an explicit image-unavailable label. This fallback is not a photograph of the event.

Summary generation targets 40–56 words. Both initial generation and background retries validate length. When supplied excerpts contain enough material, an under-40-word result is rejected for retry. When the source contains only thin headline information, a shorter factual brief is allowed and labeled Limited source detail. The app does not invent details to reach a minimum. The cache configuration version changed so newly refreshed briefings use the new policy. Legacy saved snapshots remain unchanged.


### Refresh and draft preservation

Keyword discovery includes the requested topic in both the search and its cache key, so topic-filtered results retain their category. Explicit Refresh retries failed summary jobs without resetting rate-limit cooldowns. Expired story snapshots are excluded from processing and pending counts; freshly cached stories can be queued again. Job results are checked against the current evidence version and expiry before being saved.

Media settings retain local selection edits across background source-library refreshes, including newly discovered outlets. Changing country resets that draft; applying the selection saves it. Source controls are disabled during save/add requests.

Regression checks: run `.venv\Scripts\python.exe -m pytest tests -q` for Python and `node --experimental-strip-types --test tests/media-selection.test.mjs` for selection merging.


## Progressive search (September 2026)

The web UI filters its loaded cards on every keystroke using BM25, then debounces API calls by 300 ms. It sends concurrent `phase=local` and `phase=expanded` requests to `/api/query`. Request generations and cancellation prevent stale responses from replacing a newer search; the expanded response cannot be overwritten by the local one.

The local phase reads up to 1,400 recent eligible SQLite articles, applies country/outlet/time/topic filters, and returns a BM25 shortlist without network or inline Groq calls. BM25 uses whole Unicode words, k1=1.5 and b=0.75. The expanded phase fetches subject RSS (existing 20-minute subject cache applies), repeats retrieval, and reranks up to max(150, count*3) candidates using local MiniLM cosine similarity. Scores below 0.32 are excluded. This is a tunable heuristic, not a calibrated probability. Article vectors are cached by text hash in memory, capped at 4,000. Missing models fall back to BM25. Install the model with `scripts/setup_vectors.py` and restart the backend. Query Qdrant storage remains separate.

Empty queries select newest story groups rather than the previous diversified ranking. Country changes reset topic/time filters and fetch the chosen country. Groq summaries run in the persistent background queue; no remote intent or summary request blocks progressive results. The legacy API path remains available without a phase field. Progressive results reuse story caches; they do not use the legacy final-answer vector cache.

On first use without a saved country, `/api/location?timezone=...` maps the device timezone to a country using packaged tzdata. This is an approximate suggestion, not GPS or IP geolocation. Saved/manual country selections take precedence; unknown timezones show the country picker. No coordinates are requested or sent externally.


### Location relevance and weekly backfill

Progressive search removes generic query words and recognizes named country subdivisions using pycountry. A named subdivision must occur as a full phrase in the headline/excerpt, or match a configured city alias. Uttar Pradesh includes a bounded list of cities in `backend/retrieval.py`; this is not an exhaustive geographic database. This gate applies before the free local MiniLM embedding filter. The immediate browser matcher applies the same rule for Pradesh state names, using source evidence rather than generated summary text. Embedding similarity is heuristic and may omit relevant reports; location mentions alone do not establish complete topical relevance.

For Last 24 hours / Today searches, retrieval checks the requested recent window first, then successive older windows until enough distinct relevant story groups are found or seven days are exhausted. Every window retains country, source selection and topic constraints. Explicit Yesterday and Past week windows are respected. Each tier is sorted newest first. The API reports `backfilled` and the UI explains inclusion of older news. Fewer than the requested count is expected when relevant selected-source coverage is insufficient; irrelevant stories are never deliberately added to meet the count.
