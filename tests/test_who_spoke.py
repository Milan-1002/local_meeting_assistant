"""Meeting notes record who spoke (by code), and the Brief finds past meetings by it."""

from __future__ import annotations

import asyncio

import pytest

import meeting_notes
from conftest import copilot, kb, wait_for

WORDS = " ".join(["the rollout plan needs another review before we ship it"] * 8)


@pytest.fixture(autouse=True)
def fresh_vault():
    kb.ensure_vault()
    kb.VOICES.unlink(missing_ok=True)
    for d in ("people", "meetings"):
        for p in (kb.WIKI / d).glob("*.md"):
            p.unlink()
    for p in kb.RAW.glob("*.md"):
        p.unlink()
    yield
    kb.VOICES.unlink(missing_ok=True)


def raw(mid: str, body: str) -> None:
    (kb.RAW / f"{mid}.md").write_text(f"---\ntype: transcript\ndate: {mid[:10]} 10:00\ncontext: (none)\n---\n{body}")


def test_compile_records_who_spoke(stub_llm):
    kb.name_voice(3, "Priya")
    raw("2025-12-04_10-00-00", f"[00:10] Speaker 3: {WORDS}\n[00:20] Speaker 5: Me too.\n"
                               "[00:30] Milan: Agreed.\n[00:40] Other: old label\n[00:50] Speaker 5: Yes.\n")
    asyncio.run(kb.compile_meeting("2025-12-04_10-00-00"))
    note = meeting_notes.load("2025-12-04_10-00-00")
    assert note.people == ["people/priya", "me"] and note.unnamed == 1
    assert "Who spoke" not in note.sections and "Who spoke" not in note.prompt_text
    assert "Who spoke" not in kb.load_meeting("2025-12-04_10-00-00")["sections"]


def test_a_voice_named_as_me_counts_as_me():
    kb.name_voice(4, "Milan")
    assert kb.transcript_speakers("[00:01] Speaker 4: hi\n[00:02] Milan: hello\n") == (["me"], 0)


def test_finds_the_last_meeting_where_she_spoke_unmentioned(stub_llm):
    kb.name_voice(3, "Priya")
    raw("2025-12-05_10-00-00", f"[00:10] Speaker 3: {WORDS}\n")  # never says "Priya"
    asyncio.run(kb.compile_meeting("2025-12-05_10-00-00"))
    stub_llm.reply = None
    found = meeting_notes.with_person(["people/priya"])
    assert found and found[0] == "2025-12-05_10-00-00"
    assert "Who spoke" not in found[1]


def test_old_notes_still_found_by_mention():
    (kb.WIKI / "people" / "bob.md").write_text("---\ntype: person\n---\n# Bob\n")
    (kb.WIKI / "meetings" / "2025-11-01_10-00-00.md").write_text(
        "---\ntype: meeting\nsummary: x\n---\n# Old\n\nBob agreed.\n\n## Mentioned\n[[people/bob|Bob]]\n")
    assert meeting_notes.with_person(["people/bob"])[0] == "2025-11-01_10-00-00"


def test_brief_draws_on_the_last_meeting_with_who_is_speaking(app, stub_llm):
    kb.name_voice(3, "Priya")
    raw("2025-12-06_10-00-00", f"[00:10] Speaker 3: {WORDS}\n")
    asyncio.run(kb.compile_meeting("2025-12-06_10-00-00"))
    stub_llm.calls.clear()
    app.post("/segment", json={"text": "Shall we start with the agenda?", "voice": 3})
    wait_for(lambda: copilot.meeting.segments)
    app.post("/brief", json={})
    call = wait_for(lambda: next((c for c in stub_llm.calls if "<last_meeting_with_them" in c["user"]), None))
    assert '<last_meeting_with_them id="2025-12-06_10-00-00">' in call["user"]


def test_merging_people_fixes_who_spoke():
    kb.name_voice(3, "Pri")
    (kb.WIKI / "people" / "priya.md").write_text("---\ntype: person\n---\n# Priya\n")
    (kb.WIKI / "meetings" / "2025-12-07_10-00-00.md").write_text(
        "---\ntype: meeting\npeople: people/pri, me\n---\n# M\n\n## Who spoke\n[[people/pri|Pri]] · [[me|Milan]]\n")
    kb.merge_pages("people/pri", "people/priya")
    note = meeting_notes.load("2025-12-07_10-00-00")
    assert note.people == ["people/priya", "me"]
    assert "[[people/priya|Pri]]" in note.body
    assert kb.voice_people()[3][0] == "people/priya"
