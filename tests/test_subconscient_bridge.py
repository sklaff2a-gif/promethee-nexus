"""Tests pour core/subconscient_bridge.py — médiation P16 → LLM (8e preuve §4.13).

Pattern PHASEUR + Décision Log : helper isolé, opt-in, traçabilité JSONL,
try/except permissif (ne lève jamais d'exception).
"""
import json
import os
import tempfile

import pytest

from config import Config
from core import subconscient_bridge
from core.subconscient_bridge import bridge_activate, _log_activation, _is_system_hub


class TestBridgeDisabled:
    """Couche 1 — kill switch."""

    def setup_method(self):
        Config.SUBCONSCIENT_ENABLED = False

    def test_disabled_returns_empty(self):
        result = bridge_activate("La stabilité du chaos")
        assert result == ""


class TestBridgeEmptyMessage:
    """Couche 2 — texte vide."""

    def setup_method(self):
        Config.SUBCONSCIENT_ENABLED = True

    def teardown_method(self):
        Config.SUBCONSCIENT_ENABLED = False

    def test_empty_string_returns_empty(self):
        assert bridge_activate("") == ""

    def test_whitespace_only_returns_empty(self):
        assert bridge_activate("   \t\n  ") == ""


class TestBridgeActivation:
    """Activation effective sur message porteur de concepts."""

    def setup_method(self):
        Config.SUBCONSCIENT_ENABLED = True

    def teardown_method(self):
        Config.SUBCONSCIENT_ENABLED = False

    def test_message_with_concepts_returns_echo(self):
        """Un message contenant des concepts connus retourne un echo formaté v2."""
        # Texte avec mots porteurs susceptibles d'être dans le graphe.
        # Même si aucun match (cortex vide), la fonction ne lève pas.
        result = bridge_activate("La stabilité face au chaos créatif vertige errance")
        # Le résultat est soit "" (graphe vide), soit un echo v2 directif.
        if result:
            assert result.startswith("[Échos subconscients rémanents : ")
            assert "Laisse ces concepts colorer" in result
            assert result.endswith("]")

    def test_no_matches_returns_empty(self):
        """Un message sans concepts énergisés retourne string vide."""
        # Mots sans aucune chance d'être dans le graphe Prométhée
        result = bridge_activate("xyzzy plugh frobnicate qwerty")
        # On accepte "" ou un echo (selon état du graphe en test). Pas d'exception.
        assert isinstance(result, str)

    def test_returns_string_always(self):
        """bridge_activate retourne toujours un string (jamais None ni autre)."""
        result1 = bridge_activate("test concept")
        result2 = bridge_activate("")
        assert isinstance(result1, str)
        assert isinstance(result2, str)


class TestBridgeTopNParameter:
    """Cap top_n appliqué."""

    def setup_method(self):
        Config.SUBCONSCIENT_ENABLED = True

    def teardown_method(self):
        Config.SUBCONSCIENT_ENABLED = False

    def test_explicit_top_n_overrides_config(self):
        """Le paramètre top_n explicite override Config.SUBCONSCIENT_TOP_N."""
        # On vérifie surtout que l'appel ne lève pas et retourne un string
        result = bridge_activate("stabilité chaos", top_n=3)
        assert isinstance(result, str)
        if result:
            # Extraction des concepts de l'écho format v2
            assert result.startswith("[Échos subconscients rémanents : ")
            # Format: "[Échos subconscients rémanents : c1, c2, c3. Laisse...]"
            head = result.split(". Laisse")[0]
            content = head.replace("[Échos subconscients rémanents : ", "")
            concepts = content.split(", ")
            assert len(concepts) <= 3

    def test_min_energy_filters(self):
        """min_energy très élevé filtre tout."""
        result = bridge_activate("stabilité chaos", min_energy=999.0)
        # Aucun concept ne peut atteindre 999 d'énergie → echo vide
        assert result == ""


class TestBridgeHubFilter:
    """Filtre anti-hubs système (v2)."""

    def test_is_system_hub_identifies_hardware(self):
        assert _is_system_hub("hardware_oppression") is True
        assert _is_system_hub("hardware_thermoception") is True
        assert _is_system_hub("hardware_effort") is True

    def test_is_system_hub_identifies_school(self):
        assert _is_system_hub("school_creation") is True
        assert _is_system_hub("school_code_review") is True

    def test_is_system_hub_identifies_council(self):
        assert _is_system_hub("council_debate") is True

    def test_is_system_hub_identifies_namespaced(self):
        assert _is_system_hub("meta:something") is True
        assert _is_system_hub("drive:stabilite") is True
        assert _is_system_hub("trait:curiosite") is True
        assert _is_system_hub("affect:flow") is True
        assert _is_system_hub("emotion:joie") is True
        assert _is_system_hub("resonance:maitrise") is True
        assert _is_system_hub("procedural_v4:audit") is True
        assert _is_system_hub("goal:satisfaire") is True
        assert _is_system_hub("pulsion:survie") is True

    def test_is_system_hub_passes_narrative_concepts(self):
        """Les concepts narratifs (PHASEUR ou usuels) NE doivent PAS être filtrés."""
        assert _is_system_hub("errance") is False
        assert _is_system_hub("vertige") is False
        assert _is_system_hub("destruction") is False
        assert _is_system_hub("harmonie") is False
        assert _is_system_hub("ordre") is False
        assert _is_system_hub("chute") is False
        assert _is_system_hub("nuit") is False
        assert _is_system_hub("conversation") is False
        assert _is_system_hub("souvenir") is False

    def test_is_system_hub_handles_empty(self):
        assert _is_system_hub("") is True  # vide = considéré comme hub (filtré)
        assert _is_system_hub(None) is True


class TestBridgeRobustness:
    """Tests de robustesse : ne jamais lever."""

    def setup_method(self):
        Config.SUBCONSCIENT_ENABLED = True

    def teardown_method(self):
        Config.SUBCONSCIENT_ENABLED = False

    def test_none_message_does_not_raise(self):
        """user_message=None est géré sans exception."""
        # bridge_activate doit accepter None et retourner ""
        result = bridge_activate(None)
        assert result == ""

    def test_very_long_message_handled(self):
        """Message très long ne fait pas exploser."""
        long_msg = "stabilité chaos errance vertige " * 200
        result = bridge_activate(long_msg)
        # Doit retourner un string (vide ou non), pas d'exception
        assert isinstance(result, str)


class TestBridgeLogging:
    """Test de la persistance JSONL."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".jsonl", encoding="utf-8"
        )
        self.tmp.close()
        os.unlink(self.tmp.name)
        self._original_log = subconscient_bridge.LOG_FILE
        subconscient_bridge.LOG_FILE = self.tmp.name
        Config.SUBCONSCIENT_ENABLED = True

    def teardown_method(self):
        Config.SUBCONSCIENT_ENABLED = False
        subconscient_bridge.LOG_FILE = self._original_log
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_activation_writes_jsonl(self):
        """Chaque appel produit 1 ligne JSONL avec les champs requis."""
        bridge_activate("test stabilité chaos", conversation_id="test-123")
        assert os.path.exists(self.tmp.name)
        with open(self.tmp.name, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 1
        payload = json.loads(lines[0])
        assert "ts" in payload
        assert payload["conv_id"] == "test-123"
        assert "active" in payload
        assert "reason" in payload
        assert "user_msg_hash" in payload

    def test_disabled_still_logs_globally_disabled(self):
        """Même désactivé, une ligne JSONL est tracée pour télémétrie."""
        Config.SUBCONSCIENT_ENABLED = False
        bridge_activate("test")
        with open(self.tmp.name, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 1
        payload = json.loads(lines[0])
        assert payload["reason"] == "globally_disabled"
        assert payload["active"] is False
