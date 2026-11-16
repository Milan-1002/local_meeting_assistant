"""The Meeting note module (layout, reading, marks) and the page-editing module under it."""

from __future__ import annotations

import os

import pytest

import meeting_notes
import pages
from conftest import kb

MID = "2025-12-04_10-00-00"

# The exact file compile wrote before the Meeting note module existed. Changing it changes what you see in
# Obsidian, so do it on purpose and update this together with the module.
GOLDEN = """---
type: meeting
date: 2025-12-04 10:00
summary: Demo moved to Thursday.
people: people/priya, me
unnamed_speakers: 1
updated: {today}
---
# Demo planning

## Summary
- Demo moved to Thursday.

## Decisions
- Ship Friday.

## Mentioned
[[projects/rollout|Rollout]]

## Who spoke
[[people/priya|Priya]] · [[me|Milan]] · 1 unnamed speaker

Transcript: [[raw/meetings/2025-12-04_10-00-00]]
"""


@pytest.fixture(autouse=True)
def vault():
    kb.ensure_vault()
    for d in ("people", "meetings"):
        for p in (pages.WIKI / d).glob("*.md"):
            p.unlink()
    (pages.WIKI / "people" / "priya.md").write_text("---\ntype: person\n---\n# Priya\n")


def write_sample(mid=MID, people=("people/priya", "me"), unnamed=1):
    meeting_notes.write(mid, title="Demo planning", date_="2025-12-04 10:00", summary="Demo moved to Thursday.",
                        note="## Summary\n- Demo moved to Thursday.\n\n## Decisions\n- Ship Friday.",
                        mentioned=[("projects/rollout", "Rollout"), ("projects/rollout", "Rollout")],
                        people=list(people), unnamed=unnamed)


def test_layout_on_disk_is_pinned():
    from datetime import date

    write_sample()
    assert pages.file(f"meetings/{MID}").read_text() == GOLDEN.format(today=date.today().isoformat())


def test_load_reads_back_what_write_wrote():
    write_sample()
    n = meeting_notes.load(MID)
    assert (n.title, n.date, n.summary, n.people, n.unnamed) == (
        "Demo planning", "2025-12-04 10:00", "Demo moved to Thursday.", ["people/priya", "me"], 1)
    assert list(n.sections) == ["Summary", "Decisions"]  # Mentioned and Who spoke hidden from the UI
    assert n.prompt_text == "# Demo planning\n\n## Summary\n- Demo moved to Thursday.\n\n## Decisions\n- Ship Friday."
    assert not n.needs_recompile and meeting_notes.exists(MID) and meeting_notes.load("nope") is None


def test_nobody_recorded_still_writes_an_empty_people_line():
    write_sample(people=(), unnamed=0)
    text = pages.file(f"meetings/{MID}").read_text()
    assert "\npeople: \n" in text and "## Who spoke" not in text
    assert meeting_notes.load(MID).people == []


def test_old_notes_are_read_leniently():
    pages.file("meetings/2025-10-01_09-00-00").write_text(
        "---\ntype: meeting\nsummary: \n---\n# Old one\n\n## Summary\n- First point.\n- Second.\n\n"
        "## Mentioned\n[[people/priya|Priya]]\n")
    n = meeting_notes.load("2025-10-01_09-00-00")
    assert (n.people, n.unnamed, n.recompile_reasons) == ([], 0, [])
    assert n.summary == "First point. Second."  # falls back to the Summary bullets
    assert meeting_notes.with_person(["people/priya"])[0] == "2025-10-01_09-00-00"  # found by its link


def test_recompile_marks_accumulate_once_and_writing_clears_them():
    write_sample()
    assert meeting_notes.mark_for_recompile([MID, "2099-01-01_00-00-00"], "Speaker 3 is now Priya") == [MID]
    meeting_notes.mark_for_recompile([MID], "Speaker 3 is now Priya")
    meeting_notes.mark_for_recompile([MID], "Speaker 4 is now Bob, again")  # commas are fine in reasons
    assert meeting_notes.load(MID).recompile_reasons == ["Speaker 3 is now Priya", "Speaker 4 is now Bob, again"]
    assert meeting_notes.needing_recompile() == [MID]
    write_sample()
    assert meeting_notes.needing_recompile() == []


def test_with_person_prefers_the_newest_and_honours_exclude():
    write_sample("2025-12-01_10-00-00")
    write_sample("2025-12-08_10-00-00")
    assert meeting_notes.with_person(["people/priya"])[0] == "2025-12-08_10-00-00"
    assert meeting_notes.with_person(["people/priya"], exclude="2025-12-08_10-00-00")[0] == "2025-12-01_10-00-00"
    assert meeting_notes.with_person(["people/nobody"]) is None


def test_rename_person():
    write_sample()
    assert meeting_notes.rename_person("people/priya", "people/priya-shah") == 1
    assert meeting_notes.load(MID).people == ["people/priya-shah", "me"]
    assert meeting_notes.rename_person("people/priya", "people/priya-shah") == 0


def test_edit_writes_only_when_something_changed():
    write_sample()
    f = pages.file(f"meetings/{MID}")
    os.utime(f, (1, 1))
    with pages.edit(f"meetings/{MID}") as p:
        p.set_list("people", p.get_list("people"))  # no real change
    assert f.stat().st_mtime == 1
    with pages.edit(f"meetings/{MID}") as p:
        p.set_list("people", [*p.get_list("people"), "people/bob", "me"])  # duplicates dropped
    assert pages.read(f"meetings/{MID}").get_list("people") == ["people/priya", "me", "people/bob"]
    with pages.edit("meetings/missing") as p:
        assert p is None
    assert not pages.file("meetings/missing").exists()


def test_empty_list_removes_the_key():
    p = pages.Page("x", {"needs_recompile": "a; b"})
    p.set_list("needs_recompile", [])
    assert "needs_recompile" not in p.meta
