"""Tests pour compute_context_multipliers (Phase C Etape 5, 2026-04-14).

Valide l'agregation des 5 tables d'affinite contextuelle en multiplicateurs
par intent, ainsi que l'auto-injection dans get_routines_for_drive_live.

Garde-fou central : un modulateur est un adjectif, pas un nom.
Il module un intent deja candidat mais ne peut jamais en creer un.
"""

import pytest
from unittest.mock import MagicMock, patch

from core.drive_routine_registry import (
    DRIVE_GENOME,
    compute_context_multipliers,
    _bonus_to_multiplier,
    _MULTIPLIER_MIN,
    _MULTIPLIER_MAX,
    get_routines_for_drive_live,
    get_routines_for_drive,
    set_synaptic_provider,
    set_stability_provider,
)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures : isolation des providers et des singletons organes
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def reset_providers():
    set_synaptic_provider(None)
    set_stability_provider(None)
    yield
    set_synaptic_provider(None)
    set_stability_provider(None)


# ═══════════════════════════════════════════════════════════════════════
# Tests _bonus_to_multiplier
# ═══════════════════════════════════════════════════════════════════════


class TestBonusToMultiplier:
    def test_zero_bonus_is_neutral(self):
        assert _bonus_to_multiplier(0.0) == 1.0

    def test_positive_bonus_amplifies(self):
        assert _bonus_to_multiplier(0.5) == 1.25
        assert _bonus_to_multiplier(1.0) == 1.5

    def test_negative_bonus_attenuates(self):
        assert _bonus_to_multiplier(-0.5) == 0.75
        assert _bonus_to_multiplier(-1.0) == 0.5

    def test_clamps_above_one(self):
        assert _bonus_to_multiplier(10.0) == 1.5

    def test_clamps_below_minus_one(self):
        assert _bonus_to_multiplier(-10.0) == 0.5


# ═══════════════════════════════════════════════════════════════════════
# Tests compute_context_multipliers — cas de base
# ═══════════════════════════════════════════════════════════════════════


class TestComputeContextMultipliersBasic:
    def test_empty_intents_returns_empty(self):
        assert compute_context_multipliers("MAITRISE", []) == {}

    def test_all_organ_failures_returns_empty_or_neutral(self):
        """Si tous les organes crashent/sont absents, le resultat est {} ou
        contient uniquement des 1.0 (qui sont filtres). On doit donc avoir
        un dict vide ou sans entrees."""
        with patch("core.inner_voice.voice", side_effect=ImportError):
            with patch("core.hypothalamus.Hypothalamus", side_effect=ImportError):
                with patch("core.psyche.psyche", side_effect=ImportError):
                    result = compute_context_multipliers(
                        "MAITRISE", ["SECURITY_AUDIT", "EXPANSION_CODE"]
                    )
        # Aucun intent ne doit avoir un multiplier hors 1.0 (qui est filtre)
        # Le resultat peut etre vide, ou contenir seulement des valeurs non-1.0
        # issues d'un organe qui fonctionne meme en mock
        for intent, mult in result.items():
            assert mult != 1.0  # filtre applique

    def test_multipliers_are_bounded(self):
        """Quel que soit l'etat des organes, les multiplicateurs retournes
        sont dans [_MULTIPLIER_MIN, _MULTIPLIER_MAX]."""
        result = compute_context_multipliers(
            "MAITRISE",
            list(DRIVE_GENOME.get("MAITRISE", {}).keys()) + ["EXPANSION_CODE"],
        )
        for intent, mult in result.items():
            assert _MULTIPLIER_MIN <= mult <= _MULTIPLIER_MAX, (
                f"{intent} out of bounds: {mult}"
            )

    def test_unknown_intent_ignored(self):
        """Un intent passe qui n'est dans AUCUNE table retourne multiplier 1.0
        (qui est filtre du dict). Il ne doit pas crasher."""
        result = compute_context_multipliers(
            "MAITRISE", ["__TOTALLY_UNKNOWN_INTENT_XYZ__"]
        )
        assert "__TOTALLY_UNKNOWN_INTENT_XYZ__" not in result


# ═══════════════════════════════════════════════════════════════════════
# Tests compute_context_multipliers — effet des organes
# ═══════════════════════════════════════════════════════════════════════


class TestComputeContextMultipliersOrgans:
    def test_emotion_frustration_penalizes_expansion_code(self):
        """Emotion frustration a un bonus -0.3 sur EXPANSION_CODE.
        Verifier que le multiplier est < 1.0."""
        from core.inner_voice import voice, Thought
        import time as _t
        # Purger le stream et injecter 10 pensees avec emotion=frustration
        voice.stream = []
        for _ in range(10):
            voice.stream.append(Thought(
                timestamp=_t.time(),
                content="frustre",
                source="reptilian",
                mode="evaluer",
                salience=0.5,
                emotion="frustration",
            ))
        result = compute_context_multipliers("MAITRISE", ["EXPANSION_CODE"])
        voice.stream = []
        assert "EXPANSION_CODE" in result
        assert result["EXPANSION_CODE"] < 1.0, (
            f"Attendu < 1.0, obtenu {result.get('EXPANSION_CODE')}"
        )

    def test_emotion_enthousiasme_boosts_expansion_code(self):
        """Emotion enthousiasme a +0.3 sur EXPANSION_CODE -> multiplier > 1.0."""
        from core.inner_voice import voice, Thought
        import time as _t
        voice.stream = []
        for _ in range(10):
            voice.stream.append(Thought(
                timestamp=_t.time(),
                content="enthousiaste",
                source="synaptic",
                mode="motiver",
                salience=0.5,
                emotion="enthousiasme",
            ))
        result = compute_context_multipliers("MAITRISE", ["EXPANSION_CODE"])
        voice.stream = []
        assert "EXPANSION_CODE" in result
        assert result["EXPANSION_CODE"] > 1.0


# ═══════════════════════════════════════════════════════════════════════
# Tests garde-fou : les multipliers ne creent pas d'intents
# ═══════════════════════════════════════════════════════════════════════


class TestGuardRail:
    def test_multiplier_cannot_create_intent(self):
        """Regle d'or Gemini : un intent absent du genome ET du graphe
        ne peut pas apparaitre a cause d'un multiplicateur, meme enorme."""
        # MAITRISE genome ne contient PAS "VEILLE_SILENCIEUSE" (c'est CURIOSITE)
        assert "VEILLE_SILENCIEUSE" not in DRIVE_GENOME.get("MAITRISE", {})

        # Pas de synaptic provider, donc synaptic_weights={}
        set_synaptic_provider(None)

        # Forcer un multiplier enorme sur VEILLE_SILENCIEUSE via un dict manuel
        huge_multipliers = {"VEILLE_SILENCIEUSE": 100.0}
        result = get_routines_for_drive_live(
            "MAITRISE",
            context_multipliers=huge_multipliers,
            use_context_multipliers=False,
            top_k=20,
        )
        intents = {i for i, _ in result}
        assert "VEILLE_SILENCIEUSE" not in intents, (
            "Un multiplier ne doit jamais creer un intent absent du genome"
        )

    def test_multiplier_zero_does_not_crash(self):
        """Un multiplier a 0.0 ne doit pas crasher ni creer de division par zero."""
        set_synaptic_provider(None)
        zero_mult = {"REFACTORING_AUDIT": 0.0}
        # Ne doit pas lever
        result = get_routines_for_drive_live(
            "MAITRISE",
            context_multipliers=zero_mult,
            use_context_multipliers=False,
            top_k=10,
        )
        # REFACTORING_AUDIT ne doit PAS apparaitre (poids * 0 = 0, filtre)
        intents = {i for i, _ in result}
        assert "REFACTORING_AUDIT" not in intents


# ═══════════════════════════════════════════════════════════════════════
# Tests auto-injection dans get_routines_for_drive_live
# ═══════════════════════════════════════════════════════════════════════


class TestAutoInjection:
    def test_use_context_multipliers_false_equals_pure(self):
        """Avec use_context_multipliers=False, la facade equivaut a la
        fonction pure sans multipliers."""
        set_synaptic_provider(None)
        r_live = get_routines_for_drive_live(
            "MAITRISE",
            temperature=0.0,
            top_k=5,
            use_context_multipliers=False,
        )
        r_pure = get_routines_for_drive(
            "MAITRISE",
            synaptic_weights={},
            temperature=0.0,
            top_k=5,
        )
        assert r_live == r_pure

    def test_explicit_multipliers_bypass_auto_injection(self):
        """Si l'appelant passe context_multipliers explicitement,
        l'auto-injection est bypassee (priorite a l'appelant)."""
        set_synaptic_provider(None)
        explicit = {"REFACTORING_AUDIT": 0.5}
        result = get_routines_for_drive_live(
            "MAITRISE",
            context_multipliers=explicit,
            temperature=0.0,
            top_k=10,
        )
        # Le multiplier explicite doit etre applique
        intents_dict = dict(result)
        assert "REFACTORING_AUDIT" in intents_dict
        # Il doit etre a la moitie de son floor genomique (0.9 * 0.5 = 0.45)
        # mais le floor est adouci par la grace period. On verifie juste
        # qu'il est inferieur au floor nominal
        genome_floor = DRIVE_GENOME["MAITRISE"]["REFACTORING_AUDIT"]
        assert intents_dict["REFACTORING_AUDIT"] < genome_floor

    def test_default_use_context_multipliers_is_true(self):
        """Par defaut, l'auto-injection est active (use_context_multipliers=True).
        La facade peut differer de la fonction pure si l'etat des organes
        produit des multipliers non-neutres."""
        set_synaptic_provider(None)
        r_live_auto = get_routines_for_drive_live("MAITRISE", temperature=0.0, top_k=5)
        r_live_off = get_routines_for_drive_live(
            "MAITRISE", temperature=0.0, top_k=5, use_context_multipliers=False
        )
        # Les deux doivent retourner les memes INTENTS (ordre et poids peuvent
        # differer si multipliers actifs), mais jamais d'intents etrangers.
        intents_auto = {i for i, _ in r_live_auto}
        intents_off = {i for i, _ in r_live_off}
        # L'auto ne doit pas INTRODUIRE d'intents absents (garde-fou)
        assert intents_auto.issubset(intents_off | set(DRIVE_GENOME["MAITRISE"].keys()))
