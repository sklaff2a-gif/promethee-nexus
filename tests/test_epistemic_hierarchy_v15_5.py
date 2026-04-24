"""Tests V15.5 — Hierarchie epistemologique RAG > Souvenirs.

Diagnostic 24/04 07:55 : l'agent security a prefere un vieux souvenir
(audit reasoning_protocol.py) au RAG V15.3 frais (prefrontal.py). Faille
epistemologique. V15.5 ajoute une phrase d'autorite qui force la priorite
du RAG fresh sur les souvenirs collectifs dans base_agent.generate_content.

On teste ici le source code directement pour verifier que la strategie
est inscrite (test statique). L'integration runtime sera validee par
observation d'un vrai cours ecole (tir forcé CODE_REVIEW prefrontal.py).
"""
import inspect
import pytest

from core import base_agent


class TestEpistemicHierarchyPresent:
    """Le patch V15.5 doit etre present dans base_agent.py."""

    def test_hierarchie_epistemologique_label_in_source(self):
        """La balise [HIERARCHIE EPISTEMOLOGIQUE] doit etre injectee."""
        src = inspect.getsource(base_agent)
        assert "HIERARCHIE EPISTEMOLOGIQUE" in src

    def test_priorite_absolue_affirmation(self):
        """L'autorite explicite du RAG sur les souvenirs est declaree."""
        src = inspect.getsource(base_agent)
        assert "PRIORITE ABSOLUE" in src

    def test_souvenirs_potentiellement_obsoletes_label(self):
        """Les souvenirs sont reframer comme potentiellement obsoletes."""
        src = inspect.getsource(base_agent)
        assert "SOUVENIRS POTENTIELLEMENT OBSOLETES" in src

    def test_injection_contexte_stricte_detection(self):
        """Le trigger est bien le marqueur V15.4 [INJECTION DE CONTEXTE STRICTE]."""
        src = inspect.getsource(base_agent)
        # Le detecteur cherche ce token dans prompt
        assert '"[INJECTION DE CONTEXTE STRICTE]" in' in src

    def test_code_reel_detection(self):
        """L'autre trigger est [CODE REEL — VERIFIE AVANT DE REPONDRE]."""
        src = inspect.getsource(base_agent)
        # Forme utilisee dans chat_engine.py / base_agent.py
        assert '"[CODE REEL' in src

    def test_fallback_legacy_souvenirs_block(self):
        """Si pas de RAG fresh, l'ancien format [SOUVENIRS]: reste utilise."""
        src = inspect.getsource(base_agent)
        assert '"[SOUVENIRS]:' in src or "[SOUVENIRS]:\\n" in src


class TestContextMemoryStructure:
    """Test indirect de l'assemblage : on verifie les elements assembles."""

    def test_hierarchie_only_when_rag_present(self):
        """La phrase V15.5 n'apparait QUE quand le prompt inclut du RAG."""
        # Inspection du code : la phrase est dans le bloc if _has_fresh_rag
        src = inspect.getsource(base_agent)
        # Approche textuelle : l'affirmation doit venir apres un if sur
        # _has_fresh_rag ou variable equivalente
        idx_flag = src.find("_has_fresh_rag")
        idx_priorite = src.find("PRIORITE ABSOLUE")
        assert idx_flag > 0, "flag _has_fresh_rag non trouve"
        assert idx_priorite > idx_flag, (
            "La phrase PRIORITE ABSOLUE doit apparaitre APRES la definition du flag"
        )

    def test_both_triggers_are_ored(self):
        """Le flag doit OR-er les 2 triggers (injection stricte + code reel)."""
        src = inspect.getsource(base_agent)
        # Cherche le pattern "or" entre les 2 conditions
        block_start = src.find("_has_fresh_rag = (")
        assert block_start > 0
        block = src[block_start:block_start + 300]
        assert "INJECTION DE CONTEXTE STRICTE" in block
        assert "CODE REEL" in block
        assert " or " in block
