"""The Voice registry, with made-up voice prints instead of audio."""

from __future__ import annotations

import numpy as np
import pytest

import speakers
from conftest import copilot, kb, wait_for
from voices import Registry, unit

DIM = 192
rng = np.random.default_rng(7)


def person() -> np.ndarray:
    return unit(rng.normal(size=DIM))


def near(v: np.ndarray, similarity: float) -> np.ndarray:
    """A print whose cosine similarity with v is `similarity`."""
    w = rng.normal(size=DIM)
    w = unit(w - (w @ v) * v)
    return unit(similarity * v + np.sqrt(1 - similarity ** 2) * w)


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "voices.json", match=0.45, new=0.25, new_min_s=2.0)


def test_new_voices_get_numbers_and_are_recognised(reg):
    priya, bob = person(), person()
    assert reg.identify(priya, 3.0)[0] == 1
    assert reg.identify(bob, 3.0)[0] == 2
    assert reg.identify(near(priya, 0.8), 2.0)[0] == 1
    assert reg.identify(near(bob, 0.8), 1.5)[0] == 2  # long enough to match, not to create


def test_grey_zone_and_short_lines_get_no_voice(reg):
    priya = person()
    reg.identify(priya, 3.0)
    number, score = reg.identify(near(priya, 0.35), 3.0)
    assert number is None and score == pytest.approx(0.35, abs=0.02)
    assert reg.identify(person(), 0.5) == (None, None)  # too short to judge
    assert reg.identify(person(), 1.5)[0] is None  # unknown, but too short to start a new Voice
    assert len(reg.voices) == 1


def test_numbers_survive_restart_and_are_never_reused(tmp_path):
    path = tmp_path / "voices.json"
    priya, bob = person(), person()
    reg = Registry(path)
    reg.identify(priya, 3.0)
    reg.identify(bob, 3.0)
    reg.set_me(person())
    reg.forget_me()  # retires number 3

    again = Registry(path)
    assert again.identify(near(priya, 0.9), 2.0)[0] == 1
    assert again.identify(near(bob, 0.9), 2.0)[0] == 2
    assert again.identify(person(), 3.0)[0] == 4
    assert again.me is None


def test_me_is_enrolled_and_refreshed(reg):
    me = person()
    number = reg.set_me(me)
    assert reg.has_me and reg.me == number
    assert reg.identify(near(me, 0.7), 2.0)[0] == number
    assert reg.set_me(near(me, 0.95)) == number  # refreshing keeps the same Voice


def test_old_voice_print_migrates_to_me(tmp_path):
    old = tmp_path / "voice.npy"
    me = person()
    np.save(old, me)
    reg = Registry(tmp_path / "voices.json")
    reg.migrate(old)
    assert reg.has_me and reg.identify(near(me, 0.8), 2.0)[0] == reg.me
    assert not old.exists() and (tmp_path / "voice.npy.migrated").exists()
    reg.migrate(old)  # only once
    assert len(reg.voices) == 1


def test_voice_id_labels_me_and_others(tmp_path, monkeypatch):
    from voice import VoiceID

    me, priya = person(), person()
    vid = VoiceID(Registry(tmp_path / "voices.json"))
    vid.registry.set_me(me)
    audio = np.zeros(16_000 * 3, dtype=np.float32)
    monkeypatch.setattr(vid, "embed", lambda a: near(me, 0.8))
    assert vid.label(audio)[0] == speakers.ME
    monkeypatch.setattr(vid, "embed", lambda a: priya)
    assert vid.label(audio)[0] == "s2"
    assert vid.label(audio[:8000]) == ("", None)


def test_segment_with_a_voice_is_stored_as_speaker_n(app):
    app.post("/segment", json={"text": "Can we move the demo?", "voice": 3})
    app.post("/segment", json={"text": "Sure", "speaker": "s3"})
    wait_for(lambda: len(copilot.meeting.segments) == 2)
    stored = (copilot.session_dir / "transcript.txt").read_text().splitlines()
    assert [l.split("] ", 1)[1] for l in stored] == ["Speaker 3: Can we move the demo?", "Speaker 3: Sure"]
    assert copilot.meeting.segments[0].payload("00:00")["speaker"] == "s3"

    app.post("/answer", json={"segment_id": 0})
    call = wait_for(lambda: app.llm.calls and app.llm.calls[-1])
    assert "Speaker 3: Can we move the demo?" in call["user"]
    assert "Speaker 3:" in speakers.NOTE


def test_speaker_n_transcripts_load():
    kb.RAW.mkdir(parents=True, exist_ok=True)
    (kb.RAW / "2025-12-02_10-00-00.md").write_text(
        "---\ntype: transcript\ndate: 2025-12-02 10:00\ncontext: (none)\n---\n"
        "[00:10] Speaker 12: Can you take the release notes?\n"
        "[00:14] Milan: Sure.\n[00:15] Other: Thanks.\n")
    m = kb.load_meeting("2025-12-02_10-00-00")
    assert [l["speaker"] for l in m["lines"]] == ["s12", "me", "them"]
    assert m["lines"][0]["text"] == "Can you take the release notes?"


def test_a_passed_in_registry_never_migrates_the_real_print(tmp_path, monkeypatch):
    import voice

    old = tmp_path / "voice.npy"
    np.save(old, person())
    monkeypatch.setattr(voice, "OLD_VOICE_FILE", old)
    voice.VoiceID(Registry(tmp_path / "voices.json"))
    assert old.exists()


# ---------------------------------------------------------------- 08: learning and expiry

class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def established(reg, v):
    number = reg.identify(v, 3.0)[0]
    reg.voices[number].samples = 10  # past the young phase
    return number


def test_a_meeting_of_confident_matches_moves_the_print_a_little(reg):
    priya = person()
    n = established(reg, priya)
    old = reg.voices[n].print.copy()
    heard = [near(priya, 0.8) for _ in range(6)]
    for h in heard:
        reg.identify(h, 2.0)
    assert (reg.voices[n].print == old).all()  # nothing learned mid-meeting
    reg.end_meeting()
    new = reg.voices[n].print
    mean = unit(np.mean(heard, axis=0))
    assert new @ mean > old @ mean  # moved towards what was heard
    assert new @ old > 0.97  # but only a small, bounded step


def test_weak_or_few_matches_teach_nothing(reg):
    priya = person()
    n = established(reg, priya)
    old = reg.voices[n].print.copy()
    for _ in range(6):
        reg.identify(near(priya, 0.5), 2.0)  # matches (>= 0.45) but not confidently (< 0.55)
    reg.identify(near(priya, 0.9), 2.0)
    reg.identify(near(priya, 0.9), 2.0)  # confident, but only 2
    reg.end_meeting()
    assert np.allclose(reg.voices[n].print, old)
    reg.end_meeting()  # once per meeting: nothing carried over
    assert np.allclose(reg.voices[n].print, old)


def test_unnamed_voices_expire_after_30_days(tmp_path):
    clock = Clock()
    reg = Registry(tmp_path / "voices.json", clock=clock)
    guest, priya, recent = person(), person(), person()
    g, p = reg.identify(guest, 3.0)[0], reg.identify(priya, 3.0)[0]
    me = reg.set_me(person())
    clock.t += 31 * 86400
    r = reg.identify(recent, 3.0)[0]
    reg.end_meeting(keep={p})
    assert set(reg.voices) == {p, me, r}
    assert g not in reg.voices
    assert reg.identify(guest, 3.0)[0] == r + 1  # back as a new number; the dropped one is retired
    assert Registry(tmp_path / "voices.json").next_number == r + 2


def test_end_and_new_meeting_run_voice_learning(app, tmp_path):
    from voice import VoiceID

    calls = []
    vid = VoiceID(Registry(tmp_path / "voices.json"))
    vid.end_meeting = lambda: calls.append(1)
    copilot.app.state.voice = vid
    try:
        app.post("/end")
        copilot.reset_meeting()
        assert len(calls) == 2
    finally:
        copilot.app.state.voice = None


# ---------------------------------------------------------------- 09: Forget a Voice

def test_forget_deletes_the_print_but_keeps_name_and_number(tmp_path):
    path = tmp_path / "voices.json"
    reg = Registry(path)
    priya = person()
    n = reg.identify(priya, 3.0)[0]
    kb.name_voice(n, "Priya")
    try:
        assert reg.forget(n)
        assert not reg.forget(n)  # nothing left to forget
        saved = Registry(path)
        assert saved.voices[n].print is None and n in saved.voices
        assert speakers.display(f"s{n}") == "Priya"  # past lines keep the name
        assert reg.identify(priya, 3.0)[0] == n + 1  # no longer recognised: a new Speaker
    finally:
        kb.VOICES.unlink(missing_ok=True)


def test_forget_me_then_record_again(reg):
    me = person()
    n = reg.set_me(me)
    assert reg.forget(n) and not reg.has_me and reg.me is None
    assert reg.set_me(me) != n and reg.has_me


def test_forget_endpoint_without_audio_edits_saved_prints(app):
    import voice

    reg = Registry(voice.VOICES_FILE)
    n = reg.identify(person(), 3.0)[0]
    assert app.post("/voices/forget", json={"voice": n}).json() == {"ok": True}
    assert Registry(voice.VOICES_FILE).voices[n].print is None
    assert not app.post("/voices/forget", json={"voice": n}).json()["ok"]
    assert not app.post("/voices/forget", json={"voice": "x"}).json()["ok"]
    assert app.post("/voice/forget").json() == {"ok": True}  # the old Me forget still answers
