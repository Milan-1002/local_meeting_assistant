# Spec: Remembered speaker Voices

Status: done (all 9 tickets; the live-microphone check in ticket 02 is still open)

Vocabulary follows `GLOSSARY.md`. Respects ADR 0001 (Transcripts store Voices, not names).

## Problem Statement

Milan meets mostly the same 9–10 people every day, but the copilot only knows two kinds of speaker: Milan and "Other". Transcripts, Live notes, Meeting notes and the wiki can't say who said what, so Person pages miss most of what people actually said, and the Brief has to guess who was in past Meetings from names mentioned in the text. Milan knows who is speaking but has no way to tell the app.

## Solution

Every distinct voice in a Meeting becomes a remembered **Voice**, recognised again in later Meetings. Until Milan names it, a Voice is labelled **Speaker N**, and the number stays the same across Meetings. Milan names a Voice once, live or afterwards on the Meetings page, by linking it to a Person. From then on that Voice shows with the Person's name in every past and future Meeting, in the app and in everything sent to a model. Milan's own voice is just the Voice linked to Me. When compiled, Meeting notes and Person pages reflect who actually spoke. Meetings compiled before a naming are marked for recompile, and Milan triggers the recompile himself.

## User Stories

### Recognising voices

1. As Milan, I want each distinct voice in a Meeting to get its own label, so that I can tell speakers apart instead of seeing everyone as "Other".
2. As Milan, I want a voice I haven't named to keep the same Speaker N in every Meeting, so that my regular colleagues look consistent even before I name them.
3. As Milan, I want a voice I've named to be labelled with the Person's name in later Meetings automatically, so that I only name each colleague once.
4. As Milan, I want a doubtful match to be left unlabelled or given a new Speaker N rather than a guessed name, so that nothing ends up in the wiki attributed to the wrong person.
5. As Milan, I want the app to lean towards splitting one person into two Speakers rather than merging two people into one, so that mistakes are ones I can fix by naming.
6. As Milan, I want short pieces like "yeah" to borrow a neighbouring line's Voice as they do today, so that quick back-and-forth stays readable.
7. As Milan, I want consecutive lines from the same Voice merged into one line as they are today, so that the Transcript stays compact.
8. As Milan, I want labels to appear live while the Meeting is going, so that I can follow who is talking.
9. As Milan, I want a Voice's voiceprint to improve a little from each Meeting where it was matched confidently, so that recognition gets better the more I meet someone.
10. As Milan, I want one bad match not to be able to shift a voiceprint much, so that recognition doesn't drift towards someone else over time.
11. As Milan, I want an unnamed Voice not heard for 30 days to be forgotten, so that one-off guests don't pile up.
12. As Milan, I want Speaker numbers never reused, even after a Voice is forgotten, so that old Transcripts never point at the wrong voice.

### Naming

13. As Milan, I want a Speakers strip on each Meeting that lists every Voice that spoke, with how much they talked and 2–3 sample lines, so that I can work out who each one is without audio.
14. As Milan, I want to name a Voice from that strip with a name box that autocompletes from my People pages, so that I link to the right existing Person.
15. As Milan, I want typing a new name to create that Person's page, so that I can name someone the wiki doesn't know yet.
16. As Milan, I want to click a Speaker chip on any transcript line to name that Voice, so that I can name someone the moment I recognise them.
17. As Milan, I want to name Voices live during a Meeting, so that every later line and prompt in that Meeting uses the name.
18. As Milan, I want to name Voices afterwards on the Meetings page, so that I can sort out speakers when I have time.
19. As Milan, I want naming a Voice to apply to every past and future Meeting it appears in, so that I never repeat the same naming.
20. As Milan, I want to name a second Voice with an existing Person's name and have it linked to that same Person, so that a colleague heard in a room and over Teams is still one Person.
21. As Milan, I want to move a Voice from one Person to another when I got it wrong, so that I can correct mistakes.
22. As Milan, I want to name a Speaker as myself, so that I can set up my own Voice without recording one.
23. As Milan, I want to see and edit the Voice → Person mapping in Obsidian, so that I can review or fix it outside the app.

### Me

24. As Milan, I want my existing recorded voiceprint to become the Voice linked to Me, so that nothing I set up before is lost.
25. As Milan, I want "Record my voice" to keep working as a shortcut for creating or refreshing my Voice, so that I can set it up quickly.
26. As Milan, I want auto mode never to answer lines from my own Voice, so that the copilot doesn't answer me.
27. As Milan, I want my lines still written as `Milan:` in Transcripts, so that existing readers and old Transcripts keep working.

### Transcripts and prompts

28. As Milan, I want Transcripts to keep storing `Speaker N:` even after I name the Voice, so that Transcripts are never edited.
29. As Milan, I want every view in the app to show the Person's name for a named Voice, so that I see names, not numbers.
30. As Milan, I want every prompt that includes the Transcript (answers, questions, Live notes, AI Chat, Brief, compile, weekly review) to show names for named Voices and Speaker N for the rest, so that the models know who said what.
31. As Milan, I want the prompts' transcript note to explain both name labels and Speaker N labels, so that the models read them correctly.
32. As Milan, I want old Transcripts with `Other:` left as they are and read as "not Milan, unknown who", so that past Meetings still load.

### Wiki

33. As Milan, I want each Meeting note to list the Persons whose Voices spoke and how many unnamed Speakers there were, written by code rather than the model, so that "Meetings with Priya" is a reliable lookup.
34. As Milan, I want the Brief and "last meeting with" lookups to use who actually spoke, so that they're accurate instead of guessed from mentions.
35. As Milan, I want compile to treat lines from a named Voice as that Person's own words, so that their Person page records what they said.
36. As Milan, I want naming a Voice to mark every already-compiled Meeting it appears in as needing a recompile, so that I know which Meeting notes are out of date.
37. As Milan, I want nothing recompiled without my click, so that naming never spends LLM calls on its own.
38. As Milan, I want a "needs recompile" badge on the Meetings page and a button that recompiles those Meetings, so that I can update them in one go.
39. As Milan, I want `kb.py compile` to pick up Meetings marked for recompile as well as never-compiled ones, so that the CLI does the same job.
40. As Milan, I want `kb.py lint` to list Meetings marked for recompile, so that I see them during maintenance.
41. As Milan, I want a recompile to replace only the Log lines that cite that same Meeting, so that out-of-date "Speaker 3 said" wording is fixed without losing any other history.
42. As Milan, I want moving a Voice to a different Person to flag the old Person's page, with `lint` reporting facts on it that cite the affected Meetings, so that I can check them myself.
43. As Milan, I want those facts never deleted automatically, so that true facts aren't lost.

### Forgetting

44. As Milan, I want a Forget action on each Voice that deletes its voiceprint, so that I can remove someone's biometric data on request.
45. As Milan, I want a forgotten Voice to keep its name in past Transcripts and keep its Person page, so that history stays readable.
46. As Milan, I want that person to be labelled as a new Speaker N in later Meetings after Forget, so that it's clear their voice is no longer recognised.

## Implementation Decisions

- **Voice registry (new module, deep).** It owns every Voice. Its interface works on **embedding vectors, not audio**, so the ECAPA model stays in the existing voice module and the registry can be tested with fake vectors. Rough operations:
  - `identify(embedding, duration) -> voice | None`: returns the best-matching Voice above the match threshold. It returns None in the grey zone or when the piece is too short. It creates a new Speaker N only when the embedding is clearly unlike every known Voice. It records the Voice as heard.
  - `learn(voice, embeddings)`: at the end of a Meeting, nudges a Voice's voiceprint towards that Meeting's confident matches with a small, bounded step.
  - `name(voice, person)`: links or moves the Voice and returns the Meetings it appears in, so callers can mark them for recompile.
  - `forget(voice)`: deletes the voiceprint and keeps the number and mapping.
  - `display(voice) -> str`: the Person's name, or "Speaker N".
  - `prune(now)`: drops unnamed Voices not heard for 30 days.
  - Number allocation is monotonic and never reuses a number.
- **Thresholds.** The existing `VOICE_ME` / `VOICE_THEM` pair generalises to a match threshold (counts as this Voice) and a new-voice threshold (clearly nobody known), with the grey zone between them left unlabelled. Both are set in `.env`. The defaults lean towards creating new Voices over merging.
- **Storage.** Voiceprints are kept with the other personal app data under `sessions/`, replacing the single `voice.npy`, which is migrated on first run into the Voice linked to Me. The Voice → Person mapping is a code-managed vault page, `wiki/voices.md`, one line per Voice (for example `- Speaker 3 → [[people/priya]]`). It is readable and editable in Obsidian, like `threads.md`, and the app reloads it when its mtime changes. Me's Voice is recorded there too.
- **Transcript format.** `Milan:` for Me, `Speaker N:` for any other Voice, no prefix when unlabelled. Legacy `Other:` is still parsed. A segment's speaker becomes a Voice reference rather than the `me`/`them` pair, and the "is Me" check goes through the mapping.
- **Names swapped in when text is read.** One rendering path turns a Transcript or segment list into text with names for every consumer: UI payloads, `full_text()` and the kb readers. `TRANSCRIPT_NOTE` describes name labels and Speaker N. Naming mid-meeting changes the transcript prefix and breaks the prompt cache once; that is accepted.
- **Audio pipeline.** The per-segment labelling in the transcriber calls `identify` instead of the two-way me/them label. Neighbour borrowing and same-speaker merging are unchanged. Confident matches from a Meeting are buffered and passed to `learn` at End meeting or New meeting. Enrollment ("Record my voice") creates or refreshes Me's Voice through the registry.
- **API.** `/segment` accepts an optional Voice (by number), with the legacy `me`/`them` still accepted. New endpoints cover listing a Meeting's Voices (sample lines, talk time), naming a Voice, forgetting a Voice and recompiling marked Meetings. Each new path is added to the dev proxy list. Naming is broadcast so every open view re-renders names. It must not be a `config` event, because the reducer treats that as a reconnect.
- **SSE.** A new kind for Voice changes (named, forgotten, new Voice in this Meeting) needs a reducer case.
- **UI.** A Speakers strip at the top of the live Meeting and of each past Meeting on the Meetings page. Speaker chips on transcript lines open the same name box. The box autocompletes from People pages, and a new name creates the Person page. The Meetings page shows a needs-recompile badge and a "Recompile" button.
- **Meeting note attendees.** On compile, code writes `people:` (Persons whose Voices spoke) and the unnamed-Speaker count into the Meeting note's frontmatter. The model doesn't write these. The Brief and `last_meeting_with` read from them, falling back to the current mention-based guess for older Meetings.
- **Recompile marker.** Kept per Meeting in a code-managed form that `pending_meetings` (or a sibling) reads, so `kb.py compile`, `lint` and the Meetings page all see the same list. Recompiling clears the mark.
- **Log exception.** On recompile, `write_entity` replaces existing Log lines citing that same Meeting instead of skipping them. `VAULT_RULES` gets one clause saying so. Every other Log line stays append-only.
- **Moved Voice.** When `name` moves a Voice from one Person to another, the old Person's page is recorded as needing review. `lint` reports facts on it that cite the affected Meetings and never deletes them.
- **LLM budget.** The feature adds no automatic LLM calls. Recognition is local (ECAPA on CPU, already running per segment), naming is local, and recompiles happen only on click or CLI.

## Testing Decisions

- Good tests check external behaviour: which label a line ends up with, what text a prompt receives, what the vault files say. They don't check internal data structures or thresholds' exact arithmetic.
- **Seam 1: the Voice registry.** Unit tests with hand-made unit vectors in place of ECAPA embeddings, and an injected clock and a temp storage directory. Covered:
  - a close vector matches an existing Voice
  - a grey-zone vector gets no label
  - a distant vector creates the next Speaker N
  - numbers are never reused after `prune` or `forget`
  - naming applies to past Meetings and returns the right ones for recompile
  - two Voices can share one Person
  - moving a Voice reports the old Person
  - `learn` moves a voiceprint by a bounded amount and ignores low-confidence input
  - `prune` drops only unnamed Voices past 30 days
  - `forget` keeps the name for past Transcripts
  - the old single voiceprint is migrated into Me's Voice
- **Seam 2: the FastAPI app.** Run with `--no-audio`, a stubbed `llm.complete` returning schema-shaped dicts, a temp `VAULT_DIR` and a temp session directory. This follows the manual approach already described in `CLAUDE.md`. Covered:
  - POST `/segment` with a Voice, then the Transcript stores `Speaker N:`
  - naming the Voice makes the UI payload and the stubbed model's received prompt show the name, while the stored Transcript still says `Speaker N:`
  - lines from Me's Voice are never flagged in auto mode
  - legacy `Other:` Transcripts still load
  - compile writes `people:` to the Meeting note
  - naming after compile marks the Meeting, recompile clears it and replaces only that Meeting's Log line
  - moving a Voice makes `lint` report the old Person's page
- There is no existing automated test prior art in the repo. These would be its first tests, so a minimal pytest setup is part of the work.

## Out of Scope

- Adding the asking Person's page to answer prompts ("Priya asked, so include what Priya cares about"). This is a natural follow-up.
- Splitting a Speaker that wrongly merged two people. Audio isn't kept, so it can't be done afterwards. The mitigation is the threshold.
- Relabelling past `Other:` lines into Voices.
- Re-grouping a Meeting's audio at the end for accuracy. Labelling is live only.
- Stitching several Meetings from one real call back together.
- Keeping any audio.

## Further Notes

- Voiceprints of colleagues are biometric data in some places (for example Illinois' BIPA and GDPR). They stay local, and Forget is the deletion path.
- Renaming the UI's "Refresh notes" to "Refresh live notes" matches the glossary but isn't required here.
