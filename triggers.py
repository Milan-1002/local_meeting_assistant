"""Decide when a line of transcript deserves an answer, or is a good moment to suggest questions.

Stage A (free): regex rules over each new line.
Stage B (cheap model): only for lines stage A flags - is this really aimed at the user?
"""

from __future__ import annotations

import difflib
import re

import llm
from config import MY_ALIASES, MY_NAME, TRIAGE_MODEL
from speakers import NOTE as TRANSCRIPT_NOTE

FIRST_NAME = MY_NAME.split()[0]
NAMES = {n.lower() for n in [FIRST_NAME, MY_NAME, *MY_ALIASES]}

OPINION = re.compile(
    r"what do you think|what('?s| is) your (take|view|opinion|thought)|your (take|thoughts|opinion|view)|"
    r"thoughts on|any thoughts|how do you feel|what about you|how about you|do you agree|would you say|"
    r"weigh in|anything to add|what would you|how would you|can you (explain|walk|tell|share|talk)|"
    r"could you (explain|walk|tell|share|talk)|tell (me|us) about|what('?s| is) your|have you (ever|worked|used|tried)|"
    r"do you (have|know|think|want)|are you (able|okay|working|planning)|did you",
    re.I,
)
INTERROGATIVE = re.compile(
    r"(^|[.!?]\s+)(what|why|how|when|where|who|which|is|are|do|does|did|can|could|would|will|should|"
    r"have|has|any|anyone|shall)\b",
    re.I,
)
YOU = re.compile(r"\b(you|your|yours|you'?re|you'?ve|you'?ll)\b", re.I)
# Addressed to the whole room: not the user's to answer unless they're named.
GROUP = re.compile(r"\b(you all|y'?all|you guys|you folks|everyone|everybody|anyone|anybody|folks|all of you|"
                   r"the team|team,)\b", re.I)
# Natural openings to ask something: invitations, check-ins, wrap-ups, topic changes.
ASK_MOMENT = re.compile(
    r"any (other )?questions|questions (so far|on that|about that|before)|does (that|this) make sense|"
    r"make sense\?|is that clear|anything else|anyone have anything|before (we|I) move on|"
    r"(let'?s|we'?ll|we can) move on|moving on|next (topic|item|thing|up)|that'?s (it|all) (for|from)|"
    r"to (recap|summarize)|so in summary|any thoughts|open (it )?up for questions|happy to take questions",
    re.I,
)


def mentions_me(text: str) -> bool:
    low = text.lower()
    if any(re.search(rf"\b{re.escape(n)}\b", low) for n in NAMES):
        return True
    # Fuzzy: "Milam", "Milen" etc. for a short first name.
    words = re.findall(r"[a-z]+", low)
    return any(difflib.SequenceMatcher(None, w, FIRST_NAME.lower()).ratio() >= 0.8 for w in words if len(w) >= 4)


# Fillers that contain "you" without addressing anyone.
FILLER = re.compile(r"\b(you know|thank you|thanks|see you|if you will|mind you|you see|i mean|bless you)\b", re.I)


def ask_moment(text: str) -> bool:
    """A natural point to ask something ("any questions?", "moving on", "does that make sense?")."""
    return bool(ASK_MOMENT.search(text))


def rule_check(text: str, sensitivity: str = "normal") -> str | None:
    """Return why this line might be for the user, or None. Costs nothing."""
    named = mentions_me(text)
    text = FILLER.sub(" ", text)
    question = "?" in text or bool(INTERROGATIVE.search(text))
    opinion = bool(OPINION.search(text))
    if named and (question or opinion):
        return "asked by name"
    if sensitivity == "low":
        return None
    if GROUP.search(text) and sensitivity != "high":
        return None
    if opinion:
        return "asks for your view"
    if question and YOU.search(text):
        return "question to you"
    if named:
        return "you were mentioned"
    if sensitivity == "high" and question:
        return "question"
    return None


TRIAGE_SYSTEM = f"""You watch a live meeting transcript for {MY_NAME} (first name {FIRST_NAME}). \
The transcript comes from room audio: transcription errors and fragments are normal. \
{FIRST_NAME}'s own words appear in it too. {TRANSCRIPT_NOTE}

You get the last minute or so of transcript and one flagged line. Decide whether, at the flagged \
line, someone is asking {FIRST_NAME} something that {FIRST_NAME} personally should answer now.

respond=true only when:
- {FIRST_NAME} is addressed by name (or clearly the one being spoken to, e.g. right after \
{FIRST_NAME} spoke, or a round-robin of updates has reached {FIRST_NAME}), and
- it is a real question or request for {FIRST_NAME}'s input, status, opinion or decision.

respond=false when:
- it is put to the whole room ("what do you all want to do?", "does anyone know...?") and nothing \
singles out {FIRST_NAME};
- it is rhetorical, a statement, or part of someone explaining or demoing something;
- it is aimed at someone else, or {FIRST_NAME} is the one asking;
- it got answered right after, or it is the same question already answered for {FIRST_NAME}.
A friendly "how are you" aimed at {FIRST_NAME} counts (respond=true); it just needs a short reply.

Examples:
- "Milan, have you been able to set up your GitLab SSH key yet?" -> true
- "And Milan, are you going to take the 120-day schedule story or should Jake?" -> true
- "So Milan, what do you think about doing it incrementally?" -> true
- "Alright, so what do you all want to do today with this time?" -> false (whole room)
- "Can you share the dashboard link with the team, Christina?" -> false (someone else)
- "I'm assuming it gives you a message as to why it doesn't work?" -> false (rhetorical, mid-explanation)
- "So I'm going to start by name. These are all under profile 108." -> false (statement)

When respond=true: asker = the person asking if the transcript makes it clear, else "". question = \
the question as that person would say it to {FIRST_NAME}, as one direct sentence ending with "?" \
(fix transcription errors, add only the context needed to make it standalone). No narration such \
as "At the meeting, the speaker asked...". When respond=false, asker and question are ""."""

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "respond": {"type": "boolean"},
        "asker": {"type": "string"},
        "question": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["respond", "asker", "question", "reason"],
    "additionalProperties": False,
}


async def triage(recent: str, line: str, context: str, about_me: str = "") -> dict:
    system = TRIAGE_SYSTEM + (f"\n\nAbout {FIRST_NAME}:\n{about_me}" if about_me else "")
    user = f"""<user_context>{context or "(not provided)"}</user_context>

<recent_transcript>
{recent}
</recent_transcript>

<flagged_line>{line}</flagged_line>"""
    return await llm.complete(TRIAGE_MODEL, system, user, schema=TRIAGE_SCHEMA, max_tokens=1500)
