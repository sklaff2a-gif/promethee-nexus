# tests/test_roadmap_engine.py — Tests RoadmapEngine
import json
import os
import pytest
import pytest_asyncio

from unittest.mock import patch, AsyncMock


# --- Fixture autouse : reset singleton + isoler fichier ---

@pytest.fixture(autouse=True)
def isolate_roadmap(tmp_path, monkeypatch):
    """Reset le singleton et redirige le fichier roadmap vers tmp_path."""
    from core.roadmap_engine import RoadmapEngine
    RoadmapEngine.reset_singleton()
    roadmap_file = str(tmp_path / "roadmap.json")
    monkeypatch.setattr("core.roadmap_engine.ROADMAP_FILE", roadmap_file)
    yield roadmap_file
    RoadmapEngine.reset_singleton()


def _write_roadmap(path, modules, wip_paused=False):
    """Helper pour ecrire un roadmap.json de test."""
    data = {"_wip_paused": wip_paused, "modules": modules}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _make_module(id, phase=4, status="planned", depends_on=None,
                 research_topics=None, connects_to=None):
    """Helper pour creer un module minimal."""
    return {
        "id": id,
        "phase": phase,
        "phase_name": f"PHASE_{phase}",
        "display": id.replace("planned.", ""),
        "description": f"Module {id}",
        "status": status,
        "specs": [],
        "depends_on": depends_on or [],
        "estimated_cost": 5,
        "completion_criteria": [],
        "research_topics": research_topics or [],
        "connects_to": connects_to or [],
        "created_at": "2026-03-01T00:00:00",
        "updated_at": "2026-03-01T00:00:00",
    }


# ========================
# Tests de chargement
# ========================

class TestRoadmapLoad:

    def test_load_empty_file(self, isolate_roadmap):
        """Engine fonctionne meme sans fichier."""
        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert len(engine.modules) == 0
        assert engine._wip_paused is False

    def test_load_valid_file(self, isolate_roadmap):
        """Charge correctement les modules depuis le JSON."""
        modules = [_make_module("planned.test1"), _make_module("planned.test2")]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert len(engine.modules) == 2
        assert "planned.test1" in engine.modules
        assert "planned.test2" in engine.modules

    def test_load_wip_paused(self, isolate_roadmap):
        """Le flag _wip_paused est bien lu depuis le JSON."""
        _write_roadmap(isolate_roadmap, [], wip_paused=True)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine._wip_paused is True

    def test_load_corrupt_json(self, isolate_roadmap):
        """Gere gracieusement un JSON corrompu."""
        with open(isolate_roadmap, "w") as f:
            f.write("{corrupted")

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert len(engine.modules) == 0


# ========================
# Tests singleton
# ========================

class TestRoadmapSingleton:

    def test_singleton_identity(self, isolate_roadmap):
        """Deux instanciations retournent le meme objet."""
        from core.roadmap_engine import RoadmapEngine
        a = RoadmapEngine()
        b = RoadmapEngine()
        assert a is b

    def test_reset_singleton(self, isolate_roadmap):
        """reset_singleton() cree une nouvelle instance."""
        from core.roadmap_engine import RoadmapEngine
        a = RoadmapEngine()
        RoadmapEngine.reset_singleton()
        b = RoadmapEngine()
        assert a is not b


# ========================
# Tests pending_count
# ========================

class TestPendingCount:

    def test_all_planned(self, isolate_roadmap):
        """Tous les modules planned comptent comme pending."""
        modules = [_make_module(f"m{i}") for i in range(5)]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.get_pending_count() == 5

    def test_mixed_statuses(self, isolate_roadmap):
        """Seuls planned/researching/specifying/ready sont pending."""
        modules = [
            _make_module("m1", status="planned"),
            _make_module("m2", status="researching"),
            _make_module("m3", status="in_progress"),
            _make_module("m4", status="implemented"),
            _make_module("m5", status="ready"),
        ]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.get_pending_count() == 3  # planned + researching + ready


# ========================
# Tests next_module
# ========================

class TestNextModule:

    def test_next_by_phase(self, isolate_roadmap):
        """Retourne le module de la phase la plus basse."""
        modules = [
            _make_module("m_p6", phase=6),
            _make_module("m_p4", phase=4),
            _make_module("m_p5", phase=5),
        ]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        nxt = engine.get_next_module()
        assert nxt["id"] == "m_p4"

    def test_next_none_if_no_match(self, isolate_roadmap):
        """Retourne None si aucun module ne matche le filtre."""
        modules = [_make_module("m1", status="implemented")]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.get_next_module() is None

    def test_next_with_status_filter(self, isolate_roadmap):
        """Le filtre de status fonctionne."""
        modules = [
            _make_module("m1", status="planned"),
            _make_module("m2", status="researching"),
        ]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        nxt = engine.get_next_module(status_filter="researching")
        assert nxt["id"] == "m2"


# ========================
# Tests research_queue
# ========================

class TestResearchQueue:

    def test_only_planned_with_topics(self, isolate_roadmap):
        """Seuls les modules planned avec research_topics sont dans la queue."""
        modules = [
            _make_module("m1", research_topics=["topic1"]),
            _make_module("m2", research_topics=[]),
            _make_module("m3", status="researching", research_topics=["topic2"]),
        ]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        queue = engine.get_research_queue()
        assert len(queue) == 1
        assert queue[0]["id"] == "m1"


# ========================
# Tests should_research (hysteresis)
# ========================

class TestShouldResearch:

    def test_normal_mode(self, isolate_roadmap):
        """En mode normal avec peu de pending, la recherche est autorisee."""
        modules = [_make_module(f"m{i}") for i in range(10)]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.should_research() is True
        assert engine._wip_paused is False

    def test_pause_at_threshold(self, isolate_roadmap):
        """La recherche se pause quand pending >= 25."""
        modules = [_make_module(f"m{i}") for i in range(25)]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.should_research() is False
        assert engine._wip_paused is True

    def test_resume_at_threshold(self, isolate_roadmap):
        """La recherche reprend quand pending descend a <= 20."""
        # 20 planned = sous le seuil de reprise
        modules = [_make_module(f"m{i}") for i in range(20)]
        _write_roadmap(isolate_roadmap, modules, wip_paused=True)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine._wip_paused is True
        assert engine.should_research() is True
        assert engine._wip_paused is False

    def test_stays_paused_between_thresholds(self, isolate_roadmap):
        """Entre 21 et 24, reste en pause si deja pause."""
        modules = [_make_module(f"m{i}") for i in range(22)]
        _write_roadmap(isolate_roadmap, modules, wip_paused=True)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.should_research() is False
        assert engine._wip_paused is True


# ========================
# Tests update_module_status
# ========================

class TestUpdateModuleStatus:

    @pytest.mark.asyncio
    async def test_update_status(self, isolate_roadmap):
        """Met a jour le status et persiste."""
        modules = [_make_module("m1")]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()

        with patch("core.roadmap_engine.bus.publish", new_callable=AsyncMock):
            result = await engine.update_module_status("m1", "researching")

        assert result is True
        assert engine.modules["m1"]["status"] == "researching"

        # Verifie la persistence
        with open(isolate_roadmap, "r") as f:
            data = json.load(f)
        saved_mod = {m["id"]: m for m in data["modules"]}
        assert saved_mod["m1"]["status"] == "researching"

    @pytest.mark.asyncio
    async def test_update_with_specs(self, isolate_roadmap):
        """Met a jour les specs en meme temps que le status."""
        modules = [_make_module("m1")]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()

        with patch("core.roadmap_engine.bus.publish", new_callable=AsyncMock):
            await engine.update_module_status("m1", "specifying", specs=["spec1", "spec2"])

        assert engine.modules["m1"]["specs"] == ["spec1", "spec2"]

    @pytest.mark.asyncio
    async def test_update_invalid_status(self, isolate_roadmap):
        """Refuse un status invalide."""
        modules = [_make_module("m1")]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()

        result = await engine.update_module_status("m1", "INVALID_STATUS")
        assert result is False

    @pytest.mark.asyncio
    async def test_update_unknown_module(self, isolate_roadmap):
        """Refuse un module inconnu."""
        _write_roadmap(isolate_roadmap, [])

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()

        result = await engine.update_module_status("nonexistent", "researching")
        assert result is False

    @pytest.mark.asyncio
    async def test_update_publishes_events(self, isolate_roadmap):
        """Publie ROADMAP_MODULE_STARTED quand un module demarre."""
        modules = [_make_module("m1")]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()

        with patch("core.roadmap_engine.bus.publish", new_callable=AsyncMock) as mock_pub:
            await engine.update_module_status("m1", "researching")

        events = [call.args[0] for call in mock_pub.call_args_list]
        assert "ROADMAP_MODULE_STARTED" in events
        assert "ROADMAP_PROGRESS" in events

    @pytest.mark.asyncio
    async def test_update_publishes_completed(self, isolate_roadmap):
        """Publie ROADMAP_MODULE_COMPLETED quand un module est valide."""
        modules = [_make_module("m1", status="implemented")]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()

        with patch("core.roadmap_engine.bus.publish", new_callable=AsyncMock) as mock_pub:
            await engine.update_module_status("m1", "validated")

        events = [call.args[0] for call in mock_pub.call_args_list]
        assert "ROADMAP_MODULE_COMPLETED" in events


# ========================
# Tests compute_roadmap_bonus
# ========================

class TestComputeBonus:

    def test_bonus_research_normal(self, isolate_roadmap):
        """Bonus recherche quand des modules sont dans la queue."""
        modules = [_make_module("m1", research_topics=["topic"])]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.compute_roadmap_bonus("ROADMAP_RESEARCH") == 5.0

    def test_bonus_research_empty_queue(self, isolate_roadmap):
        """Pas de bonus recherche si aucun module a rechercher."""
        modules = [_make_module("m1", status="researching")]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.compute_roadmap_bonus("ROADMAP_RESEARCH") == 0.0

    def test_bonus_spec(self, isolate_roadmap):
        """Bonus spec quand des modules sont en researching."""
        modules = [_make_module("m1", status="researching")]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.compute_roadmap_bonus("ROADMAP_SPEC") == 6.0

    def test_bonus_council(self, isolate_roadmap):
        """Bonus council quand des modules sont ready."""
        modules = [_make_module("m1", status="ready")]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.compute_roadmap_bonus("COUNCIL_DEBATE") == 2.0

    def test_malus_wip_paused(self, isolate_roadmap):
        """En WIP pause, recherche et spec sont bloquees."""
        modules = [_make_module(f"m{i}", research_topics=["t"]) for i in range(25)]
        _write_roadmap(isolate_roadmap, modules, wip_paused=True)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.compute_roadmap_bonus("ROADMAP_RESEARCH") == -10.0
        assert engine.compute_roadmap_bonus("ROADMAP_SPEC") == -10.0
        assert engine.compute_roadmap_bonus("EXPANSION_CODE") == 3.0

    def test_bonus_unrelated_intent(self, isolate_roadmap):
        """Un intent non lie a la roadmap retourne 0."""
        _write_roadmap(isolate_roadmap, [_make_module("m1")])

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.compute_roadmap_bonus("SECURITY_AUDIT") == 0.0


# ========================
# Tests context et stats
# ========================

class TestContextAndStats:

    def test_get_roadmap_context(self, isolate_roadmap):
        """Le contexte contient le compte de modules."""
        modules = [_make_module("m1"), _make_module("m2", status="researching")]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        ctx = engine.get_roadmap_context()
        assert "ROADMAP:" in ctx
        assert "2 modules" in ctx

    def test_get_roadmap_context_wip_paused(self, isolate_roadmap):
        """Le contexte mentionne WIP PAUSE quand actif."""
        modules = [_make_module(f"m{i}") for i in range(25)]
        _write_roadmap(isolate_roadmap, modules, wip_paused=True)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        ctx = engine.get_roadmap_context()
        assert "WIP PAUSE" in ctx

    def test_get_stats(self, isolate_roadmap):
        """Les stats contiennent toutes les informations requises."""
        modules = [
            _make_module("m1", phase=4),
            _make_module("m2", phase=5, status="researching"),
        ]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        stats = engine.get_stats()
        assert stats["total_modules"] == 2
        assert stats["wip_paused"] is False
        assert stats["pending_count"] == 2
        assert "by_status" in stats
        assert "by_phase" in stats

    def test_get_modules_for_graph(self, isolate_roadmap):
        """Format compatible impact_analyzer."""
        modules = [_make_module("m1", phase=4, connects_to=["core.test"])]
        _write_roadmap(isolate_roadmap, modules)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        graph_mods = engine.get_modules_for_graph()
        assert len(graph_mods) == 1
        assert graph_mods[0]["id"] == "m1"
        assert graph_mods[0]["connects_to"] == ["core.test"]
        assert graph_mods[0]["status"] == "planned"

    def test_empty_context(self, isolate_roadmap):
        """Contexte vide si aucun module."""
        _write_roadmap(isolate_roadmap, [])

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.get_roadmap_context() == ""


# ========================
# Test idempotence save/load
# ========================

class TestIdempotence:

    def test_save_load_roundtrip(self, isolate_roadmap):
        """Les donnees survivent a un save/load."""
        modules = [
            _make_module("m1", status="researching"),
            _make_module("m2", phase=7, research_topics=["ai"]),
        ]
        _write_roadmap(isolate_roadmap, modules, wip_paused=True)

        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine._wip_paused is True
        assert len(engine.modules) == 2

        # Force save
        engine._save()

        # Reload
        RoadmapEngine.reset_singleton()
        engine2 = RoadmapEngine()
        assert engine2._wip_paused is True
        assert len(engine2.modules) == 2
        assert engine2.modules["m1"]["status"] == "researching"
