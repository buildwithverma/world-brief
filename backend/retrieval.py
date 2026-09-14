"""Local BM25 candidate retrieval; embeddings rerank only a bounded shortlist."""
import math
import re
from collections import Counter


def tokens(text):
    return re.findall(r"[^\W_]+", text.casefold())


def bm25(articles, query, limit=150):
    terms = set(tokens(query))
    if not terms or not articles:
        return []
    documents = [tokens(a['title'] + ' ' + a.get('excerpt', '')) for a in articles]
    frequencies = Counter(t for doc in documents for t in set(doc))
    average = sum(map(len, documents)) / len(documents) or 1
    ranked = []
    for article, doc in zip(articles, documents):
        counts = Counter(doc)
        score = 0.0
        for term in terms:
            tf = counts[term]
            if tf:
                idf = math.log(1 + (len(documents) - frequencies[term] + .5) / (frequencies[term] + .5))
                score += idf * tf * 2.5 / (tf + 1.5 * (.25 + .75 * len(doc) / average))
        if score:
            ranked.append({**article, 'retrieval_score': score})
    return sorted(ranked, key=lambda a: (-a['retrieval_score'], -a['published']))[:limit]


QUERY_FILLER = {'news', 'latest', 'recent', 'today', 'stories', 'headlines', 'show', 'me', 'the', 'about', 'in', 'of', 'for', 'please'}
REGION_ALIASES = {
    'uttar pradesh': ('lucknow', 'kanpur', 'noida', 'agra', 'varanasi', 'prayagraj', 'ghaziabad', 'gorakhpur', 'meerut', 'ayodhya', 'bareilly', 'aligarh', 'mathura', 'jhansi', 'saharanpur', 'moradabad'),
}


def query_subject(query):
    return ' '.join(t for t in tokens(query) if t not in QUERY_FILLER)


def region_terms(query, country='IN'):
    import pycountry
    text = ' ' + ' '.join(tokens(query)) + ' '
    names = [s.name for s in pycountry.subdivisions.get(country_code=country) or []]
    regions = [name.casefold() for name in names if ' ' + ' '.join(tokens(name)) + ' ' in text]
    return [(name, *REGION_ALIASES.get(name, ())) for name in regions]


def matches_region(article, regions):
    text = ' ' + ' '.join(tokens(article['title'] + ' ' + article.get('excerpt', ''))) + ' '
    return all(any(' ' + ' '.join(tokens(alias)) + ' ' in text for alias in aliases) for aliases in regions)


def relevant_candidates(articles, query, country='IN', limit=150):
    regions = region_terms(query, country)
    eligible = [a for a in articles if matches_region(a, regions)]
    subject = query_subject(query)
    # Include known cities when the query names their state; embeddings check meaning next.
    expanded = subject + ' ' + ' '.join(alias for aliases in regions for alias in aliases[1:])
    return bm25(eligible, expanded, limit=limit)
