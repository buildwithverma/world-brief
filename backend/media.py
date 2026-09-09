"""Country discovery and selectable publisher identities from public news RSS."""
import asyncio
import calendar
import re
import time
from collections import Counter
from urllib.parse import urlencode,urlsplit
import feedparser
import httpx
import pycountry
from .feeds import plain,canonical
from .store import digest

COUNTRIES=sorted([{'code':c.alpha_2,'name':getattr(c,'common_name',c.name)} for c in pycountry.countries],key=lambda c:c['name'])
NAMES={c['code']:c['name'] for c in COUNTRIES}
NAMES.update(GB='United Kingdom',US='United States',KR='South Korea',TW='Taiwan',VN='Vietnam',RU='Russia',IR='Iran',BO='Bolivia',VE='Venezuela')
COUNTRIES=sorted([{'code':k,'name':v} for k,v in NAMES.items()],key=lambda c:c['name'])
EDITIONS={'IN':'en-IN','US':'en-US','GB':'en-GB','CA':'en-CA','AU':'en-AU','NZ':'en-NZ','SG':'en-SG','ZA':'en-ZA','IE':'en-IE'}
# A transparent curated identity list, not a quality or truth score.
MAJOR_DOMAINS=set('bbc.com bbc.co.uk reuters.com apnews.com theguardian.com cnn.com nytimes.com washingtonpost.com wsj.com bloomberg.com ft.com aljazeera.com dw.com france24.com euronews.com nbcnews.com cbsnews.com abcnews.go.com abc.net.au foxnews.com npr.org usatoday.com cnbc.com time.com newsweek.com politico.com thehill.com axios.com sky.com news.sky.com independent.co.uk telegraph.co.uk thetimes.com itv.com channel4.com cbc.ca ctvnews.ca globalnews.ca theglobeandmail.com thestar.com nationalpost.com thehindu.com timesofindia.indiatimes.com hindustantimes.com indianexpress.com ndtv.com indiatoday.in news18.com theprint.in thewire.in scroll.in deccanherald.com newindianexpress.com economictimes.indiatimes.com livemint.com business-standard.com financialexpress.com thehindubusinessline.com zeenews.india.com dnaindia.com abplive.com aajtak.in firstpost.com outlookindia.com tribuneindia.com telegraphindia.com businessinsider.com smh.com.au theage.com.au theaustralian.com.au news.com.au sbs.com.au rnz.co.nz nzherald.co.nz stuff.co.nz channelnewsasia.com straitstimes.com scmp.com japantimes.co.jp asahi.com nhk.or.jp koreatimes.co.kr koreaherald.com yna.co.kr dawn.com thenews.com.pk geo.tv dailystar.net dhakatribune.com thejakartapost.com kompas.com bangkokpost.com malaymail.com thestar.com.my rappler.com inquirer.net philstar.com manilatimes.net haaretz.com timesofisrael.com jpost.com arabnews.com thenationalnews.com gulfnews.com khaleejtimes.com africanews.com news24.com dailymaverick.co.za iol.co.za nation.africa standardmedia.co.ke punchng.com premiumtimesng.com vanguardngr.com thecable.ng al-monitor.com lemonde.fr lefigaro.fr liberation.fr spiegel.de zeit.de faz.net sueddeutsche.de elpais.com elmundo.es corriere.it repubblica.it ansa.it rte.ie irishtimes.com'.split())
ALIASES={'bbc.co.uk':'bbc.com','news.sky.com':'sky.com','m.timesofindia.com':'timesofindia.indiatimes.com','m.economictimes.com':'economictimes.indiatimes.com'}

def domain_of(url):
    host=(urlsplit(url if '://' in url else 'https://'+url).hostname or '').lower().removeprefix('www.')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}',host) or '..' in host:
        raise ValueError('Enter a public news website, for example bbc.com.')
    host=ALIASES.get(host,host)
    for known in sorted(MAJOR_DOMAINS,key=len,reverse=True):
        if host==known or host.endswith('.'+known):return ALIASES.get(known,known)
    return host

def publisher_id(url):return digest(domain_of(url))[:24]
def country_name(code):return NAMES.get(code,code)

def feed_urls(country,query=''):
    hl=EDITIONS.get(country,'en-US');edition=country if country in EDITIONS else 'US'
    params={'hl':hl,'gl':edition,'ceid':f'{edition}:en'}
    if query:
        return [('All',True,'https://news.google.com/rss/search?'+urlencode({**params,'q':f'{country_name(country)} {query} when:7d'}))]
    if country not in EDITIONS:
        return [(t,True,'https://news.google.com/rss/search?'+urlencode({**params,'q':f'{country_name(country)} {suffix} when:7d'})) for t,suffix in [('World','news'),('Business','business economy'),('Technology','technology'),('Science','science'),('Climate','climate'),('World','politics')]]
    result=[('World',True,'https://news.google.com/rss?'+urlencode(params)),('World',True,'https://news.google.com/rss/search?'+urlencode({**params,'q':f'{country_name(country)} when:1d'}))]
    for topic,name in [('World','NATION'),('World','WORLD'),('Business','BUSINESS'),('Technology','TECHNOLOGY'),('Science','SCIENCE'),('Climate','HEALTH')]:
        if name=='HEALTH':continue
        result.append((topic,name!='WORLD','https://news.google.com/rss/headlines/section/topic/'+name+'?'+urlencode(params)))
    result.append(('Climate',True,'https://news.google.com/rss/search?'+urlencode({**params,'q':f'{country_name(country)} climate when:7d'})))
    return result

def parse_news(content,country,topic,domestic):
    articles=[];publishers={}
    for item in feedparser.parse(content).entries[:100]:
        source=item.get('source',{})
        name=plain(source.get('title',''))
        source_url=source.get('href','')
        try: domain=domain_of(source_url)
        except ValueError:continue
        if domain in {'youtube.com','linkedin.com','facebook.com','instagram.com','reddit.com','x.com','tiktok.com','medium.com','researchgate.net'}:continue
        if not name or domain=='news.google.com':continue
        pid=digest(domain)[:24];url=canonical(item.get('link',''))
        if not url:continue
        title=plain(item.get('title',''))
        if title.endswith(' - '+name):title=title[:-len(name)-3]
        if not title:continue
        date=item.get('published_parsed') or item.get('updated_parsed')
        if not date:continue
        published=calendar.timegm(date)
        if published>time.time()+3600:continue
        # Google News supplies indexed headlines, not the publisher's article body.
        articles.append(dict(id=digest(url),url=url,publisher=name,title=title,excerpt='',topic=topic if topic!='All' else 'World',region=country,published=published,fetched=time.time(),content_hash=digest(title),source_id=pid,country=country,domestic=int(domestic),major=int(domain in MAJOR_DOMAINS),domain=domain))
        publishers[pid]={'id':pid,'name':name,'domain':domain,'url':'https://'+domain,'major':int(domain in MAJOR_DOMAINS)}
    return articles,publishers

async def collect_country(store,country,query=''):
    from .catalog import import_catalog
    from .publisher_feeds import collect_publishers
    import_catalog(store,country)
    feeds=feed_urls(country,query)
    # Include user-added websites even if absent from today's edition.
    custom=store.rows('SELECT domain FROM media WHERE country=? AND custom=1 AND selected=1',(country,))
    for i in range(0,len(custom),8):
        domains=' OR '.join('site:'+x['domain'] for x in custom[i:i+8])
        feeds.extend(feed_urls(country,'('+domains+') '+query))
    semaphore=asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=12,follow_redirects=True,trust_env=False,headers={'User-Agent':'WorldBrief/2.0 personal news reader'}) as client:
        async def one(spec):
            topic,domestic,url=spec
            async with semaphore:
                try:
                    r=await client.get(url);r.raise_for_status()
                    return parse_news(r.content,country,topic,domestic)
                except (httpx.HTTPError,ValueError):return [],{}
        results=await asyncio.gather(*(one(f) for f in feeds))
    articles={};publishers={}
    for items,sources in results:
        for a in items:
            if a['id'] in articles:a['domestic']=max(a['domestic'],articles[a['id']]['domestic'])
            articles[a['id']]=a
        publishers.update(sources)
    direct = await collect_publishers(store, country)
    for a in direct:
        articles[a['id']] = a
    frequency=Counter(a['source_id'] for a in articles.values())
    initial=not store.meta('media_initialized:'+country)
    with store.db() as db:
        for rank,pid in enumerate(sorted(publishers,key=lambda p:(-frequency[p],publishers[p]['name'])),1):
            p=publishers[pid]
            effective_rank = rank + (10000 if country == 'IN' else 0)
            db.execute('INSERT INTO media(country,id,name,domain,url,rank,selected,major,custom) VALUES(?,?,?,?,?,?,?,?,0) ON CONFLICT(country,id) DO UPDATE SET major=excluded.major',(country,pid,p['name'],p['domain'],p['url'],effective_rank,int(initial and rank<=50),p['major']))
    if publishers:store.set_meta('media_initialized:'+country,1)
    store.upsert_articles(list(articles.values()))
    changed = False
    with store.db() as db:
        for a in articles.values():
            old = db.execute('SELECT domestic,topic FROM country_articles WHERE country=? AND article_id=?', (country,a['id'])).fetchone()
            changed = changed or not old or old['domestic'] < a['domestic'] or (a['topic'] != 'World' and old['topic'] != a['topic'])
            db.execute('INSERT INTO country_articles VALUES(?,?,?,?,?) ON CONFLICT(country,article_id) DO UPDATE SET domestic=MAX(domestic,excluded.domestic),topic=CASE WHEN excluded.topic!=\'World\' THEN excluded.topic ELSE topic END',(country,a['id'],a['source_id'],a['domestic'],a['topic']))
    if changed:store.set_meta('revision',time.time_ns())
    store.set_meta('fetch_attempt:'+country,time.time())
    if articles:store.set_meta('last_fetch:'+country,time.time())
    return {'articles':len(articles),'outlets':len(publishers),'available':sum(bool(a) for a,p in results),'total':len(feeds)}
