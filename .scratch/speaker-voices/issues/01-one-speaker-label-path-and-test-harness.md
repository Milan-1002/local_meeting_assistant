# 01: Gather speaker labels into one path, and add test harness

**What to build:** A behaviour-preserving prefactor so the Voice work only has to change one place. Today the `me`/`them` labels are written, parsed, displayed and explained separately in the segment model, the config, the kb transcript reader, the prompts' transcript note and two UI components (which hard-code "You" and "Other"). Route all of these through a single speaker-label helper on the Python side and a single one in the UI. Add the repo's first automated tests: a minimal pytest setup and an app fixture that runs with no audio, a stubbed `llm.complete` returning schema-shaped dicts, a temp vault and a temp session folder (the approach `CLAUDE.md` already describes for manual checks). Spec: `.scratch/speaker-voices/spec.md`.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] Writing a Transcript line, parsing it back, showing it in the UI and describing it in prompts each go through one shared helper; no other code checks for `me`/`them` or the strings `Milan:`/`Other:` directly
- [x] The app looks and behaves exactly as before: `Milan:` and `Other:` lines, "You"/"Other" chips, auto mode still skips Me's lines
- [x] `pytest` runs from the repo root with no network, audio device or API key
- [x] An app-level test POSTs labelled and unlabelled lines to `/segment`, then checks the stored Transcript, the segment payload, and that the stubbed model receives the expected labels
- [x] A test loads an existing-format Transcript (with `Milan:` and `Other:`) through the Meetings reader and gets the right speakers
- [x] `CLAUDE.md`'s "no tests" line is updated to say how to run them

## Comments

Done. `speakers.py` holds the label rules (`clean`, `is_me`, `name`, `format_line`, `parse`, `NOTE`); `config.SPEAKERS`/`TRANSCRIPT_NOTE` and `kb.split_speaker` are gone. The UI mirror is `isMe`/`speakerName`/`speakerChip` in `web/src/format.js`. `SESSIONS_DIR` can now be set from the environment (tests use it). The classic page `static/index.html` doesn't show speakers, so it was left alone. Tests: `tests/test_speakers.py` (5 tests).
