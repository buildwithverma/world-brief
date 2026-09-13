"""Fetch configured publisher RSS feeds for excerpts and original article URLs.

Only known public publisher feeds are fetched. User-added websites are searched
through the news index instead of being fetched as arbitrary server URLs.
"""

import asyncio
import calendar
import time

import feedparser
import httpx

from .config import FEEDS
from .feeds import canonical, plain
from .shorts import publisher_image
from .store import digest

INDIA_FEEDS = [
    ("thehindu.com", "World", "https://www.thehindu.com/news/national/feeder/default.rss"),
    ("indianexpress.com", "World", "https://indianexpress.com/section/india/feed/"),
    ("hindustantimes.com", "World", "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml"),
    ("timesofindia.indiatimes.com", "World", "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms"),
    ("ndtv.com", "World", "https://feeds.feedburner.com/ndtvnews-india-news"),
    ("theprint.in", "World", "https://theprint.in/feed/"),
    ("scroll.in", "World", "https://scroll.in/feed"),
    ("financialexpress.com", "Business", "https://www.financialexpress.com/feed/"),
    ("thewire.in", "World", "https://thewire.in/feed"),
]


def parse_publisher(content, outlet, country, topic, domestic):
    articles = []
    for item in feedparser.parse(content).entries[:100]:
        article = _parse_publisher_item(item, outlet, country, topic, domestic)
        if article:
            articles.append(article)
    return articles


def _parse_publisher_item(item, outlet, country, topic, domestic):
    from .media import domain_of

    url = canonical(item.get("link", ""))
    try:
        if domain_of(url) != outlet["domain"]:
            return None
    except ValueError:
        return None

    date = item.get("published_parsed") or item.get("updated_parsed")
    title = plain(item.get("title", ""))
    if not date or not title:
        return None

    published = calendar.timegm(date)
    if published > time.time() + 3600:
        return None

    excerpt = plain(item.get("summary", ""))[:1600]
    return {
        "id": digest(url),
        "url": url,
        "publisher": outlet["name"],
        "title": title,
        "excerpt": excerpt,
        "topic": topic,
        "region": country,
        "published": published,
        "fetched": time.time(),
        "content_hash": digest(title + excerpt),
        "source_id": outlet["id"],
        "country": country,
        "domestic": int(domestic),
        "major": outlet["major"],
        "domain": outlet["domain"],
        "image_url": publisher_image(item),
    }


async def collect_publishers(store, country):
    selected = {
        row["domain"]: row
        for row in store.rows("SELECT * FROM media WHERE country=? AND selected=1", (country,))
    }
    feeds = _configured_feeds(country)
    feeds = [(domain, topic, url) for domain, topic, url in feeds if domain in selected]

    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=5, follow_redirects=True, max_redirects=3, trust_env=False) as client:

        async def fetch_one(spec):
            domain, topic, url = spec
            async with semaphore:
                return await _fetch_publisher_feed(client, selected[domain], country, topic, spec)

        groups = await asyncio.gather(*(fetch_one(feed) for feed in feeds))

    return [article for group in groups for article in group]


def _configured_feeds(country):
    from .media import domain_of

    feeds = list(INDIA_FEEDS) if country == "IN" else []
    for ident, _publisher, topic, _region, url in FEEDS:
        domain = "bbc.com" if ident.startswith("bbc-") else domain_of(url)
        feeds.append((domain, topic, url))
    return feeds


async def _fetch_publisher_feed(client, outlet, country, topic, spec):
    domain, _topic, url = spec
    try:
        async with asyncio.timeout(7):
            content = await _read_limited_response(client, url)
        domestic = (domain, topic, url) in INDIA_FEEDS and country == "IN"
        return parse_publisher(content, outlet, country, topic, domestic)
    except (httpx.HTTPError, TimeoutError, ValueError):
        return []


async def _read_limited_response(client, url):
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > 2_000_000:
                return bytes()
        return bytes(content)
