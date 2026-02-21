# tests/test_desire_engine.py — Tests unitaires pour DesireEngine
import json
import os
import time
import pytest
import asyncio
from unittest.mock import patch, MagicMock

from core.desire_engine import (
    DesireEngine, Drive, DRIVE_NAMES, TRAIT_RESONANCE,
    EVENT_IMPACT, DRIVE_ROUTINE_AFFINITY, DRIVE_NARRATIVES,
    NATURAL_RISE_PER_HOUR,
)


@pytest.fixture(autouse=True)
def reset_desire_singleton():
    """Reset le singleton avant chaque test."""
    DesireEngine.reset_singleton()
    yield
    DesireEngine.reset_singleton()


@pytest.fixture
def engine():
    """Cree un DesireEngine frais (sans charger de fichier)."""
    with patch("core.desire_engine.DesireEngine._load"):
        eng = DesireEngine()
    return eng


class TestDriveBasics:
    def test_initial_deprivation(self, engine):
        """Toutes les pulsions demarrent a 40."""
        for drive in engine.drives.values():
            assert drive.deprivation == 40.0

    def test_drive_names_complete(self, engine):
        """Les 7 pulsions sont presentes."""
        assert len(engine.drives) == 7
        for name in DRIVE_NAMES:
            assert name in engine.drives

    def test_drive_clamped_0_100(self, engine):
        """La deprivation reste dans [0, 100]."""
        engine.drives["CURIOSITE"].deprivation = 95.0
        engine.on_event("KNOWLEDGE_GAP_FOUND")  # +5 CURIOSITE
        assert engine.drives["CURIOSITE"].deprivation <= 100.0

        engine.drives["CREATION"].deprivation = 5.0
        engine.on_event("ARTIFACT_CREATED")  # -20 CREATION
        assert engine.drives["CREATION"].deprivation >= 0.0


class TestTick:
    def test_deprivation_rises_with_time(self, engine):
        """tick() augmente la deprivation proportionnellement au temps ecoule."""
        engine.drives["CURIOSITE"].deprivation = 50.0
        engine._last_tick = time.time() - 3600  # 1 heure avant
        with patch.object(engine, "_get_traits_avg", return_value={}):
            engine.tick()
        # ~0.3 point de montee attendu
        assert engine.drives["CURIOSITE"].deprivation > 50.0
        assert engine.drives["CURIOSITE"].deprivation < 51.0

    def test_trait_resonance_amplifies(self, engine):
        """Un trait PSYCHE eleve amplifie la montee de la pulsion correspondante."""
        engine.drives["CURIOSITE"].deprivation = 50.0
        engine._last_tick = time.time() - 3600
        # curiosite PSYCHE a 80 → amplificateur positif
        traits = {"curiosite": 80.0, "creativite": 50.0}
        with patch.object(engine, "_get_traits_avg", return_value=traits):
            engine.tick()
        curiosite_high = engine.drives["CURIOSITE"].deprivation

        # Reset et comparer sans amplification
        DesireEngine.reset_singleton()
        with patch("core.desire_engine.DesireEngine._load"):
            engine2 = DesireEngine()
        engine2.drives["CURIOSITE"].deprivation = 50.0
        engine2._last_tick = time.time() - 3600
        with patch.object(engine2, "_get_traits_avg", return_value={}):
            engine2.tick()
        curiosite_neutral = engine2.drives["CURIOSITE"].deprivation

        assert curiosite_high > curiosite_neutral

    def test_frustration_streak_amplifies(self, engine):
        """frustration_streak >= 3 amplifie la montee de 50%."""
        engine.drives["MAITRISE"].deprivation = 50.0
        engine.drives["MAITRISE"].frustration_streak = 3
        engine._last_tick = time.time() - 3600
        with patch.object(engine, "_get_traits_avg", return_value={}):
            engine.tick()
        maitrise_frustrated = engine.drives["MAITRISE"].deprivation

        DesireEngine.reset_singleton()
        with patch("core.desire_engine.DesireEngine._load"):
            engine2 = DesireEngine()
        engine2.drives["MAITRISE"].deprivation = 50.0
        engine2.drives["MAITRISE"].frustration_streak = 0
        engine2._last_tick = time.time() - 3600
        with patch.object(engine2, "_get_traits_avg", return_value={}):
            engine2.tick()
        maitrise_calm = engine2.drives["MAITRISE"].deprivation

        assert maitrise_frustrated > maitrise_calm

    def test_tick_respects_max_100(self, engine):
        """tick() ne depasse jamais 100."""
        for drive in engine.drives.values():
            drive.deprivation = 99.9
        engine._last_tick = time.time() - 7200  # 2h
        with patch.object(engine, "_get_traits_avg", return_value={}):
            engine.tick()
        for drive in engine.drives.values():
            assert drive.deprivation <= 100.0


class TestOnEvent:
    def test_routine_success_satisfies(self, engine):
        """ROUTINE_SUCCESS diminue la deprivation des pulsions associees."""
        engine.drives["CREATION"].deprivation = 70.0
        engine.on_event("ROUTINE_SUCCESS", {"intent": "EXPANSION_CODE"})
        assert engine.drives["CREATION"].deprivation < 70.0

    def test_routine_failure_frustrates(self, engine):
        """ROUTINE_FAILURE augmente la deprivation."""
        engine.drives["MAITRISE"].deprivation = 50.0
        engine.on_event("ROUTINE_FAILURE", {"intent": "EXPANSION_CODE"})
        assert engine.drives["MAITRISE"].deprivation > 50.0

    def test_council_consensus_satisfies_connexion(self, engine):
        """COUNCIL_CONSENSUS satisfait CONNEXION."""
        engine.drives["CONNEXION"].deprivation = 80.0
        engine.on_event("COUNCIL_CONSENSUS")
        assert engine.drives["CONNEXION"].deprivation < 80.0

    def test_artifact_created_satisfies_creation(self, engine):
        """ARTIFACT_CREATED satisfait fortement CREATION."""
        engine.drives["CREATION"].deprivation = 80.0
        engine.on_event("ARTIFACT_CREATED")
        assert engine.drives["CREATION"].deprivation == 60.0  # 80 - 20

    def test_evolution_deployed_multi_satisfaction(self, engine):
        """EVOLUTION_DEPLOYED satisfait CROISSANCE, CREATION et MAITRISE."""
        engine.drives["CROISSANCE"].deprivation = 80.0
        engine.drives["CREATION"].deprivation = 60.0
        engine.drives["MAITRISE"].deprivation = 50.0
        engine.on_event("EVOLUTION_DEPLOYED")
        assert engine.drives["CROISSANCE"].deprivation == 60.0
        assert engine.drives["CREATION"].deprivation == 50.0
        assert engine.drives["MAITRISE"].deprivation == 40.0

    def test_unknown_event_no_crash(self, engine):
        """Un evenement inconnu ne crashe pas."""
        initial = {name: d.deprivation for name, d in engine.drives.items()}
        engine.on_event("UNKNOWN_EVENT_XYZ")
        for name, d in engine.drives.items():
            assert d.deprivation == initial[name]

    def test_frustration_streak_increments(self, engine):
        """Les echecs consecutifs incrementent frustration_streak."""
        assert engine.drives["MAITRISE"].frustration_streak == 0
        engine.on_event("ROUTINE_FAILURE", {"intent": "EXPANSION_CODE"})
        assert engine.drives["MAITRISE"].frustration_streak == 1
        engine.on_event("ROUTINE_FAILURE", {"intent": "EXPANSION_CODE"})
        assert engine.drives["MAITRISE"].frustration_streak == 2

    def test_satisfaction_resets_frustration(self, engine):
        """Une satisfaction reset le frustration_streak."""
        engine.drives["CREATION"].frustration_streak = 5
        engine.on_event("ARTIFACT_CREATED")
        assert engine.drives["CREATION"].frustration_streak == 0


class TestDesireBonus:
    def test_no_bonus_when_serene(self, engine):
        """Pas de bonus quand deprivation < 30."""
        for drive in engine.drives.values():
            drive.deprivation = 20.0
        bonus = engine.compute_desire_bonus("EXPANSION_CODE")
        assert bonus == 0.0

    def test_bonus_scales_with_deprivation(self, engine):
        """Le bonus augmente avec la deprivation."""
        engine.drives["CREATION"].deprivation = 50.0
        bonus_low = engine.compute_desire_bonus("EXPANSION_CODE")

        engine.drives["CREATION"].deprivation = 90.0
        bonus_high = engine.compute_desire_bonus("EXPANSION_CODE")

        assert bonus_high > bonus_low

    def test_bonus_capped_at_3(self, engine):
        """Le bonus ne depasse jamais 3.0."""
        for drive in engine.drives.values():
            drive.deprivation = 100.0
        bonus = engine.compute_desire_bonus("EXPANSION_CODE")
        assert bonus <= 3.0

    def test_affinity_determines_routine_match(self, engine):
        """CONNEXION elevee favorise COUNCIL_DEBATE plus que SECURITY_AUDIT."""
        for drive in engine.drives.values():
            drive.deprivation = 20.0
        engine.drives["CONNEXION"].deprivation = 80.0
        bonus_council = engine.compute_desire_bonus("COUNCIL_DEBATE")
        bonus_security = engine.compute_desire_bonus("SECURITY_AUDIT")
        assert bonus_council > bonus_security

    def test_multiple_drives_stack(self, engine):
        """Plusieurs pulsions elevees cumulent leurs bonus."""
        # Un seul drive eleve
        for drive in engine.drives.values():
            drive.deprivation = 20.0
        engine.drives["CREATION"].deprivation = 80.0
        bonus_one = engine.compute_desire_bonus("EXPANSION_CODE")

        # Plusieurs drives eleves
        engine.drives["CROISSANCE"].deprivation = 80.0
        engine.drives["MAITRISE"].deprivation = 80.0
        bonus_multi = engine.compute_desire_bonus("EXPANSION_CODE")

        assert bonus_multi > bonus_one


class TestNarrative:
    def test_no_narrative_below_60(self, engine):
        """Pas de narratif quand toutes les pulsions sont < 60."""
        for drive in engine.drives.values():
            drive.deprivation = 55.0
        narrative = engine.get_dominant_narrative()
        assert narrative == ""

    def test_narrative_at_75(self, engine):
        """Narratif present quand une pulsion atteint 75."""
        engine.drives["CURIOSITE"].deprivation = 75.0
        narrative = engine.get_dominant_narrative()
        assert "decouvrir" in narrative.lower()

    def test_narrative_at_90_uses_highest_threshold(self, engine):
        """A 90, le narratif du seuil 90 est utilise."""
        engine.drives["CROISSANCE"].deprivation = 92.0
        narrative = engine.get_dominant_narrative()
        assert "evoluer" in narrative.lower()

    def test_top_n_limits_phrases(self, engine):
        """top_n limite le nombre de phrases."""
        engine.drives["CURIOSITE"].deprivation = 80.0
        engine.drives["CREATION"].deprivation = 80.0
        engine.drives["CONNEXION"].deprivation = 80.0

        narrative_1 = engine.get_dominant_narrative(top_n=1)
        narrative_3 = engine.get_dominant_narrative(top_n=3)
        # top_n=1 ne devrait contenir qu'une seule phrase
        assert len(narrative_1) < len(narrative_3)

    def test_empty_when_all_serene(self, engine):
        """Narratif vide quand toutes les pulsions sont satisfaites."""
        for drive in engine.drives.values():
            drive.deprivation = 10.0
        assert engine.get_dominant_narrative() == ""


class TestDriveSummary:
    def test_summary_has_all_fields(self, engine):
        """Le resume contient tous les champs attendus."""
        summary = engine.get_drive_summary()
        assert "drives" in summary
        assert "dominant" in summary
        assert "most_satisfied" in summary
        assert "urgent" in summary
        assert "narrative" in summary
        assert "total_satisfactions" in summary
        assert len(summary["drives"]) == 7

    def test_dominant_is_highest_deprivation(self, engine):
        """Le dominant est la pulsion avec la plus haute deprivation."""
        engine.drives["CREATION"].deprivation = 95.0
        summary = engine.get_drive_summary()
        assert summary["dominant"]["name"] == "CREATION"
        assert summary["dominant"]["deprivation"] == 95.0

    def test_urgent_lists_above_75(self, engine):
        """urgent liste les pulsions >= 75."""
        engine.drives["CURIOSITE"].deprivation = 80.0
        engine.drives["STABILITE"].deprivation = 90.0
        engine.drives["CREATION"].deprivation = 30.0
        summary = engine.get_drive_summary()
        assert "CURIOSITE" in summary["urgent"]
        assert "STABILITE" in summary["urgent"]
        assert "CREATION" not in summary["urgent"]


class TestPersistence:
    def test_save_and_load_roundtrip(self, engine, tmp_path):
        """Sauvegarde et chargement conservent l'etat."""
        state_file = str(tmp_path / "desire_state.json")
        engine.drives["CURIOSITE"].deprivation = 88.5
        engine.drives["CURIOSITE"].total_satisfied = 7
        engine.drives["CREATION"].frustration_streak = 3

        with patch("core.desire_engine.STATE_FILE", state_file):
            engine.save()

        # Creer un nouveau moteur qui charge le fichier
        DesireEngine.reset_singleton()
        with patch("core.desire_engine.STATE_FILE", state_file):
            engine2 = DesireEngine()

        assert abs(engine2.drives["CURIOSITE"].deprivation - 88.5) < 0.1
        assert engine2.drives["CURIOSITE"].total_satisfied == 7
        assert engine2.drives["CREATION"].frustration_streak == 3

    def test_load_missing_file_defaults(self, engine, tmp_path):
        """Chargement d'un fichier inexistant garde les valeurs par defaut."""
        state_file = str(tmp_path / "nonexistent.json")
        DesireEngine.reset_singleton()
        with patch("core.desire_engine.STATE_FILE", state_file):
            engine2 = DesireEngine()
        assert engine2.drives["CURIOSITE"].deprivation == 40.0

    def test_save_atomic(self, engine, tmp_path):
        """La sauvegarde est atomique (tmp + replace)."""
        state_file = str(tmp_path / "desire_state.json")
        with patch("core.desire_engine.STATE_FILE", state_file):
            engine.save()
        assert os.path.exists(state_file)
        assert not os.path.exists(state_file + ".tmp")


class TestEventBusIntegration:
    def test_subscribe_events_idempotent(self, engine):
        """subscribe_events peut etre appele plusieurs fois sans doublon."""
        engine._subscribe_events()
        engine._subscribe_events()
        assert engine._subscribed is True

    @pytest.mark.asyncio
    async def test_routine_complete_triggers_on_event(self, engine):
        """AUTONOMY_ROUTINE_COMPLETE declenche on_event."""
        engine.drives["CREATION"].deprivation = 70.0
        await engine._on_routine_complete({
            "intent": "EXPANSION_CODE",
            "status": "success",
            "quality_score": 0.8,
        })
        assert engine.drives["CREATION"].deprivation < 70.0


class TestTraitResonance:
    def test_high_curiosite_amplifies_curiosite_drive(self, engine):
        """Un trait curiosite PSYCHE eleve amplifie la pulsion CURIOSITE."""
        # Resonance CURIOSITE → curiosite: 0.4
        engine.drives["CURIOSITE"].deprivation = 50.0
        engine._last_tick = time.time() - 3600
        traits = {"curiosite": 90.0}  # tres eleve
        with patch.object(engine, "_get_traits_avg", return_value=traits):
            engine.tick()
        # amplifier = (90-50)/50 * 0.4 = 0.32
        # rise = 0.3 * 1.32 = 0.396
        assert engine.drives["CURIOSITE"].deprivation > 50.3

    def test_neutral_traits_no_amplification(self, engine):
        """Des traits neutres (50) n'amplifient pas."""
        engine.drives["CURIOSITE"].deprivation = 50.0
        engine._last_tick = time.time() - 3600
        traits = {"curiosite": 50.0, "creativite": 50.0}
        with patch.object(engine, "_get_traits_avg", return_value=traits):
            engine.tick()
        # rise = 0.3 * (1 + 0) = 0.3
        assert abs(engine.drives["CURIOSITE"].deprivation - 50.3) < 0.05

    def test_low_traits_reduces_rise(self, engine):
        """Des traits bas reduisent la montee de la pulsion."""
        engine.drives["CURIOSITE"].deprivation = 50.0
        engine._last_tick = time.time() - 3600
        traits = {"curiosite": 20.0}  # bas
        with patch.object(engine, "_get_traits_avg", return_value=traits):
            engine.tick()
        # amplifier = (20-50)/50 * 0.4 = -0.24
        # rise = 0.3 * 0.76 = 0.228
        assert engine.drives["CURIOSITE"].deprivation < 50.3


class TestSingleton:
    def test_singleton_pattern(self):
        """Deux instanciations retournent le meme objet."""
        with patch("core.desire_engine.DesireEngine._load"):
            e1 = DesireEngine()
            e2 = DesireEngine()
        assert e1 is e2

    def test_reset_singleton(self):
        """reset_singleton() permet de creer une nouvelle instance."""
        with patch("core.desire_engine.DesireEngine._load"):
            e1 = DesireEngine()
        DesireEngine.reset_singleton()
        with patch("core.desire_engine.DesireEngine._load"):
            e2 = DesireEngine()
        assert e1 is not e2
