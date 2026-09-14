"""Country discovery and selectable publisher identities from public news RSS."""

import asyncio
import calendar
import re
import time
from collections import Counter
from urllib.parse import urlencode, urlsplit

import feedparser
import httpx
import pycountry

from .feeds import canonical, plain
from .store import digest

COUNTRIES = sorted(
    [{"code": country.alpha_2, "name": getattr(country, "common_name", country.name)} for country in pycountry.countries],
    key=lambda country: country["name"],
)
NAMES = {country["code"]: country["name"] for country in COUNTRIES}
NAMES.update(
    GB="United Kingdom",
    US="United States",
    KR="South Korea",
    TW="Taiwan",
    VN="Vietnam",
    RU="Russia",
    IR="Iran",
    BO="Bolivia",
    VE="Venezuela",
)
COUNTRIES = sorted([{"code": code, "name": name} for code, name in NAMES.items()], key=lambda country: country["name"])

EDITIONS = {
    "IN": "en-IN",
    "US": "en-US",
    "GB": "en-GB",
    "CA": "en-CA",
    "AU": "en-AU",
    "NZ": "en-NZ",
    "SG": "en-SG",
    "ZA": "en-ZA",
    "IE": "en-IE",
}

# A transparent curated identity list, not a quality or truth score.
MAJOR_DOMAINS = set(
    """
    bbc.com bbc.co.uk reuters.com apnews.com theguardian.com cnn.com nytimes.com
    washingtonpost.com wsj.com bloomberg.com ft.com aljazeera.com dw.com france24.com
    euronews.com nbcnews.com cbsnews.com abcnews.go.com abc.net.au foxnews.com npr.org
    usatoday.com cnbc.com time.com newsweek.com politico.com thehill.com axios.com sky.com
    news.sky.com independent.co.uk telegraph.co.uk thetimes.com itv.com channel4.com cbc.ca
    ctvnews.ca globalnews.ca theglobeandmail.com thestar.com nationalpost.com thehindu.com
    timesofindia.indiatimes.com hindustantimes.com indianexpress.com ndtv.com indiatoday.in
    news18.com theprint.in thewire.in scroll.in deccanherald.com newindianexpress.com
    economictimes.indiatimes.com livemint.com business-standard.com financialexpress.com
    thehindubusinessline.com zeenews.india.com dnaindia.com abplive.com aajtak.in
    firstpost.com outlookindia.com tribuneindia.com telegraphindia.com businessinsider.com
    smh.com.au theage.com.au theaustralian.com.au news.com.au sbs.com.au rnz.co.nz
    nzherald.co.nz stuff.co.nz channelnewsasia.com straitstimes.com scmp.com japantimes.co.jp
    asahi.com nhk.or.jp koreatimes.co.kr koreaherald.com yna.co.kr dawn.com thenews.com.pk
    geo.tv dailystar.net dhakatribune.com thejakartapost.com kompas.com bangkokpost.com
    malaymail.com thestar.com.my rappler.com inquirer.net philstar.com manilatimes.net
    haaretz.com timesofisrael.com jpost.com arabnews.com thenationalnews.com gulfnews.com
    khaleejtimes.com africanews.com news24.com dailymaverick.co.za iol.co.za nation.africa
    standardmedia.co.ke punchng.com premiumtimesng.com vanguardngr.com thecable.ng al-monitor.com
    lemonde.fr lefigaro.fr liberation.fr spiegel.de zeit.de faz.net sueddeutsche.de elpais.com
    elmundo.es corriere.it repubblica.it ansa.it rte.ie irishtimes.com
    """.split()
)
ALIASES = {
    "bbc.co.uk": "bbc.com",
    "news.sky.com": "sky.com",
    "m.timesofindia.com": "timesofindia.indiatimes.com",
    "m.economictimes.com": "economictimes.indiatimes.com",
}

BLOCKED_DOMAINS = {
    "youtube.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "reddit.com",
    "x.com",
    "tiktok.com",
    "medium.com",
    "researchgate.net",
}


def domain_of(url):
    host = (urlsplit(url if "://" in url else "https://" + url).hostname or "").lower().removeprefix("www.")
    public_domain = re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", host)
    if not public_domain or ".." in host:
        raise ValueError("Enter a public news website, for example bbc.com.")

    host = ALIASES.get(host, host)
    for known in sorted(MAJOR_DOMAINS, key=len, reverse=True):
        if host == known or host.endswith("." + known):
            return ALIASES.get(known, known)
    return host


def publisher_id(url):
    return digest(domain_of(url))[:24]


def country_name(code):
    return NAMES.get(code, code)


def feed_urls(country, query="", topic="All"):
    params = _google_news_params(country)

    if query:
        topic_term = "" if topic == "All" else topic
        query_text = f"{country_name(country)} {query} {topic_term} when:7d"
        return [(topic, True, _google_rss_url("search", params, query_text))]

    if country not in EDITIONS:
        return [
            (topic_name, True, _google_rss_url("search", params, f"{country_name(country)} {suffix} when:7d"))
            for topic_name, suffix in [
                ("World", "news"),
                ("Business", "business economy"),
                ("Technology", "technology"),
                ("Science", "science"),
                ("Climate", "climate"),
                ("World", "politics"),
            ]
        ]

    result = [
        ("World", True, "https://news.google.com/rss?" + urlencode(params)),
        ("World", True, _google_rss_url("search", params, f"{country_name(country)} when:1d")),
    ]
    sections = [
        ("World", "NATION"),
        ("World", "WORLD"),
        ("Business", "BUSINESS"),
        ("Technology", "TECHNOLOGY"),
        ("Science", "SCIENCE"),
    ]
    for topic_name, section in sections:
        domestic = section != "WORLD"
        result.append((topic_name, domestic, _google_section_url(section, params)))

    result.append(("Climate", True, _google_rss_url("search", params, f"{country_name(country)} climate when:7d")))
    return result


def _google_news_params(country):
    edition = country if country in EDITIONS else "US"
    return {"hl": EDITIONS.get(country, "en-US"), "gl": edition, "ceid": f"{edition}:en"}


def _google_rss_url(kind, params, query):
    if kind != "search":
        raise ValueError("Unsupported Google RSS kind")
    return "https://news.google.com/rss/search?" + urlencode({**params, "q": query})


def _google_section_url(section, params):
    return "https://news.google.com/rss/headlines/section/topic/" + section + "?" + urlencode(params)


def parse_news(content, country, topic, domestic):
    articles = []
    publishers = {}

    for item in feedparser.parse(content).entries[:100]:
        article = _parse_google_news_item(item, country, topic, domestic)
        if not article:
            continue

        articles.append(article)
        publishers[article["source_id"]] = {
            "id": article["source_id"],
            "name": article["publisher"],
            "domain": article["domain"],
            "url": "https://" + article["domain"],
            "major": article["major"],
        }

    return articles, publishers


def _parse_google_news_item(item, country, topic, domestic):
    source = item.get("source", {})
    publisher = plain(source.get("title", ""))
    source_url = source.get("href", "")

    try:
        domain = domain_of(source_url)
    except ValueError:
        return None

    if domain in BLOCKED_DOMAINS or not publisher or domain == "news.google.com":
        return None

    url = canonical(item.get("link", ""))
    title = _clean_google_title(item.get("title", ""), publisher)
    date = item.get("published_parsed") or item.get("updated_parsed")
    if not url or not title or not date:
        return None

    published = calendar.timegm(date)
    if published > time.time() + 3600:
        return None

    return {
        "id": digest(url),
        "url": url,
        "publisher": publisher,
        "title": title,
        "excerpt": "",
        "topic": topic if topic != "All" else "World",
        "region": country,
        "published": published,
        "fetched": time.time(),
        "content_hash": digest(title),
        "source_id": digest(domain)[:24],
        "country": country,
        "domestic": int(domestic),
        "major": int(domain in MAJOR_DOMAINS),
        "domain": domain,
    }


def _clean_google_title(raw_title, publisher):
    title = plain(raw_title)
    suffix = " - " + publisher
    if title.endswith(suffix):
        title = title[: -len(suffix)]
    return title


async def collect_country(store, country, query="", topic="All"):
    from .catalog import import_catalog
    from .publisher_feeds import collect_publishers

    import_catalog(store, country)
    feeds = feed_urls(country, query, topic)
    feeds.extend(_custom_site_feeds(store, country, query, topic))

    indexed_articles, publishers, available = await _fetch_google_feeds(country, feeds)
    direct_articles = await collect_publishers(store, country)

    articles = {article["id"]: article for article in indexed_articles}
    for article in direct_articles:
        articles[article["id"]] = article

    _upsert_publishers(store, country, publishers, articles.values())
    store.upsert_articles(list(articles.values()))
    changed = _link_country_articles(store, country, articles.values())

    if changed:
        store.set_meta("revision", time.time_ns())
    store.set_meta("fetch_attempt:" + country, time.time())
    if articles:
        store.set_meta("last_fetch:" + country, time.time())

    return {
        "articles": len(articles),
        "outlets": len(publishers),
        "available": available,
        "total": len(feeds),
    }


def _custom_site_feeds(store, country, query, topic):
    feeds = []
    custom = store.rows("SELECT domain FROM media WHERE country=? AND custom=1 AND selected=1", (country,))
    for offset in range(0, len(custom), 8):
        domains = " OR ".join("site:" + row["domain"] for row in custom[offset : offset + 8])
        feeds.extend(feed_urls(country, "(" + domains + ") " + query, topic))
    return feeds


async def _fetch_google_feeds(country, feeds):
    semaphore = asyncio.Semaphore(4)
    articles = {}
    publishers = {}

    async with httpx.AsyncClient(
        timeout=12,
        follow_redirects=True,
        trust_env=False,
        headers={"User-Agent": "WorldBrief/2.0 personal news reader"},
    ) as client:

        async def fetch_one(spec):
            topic, domestic, url = spec
            async with semaphore:
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    return parse_news(response.content, country, topic, domestic)
                except (httpx.HTTPError, ValueError):
                    return [], {}

        results = await asyncio.gather(*(fetch_one(feed) for feed in feeds))

    for items, sources in results:
        for article in items:
            existing = articles.get(article["id"])
            if existing:
                article["domestic"] = max(article["domestic"], existing["domestic"])
            articles[article["id"]] = article
        publishers.update(sources)

    return list(articles.values()), publishers, sum(bool(items) for items, _sources in results)


def _upsert_publishers(store, country, publishers, articles):
    frequency = Counter(article["source_id"] for article in articles)
    initial = not store.meta("media_initialized:" + country)

    with store.db() as db:
        ordered = sorted(publishers, key=lambda source_id: (-frequency[source_id], publishers[source_id]["name"]))
        for rank, source_id in enumerate(ordered, 1):
            publisher = publishers[source_id]
            effective_rank = rank + (10000 if country == "IN" else 0)
            db.execute(
                """
                INSERT INTO media(country, id, name, domain, url, rank, selected, major, custom)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(country, id) DO UPDATE SET major = excluded.major
                """,
                (
                    country,
                    source_id,
                    publisher["name"],
                    publisher["domain"],
                    publisher["url"],
                    effective_rank,
                    int(initial and rank <= 50),
                    publisher["major"],
                ),
            )

    if publishers:
        store.set_meta("media_initialized:" + country, 1)


def _link_country_articles(store, country, articles):
    changed = False
    with store.db() as db:
        for article in articles:
            old = db.execute(
                "SELECT domestic, topic FROM country_articles WHERE country=? AND article_id=?",
                (country, article["id"]),
            ).fetchone()
            changed = changed or _country_article_changed(old, article)
            db.execute(
                """
                INSERT INTO country_articles VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(country, article_id) DO UPDATE SET
                    domestic = MAX(domestic, excluded.domestic),
                    topic = CASE WHEN excluded.topic != 'World' THEN excluded.topic ELSE country_articles.topic END
                """,
                (country, article["id"], article["source_id"], article["domestic"], article["topic"]),
            )
    return changed


def _country_article_changed(old, article):
    if not old:
        return True
    if old["domestic"] < article["domestic"]:
        return True
    return article["topic"] != "World" and old["topic"] != article["topic"]
