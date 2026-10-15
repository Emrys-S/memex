#!/usr/bin/env python3
"""Verify a LinkedIn capture run's output on disk, then (and only then) advance state.json.

Why this exists: on 2026-09-25 a run advanced its cursors without its notes being where
they should be (and the 09-28 run then wrote into the old iCloud vault). The cursors in
state.json are what make the *next* run skip posts, so they must never move ahead of the
files they claim exist.

Usage (the capture skill calls this as its last step):
    python3 commit_run.py manifest.json            # verify, then update state.json
    python3 commit_run.py manifest.json --check    # verify only, never touch state.json

Manifest (JSON), written by the capture run:
{
  "run_date": "2026-10-14",
  "vault": "/Users/emrys/Obsidian Vault",
  "sources": {                       # per source, whatever cursor applies; any key is copied
    "reactions": {"last_seen_post_url": "...", "last_seen_sig": "..."},
    "reshares":  {...}, "saved": {...}
  },
  "items": [
    {"key": "<activity URL or sig:...>", "kind": "link",
     "file": "Resources/LinkedIn Posts/Reading List/<note>.md", "author": "<People note name>"},
    {"key": "...", "kind": "general",
     "file": "Resources/LinkedIn Posts/Activity/2026-W41.md",
     "heading": "2026-10-13 — Author", "author": "<People note name>"}
  ],
  "digest": "Resources/Ingest Digest/2026-10-14-linkedin.md"   # required when items is non-empty
}

Exit code 0 = verified and (unless --check) state updated; 1 = verification failed, state untouched.
"""
import json
import os
import re
import sys
import unicodedata

CANONICAL_VAULT = "/Users/emrys/Obsidian Vault"
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
FORBIDDEN = ("Mobile Documents", "iCloud~md~obsidian")


def nfc(s):
    return unicodedata.normalize("NFC", s)


def exists_nfc(path):
    if os.path.exists(path):
        return path
    d, b = os.path.dirname(path), os.path.basename(path)
    if os.path.isdir(d):
        for f in os.listdir(d):
            if nfc(f) == nfc(b):
                return os.path.join(d, f)
    return None


def verify(manifest, vault=None):
    """Return a list of problems (empty list = everything the run claims is on disk)."""
    problems = []
    vault = vault or manifest.get("vault", "")
    if any(x in vault for x in FORBIDDEN):
        problems.append(f"vault points at the retired iCloud vault: {vault}")
    if vault != CANONICAL_VAULT and vault != os.path.realpath(CANONICAL_VAULT):
        # tests pass a temp vault explicitly; real runs must use the canonical path
        if vault.rstrip("/") != CANONICAL_VAULT:
            problems.append(f"vault is not the canonical vault ({CANONICAL_VAULT}): {vault}")
    if not os.path.isdir(vault):
        problems.append(f"vault directory does not exist: {vault}")
        return problems
    items = manifest.get("items", [])
    seen = set()
    for i, it in enumerate(items):
        key = it.get("key", "")
        tag = f"item {i} ({key[:60]})"
        if not key:
            problems.append(f"{tag}: missing key")
        elif key in seen:
            problems.append(f"{tag}: duplicate key in manifest")
        seen.add(key)
        rel = it.get("file", "")
        if any(x in rel for x in FORBIDDEN) or rel.startswith("/"):
            problems.append(f"{tag}: file must be vault-relative, got {rel}")
            continue
        path = exists_nfc(os.path.join(vault, rel))
        if not path:
            problems.append(f"{tag}: file not found: {rel}")
            continue
        text = open(path, encoding="utf-8").read()
        if len(text.strip()) < 80:
            problems.append(f"{tag}: file is empty or near-empty: {rel}")
            continue
        if it.get("kind") == "link":
            if not text.startswith("---") or not re.search(r"^link:[ \t]*\S+", text, re.M):
                problems.append(f"{tag}: link note lacks frontmatter or a non-empty link: {rel}")
            if "## Archived content" not in text:
                problems.append(f"{tag}: link note has no Archived content section: {rel}")
        elif it.get("kind") == "general":
            h = it.get("heading", "")
            if not h or f"## {h}\n" not in text + "\n":
                problems.append(f"{tag}: heading '## {h}' not found in {rel}")
        else:
            problems.append(f"{tag}: kind must be 'link' or 'general'")
        author = it.get("author")
        if author:
            if not exists_nfc(os.path.join(vault, "Resources/People", author + ".md")):
                problems.append(f"{tag}: People note missing: {author}")
    if items:
        d = manifest.get("digest")
        if not d:
            problems.append("manifest has items but no digest path")
        elif not exists_nfc(os.path.join(vault, d)):
            problems.append(f"digest note not found: {d}")
    return problems


def commit(manifest, state_path=STATE):
    state = json.load(open(state_path))
    keys = [it["key"] for it in manifest.get("items", [])]
    known = set(state.get("captured_post_urls", []))
    state["captured_post_urls"] = state.get("captured_post_urls", []) + [k for k in keys if k not in known]
    for src, cursor in manifest.get("sources", {}).items():
        state.setdefault(src, {}).update(cursor)
        state[src]["last_run"] = manifest.get("run_date", state[src].get("last_run"))
    tmp = state_path + ".tmp"
    json.dump(state, open(tmp, "w"), indent=2, ensure_ascii=False)
    os.replace(tmp, state_path)
    return len(keys)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    manifest = json.load(open(argv[1]))
    problems = verify(manifest)
    if problems:
        print(f"VERIFICATION FAILED ({len(problems)} problem(s)); state.json NOT updated:")
        for p in problems:
            print("  -", p)
        return 1
    n = len(manifest.get("items", []))
    if "--check" in argv:
        print(f"OK: {n} item(s) verified on disk (state.json not touched)")
        return 0
    added = commit(manifest)
    print(f"OK: {n} item(s) verified; state.json updated ({added} keys recorded)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
