# RAG — Market Sentiment News Index (FAISS)

Backs the market sentiment retriever agent. Built 2026-08-28.

**Corpus:** daily Google News RSS pulls, one search feed per key market
event topic in `config.NEWS_TOPICS` (Federal Reserve, US inflation, Trump
economy, Anthropic IPO, US stock market, SPX volatility — edit the list
freely). Articles land in `corpus/news/YYYY-MM-DD.jsonl`, deduped by link.

**Index:** FAISS via LangChain (`rag/index/`), embeddings computed locally
with fastembed (`BAAI/bge-small-en-v1.5`, ONNX — no API key, no torch).
Full rebuild per run; headlines need no chunking (add a LangChain text
splitter when long documents join the corpus).

## Daily routine

    python -m rag.update        # fetch all topics + rebuild index

(Or schedule it in Windows Task Scheduler.) Individual steps:
`python -m rag.news_fetcher`, `python -m rag.ingest`.

## Query

    python -m rag.retrieve "Fed rate decision impact on SPX"
    python -m agents.sentiment_retriever "US equity indices" "1 month"

`retrieve()` keeps only articles from the last 7 days by default (`days=`),
optionally filtered by topic. The sentiment agent currently returns a
topic-grouped digest of the most relevant recent headlines; LLM synthesis
activates once ANTHROPIC_API_KEY is set (wired at graph-integration time).
