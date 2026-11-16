"""Speaker labels end to end: stored Transcript, UI payload, prompts, and reading old Transcripts back."""

from __future__ import annotations

import speakers
from conftest import copilot, kb, wait_for


def test_line_round_trip():
    for who in ("me", "them", ""):
        line = speakers.format_line("01:02", "hello there", who)
        assert speakers.parse(line.split("] ", 1)[1]) == (who, "hello there")
    assert speakers.format_line("01:02", "hi", "me") == "[01:02] Milan: hi"
    assert speakers.format_line("01:02", "hi", "them") == "[01:02] Other: hi"
    assert speakers.format_line("01:02", "hi", "nobody") == "[01:02] hi"


def test_segments_are_stored_with_labels(app):
    app.post("/segment", json={"text": "I think we ship Friday", "speaker": "me"})
    app.post("/segment", json={"text": "What do you think about the rollout?", "speaker": "them"})
    app.post("/segment", json={"text": "hard to hear this one"})
    app.post("/segment", json={"text": "made-up label", "speaker": "bob"})
    wait_for(lambda: len(copilot.meeting.segments) == 4)

    stored = (copilot.session_dir / "transcript.txt").read_text().splitlines()
    assert [line.split("] ", 1)[1] for line in stored] == [
        "Milan: I think we ship Friday",
        "Other: What do you think about the rollout?",
        "hard to hear this one",
        "made-up label",
    ]
    assert [s.speaker for s in copilot.meeting.segments] == ["me", "them", "", ""]
    payload = copilot.meeting.segments[1].payload("00:01")
    assert payload["speaker"] == "them" and payload["text"] == "What do you think about the rollout?"


def test_prompt_sees_labels_and_note(app):
    app.post("/segment", json={"text": "I think we ship Friday", "speaker": "me"})
    app.post("/segment", json={"text": "What do you think about the rollout?", "speaker": "them"})
    wait_for(lambda: len(copilot.meeting.segments) == 2)

    app.post("/answer", json={"segment_id": 1})
    call = wait_for(lambda: app.llm.calls and app.llm.calls[-1])
    assert "Milan: I think we ship Friday" in call["user"]
    assert "Other: What do you think about the rollout?" in call["user"]
    assert speakers.NOTE in call["system"]


def test_own_lines_are_never_flagged(app):
    copilot.meeting.auto = True
    app.post("/segment", json={"text": "Milan, what do you think about this?", "speaker": "me"})
    app.post("/segment", json={"text": "Milan, what do you think about this?", "speaker": "them"})
    wait_for(lambda: len(copilot.meeting.segments) == 2)
    wait_for(lambda: 1 in copilot.meeting.flags)
    assert 0 not in copilot.meeting.flags


def test_old_transcript_loads_with_speakers():
    kb.RAW.mkdir(parents=True, exist_ok=True)
    (kb.RAW / "2025-12-01_10-00-00.md").write_text(
        "---\ntype: transcript\ndate: 2025-12-01 10:00\ncontext: (none)\n---\n"
        "[00:10] Other: Can you take the release notes?\n"
        "[00:14] Milan: Sure, by Thursday.\n"
        "[00:20] some unlabeled line\n")
    m = kb.load_meeting("2025-12-01_10-00-00")
    assert [(l["speaker"], l["text"]) for l in m["lines"]] == [
        ("them", "Can you take the release notes?"),
        ("me", "Sure, by Thursday."),
        ("", "some unlabeled line"),
    ]
    listed = next(x for x in kb.list_meetings() if x["id"] == "2025-12-01_10-00-00")
    assert listed["summary"].startswith("Can you take the release notes?")
