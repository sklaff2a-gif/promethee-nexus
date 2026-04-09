"""Tests du protocole de raisonnement."""

import pytest
import os
from core.reasoning_protocol import (
    verify_code_review, verify_research, verify_workshop,
    build_retry_prompt, _extract_real_names, _extract_cited_names,
    record_failure, get_failure_count, get_failure_stats,
)


class TestVerifyCodeReview:

    def test_real_names_detected(self):
        """Les fonctions reelles d'un fichier Python sont extraites."""
        # Utiliser un fichier reel du projet
        filepath = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "core", "mailbox.py")
        if not os.path.exists(filepath):
            pytest.skip("mailbox.py manquant")
        names = _extract_real_names(filepath)
        assert "write_letter" in names
        assert "read_letters" in names

    def test_cited_names_extracted(self):
        """Les noms cites dans un audit sont extraits."""
        text = """
        La fonction `write_letter()` a un bug. La classe `Mailbox` est bien.
        def read_letters est aussi ok.
        """
        names = _extract_cited_names(text)
        assert "write_letter" in names
        assert "Mailbox" in names

    def test_hallucination_detected(self):
        """Un audit avec des fonctions inventees est rejete."""
        result = """
        La fonction `load_data_file()` a un risque de path traversal.
        La fonction `save_temp_file()` devrait etre securisee.
        `handle_error()` manque de logging.
        """
        valid, reason = verify_code_review(result, "core/mailbox.py")
        assert not valid
        assert "HALLUCINATION" in reason

    def test_valid_review_accepted(self):
        """Un audit avec des fonctions reelles est accepte."""
        result = """
        La methode `write_letter()` de la classe `Mailbox` fonctionne bien.
        `read_letters()` retourne les lettres correctement.
        `_save()` utilise une ecriture atomique.
        """
        valid, reason = verify_code_review(result, "core/mailbox.py")
        assert valid

    def test_empty_result_rejected(self):
        valid, reason = verify_code_review("", "core/mailbox.py")
        assert not valid


class TestVerifyResearch:

    def test_valid_research(self):
        text = "Les reseaux neuronaux convolutifs sont utilises pour le traitement d'images. " * 5
        valid, _ = verify_research(text, "reseaux neuronaux")
        assert valid

    def test_too_short(self):
        valid, reason = verify_research("Court.", "sujet")
        assert not valid
        assert "trop courte" in reason

    def test_off_topic(self):
        text = "Les chats sont des animaux domestiques tres populaires dans le monde entier. " * 5
        valid, reason = verify_research(text, "reseaux neuronaux convolutifs")
        assert not valid
        assert "hors sujet" in reason


class TestVerifyWorkshop:

    def test_with_code(self):
        text = "def hello():\n    return 'world'\n"
        valid, _ = verify_workshop(text, "hello world")
        assert valid

    def test_without_code(self):
        text = "La programmation est importante pour les systemes autonomes."
        valid, reason = verify_workshop(text, "implementation")
        assert not valid
        assert "Aucun code" in reason


class TestRetryPrompt:

    def test_retry_contains_feedback(self):
        prompt = build_retry_prompt("Audite ce fichier", "Fonctions inventees")
        assert "REJETE" in prompt
        assert "Fonctions inventees" in prompt
        assert "Audite ce fichier" in prompt


class TestFailureMemory:

    def test_record_and_count(self, tmp_path):
        import core.reasoning_protocol as rp
        rp.FAILURES_FILE = str(tmp_path / "failures.json")
        rp._failures_cache = []
        rp._loaded = True

        record_failure("CODE_REVIEW", "test.py", "hallucination")
        record_failure("CODE_REVIEW", "test.py", "hors sujet")
        assert get_failure_count("CODE_REVIEW") == 2
        assert get_failure_count("RESEARCH") == 0

    def test_stats(self, tmp_path):
        import core.reasoning_protocol as rp
        rp.FAILURES_FILE = str(tmp_path / "failures.json")
        rp._failures_cache = []
        rp._loaded = True

        record_failure("CODE_REVIEW", "test.py", "test")
        stats = get_failure_stats()
        assert stats["today"] == 1
        assert "CODE_REVIEW" in stats["by_type"]
