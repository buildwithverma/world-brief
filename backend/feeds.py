import asyncio
import calendar
import html
import re
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import feedparser
import httpx
from .store import digest

def plain(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))).strip()

def canonical(url):
    p = urlsplit(url)
    if p.scheme not in ('https', 'http') or not p.netloc:
        return ''
    query = urlencode([(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k not in ('fbclid','gclid')])
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip('/') or '/', query, ''))

async def refresh(store):
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=18, follow_redirects=True, trust_env=False, headers={'User-Agent':'WorldBrief/1.0 (personal RSS reader)'}) as client:
        async def one(feed):
            async with semaphore:
                try:
                    headers = {}
                    if feed['etag']: headers['If-None-Match'] = feed['etag']
                    if feed['modified']: headers['If-Modified-Since'] = feed['modified']
                    response = await client.get(feed['url'], headers=headers)
                    if response.status_code != 304:
                        response.raise_for_status()
                        parsed = feedparser.parse(response.content)
                        if not parsed.entries:
                            raise ValueError('Empty or invalid feed')
                        articles = []
                        for item in parsed.entries[:25]:
                            url = canonical(item.get('link',''))
                            title = plain(item.get('title',''))
                            if not url or not title: continue
                            excerpt = plain(item.get('summary',''))[:1800]
                            date = item.get('published_parsed') or item.get('updated_parsed')
                            published = calendar.timegm(date) if date else time.time()
                            if published > time.time()+3600: continue
                            articles.append(dict(id=digest(url),url=url,publisher=feed['publisher'],title=title,excerpt=excerpt,topic=feed['topic'],region=feed['region'],published=published,fetched=time.time(),content_hash=digest(title+' '+excerpt)))
                        store.upsert_articles(articles)
                    with store.db() as db:
                        db.execute('UPDATE feeds SET checked=?,status=?,etag=?,modified=? WHERE id=?', (time.time(),'ok',response.headers.get('etag',feed['etag']),response.headers.get('last-modified',feed['modified']),feed['id']))
                    return True
                except Exception:
                    with store.db() as db:
                        db.execute('UPDATE feeds SET checked=?,status=? WHERE id=?', (time.time(),'unavailable',feed['id']))
                    return False
        outcomes = await asyncio.gather(*(one(f) for f in store.rows('SELECT * FROM feeds')))
    store.set_meta('last_fetch_attempt',time.time())
    if any(outcomes): store.set_meta('last_fetch',time.time())
    with store.db() as db:
        db.execute('DELETE FROM articles WHERE published<?', (time.time()-30*86400,))
    return {'available':sum(outcomes),'total':len(outcomes)}
