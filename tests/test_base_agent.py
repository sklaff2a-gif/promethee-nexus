"""Tests pour core/base_agent.py — Fixes #5 (RAG dedup) et #7 (CoT stripping)."""
import re
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from core.base_agent import BaseAgent


# ─── Tests CoT Stripping (Fix #7) ───

class TestStripCot:
    """Vérifie que _strip_cot retire les blocs <think> et les lignes de raisonnement."""

    def test_think_block_removed(self):
        """Les blocs <think>...</think> deepseek-r1 sont retirés."""
        text = "<think>Je réfléchis au problème...</think>Voici la réponse finale."
        result = BaseAgent._strip_cot(text)
        assert "réfléchis" not in result
        assert "Voici la réponse finale." in result

    def test_think_block_multiline(self):
        """Les blocs <think> multilignes sont retirés."""
        text = (
            "<think>\nÉtape 1: analyser\nÉtape 2: résoudre\n</think>\n"
            "Le résultat est 42."
        )
        result = BaseAgent._strip_cot(text)
        assert "Étape 1" not in result
        assert "Le résultat est 42." in result

    def test_cot_prefix_we_need(self):
        """Les lignes commençant par 'We need...' sont retirées."""
        text = "We need to analyze the code first.\nVoici le code corrigé."
        result = BaseAgent._strip_cot(text)
        assert "We need" not in result
        assert "Voici le code corrigé." in result

    def test_cot_prefix_let_me(self):
        """Les lignes commençant par 'Let me...' sont retirées."""
        text = "Let me think about this.\nLa réponse est 42."
        result = BaseAgent._strip_cot(text)
        assert "Let me" not in result
        assert "La réponse est 42." in result

    def test_cot_prefix_i_will(self):
        """Les lignes commençant par 'I will...' sont retirées."""
        text = "I will analyze the code.\ndef hello():\n    return 42"
        result = BaseAgent._strip_cot(text)
        assert "I will" not in result
        assert "def hello():" in result

    def test_cot_prefix_heres(self):
        """Les lignes commençant par 'Here's...' sont retirées."""
        text = "Here's my analysis:\ndef fix(): pass"
        result = BaseAgent._strip_cot(text)
        assert "Here's" not in result
        assert "def fix(): pass" in result

    def test_clean_text_unchanged(self):
        """Un texte propre (sans CoT) n'est pas modifié."""
        text = "def hello():\n    return 42\n\nprint(hello())"
        result = BaseAgent._strip_cot(text)
        assert result == text

    def test_only_think_block_returns_original(self):
        """Si le texte ne contient QUE un bloc <think>, on retourne l'original."""
        text = "<think>Tout est dedans</think>"
        result = BaseAgent._strip_cot(text)
        # Ne doit pas retourner une chaîne vide
        assert len(result) > 0

    def test_empty_lines_between_cot_and_content(self):
        """Les lignes vides entre le CoT et le contenu sont gérées."""
        text = "I should analyze this.\n\n\nVoici la solution."
        result = BaseAgent._strip_cot(text)
        assert "Voici la solution." in result

    def test_multiple_cot_lines_stripped(self):
        """Plusieurs lignes CoT consécutives sont retirées."""
        text = (
            "Let me think about this.\n"
            "I need to consider the edge cases.\n"
            "def solve():\n"
            "    return 42"
        )
        result = BaseAgent._strip_cot(text)
        assert "Let me" not in result
        assert "I need" not in result
        assert "def solve():" in result

    def test_cot_patterns_is_compiled_regex(self):
        """Le pattern CoT est bien un regex compilé."""
        assert isinstance(BaseAgent._COT_PATTERNS, re.Pattern)

    def test_think_only_extracts_conclusion(self):
        """Si le texte n'est QU'un bloc <think>, extrait les dernières lignes."""
        text = (
            "<think>Je dois analyser ce fichier.\n"
            "Il contient des vulnérabilités potentielles.\n"
            "1. Injection SQL possible ligne 42\n"
            "2. XSS dans le template\n"
            "Conclusion : 2 vulnérabilités détectées.</think>"
        )
        result = BaseAgent._strip_cot(text)
        assert "<think>" not in result
        assert "vulnérabilités" in result

    def test_think_only_returns_last_5_lines(self):
        """Le fallback think extrait max 5 lignes."""
        lines = [f"Étape {i}" for i in range(20)]
        text = f"<think>\n" + "\n".join(lines) + "\n</think>"
        result = BaseAgent._strip_cot(text)
        assert "<think>" not in result
        result_lines = [l for l in result.split('\n') if l.strip()]
        assert len(result_lines) <= 5

    def test_think_empty_content_returns_original(self):
        """Un bloc <think> vide retourne le texte original."""
        text = "<think></think>"
        result = BaseAgent._strip_cot(text)
        # Le contenu du think est vide, pas de meaningful lines → retourne original
        assert result == text


# ─── Tests RAG Dedup (Fix #5) ───

class TestRememberDedup:
    """Vérifie la déduplication par distance vectorielle dans remember()."""

    def _make_agent(self):
        """Crée un agent mock avec mémoire activée."""
        agent = BaseAgent.__new__(BaseAgent)
        agent.name = "test_agent"
        agent.has_memory = True
        agent.logger = MagicMock()
        agent.memory_manager = MagicMock()
        return agent

    def test_exact_substring_deduplicated(self):
        """Un texte identique (substring) n'est pas ré-enregistré."""
        agent = self._make_agent()
        agent.memory_manager.query_with_metadata.return_value = {
            "documents": [["Texte existant identique"]],
            "distances": [[0.0]],
        }
        agent.remember("Texte existant identique")
        agent.memory_manager.add_documents.assert_not_called()

    def test_near_duplicate_by_distance(self):
        """Un texte très similaire (distance < 0.15) n'est pas ré-enregistré."""
        agent = self._make_agent()
        agent.memory_manager.query_with_metadata.return_value = {
            "documents": [["Synthèse YouTube IA - tendances 2026"]],
            "distances": [[0.08]],  # Distance < 0.15
        }
        agent.remember("Synthèse YouTube IA - tendances 2026 récentes")
        agent.memory_manager.add_documents.assert_not_called()

    def test_different_text_saved(self):
        """Un texte suffisamment différent (distance > 0.15) EST enregistré."""
        agent = self._make_agent()
        agent.memory_manager.query_with_metadata.return_value = {
            "documents": [["Audit sécurité du routeur"]],
            "distances": [[0.85]],  # Distance > 0.15
        }
        agent.remember("Synthèse YouTube IA tendances")
        agent.memory_manager.add_documents.assert_called_once()

    def test_empty_memory_saves(self):
        """Si la mémoire est vide, le texte est enregistré."""
        agent = self._make_agent()
        agent.memory_manager.query_with_metadata.return_value = {
            "documents": [[]],
            "distances": [[]],
        }
        agent.remember("Premier souvenir")
        agent.memory_manager.add_documents.assert_called_once()

    def test_no_memory_no_crash(self):
        """Si has_memory=False, remember() ne crash pas."""
        agent = self._make_agent()
        agent.has_memory = False
        agent.remember("test")  # Ne doit pas crash
        agent.memory_manager.query_with_metadata.assert_not_called()

    def test_dedup_threshold_value(self):
        """Le seuil de déduplication est bien 0.15."""
        assert BaseAgent._DEDUP_DISTANCE_THRESHOLD == 0.15

    def test_exception_in_query_does_not_crash(self):
        """Une erreur ChromaDB ne fait pas crasher remember()."""
        agent = self._make_agent()
        agent.memory_manager.query_with_metadata.side_effect = Exception("ChromaDB error")
        agent.remember("test text")  # Ne doit pas lever d'exception
        agent.memory_manager.add_documents.assert_not_called()
