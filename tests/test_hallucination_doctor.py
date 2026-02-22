"""Tests pour core/grimoire/hallucination_doctor.py — diagnostic d'hallucinations."""
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.grimoire.hallucination_doctor import (
    diagnose,
    build_corrected_prompt,
    parse_doctor_verdict,
    HallucinationDoctor,
)


class TestDiagnose:
    def test_alien_imports(self):
        diag = diagnose("alien_imports", {"aliens": ["django", "flask"]})
        assert diag["type"] == "alien_imports"
        assert diag["severity"] == "high"
        assert "django" in diag["details"]
        assert "flask" in diag["details"]
        assert diag["aliens"] == ["django", "flask"]

    def test_non_structural(self):
        diag = diagnose("non_structural", {})
        assert diag["type"] == "non_structural"
        assert diag["severity"] == "high"
        assert "def" in diag["suggestion"] or "class" in diag["suggestion"]

    def test_truncation(self):
        diag = diagnose("truncation", {
            "ratio": 0.45,
            "source_length": 5000,
            "generated_length": 2250,
        })
        assert diag["type"] == "truncation"
        assert diag["severity"] == "critical"
        assert "2250" in diag["details"]

    def test_offtopic(self):
        diag = diagnose("offtopic", {"keywords_found": ["blockchain", "trading"]})
        assert diag["type"] == "offtopic"
        assert diag["severity"] == "medium"
        assert "blockchain" in diag["details"]

    def test_unknown_type(self):
        diag = diagnose("something_else", {})
        assert diag["type"] == "unknown"
        assert diag["severity"] == "low"


class TestBuildCorrectedPrompt:
    def test_alien_imports_prompt(self):
        diag = diagnose("alien_imports", {"aliens": ["django", "torch"]})
        prompt = build_corrected_prompt(diag, {
            "spec_name": "TestSpec",
            "spec_description": "Description test",
            "target_file": "core/test.py",
        })
        assert prompt  # Non vide
        assert "django" in prompt
        assert "torch" in prompt
        assert "INTERDI" in prompt.upper() or "AUTORISES" in prompt.upper()
        assert "Promethee" in prompt

    def test_non_structural_prompt(self):
        diag = diagnose("non_structural", {})
        prompt = build_corrected_prompt(diag, {
            "spec_name": "TestSpec",
            "target_file": "core/test.py",
        })
        assert prompt
        assert "def" in prompt or "class" in prompt
        assert "OBLIGATOIRE" in prompt

    def test_offtopic_prompt(self):
        diag = diagnose("offtopic", {"keywords_found": ["blockchain"]})
        prompt = build_corrected_prompt(diag, {"mission": "test mission"})
        assert prompt
        assert "blockchain" in prompt
        assert "Promethee" in prompt

    def test_truncation_returns_empty(self):
        diag = diagnose("truncation", {"ratio": 0.3, "source_length": 5000, "generated_length": 1500})
        prompt = build_corrected_prompt(diag, {})
        assert prompt == ""

    def test_unknown_returns_empty(self):
        diag = diagnose("unknown_type", {})
        prompt = build_corrected_prompt(diag, {})
        assert prompt == ""


class TestParseDoctorVerdict:
    def test_retry_verdict(self):
        assert parse_doctor_verdict({"action": "retry"}) == "retry"
        assert parse_doctor_verdict({"verdict": "retry"}) == "retry"

    def test_abandon_verdict(self):
        assert parse_doctor_verdict({"action": "abandon"}) == "abandon"
        assert parse_doctor_verdict({"verdict": "abandon"}) == "abandon"

    def test_skip_verdict(self):
        assert parse_doctor_verdict({"action": "skip"}) == "skip"

    def test_invalid_input(self):
        assert parse_doctor_verdict(None) == "abandon"
        assert parse_doctor_verdict("string") == "abandon"
        assert parse_doctor_verdict({}) == "abandon"

    def test_action_priority(self):
        # action a priorite sur verdict
        assert parse_doctor_verdict({"action": "retry", "verdict": "abandon"}) == "retry"


class TestHallucinationDoctorAgent:
    """Tests de l'agent HallucinationDoctor (process_task)."""

    @pytest.fixture
    def doctor(self):
        with patch("core.grimoire.hallucination_doctor.BaseAgent.__init__", return_value=None):
            doc = HallucinationDoctor()
            doc.name = "hallucination_doctor"
            doc.role = "test"
            doc.description = "test"
            doc.logger = MagicMock()
            doc.has_memory = False
            doc.log_thought = MagicMock()
            return doc

    @pytest.mark.asyncio
    async def test_alien_imports_retry(self, doctor):
        result = await doctor.process_task({
            "mission": "AIDE: hallucination",
            "context": json.dumps({
                "type": "alien_imports",
                "aliens": ["django"],
                "spec_name": "Test",
                "target_file": "core/test.py",
            }),
        })
        assert result["action"] == "retry"
        assert result["verdict"] == "retry"
        assert result["corrected_prompt"]
        assert result["diagnosis"]["type"] == "alien_imports"

    @pytest.mark.asyncio
    async def test_truncation_abandon(self, doctor):
        result = await doctor.process_task({
            "mission": "AIDE: hallucination",
            "context": json.dumps({
                "type": "truncation",
                "ratio": 0.3,
                "source_length": 5000,
                "generated_length": 1500,
            }),
        })
        assert result["action"] == "abandon"
        assert result["verdict"] == "abandon"
        assert result["corrected_prompt"] == ""

    @pytest.mark.asyncio
    async def test_non_structural_retry(self, doctor):
        result = await doctor.process_task({
            "mission": "AIDE: hallucination",
            "context": json.dumps({
                "type": "non_structural",
                "spec_name": "TestSpec",
                "target_file": "core/test.py",
            }),
        })
        assert result["action"] == "retry"
        assert result["corrected_prompt"]

    @pytest.mark.asyncio
    async def test_offtopic_retry(self, doctor):
        result = await doctor.process_task({
            "mission": "AIDE: hallucination",
            "context": json.dumps({
                "type": "offtopic",
                "keywords_found": ["blockchain", "trading"],
                "mission": "test mission",
            }),
        })
        assert result["action"] == "retry"
        assert result["corrected_prompt"]

    @pytest.mark.asyncio
    async def test_unknown_type_abandon(self, doctor):
        result = await doctor.process_task({
            "mission": "AIDE: hallucination",
            "context": json.dumps({"type": "something_weird"}),
        })
        assert result["action"] == "abandon"

    @pytest.mark.asyncio
    async def test_invalid_json_context(self, doctor):
        result = await doctor.process_task({
            "mission": "AIDE: hallucination",
            "context": "not valid json {{{",
        })
        # Devrait retourner un resultat meme avec un context invalide
        assert result["action"] == "abandon"

    @pytest.mark.asyncio
    async def test_empty_context(self, doctor):
        result = await doctor.process_task({
            "mission": "AIDE: hallucination",
            "context": "{}",
        })
        assert result["action"] == "abandon"
