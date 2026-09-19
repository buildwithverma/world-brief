import asyncio
import contextlib
import json
import time
from contextlib import asynccontextmanager
from typing import Literal

from dotenv import set_key
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, SecretStr

from .catalog import import_catalog, with_metadata
from .config import DEFAULT_ARTICLE_COUNT, REFRESH_SECONDS, ROOT, TOKEN_BUDGET
from .engine import Engine
from .groq import credentials
from .media import COUNTRIES, MAJOR_DOMAINS, NAMES, country_name, domain_of
from .store import digest
from .postgres import create_store
from .config import HOSTED, HOSTED_SEMANTIC, PUBLIC_ORIGIN
from .vector import VectorCache

store = create_store()
if HOSTED or hasattr(store, "pool"):
    from .pgvector import PostgresVectors
    vector = PostgresVectors(store)
else:
    vector = VectorCache()
engine = Engine(store, vector)

ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]


if HOSTED:
    ORIGINS = [PUBLIC_ORIGIN]


@asynccontextmanager
async def lifespan(app):
    maintenance_task = asyncio.create_task(maintain_sources())
    summary_task = asyncio.create_task(engine.summaries.run())
    yield

    maintenance_task.cancel()
    summary_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await summary_task
    with contextlib.suppress(asyncio.CancelledError):
        await maintenance_task
    if vector.client:
        vector.client.close()
    if hasattr(store, "close"):
        store.close()


async def maintain_sources():
    # The scheduled GitHub worker can build vectors without competing with
    # public requests. Loading fastembed on Render's free instance repeatedly
    # exceeds its memory allowance and causes the process to restart.
    if not HOSTED or HOSTED_SEMANTIC:
        await asyncio.to_thread(vector.initialize)
    while True:
        if HOSTED:
            await asyncio.to_thread(store.purge)
            await asyncio.sleep(REFRESH_SECONDS)
            continue
        country = store.meta("country")
        if country in NAMES:
            try:
                await engine.collect(country)
            except Exception:
                pass

        active_cache_ids = {
            row["id"]
            for row in store.rows(
                "SELECT id FROM query_cache WHERE expires>? AND revision=?",
                (time.time(), store.meta("revision")),
            )
        }
        await asyncio.to_thread(vector.prune, active_cache_ids)
        await asyncio.sleep(REFRESH_SECONDS)


app = FastAPI(title="World Brief", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def local_only(request: Request, call_next):
    if HOSTED:
        from fastapi.responses import JSONResponse
        origin = request.headers.get("origin")
        if origin and origin not in ORIGINS:
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        if request.url.path == "/healthz":
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        return await call_next(request)
    origin = request.headers.get("origin")
    local_host = request.url.hostname in ("127.0.0.1", "localhost", "testserver")
    if (origin and origin not in ORIGINS) or not local_host:
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "Local app access only"}, status_code=403)
    return await call_next(request)


@app.get("/healthz")
def health():
    return {"status": "ok"}


def valid_country(code):
    code = code.upper()
    if code not in NAMES:
        raise HTTPException(422, "Choose a valid country.")
    return code


class Query(BaseModel):
    phase: Literal["legacy", "local", "expanded"] = "legacy"
    count: int = Field(default=DEFAULT_ARTICLE_COUNT, ge=1, le=100)
    query: str = Field(default="", max_length=1000)
    topic: Literal["All", "World", "Technology", "Business", "Science", "Climate"] = "All"
    country: str = Field(min_length=2, max_length=2)
    period: Literal["day", "today", "yesterday", "week"] = "day"
    timezone: str = Field(default="UTC", max_length=100)
    refresh: bool = False
    previous: dict | None = None


class Preferences(BaseModel):
    country: str = Field(min_length=2, max_length=2)


class MediaSelection(BaseModel):
    country: str
    ids: list[str] = Field(max_length=500)


class CustomMedia(BaseModel):
    country: str
    website: str = Field(max_length=250)
    name: str = Field(default="", max_length=100)


class GroqSettings(BaseModel):
    key: SecretStr


class BriefingUpdates(BaseModel):
    ids: list[str] = Field(max_length=100)


@app.get("/api/countries")
def countries():
    return COUNTRIES


@app.get("/api/location")
def location(timezone: str = ""):
    from .location import country_for_timezone
    code = country_for_timezone(timezone[:100])
    return {"country": code if code in NAMES else None, "method": "device_timezone", "approximate": True}


@app.get("/api/preferences")
def preferences():
    return {"country": store.meta("country") or None, "article_count": DEFAULT_ARTICLE_COUNT}


@app.post("/api/preferences")
def set_preferences(body: Preferences):
    code = valid_country(body.country)
    store.set_meta("country", code)
    return {"country": code, "country_name": country_name(code)}


@app.get("/api/status")
def status(country: str = ""):
    code = valid_country(country) if country else store.meta("country")
    key, model = credentials()
    return {
        "groq": engine.groq.status if key else "missing_key",
        "groq_configured": bool(key),
        "groq_model": model,
        "key_location": "Backend hosting environment: GROQ_API_KEY" if HOSTED else str(ROOT / ".env"),
        "hosted": HOSTED,
        "semantic_cache": vector.status,
        "country": code or None,
        "country_name": country_name(code) if code else None,
        "articles": store.rows("SELECT COUNT(*) AS n FROM country_articles WHERE country=?", (code,))[0]["n"],
        "cached_queries": store.rows("SELECT COUNT(*) AS n FROM query_cache")[0]["n"],
        "refreshing": code in engine.refreshing,
        "last_fetch": float(store.meta("last_fetch:" + code, "0")),
        "daily_token_budget": TOKEN_BUDGET,
    }


@app.post("/api/settings/groq")
async def save_groq(body: GroqSettings):
    if HOSTED:
        raise HTTPException(403, "Set GROQ_API_KEY in the backend hosting environment.")
    key = body.key.get_secret_value().strip()
    if len(key) < 15 or len(key) > 300 or any(character.isspace() for character in key):
        raise HTTPException(422, "Enter a complete Groq API key with no spaces.")

    set_key(str(ROOT / ".env"), "GROQ_API_KEY", key, quote_mode="always")
    engine.groq.blocked_until = 0
    engine.groq.minute = []
    return {"saved": True, "status": await engine.groq.test()}


@app.post("/api/settings/groq/test")
async def test_groq():
    return {"status": await engine.groq.test()}


@app.get("/api/media")
def media(country: str):
    code = valid_country(country)
    import_catalog(store, code)
    outlets = store.rows("SELECT * FROM media WHERE country=? ORDER BY rank, name", (code,))
    return {
        "country": code,
        "country_name": country_name(code),
        "outlets": with_metadata(store, code, outlets),
        "selected_count": sum(bool(outlet["selected"]) for outlet in outlets),
        "initialized": bool(store.meta("media_initialized:" + code)),
        "ranking": media_ranking_message(code),
    }


def media_ranking_message(code):
    if code == "IN":
        return (
            "India sources are ordered by your JSON priority scores. Scores are heuristic priorities, not truth "
            "ratings. Existing selections are preserved; Select top 50 applies the ranked list."
        )
    return "Top 50 by recent indexed coverage in this country edition. This is not an audience-size ranking."


@app.post("/api/media/select")
def select_media(body: MediaSelection):
    code = valid_country(body.country)
    known = {row["id"] for row in store.rows("SELECT id FROM media WHERE country=?", (code,))}
    if any(source_id not in known for source_id in body.ids):
        raise HTTPException(422, "One of these outlets is not in this country's source list.")

    unsupported = unsupported_catalog_sources(code)
    if unsupported.intersection(body.ids):
        raise HTTPException(422, "YouTube-only channels are listed for reference; video ingestion is not supported.")

    with store.db() as db:
        db.execute("UPDATE media SET selected=0 WHERE country=?", (code,))
        db.executemany(
            "UPDATE media SET selected=1 WHERE country=? AND id=?",
            [(code, source_id) for source_id in set(body.ids)],
        )
    return media(code)


def unsupported_catalog_sources(country):
    rows = store.rows("SELECT id, metadata FROM source_catalog WHERE country=?", (country,))
    return {row["id"] for row in rows if not json.loads(row["metadata"])["supported"]}


@app.post("/api/media/add")
def add_media(body: CustomMedia):
    code = valid_country(body.country)
    try:
        domain = domain_of(body.website)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    source_id = digest(domain)[:24]
    with store.db() as db:
        db.execute(
            """
            INSERT INTO media VALUES(?, ?, ?, ?, ?, 10000, 1, ?, 1)
            ON CONFLICT(country, id) DO UPDATE SET selected = 1, custom = 1
            """,
            (
                code,
                source_id,
                body.name.strip() or domain,
                domain,
                "https://" + domain,
                int(domain in MAJOR_DOMAINS),
            ),
        )
    return media(code)


@app.post("/api/query")
async def query(body: Query, request: Request):
    data = body.model_dump()
    data["country"] = valid_country(data["country"])
    task = asyncio.create_task(engine.query(data))

    try:
        return await wait_for_query(task, request)
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            if task.cancelled() or not task.done():
                await task


async def wait_for_query(task, request):
    deadline = time.monotonic() + 65
    while not task.done():
        if await request.is_disconnected():
            task.cancel()
            raise HTTPException(499, "Request cancelled")
        if time.monotonic() > deadline:
            task.cancel()
            raise HTTPException(504, "Collection took too long. Please retry; cached articles are kept.")
        await asyncio.wait({task}, timeout=0.25)
    return await task


@app.get("/api/saved")
def saved():
    stories = [json.loads(row["data"]) for row in store.rows("SELECT * FROM saved ORDER BY created DESC")]
    for story in stories:
        story["saved"] = True
        ensure_coverage_snapshot(story)
        story["verification"]["stale"] = story["verification"].get("expires_at", 0) < time.time()
    return stories


def ensure_coverage_snapshot(story):
    if story.get("verification", {}).get("status") == "outlet_coverage":
        return

    identities = {}
    for source in story.get("sources", []):
        try:
            domain = domain_of(source.get("domain") or source["url"])
        except ValueError:
            continue
        if domain != "news.google.com":
            identities[domain] = source

    major = [
        {"id": digest(domain)[:24], "name": source["publisher"], "domain": domain}
        for domain, source in identities.items()
        if domain in MAJOR_DOMAINS
    ]
    story["verification"] = {
        "status": "outlet_coverage",
        "major_count": len(major),
        "total_count": len(identities),
        "major_outlets": major,
        "checked_at": 0,
        "expires_at": 0,
        "explanation": "Coverage from this saved snapshot. Refresh the country briefing for current reporting.",
    }


@app.post("/api/saved/{ident}")
def save(ident: str):
    rows = store.rows("SELECT data FROM story_cache WHERE id=?", (ident,))
    if not rows:
        raise HTTPException(404, "Story is not available")
    with store.db() as db:
        db.execute("INSERT OR REPLACE INTO saved VALUES(?, ?, ?)", (ident, rows[0]["data"], time.time()))
    return {"saved": True}


@app.delete("/api/saved/{ident}")
def unsave(ident: str):
    with store.db() as db:
        db.execute("DELETE FROM saved WHERE id=?", (ident,))
    return {"saved": False}


@app.post("/api/briefing-updates")
def briefing_updates(body: BriefingUpdates):
    return engine.summaries.updates(body.ids)
