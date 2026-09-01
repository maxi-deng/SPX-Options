"""Entry point: run one user query through the graph.

Usage:  python main.py "I want to trade a call spread at least 2% away from
        market with minimum $10/month premium, best theta/delta ratio"
"""

import sys

from dotenv import load_dotenv

from graph.build_graph import build_graph


def main() -> None:
    load_dotenv()
    query = " ".join(sys.argv[1:]) or input("Query: ")
    graph = build_graph()
    result = graph.invoke({"user_query": query})
    print(result["final_answer"])


if __name__ == "__main__":
    main()
