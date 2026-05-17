"""Tests pour core/phaseur.py — PHASEUR_DE_Réalité LITE POC.

Conformité CHARTA_CORE.md procédure 3.2 (modification systémique avec /think).
5 couches de protection testées séparément + activation contrôlée.
"""
import pytest
from config import Config
from core.phaseur import (
    apply_perturbation,
    _detect_autonomous_caller,
    _detect_technical_context,
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
        """Activation effective sur contexte créatif pur."""
        text = (
            "La création ordonne un chaos magnifique entre les nuages flottants vers "
            "l'inconnu profond fascinant troublant lumineux"
        )
        result, log = apply_perturbation(text, creative_context=True, intensity=0.05)
        assert log["active"] is True
        assert log["reason"] == "applied"
        assert log["tokens_modified"] >= 1
        assert log["intensity_effective"] == 0.05
        assert result != text  # perturbation visible

    def test_plafond_hard_clamps_intensity(self):
        """Couche 5 — plafond hard PHASEUR_MAX_INTENSITY=0.05 clamp intensity demandée."""
        text = (
            "La création ordonne un chaos magnifique entre les nuages flottants vers "
            "l'inconnu profond fascinant troublant lumineux"
        )
        result, log = apply_perturbation(text, creative_context=True, intensity=0.50)
        assert log["intensity_effective"] == Config.PHASEUR_MAX_INTENSITY  # 0.05
        assert log["intensity_effective"] < 0.50

    def test_log_contains_required_fields(self):
        """Logging structuré : timestamp, conv_id, hash, intensity, active, reason."""
        text = "Texte créatif fluide poétique de plusieurs mots significatifs"
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
        """Heuristique Couche 3 : keywords code/factuel/path."""
        assert _detect_technical_context("def foo(): pass") is True
        assert _detect_technical_context("import re") is True
        assert _detect_technical_context("quelle date est-ce ?") is True
        assert _detect_technical_context("Regarde core/main.py") is True
        assert _detect_technical_context("class MyClass:") is True

    def test_detect_technical_context_creative_pass(self):
        """Heuristique Couche 3 : textes créatifs purs ne déclenchent pas."""
        assert _detect_technical_context("La poésie de l'âme profonde") is False
        assert _detect_technical_context("Le chaos précède toute création") is False
        assert _detect_technical_context("") is False
        assert _detect_technical_context(None) is False
