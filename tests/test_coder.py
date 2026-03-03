import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from Agents.coder_agent import DivineCoder, _count_offtopic, _OFFTOPIC_THRESHOLD


class TestCountOfftopic:

    def test_clean_code_zero(self):
        code = "import asyncio\nasync def process_task(self):\n    return await self.generate_content(prompt)"
        assert _count_offtopic(code) == 0

    def test_blockchain_code_detected(self):
        code = "class SmartContractAgent:\n    def trade(self, blockchain, wallet):\n        pass"
        count = _count_offtopic(code)
        assert count >= 3  # smart contract, trade, blockchain, wallet

    def test_rss_code_detected(self):
        code = "import feedparser\nclass RSSAgent:\n    def parse_rss(self):\n        pass"
        count = _count_offtopic(code)
        assert count >= 2  # feedparser, rss

    def test_langchain_crewai_detected(self):
        code = "from langchain import LLMChain\nfrom crewai import Agent"
        count = _count_offtopic(code)
        assert count >= 2

    def test_kubernetes_docker_detected(self):
        code = "apiVersion: apps/v1\nkind: Deployment\nkubernetes docker terraform"
        count = _count_offtopic(code)
        assert count >= 3

    def test_prometheus_code_clean(self):
        """Du code qui parle de modules Prométhée ne doit PAS être flaggé."""
        code = (
            "from core.orchestrator import orchestrator\n"
            "from core.base_agent import BaseAgent\n"
            "async def improve_router():\n"
            "    agent = BaseAgent('test', 'role', 'desc')\n"
        )
        assert _count_offtopic(code) == 0

    def test_threshold_value(self):
        """Le seuil doit être 2 (tolérance réduite — anti-hallucination renforcée)."""
        assert _OFFTOPIC_THRESHOLD == 2


class TestCoderOfftopicFilter:

    @pytest.mark.asyncio
    async def test_offtopic_code_rejected(self):
        """Le Coder rejette le code si trop de mots-clés hors-sujet."""
        coder = DivineCoder()
        offtopic_response = (
            "class SmartContractAgent:\n"
            "    def trade(self, blockchain_client):\n"
            "        wallet = self.get_wallet()\n"
            "        token = self.mint_nft()\n"
        )
        with patch.object(coder, "generate_content", new_callable=AsyncMock, return_value=offtopic_response):
            result = await coder.process_task({"mission": "Génère du code", "context": ""})
        assert result["status"] == "warning"
        assert "hors périmètre" in result["result"]

    @pytest.mark.asyncio
    async def test_clean_code_passes(self):
        """Le Coder laisse passer du code pertinent."""
        coder = DivineCoder()
        clean_response = (
            "from core.orchestrator import orchestrator\n"
            "async def optimize_dispatch():\n"
            "    return await orchestrator.dispatch_task('coder', payload)\n"
        )
        with patch.object(coder, "generate_content", new_callable=AsyncMock, return_value=clean_response):
            result = await coder.process_task({"mission": "Optimise le dispatch", "context": ""})
        assert result["status"] == "success"
        assert result["result"] == clean_response

    @pytest.mark.asyncio
    async def test_context_included_in_prompt(self):
        """Le contexte (ex: EVOLUTION_PIPELINE) est injecté dans le prompt."""
        coder = DivineCoder()
        captured_prompt = None

        async def capture(prompt):
            nonlocal captured_prompt
            captured_prompt = prompt
            return "pass"

        with patch.object(coder, "generate_content", side_effect=capture):
            await coder.process_task({
                "mission": "Génère du code",
                "context": "EVOLUTION_PIPELINE\nSPÉC: améliore core/router.py"
            })
        assert "EVOLUTION_PIPELINE" in captured_prompt
        assert "core/router.py" in captured_prompt


class TestCoderSystemPrompt:

    def test_system_instructions_contain_project_context(self):
        coder = DivineCoder()
        assert "PROMÉTHÉE" in coder.system_instructions
        # get_project_structure() liste par dossier : "core/ : orchestrator.py, ..."
        assert "orchestrator.py" in coder.system_instructions
        assert "trading" in coder.system_instructions.lower()  # dans les interdictions
        assert "blockchain" in coder.system_instructions.lower()

    def test_system_instructions_contain_rules(self):
        coder = DivineCoder()
        assert "RÈGLES ABSOLUES" in coder.system_instructions
        assert "R.A.S" in coder.system_instructions
