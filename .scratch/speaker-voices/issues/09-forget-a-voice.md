# 09: Forget a Voice

**What to build:** Each Voice has a Forget action that deletes its voiceprint, for when someone asks Milan to remove their biometric data. It replaces today's single "forget my voice". The name stays on that Voice's lines in past Transcripts, and the Person page is kept. In later Meetings that person is no longer recognised and shows up as a new Speaker N. Forgetting Me's Voice is the same as today's forget.

**Blocked by:** 03 (Name a Voice from a transcript line)

**Status:** done

- [x] Forget is available on each Voice in the UI, separate from anything that deletes a Person
- [x] Registry test: after Forget, the voiceprint is gone from disk, the Voice → Person mapping and the number are kept, and the same vector now creates a new Speaker N
- [x] Past Meetings still show the Person's name on that Voice's lines
- [x] Forgetting Me's Voice behaves like today's forget: you can record your voice again afterwards
- [x] The old forget endpoint still works for Me

## Comments

Done. `Registry.forget(n)` (print → None; number, Me pointer cleared if it was Me, and the `voices.md` name kept). `POST /voices/forget {voice}`; without audio it edits `sessions/voices.json` directly. The UI has a two-click "Forget voice" on each Speaker card. Me's voice is still forgotten from Settings via the old `/voice/forget`, which now goes through the same code. Checked in Chrome on a temp instance: only Speaker 3's print was deleted.
