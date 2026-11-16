# Meeting Copilot

Runs on your personal laptop. It listens to the meeting, transcribes it on-device, and when someone
asks you something it drafts an answer you can say, in your own voice, using what it knows from
your past meetings. Dashboard at http://localhost:8000.

```
mic ─► speech chunks ─► mlx-whisper (local, free) ─► transcript   (live gray draft every 0.5 s, final line at each pause)
                                                      │ every line
                                         regex rules (free): question to me? named? opinion?
                                                      │ only flagged lines
                                         cheap check (Haiku): really for me?
                                                      │ only confirmed
     dashboard ◄── answer in your voice ◄── main model + vault/wiki (me.md, index, relevant pages)

question ideas (gpt-5.4) on "any questions?" / topic change / every ~4 min · running notes every ~5 min · "End meeting" → meeting compiled into the Obsidian vault
```

## Setup (once)

```bash
cd ~/local_meeting_assistant
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
(cd web && npm install && npm run build)   # the dashboard UI (needs Node)
cp .env.example .env    # then add ANTHROPIC_API_KEY and/or OPENAI_API_KEY
```

The first run downloads the speech models (~1.5 GB for final lines, ~140 MB for the live draft). macOS will ask whether your terminal can use
the microphone. Click Allow.

## Use

```bash
.venv/bin/python copilot.py                    # default mic
.venv/bin/python copilot.py --list-devices     # see input devices
.venv/bin/python copilot.py --device "Teams"   # pick one by name
.venv/bin/python copilot.py --watch live.txt   # already have a transcript? tail that file instead
.venv/bin/python copilot.py --no-audio         # only paste text into the dashboard
```

Open http://localhost:8000.

By default the AI only runs when you click, so a meeting costs only what you ask for. Each request
sends the whole transcript plus your knowledge base. Turn on **Automatic** in Settings (or set
`AUTO_MODE=on` in `.env`) to get answers, notes and question ideas without clicking. It costs more.

- Hover any transcript line: **Answer** drafts a reply to it, **Questions** suggests what you could
  ask about it.
- **Ask next** shows 1–2 questions you could ask, with why now. 👍/👎 teaches it your taste
  across meetings (`sessions/question_feedback.jsonl`). **Suggest questions** asks for fresh ones.
- **Suggested answers** appear when someone asks you something. Use Copy, Shorter, Different or
  More detail on any answer. **Not useful** is logged to help tune detection.
- Type in the bottom box: **Answer as me** drafts a reply; **Ask privately** (⌘↵) asks about this
  or past meetings; **Questions on this** suggests questions about what you typed.
- **Notes → Refresh** writes meeting notes when you want them.
- **Settings**: what this meeting is about, Automatic on/off, and (in Automatic) how eagerly to
  answer (only when named / normal / any question).
- **End meeting** writes final notes and adds the meeting to your knowledge base.

Each run saves `sessions/<timestamp>/` (transcript, summary, answers, feedback).

## Knowledge base (Obsidian)

`vault/` is an Obsidian vault built in the style of Karpathy's "LLM wiki". Transcripts go into
`raw/meetings/`, and an LLM turns them into linked pages for people, projects, topics and meetings
under `wiki/`. The copilot reads `wiki/me.md` and `wiki/index.md` before every answer and pulls in
the pages that matter.

```bash
.venv/bin/python kb.py import-sessions   # copy old sessions/ transcripts into the vault
.venv/bin/python kb.py compile           # turn new transcripts into wiki pages
.venv/bin/python kb.py lint [--fix]      # broken links / orphans; --fix tidies overgrown pages
```

**Fill in `vault/wiki/me.md`.** Your role, how you talk and what you think make the biggest
difference to answer quality. To browse the vault, open Obsidian, choose *Open folder as vault*,
pick `vault/`, then open Graph view.

## Tuning (`.env`)

See `.env.example`. Main knobs: `TRIAGE_MODEL` (default Haiku), `MAIN_MODEL` (default
gpt-5.4-mini), `SENSITIVITY`, `SUMMARY_EVERY_S`, `MY_ALIASES`, and `TRANSCRIBE_BACKEND=openai`
if you'd rather pay for cloud speech-to-text.

Audio quality matters most. Put the laptop close to the speaker, or plug the work laptop's headphone
output into this laptop's mic input. Check your company's policy on recording meetings and make
sure participants know they're being recorded.
