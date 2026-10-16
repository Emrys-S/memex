"""
result_hygiene.py
-----------------
Query-time clean-up for second-brain search results (added 2026-10-15).

Why: unscoped searches were unhelpful because ~330k Zotero full-text chunks (including
back-of-book author indexes and reference lists) were merged purely by score with ~5k vault
notes, the same paper came back several times, and empty title-only / dataview-boilerplate
vault chunks ranked high. These functions are pure (no Qdrant, no model) so they can be
tested anywhere. Thresholds are env-tunable.
"""
import os
import re

VAULT_MIN_SCORE = float(os.environ.get("VAULT_MIN_SCORE", "0.35"))
ZOTERO_MIN_SCORE = float(os.environ.get("ZOTERO_MIN_SCORE", "0.42"))
MAX_CHUNKS_PER_DOC = int(os.environ.get("MAX_CHUNKS_PER_DOC", "2"))
RRF_K = 60
ANCHOR_MIN_SCORE = float(os.environ.get("ANCHOR_MIN_SCORE", "0.55"))

_YEAR_PAREN = re.compile(r"\((?:19|20)\d{2}[a-z]?\)")
_REF_MARKERS = re.compile(
    r"doi\.org|\bdoi:|Available at|Retrieved|Accessed|\bpp\.\s*\d|\bet al\.|\bEds?\.\)|\bIn:|"
    r"Journal of|University Press|\bPress,|https?://|\bvol\.\s*\d|\bno\.\s*\d",
    re.I,
)
_FRONT_MATTER = re.compile(r"ISBN|All rights reserved|Library of Congress|First published|Cataloguing[- ]in[- ]Publication|Printed in", re.I)
_NUM_TOKEN = re.compile(r"\d{1,4}[nf]?[,.;]?")


def looks_like_index(text: str) -> bool:
    """Back-of-book author/subject index: 'Johnson, Mark 219, 249 Johnson, Steven 125, 179 ...'."""
    words = text.split()
    n = len(words)
    if n < 30:
        return False
    nums = sum(1 for w in words if _NUM_TOKEN.fullmatch(w))
    return nums / n > 0.22 and text.count(",") / n > 0.15


def looks_like_reference_list(text: str) -> bool:
    """Bibliography: dense parenthesised years, DOIs/URLs, 'Journal of', 'pp. 12'."""
    n = len(text)
    if n < 200:
        return False
    hits = len(_YEAR_PAREN.findall(text)) + len(_REF_MARKERS.findall(text))
    return hits * 1000.0 / n >= 7.0


def looks_like_front_matter(text: str) -> bool:
    return len(_FRONT_MATTER.findall(text)) >= 2 or len(re.findall(r"\.{4,}\s*\d", text)) >= 3


def is_low_value_zotero(payload: dict) -> bool:
    """True for Zotero chunks that match keywords but carry no usable content. Metadata chunks
    (title/abstract) are always kept."""
    if payload.get("chunk_type") == "metadata":
        return False
    text = payload.get("text", "") or ""
    return looks_like_index(text) or looks_like_reference_list(text) or looks_like_front_matter(text)


def is_title_only(payload: dict) -> bool:
    """An empty heading chunk (e.g. '# Ana Tavares...'): a strong match on it means the NOTE is
    relevant, so search() pulls in that note's real content chunks instead of returning the stub."""
    text = (payload.get("text", "") or "").strip()
    body = re.sub(r"(?m)^\s*#{1,6}\s.*$", "", text).strip()
    return len(re.sub(r"\.\.\.$", "", body).strip()) < 60 and not re.match(r"```", text)


def is_low_value_vault(payload: dict) -> bool:
    """True for empty title-only chunks, Obsidian dataview/tasks boilerplate and pure
    'mentioned in [[...]]' backlink blocks."""
    text = (payload.get("text", "") or "").strip()
    if re.match(r"```(?:dataview|dataviewjs|tasks)\b", text):
        return True
    body = re.sub(r"(?m)^\s*#{1,6}\s.*$", "", text).strip()      # drop heading lines
    body = re.sub(r"\.\.\.$", "", body).strip()
    if len(body) < 60:
        return True
    lines = [l for l in text.splitlines() if l.strip()]
    if lines and sum("mentioned in [[" in l for l in lines) / len(lines) >= 0.8:
        return True
    return False


def doc_key(collection_is_zotero: bool, payload: dict) -> str:
    if collection_is_zotero:
        t = re.sub(r"[^a-z0-9]+", "", (payload.get("title", "") or "").lower())[:80]
        return "z:" + (t or str(payload.get("item_key")))
    return "v:" + (payload.get("path") or payload.get("title", ""))


def clean(results, is_zotero: bool, min_score: float, per_doc: int = None):
    """Filter low-value chunks, apply a score floor, and cap chunks per document. `results` are
    objects with .score and .payload, best first. Order is preserved."""
    per_doc = per_doc or MAX_CHUNKS_PER_DOC
    out, seen = [], {}
    for r in results:
        if r.score < min_score:
            continue
        if (is_low_value_zotero if is_zotero else is_low_value_vault)(r.payload):
            continue
        k = doc_key(is_zotero, r.payload)
        if seen.get(k, 0) >= per_doc:
            continue
        seen[k] = seen.get(k, 0) + 1
        out.append(r)
    return out


def fuse(vault, zotero, top_k: int):
    """Reciprocal-rank fusion of two already-cleaned lists (so a few strong vault notes are not
    drowned by the far larger Zotero pool); ties broken by raw score."""
    scored = []
    for lst in (vault, zotero):
        for rank, r in enumerate(lst):
            scored.append((1.0 / (RRF_K + rank), r.score, r))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [r for _, _, r in scored[:top_k]]


# ---- keyword fallback (added 2026-10-16) ---------------------------------------------------------
# Semantic similarity is weak for short topic queries against noisy handwriting OCR: a bullet
# "Standards Incubation" inside a 700-char chunk of mostly garbled text scored 0.29, under the 0.35
# floor, so "standards incubation" found nothing. When the vault search is thin, also look for the
# query's own words in the note text (a Qdrant full-text index on the vault collection).
STRONG_SCORE = float(os.environ.get("LEXICAL_STRONG_SCORE", "0.45"))
LEXICAL_WANT = int(os.environ.get("LEXICAL_WANT", "3"))
LEXICAL_MAX = int(os.environ.get("LEXICAL_MAX", "4"))

_FILLER = set("""
a about after again all also am an and any anything are around as at be been before being between both
but by can could did do does doing down during each else ever every find for from get give had has have
having her here him his how if in into is it its just know like list look make many may me might more
most much must my near no nor not now of off on once only or other our out over own please remember
same say search see she should show since so some something such tell than that the their them then
there these they this those through to too under until up us use very want was we were what when where
which while who whom why will with within without would you your note notes noted wrote write written
mention mentioned document documents file files saved""".split())
_WORD = re.compile(r"[A-Za-z0-9]+")


def content_terms(query: str, max_terms: int = 4) -> list:
    """The searchable words of a question: lower-case, 3+ letters, no filler, de-duplicated, in order.
    If there are more than max_terms, keep the longest (most specific) ones."""
    seen, out = set(), []
    for w in _WORD.findall((query or "").lower()):
        if len(w) < 3 or w in _FILLER or w in seen:
            continue
        seen.add(w)
        out.append(w)
    if len(out) > max_terms:
        keep = set(sorted(out, key=len, reverse=True)[:max_terms])
        out = [w for w in out if w in keep]
    return out


def lexical_wanted(terms: list, semantic) -> bool:
    """Run the keyword fallback only when there are real words to look for and the semantic results
    are thin (fewer than LEXICAL_WANT results at STRONG_SCORE or better). A single short word is too
    common to be useful on its own."""
    if not terms:
        return False
    if len(terms) == 1 and len(terms[0]) < 8:
        return False
    return sum(1 for r in semantic if r.score >= STRONG_SCORE) < LEXICAL_WANT


def _ident(r):
    return (r.payload.get("path"), r.payload.get("section_heading"))


def merge_keyword(semantic, keyword):
    """Strong semantic hits first, then the keyword hits, then the remaining weak semantic hits;
    a chunk found both ways appears once."""
    strong = [r for r in semantic if r.score >= STRONG_SCORE]
    seen = {_ident(r) for r in strong}
    kw = []
    for r in keyword:
        if _ident(r) not in seen:
            seen.add(_ident(r))
            kw.append(r)
    weak = [r for r in semantic if r.score < STRONG_SCORE and _ident(r) not in seen]
    return strong + kw + weak


def cosine(a, b) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5
    db = sum(y * y for y in b) ** 0.5
    return num / (da * db) if da and db else 0.0
