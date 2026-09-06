#!/usr/bin/env python3
"""
mcp_server.py
-------------
MCP (Model Context Protocol) server exposing the second-brain semantic
search (query.py's search()/synthesize_answer()) as tools LibreChat can
call directly, over SSE. Reuses the exact same search logic already proven
via the CLI (`query.py`) -- this is a thin MCP wrapper, not a reimplementation.
"""

import os
import re

from mcp.server.mcpserver import MCPServer
from query import search, synthesize_answer, VAULT_SOURCES

# sentence-transformers/torch import above is genuinely slow (~90s) on this
# NAS's CPU -- confirmed, not a bug. Startup just takes a while.
mcp = MCPServer("second-brain")


@mcp.tool()
def query_second_brain(
    question: str,
    source: str = "",
    top_k: int = 8,
    synthesize: bool = False,
    zotero_collection: str = "",
) -> str:
    """Semantic search across the user's second brain: their Obsidian vault
    (Boox handwritten notes, Bluesky captures, LinkedIn captures, People
    notes, project notes, personal notes) and their full Zotero research
    library, merged and ranked by relevance.

    Args:
        question: The question or search text to look up.
        source: Optional. Scope to one source instead of searching everything.
            Valid values: boox, bluesky, linkedin, people, meeting-notes,
            personal, projects, inbox, other, zotero. Leave empty to search
            everything at once.
        top_k: Number of results to return (default 8).
        synthesize: If true, also generate a short LLM-written answer with
            citations back to the source excerpts, instead of just raw
            results. Costs a small extra API call -- use only when the raw
            excerpts alone won't clearly answer the question.
        zotero_collection: Optional. Scope to one named Zotero collection
            (e.g. "AI Governance", "Digital Sovereignty") instead of the
            whole library. Forces a Zotero-only search, overriding `source`.
            Note: collection metadata was only backfilled onto a subset of
            already-indexed Zotero items as of 2026-08-15 (many items,
            especially in Personal Library, aren't yet indexed at all --
            see the known indexing-gap issue), so an empty result for a
            real collection name may mean "not indexed yet", not "no match".

    Returns:
        Formatted results, each with a title, relevance score, source label,
        a link back to either the Obsidian note or the Zotero item, and a
        text snippet. If synthesize=True, a synthesized answer is appended.
    """
    src = source.strip() or None
    if src is not None and src not in (VAULT_SOURCES | {"zotero"}):
        valid = ", ".join(sorted(VAULT_SOURCES | {"zotero"}))
        return f"Invalid source '{src}'. Valid values: {valid} (or omit to search everything)."

    zcol = zotero_collection.strip() or None
    results = search(question, top_k, src, zcol)

    if not results:
        return "No results found."

    lines = []
    for i, r in enumerate(results, start=1):
        lines.append(f"{i}. {r.title}  (score: {r.score:.3f}, source: {r.source_label})")
        if r.obsidian_link:
            lines.append(f"   Note:   {r.obsidian_link}")
        if r.external_link:
            lines.append(f"   Source: {r.external_link}")
        snippet = r.text[:400].replace("\n", " ")
        lines.append(f"   {snippet}...")
        lines.append("")

    if synthesize:
        lines.append("─" * 40)
        lines.append("Synthesized answer:")
        lines.append(synthesize_answer(question, results))

    return "\n".join(lines)


def _split_outline(outline: str) -> list:
    """Split an outline into sections using whatever structure it actually has.
    Tries markdown headers, then top-level list items, then blank-line-separated
    paragraphs, falling back to treating the whole thing as one section.
    """
    header_matches = list(re.finditer(r"^#{1,6}\s+.+$", outline, re.MULTILINE))
    if len(header_matches) >= 2:
        bounds = [m.start() for m in header_matches] + [len(outline)]
        return [outline[bounds[i]:bounds[i + 1]].strip() for i in range(len(bounds) - 1)]

    list_matches = list(re.finditer(r"^\s*(?:\d+[.)]|-|\*)\s+.+$", outline, re.MULTILINE))
    if len(list_matches) >= 2:
        bounds = [m.start() for m in list_matches] + [len(outline)]
        return [outline[bounds[i]:bounds[i + 1]].strip() for i in range(len(bounds) - 1)]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", outline) if p.strip()]
    if len(paragraphs) >= 2:
        return paragraphs

    return [outline.strip()]


@mcp.tool()
def map_outline_to_second_brain(
    outline: str,
    source: str = "",
    top_k_per_section: int = 5,
    zotero_collection: str = "",
) -> str:
    """Map a writing outline against the user's second brain, section by section.

    Splits the outline into its natural sections (markdown headers, list items,
    or paragraphs -- whatever structure it has), retrieves relevant evidence for
    each section separately, then produces one structured analysis covering, per
    section: supporting evidence, whether the argument is already made or
    disproved elsewhere in the user's material, and nuances worth folding in.
    Use this instead of several separate query_second_brain calls when the user
    hands you a whole outline to check against their knowledge base -- it avoids
    the timeout risk of many broad, unscoped queries by doing bounded, per-section
    retrieval and a single synthesis pass.

    Args:
        outline: The full outline text. Section boundaries are detected
            automatically -- markdown headers, numbered/bulleted lists, or
            plain paragraphs all work.
        source: Optional. Scope retrieval to one source (boox, bluesky,
            linkedin, people, meeting-notes, personal, projects, inbox,
            other, zotero). Leave empty to search everything.
        top_k_per_section: Results retrieved per section (default 5).
        zotero_collection: Optional. Scope retrieval to one named Zotero
            collection (e.g. "AI Governance") instead of the whole library.
            Forces Zotero-only retrieval, overriding `source`. Same coverage
            caveat as query_second_brain -- not every indexed item has
            collection metadata yet.

    Returns:
        A structured markdown report, one subsection per outline point, each
        citing back to the specific source notes/papers it draws on.
    """
    src = source.strip() or None
    if src is not None and src not in (VAULT_SOURCES | {"zotero"}):
        valid = ", ".join(sorted(VAULT_SOURCES | {"zotero"}))
        return f"Invalid source '{src}'. Valid values: {valid} (or omit to search everything)."

    zcol = zotero_collection.strip() or None

    sections = _split_outline(outline)
    if len(sections) > 20:
        return (
            f"This outline split into {len(sections)} sections -- more than this tool "
            f"handles well in one pass (cap: 20). Try mapping it in smaller chunks."
        )

    section_evidence = []
    for section in sections:
        query_text = section[:500]
        results = search(query_text, top_k_per_section, src, zcol)
        section_evidence.append((section, results))

    if not any(results for _, results in section_evidence):
        return "No relevant content found in the second brain for any part of this outline."

    context_parts = []
    for i, (section, results) in enumerate(section_evidence, start=1):
        context_parts.append(f"=== OUTLINE SECTION {i} ===\n{section}\n")
        if not results:
            context_parts.append("(No relevant second-brain content found for this section.)\n")
            continue
        for j, r in enumerate(results, start=1):
            link = r.obsidian_link or r.external_link or ""
            link_note = f" -- {link}" if link else ""
            context_parts.append(
                f"[{i}.{j}] {r.title} (source: {r.source_label}, score: {r.score:.2f}){link_note}\n"
                f"{r.text[:600]}\n"
            )
    full_context = "\n".join(context_parts)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "(ANTHROPIC_API_KEY not set -- cannot synthesize. Raw evidence:)\n\n" + full_context

    import anthropic

    prompt = (
        "The user is drafting a piece of writing and has given you their outline, "
        "split into numbered sections below. For each section, real excerpts from "
        "their second brain (personal notes, captured articles, research library) "
        "have been retrieved by semantic search.\n\n"
        "For EACH outline section, produce a short analysis covering:\n"
        "- Supporting evidence: which excerpts (cite by [N.M] number) genuinely "
        "support this section's point, if any\n"
        "- Already made / disproved: whether this argument already appears "
        "elsewhere in their own material (worth citing or building on, not just "
        "repeating) or whether anything found contradicts it\n"
        "- Nuances: any angle, complication, or detail in the evidence that could "
        "strengthen or complicate this section, worth folding in\n\n"
        "Be honest when a section has no good supporting material -- say so rather "
        "than stretching a weak match. Structure your response with one subsection "
        "per outline section, using the same numbering.\n\n"
        f"{full_context}"
    )
    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.environ.get("MCP_PORT", "8001")))
