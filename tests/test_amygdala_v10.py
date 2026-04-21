"""Tests V10.0 (Phase 12A - 2026-04-21) : Amygdale myopie fix.

Contexte : audit limbique (21/04 matin) a revele que _INTENT_EMOTION_MAP
hardcodait des patterns ("COUNCIL_DEBATE:success") qui ne matchaient
JAMAIS les cles reellement creees par _on_routine_complete
("council_debate_success"). Mismatch de casse + separateur.

L'amygdale etait myope a 50% sur les routines (captee via fallback
substring, demi-poids). V10.0 : generation dynamique des patterns +
dict _INTENT_SPECIAL_EVENTS pour les events upstream non-standard.

Contre-expertise Gemini : Option B (generation dynamique) validee vs
Option A (dict hardcode corrige). Plus DRY, plus futur-proof.
"""
import pytest
from core.amygdala import Amygdala, _INTENT_SPECIAL_EVENTS


@pytest.fixture
def fresh_amygdala():
    """Amygdale reinitialisee, vide de memoires."""
    Amygdala.reset_singleton()
    a = Amygdala()
    a.memories.clear()
    yield a
    Amygdala.reset_singleton()


class TestAutoPatternsMatch:
    """V10.0 : les patterns generes automatiquement matchent les cles
    reellement emises par _on_routine_complete."""

    def test_council_debate_success_at_full_weight(self, fresh_amygdala):
        """council_debate_success doit etre capte a PLEIN POIDS (pas 0.5)."""
        fresh_amygdala.condition("council_debate_success", 0.8, 0.6)
        bias = fresh_amygdala.compute_emotional_bias("COUNCIL_DEBATE")
        # valence (0.8) * arousal (0.6) = 0.48 (un seul pattern matche)
        assert bias == pytest.approx(0.48, abs=0.01), (
            f"Bias attendu ~0.48 (plein poids), obtenu {bias}. "
            "Pre-V10 : capture via fallback -> 0.24 (demi-poids)."
        )

    def test_council_debate_error_at_full_weight(self, fresh_amygdala):
        fresh_amygdala.condition("council_debate_error", -0.4, 0.5)
        bias = fresh_amygdala.compute_emotional_bias("COUNCIL_DEBATE")
        assert bias == pytest.approx(-0.2, abs=0.01)

    def test_expansion_code_success_at_full_weight(self, fresh_amygdala):
        fresh_amygdala.condition("expansion_code_success", 0.5, 0.4)
        bias = fresh_amygdala.compute_emotional_bias("EXPANSION_CODE")
        assert bias == pytest.approx(0.2, abs=0.01)

    def test_arbitrary_intent_auto_generated(self, fresh_amygdala):
        """V10.0 : meme un intent non-dans-le-dict a ses patterns generes."""
        fresh_amygdala.condition("creative_writing_success", 0.6, 0.5)
        bias = fresh_amygdala.compute_emotional_bias("CREATIVE_WRITING")
        assert bias == pytest.approx(0.3, abs=0.01)


class TestSpecialEventsPreserved:
    """V10.0 : les events upstream non-standard (HALLUCINATION_DETECTED,
    COUNCIL_END) doivent rester associes via _INTENT_SPECIAL_EVENTS."""

    def test_expansion_code_hallucination_associated(self, fresh_amygdala):
        fresh_amygdala.condition("HALLUCINATION_DETECTED", -0.6, 0.7)
        bias = fresh_amygdala.compute_emotional_bias("EXPANSION_CODE")
        # HALLUCINATION_DETECTED est dans _INTENT_SPECIAL_EVENTS pour EXPANSION_CODE
        # valence -0.6 * arousal 0.7 = -0.42
        assert bias == pytest.approx(-0.42, abs=0.01), (
            "HALLUCINATION_DETECTED doit etre associe a EXPANSION_CODE via special"
        )

    def test_evolution_run_hallucination_associated(self, fresh_amygdala):
        fresh_amygdala.condition("HALLUCINATION_DETECTED", -0.6, 0.7)
        bias = fresh_amygdala.compute_emotional_bias("EVOLUTION_RUN")
        assert bias == pytest.approx(-0.42, abs=0.01)

    def test_council_debate_council_end_associated(self, fresh_amygdala):
        fresh_amygdala.condition("COUNCIL_END", 0.3, 0.3)
        bias = fresh_amygdala.compute_emotional_bias("COUNCIL_DEBATE")
        assert bias == pytest.approx(0.09, abs=0.01)


class TestSpecialEventsDictContent:
    """V10.0 : _INTENT_SPECIAL_EVENTS ne contient plus que les events
    vraiment upstream, pas les patterns generables."""

    def test_dict_contains_only_upstream_events(self):
        # EXPANSION_CODE : HALLUCINATION_DETECTED (pavlovien bus)
        assert "HALLUCINATION_DETECTED" in _INTENT_SPECIAL_EVENTS["EXPANSION_CODE"]
        # COUNCIL_DEBATE : COUNCIL_END (event bus Council)
        assert "COUNCIL_END" in _INTENT_SPECIAL_EVENTS["COUNCIL_DEBATE"]
        # EVOLUTION_RUN : idem
        assert "HALLUCINATION_DETECTED" in _INTENT_SPECIAL_EVENTS["EVOLUTION_RUN"]

    def test_dict_no_lowercase_patterns(self):
        """Les patterns generables (xxx_success, xxx_error) ne doivent
        PAS etre dans le dict - ils sont generes dynamiquement."""
        for intent, patterns in _INTENT_SPECIAL_EVENTS.items():
            for p in patterns:
                assert not p.endswith("_success"), (
                    f"{intent}: '{p}' est generable automatiquement, "
                    "retirer du dict"
                )
                assert not p.endswith("_error"), (
                    f"{intent}: '{p}' est generable automatiquement"
                )


class TestCompositeBias:
    """V10.0 : combinaison auto_patterns + special_patterns."""

    def test_council_debate_full_mix(self, fresh_amygdala):
        """COUNCIL_DEBATE peut matcher 3 patterns : auto success + auto
        error + special COUNCIL_END."""
        fresh_amygdala.condition("council_debate_success", 0.8, 0.6)  # +0.48
        fresh_amygdala.condition("council_debate_error", -0.4, 0.5)   # -0.20
        fresh_amygdala.condition("COUNCIL_END", 0.3, 0.3)             # +0.09
        # Moyenne (0.48 + -0.20 + 0.09) / 3 = 0.123
        bias = fresh_amygdala.compute_emotional_bias("COUNCIL_DEBATE")
        assert bias == pytest.approx(0.123, abs=0.02)


class TestBackwardCompatibility:
    """V10.0 : ne casse pas les cas limites."""

    def test_no_memories_returns_zero(self, fresh_amygdala):
        bias = fresh_amygdala.compute_emotional_bias("UNKNOWN_INTENT")
        assert bias == 0.0

    def test_bias_clamped_to_range(self, fresh_amygdala):
        """Le biais reste borne a [-1.5, +1.5]."""
        # Injecter un pattern tres positif qui normalement produirait
        # 1.0 * 1.0 = 1.0, bien dans la plage
        fresh_amygdala.condition("council_debate_success", 1.0, 1.0)
        bias = fresh_amygdala.compute_emotional_bias("COUNCIL_DEBATE")
        assert -1.5 <= bias <= 1.5

    def test_fallback_substring_still_works(self, fresh_amygdala):
        """Le fallback substring capte toujours les memoires orphelines
        (ex : conditionnement manuel avec nomenclature divergente)."""
        # Memoire avec une cle qui n'est ni auto ni dans special_events
        fresh_amygdala.condition("council_debate_extra_custom_pattern", 0.5, 0.4)
        bias = fresh_amygdala.compute_emotional_bias("COUNCIL_DEBATE")
        # Capte via substring (intent_lower="council_debate" in cle) a
        # demi-poids : 0.5 * 0.4 * 0.5 = 0.1
        assert bias == pytest.approx(0.1, abs=0.01)
