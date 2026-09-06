#!/usr/bin/env python3
"""
mcp_server.py
--------------
Wraps query.py's existing search logic as a standing MCP server, so any
MCP-aware client (Claude Desktop, a Claude Code session, LibreChat once it's
the daily driver) can call "search my second brain" directly as a tool,
instead of the friction this project has been living with: every query
tonight went through SSH + a cold `docker run`, ~1-2 minutes just to reload
the embedding model each time, no persistent session, and nothing a client
could call on its own.

This was already the planned shape for query.py -- CLAUDE.md's own Zotero
section noted an MCP surface was "deliberately deferred... the query logic
won't need to change when that happens, just get an MCP shell wrapped
around it later." That's exactly what this is: query.py's search()/Result
logic is imported unchanged, not reimplemented. The only change made to
query.py itself was caching the embedding model and Qdrant client as
module-level singletons (get_model()/get_client()) instead of constructing
them fresh per call -- irrelevant to the CLI (one call per process either
way) but the whole point for this server, which stays running and serves
many queries over its lifetime.

Runs over streamable-http (not stdio) so it's reachable as a normal network
service -- same pattern as this NAS's other Tailscale-reachable services
(Qdrant itself, Goose). Deliberately no auth: reachable only over Tailscale,
matching the existing precedent (Qdrant has none either), not a new
decision made here.

Environment variables (all shared with query.py, same NAS network):
  QDRANT_HOST   default qdrant (Docker-internal hostname)
  QDRANT_PORT   default 6333
  MCP_PORT      default 8420
"""

import os

from mcp.server.fastmcp import FastMCP

from query import search, VAULT_SOURCES, get_model, get_client

MCP_PORT = int(os.environ.get("MCP_PORT", "8420"))

mcp = FastMCP("second-brain", port=MCP_PORT, host="0.0.0.0")


def _format_results(results) -> str:
    if not results:
        return "No results found."
    lines = []
    for i, r in enumerate(results, start=1):
        lines.append(f"{i}. {r.title}  (score: {r.score:.3f}, source: {r.source_label})")
        if r.obsidian_link:
            lines.append(f"   Note:   {r.obsidian_link}")
        if r.external_link:
            lines.append(f"   Source: {r.external_link}")
        snippet = r.text[:500].replace("\n", " ")
        lines.append(f"   {snippet}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def search_second_brain(query: str, source: str = "", top_k: int = 8) -> str:
    """Semantic search across the user's second brain: personal notes (Boox
    handwritten captures, LinkedIn/Bluesky saves), and their Zotero research
    library. Every result cites back to its original note or paper -- never
    presents a summary without saying where it came from.

    Args:
        query: The question or topic to search for.
        source: Optional. Scope to one source instead of searching
            everything. Valid values: boox, bluesky, linkedin, people,
            meeting-notes, personal, projects, inbox, nas-ops, other, zotero.
            Leave empty to search everything.
        top_k: Number of results to return (default 8).
    """
    results = search(query, top_k, source or None)
    return _format_results(results)


@mcp.tool()
def list_second_brain_sources() -> str:
    """List the valid --source values for scoping a second_brain search
    (e.g. to search only Zotero, or only Boox notes)."""
    return ", ".join(sorted(VAULT_SOURCES | {"zotero"}))


if __name__ == "__main__":
    print(f"Pre-loading embedding model and Qdrant client before serving...", flush=True)
    get_model()
    get_client()
    print(f"Ready. Serving MCP over streamable-http on 0.0.0.0:{MCP_PORT}", flush=True)
    mcp.run(transport="streamable-http")
