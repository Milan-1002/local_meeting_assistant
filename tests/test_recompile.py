"""Naming after compile marks Meeting notes; only a click (or `kb.py compile`) recompiles them."""

from __future__ import annotations

import asyncio

import pytest

import meeting_notes
from conftest import copilot, fill, kb, wait_for

WORDS = " ".join(["the rollout plan needs another review before we ship it"] * 8)
MID = "2025-12-10_10-00-00"


@pytest.fixture(autouse=True)
def fresh_vault():
    kb.ensure_vault()
    kb.VOICES.unlink(missing_ok=True)
    kb.ME_LOG.unlink(missing_ok=True)
    for d in ("people", "meetings", "projects"):
        for p in (kb.WIKI / d).glob("*.md"):
            p.unlink()
    for p in kb.RAW.glob("*.md"):
        p.unlink()
    yield
    kb.VOICES.unlink(missing_ok=True)


def raw(mid: str, body: str) -> None:
    (kb.RAW / f"{mid}.md").write_text(f"---\ntype: transcript\ndate: {mid[:10]} 10:00\ncontext: (none)\n---\n{body}")


def compile_reply(log_note: str, me_new=()):
    """A stub compile reply that updates projects/rollout with one Log line."""
    def reply(schema):
        data = fill(schema)
        page = fill(schema["properties"]["pages"]["items"])
        page.update(path="projects/rollout", title="Rollout", log=log_note)
        data.update(pages=[page], me_new=list(me_new), me_learned=[])
        return data
    return reply


def note(mid=MID) -> meeting_notes.MeetingNote:
    return meeting_notes.load(mid)


def test_naming_marks_compiled_meetings_and_recompile_clears(app, stub_llm):
    raw(MID, f"[00:10] Speaker 3: {WORDS}\n")
    asyncio.run(kb.compile_meeting(MID))
    calls = len(stub_llm.calls)

    app.post("/voices/name", json={"voice": 3, "name": "Priya"})
    assert len(stub_llm.calls) == calls  # naming never calls the model
    assert note().recompile_reasons == ["Speaker 3 is now Priya"]
    assert meeting_notes.needing_recompile() == [MID]
    assert next(m for m in kb.list_meetings() if m["id"] == MID)["stale"]
    assert kb.lint()["needs_recompile"] == [MID]
    assert app.get("/meetings").json()[0]["stale"]

    assert app.post("/recompile").json() == {"ok": True, "meetings": 1}
    wait_for(lambda: not meeting_notes.needing_recompile())
    assert not note().needs_recompile
    assert note().people == ["people/priya"]
    assert app.post("/recompile").json() == {"ok": True, "meetings": 0}


def test_naming_marks_nothing_when_not_needed():
    raw(MID, f"[00:10] Speaker 3: {WORDS}\n")  # not compiled
    kb.name_voice(3, "Priya")
    assert meeting_notes.needing_recompile() == []

    raw("2025-12-11_10-00-00", f"[00:10] Speaker 4: {WORDS}\n")
    (kb.WIKI / "meetings" / "2025-12-11_10-00-00.md").write_text("---\ntype: meeting\n---\n# M\n")
    kb.name_voice(3, "Priya")  # same name again; Voice 3 isn't in that meeting anyway
    assert meeting_notes.needing_recompile() == []
    kb.name_voice(4, "Bob")
    kb.name_voice(4, "Bob")  # unchanged: no second reason
    assert note("2025-12-11_10-00-00").recompile_reasons == ["Speaker 4 is now Bob"]


def test_recompile_replaces_only_that_meetings_log_line(stub_llm):
    other = "2025-12-01_10-00-00"
    raw(other, f"[00:10] Speaker 9: {WORDS}\n")
    raw(MID, f"[00:10] Speaker 3: {WORDS}\n")
    stub_llm.reply = compile_reply("an older meeting's line")
    asyncio.run(kb.compile_meeting(other))
    stub_llm.reply = compile_reply("Speaker 3 raised the rollout risk")
    asyncio.run(kb.compile_meeting(MID))

    kb.name_voice(3, "Priya")
    stub_llm.reply = compile_reply("Priya raised the rollout risk")
    asyncio.run(kb.compile_pending(stale=True))

    log = kb.bullets(kb.get_section((kb.WIKI / "projects" / "rollout.md").read_text(), "Log"))
    assert log == [f"2025-12-01 · an older meeting's line ([[meetings/{other}]])",
                   f"2025-12-10 · Priya raised the rollout risk ([[meetings/{MID}]])"]


def test_end_meeting_path_leaves_marked_notes_alone(stub_llm):
    raw(MID, f"[00:10] Speaker 3: {WORDS}\n")
    asyncio.run(kb.compile_meeting(MID))
    kb.name_voice(3, "Priya")
    stub_llm.calls.clear()
    asyncio.run(kb.compile_pending())  # what End meeting runs
    assert stub_llm.calls == [] and meeting_notes.needing_recompile() == [MID]


def test_recompile_does_not_log_me_facts_twice(stub_llm):
    raw(MID, f"[00:10] Speaker 3: {WORDS}\n")
    stub_llm.reply = compile_reply("x", me_new=["Milan owns the rollout"])
    asyncio.run(kb.compile_meeting(MID))
    kb.name_voice(3, "Priya")
    asyncio.run(kb.compile_pending(stale=True))
    assert kb.ME_LOG.read_text().count("Milan owns the rollout") == 1
