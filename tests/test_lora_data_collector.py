"""Tests pour tools/lora_data_collector.py — Pipeline collecte QLoRA."""

import json
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest

# Ajouter le projet au path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from tools.lora_data_collector import (
    _is_valid_pair,
    _to_chatml,
    _strip_system_prefix,
    _split_qa,
    _resolve_lora_group,
    extract_training_pairs,
    extract_chat_history,
    extract_hippocampus,
    collect_all,
    save_datasets,
    LORA_GROUPS,
    SYSTEM_PROMPTS,
    KNOWN_AGENTS,
    MIN_RESPONSE_LENGTH,
    TRAINING_PAIRS_FILE,
)


# ============================================================
# TestValidation
# ============================================================

class TestValidation:
    """Tests pour _is_valid_pair."""

    def test_valid_pair(self):
        assert _is_valid_pair("prompt test", "x" * 60) is True

    def test_empty_prompt(self):
        assert _is_valid_pair("", "response") is False

    def test_empty_response(self):
        assert _is_valid_pair("prompt", "") is False

    def test_short_prompt(self):
        assert _is_valid_pair("abc", "x" * 60) is False

    def test_short_response(self):
        assert _is_valid_pair("prompt test", "short") is False

    def test_error_response_excluded(self):
        assert _is_valid_pair("prompt test", "Erreur lors du traitement" + "x" * 60) is False

    def test_warning_response_excluded(self):
        assert _is_valid_pair("prompt test", "⚠️ Quelque chose" + "x" * 60) is False


# ============================================================
# TestChatML
# ============================================================

class TestChatML:
    """Tests pour _to_chatml."""

    def test_basic_format(self):
        result = _to_chatml("system", "instruction", "response")
        assert len(result["messages"]) == 3
        assert result["messages"][0]["role"] == "system"
        assert result["messages"][1]["role"] == "user"
        assert result["messages"][2]["role"] == "assistant"

    def test_content_preserved(self):
        result = _to_chatml("sys", "inst", "resp")
        assert result["messages"][0]["content"] == "sys"
        assert result["messages"][1]["content"] == "inst"
        assert result["messages"][2]["content"] == "resp"

    def test_metadata_included(self):
        result = _to_chatml("s", "i", "r", metadata={"agent": "coder"})
        assert result["metadata"]["agent"] == "coder"

    def test_no_metadata(self):
        result = _to_chatml("s", "i", "r")
        assert "metadata" not in result


# ============================================================
# TestStripSystemPrefix
# ============================================================

class TestStripSystemPrefix:
    """Tests pour _strip_system_prefix."""

    def test_strip_system_tag(self):
        prompt = "[SYSTEM: Nexus V20 (Local First) | AGENT: CODER]\nDo something"
        assert "Do something" in _strip_system_prefix(prompt)
        assert "[SYSTEM" not in _strip_system_prefix(prompt)

    def test_strip_contrainte_tag(self):
        prompt = "[CONTRAINTE: Projet sur UN SEUL PC Windows...]\nTask"
        result = _strip_system_prefix(prompt)
        assert "[CONTRAINTE" not in result

    def test_strip_souvenirs(self):
        prompt = "[SOUVENIRS]:\nMemoire 1\nMemoire 2\nTâche réelle"
        result = _strip_system_prefix(prompt)
        assert "[SOUVENIRS]" not in result

    def test_plain_prompt_unchanged(self):
        prompt = "Écris une fonction qui calcule la factorielle"
        assert _strip_system_prefix(prompt) == prompt


# ============================================================
# TestSplitQA
# ============================================================

class TestSplitQA:
    """Tests pour _split_qa."""

    def test_valid_qa(self):
        text = "Q: Comment faire ?\nA: Voici la réponse"
        q, a = _split_qa(text)
        assert q == "Comment faire ?"
        assert a == "Voici la réponse"

    def test_no_qa_format(self):
        text = "Juste du texte normal"
        q, a = _split_qa(text)
        assert q == ""
        assert a == text

    def test_multiline_answer(self):
        text = "Q: Question\nA: Ligne 1\nLigne 2\nLigne 3"
        q, a = _split_qa(text)
        assert q == "Question"
        assert "Ligne 1" in a
        assert "Ligne 3" in a


# ============================================================
# TestLoRAGrouping
# ============================================================

class TestLoRAGrouping:
    """Tests pour _resolve_lora_group."""

    def test_coder_group(self):
        assert _resolve_lora_group("coder") == "coder"
        assert _resolve_lora_group("evolution") == "coder"
        assert _resolve_lora_group("factory") == "coder"

    def test_writer_group(self):
        assert _resolve_lora_group("writer") == "writer"
        assert _resolve_lora_group("formatter") == "writer"

    def test_strategist_group(self):
        assert _resolve_lora_group("strategist") == "strategist"
        assert _resolve_lora_group("architect") == "strategist"

    def test_researcher_group(self):
        assert _resolve_lora_group("researcher") == "researcher"
        assert _resolve_lora_group("security") == "researcher"

    def test_unknown_agent_fallback(self):
        assert _resolve_lora_group("unknown_agent") == "strategist"

    def test_all_known_agents_mapped(self):
        """Tous les agents connus ont un groupe LoRA."""
        for agent in KNOWN_AGENTS:
            lora = _resolve_lora_group(agent)
            assert lora in LORA_GROUPS


# ============================================================
# TestExtractTrainingPairs
# ============================================================

class TestExtractTrainingPairs:
    """Tests pour extract_training_pairs."""

    def test_extract_from_jsonl(self, tmp_path, monkeypatch):
        """Lit et parse le fichier JSONL."""
        filepath = tmp_path / "training_pairs.jsonl"
        pairs = [
            {"agent": "coder", "prompt": "Écris une fonction", "response": "x" * 100, "was_cloud": False, "timestamp": time.time()},
            {"agent": "writer", "prompt": "Rédige un résumé", "response": "y" * 100, "was_cloud": True, "timestamp": time.time()},
        ]
        with open(filepath, "w", encoding="utf-8") as f:
            for p in pairs:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")

        monkeypatch.setattr("tools.lora_data_collector.TRAINING_PAIRS_FILE", str(filepath))
        results = extract_training_pairs()
        assert len(results) == 2
        assert results[0]["metadata"]["lora"] == "coder"
        assert results[1]["metadata"]["lora"] == "writer"

    def test_skip_short_response(self, tmp_path, monkeypatch):
        """Réponse trop courte → exclue."""
        filepath = tmp_path / "training_pairs.jsonl"
        pair = {"agent": "coder", "prompt": "prompt test", "response": "short", "was_cloud": False}
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(json.dumps(pair) + "\n")
        monkeypatch.setattr("tools.lora_data_collector.TRAINING_PAIRS_FILE", str(filepath))
        results = extract_training_pairs()
        assert len(results) == 0

    def test_missing_file(self, tmp_path, monkeypatch):
        """Fichier absent → liste vide."""
        monkeypatch.setattr("tools.lora_data_collector.TRAINING_PAIRS_FILE", str(tmp_path / "nope.jsonl"))
        results = extract_training_pairs()
        assert results == []

    def test_malformed_json_skipped(self, tmp_path, monkeypatch):
        """Lignes JSON invalides → ignorées sans crash."""
        filepath = tmp_path / "training_pairs.jsonl"
        with open(filepath, "w") as f:
            f.write("not json\n")
            f.write(json.dumps({"agent": "coder", "prompt": "test prompt", "response": "z" * 100}) + "\n")
        monkeypatch.setattr("tools.lora_data_collector.TRAINING_PAIRS_FILE", str(filepath))
        results = extract_training_pairs()
        assert len(results) == 1


# ============================================================
# TestExtractChatHistory
# ============================================================

class TestExtractChatHistory:
    """Tests pour extract_chat_history."""

    def test_extract_user_assistant_pairs(self, tmp_path, monkeypatch):
        filepath = tmp_path / "chat_history.json"
        data = {
            "version": "1.0",
            "messages": [
                {"role": "user", "content": "Que fait cette fonction ?", "timestamp": time.time()},
                {"role": "assistant", "content": "a" * 100, "timestamp": time.time()},
                {"role": "user", "content": "Et celle-ci ?", "timestamp": time.time()},
                {"role": "assistant", "content": "b" * 100, "timestamp": time.time()},
            ],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f)
        monkeypatch.setattr("tools.lora_data_collector.CHAT_HISTORY_FILE", str(filepath))
        results = extract_chat_history()
        assert len(results) == 2

    def test_skip_consecutive_same_role(self, tmp_path, monkeypatch):
        """Deux messages user consécutifs → pas de paire."""
        filepath = tmp_path / "chat_history.json"
        data = {"version": "1.0", "messages": [
            {"role": "user", "content": "Premier message utilisateur"},
            {"role": "user", "content": "Deuxième message utilisateur"},
            {"role": "assistant", "content": "c" * 100},
        ]}
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f)
        monkeypatch.setattr("tools.lora_data_collector.CHAT_HISTORY_FILE", str(filepath))
        results = extract_chat_history()
        assert len(results) == 1  # Seul msg2→assistant

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tools.lora_data_collector.CHAT_HISTORY_FILE", str(tmp_path / "nope.json"))
        assert extract_chat_history() == []


# ============================================================
# TestExtractHippocampus
# ============================================================

class TestExtractHippocampus:
    """Tests pour extract_hippocampus."""

    def test_extract_arcs(self, tmp_path, monkeypatch):
        filepath = tmp_path / "hippocampus_state.json"
        data = {
            "arcs": [
                {
                    "title": "Percée Evolution",
                    "arc_type": "breakthrough",
                    "narrative": "n" * 100,
                    "emotional_arc": "frustration → enthousiasme",
                },
            ],
            "episodes": [],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f)
        monkeypatch.setattr("tools.lora_data_collector.HIPPOCAMPUS_FILE", str(filepath))
        results = extract_hippocampus()
        assert len(results) == 1
        assert results[0]["metadata"]["arc_type"] == "breakthrough"

    def test_skip_short_narrative(self, tmp_path, monkeypatch):
        filepath = tmp_path / "hippocampus_state.json"
        data = {"arcs": [{"title": "T", "arc_type": "t", "narrative": "too short"}], "episodes": []}
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f)
        monkeypatch.setattr("tools.lora_data_collector.HIPPOCAMPUS_FILE", str(filepath))
        results = extract_hippocampus()
        assert len(results) == 0


# ============================================================
# TestCollectAll
# ============================================================

class TestCollectAll:
    """Tests pour collect_all."""

    def test_filter_by_source(self, tmp_path, monkeypatch):
        """--source training → n'extrait que training_pairs."""
        filepath = tmp_path / "training_pairs.jsonl"
        pair = {"agent": "coder", "prompt": "test prompt", "response": "r" * 100}
        with open(filepath, "w") as f:
            f.write(json.dumps(pair) + "\n")
        monkeypatch.setattr("tools.lora_data_collector.TRAINING_PAIRS_FILE", str(filepath))
        monkeypatch.setattr("tools.lora_data_collector.CHAT_HISTORY_FILE", str(tmp_path / "nope"))
        monkeypatch.setattr("tools.lora_data_collector.HIPPOCAMPUS_FILE", str(tmp_path / "nope"))

        result = collect_all(sources=["training"])
        total = sum(len(v) for v in result.values())
        assert total == 1

    def test_filter_by_agent(self, tmp_path, monkeypatch):
        """--agent coder → seulement les entrées du groupe coder."""
        filepath = tmp_path / "training_pairs.jsonl"
        pairs = [
            {"agent": "coder", "prompt": "test prompt", "response": "r" * 100},
            {"agent": "writer", "prompt": "test prompt", "response": "r" * 100},
        ]
        with open(filepath, "w") as f:
            for p in pairs:
                f.write(json.dumps(p) + "\n")
        monkeypatch.setattr("tools.lora_data_collector.TRAINING_PAIRS_FILE", str(filepath))
        monkeypatch.setattr("tools.lora_data_collector.CHAT_HISTORY_FILE", str(tmp_path / "nope"))
        monkeypatch.setattr("tools.lora_data_collector.HIPPOCAMPUS_FILE", str(tmp_path / "nope"))

        result = collect_all(sources=["training"], agent_filter="coder")
        assert "coder" in result
        assert "writer" not in result


# ============================================================
# TestSaveDatasets
# ============================================================

class TestSaveDatasets:
    """Tests pour save_datasets."""

    def test_creates_jsonl_files(self, tmp_path):
        by_lora = {
            "coder": [_to_chatml("s", "i", "r", metadata={"source": "test", "lora": "coder"})],
        }
        save_datasets(by_lora, str(tmp_path))
        filepath = tmp_path / "coder.jsonl"
        assert filepath.exists()
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert len(entry["messages"]) == 3


# ============================================================
# TestSaveTrainingPairHook
# ============================================================

class TestSaveTrainingPairHook:
    """Tests pour BaseAgent._save_training_pair."""

    def test_saves_to_jsonl(self, tmp_path, monkeypatch):
        from core.base_agent import BaseAgent
        filepath = tmp_path / "training_pairs.jsonl"
        monkeypatch.setattr(BaseAgent, "_TRAINING_PAIRS_FILE", str(filepath))

        agent = BaseAgent.__new__(BaseAgent)
        agent.name = "coder"
        agent._save_training_pair("test prompt", "x" * 100, was_cloud=False)

        assert filepath.exists()
        with open(filepath, "r", encoding="utf-8") as f:
            line = f.readline()
        data = json.loads(line)
        assert data["agent"] == "coder"
        assert data["prompt"] == "test prompt"
        assert data["was_cloud"] is False

    def test_skips_short_response(self, tmp_path, monkeypatch):
        from core.base_agent import BaseAgent
        filepath = tmp_path / "training_pairs.jsonl"
        monkeypatch.setattr(BaseAgent, "_TRAINING_PAIRS_FILE", str(filepath))

        agent = BaseAgent.__new__(BaseAgent)
        agent.name = "coder"
        agent._save_training_pair("test prompt", "short", was_cloud=False)

        assert not filepath.exists()

    def test_skips_error_response(self, tmp_path, monkeypatch):
        from core.base_agent import BaseAgent
        filepath = tmp_path / "training_pairs.jsonl"
        monkeypatch.setattr(BaseAgent, "_TRAINING_PAIRS_FILE", str(filepath))

        agent = BaseAgent.__new__(BaseAgent)
        agent.name = "coder"
        agent._save_training_pair("test prompt", "Erreur lors du traitement de la requête" + "x" * 100, was_cloud=False)

        assert not filepath.exists()

    def test_truncates_long_response(self, tmp_path, monkeypatch):
        from core.base_agent import BaseAgent
        filepath = tmp_path / "training_pairs.jsonl"
        monkeypatch.setattr(BaseAgent, "_TRAINING_PAIRS_FILE", str(filepath))

        agent = BaseAgent.__new__(BaseAgent)
        agent.name = "coder"
        agent._save_training_pair("prompt", "x" * 10000, was_cloud=False)

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.loads(f.readline())
        assert len(data["response"]) == 4000

    def test_rotation_on_max_size(self, tmp_path, monkeypatch):
        from core.base_agent import BaseAgent
        filepath = tmp_path / "training_pairs.jsonl"
        monkeypatch.setattr(BaseAgent, "_TRAINING_PAIRS_FILE", str(filepath))
        monkeypatch.setattr(BaseAgent, "_TRAINING_PAIRS_MAX_SIZE", 100)

        # Écrire assez pour dépasser 100 bytes
        with open(filepath, "w") as f:
            f.write("x" * 200)

        agent = BaseAgent.__new__(BaseAgent)
        agent.name = "coder"
        agent._save_training_pair("prompt test", "y" * 100, was_cloud=False)

        # Le fichier original doit être petit (nouvelles données)
        assert filepath.exists()
        old_file = tmp_path / "training_pairs.jsonl.old"
        assert old_file.exists()


# ============================================================
# TestSystemPrompts
# ============================================================

class TestSystemPrompts:
    """Tests de cohérence des constantes."""

    def test_all_lora_groups_have_system_prompt(self):
        for lora_name in LORA_GROUPS:
            assert lora_name in SYSTEM_PROMPTS, f"Pas de system prompt pour {lora_name}"

    def test_system_prompts_not_empty(self):
        for name, prompt in SYSTEM_PROMPTS.items():
            assert len(prompt) > 20, f"System prompt trop court pour {name}"

    def test_lora_groups_cover_known_agents(self):
        covered = set()
        for agents in LORA_GROUPS.values():
            covered.update(agents)
        assert KNOWN_AGENTS.issubset(covered), f"Agents non couverts: {KNOWN_AGENTS - covered}"
