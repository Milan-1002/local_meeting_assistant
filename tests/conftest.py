"""Shared test setup: no audio, no network, no real vault or sessions folder.

The environment is set before any app module is imported, because config.py reads it once at import.
Fake API keys keep model selection the same as with real keys, and `llm.complete` is always stubbed,
so a test that forgot to stub would fail on auth rather than spend money.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="copilot-tests-"))
os.environ.update({
    "SESSIONS_DIR": str(_TMP / "sessions"),
    "VAULT_DIR": str(_TMP / "vault"),
    "MY_NAME": "Milan Sunar",
    "AUTO_MODE": "off",
    "WEEKLY_DAY": "off",
    "VOICE_ID": "off",
    "ANTHROPIC_API_KEY": "test-not-a-key",
    "OPENAI_API_KEY": "test-not-a-key",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import copilot  # noqa: E402
import kb  # noqa: E402
import llm  # noqa: E402


def fill(schema: dict):
    """A minimal value that matches a JSON schema, for stubbed model replies."""
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "null")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object":
        return {k: fill(v) for k, v in schema.get("properties", {}).items()}
    return {"array": [], "string": "stub", "integer": 0, "number": 0, "boolean": False}.get(t)


class StubLLM:
    """Records every prompt; answers with `reply` (or a schema-shaped filler)."""

    def __init__(self):
        self.calls: list[dict] = []
        self.reply = None

    async def __call__(self, model, system, user, *, schema=None, **kw):
        self.calls.append({"model": model, "system": system, "user": user, "schema": schema})
        if self.reply is not None:
            return self.reply(schema) if callable(self.reply) else self.reply
        return fill(schema) if schema else "stub"


@pytest.fixture
def stub_llm(monkeypatch):
    stub = StubLLM()
    monkeypatch.setattr(llm, "complete", stub)
    return stub


@pytest.fixture
def app(stub_llm, monkeypatch, tmp_path):
    """The running app (lifespan included) with a fresh Meeting whose files go to tmp_path."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(copilot, "meeting", copilot.Meeting())
    monkeypatch.setattr(copilot, "meeting_id", "2026-01-01_09-00-00")
    monkeypatch.setattr(copilot, "session_dir", tmp_path / "session")
    copilot.app.state.starters = []
    with TestClient(copilot.app) as client:
        client.llm = stub_llm
        yield client


def wait_for(cond, timeout: float = 3.0):
    """Poll until cond() is truthy: lines and answers land on the app's loop, not in the request."""
    end = time.time() + timeout
    while time.time() < end:
        if value := cond():
            return value
        time.sleep(0.02)
    raise AssertionError("timed out waiting for condition")
