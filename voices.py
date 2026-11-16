"""Remembered Voices: every distinct voice heard in a meeting, recognised again in later meetings.

Works on voice prints (unit-length embedding vectors), never on audio, so it can be tested with made-up
vectors; voice.py turns audio into prints. Each Voice has a number that is never reused, which is what a
Transcript records ("Speaker 3:"), and at most one Voice is Me. Saved to one JSON file with the rest of the
personal app data. Thread-safe: the transcriber thread identifies lines while the web loop enrolls or forgets.

A Voice created from a single line has a rough print, so for its first YOUNG_SAMPLES matches its print is
the running average of what it has heard. After that it only learns once per meeting (`end_meeting`): its
print takes a small, bounded step towards that meeting's confident matches (score >= match + CONFIDENT),
so recognition improves the more someone is met, and one bad meeting can't pull a print far.
An unnamed Voice not heard for EXPIRE_DAYS is dropped; its number is never handed out again.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from config import VOICE_MATCH, VOICE_NEW, VOICE_NEW_MIN_S

MIN_S = 1.0  # shorter slices ("yeah", "sure") are too little to judge
YOUNG_SAMPLES = 10
SAVE_EVERY_S = 30.0  # "last heard" times are written at most this often
CONFIDENT = 0.10  # a match this far above the match threshold may teach the print
LEARN_RATE = 0.10  # how far a print moves towards one meeting's confident matches
LEARN_MIN = 3  # confident matches a meeting needs before it teaches anything
LEARN_CAP = 40  # confident prints kept per Voice per meeting (memory stays flat)
EXPIRE_DAYS = 30


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


@dataclass
class Voice:
    number: int
    print: np.ndarray | None  # None once forgotten: the number stays retired
    heard: float = 0.0  # last time a line matched it
    samples: int = 1  # how many prints its print averages


class Registry:
    def __init__(self, path: Path, match: float = VOICE_MATCH, new: float = VOICE_NEW,
                 new_min_s: float = VOICE_NEW_MIN_S, clock=time.time):
        self.path, self.match, self.new, self.new_min_s, self.clock = Path(path), match, new, new_min_s, clock
        self.voices: dict[int, Voice] = {}
        self.next_number = 1
        self.me: int | None = None
        self.lock = threading.RLock()
        self.saved_at = 0.0
        self.confident: dict[int, list[np.ndarray]] = {}  # this meeting's confident matches, for end_meeting
        self.load()

    # ------------------------------------------------------------------ matching

    def identify(self, emb, duration: float) -> tuple[int | None, float | None]:
        """(Voice number or None, best similarity or None) for one line's print.

        None when the line is too short, or in the grey zone: too unlike every Voice to be one of them,
        but not unlike enough (or too short) to be someone new."""
        if duration < MIN_S:
            return None, None
        emb = unit(emb)
        with self.lock:
            best, score = self.best(emb)
            if best is not None and score >= self.match:
                self.heard(best, emb)
                if score >= self.match + CONFIDENT and best.samples >= YOUNG_SAMPLES:
                    buf = self.confident.setdefault(best.number, [])
                    if len(buf) < LEARN_CAP:
                        buf.append(emb)
                return best.number, score
            if (best is None or score <= self.new) and duration >= self.new_min_s:
                v = self.create(emb)
                return v.number, score
            return None, score

    def best(self, emb: np.ndarray) -> tuple[Voice | None, float | None]:
        scored = [(float(v.print @ emb), v) for v in self.voices.values() if v.print is not None]
        if not scored:
            return None, None
        score, v = max(scored, key=lambda x: x[0])
        return v, score

    def heard(self, v: Voice, emb: np.ndarray) -> None:
        v.heard = self.clock()
        if v.samples < YOUNG_SAMPLES and v.number != self.me:
            v.print = unit(v.print * v.samples + emb)
            v.samples += 1
        self.save(soon=True)

    def create(self, emb: np.ndarray) -> Voice:
        v = Voice(self.next_number, unit(emb), self.clock())
        self.voices[v.number] = v
        self.next_number += 1
        self.save()
        return v

    # ------------------------------------------------------------------ across meetings

    def end_meeting(self, keep: set[int] = frozenset()) -> None:
        """Once per meeting: teach each Voice from its confident matches, then drop stale unnamed Voices.
        `keep` is the named Voice numbers, which never expire."""
        with self.lock:
            for number, embs in self.confident.items():
                v = self.voices.get(number)
                if v is None or v.print is None or len(embs) < LEARN_MIN:
                    continue
                v.print = unit(v.print + LEARN_RATE * (unit(np.mean(embs, axis=0)) - v.print))
            self.confident = {}
            self.prune(keep)
            self.save()

    def prune(self, keep: set[int] = frozenset()) -> list[int]:
        """Drop unnamed Voices not heard for EXPIRE_DAYS. Returns the dropped numbers (never reused)."""
        cutoff = self.clock() - EXPIRE_DAYS * 86400
        with self.lock:
            gone = [n for n, v in self.voices.items() if n not in keep and n != self.me and v.heard < cutoff]
            for n in gone:
                del self.voices[n]
                self.confident.pop(n, None)
            return gone

    def forget(self, number: int) -> bool:
        """Delete a Voice's print (someone asked, or it's wrong). Its number and name stay, so past
        Transcripts still read right; that voice will come back as a new Speaker N."""
        with self.lock:
            v = self.voices.get(number)
            if v is None or v.print is None:
                return False
            v.print = None
            self.confident.pop(number, None)
            if self.me == number:
                self.me = None
            self.save()
            return True

    # ------------------------------------------------------------------ Me

    def set_me(self, emb) -> int:
        """Make (or refresh) the Voice that is Me from an enrolment print. Returns its number."""
        with self.lock:
            if self.me is not None and self.me in self.voices:
                v = self.voices[self.me]
                v.print, v.samples, v.heard = unit(emb), YOUNG_SAMPLES, self.clock()
            else:
                v = self.create(unit(emb))
                v.samples = YOUNG_SAMPLES
                self.me = v.number
            self.save()
            return v.number

    @property
    def has_me(self) -> bool:
        return self.me is not None and self.voices.get(self.me) is not None and self.voices[self.me].print is not None

    def forget_me(self) -> None:
        """Delete Me's print. The number stays retired; Me's voice will come back as a new Voice."""
        with self.lock:
            if self.me is not None:
                self.forget(self.me)
            self.me = None
            self.save()

    # ------------------------------------------------------------------ storage

    def load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text())
        self.next_number = data.get("next", 1)
        self.me = data.get("me")
        for key, d in data.get("voices", {}).items():
            p = d.get("print")
            self.voices[int(key)] = Voice(int(key), unit(p) if p else None, d.get("heard", 0.0), d.get("samples", 1))

    def save(self, soon: bool = False) -> None:
        """Write the file; with soon=True, only if the last write was over SAVE_EVERY_S ago."""
        with self.lock:
            now = time.time()
            if soon and now - self.saved_at < SAVE_EVERY_S:
                return
            self.saved_at = now
            data = {"next": self.next_number, "me": self.me,
                    "voices": {str(n): {"print": None if v.print is None else [round(float(x), 6) for x in v.print],
                                        "heard": v.heard, "samples": v.samples}
                               for n, v in sorted(self.voices.items())}}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(self.path)

    def migrate(self, old_print_file: Path) -> None:
        """The single voice print from before remembered Voices becomes Me's Voice (once)."""
        if self.voices or not old_print_file.exists():
            return
        self.set_me(np.load(old_print_file))
        old_print_file.rename(old_print_file.with_name(old_print_file.name + ".migrated"))
