"""Grounded short-news presentation helpers, without extra model calls."""

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit


def short_summary(text, limit=56):
    text = " ".join(text.split())
    tokens = text.split()
    if len(tokens) <= limit:
        return text

    prefix = " ".join(tokens[:limit])
    sentence_endings = list(re.finditer(r'[.!?](?:["\'])?(?:\s|$)', prefix))
    if sentence_endings and len(prefix[: sentence_endings[-1].end()].split()) >= 40:
        return prefix[: sentence_endings[-1].end()].strip()
    return prefix.rstrip(" ,;:") + "..."


def safe_image_url(value):
    if not isinstance(value, str):
        return ""

    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https":
            return ""
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            return ""

        from .media import domain_of

        domain = domain_of(value)
        if domain.endswith((".local", ".internal", ".localhost", ".test")):
            return ""
        return value
    except ValueError:
        return ""


class ImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag != "img":
            return
        values = dict(attrs)
        self.urls.append(values.get("src", ""))


def publisher_image(item):
    candidates = []
    for key in ("media_content", "media_thumbnail"):
        candidates.extend(
            candidate.get("url", "")
            for candidate in item.get(key, [])
            if candidate.get("medium", "image") == "image"
            and not candidate.get("type", "image/").startswith("video/")
        )

    candidates.extend(
        link.get("href", "")
        for link in item.get("links", [])
        if link.get("type", "").startswith("image/")
    )

    parser = ImageParser()
    parser.feed(item.get("summary", ""))
    candidates.extend(parser.urls)

    return next((url for candidate in candidates if (url := safe_image_url(candidate))), "")


def summary_is_usable(text, sources):
    word_count = len(text.split())
    evidence_words = sum(len(source.get("excerpt", "").split()) for source in sources)
    return 40 <= word_count <= 56 or (5 <= word_count < 40 and evidence_words < 40)
