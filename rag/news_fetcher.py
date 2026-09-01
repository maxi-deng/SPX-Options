"""Daily Google News RSS puller for the sentiment RAG corpus.

For every topic in config.NEWS_TOPICS, fetches the Google News search feed
and appends new articles to rag/corpus/news/YYYY-MM-DD.jsonl (one JSON per
line: title, source, published, link, topic). Re-running on the same day is
safe — articles are deduped by link.

Run daily:  python -m rag.news_fetcher   (or `python -m rag.update` to also
rebuild the FAISS index in the same step)
"""

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime

from config import NEWS_DIR, NEWS_TOPICS

FEED_URL = ("https://news.google.com/rss/search?"
            "q={query}&hl=en-US&gl=US&ceid=US:en")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def fetch_topic(topic: str) -> list[dict]:
    url = FEED_URL.format(query=urllib.parse.quote(topic))
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        root = ET.fromstring(resp.read())
    articles = []
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub = item.findtext("pubDate") or ""
        source = item.findtext("source") or ""
        try:
            published = parsedate_to_datetime(pub).date().isoformat()
        except (TypeError, ValueError):
            published = ""
        if title and link:
            articles.append({"title": title.strip(), "source": source.strip(),
                             "published": published, "link": link,
                             "topic": topic})
    return articles


def fetch_all() -> tuple[int, int]:
    """Fetch every topic; returns (new_articles, total_fetched)."""
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = NEWS_DIR / f"{date.today().isoformat()}.jsonl"
    seen: set[str] = set()
    if out_file.exists():
        for line in out_file.read_text(encoding="utf-8").splitlines():
            seen.add(json.loads(line)["link"])

    fetched = 0
    new: list[dict] = []
    for topic in NEWS_TOPICS:
        for art in fetch_topic(topic):
            fetched += 1
            if art["link"] not in seen:
                seen.add(art["link"])
                new.append(art)

    if new:
        with out_file.open("a", encoding="utf-8") as f:
            for art in new:
                f.write(json.dumps(art, ensure_ascii=False) + "\n")
    return len(new), fetched


if __name__ == "__main__":
    added, total = fetch_all()
    print(f"Fetched {total} articles across {len(NEWS_TOPICS)} topics; "
          f"{added} new stored in {NEWS_DIR}")
