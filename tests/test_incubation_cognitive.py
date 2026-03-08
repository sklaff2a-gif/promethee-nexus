# tests/test_incubation_cognitive.py — Tests Incubation Cognitive
"""Tests pour le subconscient asynchrone (incubation cognitive)."""

import json
import time
import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from dataclasses import asdict

from core.incubation_cognitive import (
    IncubationCognitive,
    IncubatedProblem,
    EurekaEvent,
    INCUBATION_STATE_FILE,
    MAX_QUEUE_SIZE,
    EUREKA_THRESHOLD,
    PROBLEM_MAX_AGE_HOURS,
    MAX_PROCESSING_ATTEMPTS,
    SAVE_INTERVAL,
    EUREKA_HISTORY_MAX,
)


# --- Fixture autouse ---

@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.incubation_cognitive.INCUBATION_STATE_FILE",
        str(tmp_path / "incubation_state.json"),
    )
    IncubationCognitive.reset_singleton()
    with patch.object(IncubationCognitive, "_load"):
        inc = IncubationCognitive()
    yield inc
    IncubationCognitive.reset_singleton()


# ================================================================
# TestSingleton
# ================================================================

class TestSingleton:
    def test_singleton_identity(self, isolate):
        """Deux appels retournent le meme objet."""
        inc2 = IncubationCognitive()
        assert inc2 is isolate

    def test_reset_creates_new(self, isolate):
        """reset_singleton() cree une nouvelle instance."""
        old_id = id(isolate)
        IncubationCognitive.reset_singleton()
        with patch.object(IncubationCognitive, "_load"):
            new_inc = IncubationCognitive()
        assert id(new_inc) != old_id

    def test_reinit_preserves_state(self, isolate):
        """Re-init ne reinitialise pas l'etat."""
        isolate._total_eurekas = 42
        isolate.__init__()
        assert isolate._total_eurekas == 42


# ================================================================
# TestDepositProblem
# ================================================================

class TestDepositProblem:
    def test_basic_deposit(self, isolate):
        """Depot basique retourne un ID."""
        pid = isolate.deposit_problem("Test problem", "TEST_INTENT")
        assert pid is not None
        assert len(pid) == 8
        assert len(isolate._queue) == 1
        assert isolate._queue[0].text == "Test problem"
        assert isolate._queue[0].source_intent == "TEST_INTENT"

    def test_dedup_source_intent(self, isolate):
        """Deux depots avec le meme intent → deuxieme rejete."""
        pid1 = isolate.deposit_problem("Problem 1", "SAME_INTENT")
        pid2 = isolate.deposit_problem("Problem 2", "SAME_INTENT")
        assert pid1 is not None
        assert pid2 is None
        assert len(isolate._queue) == 1

    def test_different_intents_accepted(self, isolate):
        """Intents differents → les deux acceptes."""
        pid1 = isolate.deposit_problem("P1", "INTENT_A")
        pid2 = isolate.deposit_problem("P2", "INTENT_B")
        assert pid1 is not None
        assert pid2 is not None
        assert len(isolate._queue) == 2

    def test_queue_full_eviction(self, isolate):
        """Queue pleine → le plus ancien est evince."""
        for i in range(MAX_QUEUE_SIZE):
            isolate.deposit_problem(f"Problem {i}", f"INTENT_{i}")
        assert len(isolate._queue) == MAX_QUEUE_SIZE
        # Deposer un de plus
        first_intent = isolate._queue[0].source_intent
        pid = isolate.deposit_problem("New problem", "NEW_INTENT")
        assert pid is not None
        assert len(isolate._queue) == MAX_QUEUE_SIZE
        # Le premier a ete evince
        intents = [p.source_intent for p in isolate._queue]
        assert first_intent not in intents

    def test_text_truncation(self, isolate):
        """Texte trop long est tronque a 300 chars."""
        long_text = "x" * 500
        pid = isolate.deposit_problem(long_text, "TRUNC")
        assert len(isolate._queue[0].text) == 300

    def test_context_truncation(self, isolate):
        """Contexte trop long est tronque a 200 chars."""
        pid = isolate.deposit_problem("test", "CTX", context="y" * 300)
        assert len(isolate._queue[0].context) == 200

    def test_resolved_not_blocking_dedup(self, isolate):
        """Un probleme resolu ne bloque pas un nouveau depot du meme intent."""
        pid1 = isolate.deposit_problem("P1", "INTENT_A")
        isolate._queue[0].resolved = True
        pid2 = isolate.deposit_problem("P2", "INTENT_A")
        assert pid2 is not None
        assert len(isolate._queue) == 2


# ================================================================
# TestPurge
# ================================================================

class TestPurge:
    def test_old_purged(self, isolate):
        """Problemes > 48h sont purges."""
        old_time = time.time() - (PROBLEM_MAX_AGE_HOURS + 1) * 3600
        isolate._queue.append(IncubatedProblem(
            id="old1", text="old", source_intent="OLD",
            context="", deposited_at=old_time,
        ))
        isolate._queue.append(IncubatedProblem(
            id="new1", text="new", source_intent="NEW",
            context="", deposited_at=time.time(),
        ))
        isolate._purge_old()
        assert len(isolate._queue) == 1
        assert isolate._queue[0].id == "new1"

    def test_resolved_purged(self, isolate):
        """Problemes resolus sont purges."""
        isolate._queue.append(IncubatedProblem(
            id="r1", text="resolved", source_intent="RES",
            context="", deposited_at=time.time(), resolved=True,
        ))
        isolate._purge_old()
        assert len(isolate._queue) == 0

    def test_max_attempts_purged(self, isolate):
        """Problemes avec max tentatives sont purges."""
        isolate._queue.append(IncubatedProblem(
            id="m1", text="maxed", source_intent="MAX",
            context="", deposited_at=time.time(),
            attempts=MAX_PROCESSING_ATTEMPTS,
        ))
        isolate._purge_old()
        assert len(isolate._queue) == 0

    def test_young_kept(self, isolate):
        """Problemes recents et non resolus sont gardes."""
        isolate._queue.append(IncubatedProblem(
            id="y1", text="young", source_intent="YOUNG",
            context="", deposited_at=time.time(),
            attempts=1,
        ))
        isolate._purge_old()
        assert len(isolate._queue) == 1

    def test_attempts_below_max_kept(self, isolate):
        """Problemes avec tentatives < max sont gardes."""
        isolate._queue.append(IncubatedProblem(
            id="a1", text="trying", source_intent="TRY",
            context="", deposited_at=time.time(),
            attempts=MAX_PROCESSING_ATTEMPTS - 1,
        ))
        isolate._purge_old()
        assert len(isolate._queue) == 1


# ================================================================
# TestProcessProblem
# ================================================================

class TestProcessProblem:
    @pytest.mark.asyncio
    async def test_no_bridges_no_eureka(self, isolate):
        """Pas de bridges → pas d'eureka."""
        isolate.deposit_problem("Test problem", "TEST")

        mock_afield = MagicMock()
        mock_engine = MagicMock()
        mock_engine.activate = AsyncMock(return_value=mock_afield)
        mock_engine.get_creative_bridges = MagicMock(return_value=[])

        with patch.dict("sys.modules", {"core.spreading_activation": MagicMock(activation_engine=mock_engine)}):
            await isolate._on_dmn_wander_cycle()

        assert isolate._queue[0].attempts == 1
        assert not isolate._queue[0].resolved
        assert len(isolate._eureka_history) == 0

    @pytest.mark.asyncio
    async def test_below_threshold_skip(self, isolate):
        """Bridge sous le seuil → pas d'eureka."""
        isolate.deposit_problem("Test problem", "TEST")

        weak_bridge = MagicMock()
        weak_bridge.bridge_strength = EUREKA_THRESHOLD - 0.1
        mock_engine = MagicMock()
        mock_engine.activate = AsyncMock(return_value=MagicMock())
        mock_engine.get_creative_bridges = MagicMock(return_value=[weak_bridge])

        with patch.dict("sys.modules", {"core.spreading_activation": MagicMock(activation_engine=mock_engine)}):
            await isolate._on_dmn_wander_cycle()

        assert not isolate._queue[0].resolved
        assert len(isolate._eureka_history) == 0

    @pytest.mark.asyncio
    async def test_above_threshold_eureka(self, isolate):
        """Bridge au-dessus du seuil → eureka et probleme resolu."""
        isolate.deposit_problem("Test problem", "TEST")

        strong_bridge = MagicMock()
        strong_bridge.bridge_strength = EUREKA_THRESHOLD + 0.1
        strong_bridge.node_a = "concept_A"
        strong_bridge.node_b = "concept_B"
        strong_bridge.hypothesis = "A connects to B"
        strong_bridge.used = False

        mock_engine = MagicMock()
        mock_engine.activate = AsyncMock(return_value=MagicMock())
        mock_engine.get_creative_bridges = MagicMock(return_value=[strong_bridge])

        with patch.dict("sys.modules", {"core.spreading_activation": MagicMock(activation_engine=mock_engine)}):
            with patch.object(isolate, "_fire_eureka", new_callable=AsyncMock) as mock_fire:
                await isolate._on_dmn_wander_cycle()
                mock_fire.assert_called_once()

    @pytest.mark.asyncio
    async def test_attempts_increment(self, isolate):
        """Chaque appel incremente le compteur de tentatives."""
        isolate.deposit_problem("Test problem", "TEST")

        mock_engine = MagicMock()
        mock_engine.activate = AsyncMock(return_value=MagicMock())
        mock_engine.get_creative_bridges = MagicMock(return_value=[])

        with patch.dict("sys.modules", {"core.spreading_activation": MagicMock(activation_engine=mock_engine)}):
            await isolate._on_dmn_wander_cycle()
            assert isolate._queue[0].attempts == 1
            await isolate._on_dmn_wander_cycle()
            assert isolate._queue[0].attempts == 2

    @pytest.mark.asyncio
    async def test_resolved_flag_set(self, isolate):
        """Eureka marque le probleme comme resolu."""
        isolate.deposit_problem("Test problem", "TEST")

        strong_bridge = MagicMock()
        strong_bridge.bridge_strength = EUREKA_THRESHOLD + 0.2
        strong_bridge.node_a = "A"
        strong_bridge.node_b = "B"
        strong_bridge.hypothesis = "Connection"
        strong_bridge.used = False

        mock_engine = MagicMock()
        mock_engine.activate = AsyncMock(return_value=MagicMock())
        mock_engine.get_creative_bridges = MagicMock(return_value=[strong_bridge])

        with patch.dict("sys.modules", {"core.spreading_activation": MagicMock(activation_engine=mock_engine)}):
            await isolate._on_dmn_wander_cycle()

        assert isolate._queue[0].resolved

    @pytest.mark.asyncio
    async def test_sa_stimulus_correct(self, isolate):
        """Le stimulus passe a SA est le texte du probleme."""
        isolate.deposit_problem("Mon probleme specifique", "TEST")

        mock_engine = MagicMock()
        mock_engine.activate = AsyncMock(return_value=MagicMock())
        mock_engine.get_creative_bridges = MagicMock(return_value=[])

        with patch.dict("sys.modules", {"core.spreading_activation": MagicMock(activation_engine=mock_engine)}):
            await isolate._on_dmn_wander_cycle()

        mock_engine.activate.assert_called_once_with(
            stimulus="Mon probleme specifique",
            source_collection="incubation",
        )

    @pytest.mark.asyncio
    async def test_graceful_without_sa(self, isolate):
        """Pas de spreading_activation → pas de crash."""
        isolate.deposit_problem("Test problem", "TEST")
        # Import echoue
        with patch.dict("sys.modules", {"core.spreading_activation": None}):
            # Ne devrait pas lever d'exception
            await isolate._on_dmn_wander_cycle()
        assert isolate._queue[0].attempts == 1

    @pytest.mark.asyncio
    async def test_empty_queue_noop(self, isolate):
        """Queue vide → pas de traitement."""
        await isolate._on_dmn_wander_cycle()
        # Pas de crash, pas d'action


# ================================================================
# TestEurekaFiring
# ================================================================

class TestEurekaFiring:
    @pytest.mark.asyncio
    async def test_eureka_published(self, isolate):
        """EUREKA_MOMENT est publie sur le bus."""
        problem = IncubatedProblem(
            id="p1", text="Test problem", source_intent="TEST",
            context="", deposited_at=time.time(),
        )
        bridge = MagicMock()
        bridge.node_a = "nodeA"
        bridge.node_b = "nodeB"
        bridge.bridge_strength = 0.8
        bridge.hypothesis = "A connects to B"
        bridge.used = False

        with patch("core.incubation_cognitive.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await isolate._fire_eureka(problem, bridge)
            mock_bus.publish.assert_called_once()
            call_args = mock_bus.publish.call_args
            assert call_args[0][0] == "EUREKA_MOMENT"
            event_data = call_args[0][1]
            assert event_data["problem_id"] == "p1"
            assert event_data["hypothesis"] == "A connects to B"

    @pytest.mark.asyncio
    async def test_eureka_event_recorded(self, isolate):
        """EurekaEvent est enregistre dans l'historique."""
        problem = IncubatedProblem(
            id="p1", text="Problem", source_intent="TEST",
            context="", deposited_at=time.time(),
        )
        bridge = MagicMock()
        bridge.node_a = "A"
        bridge.node_b = "B"
        bridge.bridge_strength = 0.7
        bridge.hypothesis = "Hypothesis"
        bridge.used = False

        with patch("core.incubation_cognitive.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await isolate._fire_eureka(problem, bridge)

        assert len(isolate._eureka_history) == 1
        eureka = isolate._eureka_history[0]
        assert eureka.problem_id == "p1"
        assert eureka.bridge_node_a == "A"
        assert eureka.bridge_node_b == "B"

    @pytest.mark.asyncio
    async def test_total_incremented(self, isolate):
        """_total_eurekas est incremente."""
        problem = IncubatedProblem(
            id="p1", text="P", source_intent="T",
            context="", deposited_at=time.time(),
        )
        bridge = MagicMock()
        bridge.node_a = "A"
        bridge.node_b = "B"
        bridge.bridge_strength = 0.6
        bridge.hypothesis = "H"
        bridge.used = False

        with patch("core.incubation_cognitive.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await isolate._fire_eureka(problem, bridge)

        assert isolate._total_eurekas == 1

    @pytest.mark.asyncio
    async def test_bridge_marked_used(self, isolate):
        """Le bridge est marque comme utilise."""
        problem = IncubatedProblem(
            id="p1", text="P", source_intent="T",
            context="", deposited_at=time.time(),
        )
        bridge = MagicMock()
        bridge.node_a = "A"
        bridge.node_b = "B"
        bridge.bridge_strength = 0.6
        bridge.hypothesis = "H"
        bridge.used = False

        with patch("core.incubation_cognitive.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await isolate._fire_eureka(problem, bridge)

        assert bridge.used is True

    @pytest.mark.asyncio
    async def test_history_cap(self, isolate):
        """L'historique est plafonne a EUREKA_HISTORY_MAX."""
        isolate._eureka_history = [
            EurekaEvent(
                id=f"e{i}", problem_id="p", problem_text="t",
                bridge_node_a="A", bridge_node_b="B",
                bridge_strength=0.5, hypothesis="H",
                source_intent="T", detected_at=time.time(),
            )
            for i in range(EUREKA_HISTORY_MAX)
        ]

        problem = IncubatedProblem(
            id="px", text="P", source_intent="TX",
            context="", deposited_at=time.time(),
        )
        bridge = MagicMock()
        bridge.node_a = "A"
        bridge.node_b = "B"
        bridge.bridge_strength = 0.6
        bridge.hypothesis = "New"
        bridge.used = False

        with patch("core.incubation_cognitive.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await isolate._fire_eureka(problem, bridge)

        assert len(isolate._eureka_history) <= EUREKA_HISTORY_MAX

    @pytest.mark.asyncio
    async def test_save_interval(self, isolate):
        """Save est appele tous les SAVE_INTERVAL eurekas."""
        problem = IncubatedProblem(
            id="p1", text="P", source_intent="T",
            context="", deposited_at=time.time(),
        )
        bridge = MagicMock()
        bridge.node_a = "A"
        bridge.node_b = "B"
        bridge.bridge_strength = 0.6
        bridge.hypothesis = "H"
        bridge.used = False

        with patch("core.incubation_cognitive.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            with patch.object(isolate, "_save") as mock_save:
                for i in range(SAVE_INTERVAL):
                    bridge.used = False
                    await isolate._fire_eureka(problem, bridge)
                mock_save.assert_called_once()


# ================================================================
# TestComputeEurekaBonus
# ================================================================

class TestComputeEurekaBonus:
    def test_empty_history_returns_zero(self, isolate):
        """Pas d'eurekas → 0."""
        assert isolate.compute_eureka_bonus("TEST") == 0.0

    def test_old_eureka_returns_zero(self, isolate):
        """Eureka > 24h → 0."""
        old_eureka = EurekaEvent(
            id="e1", problem_id="p1", problem_text="P",
            bridge_node_a="A", bridge_node_b="B",
            bridge_strength=0.8, hypothesis="H",
            source_intent="TEST", detected_at=time.time() - 25 * 3600,
        )
        isolate._eureka_history = [old_eureka]
        assert isolate.compute_eureka_bonus("TEST") == 0.0

    def test_matching_intent_bonus(self, isolate):
        """Eureka recent avec meme intent → bonus fort."""
        eureka = EurekaEvent(
            id="e1", problem_id="p1", problem_text="P",
            bridge_node_a="A", bridge_node_b="B",
            bridge_strength=0.8, hypothesis="H",
            source_intent="TEST_INTENT", detected_at=time.time(),
        )
        isolate._eureka_history = [eureka]
        bonus = isolate.compute_eureka_bonus("TEST_INTENT")
        assert bonus > 1.5  # freshness ~1.0 → bonus ~2.0

    def test_keyword_match_bonus(self, isolate):
        """Eureka avec mots-cles communs → bonus moyen."""
        eureka = EurekaEvent(
            id="e1", problem_id="p1", problem_text="P",
            bridge_node_a="evolution", bridge_node_b="pipeline",
            bridge_strength=0.8, hypothesis="evolution pipeline improvement",
            source_intent="OTHER", detected_at=time.time(),
        )
        isolate._eureka_history = [eureka]
        bonus = isolate.compute_eureka_bonus("EVOLUTION_PIPELINE")
        assert bonus > 0.0  # mots-cles communs

    def test_no_match_returns_zero(self, isolate):
        """Eureka sans rapport → 0."""
        eureka = EurekaEvent(
            id="e1", problem_id="p1", problem_text="P",
            bridge_node_a="nodeX", bridge_node_b="nodeY",
            bridge_strength=0.8, hypothesis="something unrelated",
            source_intent="UNRELATED", detected_at=time.time(),
        )
        isolate._eureka_history = [eureka]
        bonus = isolate.compute_eureka_bonus("COMPLETELY_DIFFERENT")
        assert bonus == 0.0

    def test_strong_recent_bridge(self, isolate):
        """Eureka tres recent avec intent exact → proche de 2.0."""
        eureka = EurekaEvent(
            id="e1", problem_id="p1", problem_text="P",
            bridge_node_a="A", bridge_node_b="B",
            bridge_strength=0.9, hypothesis="H",
            source_intent="EXACT_MATCH", detected_at=time.time(),
        )
        isolate._eureka_history = [eureka]
        bonus = isolate.compute_eureka_bonus("EXACT_MATCH")
        assert 1.9 <= bonus <= 2.0


# ================================================================
# TestBusHandlers
# ================================================================

class TestBusHandlers:
    @pytest.mark.asyncio
    async def test_routine_failed_deposits(self, isolate):
        """ROUTINE_FAILED depose un probleme."""
        await isolate._on_routine_failed({
            "intent": "EVOLUTION_PIPELINE",
            "mission": "Generer du code",
            "failure_type": "hallucination",
        })
        assert len(isolate._queue) == 1
        assert isolate._queue[0].source_intent == "EVOLUTION_PIPELINE"

    @pytest.mark.asyncio
    async def test_routine_failed_empty_intent_skip(self, isolate):
        """ROUTINE_FAILED sans intent → skip."""
        await isolate._on_routine_failed({"mission": "test"})
        assert len(isolate._queue) == 0

    @pytest.mark.asyncio
    async def test_loop_breaker_redirect_deposits(self, isolate):
        """LOOP_BREAKER redirect → depose."""
        await isolate._on_loop_breaker({
            "intent": "STUCK_INTENT",
            "prescription": "redirect",
        })
        assert len(isolate._queue) == 1

    @pytest.mark.asyncio
    async def test_loop_breaker_escalate_deposits(self, isolate):
        """LOOP_BREAKER escalate → depose."""
        await isolate._on_loop_breaker({
            "intent": "BLOCKED",
            "prescription": "escalate",
        })
        assert len(isolate._queue) == 1

    @pytest.mark.asyncio
    async def test_arc_struggle_deposits(self, isolate):
        """ARC struggle → depose."""
        await isolate._on_arc_created({
            "arc_type": "struggle",
            "intent": "HARD_INTENT",
            "title": "Difficulte persistante sur pipeline",
        })
        assert len(isolate._queue) == 1
        assert "Difficulte persistante" in isolate._queue[0].text

    @pytest.mark.asyncio
    async def test_arc_non_struggle_skip(self, isolate):
        """ARC non-struggle → skip."""
        await isolate._on_arc_created({
            "arc_type": "breakthrough",
            "intent": "GOOD_INTENT",
        })
        assert len(isolate._queue) == 0


# ================================================================
# TestPersistence
# ================================================================

class TestPersistence:
    def test_save_load_roundtrip_queue(self, isolate, tmp_path, monkeypatch):
        """Save/load roundtrip preserve la queue."""
        state_file = str(tmp_path / "test_state.json")
        monkeypatch.setattr("core.incubation_cognitive.INCUBATION_STATE_FILE", state_file)

        isolate.deposit_problem("Problem 1", "INTENT_1", "ctx1")
        isolate.deposit_problem("Problem 2", "INTENT_2", "ctx2")
        isolate._save()

        # Nouvelle instance
        IncubationCognitive.reset_singleton()
        monkeypatch.setattr("core.incubation_cognitive.INCUBATION_STATE_FILE", state_file)
        new_inc = IncubationCognitive()
        assert len(new_inc._queue) == 2
        assert new_inc._queue[0].source_intent == "INTENT_1"
        assert new_inc._queue[1].source_intent == "INTENT_2"

    def test_save_load_roundtrip_eureka(self, isolate, tmp_path, monkeypatch):
        """Save/load roundtrip preserve les eurekas."""
        state_file = str(tmp_path / "test_state.json")
        monkeypatch.setattr("core.incubation_cognitive.INCUBATION_STATE_FILE", state_file)

        isolate._eureka_history = [
            EurekaEvent(
                id="e1", problem_id="p1", problem_text="P",
                bridge_node_a="A", bridge_node_b="B",
                bridge_strength=0.7, hypothesis="Test",
                source_intent="T", detected_at=time.time(),
            )
        ]
        isolate._total_eurekas = 5
        isolate._save()

        IncubationCognitive.reset_singleton()
        monkeypatch.setattr("core.incubation_cognitive.INCUBATION_STATE_FILE", state_file)
        new_inc = IncubationCognitive()
        assert len(new_inc._eureka_history) == 1
        assert new_inc._total_eurekas == 5

    def test_missing_file_graceful(self, isolate, tmp_path, monkeypatch):
        """Fichier manquant → pas de crash."""
        state_file = str(tmp_path / "nonexistent.json")
        monkeypatch.setattr("core.incubation_cognitive.INCUBATION_STATE_FILE", state_file)
        IncubationCognitive.reset_singleton()
        monkeypatch.setattr("core.incubation_cognitive.INCUBATION_STATE_FILE", state_file)
        new_inc = IncubationCognitive()
        assert len(new_inc._queue) == 0

    def test_corrupt_file_graceful(self, isolate, tmp_path, monkeypatch):
        """Fichier corrompu → pas de crash."""
        state_file = str(tmp_path / "corrupt.json")
        with open(state_file, "w") as f:
            f.write("{invalid json")
        monkeypatch.setattr("core.incubation_cognitive.INCUBATION_STATE_FILE", state_file)
        IncubationCognitive.reset_singleton()
        monkeypatch.setattr("core.incubation_cognitive.INCUBATION_STATE_FILE", state_file)
        new_inc = IncubationCognitive()
        assert len(new_inc._queue) == 0


# ================================================================
# TestGetIncubationContext
# ================================================================

class TestGetIncubationContext:
    def test_empty_returns_empty(self, isolate):
        """Pas de problemes ni d'eurekas → vide."""
        assert isolate.get_incubation_context() == ""

    def test_with_problems(self, isolate):
        """Problemes en incubation → contexte non vide."""
        isolate.deposit_problem("Test", "INTENT_A")
        ctx = isolate.get_incubation_context()
        assert "[INCUBATION]" in ctx
        assert "INTENT_A" in ctx

    def test_with_recent_eureka(self, isolate):
        """Eureka recent → apparait dans le contexte."""
        isolate._eureka_history = [
            EurekaEvent(
                id="e1", problem_id="p1", problem_text="P",
                bridge_node_a="NodeA", bridge_node_b="NodeB",
                bridge_strength=0.8, hypothesis="Une connexion inattendue",
                source_intent="T", detected_at=time.time(),
            )
        ]
        ctx = isolate.get_incubation_context()
        assert "Eureka" in ctx
        assert "NodeA" in ctx


# ================================================================
# TestGetStats
# ================================================================

class TestGetStats:
    def test_stats_structure(self, isolate):
        """get_stats retourne les bonnes cles."""
        stats = isolate.get_stats()
        assert "queue_size" in stats
        assert "total_eurekas" in stats
        assert "recent_eurekas" in stats
        assert "problems" in stats
        assert "last_eureka" in stats

    def test_stats_with_data(self, isolate):
        """Stats refletent les donnees."""
        isolate.deposit_problem("P1", "I1")
        isolate._total_eurekas = 3
        stats = isolate.get_stats()
        assert stats["queue_size"] == 1
        assert stats["total_eurekas"] == 3
