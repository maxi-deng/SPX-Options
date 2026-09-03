"""One-command daily RAG refresh: fetch today's news, rebuild the index,
refresh the econ-release calendar.

Run daily (manually or via Windows Task Scheduler):
    python -m rag.update
"""

from rag.econ_calendar import refresh as refresh_calendar
from rag.ingest import build_index
from rag.news_fetcher import fetch_all

if __name__ == "__main__":
    added, total = fetch_all()
    print(f"Fetched {total} articles; {added} new.")
    n = build_index()
    print(f"FAISS index rebuilt with {n} articles.")
    events = refresh_calendar()
    print(f"Econ calendar refreshed: {len(events)} events.")
