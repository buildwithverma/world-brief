-- Private application tables; never expose this schema through PostgREST.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS worldbrief;
REVOKE ALL ON SCHEMA worldbrief FROM PUBLIC;
SET LOCAL search_path TO worldbrief, public, extensions;



            CREATE TABLE IF NOT EXISTS feeds(
                id TEXT PRIMARY KEY,
                publisher TEXT,
                topic TEXT,
                region TEXT,
                url TEXT,
                etag TEXT,
                modified TEXT,
                checked DOUBLE PRECISION,
                status TEXT
            );

            CREATE TABLE IF NOT EXISTS articles(
                id TEXT PRIMARY KEY,
                url TEXT UNIQUE,
                publisher TEXT,
                title TEXT,
                excerpt TEXT,
                topic TEXT,
                region TEXT,
                published DOUBLE PRECISION,
                fetched DOUBLE PRECISION,
                content_hash TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published);

            CREATE TABLE IF NOT EXISTS article_images(
                article_id TEXT PRIMARY KEY,
                url TEXT
            );

            CREATE TABLE IF NOT EXISTS country_articles(
                country TEXT,
                article_id TEXT,
                source_id TEXT,
                domestic INTEGER,
                topic TEXT,
                PRIMARY KEY(country, article_id)
            );
            CREATE INDEX IF NOT EXISTS idx_country_source ON country_articles(country, source_id);

            CREATE TABLE IF NOT EXISTS media(
                country TEXT,
                id TEXT,
                name TEXT,
                domain TEXT,
                url TEXT,
                rank INTEGER,
                selected INTEGER,
                major INTEGER,
                custom INTEGER DEFAULT 0,
                PRIMARY KEY(country, id)
            );

            CREATE TABLE IF NOT EXISTS source_catalog(
                country TEXT,
                id TEXT,
                metadata TEXT,
                PRIMARY KEY(country, id)
            );

            CREATE TABLE IF NOT EXISTS story_cache(
                id TEXT PRIMARY KEY,
                version TEXT,
                data TEXT,
                expires DOUBLE PRECISION
            );

            CREATE TABLE IF NOT EXISTS query_cache(
                id TEXT PRIMARY KEY,
                exact_key TEXT,
                query TEXT,
                scope TEXT,
                data TEXT,
                created DOUBLE PRECISION,
                expires DOUBLE PRECISION,
                revision TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_query_exact ON query_cache(exact_key, expires);

            CREATE TABLE IF NOT EXISTS summary_jobs(
                id TEXT PRIMARY KEY,
                version TEXT,
                created DOUBLE PRECISION,
                next_attempt DOUBLE PRECISION,
                attempts INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending'
            );

            CREATE TABLE IF NOT EXISTS saved(
                id TEXT PRIMARY KEY,
                data TEXT,
                created DOUBLE PRECISION
            );

            CREATE TABLE IF NOT EXISTS meta(
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS usage(
                day TEXT PRIMARY KEY,
                tokens INTEGER
            );

CREATE TABLE IF NOT EXISTS article_vectors (
 article_id TEXT PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
 content_hash TEXT NOT NULL, model TEXT NOT NULL,
 embedding vector(384) NOT NULL
);
CREATE TABLE IF NOT EXISTS query_vectors (
 id TEXT PRIMARY KEY REFERENCES query_cache(id) ON DELETE CASCADE,
 model TEXT NOT NULL, embedding vector(384) NOT NULL
);
REVOKE ALL ON ALL TABLES IN SCHEMA worldbrief FROM PUBLIC;
