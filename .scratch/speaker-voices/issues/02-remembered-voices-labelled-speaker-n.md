# 02: Remembered Voices labelled Speaker N, live

**What to build:** Every distinct voice in a Meeting becomes a remembered Voice, shown as Speaker N, and the same voice keeps the same number in later Meetings. A new Voice registry owns this. It works on voiceprint vectors, not audio: it matches against known Voices, leaves the grey zone unlabelled, creates the next Speaker N only for a clearly new voice, never reuses a number, and saves voiceprints with the other personal app data. The existing single voiceprint migrates into the Voice linked to Me, and "Record my voice" creates or refreshes that Voice. The transcriber labels each whisper segment through the registry (neighbour borrowing and same-speaker merging stay as they are). Transcripts store `Milan:` for Me, `Speaker N:` for other Voices and no prefix when unknown, per ADR 0001. The UI shows a Speaker N chip on each line. `/segment` accepts an optional Voice number, and the old `me`/`them` values still work.

**Blocked by:** 01 (Gather speaker labels into one path, and add test harness)

**Status:** done

- [x] Registry unit tests with fake vectors: a close vector matches an existing Voice, a grey-zone vector gets no label, a distant vector creates the next Speaker N, short pieces get no label, numbers survive a restart and are never reused
- [x] Match and new-voice thresholds are set in `.env`, and the defaults lean towards creating a new Voice over merging two people
- [x] An existing voiceprint is migrated to Me's Voice on first run, and lines from it are still written `Milan:` and never answered in auto mode
- [x] App test: POST `/segment` with Voice 3, and the Transcript stores `Speaker 3:` and the UI payload carries the Voice
- [x] Old Transcripts with `Other:` still load and show as "not Milan, unknown"
- [ ] Live, two different speakers show as two Speaker chips, and after restarting the app the same people get the same numbers (needs a real meeting on the mic; checked offline with real ECAPA prints of synthetic voices)
- [x] Per-segment voice scores still go to `audio_debug.log`

## Comments

Done except the live mic check. `voices.Registry` (prints only, saved to `sessions/voices.json`) plus `voice.VoiceID` (audio → print). Labels: `s3` ↔ `Speaker 3:` in `speakers.py`. Thresholds: `VOICE_MATCH` 0.45, `VOICE_NEW` 0.35 (raised from 0.25 after a real-ECAPA check: a new voice scored 0.25 against its nearest Voice and would have stayed unlabeled forever), `VOICE_NEW_MIN_S` 2.0. A young Voice (< 10 matches) averages its matches into its print within the meeting; slow learning for established Voices stays in ticket 08. Voice ID now runs whenever `VOICE_ID=on`, not only after recording your voice. Only the app's own registry migrates `voice.npy`.
