"""
newsletter_registry.py
----------------------
Newsletter registry for the newsletter-watcher (added 2026-10-16). Pure functions, no dependencies.

Why: newsletters reach the ingest mailbox as FORWARDS, so the mail's sender is the person who forwarded it,
not the publication, and nothing recorded who the publishers are. To move a newsletter to its own dedicated
address (<publication>@ingest.<domain>, so no forwarding is needed) you first have to know which
publications there are, which platform sends them, and where their subscription links are. This module:

  * finds the ORIGINAL sender in a forwarded message (Gmail, Apple Mail, Outlook formats; plain or HTML-only),
  * finds unsubscribe / manage-preferences links (anchors, plain-text lines, List-Unsubscribe header),
  * names the sending platform, proposes a dedicated address, and judges how easily it can be moved,
  * keeps a registry (dict -> JSON) and renders it as a markdown note.

The rendered note shows link HOSTS only: subscription links carry a per-subscriber token, so full links stay in
the JSON registry on the NAS, not in a note that is synced, indexed and searchable.
"""
import html as htmllib
import json
import re
import unicodedata
from email.utils import parseaddr
from urllib.parse import urlparse

INGEST_DOMAIN_DEFAULT = "ingest.example.org"

_FWD_SUBJECT = re.compile(r"^\s*(?:fwd?|wg|tr|rv|enc|doorst|vs)\s*:\s*", re.I)
_MARKER = re.compile(
    r"(?:-{2,}\s*(?:forwarded message|original message|weitergeleitete nachricht|message transf[eé]r[eé])\s*-{2,}"
    r"|begin forwarded message:?)", re.I)
_FROM = re.compile(r"^[\s>*_]*(?:from|de|von|da|van)\s*:\s*(.+?)\s*$", re.I)
_OTHER_LABEL = re.compile(r"^[\s>*_]*(?:subject|date|sent|to|cc|objet|betreff|asunto|oggetto|onderwerp)\s*:", re.I)

_ANCHOR = re.compile(r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_UNSUB_TEXT = re.compile(r"unsubscribe|opt[- ]?out|stop receiving", re.I)
_MANAGE_TEXT = re.compile(
    r"manage\s+(?:your\s+)?(?:email\s+)?(?:subscription|preferences|settings)|"
    r"(?:update|change|edit)\s+(?:your\s+)?(?:email\s+)?(?:preferences|profile|subscription|settings)|"
    r"(?:email|subscription)\s+preferences", re.I)
_PLAIN_LINK = re.compile(
    r"(unsubscribe|manage[^\n]{0,30}?(?:preferences|subscription)|update[^\n]{0,20}?preferences)"
    r"[^\n]{0,40}?<?(https?://[^\s<>\")]+)", re.I)

# (platform name, substrings looked for in sender domain / link hosts), first match wins
PLATFORMS = [
    ("Substack", ("substack.com",)),
    ("Beehiiv", ("beehiiv.com",)),
    ("Buttondown", ("buttondown.",)),
    ("Ghost", ("ghost.io", "ghost.org")),
    ("Kit (ConvertKit)", ("convertkit", "ck.page", "kit.com")),
    ("Mailchimp", ("list-manage.com", "campaign-archive.com", "mailchi.mp", "mailchimp.com")),
    ("MailerLite", ("mlsend.com", "mailerlite.com", "ml.ink")),
    ("HubSpot", ("hubspot", "hs-sites", "hsforms")),
    ("Constant Contact", ("constantcontact.com", "ctctcdn")),
    ("Campaign Monitor", ("createsend.com", "cmail")),
    ("SendGrid", ("sendgrid.net",)),
    ("LinkedIn", ("linkedin.com", "licdn.com")),
    ("Medium", ("medium.com",)),
]
# Publishers whose newsletters belong to an account (paywall / login), so they cannot be re-subscribed by a
# public signup form.
ACCOUNT_BASED = ("ft.com", "economist.com", "nytimes.com", "wsj.com", "bloomberg.com", "theatlantic.com",
                 "washingtonpost.com", "politico.com", "foreignaffairs.com", "linkedin.com")
SIMPLE_SIGNUP = {"Substack", "Beehiiv", "Buttondown", "Ghost", "Kit (ConvertKit)", "Mailchimp", "MailerLite"}


def _strip_html(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", "", h or "")
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", h)
    return htmllib.unescape(_TAG.sub(" ", h))


def strip_forward_prefix(subject: str) -> str:
    prev = None
    while prev != subject:
        prev, subject = subject, _FWD_SUBJECT.sub("", subject or "")
    return (subject or "").strip()


def clean_name(name: str) -> str:
    name = (name or "").strip().strip('"\'')
    name = re.sub(r"\s+via\s+.+$", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip()


def _parse_from_value(value: str):
    value = re.sub(r"mailto:", "", value or "", flags=re.I)
    name, addr = parseaddr(value)
    if addr and "@" not in addr:                         # parseaddr turns bare text into an "address"
        name, addr = value.strip(), ""
    if not addr:
        m = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", value)
        addr = m.group(0) if m else ""
        name = re.sub(r"[<>()\[\]]|" + re.escape(addr), "", value).strip() if addr else value.strip()
    return clean_name(name), addr.lower()


def find_original_sender(text: str):
    """The From: of the first forwarded header block in `text`, as (name, address) or None."""
    lines = (text or "").splitlines()
    m = _MARKER.search(text or "")
    start = 0
    if m:
        start = (text or "").count("\n", 0, m.start())
    for i in range(start, len(lines)):
        fm = _FROM.match(lines[i])
        if not fm:
            continue
        window = lines[i + 1:i + 7]
        if any(_OTHER_LABEL.match(w) for w in window):       # a real header block, not prose containing "From:"
            name, addr = _parse_from_value(fm.group(1))
            if name or addr:
                return name, addr
    return None


def _host(url: str) -> str:
    try:
        return re.sub(r"^www\.", "", (urlparse(url).netloc or "").lower())
    except Exception:
        return ""


def find_links(html: str, plain: str, list_unsubscribe: str = ""):
    """{'unsubscribe': url|None, 'manage': url|None}. Anchors first, then plain-text lines, then the header."""
    out = {"unsubscribe": None, "manage": None}
    for href, inner in _ANCHOR.findall(html or ""):
        text = re.sub(r"\s+", " ", _strip_html(inner)).strip()
        href = htmllib.unescape(href)
        if not href.lower().startswith(("http://", "https://")):
            continue
        if out["manage"] is None and _MANAGE_TEXT.search(text):
            out["manage"] = href
        elif out["unsubscribe"] is None and _UNSUB_TEXT.search(text):
            out["unsubscribe"] = href
    for label, url in _PLAIN_LINK.findall(plain or ""):
        url = url.rstrip(".,;)")
        key = "unsubscribe" if re.match(r"unsub", label, re.I) else "manage"
        if out[key] is None:
            out[key] = url
    if out["unsubscribe"] is None and list_unsubscribe:
        m = re.search(r"<(https?://[^>]+)>", list_unsubscribe)
        if m:
            out["unsubscribe"] = m.group(1)
    return out


def detect_platform(sender_addr: str, urls) -> str:
    hosts = [(sender_addr or "").split("@")[-1].lower()] + [_host(u) for u in urls if u]
    for name, needles in PLATFORMS:
        if any(n in h for h in hosts for n in needles):
            return name
    return "unknown"


def slugify(name: str, addr: str) -> str:
    base = clean_name(name) or (addr or "").split("@")[0]
    base = re.sub(r"\b(?:newsletter|news|team|updates?|the|from)\b", " ", base, flags=re.I)
    s = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    if len(s) < 3:
        dom = (addr or "").split("@")[-1].split(".")[0]
        s = re.sub(r"[^a-z0-9]+", "", dom.lower()) or "newsletter"
    return s[:30]


def analyse(subject: str, plain: str, html: str, headers_json: str, from_header: str, recipient: str = ""):
    """What we can tell about one inbound message.

    Returns dict: sender_name, sender_email, forwarded (bool), original_subject, platform, unsubscribe, manage,
    recipient_local, forwarded_by."""
    text = plain if plain and plain.strip() else _strip_html(html or "")
    envelope_name, envelope_addr = _parse_from_value(from_header)
    forwarded = bool(_FWD_SUBJECT.match(subject or "")) or bool(_MARKER.search(text))
    orig = find_original_sender(text) if forwarded else None
    if orig:
        name, addr = orig
    else:
        name, addr = envelope_name, envelope_addr
        if forwarded:                                    # forward without a parsable header block: say so, do not guess
            name, addr = "", ""
    list_unsub = ""
    try:
        for k, v in json.loads(headers_json or "[]"):
            if str(k).lower() == "list-unsubscribe":
                list_unsub = str(v)
    except Exception:
        pass
    links = find_links(html, text, list_unsub)
    return {
        "sender_name": name, "sender_email": addr, "forwarded": forwarded,
        "forwarded_by": envelope_addr if forwarded else "",
        "original_subject": strip_forward_prefix(subject),
        "platform": detect_platform(addr or envelope_addr, [links["unsubscribe"], links["manage"]]),
        "unsubscribe": links["unsubscribe"], "manage": links["manage"],
        "recipient_local": (recipient or "").split("@")[0].lower(),
    }


def _ease(entry) -> str:
    dom = entry["sender_email"].split("@")[-1] if entry["sender_email"] else ""
    if entry["platform"] == "LinkedIn" or any(dom == d or dom.endswith("." + d) for d in ACCOUNT_BASED):
        return "manual (tied to an account)"
    if entry["platform"] in SIMPLE_SIGNUP:
        return "can automate (simple signup form)"
    return "check the publisher's signup page"


def observe(registry: dict, info: dict, now_iso: str, note_name: str = "", ingest_domain: str = INGEST_DOMAIN_DEFAULT):
    """Fold one analysed message into the registry (in place). Returns the entry, or None if the publisher is unknown."""
    if not info.get("sender_email") and not info.get("sender_name"):
        return None
    key = info["sender_email"] or ("name:" + info["sender_name"].lower())
    e = registry.get(key)
    if e is None:
        slug = slugify(info["sender_name"], info["sender_email"])
        taken = {x["slug"] for x in registry.values()}
        if slug in taken:                                # same slug for a different sender: add the domain stem
            stem = re.sub(r"[^a-z0-9]", "", info["sender_email"].split("@")[-1].split(".")[0]) if info["sender_email"] else "2"
            slug = (slug + stem)[:30]
        e = registry[key] = {
            "name": info["sender_name"] or key, "sender_email": info["sender_email"], "slug": slug,
            "address": f"{slug}@{ingest_domain}", "platform": info["platform"], "first_seen": now_iso[:10],
            "seen_forwarded": 0, "seen_direct": 0, "example_subject": info["original_subject"],
            "unsubscribe_url": None, "manage_url": None, "last_note": "",
        }
    e["last_seen"] = now_iso[:10]
    e["seen_forwarded" if info["forwarded"] else "seen_direct"] += 1
    if info["platform"] != "unknown":
        e["platform"] = info["platform"]
    for k in ("unsubscribe", "manage"):
        if info.get(k):
            e[k + "_url"] = info[k]
    if note_name:
        e["last_note"] = note_name
    e["example_subject"] = info["original_subject"] or e["example_subject"]
    return e


def status_of(e) -> str:
    if e["seen_direct"] and not e["seen_forwarded"]:
        return "direct (done)"
    if e["seen_direct"]:
        return "direct and forwarded"
    return "forwarded only: move to the dedicated address"


def render_registry(registry: dict, ingest_domain: str = INGEST_DOMAIN_DEFAULT) -> str:
    rows = sorted(registry.values(), key=lambda e: (e["seen_direct"] > 0 and not e["seen_forwarded"], -int(e["last_seen"].replace("-", ""))))
    out = ["---", "title: Newsletter registry", "type: newsletter-registry", "tags:", "  - newsletter", "---", "",
           "# Newsletter registry", "",
           f"Generated by the newsletter-watcher from what reaches `*@{ingest_domain}`. Edit nothing here: it is rewritten on "
           "every new message. Subscribe each publication directly at its dedicated address so nothing has to be forwarded.",
           "", "| Publication | Sender | Platform | Seen (fwd / direct) | Dedicated address | Links on file | Status | How to move |",
           "|---|---|---|---|---|---|---|---|"]
    for e in rows:
        links = ", ".join(x for x in (
            "manage: " + _host(e["manage_url"]) if e.get("manage_url") else "",
            "unsubscribe: " + _host(e["unsubscribe_url"]) if e.get("unsubscribe_url") else "") if x) or "none"
        sender = e["sender_email"] or "(not in the forward)"
        out.append(f"| {e['name']} | {sender} | {e['platform']} | {e['seen_forwarded']} / {e['seen_direct']} | "
                   f"`{e['address']}` | {links} | {status_of(e)} | {_ease(e) if e['seen_direct'] == 0 or e['seen_forwarded'] else 'n/a'} |")
    out += ["", f"{len(rows)} publication(s). Last updated from: " + (max(e['last_seen'] for e in rows) if rows else "nothing yet") + ".", ""]
    return "\n".join(out)
