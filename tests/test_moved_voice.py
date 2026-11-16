"""Moving a Voice to another Person flags the old Person's page for review; nothing is deleted."""

from __future__ import annotations

import pytest

from conftest import kb

MID, OTHER = "2025-12-20_10-00-00", "2025-12-01_10-00-00"


@pytest.fixture(autouse=True)
def vault():
    kb.ensure_vault()
    kb.VOICES.unlink(missing_ok=True)
    for d in ("people", "meetings"):
        for p in (kb.WIKI / d).glob("*.md"):
            p.unlink()
    for p in kb.RAW.glob("*.md"):
        p.unlink()
    for mid in (MID, OTHER):
        (kb.RAW / f"{mid}.md").write_text(f"---\ntype: transcript\n---\n[00:01] Speaker {3 if mid == MID else 8}: hi\n")
        (kb.WIKI / "meetings" / f"{mid}.md").write_text("---\ntype: meeting\n---\n# M\n")
    yield
    kb.VOICES.unlink(missing_ok=True)


def priya_page():
    return (kb.WIKI / "people" / "priya.md")


def test_moving_a_voice_flags_the_old_person():
    kb.name_voice(3, "Priya")
    priya_page().write_text(
        "---\ntype: person\n---\n# Priya\n\n## Key facts\n"
        f"- Owns the rollout ([[meetings/{MID}]])\n- Leads QA ([[meetings/{OTHER}]])\n\n## Log\n"
        f"- 2025-12-20 · raised the risk ([[meetings/{MID}]])\n")
    before = kb.split_frontmatter(priya_page().read_text())[1]

    kb.name_voice(3, "Bob")
    report = kb.lint()["check_after_voice_moved"]
    assert report == [{"page": "people/priya", "reason": "Speaker 3 was moved to Bob", "meetings": [MID],
                       "check": [f"- Owns the rollout ([[meetings/{MID}]])",
                                 f"- 2025-12-20 · raised the risk ([[meetings/{MID}]])"]}]
    assert kb.split_frontmatter(priya_page().read_text())[1] == before  # nothing removed
    assert kb.stale_meetings() == [MID]  # and the meeting is marked as in ticket 06


def test_naming_an_unnamed_voice_flags_nothing():
    kb.name_voice(3, "Priya")
    kb.name_voice(3, "Priya")
    assert kb.lint()["check_after_voice_moved"] == []
