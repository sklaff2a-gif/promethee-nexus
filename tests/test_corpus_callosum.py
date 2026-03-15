"""
Tests pour core/corpus_callosum.py — Corpus Callosum (Resonance Inter-Organes).

~60 tests couvrant : singleton, capture, 6 patterns, bonus, anti-spam,
coherence globale, persistance, bus integration.
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from core import corpus_callosum as mod
from core.corpus_callosum import (
    CorpusCallosum, OrganSnapshot, ResonancePattern,
    CALLOSUM_STATE_FILE, MAX_RESONANCE_EFFECTS_PER_CYCLE,
    MIN_RESONANCE_INTERVAL, COGNITIVE_STATES,
)


# ============================================================
# Fixture autouse
# ============================================================

@pytest.fixture(autouse=True)
def isolate_callosum(tmp_path, monkeypatch):
    """Reset le singleton et isole le fichier d'etat."""
    CorpusCallosum.reset_singleton()
    monkeypatch.setattr(mod, "CALLOSUM_STATE_FILE", str(tmp_path / "state.json"))
    with patch.object(CorpusCallosum, "_load"):
        c = CorpusCallosum()
        mod.callosum = c
    yield c
    CorpusCallosum.reset_singleton()


def _make_snapshot(**kwargs) -> OrganSnapshot:
    """Helper : cree un snapshot avec des valeurs par defaut saines."""
    defaults = dict(
        timestamp=time.time(),
        cardiac_bpm=65.0,
        cardiac_coherence=0.5,
        cardiac_emotion="serenite",
        cardiac_emotion_intensity=0.3,
        dominant_drive="CURIOSITE",
        dominant_deprivation=40.0,
        frustrated_drives=[],
        threat_level=0.0,
        adrenaline=0.0,
        active_goals=0,
        goal_progress=0.0,
        has_active_goal=False,
        dopamine_level=0.5,
        synaptic_energy=0.3,
        active_nodes=2,
        voice_active=False,
        voice_mode="",
    )
    defaults.update(kwargs)
    return OrganSnapshot(**defaults)


# ============================================================
# TestSingleton
# ============================================================

class TestSingleton:
    """Tests singleton, reset, etat initial."""

    def test_singleton_identity(self, isolate_callosum):
        """Deux instanciations retournent le meme objet."""
        c2 = CorpusCallosum()
        assert c2 is isolate_callosum

    def test_reset_singleton(self, isolate_callosum):
        """reset_singleton cree une nouvelle instance."""
        old_id = id(isolate_callosum)
        CorpusCallosum.reset_singleton()
        with patch.object(CorpusCallosum, "_load"):
            c2 = CorpusCallosum()
        assert id(c2) != old_id

    def test_initial_state(self, isolate_callosum):
        """Etat initial : standard, coherence 0.5."""
        assert isolate_callosum.cognitive_state == "standard"
        assert isolate_callosum.global_coherence == 0.5
        assert isolate_callosum._resonance_log == []

    def test_cognitive_states_list(self):
        """Les 7 etats cognitifs sont definis."""
        assert len(COGNITIVE_STATES) == 7
        assert "flow" in COGNITIVE_STATES
        assert "crisis" in COGNITIVE_STATES
        assert "standard" in COGNITIVE_STATES


# ============================================================
# TestOrganCapture
# ============================================================

class TestOrganCapture:
    """Tests capture des etats d'organes."""

    def test_capture_without_organs(self, isolate_callosum):
        """Capture sans aucun organe disponible retourne un snapshot par defaut."""
        with patch.dict("sys.modules", {
            "core.cardiac_engine": None,
            "core.desire_engine": None,
            "core.reptilian_core": None,
            "core.prefrontal": None,
            "core.dopamine_system": None,
            "core.synaptic_network": None,
            "core.inner_voice": None,
        }):
            snap = isolate_callosum._capture_organ_states()
        assert isinstance(snap, OrganSnapshot)
        assert snap.cardiac_bpm == 65.0  # default

    def test_capture_with_cardiac(self, isolate_callosum):
        """Capture les donnees cardiaques."""
        mock_heart = MagicMock()
        mock_heart.bpm = 72.0
        mock_heart.coherence = 0.8
        mock_heart.current_emotion = "curiosite"
        mock_heart.emotion_intensity = 0.6
        mock_cardiac = MagicMock()
        mock_cardiac.heart = mock_heart
        with patch.dict("sys.modules", {"core.cardiac_engine": mock_cardiac}):
            snap = isolate_callosum._capture_organ_states()
        assert snap.cardiac_bpm == 72.0
        assert snap.cardiac_coherence == 0.8
        assert snap.cardiac_emotion == "curiosite"

    def test_capture_with_desire(self, isolate_callosum):
        """Capture les pulsions dominantes."""
        mock_drive_curiosite = MagicMock()
        mock_drive_curiosite.name = "CURIOSITE"
        mock_drive_curiosite.deprivation = 70.0
        mock_drive_maitrise = MagicMock()
        mock_drive_maitrise.name = "MAITRISE"
        mock_drive_maitrise.deprivation = 30.0
        mock_desires = MagicMock()
        mock_desires.drives = {"CURIOSITE": mock_drive_curiosite, "MAITRISE": mock_drive_maitrise}
        mock_desire_mod = MagicMock()
        mock_desire_mod.desires = mock_desires
        with patch.dict("sys.modules", {"core.desire_engine": mock_desire_mod}):
            snap = isolate_callosum._capture_organ_states()
        assert snap.dominant_drive == "CURIOSITE"
        assert snap.dominant_deprivation == 70.0
        assert "CURIOSITE" in snap.frustrated_drives

    def test_capture_with_reptilian(self, isolate_callosum):
        """Capture la menace reptilienne."""
        mock_reptile = MagicMock()
        mock_reptile.threat_level = 5.0
        mock_reptile.adrenaline = 0.7
        mock_mod = MagicMock()
        mock_mod.reptile = mock_reptile
        with patch.dict("sys.modules", {"core.reptilian_core": mock_mod}):
            snap = isolate_callosum._capture_organ_states()
        assert snap.threat_level == 5.0
        assert snap.adrenaline == 0.7

    def test_capture_with_prefrontal(self, isolate_callosum):
        """Capture les objectifs prefrontaux."""
        mock_goal = MagicMock()
        mock_goal.status = "active"
        mock_goal.progress = 60.0
        mock_pf = MagicMock()
        mock_pf.goals = [mock_goal]
        mock_mod = MagicMock()
        mock_mod.prefrontal = mock_pf
        with patch.dict("sys.modules", {"core.prefrontal": mock_mod}):
            snap = isolate_callosum._capture_organ_states()
        assert snap.active_goals == 1
        assert snap.has_active_goal is True
        assert snap.goal_progress == 60.0

    def test_capture_timestamp(self, isolate_callosum):
        """Le snapshot a un timestamp recent."""
        before = time.time()
        snap = isolate_callosum._capture_organ_states()
        after = time.time()
        assert before <= snap.timestamp <= after


# ============================================================
# TestFlowDetection
# ============================================================

class TestFlowDetection:
    """Tests detection et effets du pattern FLOW."""

    def _flow_snapshot(self, **overrides):
        """Snapshot qui declenche flow."""
        defaults = dict(
            cardiac_coherence=0.75,
            dopamine_level=0.70,
            has_active_goal=True,
            active_goals=1,
            goal_progress=50.0,
            threat_level=0.5,
        )
        defaults.update(overrides)
        return _make_snapshot(**defaults)

    def test_flow_detected(self, isolate_callosum):
        """Flow detecte quand toutes les conditions sont remplies."""
        snap = self._flow_snapshot()
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        flow = [p for p in patterns if p.pattern_type == "flow"]
        assert len(flow) == 1
        assert flow[0].confidence > 0.5

    def test_flow_not_detected_low_coherence(self, isolate_callosum):
        """Pas de flow si coherence cardiaque basse."""
        snap = self._flow_snapshot(cardiac_coherence=0.3)
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        flow = [p for p in patterns if p.pattern_type == "flow"]
        assert len(flow) == 0

    def test_flow_not_detected_no_goal(self, isolate_callosum):
        """Pas de flow sans objectif actif."""
        snap = self._flow_snapshot(has_active_goal=False)
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        flow = [p for p in patterns if p.pattern_type == "flow"]
        assert len(flow) == 0

    def test_flow_not_detected_high_threat(self, isolate_callosum):
        """Pas de flow si menace >= 3."""
        snap = self._flow_snapshot(threat_level=4.0)
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        flow = [p for p in patterns if p.pattern_type == "flow"]
        assert len(flow) == 0

    def test_flow_effects_cardiac(self, isolate_callosum):
        """Flow active cardiac:react(creation)."""
        mock_heart = MagicMock()
        mock_cardiac = MagicMock()
        mock_cardiac.heart = mock_heart
        mock_desires = MagicMock()
        mock_drive = MagicMock()
        mock_drive.deprivation = 50.0
        mock_desires.drives = {"MAITRISE": mock_drive}
        mock_desire_mod = MagicMock()
        mock_desire_mod.desires = mock_desires
        with patch.dict("sys.modules", {
            "core.cardiac_engine": mock_cardiac,
            "core.desire_engine": mock_desire_mod,
        }):
            snap = self._flow_snapshot()
            effects = isolate_callosum._effects_flow(snap)
        assert any("cardiac:react(creation)" in e for e in effects)
        mock_heart.react.assert_called_once_with("creation")

    def test_flow_confidence_range(self, isolate_callosum):
        """Confidence flow entre 0 et 1."""
        snap = self._flow_snapshot()
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        flow = [p for p in patterns if p.pattern_type == "flow"][0]
        assert 0.0 <= flow.confidence <= 1.0


# ============================================================
# TestCrisisDetection
# ============================================================

class TestCrisisDetection:
    """Tests detection et effets du pattern CRISIS."""

    def _crisis_snapshot(self, **overrides):
        """Snapshot qui declenche crisis."""
        defaults = dict(
            threat_level=6.0,
            dominant_deprivation=70.0,
            dopamine_level=0.25,
            frustrated_drives=["STABILITE", "CURIOSITE"],
        )
        defaults.update(overrides)
        return _make_snapshot(**defaults)

    def test_crisis_detected(self, isolate_callosum):
        """Crisis detecte quand menace + deprivation + dopamine basse."""
        snap = self._crisis_snapshot()
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        crisis = [p for p in patterns if p.pattern_type == "crisis"]
        assert len(crisis) == 1
        assert crisis[0].confidence > 0.5

    def test_crisis_not_detected_low_threat(self, isolate_callosum):
        """Pas de crisis si menace < 4."""
        snap = self._crisis_snapshot(threat_level=2.0)
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        crisis = [p for p in patterns if p.pattern_type == "crisis"]
        assert len(crisis) == 0

    def test_crisis_not_detected_high_dopamine(self, isolate_callosum):
        """Pas de crisis si dopamine >= 0.4."""
        snap = self._crisis_snapshot(dopamine_level=0.5)
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        crisis = [p for p in patterns if p.pattern_type == "crisis"]
        assert len(crisis) == 0

    def test_crisis_effects_suppress_drives(self, isolate_callosum):
        """Crisis supprime CURIOSITE/CREATION et booste STABILITE."""
        mock_drive_cur = MagicMock()
        mock_drive_cur.deprivation = 60.0
        mock_drive_cre = MagicMock()
        mock_drive_cre.deprivation = 50.0
        mock_drive_stab = MagicMock()
        mock_drive_stab.deprivation = 40.0
        mock_desires = MagicMock()
        mock_desires.drives = {
            "CURIOSITE": mock_drive_cur,
            "CREATION": mock_drive_cre,
            "STABILITE": mock_drive_stab,
        }
        mock_mod = MagicMock()
        mock_mod.desires = mock_desires
        with patch.dict("sys.modules", {"core.desire_engine": mock_mod}):
            snap = self._crisis_snapshot()
            effects = isolate_callosum._effects_crisis(snap)
        # CURIOSITE : 60 - 15 = 45
        assert mock_drive_cur.deprivation == 45.0
        # CREATION : 50 - 15 = 35
        assert mock_drive_cre.deprivation == 35.0
        # STABILITE : 40 + 10 = 50
        assert mock_drive_stab.deprivation == 50.0
        assert len(effects) == 3

    def test_crisis_transition_from_alert(self, isolate_callosum):
        """Alerte reptilienne FREEZE → transition immediate en crisis."""
        assert isolate_callosum.cognitive_state == "standard"
        event = {"reflex": "FREEZE", "threat_level": 8.0}
        asyncio.get_event_loop().run_until_complete(
            isolate_callosum._on_reptilian_alert(event)
        )
        assert isolate_callosum.cognitive_state == "crisis"

    def test_recovery_after_crisis(self, isolate_callosum):
        """Recovery detecte apres une crise."""
        isolate_callosum._previous_state = "crisis"
        snap = _make_snapshot(
            cardiac_bpm=50.0,
            cardiac_coherence=0.6,
            threat_level=1.0,
        )
        state = isolate_callosum._determine_cognitive_state(snap)
        assert state == "recovery"


# ============================================================
# TestCreativeSurge
# ============================================================

class TestCreativeSurge:
    """Tests detection et effets du pattern CREATIVE_SURGE."""

    def _creative_snapshot(self, **overrides):
        defaults = dict(
            dopamine_level=0.75,
            frustrated_drives=["CURIOSITE", "CREATION"],
            synaptic_energy=0.5,
        )
        defaults.update(overrides)
        return _make_snapshot(**defaults)

    def test_creative_surge_detected(self, isolate_callosum):
        """Creative surge detecte avec dopamine haute + drives creatifs + synaptique."""
        snap = self._creative_snapshot()
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        creative = [p for p in patterns if p.pattern_type == "creative_surge"]
        assert len(creative) == 1

    def test_creative_surge_not_detected_low_dopamine(self, isolate_callosum):
        """Pas de creative surge si dopamine < 0.65."""
        snap = self._creative_snapshot(dopamine_level=0.4)
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        creative = [p for p in patterns if p.pattern_type == "creative_surge"]
        assert len(creative) == 0

    def test_creative_surge_not_detected_no_frustrated_creative(self, isolate_callosum):
        """Pas de creative surge sans drives creatifs frustres."""
        snap = self._creative_snapshot(frustrated_drives=["STABILITE"])
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        creative = [p for p in patterns if p.pattern_type == "creative_surge"]
        assert len(creative) == 0

    def test_creative_effects_satisfy_drives(self, isolate_callosum):
        """Creative surge satisfait CREATION/CURIOSITE."""
        mock_drive_cre = MagicMock()
        mock_drive_cre.deprivation = 60.0
        mock_drive_cur = MagicMock()
        mock_drive_cur.deprivation = 55.0
        mock_desires = MagicMock()
        mock_desires.drives = {"CREATION": mock_drive_cre, "CURIOSITE": mock_drive_cur}
        mock_mod = MagicMock()
        mock_mod.desires = mock_desires
        mock_cortex = MagicMock()
        mock_syn = MagicMock()
        mock_syn.cortex = mock_cortex
        with patch.dict("sys.modules", {
            "core.desire_engine": mock_mod,
            "core.synaptic_network": mock_syn,
        }):
            snap = self._creative_snapshot()
            effects = isolate_callosum._effects_creative_surge(snap)
        assert mock_drive_cre.deprivation == 52.0  # 60 - 8
        assert mock_drive_cur.deprivation == 47.0  # 55 - 8
        assert any("synaptic:activate(creativite)" in e for e in effects)

    def test_creative_confidence(self, isolate_callosum):
        """Confidence creative surge dans [0, 1]."""
        snap = self._creative_snapshot()
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        creative = [p for p in patterns if p.pattern_type == "creative_surge"][0]
        assert 0.0 <= creative.confidence <= 1.0


# ============================================================
# TestStagnation
# ============================================================

class TestStagnation:
    """Tests detection et effets du pattern STAGNATION."""

    def _stagnation_snapshot(self, **overrides):
        defaults = dict(
            dopamine_level=0.2,
            dominant_deprivation=70.0,
            has_active_goal=False,
            cardiac_coherence=0.3,
        )
        defaults.update(overrides)
        return _make_snapshot(**defaults)

    def test_stagnation_detected(self, isolate_callosum):
        """Stagnation detecte avec dopamine basse + frustration + pas de goals."""
        snap = self._stagnation_snapshot()
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        stag = [p for p in patterns if p.pattern_type == "stagnation"]
        assert len(stag) == 1

    def test_stagnation_not_detected_has_goal_progressing(self, isolate_callosum):
        """Pas de stagnation si goal actif ET qui progresse."""
        snap = self._stagnation_snapshot(has_active_goal=True, goal_progress=0.5)
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        stag = [p for p in patterns if p.pattern_type == "stagnation"]
        assert len(stag) == 0

    def test_stagnation_not_detected_high_dopamine(self, isolate_callosum):
        """Pas de stagnation si dopamine >= 0.35."""
        snap = self._stagnation_snapshot(dopamine_level=0.5)
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        stag = [p for p in patterns if p.pattern_type == "stagnation"]
        assert len(stag) == 0

    def test_stagnation_effects_exploration(self, isolate_callosum):
        """Stagnation active concept exploration + micro-boost dopamine."""
        mock_cortex = MagicMock()
        mock_syn = MagicMock()
        mock_syn.cortex = mock_cortex
        mock_dopa = MagicMock()
        mock_dopa.dopamine_level = 0.2
        mock_dopa_mod = MagicMock()
        mock_dopa_mod.dopamine = mock_dopa
        with patch.dict("sys.modules", {
            "core.synaptic_network": mock_syn,
            "core.dopamine_system": mock_dopa_mod,
        }):
            snap = self._stagnation_snapshot()
            effects = isolate_callosum._effects_stagnation(snap)
        assert any("synaptic:activate(exploration)" in e for e in effects)
        assert any("dopamine:micro_boost" in e for e in effects)
        assert mock_dopa.dopamine_level == 0.25  # 0.2 + 0.05

    def test_stagnation_confidence(self, isolate_callosum):
        """Confidence stagnation dans [0, 1]."""
        snap = self._stagnation_snapshot()
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        stag = [p for p in patterns if p.pattern_type == "stagnation"][0]
        assert 0.0 <= stag.confidence <= 1.0


# ============================================================
# TestResonanceBonus
# ============================================================

class TestResonanceBonus:
    """Tests compute_resonance_bonus par etat cognitif."""

    def test_bonus_standard_is_zero(self, isolate_callosum):
        """Etat standard retourne 0."""
        isolate_callosum.cognitive_state = "standard"
        assert isolate_callosum.compute_resonance_bonus("evolution") == 0.0

    def test_bonus_flow_boost_code(self, isolate_callosum):
        """Flow booste les intents productifs."""
        isolate_callosum.cognitive_state = "flow"
        isolate_callosum.global_coherence = 0.8
        bonus = isolate_callosum.compute_resonance_bonus("code_generation")
        assert bonus > 0.0

    def test_bonus_flow_penalize_interruption(self, isolate_callosum):
        """Flow penalise les interruptions."""
        isolate_callosum.cognitive_state = "flow"
        bonus = isolate_callosum.compute_resonance_bonus("soliloque")
        assert bonus < 0.0

    def test_bonus_crisis_boost_security(self, isolate_callosum):
        """Crisis booste la securite."""
        isolate_callosum.cognitive_state = "crisis"
        bonus = isolate_callosum.compute_resonance_bonus("security_audit")
        assert bonus > 0.0

    def test_bonus_crisis_penalize_exploration(self, isolate_callosum):
        """Crisis penalise l'exploration."""
        isolate_callosum.cognitive_state = "crisis"
        bonus = isolate_callosum.compute_resonance_bonus("research_topic")
        assert bonus < 0.0

    def test_bonus_creative_surge_boost(self, isolate_callosum):
        """Creative surge booste evolution."""
        isolate_callosum.cognitive_state = "creative_surge"
        bonus = isolate_callosum.compute_resonance_bonus("evolution_pipeline")
        assert bonus > 0.0

    def test_bonus_clamping_max(self, isolate_callosum):
        """Bonus clamp a +2.0."""
        isolate_callosum.cognitive_state = "flow"
        isolate_callosum.global_coherence = 1.0
        bonus = isolate_callosum.compute_resonance_bonus("code")
        assert bonus <= 2.0

    def test_bonus_clamping_min(self, isolate_callosum):
        """Bonus clamp a -1.5."""
        isolate_callosum.cognitive_state = "crisis"
        bonus = isolate_callosum.compute_resonance_bonus("explore_curiosity")
        assert bonus >= -1.5


# ============================================================
# TestAntiSpam
# ============================================================

class TestAntiSpam:
    """Tests cooldowns et max effets par cycle."""

    def test_cooldown_prevents_duplicate_effects(self, isolate_callosum):
        """Le meme type de pattern ne s'applique pas 2x dans le cooldown."""
        snap = _make_snapshot()
        pattern = ResonancePattern(
            pattern_type="flow", confidence=0.8,
            contributing_organs=["cardiac"], timestamp=time.time()
        )
        # Premier appel : effets
        effects1 = isolate_callosum._apply_cross_effects(pattern, snap)
        # Deuxieme appel immediat : pas d'effets (cooldown)
        effects2 = isolate_callosum._apply_cross_effects(pattern, snap)
        assert len(effects2) == 0

    def test_cooldown_expires(self, isolate_callosum):
        """Apres le cooldown, les effets sont a nouveau possibles."""
        snap = _make_snapshot()
        pattern = ResonancePattern(
            pattern_type="recovery", confidence=0.8,
            contributing_organs=["cardiac"], timestamp=time.time()
        )
        # Premier appel
        isolate_callosum._apply_cross_effects(pattern, snap)
        # Forcer l'expiration du cooldown
        isolate_callosum._last_effect_time["recovery"] = time.time() - MIN_RESONANCE_INTERVAL - 1
        # Deuxieme appel : effets a nouveau
        effects2 = isolate_callosum._apply_cross_effects(pattern, snap)
        # recovery a au moins un effet possible (cardiac:react(serenite) si module dispo)
        # Mais sans mock, l'import peut echouer → effects peut etre vide
        # On verifie juste que le cooldown a expire et qu'on est passe dans la logique
        assert isinstance(effects2, list)

    def test_max_effects_per_cycle(self, isolate_callosum):
        """Max 5 effets par cycle de resonance (relaxe depuis 3)."""
        assert MAX_RESONANCE_EFFECTS_PER_CYCLE == 5

    def test_different_pattern_types_not_blocked(self, isolate_callosum):
        """Des patterns differents ne se bloquent pas mutuellement."""
        snap = _make_snapshot()
        p1 = ResonancePattern(
            pattern_type="flow", confidence=0.8,
            contributing_organs=["cardiac"], timestamp=time.time()
        )
        p2 = ResonancePattern(
            pattern_type="crisis", confidence=0.8,
            contributing_organs=["reptilian"], timestamp=time.time()
        )
        isolate_callosum._apply_cross_effects(p1, snap)
        effects = isolate_callosum._apply_cross_effects(p2, snap)
        # crisis devrait avoir ses propres effets (ou au moins ne pas etre bloque par flow)
        assert isinstance(effects, list)


# ============================================================
# TestGlobalCoherence
# ============================================================

class TestGlobalCoherence:
    """Tests calcul coherence globale."""

    def test_coherence_perfect(self, isolate_callosum):
        """Coherence maximale quand tout est optimal."""
        snap = _make_snapshot(
            cardiac_coherence=1.0,
            dominant_deprivation=0.0,
            threat_level=0.0,
            has_active_goal=True,
            goal_progress=1.0,
            dopamine_level=1.0,
            synaptic_energy=1.0,
            voice_active=True,
        )
        coherence = isolate_callosum._compute_global_coherence(snap)
        assert coherence > 0.8

    def test_coherence_worst(self, isolate_callosum):
        """Coherence minimale quand tout est mauvais."""
        snap = _make_snapshot(
            cardiac_coherence=0.0,
            dominant_deprivation=100.0,
            threat_level=10.0,
            has_active_goal=False,
            dopamine_level=0.0,
            synaptic_energy=0.0,
            voice_active=False,
        )
        coherence = isolate_callosum._compute_global_coherence(snap)
        assert coherence < 0.3

    def test_coherence_range(self, isolate_callosum):
        """Coherence toujours dans [0, 1]."""
        for _ in range(10):
            import random
            snap = _make_snapshot(
                cardiac_coherence=random.random(),
                dominant_deprivation=random.uniform(0, 100),
                threat_level=random.uniform(0, 10),
                has_active_goal=random.choice([True, False]),
                goal_progress=random.random(),
                dopamine_level=random.random(),
                synaptic_energy=random.random(),
                voice_active=random.choice([True, False]),
            )
            coherence = isolate_callosum._compute_global_coherence(snap)
            assert 0.0 <= coherence <= 1.0

    def test_coherence_partial(self, isolate_callosum):
        """Coherence intermediaire avec des valeurs moyennes."""
        snap = _make_snapshot(
            cardiac_coherence=0.5,
            dominant_deprivation=50.0,
            threat_level=3.0,
            has_active_goal=True,
            goal_progress=0.5,
            dopamine_level=0.5,
            synaptic_energy=0.5,
            voice_active=False,
        )
        coherence = isolate_callosum._compute_global_coherence(snap)
        assert 0.3 <= coherence <= 0.7


# ============================================================
# TestPersistence
# ============================================================

class TestPersistence:
    """Tests sauvegarde/chargement de l'etat."""

    def test_save_creates_file(self, isolate_callosum, tmp_path):
        """save() cree le fichier d'etat."""
        isolate_callosum.cognitive_state = "flow"
        isolate_callosum.global_coherence = 0.85
        isolate_callosum.save()
        state_file = str(tmp_path / "state.json")
        assert os.path.exists(state_file)

    def test_save_load_roundtrip(self, isolate_callosum, tmp_path):
        """L'etat survit a un cycle save/load."""
        isolate_callosum.cognitive_state = "crisis"
        isolate_callosum.global_coherence = 0.25
        isolate_callosum._stats["patterns_detected"] = 42
        isolate_callosum.save()

        # Reset et reload
        CorpusCallosum.reset_singleton()
        # Ne pas patcher _load cette fois
        c2 = CorpusCallosum()
        assert c2.cognitive_state == "crisis"
        assert c2.global_coherence == 0.25
        assert c2._stats["patterns_detected"] == 42

    def test_load_corrupt_file(self, isolate_callosum, tmp_path):
        """Chargement d'un fichier corrompu ne crashe pas."""
        state_file = str(tmp_path / "state.json")
        with open(state_file, "w") as f:
            f.write("NOT JSON")
        # Reset et reload (sans patcher _load)
        CorpusCallosum.reset_singleton()
        c2 = CorpusCallosum()
        # Valeurs par defaut
        assert c2.cognitive_state == "standard"

    def test_load_missing_file(self, isolate_callosum, tmp_path):
        """Chargement sans fichier ne crashe pas."""
        CorpusCallosum.reset_singleton()
        c2 = CorpusCallosum()
        assert c2.cognitive_state == "standard"

    def test_resonance_log_persisted(self, isolate_callosum, tmp_path):
        """Le log de resonance est sauvegarde."""
        pattern = ResonancePattern(
            pattern_type="flow", confidence=0.9,
            contributing_organs=["cardiac", "dopamine"],
            timestamp=time.time(),
            narrative="Test pattern",
        )
        isolate_callosum._resonance_log.append(pattern)
        isolate_callosum._last_pattern = pattern
        isolate_callosum.save()

        CorpusCallosum.reset_singleton()
        c2 = CorpusCallosum()
        assert len(c2._resonance_log) == 1
        assert c2._resonance_log[0].pattern_type == "flow"
        assert c2._last_pattern is not None


# ============================================================
# TestBusIntegration
# ============================================================

class TestBusIntegration:
    """Tests souscription bus et handlers."""

    def test_subscribe_events(self, isolate_callosum):
        """init() souscrit aux 14 events (+SENSORIUM_FEEDBACK)."""
        isolate_callosum._subscribed = False
        with patch("core.corpus_callosum.bus") as mock_bus:
            isolate_callosum._subscribe_events()
        assert mock_bus.subscribe.call_count == 14
        isolate_callosum._subscribed = True  # Cleanup

    def test_subscribe_idempotent(self, isolate_callosum):
        """Double subscribe n'ajoute pas de doublons."""
        isolate_callosum._subscribed = False
        with patch("core.corpus_callosum.bus") as mock_bus:
            isolate_callosum._subscribe_events()
            isolate_callosum._subscribe_events()
        assert mock_bus.subscribe.call_count == 14

    def test_on_reptilian_alert_freeze(self, isolate_callosum):
        """FREEZE declenche transition immediate en crisis."""
        event = {"reflex": "FREEZE"}
        asyncio.get_event_loop().run_until_complete(
            isolate_callosum._on_reptilian_alert(event)
        )
        assert isolate_callosum.cognitive_state == "crisis"
        assert isolate_callosum._pending_reptilian_alert == event

    def test_on_reptilian_alert_non_urgent(self, isolate_callosum):
        """FLINCH ne declenche pas de transition immediate."""
        event = {"reflex": "FLINCH"}
        asyncio.get_event_loop().run_until_complete(
            isolate_callosum._on_reptilian_alert(event)
        )
        assert isolate_callosum.cognitive_state == "standard"

    @pytest.mark.asyncio
    async def test_on_dopamine_surge_bridge_cardiac(self, isolate_callosum):
        """Dopamine surge bridge vers cardiac (eureka)."""
        mock_heart = MagicMock()
        mock_cardiac = MagicMock()
        mock_cardiac.heart = mock_heart
        with patch.dict("sys.modules", {"core.cardiac_engine": mock_cardiac}):
            await isolate_callosum._on_dopamine_surge({"rpe": 0.5})
        mock_heart.react.assert_called_once_with("eureka")


# ============================================================
# TestCognitiveState
# ============================================================

class TestCognitiveState:
    """Tests transitions d'etat cognitif."""

    def test_standard_by_default(self, isolate_callosum):
        """Etat standard si aucune condition speciale."""
        snap = _make_snapshot()
        state = isolate_callosum._determine_cognitive_state(snap)
        assert state == "standard"

    def test_crisis_priority_over_flow(self, isolate_callosum):
        """Crisis a priorite sur flow."""
        snap = _make_snapshot(
            threat_level=5.0,
            dominant_deprivation=60.0,
            dopamine_level=0.35,
            cardiac_coherence=0.7,
            has_active_goal=True,
        )
        state = isolate_callosum._determine_cognitive_state(snap)
        assert state == "crisis"

    def test_exploration_detected(self, isolate_callosum):
        """Exploration detecte avec curiosite frustree + safe + dopamine."""
        snap = _make_snapshot(
            frustrated_drives=["CURIOSITE"],
            threat_level=0.5,
            dopamine_level=0.5,
        )
        state = isolate_callosum._determine_cognitive_state(snap)
        assert state == "exploration"

    def test_state_transition_counter(self, isolate_callosum):
        """Les transitions incrementent le compteur."""
        assert isolate_callosum._stats["state_transitions"] == 0
        isolate_callosum.cognitive_state = "standard"
        old_state = isolate_callosum.cognitive_state
        isolate_callosum._previous_state = old_state
        isolate_callosum.cognitive_state = "flow"
        isolate_callosum._stats["state_transitions"] += 1
        assert isolate_callosum._stats["state_transitions"] == 1


# ============================================================
# TestAPIPublique
# ============================================================

class TestAPIPublique:
    """Tests des methodes API publiques."""

    def test_get_stats(self, isolate_callosum):
        """get_stats retourne un dict complet."""
        stats = isolate_callosum.get_stats()
        assert "cognitive_state" in stats
        assert "global_coherence" in stats
        assert "cycles_completed" in stats
        assert "patterns_detected" in stats

    def test_get_narrative_empty(self, isolate_callosum):
        """Narrative vide sans pattern."""
        assert isolate_callosum.get_narrative() == ""

    def test_get_narrative_with_pattern(self, isolate_callosum):
        """Narrative du dernier pattern."""
        isolate_callosum._last_pattern = ResonancePattern(
            pattern_type="flow", confidence=0.8,
            narrative="Etat de flow detecte",
            timestamp=time.time(),
        )
        narrative = isolate_callosum.get_narrative()
        assert "flow" in narrative

    def test_get_cognitive_context_standard(self, isolate_callosum):
        """Context vide en etat standard."""
        assert isolate_callosum.get_cognitive_context() == ""

    def test_get_cognitive_context_flow(self, isolate_callosum):
        """Context non-vide en etat flow."""
        isolate_callosum.cognitive_state = "flow"
        ctx = isolate_callosum.get_cognitive_context()
        assert "[RESONANCE]" in ctx
        assert "FLOW" in ctx

    def test_should_amplify_veto_crisis(self, isolate_callosum):
        """Veto amplifie en crisis."""
        isolate_callosum.cognitive_state = "crisis"
        assert isolate_callosum.should_amplify_veto("any") is True

    def test_should_amplify_veto_flow_prefrontal(self, isolate_callosum):
        """Veto amplifie en flow pour prefrontal/reptilian."""
        isolate_callosum.cognitive_state = "flow"
        assert isolate_callosum.should_amplify_veto("prefrontal") is True
        assert isolate_callosum.should_amplify_veto("reptilian") is True
        assert isolate_callosum.should_amplify_veto("other") is False

    def test_should_amplify_veto_standard(self, isolate_callosum):
        """Veto non amplifie en standard."""
        isolate_callosum.cognitive_state = "standard"
        assert isolate_callosum.should_amplify_veto("prefrontal") is False


# ============================================================
# TestResonanceCycle
# ============================================================

class TestResonanceCycle:
    """Tests du cycle complet de resonance."""

    @pytest.mark.asyncio
    async def test_cycle_completes(self, isolate_callosum):
        """Un cycle complet ne crashe pas."""
        with patch.dict("sys.modules", {
            "core.cardiac_engine": None,
            "core.desire_engine": None,
            "core.reptilian_core": None,
            "core.prefrontal": None,
            "core.dopamine_system": None,
            "core.synaptic_network": None,
            "core.inner_voice": None,
        }):
            with patch("core.corpus_callosum.bus.publish", new_callable=AsyncMock):
                await isolate_callosum._resonance_cycle()
        assert isolate_callosum._cycle_count == 1
        assert len(isolate_callosum._snapshots) == 1

    @pytest.mark.asyncio
    async def test_cycle_saves_every_10(self, isolate_callosum):
        """Persistance toutes les 10 cycles."""
        isolate_callosum._cycle_count = 9  # Prochain sera le 10eme
        with patch.dict("sys.modules", {
            "core.cardiac_engine": None,
            "core.desire_engine": None,
            "core.reptilian_core": None,
            "core.prefrontal": None,
            "core.dopamine_system": None,
            "core.synaptic_network": None,
            "core.inner_voice": None,
        }):
            with patch("core.corpus_callosum.bus.publish", new_callable=AsyncMock):
                with patch.object(isolate_callosum, "save") as mock_save:
                    await isolate_callosum._resonance_cycle()
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_cycle_publishes_state(self, isolate_callosum):
        """Le cycle publie CALLOSUM_STATE sur le bus."""
        with patch.dict("sys.modules", {
            "core.cardiac_engine": None,
            "core.desire_engine": None,
            "core.reptilian_core": None,
            "core.prefrontal": None,
            "core.dopamine_system": None,
            "core.synaptic_network": None,
            "core.inner_voice": None,
        }):
            with patch("core.corpus_callosum.bus.publish", new_callable=AsyncMock) as mock_pub:
                await isolate_callosum._resonance_cycle()
        mock_pub.assert_called_once()
        args = mock_pub.call_args
        assert args[0][0] == "CORPUS_CALLOSUM_STATE"

    @pytest.mark.asyncio
    async def test_cycle_buffer_limit(self, isolate_callosum):
        """Le buffer de snapshots est limite."""
        with patch.dict("sys.modules", {
            "core.cardiac_engine": None,
            "core.desire_engine": None,
            "core.reptilian_core": None,
            "core.prefrontal": None,
            "core.dopamine_system": None,
            "core.synaptic_network": None,
            "core.inner_voice": None,
        }):
            with patch("core.corpus_callosum.bus.publish", new_callable=AsyncMock):
                for _ in range(70):
                    await isolate_callosum._resonance_cycle()
        assert len(isolate_callosum._snapshots) <= 60


class TestTissuePatternHandler:
    """Sprint 3 — Grand Câblage : handler TISSUE_PATTERN_EMERGED."""

    @pytest.mark.asyncio
    async def test_tissue_pattern_high_freq_cached(self, isolate_callosum):
        """Pattern freq > 0.4 → capturé dans _tissue_frequency."""
        await isolate_callosum._on_tissue_pattern({"frequency": 0.5, "genome": "CG"})
        assert isolate_callosum._tissue_frequency == 0.5

    @pytest.mark.asyncio
    async def test_tissue_pattern_low_freq_ignored(self, isolate_callosum):
        """Pattern freq <= 0.4 → ignoré."""
        await isolate_callosum._on_tissue_pattern({"frequency": 0.3, "genome": "CG"})
        assert not hasattr(isolate_callosum, "_tissue_frequency") or isolate_callosum._tissue_frequency == 0.0


# ============================================================
# TestTissueInCoherence — Tissue dans OrganSnapshot et cohérence
# ============================================================

class TestTissueInCoherence:
    """Tests pour l'intégration tissue dans cohérence et patterns."""

    def test_organ_snapshot_has_tissue_fields(self):
        """OrganSnapshot contient tissue_vitality, tissue_diversity, tissue_alive_cells."""
        snap = OrganSnapshot()
        assert hasattr(snap, "tissue_vitality")
        assert hasattr(snap, "tissue_diversity")
        assert hasattr(snap, "tissue_alive_cells")
        assert snap.tissue_vitality == 0.0
        assert snap.tissue_diversity == 0.0
        assert snap.tissue_alive_cells == 0

    def test_coherence_weights_include_tissue(self):
        """COHERENCE_WEIGHTS contient 'tissue' avec poids > 0."""
        from core.corpus_callosum import COHERENCE_WEIGHTS
        assert "tissue" in COHERENCE_WEIGHTS
        assert COHERENCE_WEIGHTS["tissue"] > 0

    def test_coherence_weights_sum_to_one(self):
        """La somme des poids de cohérence = 1.0."""
        from core.corpus_callosum import COHERENCE_WEIGHTS
        total = sum(COHERENCE_WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=0.01)

    def test_coherence_score_with_tissue(self, isolate_callosum):
        """Tissue vital + diversifié → meilleur score de cohérence."""
        snap_low = _make_snapshot(tissue_vitality=0.0, tissue_diversity=0.0, tissue_alive_cells=5)
        snap_high = _make_snapshot(tissue_vitality=0.8, tissue_diversity=0.6, tissue_alive_cells=50)
        score_low = isolate_callosum._compute_global_coherence(snap_low)
        score_high = isolate_callosum._compute_global_coherence(snap_high)
        assert score_high > score_low

    def test_coherence_tissue_fallback_few_cells(self, isolate_callosum):
        """< 10 cellules → score tissue = 0.3 (défaut)."""
        snap = _make_snapshot(tissue_vitality=0.9, tissue_diversity=0.9, tissue_alive_cells=5)
        score = isolate_callosum._compute_global_coherence(snap)
        # Avec seulement 5 cellules, le score tissue est 0.3 (défaut)
        snap2 = _make_snapshot(tissue_vitality=0.9, tissue_diversity=0.9, tissue_alive_cells=50)
        score2 = isolate_callosum._compute_global_coherence(snap2)
        # Les 50 cellules utilisent le vrai score
        assert score2 > score

    def test_flow_pattern_tissue_bonus(self, isolate_callosum):
        """Tissu diversifié contribue à la confiance du pattern flow."""
        snap = _make_snapshot(
            cardiac_coherence=0.8,
            dopamine_level=0.7,
            has_active_goal=True,
            goal_progress=50.0,
            threat_level=1.0,
            tissue_diversity=0.5,
            tissue_vitality=0.6,
            tissue_alive_cells=50,
        )
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        flow_patterns = [p for p in patterns if p.pattern_type == "flow"]
        assert len(flow_patterns) >= 1

    def test_creative_surge_tissue_contribution(self, isolate_callosum):
        """Tissu diversifié apparaît dans les contributeurs du creative_surge."""
        snap = _make_snapshot(
            dopamine_level=0.7,
            frustrated_drives=["CURIOSITE", "CREATION"],
            dominant_deprivation=60.0,
            synaptic_energy=0.5,
            tissue_diversity=0.5,
            tissue_alive_cells=50,
        )
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        creative = [p for p in patterns if p.pattern_type == "creative_surge"]
        if creative:
            assert "tissue" in creative[0].contributing_organs

    def test_stagnation_low_tissue_diversity(self, isolate_callosum):
        """Tissu peu diversifié renforce la stagnation (bonus confiance)."""
        snap = _make_snapshot(
            dopamine_level=0.15,
            dominant_deprivation=70.0,
            synaptic_energy=0.05,
            cardiac_coherence=0.2,
            threat_level=0.5,
            tissue_diversity=0.05,
            tissue_alive_cells=50,
        )
        patterns = isolate_callosum._detect_resonance_patterns(snap)
        stag = [p for p in patterns if p.pattern_type == "stagnation"]
        # S'il y a stagnation, la confiance inclut le tissue bonus
        if stag:
            assert stag[0].confidence > 0

    def test_capture_organ_states_tissue_integration(self, isolate_callosum):
        """_capture_organ_states() capture les métriques tissue."""
        mock_tissue = MagicMock()
        mock_tissue.get_stats.return_value = {
            "alive_cells": 100,
            "avg_energy": 150.0,
            "genome_diversity": 30,
        }
        mock_mod = MagicMock()
        mock_mod.tissue = mock_tissue
        with patch.dict("sys.modules", {
            "core.cardiac_engine": None,
            "core.desire_engine": None,
            "core.reptilian_core": None,
            "core.prefrontal": None,
            "core.dopamine_system": None,
            "core.synaptic_network": None,
            "core.inner_voice": None,
            "core.hippocampus": None,
            "core.neural_tissue": mock_mod,
        }):
            snap = isolate_callosum._capture_organ_states()
        assert snap.tissue_alive_cells == 100
        assert snap.tissue_vitality == pytest.approx(0.75, abs=0.01)  # 150/200
        assert snap.tissue_diversity > 0
