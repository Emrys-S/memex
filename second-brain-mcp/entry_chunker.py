"""
entry_chunker.py
----------------
Per-entry chunking for notes that accumulate dated bullet entries (added 2026-10-16).

Why: People notes (and Boox person/meeting notes) are append-only logs: each handwritten session is
appended as "- 2026-10-06 21:41 (handwritten, Boox, page 1): ...". The indexer used to split only on
"## " headings, so a whole "## Notes" section -- several sessions, plus the handwritten-original image
embeds -- became ONE chunk. Mostly garbled OCR in one blurry embedding scored 0.29 for a query that
named the very phrase inside it. One chunk per dated entry, embedded together with the note's title so
a short entry still knows whose note it is, gives each session its own, sharper vector.

Pure functions, no dependencies: used by vault_indexer.py on the NAS, tested here.
"""
import re

# a dated bullet at column 0: "- 2026-10-06 21:41 (handwritten, ...):"
_ENTRY = re.compile(r"(?m)^- (?=\d{4}-\d{2}-\d{2})")
_LABEL = re.compile(r"^- (\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?)")
# the "Handwritten original" callout and its image embed: noise for an embedding
_CALLOUT = re.compile(r"(?m)^[ \t]*>[ \t]*(?:\[!note\][^\n]*Handwritten original|!\[\[[^\n]*\]\])[^\n]*\n?")


def split_entries(text: str, min_entries: int = 2) -> list:
    """Split a section into [(entry_label, entry_text), ...], one per dated bullet.

    Text before the first dated bullet is kept as its own (None, text) piece. With fewer than
    min_entries dated bullets the section is left whole as [(None, text)], so ordinary notes are
    chunked exactly as before.
    """
    starts = [m.start() for m in _ENTRY.finditer(text)]
    if len(starts) < min_entries:
        return [(None, text)]
    out = []
    head = text[:starts[0]].strip()
    if head:
        out.append((None, head))
    bounds = starts + [len(text)]
    for a, b in zip(bounds, bounds[1:]):
        piece = text[a:b].strip()
        m = _LABEL.match(piece)
        out.append((m.group(1) if m else None, piece))
    return out


def embed_text(title: str, heading, text: str) -> str:
    """What gets embedded: a context line (note title and section) plus the text without the
    handwritten-original image callout. The stored/displayed text is unchanged."""
    ctx = f"{title} - {heading}" if heading else f"{title}"
    body = _CALLOUT.sub("", text)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return f"{ctx}\n{body}"
