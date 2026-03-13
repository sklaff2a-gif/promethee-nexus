"""Tests pour Agents/professor_agent.py — Evaluateur pedagogique."""
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import sys

# Mock core.base_agent avant import pour eviter les dependances lourdes
# IMPORTANT: utiliser setdefault pour ne PAS ecraser si deja importe
_mock_base_agent = sys.modules.get("core.base_agent")
if _mock_base_agent is None:
    mock_base = MagicMock()
    mock_base.BaseAgent = type("BaseAgent", (), {
        "__init__": lambda self, **kw: None,
        "generate_content": AsyncMock(return_value="NOTE: 1.5\nFEEDBACK: Bon travail.\nDEFI: Essaie plus dur."),
        "name": "professor",
    })
    sys.modules["core.base_agent"] = mock_base
# NE PAS mocker core.event_bus ni core.event_bus.bus
# car cela pollue sys.modules pour les autres tests

import Agents.professor_agent as mod
from Agents.professor_agent import ProfessorAgent, DIFFICULTY_DEFAULT, DIFFICULTY_MAX, DIFFICULTY_MIN


@pytest.fixture
def professor(tmp_path, monkeypatch):
    """Cree un professeur isole."""
    monkeypatch.setattr(mod, "_GRADES_FILE_DEFAULT", str(tmp_path / "grades.json"))
    monkeypatch.setattr(mod, "_CURRICULUM_FILE_DEFAULT", str(tmp_path / "curriculum.json"))
    p = ProfessorAgent()
    p._grades_file = str(tmp_path / "grades.json")
    p._curriculum_file = str(tmp_path / "curriculum.json")
    p._grades = []
    p._curriculum = {}
    p.generate_content = AsyncMock(return_value="NOTE: 1.5\nFEEDBACK: Bon travail.\nDEFI: Essaie plus dur.")
    return p


# ── Scoring deterministe ─────────────────────────────────────────────────

class TestDeterministicScoring:
    def test_empty_text_zero(self, professor):
        result = professor._score_deterministic("", "CODE_REVIEW")
        assert result["score"] == 0.0

    def test_short_text_low_score(self, professor):
        result = professor._score_deterministic("ok", "CODE_REVIEW")
        assert result["score"] < 3.0
        assert any("court" in c.lower() or "Trop court" in c for c in result["comments"])

    def test_structured_text_higher(self, professor):
        text = (
            "## Revue de code : core/base_agent.py\n\n"
            "### Resume\n"
            "Ce fichier definit la classe BaseAgent qui gere les appels Ollama.\n\n"
            "### Bugs detectes\n"
            "- La fonction `_call_ollama` ne gere pas les erreurs HTTP 429 (rate limit)\n"
            "- La methode `generate_content` pourrait timeout sur les gros prompts\n\n"
            "### Suggestions\n"
            "1. Ajouter un retry avec backoff exponentiel en ligne 968\n"
            "2. Implementer un cache de reponses pour les prompts identiques\n\n"
            "### Points forts\n"
            "- Circuit breaker bien implemente\n"
            "- GPU scheduler robuste"
        )
        result = professor._score_deterministic(text, "CODE_REVIEW")
        assert result["score"] >= 5.0

    def test_workshop_valid_ast(self, professor):
        code = '```python\ndef is_prime(n):\n    if n <= 1:\n        return False\n    for i in range(2, n):\n        if n % i == 0:\n            return False\n    return True\n```'
        result = professor._score_deterministic(code, "WORKSHOP")
        assert result["breakdown"]["technical"] >= 1.5

    def test_workshop_invalid_ast(self, professor):
        code = "```python\ndef broken(\n    return\n```"
        result = professor._score_deterministic(code, "WORKSHOP")
        # Devrait quand meme avoir un score (meme si AST invalide)
        assert result["breakdown"]["technical"] <= 1.0

    def test_research_with_tech_words(self, professor):
        text = (
            "## Analyse des patterns async en Python\n\n"
            "L'architecture de bus d'evenements utilise async/await pour le pipeline.\n"
            "Le pattern pub/sub permet de decoupler les agents du modele de scoring.\n"
            "Le cache de memoire utilise un algorithme de consolidation avec des vecteurs.\n"
            "Les donnees transitent via l'API REST et le reseau de neurones simulees.\n"
            "Le pipeline d'agent utilise asyncio.Lock pour le GPU scheduler."
        )
        result = professor._score_deterministic(text, "RESEARCH")
        assert result["breakdown"]["technical"] >= 1.0

    def test_relevance_with_subject(self, professor):
        text = "Analyse du fichier base_agent.py et de la classe BaseAgent."
        result = professor._score_deterministic(text, "CODE_REVIEW", subject="base_agent.py BaseAgent")
        assert result["breakdown"]["relevance"] >= 0.5

    def test_relevance_off_topic(self, professor):
        text = "La meteo est belle aujourd'hui et les oiseaux chantent."
        result = professor._score_deterministic(text, "CODE_REVIEW", subject="base_agent.py")
        assert result["breakdown"]["relevance"] <= 0.5


# ── Scoring qualitatif ───────────────────────────────────────────────────

class TestQualitativeScoring:
    @pytest.mark.asyncio
    async def test_qualitative_parses_response(self, professor):
        professor.generate_content = AsyncMock(
            return_value="NOTE: 1.8\nFEEDBACK: Excellent travail de synthese.\nDEFI: Analyse un fichier plus complexe."
        )
        result = await professor._score_qualitative("Mon livrable...", "CODE_REVIEW", "base_agent.py")
        assert result["score"] == 1.8
        assert "Excellent" in result["feedback"]
        assert "complexe" in result["challenge"]

    @pytest.mark.asyncio
    async def test_qualitative_fallback_on_bad_response(self, professor):
        professor.generate_content = AsyncMock(return_value="Reponse incomprehensible sans format.")
        result = await professor._score_qualitative("Mon livrable...", "CODE_REVIEW", "")
        assert result["score"] == 1.0  # Default

    @pytest.mark.asyncio
    async def test_qualitative_clamps_score(self, professor):
        professor.generate_content = AsyncMock(return_value="NOTE: 99.9\nFEEDBACK: Wow.\nDEFI: rien.")
        result = await professor._score_qualitative("Mon livrable...", "CODE_REVIEW", "")
        assert result["score"] <= 2.0

    @pytest.mark.asyncio
    async def test_qualitative_exception_handled(self, professor):
        professor.generate_content = AsyncMock(side_effect=Exception("LLM down"))
        # L'exception est geree dans evaluate(), pas dans _score_qualitative directement
        # Mais _score_qualitative propage l'exception, evaluate() la catch
        with pytest.raises(Exception):
            await professor._score_qualitative("Mon livrable...", "CODE_REVIEW", "")


# ── Evaluation complete ──────────────────────────────────────────────────

class TestEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate_returns_complete(self, professor):
        text = (
            "## Revue de code\n\n"
            "Le fichier base_agent.py contient la classe BaseAgent.\n"
            "La fonction generate_content gere les appels LLM.\n"
            "Suggestion : ajouter un retry en ligne 200.\n"
            "Points forts : bonne architecture modulaire."
        )
        result = await professor.evaluate(text, "CODE_REVIEW", "base_agent.py")
        assert "grade" in result
        assert "feedback" in result
        assert "breakdown" in result
        assert 0.0 <= result["grade"] <= 10.0

    @pytest.mark.asyncio
    async def test_evaluate_saves_grade(self, professor):
        await professor.evaluate("Un livrable correct avec du contenu suffisant.", "RESEARCH", "async Python")
        assert len(professor._grades) == 1
        assert professor._grades[0]["course_type"] == "RESEARCH"

    @pytest.mark.asyncio
    async def test_evaluate_qualitative_failure_graceful(self, professor):
        professor.generate_content = AsyncMock(side_effect=Exception("Cloud down"))
        text = "Un livrable de bonne longueur avec du contenu technique sur le pipeline async et le bus d'evenements."
        result = await professor.evaluate(text, "RESEARCH", "async")
        # Devrait quand meme retourner un resultat (score deterministe seul)
        assert "grade" in result
        assert result["grade"] >= 0.0


# ── Difficulte adaptative ────────────────────────────────────────────────

class TestDifficultyAdaptive:
    def test_initial_difficulty(self, professor):
        assert professor.get_difficulty("CODE_REVIEW") == DIFFICULTY_DEFAULT

    def test_difficulty_increases_on_streak(self, professor):
        for _ in range(3):
            professor._adjust_difficulty("CODE_REVIEW", 9.0)
        assert professor.get_difficulty("CODE_REVIEW") > DIFFICULTY_DEFAULT

    def test_difficulty_decreases_on_failures(self, professor):
        # D'abord monter pour pouvoir descendre
        professor._curriculum["CODE_REVIEW"] = {
            "difficulty": 1.5, "mastery": 5.0, "sessions": 5, "last_grades": [7, 7, 7]
        }
        professor._adjust_difficulty("CODE_REVIEW", 3.0)
        professor._adjust_difficulty("CODE_REVIEW", 2.0)
        assert professor.get_difficulty("CODE_REVIEW") < 1.5

    def test_difficulty_clamped_max(self, professor):
        for _ in range(20):
            professor._adjust_difficulty("CODE_REVIEW", 10.0)
        assert professor.get_difficulty("CODE_REVIEW") <= DIFFICULTY_MAX

    def test_difficulty_clamped_min(self, professor):
        professor._curriculum["CODE_REVIEW"] = {
            "difficulty": 0.6, "mastery": 2.0, "sessions": 5, "last_grades": [2, 2]
        }
        for _ in range(20):
            professor._adjust_difficulty("CODE_REVIEW", 1.0)
        assert professor.get_difficulty("CODE_REVIEW") >= DIFFICULTY_MIN

    def test_difficulty_per_course_independent(self, professor):
        for _ in range(3):
            professor._adjust_difficulty("CODE_REVIEW", 9.0)
        professor._adjust_difficulty("RESEARCH", 5.0)
        assert professor.get_difficulty("CODE_REVIEW") > professor.get_difficulty("RESEARCH")


# ── Persistance ──────────────────────────────────────────────────────────

class TestPersistence:
    def test_save_load_grades(self, professor, tmp_path):
        professor._grades = [{"course_type": "CODE_REVIEW", "grade": 7.5}]
        professor._save_grades()
        professor._grades = []
        professor._load_grades()
        assert len(professor._grades) == 1

    def test_save_load_curriculum(self, professor, tmp_path):
        professor._curriculum = {"CODE_REVIEW": {"difficulty": 1.5, "mastery": 7.0, "sessions": 3, "last_grades": [7, 8, 7]}}
        professor._save_curriculum()
        professor._curriculum = {}
        professor._load_curriculum()
        assert professor._curriculum["CODE_REVIEW"]["difficulty"] == 1.5

    def test_missing_file_defaults(self, professor):
        professor._grades_file = "/nonexistent/path/grades.json"
        professor._curriculum_file = "/nonexistent/path/curriculum.json"
        professor._load_grades()
        professor._load_curriculum()
        assert professor._grades == []
        assert professor._curriculum == {}


# ── Mastery summary ──────────────────────────────────────────────────────

class TestMasterySummary:
    def test_empty_summary(self, professor):
        summary = professor.get_mastery_summary()
        assert summary == {}

    def test_summary_after_evaluations(self, professor):
        professor._curriculum = {
            "CODE_REVIEW": {"difficulty": 1.3, "mastery": 7.5, "sessions": 5, "last_grades": [7, 8]},
            "RESEARCH": {"difficulty": 1.0, "mastery": 6.0, "sessions": 2, "last_grades": [6]},
        }
        summary = professor.get_mastery_summary()
        assert "CODE_REVIEW" in summary
        assert summary["CODE_REVIEW"]["mastery"] == 7.5
        assert summary["CODE_REVIEW"]["sessions"] == 5


# ── Process task ─────────────────────────────────────────────────────────

class TestProcessTask:
    @pytest.mark.asyncio
    async def test_standard_payload(self, professor):
        result = await professor.process_task({
            "mission": "## Revue\nAnalyse du fichier base_agent.py avec la classe BaseAgent et la methode generate_content.",
            "course_type": "CODE_REVIEW",
            "subject": "base_agent.py",
        })
        assert result["status"] == "success"
        assert "grade" in result["result"]

    @pytest.mark.asyncio
    async def test_empty_payload(self, professor):
        result = await professor.process_task({"mission": "", "course_type": "CODE_REVIEW"})
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_deliverable_field(self, professor):
        result = await professor.process_task({
            "deliverable": "Un livrable correct avec suffisamment de contenu pour etre evalue.",
            "course_type": "RESEARCH",
        })
        assert result["status"] == "success"


# ── Parse qualitative ────────────────────────────────────────────────────

class TestParseQualitative:
    def test_standard_format(self, professor):
        response = "NOTE: 1.5\nFEEDBACK: Travail solide.\nDEFI: Complexifie ton analyse."
        result = professor._parse_qualitative_response(response)
        assert result["score"] == 1.5
        assert "solide" in result["feedback"]
        assert "Complexifie" in result["challenge"]

    def test_no_format(self, professor):
        result = professor._parse_qualitative_response("Juste du texte sans format.")
        assert result["score"] == 1.0  # Default

    def test_empty_response(self, professor):
        result = professor._parse_qualitative_response("")
        assert result["score"] == 1.0

    def test_partial_format(self, professor):
        result = professor._parse_qualitative_response("NOTE: 0.5\nPas de feedback ni defi.")
        assert result["score"] == 0.5
        assert result["feedback"] == ""


# ── Code block extraction ────────────────────────────────────────────────

class TestCodeExtraction:
    def test_extract_python_block(self, professor):
        text = "Voici le code:\n```python\ndef foo():\n    return 42\n```\nFin."
        blocks = professor._extract_code_blocks(text)
        assert len(blocks) == 1
        assert "def foo" in blocks[0]

    def test_no_code_blocks(self, professor):
        blocks = professor._extract_code_blocks("Juste du texte sans code.")
        assert blocks == []

    def test_multiple_blocks(self, professor):
        text = "```\nblock1\n```\nTexte\n```\nblock2\n```"
        blocks = professor._extract_code_blocks(text)
        assert len(blocks) == 2
