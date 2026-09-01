"""Build the FAISS index from the news corpus.

Reads every rag/corpus/news/*.jsonl file, dedupes by link, embeds each
headline locally (fastembed, no API key) and writes the FAISS index to
rag/index/. Full rebuild each run — cheap at headline scale, and it means
deleting a corpus file cleanly removes its articles from the index.

Headlines are short, so no chunking is needed; when long documents (PDFs,
reports) join the corpus later, add a LangChain text splitter here.

Run after fetching:  python -m rag.ingest
"""

import json

from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from config import EMBED_MODEL, FAISS_INDEX_DIR, NEWS_DIR


def load_corpus() -> list[Document]:
    docs: list[Document] = []
    seen: set[str] = set()
    for f in sorted(NEWS_DIR.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            art = json.loads(line)
            if art["link"] in seen:
                continue
            seen.add(art["link"])
            docs.append(Document(
                page_content=art["title"],
                metadata={"topic": art["topic"], "source": art["source"],
                          "published": art["published"], "link": art["link"]},
            ))
    return docs


def get_embeddings() -> FastEmbedEmbeddings:
    return FastEmbedEmbeddings(model_name=EMBED_MODEL)


def build_index() -> int:
    docs = load_corpus()
    if not docs:
        raise SystemExit("Corpus is empty — run `python -m rag.news_fetcher` first.")
    store = FAISS.from_documents(docs, get_embeddings())
    FAISS_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    store.save_local(str(FAISS_INDEX_DIR))
    return len(docs)


if __name__ == "__main__":
    n = build_index()
    print(f"FAISS index rebuilt with {n} articles -> {FAISS_INDEX_DIR}")
