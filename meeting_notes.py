"""Meeting notes: the wiki page compile writes for each Meeting, and the one place that knows its layout.

    ---
    type: meeting
    date: 2026-11-14 10:00
    summary: one line
    people: people/priya, me          # who spoke, written by code from the Transcript's labels
    unnamed_speakers: 1
    needs_recompile: Speaker 3 is now Priya; …   # set when a naming makes the note out of date
    updated: 2026-11-15
    ---
    # Title

    (the model's note: ## Summary, ## Decisions, …)

    ## Mentioned
    [[projects/rollout|Rollout]] · …

    ## Who spoke
    [[people/priya|Priya]] · [[me|Milan]] · 1 unnamed speaker

    Transcript: [[raw/meetings/<id>]]

compile decides what the note says; this module decides how it is laid out and read back. Notes from
before who-spoke was recorded have no `people:` and are read as such; they are never rewritten here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import pages
from config import MY_NAME

FIRST_NAME = MY_NAME.split()[0]
HIDDEN = ("Mentioned", "Who spoke")  # shown elsewhere in the UI, and not useful as prompt text
RECOMPILE = "needs_recompile"


@dataclass
class MeetingNote:
    id: str
    title: str
    date: str
    summary: str
    people: list[str]  # page paths of who spoke, "me" included; [] for notes from before this was recorded
    unnamed: int
    recompile_reasons: list[str]
    sections: dict[str, list[str]] = field(default_factory=dict)  # for the dashboard, HIDDEN removed
    prompt_text: str = ""  # the note for a model to read: without HIDDEN or the Transcript link
    body: str = ""  # the whole note, for readers that want everything (the weekly review)

    @property
    def needs_recompile(self) -> bool:
        return bool(self.recompile_reasons)


def _path(meeting_id: str) -> str:
    return f"meetings/{meeting_id}"


def exists(meeting_id: str) -> bool:
    return pages.file(_path(meeting_id)).exists()


def _ids() -> list[str]:
    return sorted(p.stem for p in (pages.WIKI / "meetings").glob("*.md"))


def _without(body: str, headings: tuple[str, ...]) -> str:
    """The body with these `## ` sections and the trailing Transcript link cut out."""
    parts = re.split(r"(?m)^(?=## )", body)
    kept = [p for p in parts if not any(p.startswith(f"## {h}\n") or p.rstrip() == f"## {h}" for h in headings)]
    text = "".join(kept)
    return re.sub(r"(?m)^Transcript: \[\[raw/meetings/[^\]]*\]\]\s*$", "", text).strip()


def load(meeting_id: str) -> MeetingNote | None:
    page = pages.read(_path(meeting_id))
    if page is None:
        return None
    title, sections = pages.bullet_sections(page.body)
    for h in HIDDEN:
        sections.pop(h, None)
    try:
        unnamed = int(page.meta.get("unnamed_speakers", "0") or 0)
    except ValueError:
        unnamed = 0
    summary = page.meta.get("summary") or " ".join(sections.get("Summary", [])[:3])
    return MeetingNote(id=meeting_id, title=title, date=page.meta.get("date", ""), summary=summary,
                       people=page.get_list("people"), unnamed=unnamed,
                       recompile_reasons=page.get_list(RECOMPILE), sections=sections,
                       prompt_text=_without(page.body, HIDDEN), body=page.body)


def write(meeting_id: str, *, title: str, date_: str, summary: str, note: str,
          mentioned: list[tuple[str, str]], people: list[str], unnamed: int) -> None:
    """Write (or rewrite) a Meeting note. `mentioned` is [(page path, title)], `people` who spoke (page paths,
    "me" included). Rewriting clears any needs_recompile mark."""
    meta = {"type": "meeting", "date": date_, "summary": summary}
    page = pages.Page(_path(meeting_id), meta)
    page.set_list("people", people)
    meta["people"] = meta.get("people", "")  # always present, so "nobody recorded" differs from an old note
    meta["unnamed_speakers"] = unnamed
    meta["updated"] = date.today().isoformat()
    body = f"# {title}\n\n{note.strip()}\n"
    links = list(dict.fromkeys(f"[[{p}|{t.strip()}]]" for p, t in mentioned))
    if links:
        body += "\n## Mentioned\n" + " · ".join(links) + "\n"
    if people or unnamed:
        who = [f"[[{p}|{FIRST_NAME if p == 'me' else pages.title(p)}]]" for p in people]
        who += [f"{unnamed} unnamed speaker{'s' if unnamed > 1 else ''}"] if unnamed else []
        body += "\n## Who spoke\n" + " · ".join(who) + "\n"
    body += f"\nTranscript: [[raw/meetings/{meeting_id}]]\n"
    page.body = body
    pages.write(page)


def mark_for_recompile(meeting_ids: list[str], reason: str) -> list[str]:
    """Flag these Meetings' notes as written before something they depend on changed. Returns the ids that
    have a note (whether or not they were already marked). No model call; see needing_recompile."""
    marked = []
    for mid in meeting_ids:
        with pages.edit(_path(mid)) as page:
            if page is None:
                continue
            page.set_list(RECOMPILE, [*page.get_list(RECOMPILE), reason])
            marked.append(mid)
    return marked


def needing_recompile() -> list[str]:
    return [mid for mid in _ids() if (n := load(mid)) and n.needs_recompile]


def with_person(paths: list[str], exclude: str = "") -> tuple[str, str] | None:
    """(id, prompt text) of the newest note where any of these people spoke or, for notes from before who-spoke
    was recorded, that links to any of their pages."""
    wanted, links = set(paths), [f"[[{p}" for p in paths]
    for mid in reversed(_ids()):
        if mid == exclude:
            continue
        n = load(mid)
        if n and (wanted & set(n.people) or any(l in n.body for l in links)):
            return mid, n.prompt_text
    return None


def rename_person(src: str, dst: str) -> int:
    """After merging page src into dst: rewrite who-spoke entries. Returns how many notes changed."""
    changed = 0
    for mid in _ids():
        with pages.edit(_path(mid)) as page:
            people = page.get_list("people")
            if src in people:
                page.set_list("people", [dst if p == src else p for p in people])
                changed += 1
    return changed
