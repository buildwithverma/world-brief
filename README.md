> **Free hosted edition:** Deployment code is now available. See [DEPLOYMENT.md](DEPLOYMENT.md) for Supabase/pgvector, Render, Cloudflare Pages, GitHub Actions, and API-key setup. The local instructions below remain valid.

# World Brief

A local news assistant with an Apple-inspired interface. Choose your country, select outlets, ask for a briefing, and save stories. Groq interprets questions and summarizes indexed headlines. SQLite and embedded Qdrant cache results on your computer.

## Run

Dependencies and the embedding model are installed on this computer. In PowerShell:

```powershell
cd C:\Users\sachi\Documents\ChatGPT\NewAssistant
.\start.ps1
```

Open http://127.0.0.1:3000. Keep the terminal open; Ctrl+C stops the app. If PowerShell blocks the script, use `.venv\Scripts\python.exe scripts\run.py` instead. The launcher starts the web interface on port 3000 and Python API on port 8000. Use an existing running instance or stop it before launching another.

## Groq API key

Choose your country on first use. Open **Settings → Connect Groq**, paste your key, and select **Save & check key**. The local backend saves it in `.env`; no restart is needed. The saved key is not returned to the interface.

Alternatively, edit the existing `.env` file in this project:

```dotenv
GROQ_API_KEY=your_actual_groq_key
GROQ_MODEL=openai/gpt-oss-20b
```

Do not overwrite an existing `.env` with the example. Keep the key private; `.env` is excluded from Git. No Google Fact Check key or Qdrant key is required. Without a working Groq key, the app shows headlines and source links with basic keyword search instead of AI summaries.

## Using the app

- Choose your country at first use. Changing it loads a new briefing, prioritizing country coverage before world stories.
- Enter a question and press Enter or the send button. Topic and time filters also update the briefing.
- Manage media in Settings. Up to 50 outlets are selected initially, ranked by recent indexed coverage, not audience size. Select or deselect outlets and apply changes. Add a website if missing; indexed coverage is not guaranteed.
- Each story shows matching major-outlet and total-outlet counts from your selected media. Open its evidence panel for source links. This is a coverage indicator, not a true/false verdict; outlets may share syndicated reporting.
- Save stories in Saved.

## How it works

The React/Vinext interface calls a local FastAPI server. Python collects Google News RSS headlines and publisher links for your country, groups similar headlines, prioritizes country coverage, and asks Groq to summarize the supplied headlines. It does not read paywalled article bodies. English country editions are used where supported; other countries use country-name searches.

SQLite stores articles, outlet selections, queries, summaries, coverage counts and bookmarks. Qdrant stores locally generated query embeddings for similar-query reuse. Cache matching includes country, selected outlets, filters, time and source revisions. Briefings expire after 20 minutes by default; collection runs periodically while the app runs. News availability and free Groq limits depend on their providers.

Code is in `web/` and `backend/`. Local data is in `data/`, configuration in `.env`, and logs in `data/logs/`. The app binds to localhost. Query text and indexed headlines are sent to Groq when enabled; embeddings stay local.

## Fresh installation elsewhere

Use Python 3.12 and Node.js 22.13 or newer:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements-lock.txt
cd web
pnpm install --frozen-lockfile
cd ..
.venv\Scripts\python.exe scripts\setup_vectors.py
.\start.ps1
```

The embedding download is about 83 MB. No GPU or Qdrant account is required.

## Validation

```powershell
.venv\Scripts\python.exe -m pytest tests -q
node web\node_modules\typescript\bin\tsc --noEmit --project web\tsconfig.json
cd web
node node_modules\vinext\dist\cli.js build
```

## Architecture and current options

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full Python data flow, modules, API, caching, source catalogue and limitations. India source metadata is bundled in backend/catalogs/india_news_sources_ranked.json. Existing media selections are preserved; use Select top 50 to apply the ranked India list. Publisher RSS adds excerpts and original article links where available.

The article count defaults to 50. Change the Articles control in the briefing (1–100), or set NEWS_ARTICLE_COUNT=50 in .env and restart. The browser remembers an explicit count choice. A query requesting a specific number of stories can override that count for its answer.


## Instant search

Typing filters loaded cards immediately. After a 300 ms pause, local BM25 searches stored articles while a parallel request collects additional coverage and reranks a shortlist using local MiniLM embeddings. Results remain visible throughout; clearing the search restores newest-first news. Groq summaries update separately. Country changes reset to All topics / Last 24 hours and collect the new country's feed.

A first-time country is suggested from your device timezone (approximate, editable). Your saved selection is retained. If embeddings are unavailable, run `.venv\Scripts\python.exe scripts\setup_vectors.py` and restart the app. BM25 continues working without embeddings or a Groq key.
