# tests/test_vision_agent.py — Tests DivineVision
import json
import pytest
import pytest_asyncio

from unittest.mock import patch, AsyncMock, MagicMock


# --- Fixture autouse : isoler roadmap ---

@pytest.fixture(autouse=True)
def isolate_roadmap(tmp_path, monkeypatch):
    """Reset singleton roadmap et redirige vers tmp_path."""
    from core.roadmap_engine import RoadmapEngine
    RoadmapEngine.reset_singleton()
    roadmap_file = str(tmp_path / "roadmap.json")
    monkeypatch.setattr("core.roadmap_engine.ROADMAP_FILE", roadmap_file)
    yield roadmap_file
    RoadmapEngine.reset_singleton()


def _write_roadmap(path, modules, wip_paused=False):
    data = {"_wip_paused": wip_paused, "modules": modules}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    # Rafraichir le singleton module-level pour que les imports dans vision_agent
    # obtiennent l'instance avec les donnees de test
    import core.roadmap_engine
    core.roadmap_engine.RoadmapEngine.reset_singleton()
    core.roadmap_engine.roadmap = core.roadmap_engine.RoadmapEngine()


def _make_module(id, phase=4, status="planned", research_topics=None,
                 specs=None, connects_to=None):
    return {
        "id": id,
        "phase": phase,
        "phase_name": f"PHASE_{phase}",
        "display": id.replace("planned.", ""),
        "description": f"Description de {id}",
        "status": status,
        "specs": specs or [],
        "depends_on": [],
        "estimated_cost": 5,
        "completion_criteria": [],
        "research_topics": research_topics or [],
        "connects_to": connects_to or [],
        "created_at": "2026-03-01T00:00:00",
        "updated_at": "2026-03-01T00:00:00",
    }


# ========================
# Tests instantiation
# ========================

class TestVisionInstantiation:

    def test_create_agent(self):
        """L'agent se cree correctement."""
        from Agents.vision_agent import DivineVision
        agent = DivineVision()
        assert agent.name == "vision"
        assert agent.role == "Architecte de Vision Strategique"


# ========================
# Tests dispatch
# ========================

class TestVisionDispatch:

    @pytest.mark.asyncio
    async def test_dispatch_research(self, isolate_roadmap):
        """Le contexte ROADMAP_RESEARCH dispatche vers _handle_research."""
        modules = [_make_module("m1", research_topics=["topic1"])]
        _write_roadmap(isolate_roadmap, modules)

        from Agents.vision_agent import DivineVision
        agent = DivineVision()

        with patch.object(agent, "_handle_research", new_callable=AsyncMock,
                          return_value={"status": "success"}) as mock:
            await agent.process_task({
                "mission": "test",
                "context": "ROADMAP_RESEARCH",
            })
            mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_spec(self, isolate_roadmap):
        """Le contexte ROADMAP_SPEC dispatche vers _handle_spec."""
        from Agents.vision_agent import DivineVision
        agent = DivineVision()

        with patch.object(agent, "_handle_spec", new_callable=AsyncMock,
                          return_value={"status": "success"}) as mock:
            await agent.process_task({
                "mission": "test",
                "context": "ROADMAP_SPEC",
            })
            mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_general(self, isolate_roadmap):
        """Sans contexte specifique, dispatche vers _handle_general."""
        _write_roadmap(isolate_roadmap, [])

        from Agents.vision_agent import DivineVision
        agent = DivineVision()

        with patch.object(agent, "_handle_general", new_callable=AsyncMock,
                          return_value={"status": "success"}) as mock:
            await agent.process_task({
                "mission": "Quel est l'etat de la roadmap?",
                "context": "",
            })
            mock.assert_called_once()


# ========================
# Tests _handle_research
# ========================

class TestHandleResearch:

    @pytest.mark.asyncio
    async def test_research_wip_paused(self, isolate_roadmap):
        """En WIP pause, la recherche est skippee."""
        modules = [_make_module(f"m{i}", research_topics=["t"]) for i in range(25)]
        _write_roadmap(isolate_roadmap, modules, wip_paused=True)

        from Agents.vision_agent import DivineVision
        agent = DivineVision()

        result = await agent._handle_research({"mission": "test", "context": ""})
        assert result["status"] == "skipped"
        assert result["reason"] == "wip_limit"

    @pytest.mark.asyncio
    async def test_research_no_target(self, isolate_roadmap):
        """Sans module a rechercher, la routine est skippee."""
        # Module sans research_topics
        modules = [_make_module("m1", research_topics=[])]
        _write_roadmap(isolate_roadmap, modules)

        from Agents.vision_agent import DivineVision
        agent = DivineVision()

        result = await agent._handle_research({"mission": "test", "context": ""})
        assert result["status"] == "skipped"
        assert result["reason"] == "no_research_target"

    @pytest.mark.asyncio
    async def test_research_success(self, isolate_roadmap):
        """Recherche reussie : dispatche researcher et update status."""
        modules = [_make_module("m1", research_topics=["AI amygdala"])]
        _write_roadmap(isolate_roadmap, modules)

        from Agents.vision_agent import DivineVision
        agent = DivineVision()

        mock_orchestrator = MagicMock()
        mock_orchestrator.dispatch_task = AsyncMock(
            return_value={"status": "success", "result": "Findings sur amygdala..."}
        )

        import core.orchestrator
        original = core.orchestrator.orchestrator
        core.orchestrator.orchestrator = mock_orchestrator
        try:
            with patch("core.roadmap_engine.bus.publish", new_callable=AsyncMock):
                result = await agent._handle_research({"mission": "test", "context": ""})
        finally:
            core.orchestrator.orchestrator = original

        assert result["status"] == "success"
        assert "recherche" in result["result"].lower() or "Recherche" in result["result"]


# ========================
# Tests _handle_spec
# ========================

class TestHandleSpec:

    @pytest.mark.asyncio
    async def test_spec_no_target(self, isolate_roadmap):
        """Sans module en researching, la spec est skippee."""
        modules = [_make_module("m1", status="planned")]
        _write_roadmap(isolate_roadmap, modules)

        from Agents.vision_agent import DivineVision
        agent = DivineVision()

        result = await agent._handle_spec({"mission": "test", "context": ""})
        assert result["status"] == "skipped"
        assert result["reason"] == "no_spec_target"

    @pytest.mark.asyncio
    async def test_spec_success(self, isolate_roadmap):
        """Specification reussie : appel LLM et update status -> ready."""
        modules = [_make_module("m1", status="researching",
                                specs=["RESEARCH_RAW: donnees brutes"])]
        _write_roadmap(isolate_roadmap, modules)

        from Agents.vision_agent import DivineVision
        agent = DivineVision()

        spec_text = "1. Responsabilites: detection valence\n2. Interface: evaluate()\n3. Events: AMYGDALA_ALERT" + " " * 50

        with patch.object(agent, "generate_content", new_callable=AsyncMock,
                          return_value=spec_text), \
             patch("core.roadmap_engine.bus.publish", new_callable=AsyncMock):
            result = await agent._handle_spec({"mission": "test", "context": ""})

        assert result["status"] == "success"
        assert "ready" in result["result"].lower()

        # Verifier que le status a ete mis a jour
        from core.roadmap_engine import RoadmapEngine
        engine = RoadmapEngine()
        assert engine.modules["m1"]["status"] == "ready"


# ========================
# Tests _handle_general
# ========================

class TestHandleGeneral:

    @pytest.mark.asyncio
    async def test_general_with_roadmap(self, isolate_roadmap):
        """Repond avec contexte roadmap."""
        modules = [_make_module("m1")]
        _write_roadmap(isolate_roadmap, modules)

        from Agents.vision_agent import DivineVision
        agent = DivineVision()

        with patch.object(agent, "generate_content", new_callable=AsyncMock,
                          return_value="La roadmap contient 1 module."):
            result = await agent._handle_general({
                "mission": "Quel est l'etat de la roadmap?",
                "context": "",
            })

        assert result["status"] == "success"
        assert "roadmap" in result["result"].lower()
