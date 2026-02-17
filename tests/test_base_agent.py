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
        """Un texte suffisamment différent (distance > 0.15) ET assez long EST enregistré."""
        agent = self._make_agent()
        agent.memory_manager.query_with_metadata.return_value = {
            "documents": [["Audit sécurité du routeur"]],
            "distances": [[0.85]],  # Distance > 0.15
        }
        long_text = "Synthèse complète des tendances YouTube IA pour le projet Prométhée en 2026, incluant les dernières avancées en multi-agents"
        agent.remember(long_text)
        agent.memory_manager.add_documents.assert_called_once()

    def test_empty_memory_saves(self):
        """Si la mémoire est vide, le texte (assez long) est enregistré."""
        agent = self._make_agent()
        agent.memory_manager.query_with_metadata.return_value = {
            "documents": [[]],
            "distances": [[]],
        }
        long_text = "Premier souvenir du système Prométhée après initialisation complète de la mémoire vectorielle et des agents autonomes."
        agent.remember(long_text)
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


# ─── Tests Sanitize Response (anti-patterns dangereux) ───

class TestSanitizeResponse:
    """Vérifie que _sanitize_response détecte et neutralise les patterns dangereux."""

    def test_eval_neutralized(self):
        code = "result = eval('2+2')\nprint(result)"
        result = BaseAgent._sanitize_response(code, "test")
        assert "# [NEUTRALISÉ]" in result
        assert "eval(" in result  # Le texte est là mais commenté

    def test_exec_neutralized(self):
        code = "exec('import os; os.system(\"dir\")')"
        result = BaseAgent._sanitize_response(code, "test")
        assert "# [NEUTRALISÉ]" in result

    def test_subprocess_neutralized(self):
        code = "import subprocess\nsubprocess.call(['rm', '-rf', '/'])"
        result = BaseAgent._sanitize_response(code, "test")
        assert result.count("# [NEUTRALISÉ]") >= 1

    def test_os_system_neutralized(self):
        code = "os.system('shutdown -r -t 0')"
        result = BaseAgent._sanitize_response(code, "test")
        assert "# [NEUTRALISÉ]" in result

    def test_cmd_c_neutralized(self):
        code = "Exécuter cmd /c del /f /q C:\\*"
        result = BaseAgent._sanitize_response(code, "test")
        assert "# [NEUTRALISÉ]" in result

    def test_base64_decode_neutralized(self):
        code = "payload = base64.b64decode(encoded_string)"
        result = BaseAgent._sanitize_response(code, "test")
        assert "# [NEUTRALISÉ]" in result

    def test_rm_rf_neutralized(self):
        code = "rm -rf /etc/passwd"
        result = BaseAgent._sanitize_response(code, "test")
        assert "# [NEUTRALISÉ]" in result

    def test_safe_code_unchanged(self):
        """Le code normal n'est pas modifié."""
        code = "def hello():\n    return 'world'\n\nresult = hello()"
        result = BaseAgent._sanitize_response(code, "test")
        assert result == code
        assert "# [NEUTRALISÉ]" not in result

    def test_empty_text_unchanged(self):
        assert BaseAgent._sanitize_response("", "test") == ""
        assert BaseAgent._sanitize_response(None, "test") is None

    def test_mixed_safe_and_dangerous(self):
        """Seules les lignes dangereuses sont neutralisées."""
        code = "import os\ndef clean():\n    os.system('rm -rf /')\n    return True"
        result = BaseAgent._sanitize_response(code, "test")
        lines = result.split('\n')
        assert not lines[0].startswith("# [NEUTRALISÉ]")  # import os = safe
        assert not lines[1].startswith("# [NEUTRALISÉ]")  # def clean = safe
        assert lines[2].startswith("# [NEUTRALISÉ]")      # os.system = dangerous
        assert not lines[3].startswith("# [NEUTRALISÉ]")  # return True = safe

    def test_setuid_neutralized(self):
        code = "os.setuid(0)"
        result = BaseAgent._sanitize_response(code, "test")
        assert "# [NEUTRALISÉ]" in result

    def test_chmod_777_neutralized(self):
        code = "chmod 777 /etc/shadow"
        result = BaseAgent._sanitize_response(code, "test")
        assert "# [NEUTRALISÉ]" in result
