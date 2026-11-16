"""Settings, read once from .env (see .env.example)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

HAS_ANTHROPIC = bool(os.getenv("ANTHROPIC_API_KEY"))
HAS_OPENAI = bool(os.getenv("OPENAI_API_KEY"))

# Who you are. Names are what people call you; aliases catch common mis-transcriptions.
MY_NAME = os.getenv("MY_NAME", "Milan Sunar")
MY_ALIASES = [a.strip() for a in os.getenv("MY_ALIASES", "Milan,Mellem,Milon,Meelan").split(",") if a.strip()]

# Models. The provider is picked from the model name ("claude-*" -> Anthropic, else OpenAI).
# TRIAGE: cheap check "is this question for me?". MAIN: answers, summaries, knowledge-base upkeep.
TRIAGE_MODEL = os.getenv("TRIAGE_MODEL") or ("claude-haiku-5-5" if HAS_ANTHROPIC else "gpt-5.4-mini")
MAIN_MODEL = os.getenv("MAIN_MODEL") or ("gpt-5.4-mini" if HAS_OPENAI else "claude-opus-5-5")
# QUESTIONS: suggests what to ask. Runs only a few times per meeting, so it gets a stronger model.
QUESTIONS_MODEL = os.getenv("QUESTIONS_MODEL") or ("gpt-5.4" if HAS_OPENAI else MAIN_MODEL)
QUESTIONS_EFFORT = os.getenv("QUESTIONS_EFFORT", "medium")
# CHAT: the AI Chat tab and the weekly review. You ask rarely and want a careful answer, so it gets the best model.
CHAT_MODEL = os.getenv("CHAT_MODEL") or ("gpt-5.5" if HAS_OPENAI else "claude-opus-5-5")
CHAT_EFFORT = os.getenv("CHAT_EFFORT", "medium")
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "low")
OPENAI_REASONING = os.getenv("OPENAI_REASONING", "low")

# Speech-to-text: "local" (mlx-whisper on Apple Silicon, free) or "openai" (paid API).
TRANSCRIBE_BACKEND = os.getenv("TRANSCRIBE_BACKEND", "local")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "en") or None
OPENAI_TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
# Live draft line while someone is still talking: a small fast model re-reads the unfinished chunk.
PARTIAL_MODEL = os.getenv("PARTIAL_MODEL") or (
    "mlx-community/whisper-base.en-mlx" if WHISPER_LANGUAGE == "en" else "mlx-community/whisper-base-mlx")
PARTIAL_EVERY_S = float(os.getenv("PARTIAL_EVERY_S", "0.5"))  # PARTIAL_MODEL=off disables

# Voice ID: each distinct voice becomes a remembered Voice ("Speaker 3"), recognised again in later meetings;
# yours is the one recorded in Settings -> Record my voice. Cosine similarity with a Voice's print:
# >= VOICE_MATCH is that Voice; below VOICE_NEW for every Voice (and at least VOICE_NEW_MIN_S long) is a new
# Voice; in between stays unlabeled. The gap leans towards making a new Voice rather than mixing two people
# up, since two Voices of one person can be named the same later but a mix-up can't be undone.
# Scores per line are in sessions/<id>/audio_debug.log for tuning. VOICE_ME/VOICE_THEM are the old names.
VOICE_ID = os.getenv("VOICE_ID", "on").lower() in ("on", "1", "true", "yes")
VOICE_MATCH = float(os.getenv("VOICE_MATCH") or os.getenv("VOICE_ME") or "0.45")
VOICE_NEW = float(os.getenv("VOICE_NEW") or os.getenv("VOICE_THEM") or "0.35")
VOICE_NEW_MIN_S = float(os.getenv("VOICE_NEW_MIN_S", "2.0"))
FIRST_NAME = MY_NAME.split()[0]

# When to spend tokens.
# AUTO_MODE=off (default): AI runs only when you click (answer a line, ask, suggest questions, refresh notes).
# AUTO_MODE=on: also watches every line for questions to you, refreshes notes and suggests questions by itself.
AUTO_MODE = os.getenv("AUTO_MODE", "off").lower() in ("on", "1", "true", "yes")
SENSITIVITY = os.getenv("SENSITIVITY", "normal")  # low: only when named · normal · high: any question
SUMMARY_EVERY_S = float(os.getenv("SUMMARY_EVERY_S", "300"))
SUMMARY_MIN_NEW_WORDS = int(os.getenv("SUMMARY_MIN_NEW_WORDS", "80"))
QUESTIONS_EVERY_S = float(os.getenv("QUESTIONS_EVERY_S", "240"))  # fallback when no "any questions?" moment
QUESTIONS_COOLDOWN_S = float(os.getenv("QUESTIONS_COOLDOWN_S", "45"))  # min gap between suggestion runs
RECENT_WINDOW_S = float(os.getenv("RECENT_WINDOW_S", "360"))  # transcript the LLM sees verbatim
# Weekly review: one call per week, written on WEEKLY_DAY (0=Mon … 4=Fri) from WEEKLY_HOUR on. WEEKLY_DAY=off disables.
WEEKLY_DAY = None if os.getenv("WEEKLY_DAY", "4").lower() == "off" else int(os.getenv("WEEKLY_DAY", "4"))
WEEKLY_HOUR = int(os.getenv("WEEKLY_HOUR", "15"))

VAULT_DIR = Path(os.getenv("VAULT_DIR", ROOT / "vault"))
SESSIONS_DIR = Path(os.getenv("SESSIONS_DIR", ROOT / "sessions"))  # tests point this at a temp dir

# USD per 1M tokens (input, cached input, output), for the cost readout.
# Add or override in .env as MODEL_PRICES="model=IN/CACHED/OUT; other-model=IN/OUT".
PRICES = {
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.5": (5.00, 0.50, 30.00),
    "gpt-5.6-sol": (4.00, 0.40, 20.00),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-4o": (2.50, 1.25, 10.00),
    "gpt-4.1": (2.00, 0.50, 8.00),
    "claude-opus-5-5": (4.00, 0.20, 20.00),
    "claude-sonnet-5-5": (2.00, 0.20, 10.00),
    "claude-haiku-5-5": (0.10, 0.01, 0.50),  # cached rate assumed at 10% of input
}
for item in os.getenv("MODEL_PRICES", "").split(";"):
    try:
        model, rates = item.split("=")
        nums = [float(x) for x in rates.split("/")]
        i, c, o = nums if len(nums) == 3 else (nums[0], nums[0] * 0.1, nums[1])
        PRICES[model.strip()] = (i, c, o)
    except ValueError:
        pass
