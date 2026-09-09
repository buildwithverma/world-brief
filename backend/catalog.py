"""Import user-supplied catalogue data; policy prose is never executed."""
import json
from pathlib import Path
from .store import digest

CATALOG_PATH = Path(__file__).parent / 'catalogs' / 'india_news_sources_ranked.json'

def import_catalog(store, country):
    if country != 'IN':
        return
    raw = CATALOG_PATH.read_text(encoding='utf-8-sig')
    version = digest(raw)
    if store.meta('catalog:' + country) == version:
        return
    from .media import domain_of, MAJOR_DOMAINS
    document = json.loads(raw)
    sources = {}
    for category, entries in document['categories'].items():
        for entry in entries:
            domain = domain_of(entry['website'])
            supported = domain not in {'youtube.com', 'youtu.be'}
            identity = domain if supported else entry['website'].rstrip('/').lower()
            ident = digest(identity)[:24]
            source = sources.setdefault(ident, {'domain': domain, 'entries': [], 'supported': supported})
            source['entries'].append({**entry, 'category': category})
    ordered = sorted(sources.items(), key=lambda pair: (-max(e['priority_score'] for e in pair[1]['entries']), pair[1]['entries'][0]['name']))
    initial = not store.meta('media_initialized:' + country)
    selected = 0
    with store.db() as db:
        for rank, (ident, source) in enumerate(ordered, 1):
            entries = sorted(source['entries'], key=lambda e: -e['priority_score'])
            lead = entries[0]
            choose = initial and source['supported'] and selected < 50
            selected += int(choose)
            metadata = {'entries': entries, 'priority_score': lead['priority_score'], 'priority_tier': lead['priority_tier'], 'supported': source['supported'], 'as_of': document['as_of']}
            db.execute('INSERT OR REPLACE INTO source_catalog VALUES(?,?,?)', (country, ident, json.dumps(metadata)))
            db.execute('INSERT INTO media(country,id,name,domain,url,rank,selected,major,custom) VALUES(?,?,?,?,?,?,?,?,0) ON CONFLICT(country,id) DO UPDATE SET name=excluded.name,url=excluded.url,rank=excluded.rank', (country, ident, lead['name'], source['domain'], lead['website'], rank, int(choose), int(source['domain'] in MAJOR_DOMAINS)))
        db.execute('UPDATE media SET rank=10000+rank WHERE country=? AND id NOT IN (SELECT id FROM source_catalog WHERE country=?) AND rank<10000', (country, country))
    store.set_meta('catalog:' + country, version)
    store.set_meta('media_initialized:' + country, 1)
    store.set_meta('revision', version)

def with_metadata(store, country, outlets):
    metadata = {r['id']: json.loads(r['metadata']) for r in store.rows('SELECT * FROM source_catalog WHERE country=?', (country,))}
    return [{**outlet, 'catalog': metadata.get(outlet['id'])} for outlet in outlets]
