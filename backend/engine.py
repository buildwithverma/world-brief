import asyncio
import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import CACHE_SECONDS, DEFAULT_ARTICLE_COUNT, REFRESH_SECONDS
from .groq import Groq, credentials
from .media import country_name, collect_country
from .shorts import short_summary, summary_is_usable
from .store import digest
from .summary_queue import SummaryQueue

TOPICS = ["All", "World", "Technology", "Business", "Science", "Climate"]

STOP = set(
    """
    the a an is are was were of to in on for with and or as at by from after over
    this that it its news latest top world today stories give me about what happened
    biggest only please show tell headlines brief briefing update updates new s
    happening going around across recent more story ones those instead changed my
    country local international global two minute minutes five ten
    """.split()
)


def words(text):
    return set(re.findall(r"[a-z0-9]+", text.lower())) - STOP


def normalized(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def fallback_intent(query, filters, previous=None):
    scope = dict(filters)
    q = query.lower()
    scope.update(count=filters.get("count", DEFAULT_ARTICLE_COUNT), terms=[])

    if previous and re.search(r"\b(only|those|ones|instead)\b", q):
        inherited = {k: v for k, v in previous.items() if k in ("topic", "period", "count", "terms")}
        scope.update(inherited)

    for topic in TOPICS[1:]:
        if topic.lower() in q or (topic == "Technology" and re.search(r"\b(tech|ai)\b", q)):
            scope["topic"] = topic

    if "yesterday" in q:
        scope["period"] = "yesterday"
    elif "week" in q:
        scope["period"] = "week"
    elif "today" in q:
        scope["period"] = "today"

    numeric_count = re.search(r"\b(\d+)\s+(?:top\s+)?(?:stories|headlines|news items|articles)\b", q)
    if numeric_count:
        scope["count"] = min(100, max(1, int(numeric_count[1])))

    for name, value in [("five", 5), ("ten", 10), ("three", 3)]:
        if re.search(r"\b" + name + r"\b", q):
            scope["count"] = value

    structural_text = " ".join(TOPICS)
    structural_text += " articles since yesterday week hours hour all one two three four five ten"
    structural_text += " minute tech " + country_name(scope["country"])
    structural = words(structural_text)
    scope["terms"] = sorted(words(q) - structural - set(re.findall(r"\d+", q)))

    if scope.get("topic") not in TOPICS:
        scope["topic"] = "All"
    if scope.get("period") not in ("day", "today", "yesterday", "week"):
        scope["period"] = "day"
    if not isinstance(scope.get("count"), int):
        scope["count"] = DEFAULT_ARTICLE_COUNT
    if not isinstance(scope.get("terms"), list):
        scope["terms"] = []

    scope["count"] = max(1, min(100, scope["count"]))
    scope["terms"] = [str(term).lower()[:60] for term in scope["terms"][:8]]
    return scope


def resolve_window(scope):
    try:
        zone = ZoneInfo(scope.get("timezone", "UTC"))
    except Exception:
        zone = ZoneInfo("UTC")

    now = datetime.now(zone)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    period = scope.get("period", "day")

    if period == "today":
        start, end = midnight.timestamp(), now.timestamp()
    elif period == "yesterday":
        start, end = (midnight - timedelta(days=1)).timestamp(), midnight.timestamp()
    else:
        days = 7 if period == "week" else 1
        start, end = now.timestamp() - days * 86400, now.timestamp()

    scope["date"] = now.date().isoformat()
    scope["window_bucket"] = int(time.time() // CACHE_SECONDS)
    return start, end


def cluster(articles):
    groups = []
    for article in articles:
        tokens = words(article["title"])
        match = None

        for group in groups:
            lead = group[0]
            other = words(lead["title"])
            common = tokens & other
            overlap = len(common) / max(1, len(tokens | other))
            close_in_time = abs(article["published"] - lead["published"]) < 36 * 3600

            if len(common) < 4 or overlap < 0.43 or not close_in_time:
                continue

            numbers = set(re.findall(r"\b\d+\b", article["title"]))
            other_numbers = set(re.findall(r"\b\d+\b", lead["title"]))
            if numbers and other_numbers and numbers != other_numbers:
                continue

            match = group
            break

        if match is None:
            groups.append([article])
        else:
            match.append(article)

    return groups


def coverage(group):
    outlets = {article["source_id"]: article for article in group}
    major = [
        {"id": source_id, "name": article["publisher"], "domain": article["domain"]}
        for source_id, article in outlets.items()
        if article["major"]
    ]
    now = time.time()
    return {
        "status": "outlet_coverage",
        "major_count": len(major),
        "total_count": len(outlets),
        "major_outlets": major,
        "checked_at": now,
        "expires_at": now + CACHE_SECONDS,
        "explanation": (
            "Distinct outlets in your selected media list reporting a matching story. "
            "Matching is based on headline similarity and publication time. Multiple "
            "outlets may share syndicated reporting; this count is not a true/false verdict."
        ),
    }


class Engine:
    def __init__(self, store, vector):
        self.store = store
        self.vector = vector
        self.groq = Groq(store)
        self.summaries = SummaryQueue(store, self.groq)
        self.fetch_locks = {}
        self.refreshing = set()

    def config_signature(self):
        key, model = credentials()
        return "country-v6-topic-retries:" + model + ":" + digest(key)[:12]

    def selected_signature(self, country):
        rows = self.store.rows(
            "SELECT id FROM media WHERE country=? AND selected=1 ORDER BY id",
            (country,),
        )
        ids = [row["id"] for row in rows]
        return digest("|".join(ids)), ids

    async def collect(self, country, force=False, query="", topic="All"):
        with self.store.lease("worldbrief:collect:" + country) as held:
            if held:
                return await self._collect_locked(country, force, query, topic)

    async def _collect_locked(self, country, force=False, query="", topic="All"):
        lock = self.fetch_locks.setdefault(country, asyncio.Lock())
        async with lock:
            last_attempt = float(self.store.meta("fetch_attempt:" + country, "0"))
            source_signature, _ = self.selected_signature(country)
            already_current = self.store.meta("collected_sources:" + country) == source_signature
            cooldown = 10 if force else REFRESH_SECONDS

            if not query and already_current and time.time() - last_attempt < cooldown:
                return None

            self.refreshing.add(country)
            try:
                result = await collect_country(self.store, country, query, topic)
                self.store.set_meta("collected_sources:" + country, source_signature)
                return result
            finally:
                self.refreshing.discard(country)

    async def query(self, request):
        if request.get("phase") in ("local", "expanded"):
            return await self.search_progressive(request)
        from .catalog import import_catalog

        country = request["country"]
        query_text = request.get("query", "").strip()
        import_catalog(self.store, country)

        filters = {key: request[key] for key in ("topic", "country", "period", "timezone")}
        filters["count"] = request.get("count", DEFAULT_ARTICLE_COUNT)

        source_signature, selected_ids = self.selected_signature(country)
        key_scope = dict(filters)
        resolve_window(key_scope)
        exact_key = self._exact_cache_key(query_text, key_scope, request, source_signature)

        cached = self._read_exact_cache(exact_key, request)
        if cached:
            return cached

        await self._collect_if_needed(country, request)
        scope = await self._resolve_intent(query_text, filters, request)
        await self._collect_for_subject_search(country, scope)

        start, end = resolve_window(scope)
        source_signature, selected_ids = self.selected_signature(country)
        scope.update(sources=source_signature, config=self.config_signature())
        snapshot = self.store.meta("revision")

        semantic = await self._read_semantic_cache(query_text, scope, request, snapshot)
        if semantic:
            return semantic

        articles = self._candidate_articles(country, start, end, scope)
        stories = await self._build_stories(country, source_signature, scope, articles)

        result = self._result(query_text, scope, country, articles, stories, selected_ids)
        if stories and snapshot == self.store.meta("revision"):
            await self._cache_result(exact_key, query_text, scope, result, snapshot)

        return self.decorate(result, "fresh", retry_failed=bool(request.get("refresh")))

    async def search_progressive(self, request):
        from .catalog import import_catalog
        from .retrieval import relevant_candidates, query_subject, region_terms
        country = request["country"]
        query = request.get("query", "").strip()
        import_catalog(self.store, country)
        filters = {key: request[key] for key in ("topic", "country", "period", "timezone")}
        filters["count"] = request.get("count", DEFAULT_ARTICLE_COUNT)
        scope = fallback_intent(query, filters)
        # UI filters stay authoritative while typing; no remote intent call blocks search.
        scope["topic"] = filters["topic"]
        regions = region_terms(query, country)
        if regions:
            # Preserve multiword places in the external search instead of OR-like token matches.
            scope["terms"] = ['"' + aliases[0] + '"' for aliases in regions] + [t for t in scope['terms'] if t not in set(' '.join(a[0] for a in regions).split())]
        expanded = request["phase"] == "expanded"
        warning = None
        if expanded:
            try:
                async with asyncio.timeout(35):
                    if query:
                        await self._collect_for_subject_search(country, scope)
                    elif request.get("refresh") or not self.store.rows(
                        "SELECT 1 FROM country_articles WHERE country=? LIMIT 1", (country,)
                    ):
                        # The public landing feed is served from the scheduled
                        # collector's cache. Only collect here for a manual
                        # refresh or a country that has no stored coverage.
                        await self.collect(country, force=bool(request.get("refresh")))
            except Exception:
                warning = "Fresh coverage is unavailable; showing matching stored articles."
        start, end = resolve_window(scope)
        signature, ids = self.selected_signature(country)
        method = "newest"
        articles = []
        backfilled = False
        if query:
            # Search recent coverage first; only widen when distinct relevant groups are short.
            method = "bm25"
            windows = [(start, end)]
            if scope["period"] in ("day", "today"):
                floor = end - 7 * 86400
                cursor = start
                while cursor > floor:
                    older = max(floor, cursor - 86400)
                    windows.append((older, cursor - .000001))
                    cursor = older
            for index, (lower, upper) in enumerate(windows):
                candidates = self._candidate_articles(country, lower, upper, {**scope, "terms": []})
                candidates = relevant_candidates(candidates, query, country, limit=max(150, scope["count"] * 3))
                if expanded and hasattr(self.vector, "rerank") and candidates:
                    try:
                        candidates, method = await asyncio.to_thread(self.vector.rerank, query_subject(query), candidates)
                        if method == "bm25+embeddings_partial":
                            warning = "Showing semantically checked matches. Some new articles are still being indexed; retry shortly."
                    except Exception:
                        warning = "Embedding ranking is unavailable; showing location-constrained BM25 matches."
                # Never promote old coverage above a newer relevant day's reporting.
                candidates.sort(key=lambda a: a["published"], reverse=True)
                articles.extend(candidates)
                if index and candidates:
                    backfilled = True
                if len(cluster(articles)) >= scope["count"]:
                    break
        else:
            articles = self._candidate_articles(country, start, end, {**scope, "terms": []})
            articles.sort(key=lambda a: a["published"], reverse=True)
        stories = await self._build_stories(country, signature, scope, articles, immediate=True)
        result = self._result(query, scope, country, articles, stories, ids)
        result.update(retrieval=method, phase=request["phase"], notice=warning, backfilled=backfilled, search_window_days=7 if backfilled else None)
        return self.decorate(result, "fresh" if expanded else "local", retry_failed=bool(request.get("refresh")))

    def _exact_cache_key(self, query_text, key_scope, request, source_signature):
        payload = {
            "query": normalized(query_text),
            "filters": key_scope,
            "previous": request.get("previous"),
            "config": self.config_signature(),
            "sources": source_signature,
        }
        return digest(json.dumps(payload, sort_keys=True))

    def _read_exact_cache(self, exact_key, request):
        if request.get("refresh"):
            return None
        cached = self.store.exact(exact_key, self.store.meta("revision"))
        if not cached:
            return None
        return self.decorate(json.loads(cached["data"]), "exact")

    async def _collect_if_needed(self, country, request):
        if request.get("refresh") or not self.store.meta("last_fetch:" + country):
            await self.collect(country, force=bool(request.get("refresh")))

    async def _resolve_intent(self, query_text, filters, request):
        scope = fallback_intent(query_text, filters, request.get("previous"))
        if not query_text or not credentials()[0]:
            return scope

        intent = await self.groq.json(
            (
                'Interpret a news request. Return {"topic":"All|World|Technology|Business|Science|Climate",'
                '"period":"day|today|yesterday|week","count":integer 1-100,"terms":[specific subject keywords]}. '
                "Country is explicitly chosen in the interface and must not be changed. Keep the supplied count "
                "unless the user explicitly requests a different number of stories. Broad briefings have empty "
                "terms. A reading duration is not a story count."
            ),
            {"query": query_text, "filters": filters, "previous": request.get("previous")},
            450,
        )
        if not isinstance(intent, dict):
            return scope

        if intent.get("topic") in TOPICS:
            scope["topic"] = intent["topic"]
        if intent.get("period") in ("day", "today", "yesterday", "week"):
            scope["period"] = intent["period"]
        if isinstance(intent.get("count"), int):
            scope["count"] = max(1, min(100, intent["count"]))
        if isinstance(intent.get("terms"), list):
            scope["terms"] = [str(term).lower()[:60] for term in intent["terms"][:8]]
        return scope

    async def _collect_for_subject_search(self, country, scope):
        if not scope["terms"]:
            return

        search_key = "search:" + digest(country + scope["topic"] + " ".join(scope["terms"]))
        if time.time() - float(self.store.meta(search_key, "0")) <= CACHE_SECONDS:
            return

        await self.collect(country, query=" ".join(scope["terms"]), topic=scope["topic"])
        self.store.set_meta(search_key, time.time())

    async def _read_semantic_cache(self, query_text, scope, request, snapshot):
        if not query_text or request.get("refresh"):
            return None

        for ident in await asyncio.to_thread(self.vector.search, query_text):
            rows = self.store.rows(
                "SELECT * FROM query_cache WHERE id=? AND expires>? AND revision=?",
                (ident, time.time(), snapshot),
            )
            if rows and json.loads(rows[0]["scope"]) == scope:
                return self.decorate(json.loads(rows[0]["data"]), "semantic")
        return None

    def _candidate_articles(self, country, start, end, scope):
        # The landing page only needs a modest recent pool to form its requested
        # stories. Avoid clustering every retained article on a constrained
        # hosted instance; searches retain the larger shortlist for recall.
        limit = 1400 if scope.get("terms") else max(250, scope.get("count", DEFAULT_ARTICLE_COUNT) * 5)
        articles = self.store.rows(
            """
            SELECT a.*, c.domestic, c.topic AS country_topic, m.id AS source_id, m.major, m.domain
            FROM articles a
            JOIN country_articles c ON c.article_id = a.id
            JOIN media m ON m.id = c.source_id AND m.country = c.country
            WHERE c.country = ? AND m.selected = 1 AND a.published >= ? AND a.published <= ?
            ORDER BY a.published DESC
            LIMIT ?
            """,
            (country, start, end, limit),
        )
        images = {
            row["article_id"]: row["url"]
            for row in self.store.rows(
                """
                SELECT i.*
                FROM article_images i
                JOIN country_articles c ON c.article_id = i.article_id
                WHERE c.country = ?
                """,
                (country,),
            )
        }

        for article in articles:
            article["topic"] = article["country_topic"]
            article["image_url"] = images.get(article["id"], "")

        if scope["topic"] != "All":
            articles = [article for article in articles if article["topic"] == scope["topic"]]
        if scope["terms"]:
            articles = [
                article
                for article in articles
                if any(term in (article["title"] + " " + article["excerpt"]).lower() for term in scope["terms"])
            ]
        return articles

    async def _build_stories(self, country, source_signature, scope, articles, immediate=False):
        priorities = self._source_priorities(country)
        groups = cluster(articles)
        if immediate:
            groups = groups[:scope["count"]]
        else:
            groups = self._select_groups(groups, scope["count"], priorities)

        stories = []
        missing = []
        for group in groups:
            story, version, needs_summary = self._story_from_group(country, source_signature, group)
            stories.append(story)
            if needs_summary:
                missing.append((story, version))

        if not immediate:
            await self._summarize_initial_batches(missing)
        self._write_missing_stories(missing)
        return stories

    def _source_priorities(self, country):
        rows = self.store.rows("SELECT id, metadata FROM source_catalog WHERE country=?", (country,))
        return {row["id"]: json.loads(row["metadata"])["priority_score"] for row in rows}

    def _select_groups(self, groups, count, priorities):
        selected = []
        publishers = Counter()
        topics = Counter()

        def score(group):
            age_hours = max(0, time.time() - group[0]["published"]) / 3600
            priority_bonus = max(priorities.get(article["source_id"], 0) for article in group) / 25
            outlet_bonus = min(8, len({article["source_id"] for article in group})) * 2
            return (
                20 * max(article["domestic"] for article in group)
                + 12 / (1 + age_hours / 8)
                + outlet_bonus
                + priority_bonus
            )

        while groups and len(selected) < count:
            best = max(
                groups,
                key=lambda group: score(group)
                - publishers[group[0]["source_id"]] * 3
                - topics[group[0]["topic"]] * 0.6,
            )
            groups.remove(best)
            selected.append(best)
            publishers[best[0]["source_id"]] += 1
            topics[best[0]["topic"]] += 1

        return selected

    def _story_from_group(self, country, source_signature, group):
        group = sorted(group, key=lambda article: bool(article["excerpt"]), reverse=True)
        lead = group[0]
        story_id = digest(country + "|" + source_signature + "|" + lead["id"])
        version = digest(
            "|".join(
                sorted(
                    article["content_hash"] + article["source_id"] + article.get("image_url", "")
                    for article in group
                )
            )
            + self.config_signature()
        )

        cached = self.store.rows(
            "SELECT data FROM story_cache WHERE id=? AND version=? AND expires>?",
            (story_id, version, time.time()),
        )
        if cached:
            return json.loads(cached[0]["data"]), version, False

        sources = [
            {
                "id": article["id"],
                "url": article["url"],
                "publisher": article["publisher"],
                "domain": article["domain"],
                "major": bool(article["major"]),
                "title": article["title"],
                "excerpt": article["excerpt"],
                "link_kind": "news_index" if "news.google.com/" in article["url"] else "original",
                "published_at": article["published"],
            }
            for article in group
        ]
        image_urls = list(dict.fromkeys(article["image_url"] for article in group if article.get("image_url")))
        summary = short_summary(lead["excerpt"]) or (
            "Reported by "
            + ", ".join(dict.fromkeys(article["publisher"] for article in group))
            + ". Publisher excerpt unavailable; open the reporting for details."
        )
        story = {
            "id": story_id,
            "title": lead["title"],
            "image_url": image_urls[0] if image_urls else None,
            "image_urls": image_urls,
            "summary": summary,
            "summary_kind": "publisher_excerpt" if lead["excerpt"] else "headline",
            "topic": lead["topic"],
            "country": country,
            "country_name": country_name(country),
            "local_priority": bool(max(article["domestic"] for article in group)),
            "published_at": max(article["published"] for article in group),
            "sources": sources,
            "coverage_count": len({article["source_id"] for article in group}),
            "verification": coverage(group),
            "saved": False,
        }
        return story, version, True

    async def _summarize_initial_batches(self, missing):
        if not missing or not credentials()[0]:
            return

        deadline = time.monotonic() + 20
        for offset in range(0, len(missing), 5):
            batch = missing[offset : offset + 5]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            payload = [
                {
                    "id": story["id"],
                    "reporting": [
                        {
                            "id": source["id"],
                            "publisher": source["publisher"],
                            "title": source["title"],
                            "excerpt": source.get("excerpt", "")[:700],
                        }
                        for source in story["sources"][:3]
                    ],
                }
                for story, _version in batch
            ]

            try:
                result = await asyncio.wait_for(
                    self.groq.json(self._summary_prompt(), payload, 1600),
                    timeout=remaining,
                )
            except TimeoutError:
                break

            self._apply_initial_summaries(batch, payload, result)

    @staticmethod
    def _summary_prompt():
        return (
            "Write a clear news brief of 40-56 words per story, strictly from the supplied headlines "
            "and publisher excerpts. Explain what happened and key details only when present. Include "
            "who, what, where and when only when supplied. Use fewer than 40 words only when the evidence "
            "cannot support the target without repetition or invention. Do not repeat the headline or pad "
            "with commentary about the article. Never say the article provides context or implications unless "
            "those facts are explicitly supplied. Do not invent context, causes, numbers or conclusions. "
            'Preserve attribution and uncertainty. Return {"stories":[{"id":string,"summary":string,'
            '"source_ids":[string]}]}. Every summary needs a supplied source ID from that story.'
        )

    def _apply_initial_summaries(self, batch, payload, result):
        if not isinstance(result, dict) or not isinstance(result.get("stories"), list):
            return

        by_id = {story["id"]: story for story, _version in batch}
        supplied = {item["id"]: {source["id"] for source in item["reporting"]} for item in payload}

        for item in result["stories"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue

            story = by_id.get(item["id"])
            refs = item.get("source_ids")
            summary = short_summary(item.get("summary", "")) if isinstance(item.get("summary"), str) else ""
            valid_refs = (
                isinstance(refs, list)
                and refs
                and all(isinstance(ref, str) and ref in supplied[item["id"]] for ref in refs)
            )
            if story and summary_is_usable(summary, story["sources"][:3]) and valid_refs:
                story.update(
                    summary=summary,
                    summary_kind="groq_summary",
                    summary_limited=len(summary.split()) < 40,
                )

    def _write_missing_stories(self, missing):
        for story, version in missing:
            with self.store.db() as db:
                db.execute(
                    """
                    INSERT INTO story_cache VALUES(?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        version = excluded.version,
                        data = excluded.data,
                        expires = excluded.expires
                    WHERE story_cache.version != excluded.version
                       OR json_extract(story_cache.data, '$.summary_kind') != 'groq_summary'
                       OR json_extract(excluded.data, '$.summary_kind') = 'groq_summary'
                    """,
                    (story["id"], version, json.dumps(story), time.time() + CACHE_SECONDS),
                )

    def _result(self, query_text, scope, country, articles, stories, selected_ids):
        notice = None
        if not credentials()[0]:
            notice = "Add your Groq key in Settings for AI summaries. You can still search and read source headlines."
        elif self.groq.status not in ("ready", "configured"):
            notice = (
                "Groq "
                + self.groq.status.replace("_", " ")
                + ". Source headlines are available; try again later or check your connection in Settings."
            )

        return {
            "stories": stories,
            "query": query_text,
            "intent": scope,
            "country": country,
            "country_name": country_name(country),
            "generated_at": time.time(),
            "source_updated_at": float(self.store.meta("last_fetch:" + country, "0")),
            "cache": "fresh",
            "briefing": None,
            "matched_articles": len(articles),
            "notice": notice,
            "selected_sources": len(selected_ids),
            "requested_count": scope["count"],
        }

    async def _cache_result(self, exact_key, query_text, scope, result, snapshot):
        ident = self.store.put_query(exact_key, query_text, scope, result, CACHE_SECONDS, snapshot)
        if query_text:
            await asyncio.to_thread(self.vector.put, ident, query_text)

    def decorate(self, result, cache, retry_failed=False):
        self.summaries.enqueue(result["stories"], retry_failed=retry_failed)
        updates = self.summaries.updates([story["id"] for story in result["stories"]])
        current = {story["id"]: story for story in updates["stories"]}

        result["stories"] = [current.get(story["id"], story) for story in result["stories"]]
        result["summary_pending"] = updates["pending"]
        result["summary_failed"] = updates["failed"]

        saved = {row["id"] for row in self.store.rows("SELECT id FROM saved")}
        for story in result["stories"]:
            story["saved"] = story["id"] in saved

        result["cache"] = cache
        result["stale"] = time.time() - result["source_updated_at"] > REFRESH_SECONDS * 2
        return result
