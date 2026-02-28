"""
Tests du cycle circadien — CircadianRhythm.
"""

import asyncio
import json
import os
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import core.circadian_rhythm as mod
from core.event_bus.bus import bus
from core.circadian_rhythm import (
    CircadianRhythm, SleepReport,
    PHASE_EVEIL, PHASE_CREPUSCULE, PHASE_SOMMEIL, PHASE_AUBE,
    MIN_PHASE_DURATION, MIN_CREPUSCULE_DURATION, MIN_AUBE_DURATION,
    SLEEP_TASKS, SLEEP_MULTIPLIER, BPM_TARGET,
    CPU_PRESSURE_THRESHOLD, RAM_PRESSURE_THRESHOLD,
    THREAT_PRESSURE_THRESHOLD,
)


# ============================================================
# Fixture autouse — isolation complète
# ============================================================

@pytest.fixture(autouse=True)
def isolate_circadian(tmp_path, monkeypatch):
    """Chaque test obtient un circadien frais et isolé."""
    CircadianRhythm.reset_singleton()
    monkeypatch.setattr(mod, "CIRCADIAN_STATE_FILE", str(tmp_path / "circadian.json"))
    with patch.object(CircadianRhythm, "_load"):
        c = CircadianRhythm()
        mod.circadian = c
    yield c
    CircadianRhythm.reset_singleton()


# ============================================================
# Helpers
# ============================================================

def _force_phase(c: CircadianRhythm, phase: str, elapsed: float = 400):
    """Force le circadien dans une phase donnée avec le temps écoulé."""
    c.phase = phase
    c._phase_since = time.time() - elapsed


def _health(cpu=30, ram=40):
    """Crée un dict health minimal."""
    return {"cpu_percent": cpu, "ram_percent": ram}


# ============================================================
# TestSingleton
# ============================================================

class TestSingleton:
    def test_singleton_identity(self, isolate_circadian):
        """Même objet retourné."""
        with patch.object(CircadianRhythm, "_load"):
            c2 = CircadianRhythm()
        assert c2 is isolate_circadian

    def test_reset_singleton(self, isolate_circadian):
        """Reset crée une nouvelle instance."""
        CircadianRhythm.reset_singleton()
        with patch.object(CircadianRhythm, "_load"):
            c2 = CircadianRhythm()
        assert c2 is not isolate_circadian

    def test_initial_phase_eveil(self, isolate_circadian):
        """Phase initiale = eveil."""
        assert isolate_circadian.phase == PHASE_EVEIL

    def test_stats_initial(self, isolate_circadian):
        """Stats par défaut saines."""
        stats = isolate_circadian.get_stats()
        assert stats["phase"] == PHASE_EVEIL
        assert stats["total_sleep_cycles"] == 0
        assert isinstance(stats["stats"], dict)
        assert stats["sleep_multiplier"] == 1.0
        assert stats["bpm_target"] is None


# ============================================================
# TestPhaseTransitions
# ============================================================

class TestPhaseTransitions:
    def test_eveil_to_crepuscule_budget_exhausted(self, isolate_circadian):
        """Budget exhausted → crépuscule."""
        _force_phase(isolate_circadian, PHASE_EVEIL)
        result = isolate_circadian._evaluate_transition("exhausted", _health())
        assert result == PHASE_CREPUSCULE

    def test_eveil_to_crepuscule_resource_pressure(self, isolate_circadian):
        """CPU + RAM élevés → crépuscule."""
        _force_phase(isolate_circadian, PHASE_EVEIL)
        result = isolate_circadian._evaluate_transition("full", _health(cpu=90, ram=85))
        assert result == PHASE_CREPUSCULE

    def test_eveil_to_crepuscule_threat_level(self, isolate_circadian):
        """Threat level >= 5.0 → crépuscule."""
        _force_phase(isolate_circadian, PHASE_EVEIL)
        mock_reptilian = MagicMock()
        mock_reptilian.should_freeze.return_value = False
        mock_reptilian.threat_level = 6.0
        with patch.dict("sys.modules", {"core.reptilian_core": MagicMock(reptilian=mock_reptilian)}):
            result = isolate_circadian._evaluate_transition("full", _health())
        assert result == PHASE_CREPUSCULE

    def test_no_transition_before_min_duration(self, isolate_circadian):
        """Anti-flip-flop : pas de transition avant MIN_PHASE_DURATION."""
        isolate_circadian.phase = PHASE_EVEIL
        isolate_circadian._phase_since = time.time()  # Tout juste créé
        result = isolate_circadian._evaluate_transition("exhausted", _health())
        assert result is None

    def test_crepuscule_to_sommeil(self, isolate_circadian):
        """Crépuscule → sommeil après durée minimum."""
        _force_phase(isolate_circadian, PHASE_CREPUSCULE, MIN_CREPUSCULE_DURATION + 100)
        mock_autonomy = MagicMock()
        mock_autonomy.is_processing = False
        with patch.dict("sys.modules", {"core.autonomy_engine": MagicMock(autonomy=mock_autonomy)}):
            result = isolate_circadian._evaluate_transition("exhausted", _health())
        assert result == PHASE_SOMMEIL

    def test_sommeil_to_aube_tasks_done(self, isolate_circadian):
        """Sommeil → aube quand toutes les tâches sont faites."""
        _force_phase(isolate_circadian, PHASE_SOMMEIL)
        isolate_circadian._current_sleep_task_index = len(SLEEP_TASKS)  # Toutes terminées
        result = isolate_circadian._evaluate_transition("exhausted", _health())
        assert result == PHASE_AUBE

    def test_sommeil_to_aube_budget_reset(self, isolate_circadian):
        """Sommeil → aube quand le budget est reset (nouveau jour)."""
        _force_phase(isolate_circadian, PHASE_SOMMEIL)
        isolate_circadian._current_sleep_task_index = 1  # Pas toutes terminées
        result = isolate_circadian._evaluate_transition("full", _health())
        assert result == PHASE_AUBE

    def test_aube_to_eveil(self, isolate_circadian):
        """Aube → éveil après MIN_PHASE_DURATION + budget ok."""
        _force_phase(isolate_circadian, PHASE_AUBE, MIN_PHASE_DURATION + 100)
        result = isolate_circadian._evaluate_transition("full", _health())
        assert result == PHASE_EVEIL

    def test_freeze_blocks_transitions(self, isolate_circadian):
        """FREEZE reptilien suspend toutes les transitions."""
        _force_phase(isolate_circadian, PHASE_EVEIL)
        mock_reptilian = MagicMock()
        mock_reptilian.should_freeze.return_value = True
        with patch.dict("sys.modules", {"core.reptilian_core": MagicMock(reptilian=mock_reptilian)}):
            result = isolate_circadian._evaluate_transition("exhausted", _health())
        assert result is None

    @pytest.mark.asyncio
    async def test_full_cycle(self, isolate_circadian):
        """Cycle complet ÉVEIL → CRÉPUSCULE → SOMMEIL → AUBE → ÉVEIL."""
        c = isolate_circadian
        assert c.phase == PHASE_EVEIL

        # ÉVEIL → CRÉPUSCULE
        await c._transition_to(PHASE_CREPUSCULE, "budget_exhausted")
        assert c.phase == PHASE_CREPUSCULE

        # CRÉPUSCULE → SOMMEIL
        await c._transition_to(PHASE_SOMMEIL, "durée minimale atteinte")
        assert c.phase == PHASE_SOMMEIL
        assert c._sleep_report is not None
        assert c._current_sleep_task_index == 0

        # SOMMEIL → AUBE
        c._current_sleep_task_index = len(SLEEP_TASKS)
        await c._transition_to(PHASE_AUBE, "maintenance terminée")
        assert c.phase == PHASE_AUBE
        assert c._last_sleep_report is not None
        assert c._total_sleep_cycles == 1

        # AUBE → ÉVEIL
        await c._transition_to(PHASE_EVEIL, "warmup terminé")
        assert c.phase == PHASE_EVEIL


# ============================================================
# TestShouldAllowRoutine
# ============================================================

class TestShouldAllowRoutine:
    def test_eveil_allows_all(self, isolate_circadian):
        """Éveil autorise tout."""
        isolate_circadian.phase = PHASE_EVEIL
        ok, reason = isolate_circadian.should_allow_routine("EXPANSION_CODE", 5)
        assert ok is True
        assert reason == ""

    def test_crepuscule_blocks_expensive(self, isolate_circadian):
        """Crépuscule bloque > 2pts."""
        isolate_circadian.phase = PHASE_CREPUSCULE
        ok, reason = isolate_circadian.should_allow_routine("EXPANSION_CODE", 5)
        assert ok is False
        assert "crepuscule" in reason

    def test_sommeil_blocks_all(self, isolate_circadian):
        """Sommeil bloque tout."""
        isolate_circadian.phase = PHASE_SOMMEIL
        ok, reason = isolate_circadian.should_allow_routine("AUDIT_STRUCTURE", 1)
        assert ok is False
        assert "sommeil" in reason

    def test_aube_blocks_expensive(self, isolate_circadian):
        """Aube bloque > 3pts."""
        isolate_circadian.phase = PHASE_AUBE
        ok, reason = isolate_circadian.should_allow_routine("COUNCIL_DEBATE", 12)
        assert ok is False
        assert "aube" in reason

    def test_aube_allows_cheap(self, isolate_circadian):
        """Aube autorise ≤ 3pts."""
        isolate_circadian.phase = PHASE_AUBE
        ok, reason = isolate_circadian.should_allow_routine("SECURITY_AUDIT", 2)
        assert ok is True

    def test_crepuscule_allows_free(self, isolate_circadian):
        """Crépuscule autorise 0-LLM (cost ≤ 2)."""
        isolate_circadian.phase = PHASE_CREPUSCULE
        ok, reason = isolate_circadian.should_allow_routine("NEURAL_COMPILE", 0)
        assert ok is True


# ============================================================
# TestSleepTasks
# ============================================================

class TestSleepTasks:
    @pytest.mark.asyncio
    async def test_execute_sequence(self, isolate_circadian):
        """Les tâches s'exécutent dans l'ordre."""
        c = isolate_circadian
        c.phase = PHASE_SOMMEIL
        c._sleep_report = SleepReport(started_at=time.time())
        c._current_sleep_task_index = 0

        executed = []
        for task_name in SLEEP_TASKS:
            async def mock_handler(circ, _name=task_name):
                executed.append(_name)
                return "ok"
            with patch.dict(mod._SLEEP_TASK_HANDLERS, {task_name: mock_handler}):
                result = await c.execute_next_sleep_task()
                assert result is not None
                assert result["success"] is True

        assert executed == SLEEP_TASKS

    @pytest.mark.asyncio
    async def test_dream_consolidation(self, isolate_circadian):
        """Appelle cortex.dream_consolidation()."""
        c = isolate_circadian
        c.phase = PHASE_SOMMEIL
        c._sleep_report = SleepReport(started_at=time.time())
        c._current_sleep_task_index = 0

        mock_cortex = MagicMock()
        mock_cortex.dream_consolidation.return_value = {"new_connections": 5, "pruned": 3}
        with patch.dict("sys.modules", {"core.synaptic_network": MagicMock(cortex=mock_cortex)}):
            result = await c.execute_next_sleep_task()
        assert result["success"] is True
        assert c._sleep_report.dream_connections == 5
        assert c._sleep_report.pruned_synapses == 3

    @pytest.mark.asyncio
    async def test_hippocampus_consolidation(self, isolate_circadian):
        """Appelle hippocampus.consolidate()."""
        c = isolate_circadian
        c.phase = PHASE_SOMMEIL
        c._sleep_report = SleepReport(started_at=time.time())
        c._current_sleep_task_index = 1  # Skip dream, go to hippocampus

        mock_hippo = MagicMock()
        mock_hippo.consolidate.return_value = None
        mock_hippo._arcs = [1, 2, 3]
        mock_hippo._episodes = []
        mock_hippo._save = MagicMock()
        with patch.dict("sys.modules", {"core.hippocampus": MagicMock(hippocampus=mock_hippo)}):
            result = await c.execute_next_sleep_task()
        assert result["success"] is True
        mock_hippo.consolidate.assert_called_once()

    @pytest.mark.asyncio
    async def test_chromadb_cleanup(self, isolate_circadian):
        """Appelle purge_expired + purge_low_quality."""
        c = isolate_circadian
        c.phase = PHASE_SOMMEIL
        c._sleep_report = SleepReport(started_at=time.time())
        c._current_sleep_task_index = 2  # chromadb_cleanup

        mock_mgr = AsyncMock()
        mock_mgr.async_purge_expired = AsyncMock(return_value=10)
        mock_mgr.async_purge_low_quality = AsyncMock(return_value=3)
        mock_class = MagicMock()
        mock_class._instances = {"default": mock_mgr}
        with patch.dict("sys.modules", {"core.vector_store": MagicMock(ChromaMemoryManager=mock_class)}):
            result = await c.execute_next_sleep_task()
        assert result["success"] is True
        assert c._sleep_report.chromadb_removed == 13

    @pytest.mark.asyncio
    async def test_neural_compile(self, isolate_circadian):
        """Appelle compiler.compile_rules()."""
        c = isolate_circadian
        c.phase = PHASE_SOMMEIL
        c._sleep_report = SleepReport(started_at=time.time())
        c._current_sleep_task_index = 3  # neural_compile

        mock_compiler = MagicMock()
        mock_compiler.compile_rules.return_value = 7
        with patch.dict("sys.modules", {"core.neural_compiler": MagicMock(compiler=mock_compiler)}):
            result = await c.execute_next_sleep_task()
        assert result["success"] is True
        assert c._sleep_report.rules_compiled == 7

    @pytest.mark.asyncio
    async def test_grimoire_harvest(self, isolate_circadian):
        """Filtre specs deployed et marque grimoire_candidate."""
        c = isolate_circadian
        c.phase = PHASE_SOMMEIL
        c._sleep_report = SleepReport(started_at=time.time())
        c._current_sleep_task_index = 4  # grimoire_harvest

        from datetime import datetime
        mock_spec = MagicMock()
        mock_spec.status = "deployed"
        mock_spec.deployed_at = datetime.now().isoformat()
        mock_spec.grimoire_candidate = False

        mock_catalog = MagicMock()
        mock_catalog.specs = {"test_spec": mock_spec}
        mock_catalog._save = MagicMock()

        with patch.dict("sys.modules", {"core.evolution_catalog": MagicMock(EvolutionCatalog=MagicMock(return_value=mock_catalog))}):
            result = await c.execute_next_sleep_task()
        assert result["success"] is True
        assert c._sleep_report.grimoire_harvested == 1

    @pytest.mark.asyncio
    async def test_all_done_returns_none(self, isolate_circadian):
        """None après toutes les tâches."""
        c = isolate_circadian
        c.phase = PHASE_SOMMEIL
        c._current_sleep_task_index = len(SLEEP_TASKS)
        result = await c.execute_next_sleep_task()
        assert result is None

    @pytest.mark.asyncio
    async def test_task_failure_continues(self, isolate_circadian):
        """Skip tâche échouée, passe à la suivante."""
        c = isolate_circadian
        c.phase = PHASE_SOMMEIL
        c._sleep_report = SleepReport(started_at=time.time())
        c._current_sleep_task_index = 0

        async def failing_handler(circ):
            raise RuntimeError("Boom")

        async def ok_handler(circ):
            return "ok"

        # Première tâche échoue
        with patch.dict(mod._SLEEP_TASK_HANDLERS, {"dream_consolidation": failing_handler}):
            r1 = await c.execute_next_sleep_task()
        assert r1["success"] is False
        assert "dream_consolidation" in c._sleep_report.tasks_failed

        # Deuxième tâche réussit
        with patch.dict(mod._SLEEP_TASK_HANDLERS, {"hippocampus_consolidation": ok_handler}):
            r2 = await c.execute_next_sleep_task()
        assert r2["success"] is True
        assert "hippocampus_consolidation" in c._sleep_report.tasks_completed

    @pytest.mark.asyncio
    async def test_sleep_report_populated(self, isolate_circadian):
        """Le rapport est bien rempli après un cycle complet."""
        c = isolate_circadian
        await c._transition_to(PHASE_CREPUSCULE, "test")
        await c._transition_to(PHASE_SOMMEIL, "test")
        assert c._sleep_report is not None
        assert c._sleep_report.started_at > 0
        assert c._sleep_report.trigger_reason == "test"

    @pytest.mark.asyncio
    async def test_report_preserved_after_aube(self, isolate_circadian):
        """Le rapport reste accessible après transition vers aube."""
        c = isolate_circadian
        await c._transition_to(PHASE_CREPUSCULE, "test")
        await c._transition_to(PHASE_SOMMEIL, "test")
        c._sleep_report.tasks_completed = ["dream_consolidation"]
        c._current_sleep_task_index = len(SLEEP_TASKS)
        await c._transition_to(PHASE_AUBE, "maintenance terminée")
        assert c._last_sleep_report is not None
        assert "dream_consolidation" in c._last_sleep_report.tasks_completed


# ============================================================
# TestDawnRecap
# ============================================================

class TestDawnRecap:
    def test_empty_if_no_sleep(self, isolate_circadian):
        """Vide si pas de sommeil précédent."""
        assert isolate_circadian.get_dawn_recap() == ""

    def test_with_report(self, isolate_circadian):
        """Contient les stats du rapport."""
        isolate_circadian._last_sleep_report = SleepReport(
            started_at=time.time() - 600,
            ended_at=time.time(),
            tasks_completed=["dream_consolidation", "neural_compile"],
            tasks_failed=["chromadb_cleanup"],
            dream_connections=12,
            pruned_synapses=4,
            rules_compiled=5,
            trigger_reason="budget_exhausted",
        )
        recap = isolate_circadian.get_dawn_recap()
        assert "10 min" in recap
        assert "dream_consolidation" in recap
        assert "chromadb_cleanup" in recap
        assert "12 connexions" in recap
        assert "5 règles" in recap

    def test_format_readable(self, isolate_circadian):
        """Le format est lisible (multi-lignes)."""
        isolate_circadian._last_sleep_report = SleepReport(
            started_at=time.time() - 300,
            ended_at=time.time(),
            tasks_completed=["dream_consolidation"],
            trigger_reason="test",
        )
        recap = isolate_circadian.get_dawn_recap()
        assert "[RÉVEIL]" in recap
        lines = recap.strip().split("\n")
        assert len(lines) >= 2


# ============================================================
# TestSleepMultiplier + BPMTarget
# ============================================================

class TestSleepMultiplierBPMTarget:
    def test_multiplier_eveil(self, isolate_circadian):
        isolate_circadian.phase = PHASE_EVEIL
        assert isolate_circadian.get_sleep_multiplier() == 1.0

    def test_multiplier_sommeil(self, isolate_circadian):
        isolate_circadian.phase = PHASE_SOMMEIL
        assert isolate_circadian.get_sleep_multiplier() == 0.5

    def test_multiplier_crepuscule(self, isolate_circadian):
        isolate_circadian.phase = PHASE_CREPUSCULE
        assert isolate_circadian.get_sleep_multiplier() == 1.5

    def test_bpm_eveil_none(self, isolate_circadian):
        isolate_circadian.phase = PHASE_EVEIL
        assert isolate_circadian.get_bpm_target() is None

    def test_bpm_sommeil_42(self, isolate_circadian):
        isolate_circadian.phase = PHASE_SOMMEIL
        assert isolate_circadian.get_bpm_target() == 42.0

    def test_bpm_aube_50(self, isolate_circadian):
        isolate_circadian.phase = PHASE_AUBE
        assert isolate_circadian.get_bpm_target() == 50.0


# ============================================================
# TestBusAndPersistence
# ============================================================

class TestBusAndPersistence:
    @pytest.mark.asyncio
    async def test_phase_change_event_published(self, isolate_circadian):
        """Transition publie CIRCADIAN_PHASE_CHANGE."""
        events = []
        bus.subscribe("CIRCADIAN_PHASE_CHANGE", lambda data: events.append(data))
        await isolate_circadian._transition_to(PHASE_CREPUSCULE, "test")
        assert len(events) == 1
        assert events[0]["phase"] == PHASE_CREPUSCULE
        assert events[0]["previous_phase"] == PHASE_EVEIL
        assert events[0]["reason"] == "test"

    def test_subscribe_idempotent(self, isolate_circadian):
        """Double appel à init ne double pas les souscriptions."""
        isolate_circadian._subscribed = False
        isolate_circadian.init()
        isolate_circadian.init()
        assert isolate_circadian._subscribed is True

    def test_save_load_roundtrip(self, isolate_circadian, tmp_path, monkeypatch):
        """Sauvegarde et rechargement cohérent."""
        state_file = str(tmp_path / "circadian.json")
        monkeypatch.setattr(mod, "CIRCADIAN_STATE_FILE", state_file)

        isolate_circadian.phase = PHASE_CREPUSCULE
        isolate_circadian._total_sleep_cycles = 3
        isolate_circadian._last_sleep_report = SleepReport(
            started_at=100.0, ended_at=200.0,
            tasks_completed=["dream_consolidation"],
            trigger_reason="test",
        )
        isolate_circadian.save()

        assert os.path.exists(state_file)
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["phase"] == PHASE_CREPUSCULE
        assert data["total_sleep_cycles"] == 3
        assert data["last_sleep_report"]["trigger_reason"] == "test"

    def test_load_missing_file(self, tmp_path, monkeypatch):
        """Charge sans erreur si le fichier n'existe pas."""
        CircadianRhythm.reset_singleton()
        monkeypatch.setattr(mod, "CIRCADIAN_STATE_FILE", str(tmp_path / "absent.json"))
        c = CircadianRhythm()
        assert c.phase == PHASE_EVEIL

    def test_save_atomic(self, isolate_circadian, tmp_path, monkeypatch):
        """La sauvegarde utilise un fichier temporaire."""
        state_file = str(tmp_path / "circadian.json")
        monkeypatch.setattr(mod, "CIRCADIAN_STATE_FILE", state_file)
        isolate_circadian.save()
        # Le fichier final existe, pas de .tmp résiduel
        assert os.path.exists(state_file)
        assert not os.path.exists(state_file + ".tmp")

    def test_circadian_context_text(self, isolate_circadian):
        """get_circadian_context retourne du texte lisible."""
        isolate_circadian.phase = PHASE_SOMMEIL
        ctx = isolate_circadian.get_circadian_context()
        assert "sommeil profond" in ctx
        assert "Phase circadienne" in ctx

    @pytest.mark.asyncio
    async def test_not_sommeil_returns_none(self, isolate_circadian):
        """execute_next_sleep_task retourne None si pas en sommeil."""
        isolate_circadian.phase = PHASE_EVEIL
        result = await isolate_circadian.execute_next_sleep_task()
        assert result is None
