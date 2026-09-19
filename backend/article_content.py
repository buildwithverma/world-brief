"""Bounded extraction of first-paragraph article context for sparse RSS items."""

import asyncio
import os
import re
from urllib.parse import urlsplit

import httpx

from .store import digest

MIN_RSS_EXCERPT = 180
MIN_PARAGRAPH = 80
MAX_PARAGRAPH = 1_200
MAX_ARTICLES_PER_COLLECTION = int(os.getenv("ARTICLE_CONTENT_LIMIT", "30"))
CONCURRENCY = 4


def _needs_content(article):
    url = article.get("url", "")
    host = urlsplit(url).hostname or ""
    return (
        len(article.get("excerpt", "").strip()) < MIN_RSS_EXCERPT
        and urlsplit(url).scheme in ("http", "https")
        and host not in {"news.google.com", "www.news.google.com"}
    )


def _first_paragraph(html):
    """Return one substantial readable paragraph, never the full article body."""
    import trafilatura

    text = trafilatura.extract(html, include_comments=False, include_tables=False, include_links=False, output_format="txt") or ""
    paragraphs = [re.sub(r"\s+", " ", item).strip() for item in text.split("\n")]
    for paragraph in paragraphs:
        if len(paragraph) >= MIN_PARAGRAPH:
            return paragraph[:MAX_PARAGRAPH]
    return ""


async def enrich_articles(articles):
    """Fetch a limited number of source pages concurrently and enrich sparse RSS items."""
    candidates = [article for article in articles if _needs_content(article)][:MAX_ARTICLES_PER_COLLECTION]
    if not candidates:
        return 0

    semaphore = asyncio.Semaphore(CONCURRENCY)
    headers = {"User-Agent": "WorldBrief/2.0 news reader (+https://world-brief-12e.pages.dev/)"}
    async with httpx.AsyncClient(timeout=8, follow_redirects=True, max_redirects=3, trust_env=False, headers=headers) as client:
        async def fetch(article):
            async with semaphore:
                try:
                    response = await client.get(article["url"])
                    response.raise_for_status()
                    if "html" not in response.headers.get("content-type", "").lower():
                        return False
                    paragraph = await asyncio.to_thread(_first_paragraph, response.text)
                    if not paragraph:
                        return False
                    article["excerpt"] = paragraph
                    article["content_hash"] = digest(article["title"] + " " + paragraph)
                    return True
                except (httpx.HTTPError, ValueError):
                    return False

        results = await asyncio.gather(*(fetch(article) for article in candidates))
    return sum(results)
