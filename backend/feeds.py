import asyncio
import calendar
import html
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

from .store import digest


def plain(text):
    without_tags = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def canonical(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        return ""

    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query)
            if not key.lower().startswith("utm_") and key not in ("fbclid", "gclid")
        ]
    )
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/") or "/", query, ""))


async def refresh(store):
    semaphore = asyncio.Semaphore(4)
    feeds = store.rows("SELECT * FROM feeds")

    async with httpx.AsyncClient(
        timeout=18,
        follow_redirects=True,
        trust_env=False,
        headers={"User-Agent": "WorldBrief/1.0 (personal RSS reader)"},
    ) as client:

        async def fetch_one(feed):
            async with semaphore:
                return await _refresh_feed(store, client, feed)

        outcomes = await asyncio.gather(*(fetch_one(feed) for feed in feeds))

    store.set_meta("last_fetch_attempt", time.time())
    if any(outcomes):
        store.set_meta("last_fetch", time.time())

    with store.db() as db:
        db.execute("DELETE FROM articles WHERE published<?", (time.time() - 30 * 86400,))
    return {"available": sum(outcomes), "total": len(outcomes)}


async def _refresh_feed(store, client, feed):
    try:
        response = await _get_feed_response(client, feed)
        if response.status_code != 304:
            response.raise_for_status()
            articles = _parse_feed_articles(feed, response.content)
            if not articles:
                raise ValueError("Empty or invalid feed")
            store.upsert_articles(articles)

        _mark_feed(store, feed, "ok", response)
        return True
    except Exception:
        _mark_feed(store, feed, "unavailable")
        return False


async def _get_feed_response(client, feed):
    headers = {}
    if feed["etag"]:
        headers["If-None-Match"] = feed["etag"]
    if feed["modified"]:
        headers["If-Modified-Since"] = feed["modified"]
    return await client.get(feed["url"], headers=headers)


def _parse_feed_articles(feed, content):
    articles = []
    for item in feedparser.parse(content).entries[:25]:
        article = _parse_feed_item(feed, item)
        if article:
            articles.append(article)
    return articles


def _parse_feed_item(feed, item):
    url = canonical(item.get("link", ""))
    title = plain(item.get("title", ""))
    if not url or not title:
        return None

    excerpt = plain(item.get("summary", ""))[:1800]
    date = item.get("published_parsed") or item.get("updated_parsed")
    published = calendar.timegm(date) if date else time.time()
    if published > time.time() + 3600:
        return None

    return {
        "id": digest(url),
        "url": url,
        "publisher": feed["publisher"],
        "title": title,
        "excerpt": excerpt,
        "topic": feed["topic"],
        "region": feed["region"],
        "published": published,
        "fetched": time.time(),
        "content_hash": digest(title + " " + excerpt),
    }


def _mark_feed(store, feed, status, response=None):
    etag = response.headers.get("etag", feed["etag"]) if response else feed["etag"]
    modified = response.headers.get("last-modified", feed["modified"]) if response else feed["modified"]
    with store.db() as db:
        db.execute(
            "UPDATE feeds SET checked=?, status=?, etag=?, modified=? WHERE id=?",
            (time.time(), status, etag, modified, feed["id"]),
        )
