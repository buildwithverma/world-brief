"""Import user-supplied catalogue data; policy prose is never executed."""

import json
from pathlib import Path

from .store import digest

CATALOG_PATH = Path(__file__).parent / "catalogs" / "india_news_sources_ranked.json"


def import_catalog(store, country):
    if country != "IN":
        return

    raw = CATALOG_PATH.read_text(encoding="utf-8-sig")
    version = digest(raw)
    if store.meta("catalog:" + country) == version:
        return

    document = json.loads(raw)
    sources = _catalog_sources(document)
    ordered = _ranked_sources(sources)
    initial = not store.meta("media_initialized:" + country)

    selected = 0
    with store.db() as db:
        for rank, (source_id, source) in enumerate(ordered, 1):
            choose = initial and source["supported"] and selected < 50
            selected += int(choose)
            _upsert_catalog_source(db, country, source_id, source, rank, choose, document["as_of"])

        db.execute(
            """
            UPDATE media
            SET rank = 10000 + rank
            WHERE country = ?
              AND id NOT IN (SELECT id FROM source_catalog WHERE country = ?)
              AND rank < 10000
            """,
            (country, country),
        )

    store.set_meta("catalog:" + country, version)
    store.set_meta("media_initialized:" + country, 1)
    store.set_meta("revision", version)


def with_metadata(store, country, outlets):
    metadata = {
        row["id"]: json.loads(row["metadata"])
        for row in store.rows("SELECT * FROM source_catalog WHERE country=?", (country,))
    }
    return [{**outlet, "catalog": metadata.get(outlet["id"])} for outlet in outlets]


def _catalog_sources(document):
    from .media import domain_of

    sources = {}
    for category, entries in document["categories"].items():
        for entry in entries:
            domain = domain_of(entry["website"])
            supported = domain not in {"youtube.com", "youtu.be"}
            identity = domain if supported else entry["website"].rstrip("/").lower()
            source_id = digest(identity)[:24]
            source = sources.setdefault(
                source_id,
                {"domain": domain, "entries": [], "supported": supported},
            )
            source["entries"].append({**entry, "category": category})
    return sources


def _ranked_sources(sources):
    return sorted(
        sources.items(),
        key=lambda pair: (
            -max(entry["priority_score"] for entry in pair[1]["entries"]),
            pair[1]["entries"][0]["name"],
        ),
    )


def _upsert_catalog_source(db, country, source_id, source, rank, selected, as_of):
    from .media import MAJOR_DOMAINS

    entries = sorted(source["entries"], key=lambda entry: -entry["priority_score"])
    lead = entries[0]
    metadata = {
        "entries": entries,
        "priority_score": lead["priority_score"],
        "priority_tier": lead["priority_tier"],
        "supported": source["supported"],
        "as_of": as_of,
    }
    db.execute(
        "INSERT OR REPLACE INTO source_catalog VALUES(?, ?, ?)",
        (country, source_id, json.dumps(metadata)),
    )
    db.execute(
        """
        INSERT INTO media(country, id, name, domain, url, rank, selected, major, custom)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(country, id) DO UPDATE SET
            name = excluded.name,
            url = excluded.url,
            rank = excluded.rank
        """,
        (
            country,
            source_id,
            lead["name"],
            source["domain"],
            lead["website"],
            rank,
            int(selected),
            int(source["domain"] in MAJOR_DOMAINS),
        ),
    )
