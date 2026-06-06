# -*- coding: utf-8 -*-
"""Tests V26.0 — compactage du contexte chat (facon console Claude Code).

Au seuil (~60% du ctx), les tours anciens sont resumes (1 appel LLM mocke) en un
digest persiste, le brut est archive, seuls les derniers tours restent bruts.
SECURITE testee : un resume vide/echoue n'altere RIEN (jamais de perte sans digest).
"""
import json
import pytest
from unittest.mock import patch

import core.chat_engine as ce
from core.chat_engine import ChatEngine, COMPACT_KEEP_RAW, COMPACT_TRIGGER_CHARS


@pytest.fixture(autouse=True)
def reset_engine():
    ChatEngine.reset_singleton()
    yield
    ChatEngine.reset_singleton()


@pytest.fixture
def engine():
    with patch.object(ChatEngine, "_load"):
        e = ChatEngine()
    e._conversation_digest = ""
    return e


def _msgs(n, char_each):
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": "x" * char_each, "timestamp": float(i)} for i in range(n)]


@pytest.mark.asyncio
async def test_pas_de_compactage_sous_seuil(engine, monkeypatch):
    engine.messages = _msgs(10, 100)  # 1000 chars << seuil
    called = {"n": 0}
    async def _fake(t):
        called["n"] += 1
        return "digest"
    monkeypatch.setattr(engine, "_summarize_for_digest", _fake)
    await engine._maybe_compact_context()
    assert called["n"] == 0
    assert len(engine.messages) == 10


@pytest.mark.asyncio
async def test_compactage_au_dessus_seuil(engine, monkeypatch):
    engine.messages = _msgs(40, 1000)  # 40000 chars > seuil (~22000)
    assert sum(len(m["content"]) for m in engine.messages) > COMPACT_TRIGGER_CHARS
    async def _fake(t):
        return "RESUME FIDELE : faits, decisions et fils ouverts de la conversation, assez long."
    monkeypatch.setattr(engine, "_summarize_for_digest", _fake)
    monkeypatch.setattr(engine, "_archive_messages", lambda m: None)
    monkeypatch.setattr(engine, "_trace_compaction", lambda n, d: None)
    monkeypatch.setattr(engine, "_save", lambda: None)
    await engine._maybe_compact_context()
    assert engine._conversation_digest.startswith("RESUME FIDELE")
    assert len(engine.messages) == COMPACT_KEEP_RAW  # ne garde que les derniers tours


@pytest.mark.asyncio
async def test_resume_vide_naltere_rien(engine, monkeypatch):
    engine.messages = _msgs(40, 1000)
    async def _fake(t):
        return ""  # echec du resume
    monkeypatch.setattr(engine, "_summarize_for_digest", _fake)
    await engine._maybe_compact_context()
    assert len(engine.messages) == 40, "securite violee : perte sans digest valide"
    assert engine._conversation_digest == ""


@pytest.mark.asyncio
async def test_digest_existant_est_fusionne(engine, monkeypatch):
    engine._conversation_digest = "DIGEST PRECEDENT"
    engine.messages = _msgs(40, 1000)
    seen = {"text": None}
    async def _fake(t):
        seen["text"] = t
        return "NOUVEAU DIGEST consolide avec le precedent et les nouveaux tours."
    monkeypatch.setattr(engine, "_summarize_for_digest", _fake)
    monkeypatch.setattr(engine, "_archive_messages", lambda m: None)
    monkeypatch.setattr(engine, "_trace_compaction", lambda n, d: None)
    monkeypatch.setattr(engine, "_save", lambda: None)
    await engine._maybe_compact_context()
    assert "DIGEST PRECEDENT" in seen["text"]  # l'ancien digest est inclus dans le resume


def test_save_inclut_digest(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "CHAT_HISTORY_FILE", tmp_path / "ch.json")
    engine._conversation_digest = "mon digest persiste"
    engine.messages = [{"role": "user", "content": "hi"}]
    engine._save()
    data = json.loads((tmp_path / "ch.json").read_text(encoding="utf-8"))
    assert data.get("conversation_digest") == "mon digest persiste"
