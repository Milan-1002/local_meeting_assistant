"""Obsidian-style knowledge base built from meetings (the "LLM wiki" pattern).

vault/raw/meetings/<id>.md   transcripts, never edited
vault/wiki/index.md          one line per page; the copilot reads this to find pages
vault/wiki/me.md             who the user is; written by hand, plus facts learned from meetings
vault/wiki/{people,projects,topics,meetings}/*.md   pages linked with [[wikilinks]]
vault/wiki/weekly/<monday>.md   the week's review: to-dos, decisions, what to know

    python kb.py import-sessions     # copy sessions/*/transcript.txt into raw/
    python kb.py compile [ID]        # turn new raw meetings into wiki pages (LLM)
    python kb.py index               # rebuild index.md
    python kb.py lint [--fix]        # broken links, orphans; --fix rewrites overgrown pages (LLM)
    python kb.py weekly [DATE]       # write the weekly review for the week containing DATE (LLM)
    python kb.py backup              # copy wiki/ to .backup/<time>/ (the migrations below do this first)
    python kb.py dedupe [--apply]    # find duplicate pages + build the glossary (one LLM call); --apply merges
    python kb.py merge FROM TO       # merge one page into another, e.g. topics/cpnr projects/cpr
    python kb.py rebuild-pages       # convert append-only pages to Now / Key facts / Log (one call per page)
    python kb.py find QUERY          # what the copilot would look up for QUERY
    python kb.py threads             # open threads: who owes what to whom

Entity pages (people/projects/topics) hold one current picture, rewritten by every meeting that touches them:
"## Now" (current state) · "## Key facts" (deduped, cited) · projects also "## Decisions", "## Open threads",
"## People" · "## Log" (one line per meeting, append-only, so nothing learned is ever lost).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import shutil
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import llm
import speakers
from config import CHAT_EFFORT, CHAT_MODEL, MAIN_MODEL, MY_ALIASES, MY_NAME, SESSIONS_DIR, VAULT_DIR
from speakers import NOTE as TRANSCRIPT_NOTE

RAW = VAULT_DIR / "raw" / "meetings"
WIKI = VAULT_DIR / "wiki"
WEEKLY = WIKI / "weekly"
KINDS = ("people", "projects", "topics")
TYPES = {"people": "person", "projects": "project", "topics": "topic"}
GLOSSARY = WIKI / "glossary.md"
THREADS = WIKI / "threads.md"
ME_LOG = WIKI / "me-log.md"
VOICES = WIKI / "voices.md"
SPECIAL = {"me", "me-log", "glossary", "threads", "voices"}  # code-managed pages that aren't people/projects/topics
ORDER = ("Now", "Key facts", "Decisions", "Open threads", "People")  # entity page sections; "Log" goes last
MAX_FACTS, MAX_NOW, MAX_ME = 15, 6, 12
FIRST_NAME = MY_NAME.split()[0]
LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")

STOPWORDS = set("""about above after again against all also and any are because been before being below
between both but can could did does doing down during each few for from further had has have having here
how into just like more most much need only other our out over own really right same should some such than
that the their them then there these they this those through too under until very want was way were what
when where which while who whom why will with would yeah you your okay going think know thing things
gonna kind sort well good make maybe said says sure time today actually""".split())

ME_TEMPLATE = f"""---
type: me
summary: Who {FIRST_NAME} is, what they work on, and how they like to answer.
---
# {MY_NAME}

Edit this page freely. The copilot reads it before every answer, so the more specific it is, the more
the answers sound like you.

## About me
- Role:
- Team / company:
- Background and strengths:

## How I talk
- e.g. short and direct; friendly; I like to give a concrete example; I say "I'd suggest" rather than "you should".

## My views and opinions
- e.g. I prefer small incremental releases over big-bang launches.

## What I'm working on

## Learned from meetings
"""

VAULT_RULES = f"""# Vault rules

This is {MY_NAME}'s meeting knowledge base, maintained by an LLM (Karpathy's "LLM wiki" pattern) and
browsed in Obsidian. Any agent editing it should follow these rules.

- `raw/meetings/` holds transcripts. Never edit them.
- `wiki/` is written by `kb.py compile` and by hand. Every page has YAML frontmatter with `type`,
  `summary` (one line; used by `index.md`) and `updated`.
- Pages live in `wiki/people/`, `wiki/projects/`, `wiki/topics/`, `wiki/meetings/`, plus `wiki/me.md`.
  File names are lowercase-hyphenated slugs. Link with `[[people/jane-doe|Jane]]`.
- People/project/topic pages hold one current picture, rewritten as meetings add to it:
  `## Now` (current state, 2-6 bullets) · `## Key facts` (at most 15, deduped, each ending with the meeting
  it came from: `([[meetings/<id>]])`) · projects also `## Decisions`, `## Open threads`, `## People` ·
  `## Log` (one dated line per meeting; append-only, never rewritten, except that recompiling a meeting
  replaces that same meeting's own line).
- `wiki/glossary.md`: canonical names and their aliases/mis-hearings (`- **CPR** — aka CPNR, CP&R — meaning → [[page]]`).
- `wiki/threads.md`: open loops across meetings (`- [ ] t7 · owner → waiting on · item · [[project]] · since [[meeting]]`);
  tick a box to close one.
- `wiki/voices.md`: which Person each remembered Voice is (`- Speaker 3 → [[people/priya|Priya]]`; `[[me]]` is
  {FIRST_NAME}). Transcripts say "Speaker 3:" and never change; this page supplies the name. Fix a wrong line here.
  Naming a speaker marks Meeting notes written before the name was known with `needs_recompile:`;
  `python kb.py compile` (or Recompile on the Meetings page) rewrites them. Moving a speaker to a different
  person adds `needs_review:` to the old person's page; `lint` lists the lines there that cite the affected
  meetings. Check them, then delete the `needs_review` and `review_meetings` lines.
- `wiki/me-log.md`: every fact learned about the user; `me.md`'s "Learned from meetings" is the condensed version.
- `wiki/index.md` is generated: run `python kb.py index` instead of editing it.
- `wiki/me.md` is the user's own page; keep their hand-written sections intact.
- Run `python kb.py lint` to find broken links and orphan pages, `python kb.py dedupe` for duplicate pages.
- `kb.py` keeps backups in `.backup/` before bulk changes.
"""

GRAPH_CONFIG = {
    "colorGroups": [
        {"query": "path:wiki/people", "color": {"a": 1, "rgb": 15105570}},
        {"query": "path:wiki/projects", "color": {"a": 1, "rgb": 3066993}},
        {"query": "path:wiki/topics", "color": {"a": 1, "rgb": 10181046}},
        {"query": "path:wiki/meetings", "color": {"a": 1, "rgb": 9807270}},
    ],
    "search": "path:wiki",
}


# --------------------------------------------------------------------------- files


def ensure_vault() -> None:
    for d in [RAW, *(WIKI / k for k in (*KINDS, "meetings"))]:
        d.mkdir(parents=True, exist_ok=True)
    if not (WIKI / "me.md").exists():
        (WIKI / "me.md").write_text(ME_TEMPLATE)
    if not (VAULT_DIR / "CLAUDE.md").exists():
        (VAULT_DIR / "CLAUDE.md").write_text(VAULT_RULES)
    graph = VAULT_DIR / ".obsidian" / "graph.json"
    if not graph.exists():
        graph.parent.mkdir(exist_ok=True)
        graph.write_text(json.dumps(GRAPH_CONFIG, indent=2))
    if not (WIKI / "index.md").exists():
        rebuild_index()


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[end + 5:]


def join_frontmatter(meta: dict, body: str) -> str:
    head = "\n".join(f"{k}: {str(v).replace(chr(10), ' ')}" for k, v in meta.items())
    return f"---\n{head}\n---\n{body.lstrip(chr(10))}"


def title_of(body: str, fallback: str) -> str:
    m = re.search(r"^# (.+)$", body, re.M)
    return m.group(1).strip() if m else fallback


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "untitled"


def wiki_pages() -> list[Path]:
    return sorted(p for p in WIKI.rglob("*.md") if p.name != "index.md")


def rel(p: Path) -> str:
    return p.relative_to(WIKI).with_suffix("").as_posix()


def entity_pages() -> list[Path]:
    return [p for p in wiki_pages() if rel(p).split("/")[0] in KINDS]


# --------------------------------------------------------------------------- page sections


def parse_page(body: str) -> tuple[str, list[list[str]]]:
    """Body -> (text before the first "## " heading, [[heading, content], …]). "### " stays inside its section."""
    parts = re.split(r"^## (.+?)[ \t]*$", body, flags=re.M)
    return parts[0].strip("\n"), [[parts[i].strip(), parts[i + 1].strip("\n")] for i in range(1, len(parts), 2)]


def get_section(body: str, name: str) -> str | None:
    return next((c for h, c in parse_page(body)[1] if h == name), None)


def replace_section(body: str, name: str, content: str) -> str:
    """Swap one section's content in place, keeping every other section where it is (used for me.md)."""
    head, sections = parse_page(body)
    for s in sections:
        if s[0] == name:
            s[1] = content
            break
    else:
        sections.append([name, content])
    return "\n\n".join([head.strip(), *(f"## {h}\n{c.strip()}".strip() for h, c in sections)]) + "\n"


def render_entity(head: str, sections: dict[str, str]) -> str:
    """An entity page in canonical order: Now, Key facts, Decisions, Open threads, People, anything else, Log."""
    order = [h for h in ORDER if h in sections] + [h for h in sections if h not in ORDER and h != "Log"]
    order += ["Log"] if "Log" in sections else []
    out = [head.strip()]
    for h in order:
        if sections[h].strip():
            out += ["", f"## {h}", sections[h].strip()]
    return "\n".join(out) + "\n"


def bullets(text: str | None) -> list[str]:
    return [line[2:].strip() for line in (text or "").splitlines() if line.startswith("- ") and line[2:].strip()]


def as_list(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items)


def clean_item(item: str) -> str:
    """Models sometimes start list items with their own "- " or "• "."""
    return re.sub(r"^\s*([-*•]\s*)+", "", str(item)).strip()


def norm(text: str) -> str:
    """For spotting duplicates: lowercase words without citations or punctuation."""
    text = re.sub(r"\(\s*\[\[[^\]]*\]\][^)]*\)", " ", text.lower())
    return " ".join(re.findall(r"[a-z0-9]+", text))


def merge_unique(old: list[str], new: list[str]) -> list[str]:
    seen = {norm(x) for x in old}
    out = list(old)
    for x in new:
        if norm(x) and norm(x) not in seen:
            seen.add(norm(x))
            out.append(x)
    return out


def page_title(path_rel: str) -> str:
    p = WIKI / f"{path_rel}.md"
    return title_of(split_frontmatter(p.read_text())[1], p.stem) if p.exists() else path_rel.split("/")[-1]


# --------------------------------------------------------------------------- reading (live use)


def load_me() -> str:
    p = WIKI / "me.md"
    return split_frontmatter(p.read_text())[1].strip() if p.exists() else ""


def load_index() -> str:
    p = WIKI / "index.md"
    return p.read_text().strip() if p.exists() else ""


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9']{3,}", text.lower()) if w not in STOPWORDS}


def load_glossary_text(limit: int = 4000) -> str:
    if not GLOSSARY.exists():
        return ""
    lines = [x for x in split_frontmatter(GLOSSARY.read_text())[1].splitlines() if x.startswith("- ")]
    return "\n".join(lines)[:limit]


# --------------------------------------------------------------------------- glossary


def load_glossary() -> dict[str, dict]:
    """Canonical term -> {aliases, meaning, page}, from lines like
    `- **CPR** — aka CPNR, CP&R — Client Payment & Reporting → [[projects/cpr]]`."""
    out: dict[str, dict] = {}
    if not GLOSSARY.exists():
        return out
    for line in split_frontmatter(GLOSSARY.read_text())[1].splitlines():
        m = re.match(r"- \*\*(.+?)\*\*(.*)$", line.strip())
        if not m:
            continue
        term, rest, page = m.group(1).strip(), m.group(2), ""
        pm = re.search(r"\s*→\s*\[\[([^\]|#]+)[^\]]*\]\]\s*$", rest)
        if pm:
            page, rest = pm.group(1).strip(), rest[:pm.start()]
        aliases, meaning = [], ""
        for part in (x.strip() for x in rest.split(" — ")):
            if part.startswith("aka "):
                aliases = [a.strip() for a in part[4:].split(",") if a.strip()]
            elif part:
                meaning = part
        out[term] = {"aliases": aliases, "meaning": meaning, "page": page}
    return out


def save_glossary(g: dict[str, dict]) -> None:
    lines = []
    for term in sorted(g, key=str.lower):
        e = g[term]
        line = f"- **{term}**"
        if e["aliases"]:
            line += " — aka " + ", ".join(a.replace(",", " ") for a in e["aliases"])
        if e["meaning"]:
            line += " — " + e["meaning"].replace(" — ", " - ").replace("→", "->")
        if e["page"]:
            line += f" → [[{e['page']}]]"
        lines.append(line)
    meta = {"type": "glossary", "summary": "Canonical names for teams, systems, terms and people, and how they get mis-heard.",
            "updated": date.today().isoformat()}
    body = ("# Glossary\n\n_Maintained by kb.py from meetings; edit freely. One term per line; \"aka\" lists "
            "abbreviations and how speech-to-text mis-hears it._\n\n" + "\n".join(lines) + "\n")
    GLOSSARY.write_text(join_frontmatter(meta, body))


def merge_glossary(entries: list[dict]) -> int:
    """Fold {term, aliases, meaning, page} entries into the glossary. Returns how many entries changed."""
    g = load_glossary()
    amap = alias_map(g)
    changed = 0
    for e in entries:
        term = clean_item(e.get("term", "")).replace("*", "").strip()
        if not term or len(term) > 60:
            continue
        canon = amap.get(term.lower(), term)
        cur = g.setdefault(canon, {"aliases": [], "meaning": "", "page": ""})
        before = json.dumps(cur, sort_keys=True)
        have = {canon.lower(), *(a.lower() for a in cur["aliases"])}
        for a in [term, *e.get("aliases", [])]:
            a = clean_item(a).replace("*", "").strip()
            if a and a.lower() not in have and len(a) <= 40:
                cur["aliases"].append(a)
                have.add(a.lower())
        if not cur["meaning"] and e.get("meaning", "").strip():
            cur["meaning"] = e["meaning"].strip()[:120]
        page = e.get("page", "").strip().strip("/").removesuffix(".md")
        if page and not cur["page"] and page.split("/")[0] in KINDS and (WIKI / f"{page}.md").exists():
            cur["page"] = page
        if json.dumps(cur, sort_keys=True) != before:
            changed += 1
            for a in cur["aliases"]:
                amap[a.lower()] = canon
    if changed:
        save_glossary(g)
    return changed


def alias_map(g: dict[str, dict] | None = None) -> dict[str, str]:
    """lowercase alias or term -> canonical term."""
    g = load_glossary() if g is None else g
    out = {}
    for term, e in g.items():
        out[term.lower()] = term
        for a in e["aliases"]:
            out.setdefault(a.lower(), term)
    return out


def _mentions(name: str, text_lower: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", text_lower) is not None


def expand_aliases(text: str, amap: dict[str, str]) -> str:
    """Append the canonical term for every alias found, so "CP&R" also searches as "CPR"."""
    low = text.lower()
    extra = {term for alias, term in amap.items() if alias != term.lower() and _mentions(alias, low)}
    return text + (" " + " ".join(sorted(extra)) if extra else "")


# --------------------------------------------------------------------------- lookup (BM25, no embeddings)

_SEARCH: dict = {"sig": None}


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9'&]*", text.lower()) if len(w) > 1 and w not in STOPWORDS]


def _search_index() -> dict:
    """Per-page term counts for BM25, rebuilt only when a wiki file changes."""
    files = [p for p in wiki_pages() if rel(p) not in SPECIAL]
    stamps = [p.stat().st_mtime for p in files] + ([GLOSSARY.stat().st_mtime] if GLOSSARY.exists() else [])
    sig = (len(files), max(stamps, default=0.0))
    if _SEARCH["sig"] == sig:
        return _SEARCH
    g = load_glossary()
    names_of: dict[str, list[str]] = {}
    for term, e in g.items():
        if e["page"]:
            names_of.setdefault(e["page"], []).extend([term, *e["aliases"]])
    docs = []
    for p in files:
        r = rel(p)
        kind = r.split("/")[0]
        meta, body = split_frontmatter(p.read_text())
        title = title_of(body, p.stem)
        text = "\n".join(c for h, c in parse_page(body)[1] if h != "Log") if kind in KINDS else body
        names = [title, *names_of.get(r, [])]
        toks = _tokens(" ".join([title, r.split("/")[-1].replace("-", " "), meta.get("summary", ""), *names, text]))
        docs.append({"path": r, "kind": kind, "title": title, "body": body, "updated": meta.get("updated", ""),
                     "names": sorted({n.lower() for n in names if len(n) > 2}), "tf": Counter(toks), "len": len(toks),
                     "mentions": body.count("[[meetings/")})
    df: Counter = Counter()
    for d in docs:
        df.update(d["tf"].keys())
    _SEARCH.update(sig=sig, docs=docs, df=df, n=len(docs), alias=alias_map(g), hint=None,
                   avg=(sum(d["len"] for d in docs) / len(docs)) if docs else 1.0)
    return _SEARCH


def _excerpt(d: dict, per_page: int) -> str:
    body = d["body"].strip()
    if d["kind"] in KINDS:
        head, secs = parse_page(body)
        text = "\n\n".join([head, *(f"## {h}\n{c}" for h, c in secs if h != "Log" and c.strip())])
        log = bullets(get_section(body, "Log"))
        if log and len(text) < per_page - 300:
            text += "\n\n## Log (latest)\n" + as_list(log[-4:])
    else:
        text = body
    return text if len(text) <= per_page else text[:per_page].rsplit("\n", 1)[0] + "\n…"


def find_pages(query: str, k: int = 5, per_page: int = 1500, context: str = "",
               kinds: tuple[str, ...] | None = None) -> list[tuple[str, str]]:
    """Pages for a query: BM25 over each page's title, summary, aliases and current content, with aliases expanded
    (CP&R -> CPR) and a boost for pages whose name appears in the query, or in `context` (e.g. recent transcript)."""
    s = _search_index()
    if not s["docs"]:
        return []
    q_text = expand_aliases(query, s["alias"]).lower()
    ctx = expand_aliases(context, s["alias"]).lower() if context else ""
    q = Counter(_tokens(q_text))
    if not q:
        return []
    k1, b, today = 1.4, 0.75, date.today()
    scored = []
    for d in s["docs"]:
        if kinds and d["kind"] not in kinds:
            continue
        score = 0.0
        for w, qn in q.items():
            f = d["tf"].get(w)
            if f:
                idf = math.log(1 + (s["n"] - s["df"][w] + 0.5) / (s["df"][w] + 0.5))
                score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * d["len"] / s["avg"])) * (1 + math.log(qn))
        if any(_mentions(n, q_text) for n in d["names"]):
            score += 6
        elif ctx and any(_mentions(n, ctx) for n in d["names"]):
            score += 3
        if score <= 0:
            continue
        score *= 1.15 if d["kind"] in KINDS else 0.85  # people/project/topic pages hold the distilled picture
        try:
            if (today - date.fromisoformat(d["updated"])).days <= 14:
                score += 0.5
        except ValueError:
            pass
        scored.append((score, d["path"], d))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [(d["path"], _excerpt(d, per_page)) for _, _, d in scored[:k]]


def relevant_pages(query: str, k: int = 3, per_page: int = 1500) -> list[tuple[str, str]]:
    return find_pages(query, k=k, per_page=per_page)


def mentioned_pages(text: str, kinds: tuple[str, ...] = KINDS) -> list[str]:
    """Pages whose title or an alias is said in the text (e.g. the people named in a transcript)."""
    s = _search_index()
    low = expand_aliases(text, s["alias"]).lower()
    return [d["path"] for d in s["docs"] if d["kind"] in kinds and any(_mentions(n, low) for n in d["names"])]


def hint_terms(limit: int = 160) -> str:
    """Names and terms for the speech-to-text prompt so they get spelled right: glossary terms and people,
    most-mentioned first. Only names (whisper repeats its prompt, so never sentences)."""
    s = _search_index()
    if s.get("hint") is None:
        weight = {d["path"]: d["mentions"] for d in s["docs"]}
        cands = {d["title"]: weight[d["path"]] for d in s["docs"] if d["kind"] == "people"}
        for term, e in load_glossary().items():
            cands[term] = max(cands.get(term, 0), weight.get(e["page"], 1))
        out, size = [], 0
        for name, _ in sorted(cands.items(), key=lambda x: (-x[1], x[0])):
            name = re.sub(r"\s*\(.*?\)", "", name).strip()
            if not name or name.lower() in (x.lower() for x in out) or size + len(name) + 2 > limit:
                continue
            out.append(name)
            size += len(name) + 2
        s["hint"] = ", ".join(out)
    return s["hint"]


# --------------------------------------------------------------------------- open threads


def load_threads() -> list[dict]:
    """Lines like `- [ ] t7 · Jennifer → Milan · confirm the claim window · [[projects/x]] · since [[meetings/id]]`."""
    out = []
    if not THREADS.exists():
        return out
    for line in split_frontmatter(THREADS.read_text())[1].splitlines():
        m = re.match(r"- \[([ xX])\] (?:(t\d+) · )?(.+)$", line.strip())
        if not m:
            continue
        t = {"id": m.group(2) or "", "done": m.group(1) != " ", "who": "", "text": "", "project": "", "since": "",
             "closed": ""}
        for i, part in enumerate(x.strip() for x in m.group(3).split(" · ")):
            link = re.fullmatch(r"\[\[([^\]|#]+)[^\]]*\]\]", part)
            if part.startswith("since "):
                t["since"] = part[6:].strip().removeprefix("[[meetings/").rstrip("]")
            elif part.startswith("closed "):
                t["closed"] = part[7:].strip()
            elif link:
                t["project"] = link.group(1).strip()
            elif i == 0 and "→" in part:
                t["who"] = part
            else:
                t["text"] = f"{t['text']} · {part}" if t["text"] else part
        out.append(t)
    return out


def thread_line(t: dict) -> str:
    parts = [p for p in (t["id"], t["who"], t["text"]) if p]
    if t["project"]:
        parts.append(f"[[{t['project']}]]")
    if t["since"]:
        parts.append(f"since [[meetings/{t['since']}]]")
    if t["closed"]:
        parts.append(f"closed {t['closed']}")
    return f"- [{'x' if t['done'] else ' '}] " + " · ".join(parts)


def save_threads(threads: list[dict]) -> None:
    n = max((int(t["id"][1:]) for t in threads if re.fullmatch(r"t\d+", t["id"])), default=0)
    for t in threads:
        if not t["id"]:  # added by hand in Obsidian
            n += 1
            t["id"] = f"t{n}"
    open_ = [t for t in threads if not t["done"]]
    closed = [t for t in threads if t["done"]][-60:]
    meta = {"type": "threads", "summary": "Open loops across meetings: who owes what to whom.",
            "updated": date.today().isoformat()}
    body = ["# Open threads", "", "_Maintained by kb.py from meetings. Tick a box to close one; add your own as "
            "`- [ ] owner → waiting on · item`._", "", "## Open", *map(thread_line, open_), "", "## Closed",
            *map(thread_line, reversed(closed))]
    THREADS.write_text(join_frontmatter(meta, "\n".join(body) + "\n"))
    sync_project_threads(open_)


def sync_project_threads(open_threads: list[dict]) -> None:
    """Copy each project's open threads into its "## Open threads" section."""
    by_project: dict[str, list[str]] = {}
    for t in open_threads:
        if t["project"].startswith("projects/"):
            by_project.setdefault(t["project"], []).append(" · ".join(p for p in (t["id"], t["who"], t["text"]) if p))
    for p in (WIKI / "projects").glob("*.md"):
        meta, body = split_frontmatter(p.read_text())
        head, secs = parse_page(body)
        sec = dict(secs)
        new = as_list(by_project.get(rel(p), []))
        if sec.get("Open threads", "").strip() == new.strip():
            continue
        sec["Open threads"] = new
        p.write_text(join_frontmatter(meta, render_entity(head, sec)))


def update_threads(new: list[dict], closed: list[dict], meeting_id: str) -> tuple[int, int]:
    threads = load_threads()
    by_id = {t["id"]: t for t in threads}
    n_closed = 0
    for c in closed:
        t = by_id.get(str(c.get("id", "")).strip())
        if t and not t["done"]:
            t["done"] = True
            t["closed"] = f"[[meetings/{meeting_id}]]: {clean_item(c.get('how', '')).replace(' · ', ', ')}".rstrip(": ")
            n_closed += 1
    open_norm = {norm(t["text"]) for t in threads if not t["done"]}
    n = max((int(t["id"][1:]) for t in threads if re.fullmatch(r"t\d+", t["id"])), default=0)
    added = 0
    for x in new:
        text = clean_item(x.get("text", "")).replace(" · ", ", ")
        if not text or norm(text) in open_norm:
            continue
        owner, waiting = clean_item(x.get("owner", "")) or "?", clean_item(x.get("waiting_on", ""))
        project = x.get("project", "").strip().strip("/").removesuffix(".md")
        n += 1
        added += 1
        open_norm.add(norm(text))
        threads.append({"id": f"t{n}", "done": False, "who": f"{owner} → {waiting}" if waiting else f"{owner} →",
                        "text": text, "project": project if (WIKI / f"{project}.md").exists() else "",
                        "since": meeting_id, "closed": ""})
    if added or n_closed or THREADS.exists():
        save_threads(threads)
    return added, n_closed


def related_threads(paths: list[str], text: str = "", limit: int = 15) -> list[dict]:
    """Open threads tied to these pages: their project is one of them, or they name one of the people."""
    names = [page_title(p).lower() for p in paths if p.startswith("people/")]
    low = text.lower()
    out = []
    for t in load_threads():
        if t["done"]:
            continue
        hay = f"{t['who']} {t['text']}".lower()
        if t["project"] in paths or any(_mentions(n, hay) for n in names) or (
                t["project"] and _mentions(page_title(t["project"]).lower(), low)):
            out.append(t)
    return out[-limit:]


def note_people(meta: dict) -> list[str]:
    """The Persons a Meeting note records as having spoken (its `people:` frontmatter)."""
    return [p.strip() for p in meta.get("people", "").split(",") if p.strip()]


def last_meeting_with(paths: list[str], exclude: str = "") -> tuple[str, str] | None:
    """The newest compiled meeting note where any of these people spoke, or (for notes from before speakers were
    recorded) that links to any of these pages."""
    wanted, links = set(paths), [f"[[{p}" for p in paths]
    for p in sorted((WIKI / "meetings").glob("*.md"), reverse=True):
        if p.stem == exclude:
            continue
        meta, body = split_frontmatter(p.read_text())
        if wanted & set(note_people(meta)) or any(l in body for l in links):
            return p.stem, body.split("\n## Mentioned")[0].split("\n## Who spoke")[0].strip()
    return None


# --------------------------------------------------------------------------- writing


def save_raw(meeting_id: str, lines: list[str], context: str = "") -> Path:
    ensure_vault()
    day, _, clock = meeting_id.partition("_")  # 2026-10-21_12-56-00
    meta = {"type": "transcript", "date": f"{day} {clock[:5].replace('-', ':')}".strip(),
            "context": context.strip() or "(none)"}
    path = RAW / f"{meeting_id}.md"
    path.write_text(join_frontmatter(meta, "\n".join(lines) + "\n"))
    return path


def import_sessions() -> list[str]:
    ids = []
    for t in sorted(SESSIONS_DIR.glob("*/transcript.txt")):
        mid = t.parent.name
        if not (RAW / f"{mid}.md").exists():
            save_raw(mid, t.read_text().splitlines())
            ids.append(mid)
    return ids


# --------------------------------------------------------------------------- voices (who each Speaker N is)

VOICE_LINE = re.compile(r"^- Speaker ([1-9]\d*) → \[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
_voices_cache: tuple[int, dict[int, tuple[str, str]]] = (-1, {})


def voice_people() -> dict[int, tuple[str, str]]:
    """Voice number -> (page path, name) from wiki/voices.md, e.g. {3: ("people/priya", "Priya"), 1: ("me", "Milan")}.
    Re-read whenever the file changes, so edits made in Obsidian count straight away."""
    global _voices_cache
    try:
        mtime = VOICES.stat().st_mtime_ns
    except FileNotFoundError:
        return {}
    if mtime != _voices_cache[0]:
        out = {}
        for line in split_frontmatter(VOICES.read_text())[1].splitlines():
            if m := VOICE_LINE.match(line.strip()):
                path = m.group(2).strip().removesuffix(".md")
                out[int(m.group(1))] = (path, FIRST_NAME if path == "me" else page_title(path))
        _voices_cache = (mtime, out)
    return _voices_cache[1]


def save_voice_people(mapping: dict[int, str]) -> None:
    meta = {"type": "voices", "summary": "Which person each remembered voice in the transcripts is.",
            "updated": date.today().isoformat()}
    lines = [f"- Speaker {n} → [[{p}|{FIRST_NAME if p == 'me' else page_title(p)}]]" for n, p in sorted(mapping.items())]
    body = ("# Voices\n\nTranscripts label each remembered voice \"Speaker N\" and never change. This page says who "
            "each one is; the app writes it when you name a speaker, and you can fix a line here.\n\n"
            + "\n".join(lines) + "\n")
    VOICES.parent.mkdir(parents=True, exist_ok=True)
    VOICES.write_text(join_frontmatter(meta, body))


def person_for(name: str) -> str:
    """The page for a typed name: "me" for the user, an existing person page (by title, file name or glossary
    alias), or a new person page."""
    name = " ".join(name.split())
    low = name.lower()
    if low in {n.lower() for n in (FIRST_NAME, MY_NAME, *MY_ALIASES)}:
        return "me"
    canonical = alias_map().get(low, name).lower()
    for p in sorted((WIKI / "people").glob("*.md")):
        r = rel(p)
        if low in (page_title(r).lower(), p.stem) or canonical == page_title(r).lower() or slugify(name) == p.stem:
            return r
    if name.islower():  # typed quickly: "priya shah" -> "Priya Shah"; "McKenzie" or "DJ" stay as typed
        name = name.title()
    path = f"people/{slugify(name)}"
    f = WIKI / f"{path}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(join_frontmatter({"type": "person", "summary": "", "updated": date.today().isoformat()}, f"# {name}\n"))
    rebuild_index()
    return path


RECOMPILE = "needs_recompile"  # Meeting note frontmatter: why the note is out of date


def voice_meetings(number: int) -> list[str]:
    """Meetings whose Transcript has lines from Voice `number`."""
    pat = re.compile(rf"^\[\d+:\d\d\] Speaker {number}: ", re.M)
    return sorted(p.stem for p in RAW.glob("*.md") if pat.search(p.read_text()))


def mark_recompile(meeting_ids: list[str], reason: str) -> list[str]:
    """Flag these Meetings' notes (when they have one) as written before something they depend on changed.
    No model call: `compile` and the Meetings page's Recompile button pick them up."""
    marked = []
    for mid in meeting_ids:
        note = WIKI / "meetings" / f"{mid}.md"
        if not note.exists():
            continue
        meta, body = split_frontmatter(note.read_text())
        reasons = [r for r in meta.get(RECOMPILE, "").split("; ") if r]
        if reason not in reasons:
            meta[RECOMPILE] = "; ".join([*reasons, reason])
            note.write_text(join_frontmatter(meta, body))
        marked.append(mid)
    return marked


def stale_meetings() -> list[str]:
    """Compiled Meetings marked as needing a recompile."""
    return sorted(p.stem for p in (WIKI / "meetings").glob("*.md")
                  if split_frontmatter(p.read_text())[0].get(RECOMPILE))


REVIEW, REVIEW_MEETINGS = "needs_review", "review_meetings"  # Person page frontmatter after a Voice moved off it


def page_file(path_rel: str) -> Path:
    return WIKI / f"{path_rel}.md"


def flag_review(path_rel: str, reason: str, meeting_ids: list[str]) -> None:
    """A Voice was moved off this Person: facts on the page citing these Meetings may be someone else's.
    Recorded in its frontmatter for `lint`; nothing on the page is changed or deleted."""
    f = page_file(path_rel)
    if not meeting_ids or not f.exists():
        return
    meta, body = split_frontmatter(f.read_text())
    reasons = [r for r in meta.get(REVIEW, "").split("; ") if r]
    ids = [x.strip() for x in meta.get(REVIEW_MEETINGS, "").split(",") if x.strip()]
    meta[REVIEW] = "; ".join(dict.fromkeys([*reasons, reason]))
    meta[REVIEW_MEETINGS] = ", ".join(dict.fromkeys([*ids, *meeting_ids]))
    f.write_text(join_frontmatter(meta, body))


def review_report() -> list[dict]:
    """Pages flagged by flag_review, with every line on them that cites one of the affected Meetings."""
    out = []
    for p in [WIKI / "me.md", *sorted((WIKI / "people").glob("*.md"))]:
        if not p.exists():
            continue
        meta, body = split_frontmatter(p.read_text())
        if not meta.get(REVIEW):
            continue
        ids = [x.strip() for x in meta.get(REVIEW_MEETINGS, "").split(",") if x.strip()]
        lines = [l.strip() for l in body.splitlines() if any(f"[[meetings/{m}" in l for m in ids)]
        out.append({"page": rel(p), "reason": meta[REVIEW], "meetings": ids, "check": lines})
    return out


def list_people() -> list[dict]:
    """Names to offer when naming a Voice: the user first, then every person page by title."""
    people = [{"path": rel(p), "name": page_title(rel(p))} for p in (WIKI / "people").glob("*.md")]
    return [{"path": "me", "name": FIRST_NAME}, *sorted(people, key=lambda x: x["name"].lower())]


def name_voice(number: int, name: str) -> str:
    """Link Voice `number` to the Person called `name` (creating the page if needed). Returns the page path."""
    ensure_vault()
    path = person_for(name)
    mapping = {n: p for n, (p, _) in voice_people().items()}
    old = mapping.get(number)
    mapping[number] = path
    save_voice_people(mapping)
    if len(mapping) == 1:
        rebuild_index()  # first entry: index gets its Voices link
    if old != path:  # notes compiled while this Voice had another (or no) name are out of date
        who = FIRST_NAME if path == "me" else page_title(path)
        compiled = mark_recompile(voice_meetings(number), f"Speaker {number} is now {who}")
        if old:  # moved off a Person: what those Meetings put on their page may not be theirs
            flag_review(old, f"Speaker {number} was moved to {who}", compiled)
    return path


def speakers_in(labels) -> tuple[list[str], int]:
    """Who spoke, from line labels: (Person page paths, "me" included, in order of first line; how many distinct
    unnamed Voices). Old "Other" lines and unlabeled ones aren't anyone in particular, so they don't count."""
    people, unnamed, named = [], set(), voice_people()
    for label in labels:
        if label == speakers.ME:
            path = "me"
        elif (n := speakers.voice_number(label)) is not None:
            if n not in named:
                unnamed.add(n)
                continue
            path = named[n][0]
        else:
            continue
        if path not in people:
            people.append(path)
    return people, len(unnamed)


def transcript_speakers(transcript: str) -> tuple[list[str], int]:
    """speakers_in for a stored Transcript's text."""
    return speakers_in(speakers.parse(m.group(3))[0] for m in map(LINE.match, transcript.splitlines()) if m)


def _speaker_people() -> dict[int, tuple[str, bool]]:
    return {n: (who, path == "me") for n, (path, who) in voice_people().items()}


speakers.use_people(_speaker_people)


def rebuild_index() -> None:
    groups: dict[str, list[str]] = {k: [] for k in (*KINDS, "meetings", "weekly")}
    for p in wiki_pages():
        r = rel(p)
        kind = r.split("/")[0]
        if kind not in groups:
            continue
        meta, body = split_frontmatter(p.read_text())
        groups[kind].append(f"- [[{r}|{title_of(body, p.stem)}]] — {meta.get('summary', '')}")
    groups["meetings"].sort(reverse=True)
    groups["weekly"].sort(reverse=True)
    parts = ["# Index", "_Generated by `kb.py index`. One line per page._", "",
             "- [[me|Me]] — who I am, how I talk, what I think"]
    if GLOSSARY.exists():
        parts.append("- [[glossary|Glossary]] — canonical names of teams, systems and people, and their mis-hearings")
    if THREADS.exists():
        parts.append("- [[threads|Open threads]] — who owes what to whom, across meetings")
    if VOICES.exists():
        parts.append("- [[voices|Voices]] — which person each remembered voice in the transcripts is")
    for kind, lines in groups.items():
        if lines:
            parts += ["", f"## {kind.title()}", *(lines if kind == "meetings" else sorted(lines))]
    (WIKI / "index.md").write_text("\n".join(parts) + "\n")


def append_me_log(lines: list[str], heading: str = "") -> None:
    """Everything ever learned about the user, uncondensed. Not sent to the model."""
    if not ME_LOG.exists():
        meta = {"type": "me-log", "summary": f"Every fact about {FIRST_NAME} picked up from meetings, uncondensed."}
        ME_LOG.write_text(join_frontmatter(meta, f"# Learned about {FIRST_NAME}: full log\n\n_me.md's \"Learned from "
                                                 "meetings\" is the condensed version the copilot reads._\n"))
    text = ME_LOG.read_text().rstrip() + "\n"
    if heading:
        text += f"\n### {heading}\n"
    ME_LOG.write_text(text + "\n".join(lines) + "\n")


def set_me_learned(items: list[str]) -> None:
    """Replace me.md's "Learned from meetings" with a condensed list. Hand-written prose found there is archived
    to me-log.md first, so nothing the user wrote is lost."""
    p = WIKI / "me.md"
    meta, body = split_frontmatter(p.read_text())
    old = get_section(body, "Learned from meetings") or ""
    if any(line.strip() and not line.startswith("- ") for line in old.splitlines()):
        append_me_log(old.strip().splitlines(), heading=f"Archived from me.md on {date.today().isoformat()}")
    items = [clean_item(x) for x in items if clean_item(x)][:MAX_ME]
    body = replace_section(body, "Learned from meetings", as_list(items))
    p.write_text(join_frontmatter(meta, body) if meta else body)


def write_entity(path_rel: str, page: dict, meeting_id: str, replace: bool) -> None:
    """Fold what one meeting says about a page into it. replace=True: the model saw the page's full Now / Key facts
    / Decisions and returned updated versions, so they're swapped in. Otherwise new facts are merged without
    duplicates. Old "## Notes" bullets move to the Log, and every meeting adds one Log line."""
    kind = path_rel.split("/")[0]
    path = WIKI / f"{path_rel}.md"
    cite = f"([[meetings/{meeting_id}]])"
    new_page = not path.exists()
    if new_page:
        path.parent.mkdir(parents=True, exist_ok=True)
        meta, body = {"type": TYPES[kind], "summary": "", "updated": ""}, f"# {page['title'].strip()}\n"
    else:
        meta, body = split_frontmatter(path.read_text())
    head, secs = parse_page(body)
    sec = dict(secs)
    log = bullets(sec.pop("Log", "")) + legacy_log(sec.pop("Notes", ""))

    def items(key: str) -> list[str]:
        return [clean_item(x) for x in page.get(key, []) if clean_item(x)]

    def cited(xs: list[str]) -> list[str]:
        return [x if "[[meetings/" in x or "[[raw/" in x else f"{x} {cite}" for x in xs]

    now, facts, decisions = items("now")[:MAX_NOW], items("key_facts"), items("decisions")
    if replace or new_page:
        if now:
            sec["Now"] = as_list(now)
        if facts:  # an empty list would wipe the page: keep what's there instead
            sec["Key facts"] = as_list((cited(facts) if new_page else facts)[:MAX_FACTS])
        if kind == "projects" and decisions:
            sec["Decisions"] = as_list(cited(decisions) if new_page else decisions)
        if page.get("summary", "").strip():
            meta["summary"] = page["summary"].strip()
    else:
        if now and not bullets(sec.get("Now")):
            sec["Now"] = as_list(now)
        sec["Key facts"] = as_list(merge_unique(bullets(sec.get("Key facts")), cited(facts)))
        if kind == "projects" and decisions:
            sec["Decisions"] = as_list(merge_unique(bullets(sec.get("Decisions")), cited(decisions)))
        if not meta.get("summary") and page.get("summary", "").strip():
            meta["summary"] = page["summary"].strip()
    if kind == "projects":
        people = [p.strip().strip("/").removesuffix(".md") for p in page.get("people", [])]
        links = [f"[[{p}|{page_title(p)}]]" for p in people if p.startswith("people/") and (WIKI / f"{p}.md").exists()]
        if links:
            sec["People"] = as_list(merge_unique(bullets(sec.get("People")), links))
    note = clean_item(page.get("log", ""))
    if note:
        line = f"{meeting_id[:10]} · {note} {cite}"
        mine = [i for i, x in enumerate(log) if cite in x]
        if mine:  # a recompile: this meeting's own line is rewritten in place; every other line stays
            log[mine[0]] = line
            log = [x for i, x in enumerate(log) if i not in mine[1:]]
        else:
            log.append(line)
    if log:
        sec["Log"] = as_list(log)
    meta["updated"] = date.today().isoformat()
    path.write_text(join_frontmatter(meta, render_entity(head, sec)))


def legacy_log(notes: str | None) -> list[str]:
    """Old append-only "## Notes" bullets -> dated Log lines, so the history survives the new layout."""
    out = []
    for x in bullets(notes):
        m = re.search(r"\[\[meetings/(\d{4}-\d\d-\d\d)", x)
        out.append(f"{m.group(1)} · {x}" if m and not x.startswith(m.group(1)) else x)
    return sorted(out, key=lambda x: x[:10])


# --------------------------------------------------------------------------- compile (LLM)

COMPILE_SYSTEM = f"""You maintain {MY_NAME}'s personal meeting knowledge base: an Obsidian wiki of \
markdown pages about the people, projects and topics in their meetings. {FIRST_NAME} is the user. The wiki \
should get sharper with every meeting: each page holds one current picture of a person, project or topic, \
not a growing pile of notes.

You get one raw meeting transcript (from room audio: transcription errors, repeated fragments; \
{TRANSCRIPT_NOTE}), plus the wiki index, the glossary (canonical names and how they get mis-heard), the \
current content of the pages this meeting most likely touches, the open threads, and {FIRST_NAME}'s own page. \
Produce:

- title: short meeting title (e.g. "Payments sprint planning").
- summary: one line for the index.
- note: the meeting page body in markdown, without a top-level title. Sections: "## Summary" \
(3-8 bullets), "## Decisions", "## Action items" (owner and date when stated), "## Open questions", \
and "## {FIRST_NAME}'s part" (what {FIRST_NAME} said, was asked, or committed to; only what is \
clearly attributable). Skip empty sections. Link people, projects and topics with \
[[people/slug|Name]], [[projects/slug|Name]], [[topics/slug|Name]], using the same paths as `pages`.
- pages: the people, projects and topics this meeting taught something durable about.
  - path: "projects/<slug>" for the team's own work: a service, repo, team, process, migration, epic or \
investigation with owners, goals or a status. "topics/<slug>" for a tool, vendor product, technology, concept \
or practice used across projects, even a named one (Kafka, Dynatrace, BigQuery, code review). \
"people/<slug>" for a person. \
Reuse the exact path from the index or glossary when the page exists, and never make a second page for \
something the glossary lists under another name. Do not make a page for {FIRST_NAME}.
  - title: display name (the glossary's canonical spelling). summary: one line for the index describing the \
page as it stands after this meeting.
  - now: 2-{MAX_NOW} bullets: who or what this is and where it stands as of this meeting. For a page in \
<current_pages>, rewrite its Now so it is true after this meeting (keep what still holds, update what changed).
  - key_facts: durable facts worth remembering: role, ownership, how it works, numbers, deadlines, preferences. \
For a page in <current_pages>, return its full updated list: keep existing facts with their citations, merge \
duplicates, drop what this meeting proved outdated, and add new facts ending with ([[meetings/<this id>]]). \
At most {MAX_FACTS}. For any other page, only the new facts from this meeting.
  - decisions: projects only, otherwise empty. For a page in <current_pages>, the full updated list; else only \
new ones. Each ends with its meeting citation.
  - people: projects only, otherwise empty: "people/<slug>" paths of the people working on it.
  - log: one line: what this meeting added about the page.
- me_new: durable facts about {FIRST_NAME} from this meeting (role, work, views, commitments, how they speak). \
Only clearly attributable ones; empty is fine.
- me_learned: only when me_new adds something worth keeping: the full rewritten "Learned from meetings" list \
for {FIRST_NAME}'s page: at most {MAX_ME} short bullets merging the current list with the new facts, without \
repeating what the hand-written sections of the page already say. Otherwise empty (the page stays as it is).
- glossary: names worth one canonical spelling (teams, systems, products, acronyms, people), with aliases = \
abbreviations and the mis-transcriptions seen in this transcript (e.g. "CPNR", "C P and R" for "CPR"), \
meaning in a few words, page = its wiki path or "". Only entries that are new or gained an alias.
- threads_new: commitments and open loops someone has to close later: what {FIRST_NAME} owes, what others owe \
{FIRST_NAME} or the team, questions left open. owner: who must act ("{FIRST_NAME}" for the user). \
waiting_on: who is waiting ("" when nobody in particular). text: the concrete item, with any deadline. \
project: its project path or "". Skip anything already in <open_threads>, and skip trivia.
- threads_closed: ids from <open_threads> this meeting resolved, and how.

Write only what the transcript supports. Infer names and terms charitably from mis-transcriptions \
when the context makes it obvious (e.g. "Mellem" -> {FIRST_NAME}). If the meeting is pure small \
talk, keep it brief and return few or no pages."""

_STRS = {"type": "array", "items": {"type": "string"}}


def _obj(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


GLOSSARY_ITEMS = {"type": "array", "items": _obj(term={"type": "string"}, aliases=_STRS, meaning={"type": "string"},
                                                  page={"type": "string"})}
COMPILE_SCHEMA = _obj(
    title={"type": "string"},
    summary={"type": "string"},
    note={"type": "string"},
    pages={"type": "array", "items": _obj(path={"type": "string"}, title={"type": "string"},
                                          summary={"type": "string"}, now=_STRS, key_facts=_STRS, decisions=_STRS,
                                          people=_STRS, log={"type": "string"})},
    me_new=_STRS,
    me_learned=_STRS,
    glossary=GLOSSARY_ITEMS,
    threads_new={"type": "array", "items": _obj(owner={"type": "string"}, waiting_on={"type": "string"},
                                                text={"type": "string"}, project={"type": "string"})},
    threads_closed={"type": "array", "items": _obj(id={"type": "string"}, how={"type": "string"})},
)


LINE = re.compile(r"^\[(\d+):(\d\d)\] (.*)$")


def plain(text: str) -> str:
    """[[topics/kafka|Kafka]] -> Kafka and **bold** -> bold, for display outside Obsidian."""
    text = re.sub(r"\[\[([^\]|]+\|)?([^\]]+)\]\]", lambda m: m.group(2).split("/")[-1], text)
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def meeting_sections(body: str) -> tuple[str, dict[str, list[str]]]:
    """Title and `## Heading` -> bullets from a compiled meeting note."""
    title, sections, cur = "", {}, None
    for line in body.splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith("## "):
            cur = sections.setdefault(line[3:].strip(), [])
        elif cur is not None and line.startswith("- "):
            cur.append(plain(line[2:].strip()))
    return title, sections


def list_meetings() -> list[dict]:
    """Past meetings for the dashboard, newest first: raw transcripts plus the compiled note when there is one."""
    out = []
    for raw in sorted(RAW.glob("*.md"), reverse=True):
        meta, body = split_frontmatter(raw.read_text())
        lines = [m for m in map(LINE.match, body.splitlines()) if m]
        if not lines:
            continue
        note = WIKI / "meetings" / raw.name
        title, summary = "", ""
        if note.exists():
            nmeta, nbody = split_frontmatter(note.read_text())
            title, sections = meeting_sections(nbody)
            summary = nmeta.get("summary") or " ".join(sections.get("Summary", [])[:3])
        out.append({"id": raw.stem, "date": meta.get("date", ""), "title": title,
                    "summary": summary or " ".join(speakers.parse(m.group(3))[1] for m in lines[:3])[:300],
                    "minutes": int(lines[-1].group(1)) + 1, "compiled": note.exists(),
                    "stale": bool(note.exists() and nmeta.get(RECOMPILE))})
    return out


def load_meeting(meeting_id: str) -> dict | None:
    raw = RAW / f"{meeting_id}.md"
    if not re.fullmatch(r"[\w-]+", meeting_id) or not raw.exists():
        return None
    meta, body = split_frontmatter(raw.read_text())
    lines = []
    for m in map(LINE.match, body.splitlines()):
        if m:
            speaker, text = speakers.parse(m.group(3))
            lines.append({"id": len(lines), "clock": f"{int(m.group(1)):02d}:{m.group(2)}",
                          "text": text, "speaker": speaker})
    note = WIKI / "meetings" / raw.name
    title, sections = meeting_sections(split_frontmatter(note.read_text())[1]) if note.exists() else ("", {})
    sections.pop("Mentioned", None)
    sections.pop("Who spoke", None)  # the page's Speakers strip shows this
    return {"id": meeting_id, "date": meta.get("date", ""), "title": title, "sections": sections, "lines": lines}


def pending_meetings() -> list[str]:
    return sorted(p.stem for p in RAW.glob("*.md") if not (WIKI / "meetings" / p.name).exists())


def compile_candidates(transcript: str, k: int = 8, cap: int = 2400) -> dict[str, tuple[str, bool]]:
    """Existing pages this meeting probably updates -> (their current Now/Key facts/Decisions, whether that text
    is complete). The model rewrites complete pages; for truncated ones its facts are merged instead."""
    paths = [p for p, _ in find_pages(transcript, k=k, kinds=KINDS)]
    paths += [p for p in mentioned_pages(transcript, kinds=("people",)) if p not in paths]
    out = {}
    for path in paths[:16]:
        body = split_frontmatter((WIKI / f"{path}.md").read_text())[1]
        text = "\n\n".join(f"## {h}\n{c.strip()}" for h, c in parse_page(body)[1]
                           if h in ("Now", "Key facts", "Decisions", "Notes") and c.strip())
        out[path] = (text[:cap], len(text) <= cap)
    return out


async def compile_meeting(meeting_id: str, model: str = MAIN_MODEL) -> str:
    ensure_vault()
    meta, transcript = split_frontmatter((RAW / f"{meeting_id}.md").read_text())
    spoke, unnamed = transcript_speakers(transcript)  # from the stored labels, not from the model
    transcript = speakers.render(transcript)  # "Speaker 3:" -> the Person's name, when known
    if len(transcript.split()) < 60:
        return f"{meeting_id}: skipped (too short)"
    current = compile_candidates(transcript)
    pages_block = "\n\n".join(f'<page path="{p}" title="{page_title(p)}">\n{text or "(empty)"}\n</page>'
                              for p, (text, _) in current.items()) or "(none)"
    open_threads = [t for t in load_threads() if not t["done"]][-60:]
    threads_block = "\n".join(thread_line(t) for t in open_threads) or "(none)"
    user = f"""<wiki_index>
{load_index()}
</wiki_index>

<glossary>
{load_glossary_text() or "(empty)"}
</glossary>

<me_page>
{load_me()}
</me_page>

<current_pages>
{pages_block}
</current_pages>

<open_threads>
{threads_block}
</open_threads>

<meeting id="{meeting_id}" date="{meta.get('date', '')}" user_context="{meta.get('context', '')}">
{transcript}
</meeting>"""
    data = await llm.complete(model, COMPILE_SYSTEM, user, schema=COMPILE_SCHEMA, max_tokens=16000)

    amap, g = alias_map(), load_glossary()
    mentioned = []
    for page in data["pages"]:
        kind, _, slug = page["path"].strip().strip("/").removesuffix(".md").partition("/")
        kind = kind.lower()
        if kind not in KINDS:
            continue
        path_rel = f"{kind}/{slugify(slug or page['title'])}"
        if not (WIKI / f"{path_rel}.md").exists():  # a known thing under another name: use its page
            term = amap.get(page["title"].strip().lower()) or amap.get(slug.replace("-", " ").lower())
            target = g.get(term, {}).get("page", "") if term else ""
            if target and target.split("/")[0] in KINDS and (WIKI / f"{target}.md").exists():
                path_rel = target
        write_entity(path_rel, page, meeting_id, replace=current.get(path_rel, ("", False))[1])
        mentioned.append(f"[[{path_rel}|{page['title'].strip()}]]")

    me_new = [clean_item(x) for x in data["me_new"] if clean_item(x)]
    if me_new and ME_LOG.exists():  # a recompile shouldn't log the same facts twice
        seen = {norm(x) for x in ME_LOG.read_text().splitlines()}
        me_new = [x for x in me_new if norm(f"- {x} ([[meetings/{meeting_id}]])") not in seen]
    if me_new:
        append_me_log([f"- {x} ([[meetings/{meeting_id}]])" for x in me_new])
    if data["me_learned"]:
        set_me_learned(data["me_learned"])
    merge_glossary(data["glossary"])
    added, closed = update_threads(data["threads_new"], data["threads_closed"], meeting_id)

    note_meta = {"type": "meeting", "date": meta.get("date", ""), "summary": data["summary"],
                 "people": ", ".join(spoke), "unnamed_speakers": unnamed, "updated": date.today().isoformat()}
    body = f"# {data['title']}\n\n{data['note'].strip()}\n"
    if mentioned:
        body += "\n## Mentioned\n" + " · ".join(dict.fromkeys(mentioned)) + "\n"
    if spoke or unnamed:
        who = [f"[[{p}|{FIRST_NAME if p == 'me' else page_title(p)}]]" for p in spoke]
        who += [f"{unnamed} unnamed speaker{'s' if unnamed > 1 else ''}"] if unnamed else []
        body += "\n## Who spoke\n" + " · ".join(who) + "\n"
    body += f"\nTranscript: [[raw/meetings/{meeting_id}]]\n"
    (WIKI / "meetings" / f"{meeting_id}.md").write_text(join_frontmatter(note_meta, body))
    rebuild_index()
    extra = f", threads +{added}/-{closed}" if added or closed else ""
    return f"{meeting_id}: {data['title']} ({len(mentioned)} pages{extra})"


async def compile_pending(on_progress=print, stale: bool = False) -> list[str]:
    """Compile every Meeting without a note; with stale=True, also rewrite the notes marked needs_recompile."""
    results = []
    for mid in pending_meetings() + (stale_meetings() if stale else []):
        try:
            results.append(await compile_meeting(mid))
        except Exception as e:
            results.append(f"{mid}: failed ({e})")
        on_progress(results[-1])
    return results


# --------------------------------------------------------------------------- weekly review


def week_of(d: date) -> date:
    """Monday of the week containing d."""
    return d - timedelta(days=d.weekday())


def meetings_in_week(monday: date) -> list[str]:
    days = {(monday + timedelta(days=i)).isoformat() for i in range(7)}
    return sorted(p.stem for p in RAW.glob("*.md") if p.stem[:10] in days)


def meeting_label(meeting_id: str) -> str:
    """2026-10-28_09-02-12 -> "Wed 09:02"."""
    try:
        return datetime.strptime(meeting_id, "%Y-%m-%d_%H-%M-%S").strftime("%a %H:%M")
    except ValueError:
        return meeting_id


def list_weeks() -> list[dict]:
    """Weeks that have meetings, newest first, and whether their review is written."""
    weeks: dict[str, int] = {}
    for p in RAW.glob("*.md"):
        try:
            monday = week_of(date.fromisoformat(p.stem[:10])).isoformat()
        except ValueError:
            continue
        weeks[monday] = weeks.get(monday, 0) + 1
    out = []
    for monday, n in sorted(weeks.items(), reverse=True):
        f = WEEKLY / f"{monday}.md"
        meta = split_frontmatter(f.read_text())[0] if f.exists() else {}
        out.append({"week": monday, "meetings": n, "written": f.exists(), "summary": meta.get("summary", ""),
                    "generated": meta.get("generated", "")})
    return out


def load_weekly(monday: str) -> dict | None:
    if not re.fullmatch(r"\d{4}-\d\d-\d\d", monday):
        return None
    days = {(date.fromisoformat(monday) + timedelta(days=i)).isoformat() for i in range(7)}
    meetings = [{"id": m["id"], "title": m["title"], "label": meeting_label(m["id"]), "minutes": m["minutes"]}
                for m in list_meetings() if m["id"][:10] in days]
    f = WEEKLY / f"{monday}.md"
    if not f.exists():
        return {"week": monday, "written": False, "meetings": meetings}
    meta, body = split_frontmatter(f.read_text())
    title, sections = meeting_sections(body)
    sections.pop("Meetings this week", None)
    return {"week": monday, "written": True, "title": title, "summary": plain(meta.get("summary", "")),
            "generated": meta.get("generated", ""), "sections": sections, "markdown": body, "meetings": meetings}


WEEKLY_SYSTEM = f"""You write the end-of-week review for {MY_NAME}, built from the notes and transcripts of \
the week's meetings. {FIRST_NAME} reads it on Friday to get fully up to date before next week: what they have \
to do, what was decided, what they need to know, and what to think about. Write to {FIRST_NAME} as "you".

Rules:
- Be specific: names, systems, tickets, numbers, dates. "Fix the replay end date for invoice data" beats \
"work on replay".
- Merge the same item across meetings and keep its latest state (if Tuesday's plan changed on Thursday, give \
Thursday's version and say it changed).
- my_todos: what {FIRST_NAME} must do, was asked to do, or volunteered for, including team tasks that clearly \
include them. Most urgent first; give the deadline when one was mentioned.
- waiting_on: what other people committed to that {FIRST_NAME}'s work depends on or should track. Name the person.
- decisions: what was agreed, and why if it was said.
- need_to_know: context worth knowing even with no action attached: changed requirements, process changes, \
who owns what, how a system actually behaves, news from leadership.
- consider: open questions, risks, disagreements and loose ends to think about or raise.
- next_week: what is coming up and what to prepare (meetings, deadlines, demos, releases).
- Each item cites the meeting ids it came from. Skip small talk, and leave a section empty rather than pad it.
- If a previous review is given, carry forward its to-dos and open questions that were not resolved this week.
- Open threads (if given) are loops tracked across meetings; use them for my_todos and waiting_on when still open.
- Meeting notes were written by an LLM from auto-transcribed audio, and raw transcripts contain transcription \
errors; read through obvious mis-hearings. {TRANSCRIPT_NOTE}"""

_WEEK_ITEMS = {"type": "array", "items": {
    "type": "object",
    "properties": {"text": {"type": "string"}, "meetings": {"type": "array", "items": {"type": "string"}}},
    "required": ["text", "meetings"], "additionalProperties": False}}
WEEKLY_SECTIONS = {
    "my_todos": "My to-dos", "waiting_on": "Waiting on others", "decisions": "Decisions",
    "need_to_know": "Need to know", "consider": "To think about", "next_week": "Coming up next week",
}
WEEKLY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}, **{k: _WEEK_ITEMS for k in WEEKLY_SECTIONS}},
    "required": ["summary", *WEEKLY_SECTIONS],
    "additionalProperties": False,
}


def _week_material(ids: list[str], max_raw_words: int = 8000) -> str:
    """The compiled note for each meeting, or its transcript when it has none yet."""
    parts = []
    for mid in ids:
        note = WIKI / "meetings" / f"{mid}.md"
        meta, text = split_frontmatter((note if note.exists() else RAW / f"{mid}.md").read_text())
        if not note.exists():
            text = speakers.render(text)
        words = text.split()
        if not note.exists():
            if len(words) < 60:
                continue
            if len(words) > max_raw_words:  # keep the start and the end, where plans and wrap-ups are
                text = " ".join(words[: max_raw_words // 2]) + "\n…\n" + " ".join(words[-max_raw_words // 2:])
        kind = "notes" if note.exists() else "transcript"
        parts.append(f'<meeting id="{mid}" when="{meeting_label(mid)}" source="{kind}" '
                     f'context="{meta.get("context", "")}">\n{plain(text).strip()}\n</meeting>')
    return "\n\n".join(parts)


async def write_weekly(monday: date, model: str = CHAT_MODEL) -> str:
    """One LLM call: the week's meetings -> wiki/weekly/<monday>.md. Returns a status line."""
    ids = meetings_in_week(monday)
    material = _week_material(ids)
    if not material:
        return f"week of {monday}: no meetings to review"
    prev = WEEKLY / f"{monday - timedelta(days=7)}.md"
    prev_block = (f"<previous_review>\n{split_frontmatter(prev.read_text())[1].strip()}\n</previous_review>\n\n"
                  if prev.exists() else "")
    threads = "\n".join(thread_line(t) for t in load_threads() if not t["done"])
    threads_block = f"<open_threads>\n{threads}\n</open_threads>\n\n" if threads else ""
    user = f"""<me_page>
{load_me()}
</me_page>

{prev_block}{threads_block}<week start="{monday}" today="{date.today().isoformat()}">
{material}
</week>"""
    data = await llm.complete(model, WEEKLY_SYSTEM, user, schema=WEEKLY_SCHEMA, max_tokens=12000, effort=CHAT_EFFORT)

    def cite(item: dict) -> str:
        links = [f"[[{'meetings' if (WIKI / 'meetings' / f'{m}.md').exists() else 'raw/meetings'}/{m}|"
                 f"{meeting_label(m)}]]" for m in item["meetings"] if m in ids]
        return f"- {item['text'].strip()}" + (f" ({', '.join(links)})" if links else "")

    body = [f"# Week of {monday.strftime('%B %-d')}", "", data["summary"].strip()]
    for key, heading in WEEKLY_SECTIONS.items():
        if data[key]:
            body += ["", f"## {heading}", *map(cite, data[key])]
    body += ["", "## Meetings this week"]
    for mid in ids:
        note = WIKI / "meetings" / f"{mid}.md"
        title = title_of(split_frontmatter(note.read_text())[1], mid) if note.exists() else "(not compiled yet)"
        body.append(f"- [[{'meetings' if note.exists() else 'raw/meetings'}/{mid}|{meeting_label(mid)}]] {title}")
    meta = {"type": "weekly", "week": monday.isoformat(), "summary": data["summary"].strip(),
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "updated": date.today().isoformat()}
    WEEKLY.mkdir(parents=True, exist_ok=True)
    (WEEKLY / f"{monday}.md").write_text(join_frontmatter(meta, "\n".join(body) + "\n"))
    rebuild_index()
    return f"week of {monday}: review written from {len(ids)} meeting(s)"


# --------------------------------------------------------------------------- lint


def lint() -> dict:
    pages = {rel(p): p for p in wiki_pages()}
    inbound = {r: 0 for r in pages}
    broken, overgrown, legacy = [], [], []
    for r, p in pages.items():
        text = p.read_text()
        for target in LINK.findall(text):
            target = target.strip()
            if target.startswith("raw/"):
                if not (VAULT_DIR / f"{target}.md").exists():
                    broken.append(f"{r} -> {target}")
            elif target in pages:
                if target != r:
                    inbound[target] += 1
            else:
                broken.append(f"{r} -> {target}")
        if r.split("/")[0] in KINDS:
            body = split_frontmatter(text)[1]
            if get_section(body, "Notes") is not None:
                legacy.append(r)
            if (len(bullets(get_section(body, "Key facts"))) > MAX_FACTS or len(bullets(get_section(body, "Now"))) > MAX_NOW
                    or len(bullets(get_section(body, "Notes"))) > 20):
                overgrown.append(r)
    orphans = [r for r, n in inbound.items() if n == 0 and r not in SPECIAL and not r.startswith(("meetings/", "weekly/"))]
    return {"pages": len(pages), "broken_links": broken, "orphans": orphans, "overgrown": overgrown,
            "old_layout": legacy, "needs_recompile": stale_meetings(), "check_after_voice_moved": review_report()}


# --------------------------------------------------------------------------- maintenance (backup, merge, rebuild)


def backup() -> Path:
    dst = VAULT_DIR / ".backup" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    shutil.copytree(WIKI, dst / "wiki")
    return dst


def rewrite_links(src: str, dst: str) -> int:
    """[[src…]] -> [[dst…]] in every wiki file, and src -> dst in Meeting notes' `people:` frontmatter."""
    pat = re.compile(r"\[\[" + re.escape(src) + r"(?=[\]|#])")
    people = re.compile(r"^people: (.*)$", re.M)

    def fix_people(m: re.Match) -> str:
        items = [dst if x.strip() == src else x.strip() for x in m.group(1).split(",") if x.strip()]
        return "people: " + ", ".join(dict.fromkeys(items))

    n = 0
    for p in WIKI.rglob("*.md"):
        text = p.read_text()
        new, k = pat.subn(f"[[{dst}", text)
        if text.startswith("---\n") and "\npeople: " in text:
            fixed = people.sub(fix_people, new, count=1)
            new, k = fixed, k + (fixed != new)
        if k:
            p.write_text(new)
            n += k
    return n


def merge_pages(src: str, dst: str) -> str:
    """Fold page src into dst (or move it there when dst doesn't exist), fix every link, and remember src's title
    as an alias of dst in the glossary."""
    s, d = WIKI / f"{src}.md", WIKI / f"{dst}.md"
    if src == dst or not s.exists() or dst.split("/")[0] not in KINDS:
        return f"skip {src} -> {dst}"
    smeta, sbody = split_frontmatter(s.read_text())
    stitle = title_of(sbody, s.stem)
    if not d.exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        smeta["type"] = TYPES[dst.split("/")[0]]
        d.write_text(join_frontmatter(smeta, sbody))
        action = "moved"
    else:
        dmeta, dbody = split_frontmatter(d.read_text())
        dhead, dsecs = parse_page(dbody)
        dsec, ssec = dict(dsecs), dict(parse_page(sbody)[1])
        for name in ("Key facts", "Decisions", "People", "Notes"):
            if bullets(ssec.get(name)):
                dsec[name] = as_list(merge_unique(bullets(dsec.get(name)), bullets(ssec[name])))
        if not bullets(dsec.get("Now")) and ssec.get("Now"):
            dsec["Now"] = ssec["Now"]
        if bullets(ssec.get("Log")):
            dsec["Log"] = as_list(sorted(bullets(dsec.get("Log")) + bullets(ssec["Log"]), key=lambda x: x[:10]))
        for name, content in ssec.items():
            if name not in dsec and name not in ("Now", "Log"):
                dsec[name] = content
        dmeta["updated"] = date.today().isoformat()
        d.write_text(join_frontmatter(dmeta, render_entity(dhead, dsec)))
        action = "merged"
    s.unlink()
    links = rewrite_links(src, dst)
    dtitle = page_title(dst)
    if stitle.lower() != dtitle.lower():
        merge_glossary([{"term": dtitle, "aliases": [stitle], "meaning": "", "page": dst}])
    return f"{action} {src} -> {dst} ({links} links updated)"


DEDUPE_SYSTEM = f"""You clean up the page list of {MY_NAME}'s meeting wiki. An LLM built it from auto-transcribed \
meetings, so abbreviations and mis-hearings created duplicate pages (e.g. "CPNR", "CP&R" and "CPR" for one team), \
and many named systems were filed as topics instead of projects. Given the index and the current glossary, return:

- merges: pages that are clearly the same person, project or topic. from: the path to remove; to: the path to \
keep (the best-named, most complete one). A part of a system is not a duplicate of the system. reason: a few words.
- moves: topics that are really projects, or the reverse. A project is the team's own work: a service, repo, \
team, process, migration, epic or investigation with owners, goals or a status. A topic is a tool, vendor \
product, technology or practice used across projects, even when it has a proper name (Kafka, Dynatrace, \
BigQuery, Jira, MCP, Okta itself): those stay topics. from: current path; to: new path ("projects/<slug>"). \
Only paths that exist in the index, and not pages you are already merging. When unsure, leave it.
- glossary: canonical names for the teams, systems, acronyms and people in the wiki: term (canonical spelling), \
aliases (abbreviations and the variant spellings or mis-hearings visible in titles and summaries), meaning \
(a few words), page (the path of the page that is about exactly this term, after your merges and moves; "" \
when no page is about it, even if a related page mentions it)."""

DEDUPE_SCHEMA = _obj(
    merges={"type": "array", "items": _obj(**{"from": {"type": "string"}}, to={"type": "string"},
                                           reason={"type": "string"})},
    moves={"type": "array", "items": _obj(**{"from": {"type": "string"}}, to={"type": "string"},
                                          reason={"type": "string"})},
    glossary=GLOSSARY_ITEMS,
)


DEDUPE_PLAN = VAULT_DIR / ".backup" / "dedupe-plan.json"  # dry run writes it; --apply uses it as reviewed


async def plan_dedupe(model: str = CHAT_MODEL) -> dict:
    user = f"<wiki_index>\n{load_index()}\n</wiki_index>\n\n<glossary>\n{load_glossary_text() or '(empty)'}\n</glossary>"
    return await llm.complete(model, DEDUPE_SYSTEM, user, schema=DEDUPE_SCHEMA, max_tokens=8000, effort=CHAT_EFFORT)


def apply_dedupe(plan: dict) -> list[str]:
    done: dict[str, str] = {}

    def resolve(path: str) -> str:
        path = path.strip().strip("/").removesuffix(".md")
        while path in done:
            path = done[path]
        return path

    out = []
    for m in [*plan["merges"], *plan["moves"]]:
        src, dst = resolve(m["from"]), resolve(m["to"])
        kind, _, slug = dst.partition("/")
        dst = f"{kind}/{slugify(slug)}"
        result = merge_pages(src, dst)
        out.append(result)
        if not result.startswith("skip"):
            done[src] = dst
    for e in plan["glossary"]:
        e["page"] = resolve(e.get("page", ""))
    n = merge_glossary(plan["glossary"])
    out.append(f"glossary: {n} entr{'y' if n == 1 else 'ies'} added or updated")
    save_threads(load_threads()) if THREADS.exists() else None
    rebuild_index()
    return out


REBUILD_SYSTEM = f"""You restructure one page of {MY_NAME}'s meeting wiki (people, projects, topics) into its new \
layout. So far the page was a pile of notes appended meeting by meeting: duplicates, outdated items, the same \
fact five times. Return:
- summary: one line for the index, describing the page as it stands now.
- now: 2-{MAX_NOW} bullets: who or what this is and where it stands now (newer meetings win; meeting ids are dates).
- key_facts: at most {MAX_FACTS} durable facts: duplicates merged, outdated ones dropped. Keep each fact's \
meeting citation like ([[meetings/<id>]]) exactly; a merged fact keeps the citations of the facts it merged.
- decisions: projects only (otherwise empty), each with its citation.
- people: projects only (otherwise empty): "people/<slug>" paths linked from the page.
Keep [[wikilinks]] that carry information. Only use what the page says."""

REBUILD_SCHEMA = _obj(summary={"type": "string"}, now=_STRS, key_facts=_STRS, decisions=_STRS, people=_STRS)


async def rebuild_page(page: str, model: str = MAIN_MODEL) -> str:
    """One call: an append-only page -> Now / Key facts / (Decisions, People) / Log. The old notes become the Log."""
    path = WIKI / f"{page}.md"
    meta, body = split_frontmatter(path.read_text())
    kind = page.split("/")[0]
    head, secs = parse_page(body)
    sec = dict(secs)
    data = await llm.complete(model, REBUILD_SYSTEM, f'<page path="{page}" kind="{TYPES[kind]}">\n{body}\n</page>',
                              schema=REBUILD_SCHEMA, max_tokens=4000)
    if not data["key_facts"] and bullets(sec.get("Notes")):
        return f"{page}: kept (model returned no facts)"
    log = bullets(sec.pop("Log", "")) + legacy_log(sec.pop("Notes", ""))
    sec["Now"] = as_list([clean_item(x) for x in data["now"]][:MAX_NOW])
    sec["Key facts"] = as_list([clean_item(x) for x in data["key_facts"]][:MAX_FACTS])
    if kind == "projects":
        if data["decisions"]:
            sec["Decisions"] = as_list([clean_item(x) for x in data["decisions"]])
        links = [f"[[{p}|{page_title(p)}]]" for p in (x.strip().removesuffix(".md") for x in data["people"])
                 if p.startswith("people/") and (WIKI / f"{p}.md").exists()]
        if links:
            sec["People"] = as_list(merge_unique(bullets(sec.get("People")), links))
    if log:
        sec["Log"] = as_list(sorted(log, key=lambda x: x[:10]))
    if data["summary"].strip():
        meta["summary"] = data["summary"].strip()
    meta["updated"] = date.today().isoformat()
    path.write_text(join_frontmatter(meta, render_entity(head, sec)))
    return f"{page}: {len(bullets(sec['Key facts']))} key facts, {len(log)} log lines"


ME_SYSTEM = f"""You condense the "Learned from meetings" part of {MY_NAME}'s own page in their meeting wiki. \
The copilot reads this page before every answer, so it must be short. Return learned: at most {MAX_ME} bullets \
with the most useful durable things about {FIRST_NAME} (role, responsibilities, current work, views, how they \
work and talk), newest information winning. Skip anything the hand-written sections already say."""


async def rebuild_me(model: str = MAIN_MODEL) -> str:
    body = split_frontmatter((WIKI / "me.md").read_text())[1]
    learned = get_section(body, "Learned from meetings") or ""
    if len(bullets(learned)) <= MAX_ME and not any(x.strip() and not x.startswith("- ") for x in learned.splitlines()):
        return "me: already condensed"
    data = await llm.complete(model, ME_SYSTEM, f"<me_page>\n{body}\n</me_page>",
                              schema=_obj(learned=_STRS), max_tokens=2000)
    if not data["learned"]:
        return "me: kept (model returned nothing)"
    if learned.strip() and all(x.startswith("- ") or not x.strip() for x in learned.splitlines()):
        append_me_log(learned.strip().splitlines(), heading=f"Before condensing, {date.today().isoformat()}")
    before = len(body.split())
    set_me_learned(data["learned"])
    after = len(split_frontmatter((WIKI / "me.md").read_text())[1].split())
    return f"me: {before} -> {after} words (old section kept in me-log.md)"


async def rebuild_pages(pages: list[str], include_me: bool = True, on_progress=print) -> list[str]:
    sem = asyncio.Semaphore(6)

    async def one(page: str) -> str:
        async with sem:
            try:
                r = await rebuild_page(page)
            except Exception as e:
                r = f"{page}: failed ({e})"
            on_progress(r)
            return r

    results = list(await asyncio.gather(*(one(p) for p in pages)))
    if include_me:
        results.append(await rebuild_me())
        on_progress(results[-1])
    if THREADS.exists():
        save_threads(load_threads())  # refill each project's Open threads
    rebuild_index()
    return results


# --------------------------------------------------------------------------- CLI


def main() -> None:
    p = argparse.ArgumentParser(description="Meeting knowledge base")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("import-sessions")
    c = sub.add_parser("compile")
    c.add_argument("meeting", nargs="?", help="meeting id (default: every one not yet compiled, plus those marked "
                                              "needs_recompile)")
    sub.add_parser("index")
    lp = sub.add_parser("lint")
    lp.add_argument("--fix", action="store_true", help="rewrite overgrown pages into the Now / Key facts layout (LLM)")
    wp = sub.add_parser("weekly")
    wp.add_argument("date", nargs="?", help="any day in the week, YYYY-MM-DD (default: this week)")
    sub.add_parser("backup")
    dp = sub.add_parser("dedupe")
    dp.add_argument("--apply", action="store_true", help="merge/move the pages and write the glossary")
    mp = sub.add_parser("merge")
    mp.add_argument("src")
    mp.add_argument("dst")
    rp = sub.add_parser("rebuild-pages")
    rp.add_argument("--all", action="store_true", help="also pages already in the new layout")
    rp.add_argument("--no-me", action="store_true", help="leave me.md alone")
    rp.add_argument("pages", nargs="*", help="only these pages, e.g. people/jane-doe")
    fp = sub.add_parser("find")
    fp.add_argument("query", nargs="+")
    sub.add_parser("threads")
    args = p.parse_args()

    ensure_vault()
    if args.cmd == "import-sessions":
        ids = import_sessions()
        print(f"Imported {len(ids)} meeting(s) into {RAW}")
    elif args.cmd == "compile":
        if args.meeting:
            print(asyncio.run(compile_meeting(args.meeting)))
        else:
            stale = stale_meetings()
            print(f"Compiling {len(pending_meetings())} new meeting(s) and recompiling {len(stale)} marked one(s) "
                  f"with {MAIN_MODEL}…")
            asyncio.run(compile_pending(stale=True))
        print(f"Usage: {json.dumps(llm.usage_summary())}")
    elif args.cmd == "index":
        rebuild_index()
        print(f"Wrote {WIKI / 'index.md'}")
    elif args.cmd == "lint":
        report = lint()
        print(json.dumps(report, indent=2))
        if args.fix and report["overgrown"]:
            print(f"Backup: {backup()}")
            asyncio.run(rebuild_pages(report["overgrown"], include_me=False))
    elif args.cmd == "weekly":
        day = date.fromisoformat(args.date) if args.date else date.today()
        print(asyncio.run(write_weekly(week_of(day))))
        print(f"Usage: {json.dumps(llm.usage_summary())}")
    elif args.cmd == "backup":
        print(f"Backup: {backup()}")
    elif args.cmd == "dedupe":
        if args.apply and DEDUPE_PLAN.exists():
            plan = json.loads(DEDUPE_PLAN.read_text())
            print(f"Applying the reviewed plan in {DEDUPE_PLAN}")
        else:
            plan = asyncio.run(plan_dedupe())
        for key in ("merges", "moves"):
            for m in plan[key]:
                print(f"{key[:-1]:6s} {m['from']} -> {m['to']}   ({m['reason']})")
        for e in plan["glossary"]:
            print(f"term   {e['term']}" + (f"  aka {', '.join(e['aliases'])}" if e["aliases"] else "")
                  + (f"  -> {e['page']}" if e["page"] else ""))
        if args.apply:
            print(f"Backup: {backup()}")
            for line in apply_dedupe(plan):
                print(line)
            DEDUPE_PLAN.unlink(missing_ok=True)
        else:
            DEDUPE_PLAN.parent.mkdir(parents=True, exist_ok=True)
            DEDUPE_PLAN.write_text(json.dumps(plan, indent=2))
            print(f"\nDry run: nothing changed. The plan is saved in {DEDUPE_PLAN}; edit it (delete lines you "
                  "disagree with) and run `kb.py dedupe --apply` to apply exactly that.")
        print(f"Usage: {json.dumps(llm.usage_summary())}")
    elif args.cmd == "merge":
        print(f"Backup: {backup()}")
        print(merge_pages(args.src.removesuffix(".md"), args.dst.removesuffix(".md")))
        rebuild_index()
    elif args.cmd == "rebuild-pages":
        pages = args.pages or [rel(x) for x in entity_pages()
                               if args.all or get_section(split_frontmatter(x.read_text())[1], "Notes") is not None]
        print(f"Backup: {backup()}")
        print(f"Rewriting {len(pages)} page(s) with {MAIN_MODEL}…")
        asyncio.run(rebuild_pages(pages, include_me=not args.no_me and not args.pages))
        print(f"Usage: {json.dumps(llm.usage_summary())}")
    elif args.cmd == "find":
        for path, text in find_pages(" ".join(args.query), k=5, per_page=300):
            print(f"== {path}\n{text}\n")
    elif args.cmd == "threads":
        for t in load_threads():
            if not t["done"]:
                print(thread_line(t))


if __name__ == "__main__":
    main()
