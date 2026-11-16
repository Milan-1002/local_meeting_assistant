# 05: Meeting notes record who spoke

**What to build:** When a Meeting is compiled, code (not the model) writes into the Meeting note's frontmatter the Persons whose Voices spoke and the count of unnamed Speakers. The Brief and `last_meeting_with` use this record of who actually spoke, and fall back to the current guess from mentions for older Meetings that don't have it.

**Blocked by:** 03 (Name a Voice from a transcript line)

**Status:** done

- [x] App test: compile a Meeting where Priya (named) and Speaker 5 (unnamed) spoke, and the Meeting note's frontmatter lists Priya and one unnamed Speaker, with the model stubbed out
- [x] Me is recorded as having spoken when Me's Voice spoke
- [x] `last_meeting_with` Priya finds a Meeting where she spoke but was never mentioned by name
- [x] The Brief for a Meeting with Priya draws on that Meeting
- [x] Meetings compiled before this change still work with the old mention-based lookup

## Comments

Done. `kb.speakers_in` / `kb.transcript_speakers` turn stored labels into Person paths plus an unnamed count. Compile writes `people:` and `unnamed_speakers:` frontmatter and a `## Who spoke` wikilink line (so Obsidian's graph links people to the meetings they spoke in; hidden on the Meetings page, where the Speakers strip shows it). `last_meeting_with` checks `people:` first, then the old link match. The Brief adds the People speaking in the live meeting to its pages and prefers them for "last meeting with". Extra: `rewrite_links` (used by `merge`/`dedupe`) now also rewrites `people:` frontmatter, since those are plain paths, not links. Tests: `tests/test_who_spoke.py` (6).
