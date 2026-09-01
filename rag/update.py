"""One-command daily RAG refresh: fetch today's news, rebuild the index.

Run daily (manually or via Windows Task Scheduler):
    python -m rag.update
"""

from rag.ingest import build_index
from rag.news_fetcher import fetch_all

if __name__ == "__main__":
    added, total = fetch_all()
    print(f"Fetched {total} articles; {added} new.")
    n = build_index()
    print(f"FAISS index rebuilt with {n} articles.")
