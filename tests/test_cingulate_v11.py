"""Tests V11.0 (Phase 12C - 2026-04-21) : Cingulate - 3 pistes validées.

Contexte : audit révéle que le cingulate émettait 59071 CINGULATE_CONFLICT
pour 1910 sélections (ratio 31x). Chaque divergence mineure entre 2
couches du scoring (ex: amygdale +0.5 vs ganglions -1.0) déclenchait
une inhibition downstream dans les ganglions. Maladie auto-immune cognitive.

V11.0 corrige 3 pathologies (validation Gemini) :
 - Piste C : decay_adaptations orpheline → connectée au cardiac_beat
 - Piste D : gate de sévérité 2.5 à la publication bus (vs 1.5 détection)
 - Piste E : persistance cingulate_state périodique (fix amnésie)
"""
import asyncio
import pytest
from unittest.mock import patch, AsyncMock
from core.cingulate_cortex import (
    CingulateCortex,
    CONFLICT_THRESHOLD, CONFLICT_PUBLISH_SEVERITY,
    ADAPTATION_DECAY_INTERVAL, CINGULATE_PERSIST_INTERVAL,
    ADAPTATION_DECAY, MAX_ADAPTATION_MALUS,
)


@pytest.fixture
def fresh_cingulate():
    CingulateCortex.reset_singleton()
    c = CingulateCortex()
    c.decision_history.clear()
    c.conflict_log.clear()
    c.error_memory.clear()
    c.adaptation_weights.clear()
    c.total_conflicts = 0
    c.total_errors = 0
    c._conflict_cooldowns.clear()
    c._active_conflicts.clear()
    c._cardiac_ticks = 0
    yield c
    CingulateCortex.reset_singleton()


# ═══════════════════════════════════════════════════════════════════════
# Piste D : Gate de sévérité à la publication
# ═══════════════════════════════════════════════════════════════════════


class TestSeverityGate:
    """CONFLICT_PUBLISH_SEVERITY=2.5 filtre les conflits mineurs."""

    def test_publish_threshold_higher_than_detection(self):
        """Le seuil de publication (2.5) doit être > seuil de détection (1.5).
        Sinon, pas de filtrage."""
        assert CONFLICT_PUBLISH_SEVERITY > CONFLICT_THRESHOLD

    @pytest.mark.asyncio
    async def test_minor_conflict_not_published(self, fresh_cingulate):
        """Conflit sévérité 2.0 (< 2.5) → log interne mais pas publié."""
        # Construire manuellement un event avec breakdown
        event = {
            "intent": "TEST_INTENT",
            "status": "success",
            "scoring_breakdown": {
                "amygdala": 1.0,     # positif
                "ganglia": -1.0,     # négatif
                # écart 2.0 > seuil détection 1.5 → conflict détecté
                # mais 2.0 < 2.5 → pas publié
            },
        }
        with patch("core.cingulate_cortex.bus.publish", new=AsyncMock()) as mock_pub:
            # Injecter 2 décisions identiques pour déclencher détection
            await fresh_cingulate._on_routine_complete(event)
            await fresh_cingulate._on_routine_complete(event)

        # Vérifier qu'aucun CINGULATE_CONFLICT n'a été publié
        cingulate_publishes = [
            c for c in mock_pub.call_args_list
            if c[0] and c[0][0] == "CINGULATE_CONFLICT"
        ]
        assert len(cingulate_publishes) == 0, (
            f"Conflit mineur (severity 2.0) publié à tort : {cingulate_publishes}"
        )
        # Mais log interne a reçu le conflit
        assert fresh_cingulate.total_conflicts >= 1, (
            "Log interne doit toujours compter le conflit pour stats"
        )

    @pytest.mark.asyncio
    async def test_severe_conflict_is_published(self, fresh_cingulate):
        """Conflit sévérité 3.0 (>= 2.5) → publié normalement."""
        event = {
            "intent": "DANGER",
            "status": "success",
            "scoring_breakdown": {
                "amygdala": 1.5,
                "ganglia": -1.5,  # écart 3.0 >= 2.5 → publié
            },
        }
        with patch("core.cingulate_cortex.bus.publish", new=AsyncMock()) as mock_pub:
            await fresh_cingulate._on_routine_complete(event)
            await fresh_cingulate._on_routine_complete(event)

        cingulate_publishes = [
            c for c in mock_pub.call_args_list
            if c[0] and c[0][0] == "CINGULATE_CONFLICT"
        ]
        assert len(cingulate_publishes) >= 1, (
            "Conflit sévère (severity 3.0) doit être publié"
        )

    @pytest.mark.asyncio
    async def test_log_always_captures_even_minor(self, fresh_cingulate):
        """Même les conflits mineurs doivent être dans conflict_log (stats)."""
        event = {
            "intent": "MINOR",
            "status": "success",
            "scoring_breakdown": {"a": 1.0, "b": -1.0},  # severity 2.0
        }
        with patch("core.cingulate_cortex.bus.publish", new=AsyncMock()):
            await fresh_cingulate._on_routine_complete(event)
            await fresh_cingulate._on_routine_complete(event)

        # Log interne non vide
        assert len(fresh_cingulate.conflict_log) >= 1


# ═══════════════════════════════════════════════════════════════════════
# Piste C : decay_adaptations connecté au cardiac_beat
# ═══════════════════════════════════════════════════════════════════════


class TestDecayAdaptations:
    """V11.0 : decay_adaptations est déclenché tous les 100 beats."""

    @pytest.mark.asyncio
    async def test_decay_triggered_every_100_beats(self, fresh_cingulate):
        """ADAPTATION_DECAY_INTERVAL=100 beats → 1 decay exact."""
        # Setup : populated adaptations
        fresh_cingulate.adaptation_weights = {
            "FAILING_INTENT": 0.8,  # max
            "WEAK": 0.05,
        }

        with patch.object(fresh_cingulate, 'decay_adaptations') as mock_decay:
            for _ in range(ADAPTATION_DECAY_INTERVAL):
                await fresh_cingulate._on_cardiac_beat({})
        assert mock_decay.call_count == 1

    @pytest.mark.asyncio
    async def test_no_decay_before_interval(self, fresh_cingulate):
        """< INTERVAL beats → 0 decay."""
        with patch.object(fresh_cingulate, 'decay_adaptations') as mock_decay:
            for _ in range(ADAPTATION_DECAY_INTERVAL - 1):
                await fresh_cingulate._on_cardiac_beat({})
        assert mock_decay.call_count == 0

    def test_decay_reduces_adaptation_weight(self, fresh_cingulate):
        """decay_adaptations applique bien ADAPTATION_DECAY."""
        fresh_cingulate.adaptation_weights["A"] = 0.5
        fresh_cingulate.decay_adaptations()
        expected = 0.5 * ADAPTATION_DECAY
        assert fresh_cingulate.adaptation_weights["A"] == pytest.approx(expected, abs=0.001)

    def test_decay_removes_negligible_adaptations(self, fresh_cingulate):
        """Adaptation < 0.01 → supprimée."""
        fresh_cingulate.adaptation_weights["NEG"] = 0.005
        fresh_cingulate.decay_adaptations()
        assert "NEG" not in fresh_cingulate.adaptation_weights


# ═══════════════════════════════════════════════════════════════════════
# Piste E : Persistance périodique
# ═══════════════════════════════════════════════════════════════════════


class TestPeriodicPersistence:
    """V11.0 : _save() déclenché tous les 500 beats (~4h)."""

    @pytest.mark.asyncio
    async def test_save_triggered_every_persist_interval(self, fresh_cingulate):
        """CINGULATE_PERSIST_INTERVAL=500 beats → 1 save exact."""
        with patch.object(fresh_cingulate, '_save') as mock_save:
            for _ in range(CINGULATE_PERSIST_INTERVAL):
                await fresh_cingulate._on_cardiac_beat({})
        assert mock_save.call_count == 1

    @pytest.mark.asyncio
    async def test_save_and_decay_both_fire_at_multiple(self, fresh_cingulate):
        """Au 500e beat, decay (multiple de 100) ET save (multiple de 500)."""
        with patch.object(fresh_cingulate, 'decay_adaptations') as mock_decay, \
             patch.object(fresh_cingulate, '_save') as mock_save:
            for _ in range(CINGULATE_PERSIST_INTERVAL):
                await fresh_cingulate._on_cardiac_beat({})
        # 5 decays (à 100, 200, 300, 400, 500) + 1 save
        assert mock_decay.call_count == 5
        assert mock_save.call_count == 1

    @pytest.mark.asyncio
    async def test_persist_interval_higher_than_decay(self):
        """Sanity : persistance moins fréquente que decay."""
        assert CINGULATE_PERSIST_INTERVAL > ADAPTATION_DECAY_INTERVAL


# ═══════════════════════════════════════════════════════════════════════
# Non-régression : détection interne toujours active
# ═══════════════════════════════════════════════════════════════════════


class TestDetectionStillWorks:
    """V11.0 ne casse pas la détection interne (seulement filtre la publication)."""

    @pytest.mark.asyncio
    async def test_minor_conflict_still_detected(self, fresh_cingulate):
        """Conflit mineur (severity > 1.5 mais < 2.5) : détecté + loggé,
        pas publié."""
        event = {
            "intent": "MINOR",
            "status": "success",
            "scoring_breakdown": {"a": 1.0, "b": -1.0},  # severity 2.0
        }
        with patch("core.cingulate_cortex.bus.publish", new=AsyncMock()):
            await fresh_cingulate._on_routine_complete(event)
            await fresh_cingulate._on_routine_complete(event)

        # Compteur interne incrémenté
        assert fresh_cingulate.total_conflicts >= 1
        # Log interne contient l'entrée
        assert len(fresh_cingulate.conflict_log) >= 1

    @pytest.mark.asyncio
    async def test_error_recording_unchanged(self, fresh_cingulate):
        """Les erreurs sont toujours enregistrées (non impactées par gate)."""
        event = {
            "intent": "FAIL",
            "status": "error",
            "scoring_breakdown": {},
            "reason": "test",
        }
        with patch("core.cingulate_cortex.bus.publish", new=AsyncMock()):
            await fresh_cingulate._on_routine_complete(event)

        assert fresh_cingulate.total_errors == 1
        assert "FAIL" in fresh_cingulate.adaptation_weights
