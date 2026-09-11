import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager,closing
from .config import DATA, FEEDS

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

class Store:
    def __init__(self, path=None):
        self.path = str(path or DATA / 'brief.sqlite3')
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS feeds(id TEXT PRIMARY KEY, publisher TEXT, topic TEXT, region TEXT, url TEXT, etag TEXT, modified TEXT, checked REAL, status TEXT);
                CREATE TABLE IF NOT EXISTS articles(id TEXT PRIMARY KEY, url TEXT UNIQUE, publisher TEXT, title TEXT, excerpt TEXT, topic TEXT, region TEXT, published REAL, fetched REAL, content_hash TEXT);
                CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published);
                CREATE TABLE IF NOT EXISTS story_cache(id TEXT PRIMARY KEY, version TEXT, data TEXT, expires REAL);
                CREATE TABLE IF NOT EXISTS query_cache(id TEXT PRIMARY KEY, exact_key TEXT, query TEXT, scope TEXT, data TEXT, created REAL, expires REAL, revision TEXT);
                CREATE INDEX IF NOT EXISTS idx_query_exact ON query_cache(exact_key, expires);
                CREATE TABLE IF NOT EXISTS saved(id TEXT PRIMARY KEY, data TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY, tokens INTEGER);
                CREATE TABLE IF NOT EXISTS media(country TEXT,id TEXT,name TEXT,domain TEXT,url TEXT,rank INTEGER,selected INTEGER,major INTEGER,custom INTEGER DEFAULT 0,PRIMARY KEY(country,id));
                CREATE TABLE IF NOT EXISTS country_articles(country TEXT,article_id TEXT,source_id TEXT,domestic INTEGER,topic TEXT,PRIMARY KEY(country,article_id));
                CREATE INDEX IF NOT EXISTS idx_country_source ON country_articles(country,source_id);
                CREATE TABLE IF NOT EXISTS source_catalog(country TEXT,id TEXT,metadata TEXT,PRIMARY KEY(country,id));
                CREATE TABLE IF NOT EXISTS article_images(article_id TEXT PRIMARY KEY,url TEXT);
                CREATE TABLE IF NOT EXISTS summary_jobs(id TEXT PRIMARY KEY,version TEXT,created REAL,next_attempt REAL,attempts INTEGER DEFAULT 0,status TEXT DEFAULT 'pending');
            ''')
            for f in FEEDS:
                db.execute('INSERT INTO feeds(id,publisher,topic,region,url,status) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET publisher=excluded.publisher,topic=excluded.topic,region=excluded.region,url=excluded.url', (*f, 'pending'))

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def rows(self, sql, args=()):
        with self.db() as db:
            return [dict(x) for x in db.execute(sql, args).fetchall()]

    def meta(self, key, default=''):
        rows = self.rows('SELECT value FROM meta WHERE key=?', (key,))
        return rows[0]['value'] if rows else default

    def set_meta(self, key, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, str(value)))

    def upsert_articles(self, articles):
        changed = 0
        with self.db() as db:
            for a in articles:
                if a.get('image_url'):
                    image=db.execute('SELECT url FROM article_images WHERE article_id=?',(a['id'],)).fetchone()
                    if not image or image[0]!=a['image_url']:
                        db.execute('INSERT OR REPLACE INTO article_images VALUES(?,?)',(a['id'],a['image_url']))
                        changed+=1
                old = db.execute('SELECT content_hash FROM articles WHERE id=?', (a['id'],)).fetchone()
                if not old or old[0] != a['content_hash']:
                    db.execute('INSERT OR REPLACE INTO articles VALUES(?,?,?,?,?,?,?,?,?,?)', tuple(a[k] for k in ['id','url','publisher','title','excerpt','topic','region','published','fetched','content_hash']))
                    changed += 1
        if changed:
            self.set_meta('revision', str(time.time_ns()))
        return changed

    def exact(self, key, revision):
        rows = self.rows('SELECT * FROM query_cache WHERE exact_key=? AND expires>? AND revision=? ORDER BY created DESC LIMIT 1', (key, time.time(), revision))
        return rows[0] if rows else None

    def put_query(self, key, query, scope, result, ttl, revision):
        ident = digest(key + revision)
        now = time.time()
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO query_cache VALUES(?,?,?,?,?,?,?,?)', (ident,key,query,json.dumps(scope,sort_keys=True),json.dumps(result),now,now+ttl,revision))
            # Bound disk growth; embeddings are pruned separately.
            db.execute('DELETE FROM query_cache WHERE created<?', (now - 30*86400,))
        return ident
