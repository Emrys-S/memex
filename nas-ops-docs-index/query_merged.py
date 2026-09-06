#!/usr/bin/env python3
"""
query.py
---------
Generalized semantic search across the whole second brain: the vault index
built by vault_indexer.py ("second_brain" collection -- Boox, Bluesky,
LinkedIn, People, Projects, Personal notes, nas-ops project docs) AND the
existing Zotero library index ("zotero_library", built separately by
zotero_indexer.py). Both collections share the same embedding model
(all-MiniLM-L6-v2), so their scores are directly comparable -- no migration
needed to search "everything that matches", just two collections queried
and merged.

Khoj-style source scoping (--source), Onyx-style citation discipline (every
result always prints its way back to the original note or paper -- no bare
summaries with the source implied). This is the "actual product-market-fit
gap" query tool called for in the 2026-08-06 architecture review.

Usage:
  python3 query.py "your question"
  python3 query.py "your question" --source zotero
  python3 query.py "your question" --source bluesky
  python3 query.py "your question" --zotero-collection "AI Governance"
  python3 query.py "your question" --synthesize
  python3 query.py --list-sources

Valid --source values: boox, bluesky, linkedin, people, meeting-notes,
personal, projects, inbox, nas-ops, other, zotero. Omit --source to search
everything at once.

Reconciled 2026-08-24: this file had forked into two divergent copies --
this one (used by second-brain-mcp, LibreChat's MCP tool) had gained
--zotero-collection scoping (2026-08-15, via the collection_names payload
field zotero_indexer.py writes) that the other (second-brain-index, the
CLI/ad-hoc path) never got; the other had gained an explicit Qdrant client
timeout (this NAS's qdrant-client default of ~5s was repeatedly timing out
real queries under normal load -- confirmed live, not hypothetical),
cached model/client singletons (so a long-running server like mcp_server.py
doesn't reload the embedding model on every single call), and the
"nas-ops" source tag. This version has all of it, deployed identically to
both locations so they can't silently diverge again.
"""

import argparse
import os

from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

QDRANT_HOST = os.environ.get("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
VAULT_COLLECTION = "second_brain"
ZOTERO_COLLECTION = "zotero_library"

VAULT_SOURCES = {"boox", "bluesky", "linkedin", "people", "meeting-notes", "personal", "projects", "inbox", "nas-ops", "other"}

# Cached singletons, not re-created per search() call -- added 2026-08-24
# for mcp_server.py, which stays running and calls search() many times over
# its lifetime. The CLI path (one search() call per process) doesn't care
# either way, so this is a pure win with no downside for that usage.
_model = None
_client = None


def get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def get_client():
    global _client
    if _client is None:
        # Explicit timeout -- qdrant-client's default (~5s) repeatedly timed
        # out real queries on this NAS under normal load (2-core Celeron,
        # often-elevated loadavg), confirmed live 2026-08-24 against a
        # small, healthy collection, not just under the Z-Library backfill
        # load. Same class of fix already applied to the indexer scripts.
        _client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=60)
    return _client


class Result:
    """Normalizes the two collections' differently-shaped payloads into one shape."""

    def __init__(self, score, collection, payload):
        self.score = score
        self.collection = collection
        self.payload = payload

    @property
    def title(self):
        return self.payload.get("title", "(untitled)")

    @property
    def source_label(self):
        return "zotero" if self.collection == ZOTERO_COLLECTION else self.payload.get("source", "other")

    @property
    def text(self):
        return self.payload.get("text", "")

    @property
    def obsidian_link(self):
        return self.payload.get("obsidian_link")  # None for Zotero items -- no note exists

    @property
    def external_link(self):
        if self.collection == ZOTERO_COLLECTION:
            return self.payload.get("zotero_link")
        return self.payload.get("external_link")


def search(query: str, top_k: int, source: str, zotero_collection: str = None):
    """zotero_collection scopes to one named Zotero collection (e.g. "AI
    Governance") via the collection_names payload field. Only meaningful
    for Zotero content -- setting it forces a zotero-only search regardless
    of `source`, since vault content has no such field.
    """
    model = get_model()
    client = get_client()
    vector = model.encode(query, show_progress_bar=False).tolist()

    results = []

    if zotero_collection:
        flt = Filter(must=[FieldCondition(key="collection_names", match=MatchValue(value=zotero_collection))])
        resp = client.query_points(collection_name=ZOTERO_COLLECTION, query=vector, limit=top_k, query_filter=flt)
        results = [Result(r.score, ZOTERO_COLLECTION, r.payload) for r in resp.points]
    elif source == "zotero":
        resp = client.query_points(collection_name=ZOTERO_COLLECTION, query=vector, limit=top_k)
        results = [Result(r.score, ZOTERO_COLLECTION, r.payload) for r in resp.points]
    elif source in VAULT_SOURCES:
        flt = Filter(must=[FieldCondition(key="source", match=MatchValue(value=source))])
        resp = client.query_points(collection_name=VAULT_COLLECTION, query=vector, limit=top_k, query_filter=flt)
        results = [Result(r.score, VAULT_COLLECTION, r.payload) for r in resp.points]
    else:
        vault_resp = client.query_points(collection_name=VAULT_COLLECTION, query=vector, limit=top_k)
        zotero_resp = client.query_points(collection_name=ZOTERO_COLLECTION, query=vector, limit=top_k)
        results = (
            [Result(r.score, VAULT_COLLECTION, r.payload) for r in vault_resp.points]
            + [Result(r.score, ZOTERO_COLLECTION, r.payload) for r in zotero_resp.points]
        )
        results.sort(key=lambda r: r.score, reverse=True)
        results = results[:top_k]

    return results


def print_results(results):
    if not results:
        print("No results found.")
        return
    for i, r in enumerate(results, start=1):
        print(f"\n{i}. {r.title}  (score: {r.score:.3f}, source: {r.source_label})")
        if r.obsidian_link:
            print(f"   Note:     {r.obsidian_link}")
        if r.external_link:
            print(f"   Source:   {r.external_link}")
        snippet = r.text[:300].replace("\n", " ")
        print(f"   {snippet}...")


def synthesize_answer(query: str, results: list) -> str:
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "(ANTHROPIC_API_KEY not set -- skipping synthesis, raw results only)"

    context = "\n\n".join(
        f"[{i+1}] {r.title} (source: {r.source_label})\n{r.text}" for i, r in enumerate(results)
    )
    prompt = (
        f"Based on the following excerpts from the user's second brain (personal notes, "
        f"captured articles, research library), answer this question: {query}\n\n"
        f"Cite sources by their [N] number. If the excerpts don't actually answer "
        f"the question, say so rather than guessing.\n\n{context}"
    )
    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def main():
    parser = argparse.ArgumentParser(description="Semantic search across your whole second brain")
    parser.add_argument("query", nargs="?", help="Your question or search text")
    parser.add_argument("-k", "--top-k", type=int, default=8, help="Number of results (default 8)")
    parser.add_argument(
        "--source", default=None,
        help="Scope to one source: " + ", ".join(sorted(VAULT_SOURCES | {"zotero"})) + " (default: search everything)",
    )
    parser.add_argument(
        "--zotero-collection", default=None,
        help="Scope to one named Zotero collection (e.g. 'AI Governance'). Forces zotero-only search.",
    )
    parser.add_argument("--synthesize", action="store_true", help="Also generate a Claude-synthesized answer")
    parser.add_argument("--list-sources", action="store_true", help="List valid --source values and exit")
    args = parser.parse_args()

    if args.list_sources:
        for s in sorted(VAULT_SOURCES | {"zotero"}):
            print(s)
        return

    if not args.query:
        parser.error("query is required unless --list-sources is given")

    results = search(args.query, args.top_k, args.source, args.zotero_collection)
    print_results(results)

    if args.synthesize:
        print("\n" + "─" * 55)
        print("Synthesized answer:\n")
        print(synthesize_answer(args.query, results))


if __name__ == "__main__":
    main()
