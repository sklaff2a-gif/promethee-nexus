"""Tests V15.7 — Amnesie ciblee contextuelle (remplace V15.5).

Diagnostic 24/04 11:36 : V15.5 (hierarchie epistemologique) avait essaye
d'etiqueter les souvenirs comme "potentiellement obsoletes" mais le LLM 9B
continuait de prefferer un audit narratif complet aux chunks AST bruts du
RAG fresh. Le "biais de plausibilite narrative" l'emporte sur l'injonction.

V15.7 = chirurgie radicale : si RAG fresh present dans prompt, SKIP TOTAL
du recall collective_wisdom. Pas de souvenirs concurrents = pas de
tentation = LLM focalise sur le code fresh exclusivement.

Tests via inspection source (test statique). L'integration runtime est
validee par observation d'un vrai cours ecole (tir forcé CODE_REVIEW).
"""
import inspect

from core import base_agent


class TestV15_7AmnesieCiblee:
    """Le patch V15.7 doit etre present et bien formule."""

    def test_v15_7_marker_in_source(self):
        """Le commentaire V15.7 doit etre inscrit pour traceabilite."""
        src = inspect.getsource(base_agent)
        assert "V15.7" in src, "Marqueur de version absent"

    def test_skip_message_logged(self):
        """Quand le skip se produit, un log_thought informe pour observabilite."""
        src = inspect.getsource(base_agent)
        assert "RAG fresh detecte" in src
        assert "amnesie" in src.lower() or "amnésie" in src.lower()

    def test_recall_inside_else_branch(self):
        """Le recall collective_wisdom doit etre dans la branche else (pas
        de RAG fresh) — sinon V15.7 ne sert a rien."""
        src = inspect.getsource(base_agent)
        # Le pattern attendu : "else:" suivi (a quelques lignes) du recall
        idx_flag = src.find("_has_fresh_rag")
        idx_recall = src.find('self.recall(prompt, collection="collective_wisdom"')
        assert idx_flag > 0, "_has_fresh_rag non trouve"
        assert idx_recall > 0, "recall collective_wisdom absent"
        # Le recall doit etre APRES la definition du flag
        assert idx_recall > idx_flag, (
            "Le recall doit etre conditionnel sur _has_fresh_rag (apres son test)"
        )

    def test_both_triggers_in_flag(self):
        """Le flag doit OR-er les 2 triggers (V15.4 injection + V15.2 chat)."""
        src = inspect.getsource(base_agent)
        block_start = src.find("_has_fresh_rag = (")
        assert block_start > 0
        block = src[block_start:block_start + 300]
        assert "INJECTION DE CONTEXTE STRICTE" in block
        assert "CODE REEL" in block
        assert " or " in block

    def test_no_souvenirs_block_if_skip(self):
        """Si on skip recall, mem1 n'existe pas, donc context_memory reste vide.
        Le bloc [SOUVENIRS]: ne doit PAS etre construit dans la branche
        skip — sinon il referencerait une variable non definie."""
        src = inspect.getsource(base_agent)
        # On verifie qu'au moins le flow est : skip -> log -> rien construit
        # vs branche else -> recall -> if mem1 -> [SOUVENIRS]
        # Indices : "[SOUVENIRS]:" doit apparaitre APRES "else:" du flag
        idx_flag = src.find("_has_fresh_rag = (")
        idx_else = src.find("else:", idx_flag)
        idx_souvenirs = src.find("[SOUVENIRS]:", idx_flag)
        assert idx_else > 0, "branche else manquante"
        assert idx_souvenirs > 0, "le bloc [SOUVENIRS] doit exister pour le cas non-RAG"
        assert idx_souvenirs > idx_else, (
            "[SOUVENIRS] doit etre construit dans la branche else (pas de RAG fresh)"
        )


class TestV15_7Integration:
    """Verifications structurelles complementaires."""

    def test_legacy_v15_5_hierarchie_phrase_removed(self):
        """V15.5 'HIERARCHIE EPISTEMOLOGIQUE' n'est plus necessaire avec V15.7
        (skip total des souvenirs = pas besoin de hierarchiser). On accepte
        soit l'absence totale soit une mention historique en commentaire."""
        src = inspect.getsource(base_agent)
        # Pas de bloc [HIERARCHIE EPISTEMOLOGIQUE] ACTIF dans le code execute.
        # Si c'est juste dans un commentaire historique, OK.
        active_lines = [
            line for line in src.split("\n")
            if "HIERARCHIE EPISTEMOLOGIQUE" in line and not line.strip().startswith("#")
        ]
        assert active_lines == [], (
            "V15.7 ne doit pas avoir de phrase HIERARCHIE EPISTEMOLOGIQUE active "
            "(le skip total est plus radical et plus efficace)"
        )

    def test_v15_5_replaced_not_just_disabled(self):
        """Le commentaire d'historique doit mentionner V15.5 -> V15.7."""
        src = inspect.getsource(base_agent)
        assert "V15.5" in src, "L'historique de V15.5 doit etre conserve en commentaire"
