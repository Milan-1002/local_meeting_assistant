"""Meeting Copilot: listens to a meeting and drafts answers when someone asks you something.

Audio is transcribed locally (mlx-whisper). By default (AUTO_MODE=off) the AI only runs when you
click: answer a line, type a question, suggest questions, refresh notes. Each of those calls gets the
whole transcript plus your knowledge base (vault/). With AUTO_MODE=on, each new line also goes
through free regex rules; only flagged lines reach a cheap model (is this really for me?), only
confirmed questions reach the main model, and notes and question ideas refresh on timers.
Everything shows up at http://localhost:8000.

    python copilot.py                      # listen on the default mic
    python copilot.py --device "Teams"     # pick an input device by name
    python copilot.py --watch notes.txt    # no audio: tail a transcript file
    python copilot.py --no-audio           # paste lines in the browser only
    python copilot.py --list-devices
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import kb
import llm
import speakers
from config import (AUTO_MODE, CHAT_EFFORT, CHAT_MODEL, HAS_ANTHROPIC, HAS_OPENAI, MAIN_MODEL, MY_NAME, QUESTIONS_COOLDOWN_S, QUESTIONS_EFFORT,
                    QUESTIONS_EVERY_S, QUESTIONS_MODEL, RECENT_WINDOW_S, ROOT, SENSITIVITY, SESSIONS_DIR,
                    SUMMARY_EVERY_S, SUMMARY_MIN_NEW_WORDS, TRANSCRIBE_BACKEND,
                    TRIAGE_MODEL, VAULT_DIR, VOICE_ID, WEEKLY_DAY, WEEKLY_HOUR)
from speakers import NOTE as TRANSCRIPT_NOTE
from triggers import FIRST_NAME, ask_moment, rule_check, triage

# --------------------------------------------------------------------------- state


@dataclass
class Segment:
    id: int
    t: float
    text: str
    speaker: str = ""  # see speakers.py: "me", "them", or "" when the voice wasn't recognized

    def line(self, clock: str) -> str:
        """As stored in the Transcript: "Speaker 3: …" even once that Voice is named."""
        return speakers.format_line(clock, self.text, self.speaker)

    def shown_line(self, clock: str) -> str:
        """As read by models: named Voices by their Person's name."""
        return speakers.render_line(clock, self.text, self.speaker)

    def payload(self, clock: str) -> dict:
        return {"id": self.id, "clock": clock, "text": self.text, "speaker": self.speaker}


@dataclass
class Meeting:
    context: str = ""  # who you are / what you want out of the meeting
    segments: list[Segment] = field(default_factory=list)
    analysis: dict = field(default_factory=dict)
    suggestions: list[dict] = field(default_factory=list)  # questions suggested for the user to ask
    last_suggest_t: float = 0.0
    words_at_last_suggest: int = 0
    answers: list[dict] = field(default_factory=list)
    flags: dict[int, dict] = field(default_factory=dict)  # segment id -> latest flag state, for page reloads
    words_at_last_analysis: int = 0
    last_analysis_t: float = field(default_factory=time.time)
    paused: bool = False
    auto: bool = AUTO_MODE  # off: AI calls only when the user clicks
    sensitivity: str = SENSITIVITY
    meeting_type: str = "auto"  # a MEETING_TYPES key, or "auto" to let the questions call infer it
    detected_type: str = ""  # what the questions call last inferred
    brief: dict = field(default_factory=dict)  # "Brief me": who's here, what's open, past decisions
    ended: bool = False
    started: float = field(default_factory=time.time)

    def word_count(self) -> int:
        return sum(len(s.text.split()) for s in self.segments)

    def recent_text(self, window: float = RECENT_WINDOW_S, upto: int | None = None) -> str:
        segs = self.segments if upto is None else self.segments[: upto + 1]
        cutoff = (segs[-1].t if segs else time.time()) - window
        recent = [s for s in segs if s.t >= cutoff] or segs[-10:]
        return self.lines(recent)

    def full_text(self, upto: int | None = None, since: float = 0.0) -> str:
        segs = self.segments if upto is None else self.segments[: upto + 1]
        return self.lines([s for s in segs if s.t >= since]) or "(nothing yet)"

    def lines(self, segs: list[Segment]) -> str:
        return "\n".join(s.shown_line(self.clock(s.t)) for s in segs)

    def clock(self, t: float) -> str:
        m, s = divmod(int(t - self.started), 60)
        return f"{m:02d}:{s:02d}"


meeting = Meeting()
meeting_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
session_dir = SESSIONS_DIR / meeting_id
subscribers: set[asyncio.Queue] = set()
loop: asyncio.AbstractEventLoop | None = None
summarize_now = asyncio.Event()
flags: asyncio.Queue = asyncio.Queue()
analysis_lock = asyncio.Lock()
suggest_lock = asyncio.Lock()
QUESTION_FEEDBACK = SESSIONS_DIR / "question_feedback.jsonl"  # across meetings: what kind of questions you like


def broadcast(kind: str, data) -> None:
    msg = json.dumps({"kind": kind, "data": data})
    for q in list(subscribers):
        q.put_nowait(msg)


def is_current(seg: Segment) -> bool:
    """False for a line from a meeting that has since been replaced by a new one."""
    return seg.id < len(meeting.segments) and meeting.segments[seg.id] is seg


def end_voice_meeting() -> None:
    """Voice prints learn once per meeting, at End meeting, New meeting or shutdown (voices.Registry.end_meeting)."""
    voice = getattr(app.state, "voice", None)
    if voice is not None:
        try:
            voice.end_meeting()
        except Exception as e:  # never block ending a meeting on this
            print(f"voice update failed: {e}")


def reset_meeting() -> None:
    """Start a fresh meeting in this process: new session folder, transcript, notes, answers and cost.
    Your settings (automatic mode, sensitivity) carry over; the meeting description and type don't."""
    global meeting, meeting_id, session_dir
    end_voice_meeting()
    old_id = meeting_id
    meeting = Meeting(auto=meeting.auto, sensitivity=meeting.sensitivity)
    meeting_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    while meeting_id == old_id:  # two meetings in the same second would share a folder
        time.sleep(0.2)
        meeting_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    session_dir = SESSIONS_DIR / meeting_id
    while not flags.empty():
        flags.get_nowait()
    summarize_now.clear()
    llm.usage.clear()
    transcriber = getattr(app.state, "transcriber", None)
    if transcriber is not None:
        transcriber.new_meeting()  # drop the old meeting's queued audio; restart speech-to-text if it hung
        if getattr(app.state, "debug_in_session", False):
            transcriber.debug_path = str(session_dir / "audio_debug.log")


def set_flag(seg_id: int, state: str, reason: str = "") -> None:
    flag = {"id": seg_id, "state": state, **({"reason": reason} if reason else {})}
    meeting.flags[seg_id] = {**meeting.flags.get(seg_id, {}), **flag}
    broadcast("flag", meeting.flags[seg_id])


def broadcast_usage() -> None:
    broadcast("usage", llm.usage_summary())


def add_segment(text: str, speaker: str = "") -> None:
    """Thread-safe entry point for new transcript text; speaker is a speakers.py label ("" = unknown)."""
    text = text.strip()
    if not text or loop is None:
        return

    def _add():
        seg = Segment(len(meeting.segments), time.time(), text, speakers.clean(speaker))
        meeting.segments.append(seg)
        session_dir.mkdir(parents=True, exist_ok=True)
        with open(session_dir / "transcript.txt", "a") as f:
            f.write(seg.line(meeting.clock(seg.t)) + "\n")
        broadcast("segment", seg.payload(meeting.clock(seg.t)))
        if meeting.paused or not meeting.auto or speakers.is_me(seg.speaker):  # never answer the user's own lines
            return
        if ask_moment(text):
            request_questions("someone invited questions or is wrapping up")
        reason = rule_check(text, meeting.sensitivity)
        if reason:
            set_flag(seg.id, "checking", reason)
            flags.put_nowait((seg, reason))

    loop.call_soon_threadsafe(_add)


def set_status(text: str) -> None:
    if loop is not None:
        loop.call_soon_threadsafe(broadcast, "status", text)


def set_partial(text: str) -> None:
    """Live draft of what is being said right now; replaced by the final segment."""
    if loop is not None:
        loop.call_soon_threadsafe(broadcast, "partial", {"text": text})


def set_voice(state: dict) -> None:
    """Voice recording progress from the audio thread."""
    app.state.voice_state = state
    if loop is not None:
        loop.call_soon_threadsafe(broadcast, "voice", {**state, **voice_info()})


def voice_info() -> dict:
    voice = getattr(app.state, "voice", None)
    return {"available": voice is not None, "enrolled": bool(voice and voice.enrolled)}


def prompt_hint() -> str:
    """Words that help speech-to-text spell names and jargon right. No transcript lines: whisper echoes
    its prompt, so one hallucinated line would get copied into every line after it. Names from the
    knowledge base (people, glossary terms) are fine: a plain list, so it can't seed a sentence."""
    try:
        names = kb.hint_terms()
    except Exception:  # a half-written vault file must never stop transcription
        names = ""
    context = meeting.context[:300].strip()
    if context and context[-1] not in ".!?":
        context += "."
    return " ".join(x for x in (f"Meeting with {MY_NAME}.", context, f"Names: {names}." if names else "") if x)


def watch_file(path: Path) -> None:
    """Tail a transcript file that another tool keeps appending to."""
    path.touch(exist_ok=True)
    with open(path) as f:
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if line:
                add_segment(line)
            else:
                time.sleep(0.5)


# --------------------------------------------------------------------------- answers

ANSWER_SYSTEM = f"""You are {MY_NAME}'s private meeting copilot. In a live meeting, someone just \
asked {FIRST_NAME} something. Write what {FIRST_NAME} could say out loud right now, in first person, \
in {FIRST_NAME}'s own voice (see <me>).

- 1-4 natural spoken sentences. Conversational, confident, no lists, no preamble, nothing that \
sounds like an AI. Match the question: a quick "how are you" gets a one-line reply; a technical \
question gets a clear, concrete answer.
- Use what you know about {FIRST_NAME} (<me>), the knowledge-base pages, and what was said in this \
meeting (<transcript>, from room audio with some transcription errors; the question is about the \
latest part). {TRANSCRIPT_NOTE} Don't repeat what {FIRST_NAME} already said; build on it. Stay consistent with {FIRST_NAME}'s stated views and past commitments.
- Never invent personal facts only {FIRST_NAME} would know (status of their own work, dates, \
numbers, private opinions not on record). Use a short placeholder like [your ETA] or phrase it as \
something to confirm.
- General, technical or common-knowledge questions: answer from general knowledge.
- points: 0-3 very short facts to glance at while speaking (names, numbers, decisions); empty when \
not needed.
- basis: where the answer mainly comes from."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
        "basis": {"type": "string", "enum": ["your notes", "this meeting", "general knowledge", "guess"]},
    },
    "required": ["answer", "points", "basis"],
    "additionalProperties": False,
}

STYLES = {
    "shorter": "Make it shorter: one or two sentences.",
    "different": "Give a different answer from the previous one (different angle or wording).",
    "detailed": "Give a fuller answer: up to 6 sentences with specifics.",
}


def stable_prompt(system: str) -> str:
    """System prompt + the user's page + glossary + wiki index: same bytes on every call, so it gets cached."""
    glossary = kb.load_glossary_text()
    glossary = (f"\n\n<glossary note=\"canonical names; aliases are how speech-to-text mis-hears them\">\n{glossary}\n"
                "</glossary>") if glossary else ""
    return (f"{system}\n\n<me>\n{kb.load_me()}\n</me>{glossary}\n\n"
            f"<knowledge_index>\n{kb.load_index()}\n</knowledge_index>")


def kb_block(query: str, k: int = 5) -> str:
    pages = kb.find_pages(query, k=k)
    return "\n\n".join(f'<page path="{p}">\n{body}\n</page>' for p, body in pages) or "(no matching pages)"


async def answer(question: str, *, source: str, segment_id: int | None = None, style: str = "",
                 previous: str = "", asker: str = "") -> None:
    m = meeting
    broadcast("answer_pending", {"question": question, "asker": asker, "segment_id": segment_id})
    # Whole transcript first: it only grows, so consecutive calls share a cached prefix.
    transcript = meeting.full_text(upto=segment_id)
    user = f"""<transcript>
{transcript}
</transcript>

<user_context>{meeting.context or "(not provided)"}</user_context>

<knowledge_pages>
{kb_block(question + " " + transcript[-600:])}
</knowledge_pages>

<meeting_summary>
{chr(10).join("- " + p for p in meeting.analysis.get("summary", [])) or "(none yet)"}
</meeting_summary>

<question asked_by="{asker or 'unknown'}">{question}</question>"""
    if style in STYLES:
        user += f"\n\n<previous_answer>{previous}</previous_answer>\n{STYLES[style]}"
    try:
        data = await llm.complete(MAIN_MODEL, stable_prompt(ANSWER_SYSTEM), user, schema=ANSWER_SCHEMA,
                                  max_tokens=3000)
    except Exception as e:
        broadcast("answer_failed", {"question": question, "error": str(e)})
        broadcast("status", f"LLM error: {e}")
        return
    finally:
        broadcast_usage()
    if m is not meeting:  # a new meeting started while this was running
        return
    item = {"id": len(meeting.answers), "question": question, "asker": asker, "clock": meeting.clock(time.time()),
            "source": source, "segment_id": segment_id, **data}
    meeting.answers.append(item)
    broadcast("answer", item)
    if segment_id is not None:
        set_flag(segment_id, "answered")
    session_dir.mkdir(parents=True, exist_ok=True)
    with open(session_dir / "answers.md", "a") as f:
        f.write(f"**[{item['clock']}] {asker or 'Q'}:** {question}\n\n{data['answer']}\n\n")


async def flag_worker() -> None:
    """Stage B: a cheap model confirms the flagged line is a question for the user, then we answer."""
    while True:
        seg, reason = await flags.get()
        while not flags.empty():  # only the newest flag matters
            set_flag(seg.id, "skipped", "a newer line came in")
            seg, reason = flags.get_nowait()
        recent = meeting.recent_text(90, upto=seg.id)
        handled = [a["question"] for a in meeting.answers[-5:]]
        if handled:
            recent += "\n\n(Already answered for the user: " + " | ".join(handled) + ")"
        try:
            verdict = await triage(recent, seg.text, meeting.context, about_me=kb.load_me()[:1500])
        except Exception as e:
            set_flag(seg.id, "skipped", "check failed")
            broadcast("status", f"LLM error: {e}")
            continue
        finally:
            broadcast_usage()
        if not is_current(seg):
            continue
        if not verdict["respond"]:
            set_flag(seg.id, "skipped", verdict["reason"])
            continue
        set_flag(seg.id, "answering")
        await answer(verdict["question"] or seg.text, source=reason, segment_id=seg.id, asker=verdict["asker"])


# --------------------------------------------------------------------------- questions to ask

# Meeting type -> (label, what good questions look like in that kind of meeting). These are the defaults; the user
# can add, edit and remove types in Settings, saved to MEETING_TYPES_FILE. "{me}" in a lens becomes the first name.
DEFAULT_MEETING_TYPES = {
    "standup": ("Standup", "Short status updates. Ask rarely and briefly: about a blocker someone \
mentioned, a dependency that touches {me}'s work, or who to pair with. Never a deep design question."),
    "refinement": ("Refinement / grooming", "Stories are being shaped. Ask about acceptance criteria, \
edge cases and bad input, which system or table the data comes from, what is out of scope, and \
whether the estimate hides unknowns."),
    "design": ("Design / architecture", "A solution is being chosen. Ask about tradeoffs between the \
options, failure modes (retries, duplicates, partial failure, late data), contracts with other \
services, data ownership, scale, and how to roll back."),
    "knowledge_transfer": ("Onboarding / walkthrough", "Someone is explaining a system {me} will work \
on. Ask to confirm understanding of the flow, where the code, config, logs or data live, how to \
run or test it locally, and what usually breaks."),
    "one_on_one": ("1:1 with lead", "Ask about priorities, expectations for {me}'s work, feedback, \
who to go to for what, and how to take on more ownership."),
    "planning": ("Sprint / project planning", "Ask about priority order, dependencies between \
tickets, what blocks {me}'s items, deadlines and who decides scope."),
    "general": ("General", "Use the angles below as the moment calls for."),
}
MEETING_TYPES_FILE = SESSIONS_DIR / "meeting_types.json"


def load_meeting_types() -> dict[str, tuple[str, str]]:
    try:
        saved = json.loads(MEETING_TYPES_FILE.read_text())
        types = {str(k): (str(v["label"]), str(v.get("lens", ""))) for k, v in saved.items() if v.get("label")}
        return types or dict(DEFAULT_MEETING_TYPES)
    except (OSError, ValueError, AttributeError, TypeError):
        return dict(DEFAULT_MEETING_TYPES)


def save_meeting_types() -> None:
    MEETING_TYPES_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEETING_TYPES_FILE.write_text(json.dumps({k: {"label": l, "lens": t} for k, (l, t) in MEETING_TYPES.items()},
                                             indent=2))


MEETING_TYPES = load_meeting_types()  # edited in place, so every reference sees changes


def default_lens(label: str) -> str:
    return f"A {label} meeting. Use the angles below as the moment calls for."

QUESTION_ANGLES = {
    "confirm": "Confirm understanding",
    "edge_case": "Edge cases & failure",
    "contract": "Ownership & contracts",
    "rollout": "Testing & rollout",
    "priority": "Scope & priority",
    "connect": "Links to earlier",
}

def lenses_text() -> str:
    return "\n".join(f"- {k} ({label}): {(text or default_lens(label)).replace('{me}', FIRST_NAME)}"
                     for k, (label, text) in MEETING_TYPES.items())

QUESTIONS_SYSTEM = f"""You help {FIRST_NAME} decide what to ask in a live meeting. {FIRST_NAME} reads \
your suggestions silently and may say one out loud. <me> says who {FIRST_NAME} is (role, \
experience, what they work on); the knowledge pages hold what they already learned in earlier \
meetings. The transcript comes from room audio with some transcription errors. {TRANSCRIPT_NOTE} \
Never suggest something {FIRST_NAME} already asked or said.

Think like a strong mid-level to senior engineer on {FIRST_NAME}'s team: someone who wants to \
understand the system well enough to build and support it, spots risk early, and asks one sharp \
question rather than three vague ones.

Step 1, meeting type. <meeting_type> either names the type or says to infer it. When inferring, \
pick the closest type from the transcript. Then use that type's lens:
@@LENSES@@

Step 2, write 6 candidates from different angles (at least 4 different angles; weight them by the \
lens; skip an angle that doesn't fit this moment):
- confirm: restate what {FIRST_NAME} understood and ask if it's right ("So if I've got this right, \
X feeds Y nightly, is that correct?"). The most natural question a newer team member can ask.
- edge_case: what happens on retries, duplicates, nulls, partial failure, late or bad data.
- contract: who owns the table, API or decision; who consumes it; does a change break anyone.
- rollout: how it gets tested, feature flag, rollback, monitoring, what "done" means.
- priority: is it in this sprint, what blocks it, what comes first, who decides.
- connect: tie it to something from earlier meetings or the knowledge pages ("Last week Jane \
said the crosswalk lives in BigQuery, is this the same source?").

A great question:
- is prompted by something said in the last couple of minutes, or by <focus> when given \
(prompted_by: a short quote);
- is not answered already, in this meeting or in the knowledge pages;
- names the actual system, field, table, ticket, person or number being discussed;
- sounds like {FIRST_NAME} talking: one natural spoken sentence under 30 words, curious and polite, \
often "So…", "Just to make sure…", "How do we handle…", "Who owns…". Not a quiz, not a challenge, \
not two questions stacked together.

Weak vs strong:
- Weak: "Can you explain the retry logic?" Strong: "If the retry fires after we've already \
published to Kafka, do we dedupe on claim ID, or could the rebate get counted twice?"
- Weak: "What are the next steps?" Strong: "Is the rate grid piece blocked on the GPO data \
feed, or can I start on the CRUD endpoints this sprint?"
- Weak: "How does the data flow work?" Strong: "Just to make sure I follow: product groups come \
from the GPO feed, get enriched with Drug IQ, then land in BigQuery for reporting, right?"
- Weak: "Are there any risks?" Strong: "What happens to in-flight claims if we roll this back \
mid-cycle, do they get reprocessed or do we lose them?"

Avoid: generic prompts unless made specific; anything in <already_suggested> or a rewording of \
it; things easy to look up later; things the speaker is clearly about to explain next; small talk.

For each candidate: question; angle; why_now (under 15 words: why this moment); prompted_by; \
answered_already (true if the transcript or pages already answer it); score 1-10 against the \
criteria and the lens. picks: indexes of the best 0-2 candidates, best first. Only pick score >= 7 \
and not answered_already. Picking nothing is fine when nothing is worth asking. meeting_type: the \
type you used."""

QUESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "meeting_type": {"type": "string"},  # enum filled in per call from MEETING_TYPES
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "angle": {"type": "string", "enum": list(QUESTION_ANGLES)},
                    "why_now": {"type": "string"},
                    "prompted_by": {"type": "string"},
                    "answered_already": {"type": "boolean"},
                    "score": {"type": "integer"},
                },
                "required": ["question", "angle", "why_now", "prompted_by", "answered_already", "score"],
                "additionalProperties": False,
            },
        },
        "picks": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["meeting_type", "candidates", "picks"],
    "additionalProperties": False,
}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", text.lower())) - kb.STOPWORDS


def is_repeat(question: str, earlier: list[str]) -> bool:
    """Same question reworded? Word overlap or character similarity against everything suggested so far."""
    w = _words(question)
    for e in earlier:
        ew = _words(e)
        if w and ew and len(w & ew) / len(w | ew) >= 0.5:
            return True
        if difflib.SequenceMatcher(None, question.lower(), e.lower()).ratio() >= 0.7:
            return True
    return False


def question_examples() -> str:
    """Questions the user marked good / not useful in past meetings, to steer toward their taste."""
    if not QUESTION_FEEDBACK.exists():
        return ""
    good, bad = [], []
    for line in QUESTION_FEEDBACK.read_text().splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        (good if item.get("verdict") == "good" else bad).append(item.get("question", ""))
    parts = []
    if good:
        parts.append("Questions {0} liked:\n".format(FIRST_NAME) + "\n".join(f"- {q}" for q in good[-8:]))
    if bad:
        parts.append("Questions {0} found not useful:\n".format(FIRST_NAME) + "\n".join(f"- {q}" for q in bad[-8:]))
    return "\n\n".join(parts)


def meeting_type_hint() -> str:
    if meeting.meeting_type in MEETING_TYPES:
        return f"{meeting.meeting_type} (set by {FIRST_NAME})"
    return "infer it from the transcript"


def request_questions(reason: str, force: bool = False, focus: str = "") -> bool:
    """Suggest questions now, unless a run is in progress or one just happened."""
    if suggest_lock.locked() or not meeting.segments:
        return False
    if not force and time.time() - meeting.last_suggest_t < QUESTIONS_COOLDOWN_S:
        return False
    asyncio.create_task(suggest_questions(reason, focus=focus, asked=force))
    return True


async def suggest_questions(reason: str, focus: str = "", asked: bool = False) -> None:
    async with suggest_lock:
        m = meeting
        meeting.last_suggest_t = time.time()
        meeting.words_at_last_suggest = meeting.word_count()
        transcript = meeting.full_text()
        earlier = [q["question"] for q in meeting.suggestions]
        user = f"""<transcript elapsed="{meeting.clock(time.time())}">
{transcript}
</transcript>

<user_context>{meeting.context or "(not provided)"}</user_context>

<knowledge_pages>
{kb_block(focus + " " + transcript[-1500:])}
</knowledge_pages>

<meeting_summary>
{chr(10).join("- " + p for p in meeting.analysis.get("summary", [])) or "(none yet)"}
</meeting_summary>

<taste>
{question_examples() or "(no feedback yet)"}
</taste>

<already_suggested>
{chr(10).join("- " + q for q in earlier) or "(none)"}
</already_suggested>

<meeting_type>{meeting_type_hint()}</meeting_type>

Why now: {reason}."""
        if focus:
            user += f"\n\n<focus>{focus}</focus>\nQuestions should be about this."
        broadcast("questions_pending", {"reason": reason})
        try:
            # Built from the current type list: the same bytes on every call until the user edits the types.
            system = QUESTIONS_SYSTEM.replace("@@LENSES@@", lenses_text())
            schema = {**QUESTIONS_SCHEMA, "properties": {
                **QUESTIONS_SCHEMA["properties"], "meeting_type": {"type": "string", "enum": list(MEETING_TYPES)}}}
            data = await llm.complete(QUESTIONS_MODEL, stable_prompt(system), user,
                                      schema=schema, max_tokens=6000, effort=QUESTIONS_EFFORT)
        except Exception as e:
            broadcast("questions", {"items": [], "reason": reason, "error": str(e)})
            broadcast("status", f"LLM error: {e}")
            return
        finally:
            broadcast_usage()
        if m is not meeting:
            return
        cands = data["candidates"]
        items = []
        if meeting.meeting_type == "auto":
            meeting.detected_type = data["meeting_type"]

        def keep(c: dict) -> None:
            earlier.append(c["question"])
            items.append({"id": len(meeting.suggestions), "question": clean(c["question"]), "why": c["why_now"],
                          "angle": QUESTION_ANGLES.get(c["angle"], ""), "clock": meeting.clock(time.time()), "reason": reason})
            meeting.suggestions.append(items[-1])

        for i in data["picks"]:
            if not 0 <= i < len(cands):
                continue
            c = cands[i]
            if c["answered_already"] or c["score"] < 7 or is_repeat(c["question"], earlier):
                continue
            keep(c)
            if len(items) == 2:
                break
        if asked and not items:  # the user asked, so offer the best fresh candidate even if it scored low
            fresh = [c for c in cands if not c["answered_already"] and not is_repeat(c["question"], earlier)]
            if fresh:
                keep(max(fresh, key=lambda c: c["score"]))
        broadcast("questions", {"items": items, "reason": reason, "detected_type": meeting.detected_type})
        if items:
            session_dir.mkdir(parents=True, exist_ok=True)
            with open(session_dir / "questions.md", "a") as f:
                f.writelines(f"- [{q['clock']}] {q['question']}  _({q['why']})_\n" for q in items)


# --------------------------------------------------------------------------- running summary

SUMMARY_SYSTEM = f"""You keep running notes of a meeting for one participant (the user, {FIRST_NAME}). \
You read a transcript captured live from room audio, so expect transcription errors and fragments \
cut mid-sentence. Infer meaning charitably. {TRANSCRIPT_NOTE}

You get the user's context, your previous notes, and the most recent stretch of transcript. \
Produce an updated view (plain text items, no leading dashes or bullets):

- summary: 3-8 short bullets covering the whole meeting so far (fold the new material into the \
previous summary; don't drop earlier points that still matter).
- current_topic: what is being discussed right now, in under 12 words.
- decisions, action_items (include owner and date when stated), open_issues: short phrases, \
cumulative across the meeting, at most 8 each: merge related items and drop ones that got \
resolved. Empty lists are fine."""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "array", "items": {"type": "string"}},
        "current_topic": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "action_items": {"type": "array", "items": {"type": "string"}},
        "open_issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "current_topic", "decisions", "action_items", "open_issues"],
    "additionalProperties": False,
}


def clean(item: str) -> str:
    """Models sometimes start list items with their own "- " or "• "."""
    return re.sub(r"^\s*([-*•]\s*)+", "", item).strip()


async def summarize() -> None:
    if not meeting.segments or analysis_lock.locked():
        return
    async with analysis_lock:
        m = meeting
        since = meeting.last_analysis_t - 60 if meeting.analysis else 0.0  # everything new, plus a little overlap
        meeting.words_at_last_analysis = meeting.word_count()
        meeting.last_analysis_t = time.time()
        prev = meeting.analysis
        user = f"""<user_context>
{meeting.context or "(not provided)"}
</user_context>

<previous_summary>
{chr(10).join("- " + p for p in prev.get("summary", [])) or "(none yet)"}
</previous_summary>

<previous_lists>
decisions: {json.dumps(prev.get("decisions", []))}
action_items: {json.dumps(prev.get("action_items", []))}
open_issues: {json.dumps(prev.get("open_issues", []))}
</previous_lists>

<recent_transcript elapsed="{meeting.clock(time.time())}">
{meeting.full_text(since=since)}
</recent_transcript>"""
        broadcast("status", "updating notes…")
        try:
            data = await llm.complete(MAIN_MODEL, SUMMARY_SYSTEM, user, schema=SUMMARY_SCHEMA)
        except Exception as e:
            broadcast("status", f"LLM error: {e}")
            return
        finally:
            broadcast_usage()
        if m is not meeting:
            return
        for key in ("summary", "decisions", "action_items", "open_issues"):
            data[key] = [clean(x) for x in data[key] if clean(x)]
        topic_changed = prev and prev.get("current_topic") != data["current_topic"]
        meeting.analysis = data
        data["clock"] = meeting.clock(time.time())
        broadcast("analysis", data)
        broadcast("status", "paused" if meeting.paused else "listening")
        save_summary()
        if topic_changed and meeting.auto:
            request_questions(f"the topic moved on to: {data['current_topic']}")


def save_summary() -> None:
    a = meeting.analysis
    session_dir.mkdir(parents=True, exist_ok=True)
    sections = [
        ("Summary", a.get("summary", [])),
        ("Decisions", a.get("decisions", [])),
        ("Action items", a.get("action_items", [])),
        ("Open issues", a.get("open_issues", [])),
        ("Questions suggested", [q["question"] for q in meeting.suggestions]),
    ]
    body = "\n\n".join(f"## {t}\n" + "\n".join(f"- {x}" for x in items) for t, items in sections)
    (session_dir / "summary.md").write_text(f"# Meeting {meeting_id}\n\n{body}\n")


async def summary_loop() -> None:
    while True:
        try:
            await asyncio.wait_for(summarize_now.wait(), timeout=5)
            summarize_now.clear()
            await summarize()
            continue
        except asyncio.TimeoutError:
            pass
        if meeting.paused or not meeting.auto:
            continue
        new_words = meeting.word_count() - meeting.words_at_last_analysis
        if time.time() - meeting.last_analysis_t >= SUMMARY_EVERY_S and new_words >= SUMMARY_MIN_NEW_WORDS:
            await summarize()
        # Fallback when nobody says "any questions?" for a while.
        since = meeting.word_count() - meeting.words_at_last_suggest
        if time.time() - max(meeting.last_suggest_t, meeting.started) >= QUESTIONS_EVERY_S and since >= 150:
            request_questions("a few minutes have passed since the last suggestions")


def save_to_vault() -> None:
    if meeting.segments:
        lines = [s.line(meeting.clock(s.t)) for s in meeting.segments]
        kb.save_raw(meeting_id, lines, meeting.context)


weekly_lock = asyncio.Lock()
weekly_busy: str | None = None  # the week being written, for the Weekly page


async def make_weekly(monday) -> str:
    """Write one week's review (one LLM call) and tell the UI."""
    global weekly_busy
    async with weekly_lock:
        weekly_busy = monday.isoformat()
        broadcast("weekly", {"state": "writing", "week": weekly_busy})
        try:
            result = await kb.write_weekly(monday)
            broadcast("weekly", {"state": "ready", "week": weekly_busy})
            return result
        except Exception as e:
            broadcast("weekly", {"state": "failed", "week": weekly_busy, "error": str(e)})
            raise
        finally:
            weekly_busy = None
            broadcast_usage()


async def weekly_loop() -> None:
    """From WEEKLY_DAY at WEEKLY_HOUR, write this week's review once; also last week's if the app was off then."""
    tried = set()
    while True:
        now = datetime.now()
        this_week = kb.week_of(now.date())
        due = [this_week - timedelta(days=7)]
        if (now.weekday(), now.hour) >= (WEEKLY_DAY, WEEKLY_HOUR):
            due.append(this_week)
        for monday in due:
            if monday not in tried and not (kb.WEEKLY / f"{monday}.md").exists() and kb.meetings_in_week(monday):
                tried.add(monday)
                try:
                    print(await make_weekly(monday))
                except Exception as e:
                    print(f"weekly review for {monday} failed: {e}")
        await asyncio.sleep(600)


def kb_stats() -> dict:
    return {"vault": str(VAULT_DIR), "pages": len(kb.wiki_pages()), "pending": len(kb.pending_meetings()),
            "stale": len(kb.stale_meetings())}


# --------------------------------------------------------------------------- web

@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop
    loop = asyncio.get_running_loop()
    kb.ensure_vault()
    tasks = [asyncio.create_task(summary_loop()), asyncio.create_task(flag_worker())]
    if WEEKLY_DAY is not None:
        tasks.append(asyncio.create_task(weekly_loop()))
    for start in app.state.starters:
        start()
    yield
    for t in tasks:
        t.cancel()
    if meeting.analysis:
        save_summary()
    if not meeting.ended:
        save_to_vault()
    end_voice_meeting()
    print(f"\nLLM usage: {json.dumps(llm.usage_summary())}")
    if kb.pending_meetings():
        print("Run `python kb.py compile` to add this meeting to your knowledge base.")


app = FastAPI(lifespan=lifespan)


WEB_DIST = ROOT / "web" / "dist"  # React UI, built with `cd web && npm run build`
if (WEB_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.get("/")
async def index():
    built = WEB_DIST / "index.html"
    return FileResponse(built if built.exists() else ROOT / "static" / "index.html")


@app.get("/classic")
async def classic():
    return FileResponse(ROOT / "static" / "index.html")


def config_payload() -> dict:
    return {"context": meeting.context, "paused": meeting.paused, "auto": meeting.auto,
            "sensitivity": meeting.sensitivity,
            "meeting_type": meeting.meeting_type, "detected_type": meeting.detected_type,
            "meeting_types": {k: label for k, (label, _) in MEETING_TYPES.items()},
            "meeting_type_lenses": {k: lens for k, (_, lens) in MEETING_TYPES.items()},
            "meeting_types_custom": MEETING_TYPES_FILE.exists(), "mode": app.state.mode, "name": MY_NAME,
            "models": {"triage": TRIAGE_MODEL, "main": MAIN_MODEL, "questions": QUESTIONS_MODEL, "chat": CHAT_MODEL,
                       "speech": TRANSCRIBE_BACKEND},
            "kb": kb_stats(), "ended": meeting.ended, "started": meeting.started, "meeting_id": meeting_id,
            "voice": {**voice_info(), **getattr(app.state, "voice_state", {})}, "voices": voices_payload()}


def voices_payload() -> dict:
    """Named Voices for the UI: {"s3": {"name": "Priya", "me": false, "person": "people/priya"}}."""
    return {speakers.voice(n): {"name": who, "me": path == "me", "person": path}
            for n, (path, who) in kb.voice_people().items()}


@app.get("/events")
async def events(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    subscribers.add(q)
    # Replay current state for a freshly opened tab.
    q.put_nowait(json.dumps({"kind": "config", "data": config_payload()}))
    for s in meeting.segments:
        q.put_nowait(json.dumps({"kind": "segment", "data": s.payload(meeting.clock(s.t))}))
    for f in meeting.flags.values():
        q.put_nowait(json.dumps({"kind": "flag", "data": f}))
    for a in meeting.answers:
        q.put_nowait(json.dumps({"kind": "answer", "data": a}))
    if meeting.suggestions:
        q.put_nowait(json.dumps({"kind": "questions", "data": {"items": meeting.suggestions, "replay": True}}))
    if meeting.analysis:
        q.put_nowait(json.dumps({"kind": "analysis", "data": meeting.analysis}))
    if meeting.brief:
        q.put_nowait(json.dumps({"kind": "brief", "data": meeting.brief}))
    q.put_nowait(json.dumps({"kind": "usage", "data": llm.usage_summary()}))

    async def stream():
        try:
            while not await request.is_disconnected():
                try:
                    yield f"data: {await asyncio.wait_for(q.get(), timeout=15)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            subscribers.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/meetings")
async def meetings():
    return [m for m in kb.list_meetings() if m["id"] != meeting_id]


@app.get("/people")
async def people():
    """Names for the Speaker name box's autocomplete."""
    return kb.list_people()


@app.get("/meetings/{mid}")
async def meeting_detail(mid: str):
    return kb.load_meeting(mid) or {"error": "not found"}


@app.get("/weekly")
async def weekly_list():
    return {"weeks": kb.list_weeks(), "busy": weekly_busy}


@app.get("/weekly/{week}")
async def weekly_detail(week: str):
    return kb.load_weekly(week) or {"error": "not found"}


@app.post("/weekly")
async def weekly_write(body: dict | None = None):
    """Write (or rewrite) the review for the week containing body["week"] (default: this week)."""
    try:
        day = datetime.strptime((body or {}).get("week") or datetime.now().strftime("%Y-%m-%d"), "%Y-%m-%d").date()
        monday = kb.week_of(day)
        await make_weekly(monday)
    except Exception as e:
        return {"error": str(e)}
    return kb.load_weekly(monday.isoformat())


@app.post("/meeting_types")
async def edit_meeting_types(body: dict):
    """Settings -> Meeting type -> Edit: add {label, lens}, update {key, label, lens}, remove {key}, or reset."""
    action = body.get("action")
    label = str(body.get("label") or "").strip()[:60]
    lens = str(body.get("lens") or "").strip()[:600]
    key = str(body.get("key") or "")
    if action == "add":
        if not label:
            return {"ok": False, "error": "Give the type a name."}
        if label.lower() in (l.lower() for l, _ in MEETING_TYPES.values()):
            return {"ok": False, "error": f"There's already a type called {label}."}
        base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "type"
        key, n = base, 2
        while key in MEETING_TYPES or key == "auto":
            key, n = f"{base}_{n}", n + 1
        MEETING_TYPES[key] = (label, lens)
    elif action == "update" and key in MEETING_TYPES:
        MEETING_TYPES[key] = (label or MEETING_TYPES[key][0], lens)
    elif action == "remove" and key in MEETING_TYPES:
        if len(MEETING_TYPES) == 1:
            return {"ok": False, "error": "Keep at least one type."}
        del MEETING_TYPES[key]
    elif action == "reset":
        MEETING_TYPES.clear()
        MEETING_TYPES.update(DEFAULT_MEETING_TYPES)
        MEETING_TYPES_FILE.unlink(missing_ok=True)
    else:
        return {"ok": False, "error": "Unknown type."}
    if action != "reset":
        save_meeting_types()
    if meeting.meeting_type not in MEETING_TYPES:
        meeting.meeting_type = "auto"
    if meeting.detected_type not in MEETING_TYPES:
        meeting.detected_type = ""
    c = config_payload()  # not broadcast: a "config" event makes the UI reset as if reconnecting
    return {"ok": True, "key": key, "config": {k: c[k] for k in ("meeting_types", "meeting_type_lenses",
                                                                 "meeting_types_custom", "meeting_type", "detected_type")}}


@app.post("/settings")
async def settings(body: dict):
    if "context" in body:
        meeting.context = str(body["context"])[:4000]
        schedule_auto_brief()
    if body.get("meeting_type") in ("auto", *MEETING_TYPES):
        meeting.meeting_type = body["meeting_type"]
    if body.get("sensitivity") in ("low", "normal", "high"):
        meeting.sensitivity = body["sensitivity"]
    if "auto" in body:
        meeting.auto = bool(body["auto"])
    return config_payload()


@app.post("/answer")
async def answer_now(body: dict):
    """Answer on demand: a transcript line (segment_id), a typed question, or a redo of an answer."""
    seg_id = body.get("segment_id")
    question = (body.get("question") or "").strip()
    if seg_id is not None and 0 <= seg_id < len(meeting.segments):
        question = question or meeting.segments[seg_id].text
    elif not question and meeting.segments:
        seg_id = meeting.segments[-1].id
        question = meeting.segments[-1].text
    if not question:
        return {"ok": False}
    asyncio.create_task(answer(question, source="you asked", segment_id=seg_id,
                               style=body.get("style", ""), previous=body.get("previous", "")))
    return {"ok": True}


@app.post("/feedback")
async def feedback(body: dict):
    session_dir.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({**body, "meeting": meeting_id, "t": time.time()}) + "\n"
    with open(session_dir / "feedback.jsonl", "a") as f:
        f.write(entry)
    if body.get("kind") == "question":  # kept across meetings to learn which questions you like
        with open(QUESTION_FEEDBACK, "a") as f:
            f.write(entry)
    return {"ok": True}


@app.post("/questions")
async def questions_now(body: dict | None = None):
    """Question ideas on demand, optionally about a typed topic (focus) or a transcript line (segment_id)."""
    body = body or {}
    focus = (body.get("focus") or "").strip()[:1000]
    seg_id = body.get("segment_id")
    if seg_id is not None and 0 <= seg_id < len(meeting.segments):
        focus = focus or f'what was said at [{meeting.clock(meeting.segments[seg_id].t)}]: "{meeting.segments[seg_id].text}"'
    reason = "the user asked for question ideas" + (" about a specific point" if focus else "")
    return {"ok": request_questions(reason, force=True, focus=focus)}


BRIEF_SYSTEM = f"""You brief {MY_NAME} for a meeting that is about to start or just started, from their \
meeting knowledge base: wiki pages about people, projects and topics, open threads (who owes what), and the last \
meeting with the same people. {FIRST_NAME} reads it in under a minute, so be specific and short: names, systems, \
dates, numbers. Write to {FIRST_NAME} as "you". Return:
- who: the people likely involved, each with one line: who they are to {FIRST_NAME} and what is open with them.
- open_threads: open items that matter for this meeting, most important first; say who owes whom.
- recent_decisions: earlier decisions this meeting should respect or may revisit.
- watch_for: 2-4 things to raise, check or be careful about.
Use only what the material supports; an empty list is better than a guess. {TRANSCRIPT_NOTE}"""

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "who": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"},
                                                                           "line": {"type": "string"}},
                                           "required": ["name", "line"], "additionalProperties": False}},
        **{k: {"type": "array", "items": {"type": "string"}} for k in ("open_threads", "recent_decisions", "watch_for")},
    },
    "required": ["who", "open_threads", "recent_decisions", "watch_for"],
    "additionalProperties": False,
}
brief_lock = asyncio.Lock()
_auto_brief: asyncio.Task | None = None


async def write_brief(focus: str = "") -> None:
    """One MAIN_MODEL call: what to know going into this meeting, from the knowledge base."""
    m = meeting
    recent = meeting.full_text()[-1500:] if meeting.segments else ""
    query = " ".join(x for x in (focus, meeting.context, recent) if x).strip()
    if not query:
        broadcast("brief", {"state": "failed", "error": "Describe the meeting in Settings, or type a topic first."})
        return
    async with brief_lock:
        broadcast("brief", {"state": "writing"})
        pages = kb.find_pages(query, k=8, per_page=1500)
        paths = [p for p, _ in pages]
        paths += [p for p in kb.mentioned_pages(query, kinds=("people",)) if p not in paths]
        spoke = [p for p in kb.speakers_in(s.speaker for s in meeting.segments)[0] if p.startswith("people/")]
        paths += [p for p in spoke if p not in paths]  # who is actually talking in this meeting
        threads = kb.related_threads(paths, query)
        last = kb.last_meeting_with(spoke or [p for p in paths if p.startswith("people/")], exclude=meeting_id)
        pages_block = "\n\n".join(f'<page path="{p}">\n{body}\n</page>' for p, body in pages) or "(no matching pages)"
        user = f"""<meeting_context>{meeting.context or "(not provided)"}</meeting_context>
<focus>{focus or "(none)"}</focus>

<knowledge_pages>
{pages_block}
</knowledge_pages>

<open_threads>
{chr(10).join(kb.thread_line(t) for t in threads) or "(none)"}
</open_threads>

<last_meeting_with_them id="{last[0] if last else ''}">
{last[1][:2500] if last else "(none)"}
</last_meeting_with_them>

<transcript_so_far>
{recent or "(not started)"}
</transcript_so_far>"""
        try:
            data = await llm.complete(MAIN_MODEL, stable_prompt(BRIEF_SYSTEM), user, schema=BRIEF_SCHEMA,
                                      max_tokens=3000)
        except Exception as e:
            if m is meeting:
                broadcast("brief", {"state": "failed", "error": str(e)})
            return
        finally:
            broadcast_usage()
        if m is not meeting:
            return
        data.update(state="ready", focus=focus or meeting.context[:120], clock=meeting.clock(time.time()))
        meeting.brief = data
        broadcast("brief", data)


def schedule_auto_brief() -> None:
    """Automatic mode: brief once, after the meeting description has stopped changing for a few seconds."""
    global _auto_brief
    if _auto_brief and not _auto_brief.done():
        _auto_brief.cancel()
    if not (meeting.auto and len(meeting.context.strip()) >= 15 and not meeting.brief):
        return

    async def later() -> None:
        await asyncio.sleep(8)
        await write_brief()

    _auto_brief = asyncio.create_task(later())


@app.post("/brief")
async def brief_now(body: dict | None = None):
    """Brief me: who's in this meeting, what's open with them, past decisions. Runs in the background."""
    focus = str((body or {}).get("focus") or "").strip()[:500]
    if brief_lock.locked():
        return {"ok": False, "error": "busy"}
    asyncio.create_task(write_brief(focus))
    return {"ok": True}


@app.post("/summarize")
async def summarize_endpoint():
    summarize_now.set()
    return {"ok": True}


@app.post("/pause")
async def pause(body: dict):
    meeting.paused = bool(body.get("paused"))
    broadcast("status", "paused" if meeting.paused else "listening")
    return {"paused": meeting.paused}


@app.post("/segment")
async def post_segment(body: dict):
    """Paste text manually (or POST from another tool). Optional `voice` (a Voice number) or `speaker`
    (a speakers.py label: "me", "s3", or the old "them")."""
    speaker = speakers.voice(int(body["voice"])) if body.get("voice") else body.get("speaker", "")
    for line in body.get("text", "").splitlines():
        add_segment(line, speaker)
    return {"ok": True}


ASK_SYSTEM = f"""You are {FIRST_NAME}'s private assistant for their work meetings. Answer their questions \
using the live transcript of the meeting in progress, the knowledge base built from past meetings (the index \
below, plus the pages attached to each question), and the earlier turns of this chat.

- Answer the question directly first, then give the specifics that support it: names, systems, numbers, \
dates, and which meeting it came from (by date).
- Be as long as the question needs and no longer. Use short "- " bullets for lists. Plain text, no markdown \
headings or bold.
- Separate what was actually said from your own reading of it. If the transcript and the knowledge base \
don't cover something, say so plainly instead of guessing, and say what would answer it.
- The transcript is auto-transcribed: read through obvious mis-hearings. {TRANSCRIPT_NOTE}"""


@app.post("/ask")
async def ask(body: dict):
    """Ask a private question about this meeting or past ones. `history`: earlier turns of the chat."""
    q = body.get("q", "")
    history = [h for h in body.get("history", [])[-8:] if isinstance(h, dict) and h.get("text")]
    chat = "\n".join(f"<{'me' if h.get('role') == 'me' else 'you'}>{h['text']}</{'me' if h.get('role') == 'me' else 'you'}>"
                     for h in history)
    lookup = q + " " + " ".join(h["text"] for h in history[-2:])
    user = (f"<transcript>\n{meeting.full_text() or '(no meeting in progress)'}\n</transcript>\n\n"
            f"<user_context>{meeting.context or '(not provided)'}</user_context>\n\n"
            f"<knowledge_pages>\n{kb_block(lookup, k=8)}\n</knowledge_pages>\n\n"
            f"<now>{datetime.now().strftime('%A %Y-%m-%d %H:%M')}</now>\n\n"
            + (f"<earlier_chat>\n{chat}\n</earlier_chat>\n\n" if chat else "") + f"<question>{q}</question>")
    try:
        return {"answer": await llm.complete(CHAT_MODEL, stable_prompt(ASK_SYSTEM), user, max_tokens=6000,
                                             effort=CHAT_EFFORT)}
    except Exception as e:
        return {"answer": f"Error: {e}"}
    finally:
        broadcast_usage()


@app.post("/new")
async def new_meeting():
    """Finish the current meeting (saved and added to the knowledge base, like End) and start a new one."""
    if meeting.segments and not meeting.ended:
        await end_meeting()
    reset_meeting()
    broadcast("config", config_payload())
    broadcast_usage()
    broadcast("status", "listening")
    return {"ok": True, "id": meeting_id}


@app.post("/voice/enroll")
async def voice_enroll():
    """Record the user's voice from the mic for the next ~20 s of speech."""
    transcriber = getattr(app.state, "transcriber", None)
    if transcriber is None or transcriber.voice is None:
        return {"ok": False, "error": "Voice ID needs the microphone (and VOICE_ID=on)."}
    transcriber.start_enroll()
    return {"ok": True}


@app.post("/voice/cancel")
async def voice_cancel():
    transcriber = getattr(app.state, "transcriber", None)
    if transcriber is not None:
        transcriber.cancel_enroll()
    set_voice({"state": "idle"})
    return {"ok": True}


@app.post("/voices/name")
async def name_voice(body: dict):
    """Link a Voice to a Person: {"voice": 3, "name": "Priya"}. Applies to every Meeting that Voice is in."""
    try:
        number = int(body.get("voice"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Which speaker?"}
    name = " ".join(str(body.get("name", "")).split())
    if number < 1 or not name:
        return {"ok": False, "error": "Type a name."}
    path = kb.name_voice(number, name)
    voice = getattr(app.state, "voice", None)
    if path == "me" and voice is not None and voice.registry.me is None:
        voice.registry.me = number  # "Record my voice" now refreshes this Voice
        voice.registry.save()
    payload = voices_payload()
    broadcast("voices", payload)  # not "config": the UI treats that as a reconnect and resets
    broadcast("kb", kb_stats())  # naming may have marked Meeting notes for recompile
    return {"ok": True, "person": path, "voices": payload}


@app.post("/voices/forget")
async def forget_voice(body: dict):
    """Delete one Voice's print: {"voice": 3}. Its name stays on past lines; it will come back as a new Speaker N."""
    try:
        number = int(body.get("voice"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Which speaker?"}
    voice = getattr(app.state, "voice", None)
    if voice is not None:
        registry = voice.registry
    else:  # not listening (paste-only mode): edit the saved prints directly
        from voice import VOICES_FILE
        from voices import Registry

        registry = Registry(VOICES_FILE)
    if not registry.forget(number):
        return {"ok": False, "error": f"No voice print is saved for Speaker {number}."}
    set_voice({"state": "idle"})  # Settings' "Voice set up" may have changed
    return {"ok": True}


@app.post("/voice/forget")
async def voice_forget():
    transcriber = getattr(app.state, "transcriber", None)
    if transcriber is not None:
        transcriber.cancel_enroll()
    if getattr(app.state, "voice", None):
        app.state.voice.forget()
    set_voice({"state": "idle"})
    return {"ok": True}


recompile_lock = asyncio.Lock()


@app.post("/recompile")
async def recompile():
    """Rewrite the Meeting notes marked needs_recompile (one MAIN_MODEL call each). Only ever on this click."""
    ids = kb.stale_meetings()
    if not ids:
        return {"ok": True, "meetings": 0}
    if recompile_lock.locked():
        return {"ok": False, "error": "Already recompiling."}

    async def run():
        async with recompile_lock:
            broadcast("kb", {"state": "compiling", **kb_stats()})
            results = []
            for mid in ids:
                try:
                    results.append(await kb.compile_meeting(mid))
                except Exception as e:
                    results.append(f"{mid}: failed ({e})")
                broadcast("status", results[-1])
            broadcast_usage()
            broadcast("kb", {"state": "done", "results": results, **kb_stats()})

    asyncio.create_task(run())
    return {"ok": True, "meetings": len(ids)}


@app.post("/end")
async def end_meeting():
    """Stop listening, write final notes, and fold the meeting into the knowledge base."""
    meeting.paused = meeting.ended = True
    broadcast("status", "wrapping up…")
    end_voice_meeting()
    # Manual mode skips final notes unless they were in use; compiling the knowledge base writes a meeting note anyway.
    if meeting.word_count() > meeting.words_at_last_analysis and (meeting.auto or meeting.analysis):
        await summarize()
    save_to_vault()

    async def compile_kb():
        broadcast("kb", {"state": "compiling", **kb_stats()})
        results = await kb.compile_pending(on_progress=lambda r: broadcast("status", r))
        broadcast_usage()
        broadcast("kb", {"state": "done", "results": results, **kb_stats()})
        broadcast("status", "meeting saved to knowledge base")

    asyncio.create_task(compile_kb())
    return {"ok": True}


def main() -> None:
    p = argparse.ArgumentParser(description="Live meeting copilot")
    p.add_argument("--device", help="input device name (substring), default = system mic")
    p.add_argument("--watch", type=Path, help="tail this transcript file instead of using audio")
    p.add_argument("--no-audio", action="store_true", help="only accept pasted / POSTed text")
    p.add_argument("--list-devices", action="store_true")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()

    if args.list_devices:
        from audio import list_devices

        list_devices()
        return
    if not (HAS_ANTHROPIC or HAS_OPENAI):
        raise SystemExit("Set ANTHROPIC_API_KEY and/or OPENAI_API_KEY (in .env or your shell).")

    starters = []
    if args.watch:
        app.state.mode = f"watching {args.watch.name}"
        starters.append(lambda: threading.Thread(target=watch_file, args=(args.watch,), daemon=True).start())
    elif args.no_audio:
        app.state.mode = "paste only"
    else:
        if TRANSCRIBE_BACKEND == "openai" and not HAS_OPENAI:
            raise SystemExit("Set OPENAI_API_KEY for TRANSCRIBE_BACKEND=openai (or use the default local backend).")
        from audio import AudioTranscriber

        # Mic levels and stage counters every 5 s (a few KB per meeting), so a stalled transcript can be diagnosed.
        session_dir.mkdir(parents=True, exist_ok=True)
        app.state.debug_in_session = "AUDIO_DEBUG" not in os.environ  # then each new meeting gets its own log
        os.environ.setdefault("AUDIO_DEBUG", str(session_dir / "audio_debug.log"))
        app.state.mode = "microphone"
        if VOICE_ID:
            from voice import VoiceID

            app.state.voice = VoiceID()
        app.state.transcriber = AudioTranscriber(args.device, add_segment, set_partial, set_status,
                                                 lambda: meeting.paused, prompt_hint,
                                                 voice=getattr(app.state, "voice", None), on_voice=set_voice)
        starters.append(app.state.transcriber.start)
    app.state.starters = starters

    print(f"Speech: {TRANSCRIBE_BACKEND} · triage: {TRIAGE_MODEL} · answers: {MAIN_MODEL} · questions: {QUESTIONS_MODEL}"
          f" · chat: {CHAT_MODEL}")
    print(f"Meeting Copilot → http://localhost:{args.port}   (session files: {session_dir})")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
