"""Naming a Voice: the mapping page, names everywhere text is read, and Transcripts left as stored."""

from __future__ import annotations

import asyncio
import json
import os

import numpy as np
import pytest

import speakers
from conftest import copilot, kb, wait_for


@pytest.fixture(autouse=True)
def fresh_vault():
    kb.ensure_vault()
    kb.VOICES.unlink(missing_ok=True)
    for p in (kb.WIKI / "people").glob("*.md"):
        p.unlink()
    yield
    kb.VOICES.unlink(missing_ok=True)


def lines(app, *items):
    for text, voice in items:
        app.post("/segment", json={"text": text, "voice": voice} if voice else {"text": text, "speaker": "me"})
    wait_for(lambda: len(copilot.meeting.segments) == len(items))


def test_naming_shows_names_but_keeps_the_stored_transcript(app):
    lines(app, ("Can we move the demo to Thursday?", 3), ("Thursday works for me.", None))
    r = app.post("/voices/name", json={"voice": 3, "name": "Priya"}).json()
    assert r["ok"] and r["person"] == "people/priya"
    assert r["voices"] == {"s3": {"name": "Priya", "me": False, "person": "people/priya"}}

    stored = (copilot.session_dir / "transcript.txt").read_text()
    assert "Speaker 3: Can we move the demo to Thursday?" in stored and "Priya" not in stored

    app.post("/answer", json={"segment_id": 0})
    call = wait_for(lambda: app.llm.calls and app.llm.calls[-1])
    assert "Priya: Can we move the demo to Thursday?" in call["user"]
    assert "Speaker 3:" not in call["user"]


def test_naming_applies_to_past_meetings_and_compile_sees_names(stub_llm):
    kb.RAW.mkdir(parents=True, exist_ok=True)
    words = " ".join(["the rollout plan needs another review before we ship it"] * 8)
    (kb.RAW / "2025-12-03_10-00-00.md").write_text(
        "---\ntype: transcript\ndate: 2025-12-03 10:00\ncontext: (none)\n---\n"
        f"[00:10] Speaker 7: {words}\n[00:20] Milan: Agreed.\n")
    kb.name_voice(7, "Bob")
    m = kb.load_meeting("2025-12-03_10-00-00")
    assert m["lines"][0]["speaker"] == "s7"  # the UI resolves the name from the voices map
    assert speakers.display("s7") == "Bob"

    asyncio.run(kb.compile_meeting("2025-12-03_10-00-00"))
    compile_call = next(c for c in stub_llm.calls if "rollout plan" in c["user"])
    assert "Bob: the rollout plan" in compile_call["user"]
    raw = (kb.RAW / "2025-12-03_10-00-00.md").read_text()
    assert "Speaker 7:" in raw and "Bob" not in raw


def test_naming_creates_or_links_the_person_page():
    assert kb.name_voice(3, "Priya") == "people/priya"
    assert (kb.WIKI / "people" / "priya.md").exists()
    (kb.WIKI / "people" / "bob-stone.md").write_text("---\ntype: person\nsummary: QA lead\n---\n# Bob Stone\n")
    assert kb.name_voice(4, "bob stone") == "people/bob-stone"
    assert kb.page_title("people/bob-stone") == "Bob Stone"
    assert len(list((kb.WIKI / "people").glob("*.md"))) == 2


def test_two_voices_can_be_one_person():
    kb.name_voice(3, "Priya")
    kb.name_voice(9, "Priya")
    assert kb.voice_people() == {3: ("people/priya", "Priya"), 9: ("people/priya", "Priya")}
    assert speakers.display("s3") == speakers.display("s9") == "Priya"


def test_naming_a_speaker_as_me(app, tmp_path):
    from voice import VoiceID
    from voices import Registry, unit

    copilot.app.state.voice = VoiceID(Registry(tmp_path / "voices.json"))
    try:
        mine = unit(np.random.default_rng(1).normal(size=192))
        copilot.app.state.voice.registry.identify(mine, 3.0)  # heard as Voice 1 before being named
        lines(app, ("I can take that one.", 1))
        app.post("/voices/name", json={"voice": 1, "name": "milan"})
        assert speakers.is_me("s1") and speakers.display("s1") == "Milan"
        assert copilot.app.state.voice.registry.me == 1  # "Record my voice" now refreshes this Voice

        vid = copilot.app.state.voice
        vid.embed = lambda a: mine
        assert vid.label(np.zeros(16_000 * 3, dtype=np.float32))[0] == speakers.ME  # written "Milan:" from now on

        copilot.meeting.auto = True
        app.post("/segment", json={"text": "Milan, what do you think about this?", "voice": 1})
        app.post("/segment", json={"text": "Milan, what do you think about this?", "voice": 2})
        wait_for(lambda: 2 in copilot.meeting.flags)
        assert 1 not in copilot.meeting.flags
    finally:
        copilot.app.state.voice = None


def test_editing_voices_md_by_hand_is_picked_up():
    kb.name_voice(3, "Priya")
    assert speakers.display("s3") == "Priya"
    text = kb.VOICES.read_text().replace("Speaker 3 → [[people/priya|Priya]]", "Speaker 3 → [[people/carol|Carol]]")
    (kb.WIKI / "people" / "carol.md").write_text("---\ntype: person\n---\n# Carol\n")
    kb.VOICES.write_text(text)
    st = kb.VOICES.stat()
    os.utime(kb.VOICES, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # same-tick writes still count
    assert speakers.display("s3") == "Carol"


def test_naming_is_broadcast_without_a_config_reset(app):
    q: asyncio.Queue = asyncio.Queue()
    copilot.subscribers.add(q)
    try:
        app.post("/voices/name", json={"voice": 5, "name": "Dana"})
        kinds = []
        while not q.empty():
            kinds.append(json.loads(q.get_nowait())["kind"])
        assert "voices" in kinds and "config" not in kinds
    finally:
        copilot.subscribers.discard(q)
    events = app.get("/meetings").status_code  # app still serves after naming
    assert events == 200


def test_bad_names_are_refused(app):
    assert not app.post("/voices/name", json={"voice": 3, "name": "  "}).json()["ok"]
    assert not app.post("/voices/name", json={"voice": "x", "name": "Priya"}).json()["ok"]
    assert not kb.VOICES.exists()


def test_people_offered_for_naming(app):
    (kb.WIKI / "people" / "zoe.md").write_text("---\ntype: person\n---\n# Zoe Park\n")
    kb.name_voice(3, "priya")
    names = [p["name"] for p in app.get("/people").json()]
    assert names == ["Milan", "Priya", "Zoe Park"]  # typed lowercase, saved capitalised
