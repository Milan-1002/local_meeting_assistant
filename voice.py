"""Tell voices apart, on-device: audio -> voice print -> remembered Voice (voices.py).

ECAPA speaker embeddings (SpeechBrain, ~83 MB, downloaded on first use) compared by cosine similarity
with every remembered Voice. Each call works on one slice of audio and keeps no audio, so memory stays
flat however long the meeting runs.
"""

from __future__ import annotations

import numpy as np

import speakers
from config import SESSIONS_DIR
from voices import MIN_S, Registry

SAMPLE_RATE = 16_000
OLD_VOICE_FILE = SESSIONS_DIR / "voice.npy"  # the single "me" print from before remembered Voices
VOICES_FILE = SESSIONS_DIR / "voices.json"  # every Voice's print; personal, like the rest of sessions/


class VoiceID:
    def __init__(self, registry: Registry | None = None):
        self.model = None
        if registry is None:  # the app's own registry; one passed in (tests, scripts) is left alone
            registry = Registry(VOICES_FILE)
            registry.migrate(OLD_VOICE_FILE)
        self.registry = registry

    @property
    def enrolled(self) -> bool:
        """Whether Me's Voice exists (Settings shows "Voice set up")."""
        return self.registry.has_me

    def load(self) -> None:
        if self.model is not None:
            return
        import torch
        from speechbrain.inference.speaker import EncoderClassifier

        torch.set_num_threads(2)  # leave the cores to whisper
        self.model = EncoderClassifier.from_hparams("speechbrain/spkrec-ecapa-voxceleb",
                                                    savedir=str(SESSIONS_DIR / ".models" / "ecapa"),
                                                    run_opts={"device": "cpu"})

    def embed(self, audio: np.ndarray) -> np.ndarray:
        import torch

        self.load()
        with torch.no_grad():
            e = self.model.encode_batch(torch.from_numpy(audio.astype(np.float32)).unsqueeze(0)).squeeze().numpy()
        return e / (np.linalg.norm(e) + 1e-9)

    def label(self, audio: np.ndarray) -> tuple[str, float | None]:
        """(speaker label, best similarity) for one slice: speakers.ME, a Voice ("s3"), or "" when unsure."""
        duration = len(audio) / SAMPLE_RATE
        if duration < MIN_S:
            return "", None
        number, score = self.registry.identify(self.embed(audio), duration)
        if number is None:
            return "", score
        label = speakers.voice(number)
        return (speakers.ME if number == self.registry.me or speakers.is_me(label) else label), score

    def enroll(self, chunks: list[np.ndarray]) -> None:
        embs = [self.embed(c) for c in chunks if len(c) >= MIN_S * SAMPLE_RATE]
        self.registry.set_me(np.mean(embs, axis=0))

    def forget(self) -> None:
        self.registry.forget_me()

    def end_meeting(self) -> None:
        """Learn from this meeting's confident matches and expire stale unnamed Voices (named ones are kept)."""
        self.registry.end_meeting(keep=speakers.named_numbers())
