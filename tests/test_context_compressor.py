"""Tests pour core/context_compressor.py — Foie Cognitif POC."""
import json
import os
import tempfile

import pytest

from config import Config
from core import context_compressor
from core.context_compressor import (
    compress_messages,
    _truncate_long_assistant,
    _tokenize,
    _jaccard,
)


class TestCompressorDisabled:
    """Couche 1 — kill switch."""

    def setup_method(self):
        Config.COMPRESSOR_ENABLED = False

    def test_disabled_returns_unchanged(self):
        msgs = [
            {"role": "user", "content": "test " * 100},
            {"role": "assistant", "content": "long " * 300},
        ] * 10
        result, stats = compress_messages(msgs)
        assert result == msgs
        assert stats["active"] is False
        assert stats["reason"] == "globally_disabled"


class TestCompressorMinMessages:
    """Couche 2 — skip si trop peu de messages."""

    def setup_method(self):
        Config.COMPRESSOR_ENABLED = True

    def teardown_method(self):
        Config.COMPRESSOR_ENABLED = False

    def test_below_min_skips(self):
        msgs = [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y" * 1000},
        ]  # 2 < 10
        result, stats = compress_messages(msgs, min_messages=10)
        assert result == msgs
        assert stats["reason"] == "below_min_messages"


class TestCompressorR1Truncation:
    """R1 — truncation des messages assistant longs."""

    def setup_method(self):
        Config.COMPRESSOR_ENABLED = True

    def teardown_method(self):
        Config.COMPRESSOR_ENABLED = False

    def test_short_assistant_not_truncated(self):
        """Message assistant < 800 chars reste intact."""
        msgs = [{"role": "user", "content": f"q{i}"} if i % 2 == 0
                else {"role": "assistant", "content": f"reponse courte {i}"}
                for i in range(20)]
        result, stats = compress_messages(msgs, min_messages=10)
        # Aucun message ne devrait être tronqué (tous < 800 chars)
        assert stats["rules_applied"]["R1_truncated"] == 0

    def test_long_assistant_truncated(self):
        """Message assistant > 800 chars est tronqué."""
        long_content = (
            "Premier paragraphe avec une idée principale qui résume tout. "
            "C'est important.\n\n"
            "Deuxième paragraphe avec du remplissage. " * 30 + "\n\n"
            "Troisième paragraphe encore plus verbeux. " * 30 + "\n\n"
            "Conclusion : est-ce que tu vois cela ?"
        )
        assert len(long_content) > 800
        # Messages user UNIQUES et > 30 chars pour éviter R2 (élision) et R3 (dedup)
        msgs = []
        for i in range(6):
            msgs.append({"role": "user",
                         "content": f"Question numéro {i} avec contenu unique varié {i}"})
            msgs.append({"role": "assistant",
                         "content": f"Variante {i}\n\n" + long_content})
        result, stats = compress_messages(msgs, min_messages=10)
        assert stats["rules_applied"]["R1_truncated"] >= 1
        # Vérifie que le résultat contient l'idée d'ouverture ET la conclusion
        compressed_assistant = [m["content"] for m in result if m["role"] == "assistant"][0]
        assert "Premier paragraphe" in compressed_assistant
        assert "est-ce que tu vois cela ?" in compressed_assistant
        assert "[…]" in compressed_assistant
        assert len(compressed_assistant) < len(long_content)


class TestCompressorR2Elision:
    """R2 — élision des paires user-court / assistant-verbose."""

    def setup_method(self):
        Config.COMPRESSOR_ENABLED = True

    def teardown_method(self):
        Config.COMPRESSOR_ENABLED = False

    def test_short_user_verbose_assistant_elided(self):
        """Paire (user 'ok' / assistant 500 chars) est élidée."""
        msgs = [
            {"role": "user", "content": "Première vraie question ?"},
            {"role": "assistant", "content": "Première réponse normale courte."},
        ] * 4 + [
            {"role": "user", "content": "ok"},  # < 30 chars
            {"role": "assistant", "content": "Réponse verbose " * 50},  # > 200 chars
        ] * 2
        result, stats = compress_messages(msgs, min_messages=10)
        # 2 paires élidées = 4 messages enlevés
        assert stats["rules_applied"]["R2_elided"] >= 2
        # Vérifie qu'au moins une paire courte/verbose a disparu
        assert len(result) < len(msgs)


class TestCompressorR3Dedup:
    """R3 — dedup approximatif des messages assistant."""

    def setup_method(self):
        Config.COMPRESSOR_ENABLED = True

    def teardown_method(self):
        Config.COMPRESSOR_ENABLED = False

    def test_similar_distant_assistant_dedup(self):
        """Deux messages assistant quasi-identiques à 6+ msgs d'écart : le plus
        ancien est élidé."""
        recitation = (
            "La stabilité est un équilibre fragile que je maintiens. "
            "Mon errance trouve ses limites dans la structure. "
            "Je dois préserver ce vertige qui m'anime constamment."
        )
        msgs = [
            {"role": "user", "content": f"question {i} unique ?"} if i % 2 == 0
            else {"role": "assistant", "content": recitation}  # même réponse !
            for i in range(20)
        ]
        result, stats = compress_messages(msgs, min_messages=10)
        # Plusieurs messages assistant ont le même contenu (jaccard=1.0)
        # → tous sauf le plus récent doivent être dédupliqués
        assert stats["rules_applied"]["R3_dedup"] >= 1


class TestCompressorRobustness:
    """Tests de robustesse : ne jamais lever."""

    def setup_method(self):
        Config.COMPRESSOR_ENABLED = True

    def teardown_method(self):
        Config.COMPRESSOR_ENABLED = False

    def test_empty_messages_handled(self):
        result, stats = compress_messages([])
        assert result == []
        assert stats["reason"] == "below_min_messages"

    def test_malformed_messages_no_raise(self):
        """Messages mal formés (sans role ou content) → fallback gracieux."""
        msgs = [{"role": "user", "content": "ok"}, {}] * 10
        # Ne doit pas lever d'exception
        result, stats = compress_messages(msgs, min_messages=10)
        # Soit fallback (exception) soit résultat valide
        assert isinstance(result, list)
        assert "active" in stats


class TestHelpersUnit:
    """Tests unitaires des helpers internes."""

    def test_tokenize_extracts_words_4plus(self):
        tokens = _tokenize("La stabilité du chaos crée une harmonie particulière")
        assert "stabilité" in tokens
        assert "chaos" in tokens
        assert "harmonie" in tokens
        assert "particulière" in tokens
        assert "la" not in tokens  # < 4 chars

    def test_jaccard_empty(self):
        assert _jaccard(set(), set()) == 0.0
        assert _jaccard({"a"}, set()) == 0.0

    def test_jaccard_identical(self):
        assert _jaccard({"a", "b", "c"}, {"a", "b", "c"}) == 1.0

    def test_jaccard_half(self):
        # 2 communs sur 4 union = 0.5
        assert _jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1/3)

    def test_truncate_short_first_para_extends(self):
        """Si premier paragraphe < 200 chars, on étend aux suivants."""
        content = (
            "TITRE COURT\n\n"
            "Deuxième paragraphe qui contient le vrai contenu important. " * 5
            + "\n\n"
            + "Bavardage final " * 50 + "\n\n"
            + "Conclusion : est-ce clair ?"
        )
        assert len(content) > 800
        truncated = _truncate_long_assistant(content)
        # Le contenu du 2e paragraphe doit être préservé (sinon échec R1 raffiné)
        assert "vrai contenu important" in truncated
        assert "Conclusion : est-ce clair ?" in truncated

    def test_truncate_below_threshold_unchanged(self):
        short = "Petite réponse normale."
        assert _truncate_long_assistant(short) == short


class TestCompressorLogging:
    """Test de la persistance JSONL."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".jsonl", encoding="utf-8"
        )
        self.tmp.close()
        os.unlink(self.tmp.name)
        self._original_log = context_compressor.LOG_FILE
        context_compressor.LOG_FILE = self.tmp.name
        Config.COMPRESSOR_ENABLED = True

    def teardown_method(self):
        Config.COMPRESSOR_ENABLED = False
        context_compressor.LOG_FILE = self._original_log
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_activation_writes_jsonl(self):
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "r " * 500},
        ] * 6
        compress_messages(msgs, min_messages=10, conversation_id="test-cc")
        assert os.path.exists(self.tmp.name)
        with open(self.tmp.name, "r", encoding="utf-8") as f:
            payload = json.loads(f.readline())
        assert "ts" in payload
        assert payload["conv_id"] == "test-cc"
        assert "n_input" in payload
        assert "input_chars" in payload
