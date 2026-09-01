"""Query the FAISS news index.

retrieve() embeds the query, pulls the most similar headlines, and (by
default) keeps only recent ones — sentiment should be driven by fresh news.
"""

from datetime import date, timedelta

from langchain_community.vectorstores import FAISS

from config import FAISS_INDEX_DIR
from rag.ingest import get_embeddings


def retrieve(query: str, k: int = 12, days: int | None = 7,
             topic: str | None = None, as_of: str | None = None) -> list[dict]:
    """Top-k relevant articles as dicts (title/topic/source/published/link).

    as_of ("YYYY-MM-DD") reconstructs a past point in time: only articles
    published on or before that date count, and the recency window is
    measured back from it instead of from today.
    """
    if not (FAISS_INDEX_DIR / "index.faiss").exists():
        raise SystemExit("No FAISS index — run `python -m rag.update` first.")
    store = FAISS.load_local(str(FAISS_INDEX_DIR), get_embeddings(),
                             allow_dangerous_deserialization=True)
    ref = date.fromisoformat(as_of) if as_of else date.today()
    cutoff = (ref - timedelta(days=days)).isoformat() if days else ""

    def keep(meta: dict) -> bool:
        if topic and meta.get("topic") != topic:
            return False
        pub = meta.get("published") or ""
        if as_of and pub > as_of:
            return False  # article is from the future relative to as_of
        return not cutoff or pub >= cutoff

    hits = store.similarity_search_with_score(query, k=k * 4)
    out = []
    for doc, score in hits:
        if not keep(doc.metadata):
            continue
        out.append({"title": doc.page_content, **doc.metadata,
                    "score": round(float(score), 4)})
        if len(out) >= k:
            break
    return out


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Fed policy impact on US stock market"
    for hit in retrieve(q):
        print(f"[{hit['topic']}] ({hit['published']}, {hit['source']}) "
              f"{hit['title']}  score={hit['score']}")
