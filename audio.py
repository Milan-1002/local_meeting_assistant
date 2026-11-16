"""Mic -> speech chunks (simple energy VAD) -> text, locally with mlx-whisper or via OpenAI."""

from __future__ import annotations

import io
import os
import queue
import re
import threading
import time
import wave
from collections import deque
from typing import Callable

import numpy as np

from config import (OPENAI_TRANSCRIBE_MODEL, PARTIAL_EVERY_S, PARTIAL_MODEL, TRANSCRIBE_BACKEND, WHISPER_LANGUAGE,
                    WHISPER_MODEL)

SAMPLE_RATE = 16_000
BLOCK_S = 0.1
MIN_CHUNK_S = 4.0  # don't cut before this much audio
MAX_CHUNK_S = 18.0  # always cut by this point
SILENCE_CUT_S = 0.7  # pause length that ends a chunk
NOISE_WINDOW_S = 10.0  # background level = quietest 10% of the last 10 s
MIN_SPEECH_RMS = 0.003  # never treat quieter than this as speech
MIC_TIMEOUT_S = 5.0  # no blocks (or only exact zeros) this long = the input device went away; reopen it
STUCK_S = 60.0  # one transcription taking this long (normal: 1-20 s) is hung: abandon it and start a fresh thread
MAX_RESTARTS = 3  # per process; past this, ask the user to restart the app
ENROLL_S = 20  # seconds of the user's speech for the voice print
ENROLL_TIMEOUT_S = 120

# Scripts whisper sometimes slips into on unclear audio (CJK, kana, Hangul, Cyrillic, Greek, Arabic,
# Indic, Thai...) plus the "�" replacement character. Latin letters with accents are kept for names.
FOREIGN_SCRIPT = re.compile(r"[\u0370-\u1fff\u2e80-\ua4cf\uac00-\ud7af\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef\ufffd]+")
REPEATED_PHRASE = re.compile(r"(\S.{6,80}?)(?:[\s,.]*\1){1,}", re.S)  # "is so risky. is so risky."
REPEATED_SHORT = re.compile(r"([^\s\d]{1,8}?)\1{3,}")  # "會員會員會員…", "lalalala"


def clean_text(text: str) -> str:
    """Undo whisper's usual failure modes: other-script tokens in English speech, and loops."""
    if WHISPER_LANGUAGE == "en":
        text = FOREIGN_SCRIPT.sub(" ", text)
    text = REPEATED_SHORT.sub(r"\1", text)
    text = REPEATED_PHRASE.sub(r"\1", text)
    # Tidy what removal leaves behind ("allowed..", "protected?,", ",,") but keep real "..." ellipses.
    text = re.sub(r"\s+([,.?!])", r"\1", text)
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)
    text = re.sub(r"([.?!]),|,(?:\s*,)+", lambda m: m.group(1) or ",", text)
    return re.sub(r"\s{2,}", " ", text).strip(" ,")


# Whisper invents these on noise or silence.
HALLUCINATIONS = {
    "thank you.", "thank you", "thanks for watching!", "thanks for watching.", "you", "bye.", "bye",
    "thank you for watching.", "thank you very much.", ".", "okay.", "so", "subtitles by the amara.org community",
}


def list_devices() -> None:
    import sounddevice as sd

    print(sd.query_devices())


class AudioTranscriber:
    def __init__(
        self,
        device: str | None,
        on_text: Callable[[str], None],
        on_partial: Callable[[str], None],
        on_status: Callable[[str], None],
        is_paused: Callable[[], bool],
        prompt_hint: Callable[[], str],
        voice=None,  # voice.VoiceID: labels each line with a remembered Voice; None turns that off
        on_voice: Callable[[dict], None] = lambda state: None,
    ):
        import sounddevice as sd

        self.sd = sd
        self.on_text, self.on_partial = on_text, on_partial
        self.on_status, self.is_paused, self.prompt_hint = on_status, is_paused, prompt_hint
        self.voice, self.on_voice = voice, on_voice
        self.enrolling = 0.0  # when voice recording started, 0 when not recording
        self.enroll_buf: list[np.ndarray] = []
        self.device_name = device
        self.device = self._resolve_device(device)
        self.blocks: queue.Queue = queue.Queue()
        self.chunks: queue.Queue = queue.Queue()
        self.noise_floor = 0.002
        self.levels: deque = deque(maxlen=int(NOISE_WINDOW_S / BLOCK_S))
        self.transcribe = self._transcribe_local if TRANSCRIBE_BACKEND == "local" else self._transcribe_openai
        # Live draft: the chunker drops a snapshot of the unfinished chunk here; `gen` counts finished
        # chunks so a draft of an already-finished chunk is thrown away.
        self.live_drafts = TRANSCRIBE_BACKEND == "local" and PARTIAL_MODEL != "off"
        self.draft: tuple[int, np.ndarray] | None = None
        self.gen = 0
        self.stats = {"rms": [], "speech_blocks": 0, "chunks": 0, "finals": 0, "drafts": 0, "last_final": 0.0,
                      "reconnects": 0, "restarts": 0, "voice": []}
        self.last_signal = time.time()  # last block with any non-zero sample; a real mic is never exactly 0
        self.busy_since = 0.0  # when the running transcription started, 0 when idle
        # A native call (MLX / torch) can hang forever and a Python thread can't be killed, so a hung
        # transcriber is abandoned: `worker` is bumped, a new thread starts with its own models, and the
        # old one exits without emitting anything if it ever wakes up.
        self.worker = 0
        self.restarts = 0
        self._tls = threading.local()  # per-thread whisper models (MLX models aren't shared across threads)

    def _resolve_device(self, name: str | None):
        if not name:
            return None
        for i, d in enumerate(self.sd.query_devices()):
            if d["max_input_channels"] > 0 and name.lower() in d["name"].lower():
                print(f"Using input device #{i}: {d['name']}")
                return i
        raise SystemExit(f"No input device matching {name!r}. Try --list-devices.")

    def start(self) -> None:
        threading.Thread(target=self._keep_alive, args=(self._chunker,), daemon=True).start()
        self._start_transcriber()
        if os.getenv("AUDIO_DEBUG"):
            self.debug_path = os.getenv("AUDIO_DEBUG")  # the app points this at each new meeting's folder
            threading.Thread(target=self._debug_log, daemon=True).start()
        self._open_stream()
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _start_transcriber(self) -> None:
        threading.Thread(target=self._keep_alive, args=(self._transcriber, self.worker), daemon=True).start()

    def restart_transcriber(self, why: str) -> None:
        """Abandon the running transcriber thread (possibly hung inside whisper or torch) and start a new one."""
        if self.restarts >= MAX_RESTARTS:
            self.on_status(f"speech-to-text is stuck ({why}) – restart copilot.py")
            return
        self.restarts += 1
        self.stats["restarts"] += 1
        self.worker += 1
        self.busy_since = 0.0
        if self.voice is not None:
            self.voice.model = None  # the new thread loads its own copy rather than sharing the stuck one
        self.on_status(f"speech-to-text {why} – restarting it…")
        self._start_transcriber()

    def new_meeting(self) -> None:
        """Called when a new meeting starts: drop audio queued for the old one, and unstick the transcriber."""
        self.gen += 1
        self.draft = None
        while not self.chunks.empty():
            try:
                self.chunks.get_nowait()
            except queue.Empty:
                break
        self.on_partial("")
        if self.busy_since and time.time() - self.busy_since > 20:
            self.restart_transcriber(f"was stuck for {time.time() - self.busy_since:.0f} s")

    def _keep_alive(self, loop: Callable[..., None], *args) -> None:
        """A crash in a worker thread would silently stop the transcript; report it and start over."""
        while True:
            try:
                return loop(*args)
            except Exception as e:
                self.on_status(f"audio error: {e} – restarting")
                time.sleep(1)

    def _open_stream(self) -> None:
        self.stream = self.sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=int(SAMPLE_RATE * BLOCK_S),
            device=self.device,
            callback=self._on_block,
        )
        self.stream.start()
        self.last_signal = time.time()

    def _on_block(self, data, *_) -> None:
        if data.any():
            self.last_signal = time.time()
        self.blocks.put(data[:, 0].copy())

    def _reopen_stream(self) -> None:
        """macOS stops feeding a stream, without any error, when its device disappears or the default
        input changes (headset plugged in, Bluetooth, another app taking the mic). Start a fresh one."""
        old = self.stream
        closer = threading.Thread(target=lambda: (old.abort(), old.close()), daemon=True)
        closer.start()
        closer.join(2)  # closing a dead device can hang; don't wait on it
        self.sd._terminate()  # PortAudio caches the device list; reload it to find the new default
        self.sd._initialize()
        try:
            self.device = self._resolve_device(self.device_name)
        except SystemExit:  # the named device is gone: use whatever the system default is now
            self.device = None
        self._open_stream()

    def _watchdog(self) -> None:
        while True:
            time.sleep(1)
            if time.time() - self.last_signal > MIC_TIMEOUT_S:
                self.on_status("no audio from the mic – reconnecting…")
                self.stats["reconnects"] += 1
                try:
                    self._reopen_stream()
                    self.on_status("paused" if self.is_paused() else "listening")
                except Exception as e:
                    self.on_status(f"can't open the mic ({e}) – retrying…")
                    time.sleep(2)
            busy = time.time() - self.busy_since if self.busy_since else 0.0
            if busy > STUCK_S:
                self.restart_transcriber(f"hung for {busy:.0f} s")

    def _chunker(self) -> None:
        buf, voiced, silence, last_draft = [], 0, 0.0, 0.0
        while True:
            block = self.blocks.get()
            if self.is_paused() and not self.enrolling:
                if buf:
                    self.gen += 1
                    self.draft = None
                    self.on_partial("")
                buf, voiced, silence = [], 0, 0.0
                continue
            rms = float(np.sqrt(np.mean(block**2)))
            # Background level from the quiet moments (gaps between words, pauses). A low percentile
            # can't creep up into speech the way averaging "non-speech" blocks does.
            self.levels.append(rms)
            if len(self.levels) >= 20:
                self.noise_floor = float(np.percentile(self.levels, 10))
            is_speech = rms > max(self.noise_floor * 2.5, MIN_SPEECH_RMS)
            self.stats["rms"].append(rms)
            self.stats["speech_blocks"] += is_speech
            buf.append(block)
            voiced += is_speech
            silence = 0.0 if is_speech else silence + BLOCK_S
            dur = len(buf) * BLOCK_S

            if not voiced and dur > 2:  # pure silence: keep a short tail only
                buf = buf[-5:]
                continue
            if (dur >= MIN_CHUNK_S and silence >= SILENCE_CUT_S) or dur >= MAX_CHUNK_S:
                self.gen += 1
                self.draft = None
                if voiced * BLOCK_S >= 0.8:  # at least ~0.8s of actual speech
                    self.chunks.put(np.concatenate(buf))
                    self.stats["chunks"] += 1
                else:
                    self.on_partial("")
                buf, voiced, silence = [], 0, 0.0
            elif self.live_drafts and is_speech and voiced >= 10 and dur - last_draft >= PARTIAL_EVERY_S:
                self.draft = (self.gen, np.concatenate(buf))
                last_draft = dur
            if not buf:
                last_draft = 0.0

    def _transcriber(self, me: int = 0) -> None:
        current = lambda: me == self.worker  # False once this thread has been abandoned as hung
        if TRANSCRIBE_BACKEND == "local":
            self.on_status("loading speech model…")
            silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
            try:
                self._transcribe_local(silence, "")  # download + warm up
                if self.live_drafts:
                    self._transcribe_local(silence, "", PARTIAL_MODEL)
            except Exception as e:
                self.on_status(f"speech model error: {e}")
                return
        if self.voice:
            self.on_status("loading voice model…")
            try:
                self.voice.load()
            except Exception as e:  # transcribe without labels rather than not at all
                self.on_status(f"voice model error: {e}")
                self.voice = None
        if not current():
            return
        self.on_status("paused" if self.is_paused() else "listening")
        while current():
            # Finished chunks first; when none are waiting, refresh the live draft.
            try:
                audio = self.chunks.get(timeout=0.05)
            except queue.Empty:
                if self.enrolling:
                    self._check_enroll_timeout()
                else:
                    self._update_draft()
                continue
            if self.enrolling:
                self._enroll_chunk(audio)
                continue
            self.busy_since = time.time()
            try:
                lines = self._transcribe_labeled(audio)
            except Exception as e:  # keep listening even if one chunk fails
                if current():
                    self.on_status(f"transcription error: {e}")
                continue
            finally:
                if current():
                    self.busy_since = 0.0
            if not current():
                return
            self.stats["finals"] += 1
            self.stats["last_final"] = time.time()
            lines = [(t, who) for t, who in lines if t and t.lower() not in HALLUCINATIONS]
            for text, who in lines:
                self.on_text(text, who)  # the UI swaps the draft line for this
            if not lines:
                self.on_partial("")

    def _transcribe_labeled(self, audio: np.ndarray) -> list[tuple[str, str]]:
        """Text for one chunk as (text, speaker) lines. With voice ID, each whisper segment is labeled on
        its own slice of audio, so a quick back-and-forth inside one chunk comes out as separate lines."""
        if not self.voice:
            return [(self.transcribe(audio, self.prompt_hint()).strip(), "")]
        if TRANSCRIBE_BACKEND != "local":
            who, score = self.voice.label(audio)
            self.stats["voice"].append(score)
            return [(self.transcribe(audio, self.prompt_hint()).strip(), who)]
        parts = []
        for start, end, text in self._local_segments(audio, self.prompt_hint()):
            who, score = self.voice.label(audio[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)])
            self.stats["voice"].append(score)
            parts.append([text, who])
        # Too-short pieces ("yeah.") take the label of the piece before or after them.
        for i, p in enumerate(parts):
            if not p[1]:
                near = [q[1] for q in parts[max(0, i - 1):i + 2] if q[1]]
                p[1] = near[0] if len(set(near)) == 1 else ""
        lines: list[list[str]] = []
        for text, who in parts:
            if lines and lines[-1][1] == who:
                lines[-1][0] += text
            else:
                lines.append([text, who])
        return [(clean_text(t), who) for t, who in lines]

    def start_enroll(self) -> None:
        """Record the user's voice: the next ~20 s of speech go to the voice print, not the transcript."""
        self.enroll_buf, self.enrolling = [], time.time()
        self.on_voice({"state": "recording", "seconds": 0, "target": ENROLL_S})

    def cancel_enroll(self) -> None:
        self.enrolling, self.enroll_buf = 0.0, []

    def _enroll_chunk(self, audio: np.ndarray) -> None:
        self.enroll_buf.append(audio)
        heard = sum(len(a) for a in self.enroll_buf) / SAMPLE_RATE
        if heard < ENROLL_S:
            self.on_voice({"state": "recording", "seconds": round(heard), "target": ENROLL_S})
            return
        self.on_voice({"state": "saving"})
        try:
            self.voice.enroll(self.enroll_buf)
            self.on_voice({"state": "ready"})
        except Exception as e:
            self.on_voice({"state": "failed", "error": str(e)})
        self.enrolling, self.enroll_buf = 0.0, []

    def _check_enroll_timeout(self) -> None:
        if time.time() - self.enrolling > ENROLL_TIMEOUT_S:
            self.enrolling, self.enroll_buf = 0.0, []
            self.on_voice({"state": "failed", "error": "Didn't hear enough speech. Try again a bit closer to the mic."})

    def _update_draft(self) -> None:
        draft, self.draft = self.draft, None
        if draft is None:
            return
        gen, audio = draft
        # No prompt (the small model tends to echo it back) and a token cap sized to the audio, so a
        # hallucination loop can't stall the draft.
        max_tokens = int(len(audio) / SAMPLE_RATE * 6) + 8
        me = self.worker
        self.busy_since = time.time()
        try:
            text = self._transcribe_local(audio, "", PARTIAL_MODEL, sample_len=max_tokens).strip()
        except Exception:
            return
        finally:
            if me == self.worker:
                self.busy_since = 0.0
        if me != self.worker:
            return
        text = re.sub(r"(\s*//)+", "", text)  # stray "//" the small model sometimes emits
        self.stats["drafts"] += 1
        if gen == self.gen and self.chunks.empty() and text.lower() not in HALLUCINATIONS:
            self.on_partial(text)

    def _debug_log(self) -> None:
        """AUDIO_DEBUG=<file>: every 5 s, log input levels and whether each stage keeps up."""
        t0 = time.time()
        while True:
            time.sleep(5)
            os.makedirs(os.path.dirname(self.debug_path) or ".", exist_ok=True)
            with open(self.debug_path, "a") as f:
                st, rms, voice = self.stats, self.stats["rms"], [v for v in self.stats["voice"] if v is not None]
                self.stats["rms"], self.stats["voice"] = [], []
                lvl = f"rms p50={np.median(rms):.4f} p90={np.percentile(rms, 90):.4f}" if rms else "rms none (no mic data!)"
                f.write(f"{time.time() - t0:6.0f}s {lvl} floor={self.noise_floor:.4f} "
                        f"threshold={max(self.noise_floor * 2.5, MIN_SPEECH_RMS):.4f} speech_blocks={st['speech_blocks']} "
                        f"chunks={st['chunks']} finals={st['finals']} drafts={st['drafts']} "
                        f"queue_blocks={self.blocks.qsize()} queue_chunks={self.chunks.qsize()} paused={self.is_paused()} "
                        f"silent_for={time.time() - self.last_signal:.0f}s reconnects={st['reconnects']} restarts={st['restarts']} "
                        f"busy_for={time.time() - self.busy_since if self.busy_since else 0:.0f}s"
                        f"{' voice=' + ','.join(f'{v:.2f}' for v in voice) if voice else ''}"
                        f"{' enrolling' if self.enrolling else ''}\n")
                f.flush()

    def _transcribe_local(self, audio: np.ndarray, prompt: str, model: str = WHISPER_MODEL, **options) -> str:
        return clean_text("".join(t for _, _, t in self._local_segments(audio, prompt, model, **options)))

    def _local_segments(self, audio: np.ndarray, prompt: str, model: str = WHISPER_MODEL,
                        **options) -> list[tuple[float, float, str]]:
        """(start s, end s, text) per whisper segment, minus the ones whisper flags as junk."""
        import mlx.core as mx
        import mlx_whisper
        from mlx_whisper.load_models import load_model
        from mlx_whisper.transcribe import ModelHolder

        # mlx-whisper caches only one model and reloads (plus re-checks Hugging Face) whenever the model
        # changes. We alternate between the draft and final models, so keep both loaded ourselves.
        models = getattr(self._tls, "models", None)
        if models is None:
            models = self._tls.models = {}
        if model not in models:
            models[model] = load_model(model, dtype=mx.float16)
        ModelHolder.model, ModelHolder.model_path = models[model], model

        # Heat: on unclear audio whisper's default ladder re-decodes a chunk up to 6 times (0.0…1.0), which
        # measured 6-8 s of full GPU per chunk instead of ~1 s. One retry plus a token cap sized to the audio
        # keeps the worst case near 2 s and stops a runaway loop. The cleanup below catches what retries would.
        options.setdefault("temperature", (0.0, 0.5))
        options.setdefault("sample_len", int(len(audio) / SAMPLE_RATE * 8) + 24)
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=model,
            language=WHISPER_LANGUAGE,
            initial_prompt=prompt or None,
            condition_on_previous_text=False,
            **options,
        )
        if not result.get("segments"):
            return [(0.0, len(audio) / SAMPLE_RATE, result["text"])]
        # Drop segments whisper itself flags as junk: repetition loops or likely non-speech.
        return [(seg["start"], seg["end"], seg["text"]) for seg in result["segments"]
                if seg.get("compression_ratio", 0) <= 2.4
                and not (seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -1.0)]

    def _transcribe_openai(self, audio: np.ndarray, prompt: str) -> str:
        if not hasattr(self, "openai"):
            from openai import OpenAI

            self.openai = OpenAI()
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        wav = io.BytesIO()
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm.tobytes())
        result = self.openai.audio.transcriptions.create(
            model=OPENAI_TRANSCRIBE_MODEL,
            file=("chunk.wav", wav.getvalue(), "audio/wav"),
            prompt=prompt or None,
        )
        return result.text
