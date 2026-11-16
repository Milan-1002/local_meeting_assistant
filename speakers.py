"""Who said a line: the one place that writes, reads, names and explains speaker labels.

A segment's speaker is one of:
  "me"   Milan's Voice                          written "Milan: …"
  "s3"   remembered Voice number 3 (voices.py)  written "Speaker 3: …"
  "them" someone else (Transcripts from before remembered Voices)  written "Other: …"
  ""     not recognised                         no prefix
Transcripts store these labels and are never edited (docs/adr/0001). A named Voice's Person comes from
wiki/voices.md (kb installs the lookup with `use_people`), and is swapped in only when text is read:
`display`, `render_line` and `render` give the name, `name` and `format_line` give the stored label.
Everything else (transcripts on disk, the UI payload, the kb readers, the prompts' note) goes through here.
"""

from __future__ import annotations

import re
from typing import Callable

from config import FIRST_NAME

ME, OTHER = "me", "them"
NAMES = {ME: FIRST_NAME, OTHER: "Other"}  # fixed labels and how they are written in a Transcript
VOICE_LABEL = re.compile(r"s([1-9]\d*)")
VOICE_PREFIX = re.compile(r"Speaker ([1-9]\d*): ")
STORED_VOICE = re.compile(r"^(\[\d+:\d\d\] )Speaker ([1-9]\d*): ", re.M)

# Voice number -> (Person's name, is Me) for named Voices. Set by kb (from wiki/voices.md).
_people: Callable[[], dict[int, tuple[str, bool]]] = dict


def use_people(lookup: Callable[[], dict[int, tuple[str, bool]]]) -> None:
    global _people
    _people = lookup


NOTE = (f"Lines starting \"{FIRST_NAME}:\" were matched to {FIRST_NAME}'s voice, and lines starting with another "
        "person's name to that person's voice. \"Speaker 3:\" and the like are people not named yet, told apart by "
        "voice: the same number is the same person, across meetings too. \"Other:\" (older meetings) is someone "
        f"other than {FIRST_NAME}. Unlabeled lines are unknown. Voice labels are usually right but can be wrong.")


def named_numbers() -> set[int]:
    """Numbers of the Voices that are linked to a Person."""
    return set(_people())


def voice(number: int) -> str:
    """The label for remembered Voice `number`."""
    return f"s{number}"


def voice_number(speaker: str | None) -> int | None:
    m = VOICE_LABEL.fullmatch(speaker or "")
    return int(m.group(1)) if m else None


def clean(speaker: str | None) -> str:
    """A known label, or "" for anything else."""
    return speaker if speaker in NAMES or voice_number(speaker) else ""


def person(speaker: str | None) -> tuple[str, bool] | None:
    """(name, is Me) when the label is a named Voice, else None."""
    n = voice_number(speaker)
    return _people().get(n) if n else None


def is_me(speaker: str | None) -> bool:
    """Milan's line: the "me" label, or a Voice named as Milan."""
    return speaker == ME or bool((p := person(speaker)) and p[1])


def display(speaker: str | None) -> str:
    """How a line's speaker is shown and sent to models: the Person's name for a named Voice, else `name`."""
    p = person(speaker)
    return p[0] if p else name(speaker)


def name(speaker: str | None) -> str:
    """How the label is written in a Transcript or prompt; "" when unknown."""
    n = voice_number(speaker)
    return f"Speaker {n}" if n else NAMES.get(speaker or "", "")


def format_line(clock: str, text: str, speaker: str = "") -> str:
    """One Transcript line: "[03:12] Milan: text", or "[03:12] text" when the speaker is unknown."""
    who = name(speaker)
    return f"[{clock}] {who}: {text}" if who else f"[{clock}] {text}"


def render_line(clock: str, text: str, speaker: str = "") -> str:
    """Like format_line, with named Voices shown by name: for prompts and anything else that is read."""
    who = display(speaker)
    return f"[{clock}] {who}: {text}" if who else f"[{clock}] {text}"


def render(transcript: str) -> str:
    """A stored Transcript's text with "Speaker 3:" replaced by the Person's name wherever it is known."""
    people = _people()
    if not people:
        return transcript

    def sub(m: re.Match) -> str:
        p = people.get(int(m.group(2)))
        return f"{m.group(1)}{p[0]}: " if p else m.group(0)

    return STORED_VOICE.sub(sub, transcript)


def parse(text: str) -> tuple[str, str]:
    """The text after a line's clock -> (speaker, text). "Speaker 3: hi" -> ("s3", "hi"); no prefix -> ("", text)."""
    if m := VOICE_PREFIX.match(text):
        return voice(int(m.group(1))), text[m.end():]
    for key, who in NAMES.items():
        if text.startswith(f"{who}: "):
            return key, text[len(who) + 2:]
    return "", text
