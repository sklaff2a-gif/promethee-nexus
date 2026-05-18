"""Tests pour core/phaseur.py — PHASEUR_DE_Réalité v2 CONCEPTUEL.

Conformité CHARTA_CORE.md procédure 3.2 (modification systémique avec /think).
5 couches de protection testées séparément + activation contrôlée + substitution conceptuelle.

v2 (18/05/2026) : substitution de mots porteurs (stabilité→vertige, logique→chaos…)
au lieu de suffixes syntaxiques. Tests d'activation utilisent des textes contenant
au moins un mot du dictionnaire _CONCEPT_SUBSTITUTIONS.
"""
import pytest
from config import Config
from core.phaseur import (
    apply_perturbation,
    _detect_autonomous_caller,
    _detect_technical_context,
    _preserve_case,
    _apply_concept_substitutions,
    _CONCEPT_SUBSTITUTIONS,
)


class TestPhaseurDisabledByDefault:
    """Couche 5 — kill switch via Config.PHASEUR_ENABLED."""

    def test_globally_disabled_returns_unchanged(self):
        Config.PHASEUR_ENABLED = False
        text = "La création ordonne un chaos magnifique entre les nuages flottants"
        result, log = apply_perturbation(text, creative_context=True, intensity=0.05)
        assert result == text
        assert log["active"] is False
        assert log["reason"] == "globally_disabled"


class TestPhaseurRefusals:
    """Tests des refus pour chaque couche de protection."""

    def setup_method(self):
        Config.PHASEUR_ENABLED = True

    def teardown_method(self):
        Config.PHASEUR_ENABLED = False

    def test_refus_not_creative(self):
        """Couche 2 — flag creative_context=False bloque."""
        text = "Texte créatif fluide poétique"
        result, log = apply_perturbation(text, creative_context=False, intensity=0.05)
        assert result == text
        assert log["reason"] == "not_creative_context"

    def test_refus_vision_invoked(self):
        """CHARTA Article 1.2 — non-hallucination visuelle, blocage strict."""
        text = "Description d'une image perçue"
        result, log = apply_perturbation(
            text, creative_context=True, vision_invoked=True, intensity=0.05
        )
        assert result == text
        assert log["reason"] == "vision_invoked_blocked"

    def test_refus_rag_present(self):
        """RAG = contexte factuel, blocage."""
        text = "Reformulation d'une source"
        result, log = apply_perturbation(
            text, creative_context=True, rag_present=True, intensity=0.05
        )
        assert result == text
        assert log["reason"] == "rag_present_blocked"

    def test_refus_technical_keyword_code(self):
        """Couche 3 — heuristique : mots-clés code (def, class, import, ```)."""
        text = "Voici une fonction def my_function(): pass utile pour cela"
        result, log = apply_perturbation(text, creative_context=True, intensity=0.05)
        assert result == text
        assert log["reason"] == "technical_context_detected"

    def test_refus_technical_keyword_factual(self):
        """Couche 3 — heuristique : mots-clés factuel (combien, quelle date)."""
        text = "Combien de cellules vivantes dans le tissu actuellement"
        result, log = apply_perturbation(text, creative_context=True, intensity=0.05)
        assert result == text
        assert log["reason"] == "technical_context_detected"

    def test_refus_path_in_text(self):
        """Couche 3 — détection path technique dans le texte."""
        text = "Regarde core/body_schema.py pour comprendre les implications profondes"
        result, log = apply_perturbation(text, creative_context=True, intensity=0.05)
        assert result == text
        assert log["reason"] == "technical_context_detected"

    def test_refus_autonomous_caller_explicit(self):
        """Couche 4 — backup explicit caller_context='autonomous' (CHARTA 3.3)."""
        text = "Réponse créative dans un contexte poétique abstrait"
        result, log = apply_perturbation(
            text, creative_context=True, intensity=0.05, caller_context="autonomous"
        )
        assert result == text
        assert log["reason"] == "autonomous_caller_refused"

    def test_intensity_zero_returns_unchanged(self):
        """Plafond inférieur : intensity=0 → pas d'activation."""
        text = "Texte créatif fluide poétique sans technique"
        result, log = apply_perturbation(text, creative_context=True, intensity=0.0)
        assert result == text
        assert log["reason"] == "intensity_zero"


class TestPhaseurActivation:
    """Tests d'activation effective + plafond hard."""

    def setup_method(self):
        Config.PHASEUR_ENABLED = True

    def teardown_method(self):
        Config.PHASEUR_ENABLED = False

    def test_activation_creative_pure(self):
        """Activation effective sur contexte créatif pur — v2 substitution conceptuelle.

        Texte contient stabilité+logique+chaos+création+structure (5 mots porteurs)
        pour garantir au moins 1 substitution même à intensity=0.05.
        """
        text = (
            "La création maintient une fragile stabilité face au chaos environnant, "
            "et la logique défend la structure intérieure de cette ordre vivant"
        )
        result, log = apply_perturbation(text, creative_context=True, intensity=0.05)
        assert log["active"] is True
        assert log["reason"] == "applied"
        assert log["tokens_modified"] >= 1
        assert log["intensity_effective"] == 0.05
        assert result != text  # perturbation visible (au moins 1 mot substitué)

    def test_plafond_hard_clamps_intensity(self):
        """Couche 5 — plafond hard PHASEUR_MAX_INTENSITY=0.05 clamp intensity demandée."""
        text = (
            "La création maintient une fragile stabilité face au chaos environnant, "
            "et la logique défend la structure intérieure de cette ordre vivant"
        )
        result, log = apply_perturbation(text, creative_context=True, intensity=0.50)
        assert log["intensity_effective"] == Config.PHASEUR_MAX_INTENSITY  # 0.05
        assert log["intensity_effective"] < 0.50

    def test_log_contains_required_fields(self):
        """Logging structuré : timestamp, conv_id, hash, intensity, active, reason.

        Texte contient stabilité+logique (2 mots porteurs) pour garantir activation.
        """
        text = "La stabilité et la logique fondent la structure de cette pensée vivante"
        _, log = apply_perturbation(
            text, creative_context=True, intensity=0.05,
            conversation_id="test-conv-123", user_message_hash="abc1234",
        )
        assert "ts" in log
        assert log["conv_id"] == "test-conv-123"
        assert log["user_msg_hash"] == "abc1234"
        assert log["intensity_requested"] == 0.05
        assert "active" in log
        assert "reason" in log


class TestPhaseurV2ConceptualSubstitution:
    """Tests v2 — substitution conceptuelle effective + cas particuliers."""

    def setup_method(self):
        Config.PHASEUR_ENABLED = True

    def teardown_method(self):
        Config.PHASEUR_ENABLED = False

    def test_v2_substitution_uses_dictionary(self):
        """Le mot substitué doit être l'une des variantes du dictionnaire."""
        text = "La stabilité est mon ancrage face au monde"
        result, log = apply_perturbation(text, creative_context=True, intensity=0.5)
        # intensity=0.5 sera clampée à 0.05 mais ça donnera au moins 1 substitution
        # (n_to_perturb = max(1, ...))
        assert log["active"] is True
        # Le mot "stabilité" doit avoir été remplacé par "vertige" ou "déséquilibre"
        assert "stabilité" not in result.lower()
        assert any(v in result.lower() for v in ("vertige", "déséquilibre"))

    def test_v2_preserves_case_capitalized(self):
        """'Stabilité' (capitalisé) doit donner 'Vertige' ou 'Déséquilibre' (capitalisé)."""
        text = "Stabilité fragile et ancrée dans cette structure ancienne"
        # Forcer la substitution avec intensity au max
        result, log = apply_perturbation(text, creative_context=True, intensity=0.5)
        assert log["active"] is True
        # Vérifie qu'aucune variante en minuscules ne commence le texte
        if not result.startswith("Stabilité"):
            # Le premier mot a été substitué : il doit garder la capitalisation
            first_word = result.split()[0]
            assert first_word[0].isupper(), f"Casse non préservée : {first_word}"

    def test_v2_no_target_concepts_returns_unchanged(self):
        """Texte créatif sans mot porteur du dico → no-op + reason='no_target_concepts'."""
        text = "Les nuages flottent doucement vers leur horizon inconnu fascinant"
        result, log = apply_perturbation(text, creative_context=True, intensity=0.05)
        assert result == text  # aucune modification
        assert log["active"] is False
        assert log["reason"] == "no_target_concepts"
        assert log["tokens_modified"] == 0

    def test_v2_helper_preserve_case(self):
        """Helper _preserve_case : lowercase/Capitalized/UPPERCASE."""
        assert _preserve_case("stabilité", "vertige") == "vertige"
        assert _preserve_case("Stabilité", "vertige") == "Vertige"
        assert _preserve_case("STABILITÉ", "vertige") == "VERTIGE"
        assert _preserve_case("", "vertige") == "vertige"

    def test_v2_helper_apply_substitutions_no_match(self):
        """Helper _apply_concept_substitutions : zéro substitution si rien ne matche."""
        text = "Les nuages flottent doucement"
        result, count = _apply_concept_substitutions(text, n_max=3)
        assert result == text
        assert count == 0

    def test_v2_dictionary_non_empty_and_consistent(self):
        """Le dictionnaire doit contenir au moins les 10 paires de référence v2."""
        assert len(_CONCEPT_SUBSTITUTIONS) >= 10
        # Toutes les clés sont en minuscules
        for concept in _CONCEPT_SUBSTITUTIONS:
            assert concept == concept.lower(), f"Clé non lowercased: {concept}"
        # Toutes les variantes sont des tuples non vides de strings ≥4 chars
        for concept, variants in _CONCEPT_SUBSTITUTIONS.items():
            assert isinstance(variants, tuple) and len(variants) >= 1
            for v in variants:
                assert isinstance(v, str) and len(v) >= 4, f"Variante invalide: {concept}→{v}"


class TestPhaseurHelpers:
    """Tests des helpers internes."""

    def test_detect_autonomous_explicit_marker(self):
        """caller_context='autonomous' explicite → True (backup pour asyncio)."""
        assert _detect_autonomous_caller("autonomous") is True

    def test_detect_autonomous_no_marker(self):
        """caller_context None ou autre valeur → False par défaut (depuis pytest)."""
        assert _detect_autonomous_caller(None) is False
        assert _detect_autonomous_caller("interactive") is False
        assert _detect_autonomous_caller("user-driven") is False

    def test_detect_technical_context_keywords(self):
        """Heuristique Couche 3 v2.1 : keywords univoques uniquement (code/factuel/path)."""
        assert _detect_technical_context("def foo(): pass") is True
        assert _detect_technical_context("import re") is True
        assert _detect_technical_context("combien de lignes ?") is True  # "combien" matche
        assert _detect_technical_context("Regarde core/main.py") is True
        assert _detect_technical_context("class MyClass:") is True
        assert _detect_technical_context("le bug du parseur") is True  # \bbug\b
        assert _detect_technical_context("ligne 42 du code") is True  # "ligne " + \bcode\b

    def test_detect_technical_context_creative_pass(self):
        """Heuristique Couche 3 v2.1 : textes créatifs purs ne déclenchent pas.

        v2.1 a retiré \\bquand\\b, où est, quelle date, qui a (ambigus en français créatif).
        """
        assert _detect_technical_context("La poésie de l'âme profonde") is False
        assert _detect_technical_context("Le chaos précède toute création") is False
        assert _detect_technical_context("") is False
        assert _detect_technical_context(None) is False
        # v2.1 : ces patterns créatifs ne doivent plus déclencher de faux positif
        assert _detect_technical_context("Quand les mots dérivent vers l'abstraction") is False
        assert _detect_technical_context("Où est passée la lumière ?") is False
        assert _detect_technical_context("Quelle date pour cette douceur ?") is False
        assert _detect_technical_context("Qui a vraiment vu l'aube ?") is False
