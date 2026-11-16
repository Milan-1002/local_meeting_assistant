# 03: Name a Voice from a transcript line

**What to build:** Milan clicks a Speaker chip on any transcript line, live or on a past Meeting, types a name, and that Voice is linked to a Person from then on. Naming writes the Voice → Person mapping to a code-managed vault page (`voices.md`, editable in Obsidian like `threads.md`, reloaded when it changes) and creates the Person page if it doesn't exist. Naming a second Voice with an existing name links it to the same Person. Naming Me works the same way, and auto mode then skips that Voice. Names are swapped in when text is read, never when it's stored: every view, `full_text()`, and every prompt (answers, questions, Live notes, AI Chat, Brief, compile, weekly review) shows the Person's name for named Voices and Speaker N for the rest. The prompts' transcript note explains both. A new SSE kind tells open views to re-render names; it must not be a `config` event, because the reducer treats that as a reconnect. The new endpoint is added to the dev proxy list.

**Blocked by:** 02 (Remembered Voices labelled Speaker N, live)

**Status:** done

- [x] Naming Speaker 3 "Priya" shows "Priya" on every line from that Voice in the live Meeting and in past Meetings, while every stored Transcript still says `Speaker 3:`
- [x] App test: after naming, the stubbed model receives "Priya:" lines in an answer prompt and in a compile prompt
- [x] Naming creates `people/priya` when it is missing, and links to the existing page when it is there
- [x] Two Voices named "Priya" both link to the one Person
- [x] Naming a Speaker as Milan makes that Voice Me: lines are written `Milan:` from then on and auto mode never answers them
- [x] Editing `voices.md` in Obsidian is picked up without a restart
- [x] Every open browser tab updates its names after a naming, without a reconnect or reset
- [x] Naming works under `npm run dev` as well as from `web/dist`

## Comments

Done. Mapping: `wiki/voices.md` via `kb.voice_people` / `kb.name_voice` / `kb.person_for` (matches the user's name and aliases to `me`, then People pages by title, file name or glossary alias, else creates the page). `speakers.use_people` is the lookup kb installs; `display`/`render_line`/`render` give names for reading, `format_line` keeps `Speaker N:` for storage. Endpoint `POST /voices/name`, SSE kind `voices`, `config.voices` on replay. The chip is a button opening an inline name box (no browser dialogs). Checked in Chrome against a temp instance: naming Speaker 3 "Priya" renamed both of its paragraphs, survived a reload, wrote `voices.md` and `people/priya.md`, and the stored Transcript kept `Speaker 3:`. Note: the vault-rules line about `voices.md` only reaches `vault/CLAUDE.md` when that file is created, so an existing vault doesn't get it. Obsidian edits to `voices.md` reach open tabs on their next reconnect or naming, not instantly.
