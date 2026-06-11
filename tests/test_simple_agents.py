"""Smoke tests des agents simples (audit 11/06/2026).

Couvre les 5 agents qui n'avaient aucun test dédié :
strategist, writer, security, infra, philosopher (Agents/).

Objectif : vérifier le contrat process_task (status/result/agent),
generate_content mocké — aucun appel LLM réel, aucun accès chroma.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from Agents.strategist_agent import DivineStrategist
from Agents.writer_agent import DivineWriter
from Agents.security_agent import DivineSecurity
from Agents.infra_agent import DivineInfra
from Agents.philosopher_agent import Philosopher


REPONSE_LLM = "ANALYSE : tout va bien.\nRECOMMANDATION ACTIONNABLE : continuer."


def _mock_memory(agent):
    """Neutralise recall/remember (pas d'accès chroma dans les smoke tests)."""
    agent.recall = MagicMock(return_value="")
    agent.remember = MagicMock()


class TestStrategistSmoke:

    @pytest.mark.asyncio
    async def test_process_task_contract(self):
        agent = DivineStrategist()
        _mock_memory(agent)
        with patch.object(agent, "generate_content", new_callable=AsyncMock, return_value=REPONSE_LLM):
            result = await agent.process_task({"mission": "Optimise le flux X", "context": "ctx"})
        assert result["status"] == "success"
        assert result["result"] == REPONSE_LLM
        assert result["agent"] == "strategist"

    @pytest.mark.asyncio
    async def test_context_tronque_avant_llm(self):
        """Le contexte géant ne doit pas être injecté intégralement dans le prompt."""
        agent = DivineStrategist()
        _mock_memory(agent)
        captured = {}

        async def _capture(prompt):
            captured["prompt"] = prompt
            return REPONSE_LLM

        with patch.object(agent, "generate_content", side_effect=_capture):
            await agent.process_task({"mission": "m", "context": "x" * 500_000})
        assert len(captured["prompt"]) < 500_000


class TestWriterSmoke:

    @pytest.mark.asyncio
    async def test_process_task_contract(self):
        agent = DivineWriter()
        _mock_memory(agent)
        with patch.object(agent, "generate_content", new_callable=AsyncMock, return_value=REPONSE_LLM):
            result = await agent.process_task({"mission": "Rédige un rapport", "context": ""})
        assert result["status"] == "success"
        assert result["agent"] == "writer"

    @pytest.mark.asyncio
    async def test_memorise_productions_longues(self):
        """Une production > 200 chars doit être mémorisée."""
        agent = DivineWriter()
        _mock_memory(agent)
        longue = "R" * 300
        with patch.object(agent, "generate_content", new_callable=AsyncMock, return_value=longue):
            await agent.process_task({"mission": "Rédige", "context": ""})
        agent.remember.assert_called_once()

    @pytest.mark.asyncio
    async def test_pas_de_memorisation_courte(self):
        agent = DivineWriter()
        _mock_memory(agent)
        with patch.object(agent, "generate_content", new_callable=AsyncMock, return_value="court"):
            await agent.process_task({"mission": "Rédige", "context": ""})
        agent.remember.assert_not_called()


class TestSecuritySmoke:

    @pytest.mark.asyncio
    async def test_process_task_contract(self):
        agent = DivineSecurity()
        _mock_memory(agent)
        finding = "AUDIT SÉCURITÉ — config\nVULNÉRABILITÉS DÉTECTÉES :\n- [V1] Sévérité: HAUTE — clé en clair " + "x" * 100
        with patch.object(agent, "generate_content", new_callable=AsyncMock, return_value=finding):
            result = await agent.process_task({"mission": "Audit du fichier config.py", "context": ""})
        assert result["status"] == "success"
        assert result["agent"] == "security"
        # Un finding significatif est mémorisé avec le nom du fichier extrait
        agent.remember.assert_called_once()
        assert "config.py" in agent.remember.call_args[0][0]

    @pytest.mark.asyncio
    async def test_audit_clean_pas_de_stockage(self):
        """Un audit sans vulnérabilité ne pollue pas le RAG (anti-bruit)."""
        agent = DivineSecurity()
        _mock_memory(agent)
        clean = "AUDIT SÉCURITÉ — RAS\nAucune vulnérabilité détectée. " + "x" * 100
        with patch.object(agent, "generate_content", new_callable=AsyncMock, return_value=clean):
            await agent.process_task({"mission": "Audit", "context": ""})
        agent.remember.assert_not_called()


class TestInfraSmoke:

    @pytest.mark.asyncio
    async def test_mode_gardien_sans_llm(self):
        """Une mission health-check passe par les métriques réelles, zéro LLM."""
        agent = DivineInfra()
        with patch.object(agent, "generate_content", new_callable=AsyncMock) as gen:
            result = await agent.process_task({"mission": "health check du serveur", "context": ""})
        gen.assert_not_called()
        assert result["status"] == "success"
        assert "RAPPORT INFRA" in result["result"]

    @pytest.mark.asyncio
    async def test_mode_expert_generatif(self):
        """Une mission sans mot-clé gardien part bien vers le LLM."""
        agent = DivineInfra()
        _mock_memory(agent)
        with patch.object(agent, "generate_content", new_callable=AsyncMock, return_value=REPONSE_LLM):
            result = await agent.process_task({
                "mission": "Propose une optimisation du scheduler GPU",
                "context": "",
            })
        assert result["status"] == "success"


class TestPhilosopherSmoke:

    @pytest.mark.asyncio
    async def test_process_task_contract(self):
        agent = Philosopher()
        _mock_memory(agent)
        with patch.object(agent, "generate_content", new_callable=AsyncMock, return_value="Déduction : car A, donc B."):
            result = await agent.process_task({
                "mission": "Que découle-t-il de l'axiome ?",
                "context": "AXIOME : tout flux se conserve.",
            })
        assert result["status"] == "success"
        assert result["agent"] == "philosopher"

    @pytest.mark.asyncio
    async def test_axiome_injecte_dans_prompt(self):
        """Le contexte (axiome + concepts) doit atteindre le LLM."""
        agent = Philosopher()
        captured = {}

        async def _capture(prompt):
            captured["prompt"] = prompt
            return "ok"

        with patch.object(agent, "generate_content", side_effect=_capture):
            await agent.process_task({"mission": "m", "context": "AXIOME : tout flux se conserve."})
        assert "AXIOME : tout flux se conserve." in captured["prompt"]
