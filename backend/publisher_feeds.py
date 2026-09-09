"""Publisher RSS supplies real excerpts and original article URLs.

Only configured public publisher feeds are fetched. A user-added website is
searched through the news index, never fetched as an arbitrary server URL.
"""
import asyncio
import calendar
import time
import feedparser
import httpx
from .config import FEEDS
from .feeds import plain, canonical
from .store import digest

INDIA_FEEDS = [
    ('thehindu.com', 'World', 'https://www.thehindu.com/news/national/feeder/default.rss'),
    ('indianexpress.com', 'World', 'https://indianexpress.com/section/india/feed/'),
    ('hindustantimes.com', 'World', 'https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml'),
    ('timesofindia.indiatimes.com', 'World', 'https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms'),
    ('ndtv.com', 'World', 'https://feeds.feedburner.com/ndtvnews-india-news'),
    ('theprint.in', 'World', 'https://theprint.in/feed/'),
    ('scroll.in', 'World', 'https://scroll.in/feed'),
    ('financialexpress.com', 'Business', 'https://www.financialexpress.com/feed/'),
    ('thewire.in', 'World', 'https://thewire.in/feed'),
]

def parse_publisher(content, outlet, country, topic, domestic):
    from .media import domain_of
    articles = []
    for item in feedparser.parse(content).entries[:100]:
        url = canonical(item.get('link', ''))
        try:
            if domain_of(url) != outlet['domain']:
                continue
        except ValueError:
            continue
        date = item.get('published_parsed') or item.get('updated_parsed')
        title = plain(item.get('title', ''))
        if not date or not title:
            continue
        published = calendar.timegm(date)
        if published > time.time() + 3600:
            continue
        excerpt = plain(item.get('summary', ''))[:1600]
        articles.append(dict(id=digest(url), url=url, publisher=outlet['name'], title=title, excerpt=excerpt,
            topic=topic, region=country, published=published, fetched=time.time(), content_hash=digest(title + excerpt),
            source_id=outlet['id'], country=country, domestic=int(domestic), major=outlet['major'], domain=outlet['domain']))
    return articles

async def collect_publishers(store, country):
    from .media import domain_of
    selected = {r['domain']: r for r in store.rows('SELECT * FROM media WHERE country=? AND selected=1', (country,))}
    feeds = list(INDIA_FEEDS) if country == 'IN' else []
    feeds += [('bbc.com' if ident.startswith('bbc-') else domain_of(url), topic, url) for ident, _, topic, _, url in FEEDS]
    feeds = [(domain, topic, url) for domain, topic, url in feeds if domain in selected]
    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=5, follow_redirects=True, max_redirects=3, trust_env=False) as client:
        async def one(spec):
            domain, topic, url = spec
            async with semaphore:
                try:
                    async with asyncio.timeout(7):
                        async with client.stream('GET', url) as response:
                            response.raise_for_status()
                            content = bytearray()
                            async for chunk in response.aiter_bytes():
                                content.extend(chunk)
                                if len(content) > 2_000_000:
                                    return []
                    return parse_publisher(bytes(content), selected[domain], country, topic, (domain, topic, url) in INDIA_FEEDS and country == 'IN')
                except (httpx.HTTPError, TimeoutError, ValueError):
                    return []
        return [a for group in await asyncio.gather(*(one(f) for f in feeds)) for a in group]
