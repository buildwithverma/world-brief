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
11. Each story uses available publisher excerpts and original links. Stories with only indexed headlines remain explicitly labeled. Groq receives headlines and excerpts in batches of five and is asked for two to four informative sentences grounded in that material.
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
