# tests/test_curiosity_reflex.py — Tests CuriosityReflex
"""Tests pour le reflexe instinctif d'apprentissage."""

import json
import time
import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from dataclasses import asdict

from core.curiosity_reflex import (
    CuriosityReflex,
    CuriosityQuery,
    CURIOSITY_STATE_FILE,
    CURIOSITE_DEPRIVATION_THRESHOLD,
    DAILY_QUERY_CAP,
    PER_QUESTION_COOLDOWN,
    GLOBAL_COOLDOWN,
    MIN_ANSWER_LENGTH,
    MIN_QUALITY_SCORE,
    WEB_SEARCH_QUALITY_THRESHOLD,
    HISTORY_MAX,
    IGNORANCE_MARKERS,
    QUESTION_TEMPLATES,
)


# --- Fixture autouse ---

@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.curiosity_reflex.CURIOSITY_STATE_FILE",
        str(tmp_path / "curiosity_reflex_state.json"),
    )
    CuriosityReflex.reset_singleton()
    with patch.object(CuriosityReflex, "_load"):
        cr = CuriosityReflex()
    yield cr
    CuriosityReflex.reset_singleton()


# Helper : creer un mock DesireEngine avec CURIOSITE haute
def _mock_desires(deprivation=70.0):
    mock = MagicMock()
    drive = MagicMock()
    drive.deprivation = deprivation
    mock.drives = {"CURIOSITE": drive}
    return mock


# ================================================================
# TestSingleton
# ================================================================

class TestSingleton:
    def test_singleton_identity(self, isolate):
        """Deux appels retournent le meme objet."""
        cr2 = CuriosityReflex()
        assert cr2 is isolate

    def test_reset_creates_new(self, isolate):
        """reset_singleton() cree une nouvelle instance."""
        old_id = id(isolate)
        CuriosityReflex.reset_singleton()
        with patch.object(CuriosityReflex, "_load"):
            new_cr = CuriosityReflex()
        assert id(new_cr) != old_id

    def test_reinit_preserves_state(self, isolate):
        """Re-init ne reinitialise pas l'etat."""
        isolate._total_queries = 42
        cr2 = CuriosityReflex()
        assert cr2._total_queries == 42


# ================================================================
# TestShouldTrigger
# ================================================================

class TestShouldTrigger:
    def _setup_trigger(self, isolate, deprivation=70.0):
        """Configure l'etat pour que _should_trigger retourne True."""
        isolate._processing = False
        isolate._daily_queries = 0
        from datetime import date
        isolate._daily_date = date.today().isoformat()
        isolate._last_query_time = 0.0
        isolate._topic_cooldowns = {}
        isolate._history = []
        return _mock_desires(deprivation)

    def test_daily_cap_reached(self, isolate):
        """Cap quotidien atteint → False."""
        mock_d = self._setup_trigger(isolate)
        isolate._daily_queries = DAILY_QUERY_CAP
        with patch("core.desire_engine.desires", mock_d):
            assert isolate._should_trigger("test topic") is False

    def test_global_cooldown(self, isolate):
        """Cooldown global pas ecoule → False."""
        mock_d = self._setup_trigger(isolate)
        isolate._last_query_time = time.time()  # Juste maintenant
        with patch("core.desire_engine.desires", mock_d):
            assert isolate._should_trigger("test topic") is False

    def test_topic_cooldown(self, isolate):
        """Cooldown per-topic pas ecoule → False."""
        mock_d = self._setup_trigger(isolate)
        isolate._topic_cooldowns = {"test topic": time.time()}
        with patch("core.desire_engine.desires", mock_d):
            assert isolate._should_trigger("test topic") is False

    def test_low_deprivation(self, isolate):
        """CURIOSITE deprivation trop basse → False."""
        mock_d = self._setup_trigger(isolate, deprivation=30.0)
        with patch("core.desire_engine.desires", mock_d):
            assert isolate._should_trigger("test topic") is False

    def test_circuit_breaker_open(self, isolate):
        """Circuit breaker Ollama ouvert → False."""
        mock_d = self._setup_trigger(isolate)
        with patch("core.desire_engine.desires", mock_d), \
             patch("core.base_agent.BaseAgent._ollama_circuit_is_open", return_value=True):
            assert isolate._should_trigger("test topic") is False

    def test_already_learned(self, isolate):
        """Sujet deja appris dans l'historique → False."""
        mock_d = self._setup_trigger(isolate)
        q = CuriosityQuery(topic="test topic", resolved=True)
        isolate._history = [q]
        with patch("core.desire_engine.desires", mock_d):
            assert isolate._should_trigger("test topic") is False

    def test_concurrent_block(self, isolate):
        """Processing deja en cours → False."""
        mock_d = self._setup_trigger(isolate)
        isolate._processing = True
        with patch("core.desire_engine.desires", mock_d):
            assert isolate._should_trigger("test topic") is False

    def test_all_conditions_pass(self, isolate):
        """Toutes les conditions OK → True."""
        mock_d = self._setup_trigger(isolate)
        with patch("core.desire_engine.desires", mock_d), \
             patch("core.base_agent.BaseAgent._ollama_circuit_is_open", return_value=False):
            assert isolate._should_trigger("test topic") is True


# ================================================================
# TestFormulateQuestion
# ================================================================

class TestFormulateQuestion:
    def test_gap_template(self, isolate):
        """KNOWLEDGE_GAP_DETECTED utilise le bon template."""
        q = isolate._formulate_question("decorators", "KNOWLEDGE_GAP_DETECTED")
        assert "decorators" in q
        assert "Explique" in q

    def test_spark_template(self, isolate):
        """CURIOSITY_SPARK utilise le bon template."""
        q = isolate._formulate_question("asyncio", "CURIOSITY_SPARK")
        assert "asyncio" in q
        assert "Definition" in q or "Qu'est-ce" in q

    def test_council_template(self, isolate):
        """COUNCIL_QUESTION utilise le bon template."""
        q = isolate._formulate_question("microservices", "COUNCIL_QUESTION")
        assert "microservices" in q
        assert "Resume" in q


# ================================================================
# TestEvaluateQuality
# ================================================================

class TestEvaluateQuality:
    def test_empty_answer(self, isolate):
        """Reponse vide → 0.0."""
        assert isolate._evaluate_quality("question?", "") == 0.0

    def test_too_short(self, isolate):
        """Reponse trop courte → score bas."""
        score = isolate._evaluate_quality("question?", "Oui.")
        assert score <= MIN_QUALITY_SCORE

    def test_good_answer(self, isolate):
        """Bonne reponse avec tous les criteres → score >= 0.8."""
        answer = (
            "Les decorateurs Python permettent de modifier le comportement d'une fonction "
            "sans changer son code source. Ils sont definis avec le symbole @ et peuvent "
            "etre utilises pour le logging, la mise en cache, ou la validation des arguments."
        )
        score = isolate._evaluate_quality("Explique brievement: decorateurs", answer)
        assert score >= 0.8

    def test_ignorance_markers(self, isolate):
        """Reponse avec marqueurs d'ignorance → score reduit."""
        answer = (
            "Je ne sais pas exactement ce que sont les decorateurs. "
            "Je n'ai pas assez d'information pour repondre correctement a cette question."
        )
        score = isolate._evaluate_quality("Explique: decorateurs", answer)
        # Marqueur present → perd 1 point sur 5 (score 4/5 = 0.8 max)
        assert score <= 0.8

    def test_non_latin_ratio(self, isolate):
        """Trop de caracteres non-latins → score reduit."""
        # Plus de 10% de caracteres non-latins
        base = "Decorateurs " * 5
        non_latin = "\u4e00\u4e01\u4e02\u4e03\u4e04" * 20  # Caracteres CJK
        answer = base + non_latin
        score = isolate._evaluate_quality("question?", answer)
        assert score < 1.0

    def test_no_keyword_overlap(self, isolate):
        """Aucun mot commun question/reponse → perd 1 point."""
        answer = (
            "Le temps est magnifique aujourd'hui. "
            "Les oiseaux chantent dans le jardin. "
            "Le soleil brille fort dans le ciel bleu."
        )
        score = isolate._evaluate_quality("Explique: decorateurs Python", answer)
        assert score <= 0.8

    def test_single_sentence(self, isolate):
        """Une seule phrase → perd le critere structure."""
        answer = "Les decorateurs Python modifient le comportement des fonctions en les wrappant dans une closure"
        score = isolate._evaluate_quality("decorateurs", answer)
        # Devrait perdre le critere >= 2 phrases
        assert score <= 0.8

    def test_composite_good(self, isolate):
        """Score composite avec 5/5 criteres valides → 1.0."""
        answer = (
            "Les decorateurs sont un pattern Python tres utile. "
            "Ils permettent de modifier les fonctions existantes. "
            "On les utilise avec la syntaxe @decorator avant la definition."
        )
        score = isolate._evaluate_quality("Explique decorateurs Python", answer)
        assert score == 1.0


# ================================================================
# TestFireReflex
# ================================================================

class TestFireReflex:
    @pytest.mark.asyncio
    async def test_success_ollama(self, isolate):
        """Ollama donne une bonne reponse → resolved, pas d'escalade."""
        good_answer = (
            "Les generateurs Python sont des fonctions speciales. "
            "Ils utilisent yield pour produire des valeurs paresseusement. "
            "Tres utile pour traiter de grands ensembles de donnees."
        )
        with patch.object(isolate, "_ask_ollama", new_callable=AsyncMock, return_value=good_answer), \
             patch.object(isolate, "_store_learning", new_callable=AsyncMock), \
             patch.object(isolate, "_satisfy_curiosity", new_callable=AsyncMock), \
             patch.object(isolate, "_save"), \
             patch("core.curiosity_reflex.bus", MagicMock(publish=AsyncMock())):
            await isolate._fire_reflex("generateurs Python", "KNOWLEDGE_GAP_DETECTED")

        assert len(isolate._history) == 1
        assert isolate._history[0].resolved is True
        assert isolate._history[0].escalated_to == ""

    @pytest.mark.asyncio
    async def test_web_escalation(self, isolate):
        """Reponse Ollama partielle → escalade web search."""
        partial_answer = (
            "Les generateurs Python utilisent yield. "
            "C'est une technique utile."
        )  # Score ~0.6 mais courte → score entre 0.3 et 0.4
        # On force le score dans la zone d'escalade web
        web_answer = "Detailed web result about generators with comprehensive explanation and examples of yield usage."

        with patch.object(isolate, "_ask_ollama", new_callable=AsyncMock, return_value=partial_answer), \
             patch.object(isolate, "_evaluate_quality", return_value=0.35), \
             patch.object(isolate, "_ask_web", new_callable=AsyncMock, return_value=web_answer), \
             patch.object(isolate, "_store_learning", new_callable=AsyncMock), \
             patch.object(isolate, "_satisfy_curiosity", new_callable=AsyncMock), \
             patch.object(isolate, "_save"), \
             patch("core.curiosity_reflex.bus", MagicMock(publish=AsyncMock())):
            await isolate._fire_reflex("generateurs", "CURIOSITY_SPARK")

        assert len(isolate._history) == 1
        assert isolate._history[0].escalated_to == "web_search"
        assert isolate._history[0].resolved is True

    @pytest.mark.asyncio
    async def test_incubation_fallback(self, isolate):
        """Web search echoue apres escalade → incubation."""
        with patch.object(isolate, "_ask_ollama", new_callable=AsyncMock, return_value="Bref."), \
             patch.object(isolate, "_evaluate_quality", return_value=0.35), \
             patch.object(isolate, "_ask_web", new_callable=AsyncMock, return_value=""), \
             patch.object(isolate, "_incubate", new_callable=AsyncMock) as mock_incub, \
             patch.object(isolate, "_save"), \
             patch("core.curiosity_reflex.bus", MagicMock(publish=AsyncMock())):
            await isolate._fire_reflex("topic obscur", "KNOWLEDGE_GAP_DETECTED")

        mock_incub.assert_called_once()
        assert isolate._history[0].escalated_to == "incubation"
        assert isolate._history[0].resolved is False

    @pytest.mark.asyncio
    async def test_low_quality_direct_incubation(self, isolate):
        """Score tres bas → incubation directe (pas de web)."""
        with patch.object(isolate, "_ask_ollama", new_callable=AsyncMock, return_value=""), \
             patch.object(isolate, "_incubate", new_callable=AsyncMock) as mock_incub, \
             patch.object(isolate, "_ask_web", new_callable=AsyncMock) as mock_web, \
             patch.object(isolate, "_save"), \
             patch("core.curiosity_reflex.bus", MagicMock(publish=AsyncMock())):
            await isolate._fire_reflex("concept inconnu", "CURIOSITY_SPARK")

        mock_incub.assert_called_once()
        mock_web.assert_not_called()

    @pytest.mark.asyncio
    async def test_recording(self, isolate):
        """Le reflexe enregistre correctement la query."""
        good_answer = (
            "Explication detaillee sur les metaclasses Python. "
            "Elles permettent de creer des classes dynamiquement. "
            "Utile pour les frameworks et la metaprogrammation avancee."
        )
        with patch.object(isolate, "_ask_ollama", new_callable=AsyncMock, return_value=good_answer), \
             patch.object(isolate, "_store_learning", new_callable=AsyncMock), \
             patch.object(isolate, "_satisfy_curiosity", new_callable=AsyncMock), \
             patch.object(isolate, "_save"), \
             patch("core.curiosity_reflex.bus", MagicMock(publish=AsyncMock())):
            await isolate._fire_reflex("metaclasses", "KNOWLEDGE_GAP_DETECTED")

        assert isolate._total_queries == 1
        assert isolate._daily_queries == 1
        assert isolate._last_query_time > 0

    @pytest.mark.asyncio
    async def test_bus_event_published(self, isolate):
        """CURIOSITY_LEARNING est publie sur le bus."""
        good_answer = (
            "Les context managers Python gerent les ressources. "
            "Avec with, on garantit la liberation des ressources. "
            "Tres utile pour fichiers, connexions et locks."
        )
        mock_bus = MagicMock(publish=AsyncMock())
        with patch.object(isolate, "_ask_ollama", new_callable=AsyncMock, return_value=good_answer), \
             patch.object(isolate, "_store_learning", new_callable=AsyncMock), \
             patch.object(isolate, "_satisfy_curiosity", new_callable=AsyncMock), \
             patch.object(isolate, "_save"), \
             patch("core.curiosity_reflex.bus", mock_bus):
            await isolate._fire_reflex("context managers", "CURIOSITY_SPARK")

        mock_bus.publish.assert_called_once()
        call_args = mock_bus.publish.call_args
        assert call_args[0][0] == "CURIOSITY_LEARNING"
        payload = call_args[0][1]
        assert payload["topic"] == "context managers"
        assert payload["resolved"] is True


# ================================================================
# TestAskOllama
# ================================================================

class TestAskOllama:
    @pytest.mark.asyncio
    async def test_success(self, isolate):
        """Appel Ollama reussi retourne la reponse."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "Bonne reponse sur Python."}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("core.base_agent.BaseAgent._get_ollama_semaphore", return_value=asyncio.Semaphore(2)), \
             patch("core.base_agent.BaseAgent._ollama_record_success"), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await isolate._ask_ollama("Question test")

        assert result == "Bonne reponse sur Python."

    @pytest.mark.asyncio
    async def test_timeout(self, isolate):
        """Timeout Ollama retourne chaine vide."""
        import httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))

        with patch("core.base_agent.BaseAgent._get_ollama_semaphore", return_value=asyncio.Semaphore(2)), \
             patch("core.base_agent.BaseAgent._ollama_record_timeout"), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await isolate._ask_ollama("Question test")

        assert result == ""

    @pytest.mark.asyncio
    async def test_circuit_breaker_recorded(self, isolate):
        """Timeout enregistre dans le circuit breaker."""
        import httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))

        with patch("core.base_agent.BaseAgent._get_ollama_semaphore", return_value=asyncio.Semaphore(2)), \
             patch("core.base_agent.BaseAgent._ollama_record_timeout") as mock_record, \
             patch("httpx.AsyncClient", return_value=mock_client):
            await isolate._ask_ollama("Question")

        mock_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_semaphore_used(self, isolate):
        """Le semaphore Ollama est utilise."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        sem = asyncio.Semaphore(2)
        with patch("core.base_agent.BaseAgent._get_ollama_semaphore", return_value=sem), \
             patch("core.base_agent.BaseAgent._ollama_record_success"), \
             patch("httpx.AsyncClient", return_value=mock_client):
            await isolate._ask_ollama("Test")

        # Si le semaphore a ete acquire et release correctement, il reste a 2
        assert sem._value == 2


# ================================================================
# TestEscalation
# ================================================================

class TestEscalation:
    @pytest.mark.asyncio
    async def test_web_success(self, isolate):
        """Web search retourne un bon resultat."""
        mock_surfer = MagicMock()
        mock_surfer.search.return_value = [
            {"snippet": "Python decorators wrap functions to extend behavior without modification. " * 2},
            {"snippet": "They are commonly used for logging, caching, and access control in Python applications."},
        ]
        with patch("core.capabilities.web_surfer.WebSurfer", return_value=mock_surfer):
            result = await isolate._ask_web("decorators Python")

        assert len(result) > 0
        assert "decorators" in result.lower() or "Python" in result

    @pytest.mark.asyncio
    async def test_web_fail_returns_empty(self, isolate):
        """Web search echoue → chaine vide."""
        with patch("core.capabilities.web_surfer.WebSurfer", side_effect=Exception("No network")):
            result = await isolate._ask_web("test topic")

        assert result == ""

    @pytest.mark.asyncio
    async def test_incubation_deposit(self, isolate):
        """Incubation depose le sujet dans l'incubateur."""
        mock_incub = MagicMock()
        mock_incub.deposit = MagicMock()
        with patch("core.incubation_cognitive.incubation", mock_incub):
            await isolate._incubate("sujet complexe")

        mock_incub.deposit.assert_called_once_with("sujet complexe", source="curiosity_reflex")


# ================================================================
# TestScoringBonus
# ================================================================

class TestScoringBonus:
    def test_no_history_returns_zero(self, isolate):
        """Pas d'historique → bonus 0."""
        assert isolate.compute_curiosity_bonus("VEILLE_SILENCIEUSE") == 0.0

    def test_recent_resolved_boost(self, isolate):
        """Queries recentes resolues → bonus > 0."""
        # Ajouter des queries resolues recentes
        for i in range(3):
            q = CuriosityQuery(
                topic=f"topic_{i}",
                asked_at=time.time() - 100,  # Il y a 100s
                resolved=True,
            )
            isolate._history.append(q)

        bonus = isolate.compute_curiosity_bonus("VEILLE_SILENCIEUSE")
        assert bonus > 0.0
        assert bonus <= 1.5

    def test_old_queries_no_boost(self, isolate):
        """Queries anciennes (>2h) → pas de bonus."""
        q = CuriosityQuery(
            topic="old_topic",
            asked_at=time.time() - 10000,  # Il y a ~3h
            resolved=True,
        )
        isolate._history.append(q)

        bonus = isolate.compute_curiosity_bonus("VEILLE_SILENCIEUSE")
        assert bonus == 0.0


# ================================================================
# TestContext
# ================================================================

class TestContext:
    def test_empty_history(self, isolate):
        """Pas d'historique → contexte vide."""
        assert isolate.get_curiosity_context() == ""

    def test_with_recent_topics(self, isolate):
        """Topics recents resolus → contexte non vide."""
        for topic in ["decorateurs", "asyncio", "metaclasses"]:
            q = CuriosityQuery(
                topic=topic,
                asked_at=time.time() - 60,
                resolved=True,
            )
            isolate._history.append(q)

        ctx = isolate.get_curiosity_context()
        assert "[CURIOSITE]" in ctx
        assert "decorateurs" in ctx or "asyncio" in ctx or "metaclasses" in ctx


# ================================================================
# TestPersistence
# ================================================================

class TestPersistence:
    def test_roundtrip(self, isolate, tmp_path, monkeypatch):
        """Save + load preservent l'etat."""
        state_file = str(tmp_path / "curiosity_state_rt.json")
        monkeypatch.setattr("core.curiosity_reflex.CURIOSITY_STATE_FILE", state_file)

        isolate._total_queries = 5
        isolate._daily_queries = 3
        from datetime import date
        isolate._daily_date = date.today().isoformat()
        q = CuriosityQuery(id="abc123", topic="test", asked_at=1000.0, resolved=True)
        isolate._history = [q]
        isolate._save()

        # Charger dans une nouvelle instance
        CuriosityReflex.reset_singleton()
        with patch.object(CuriosityReflex, "_load"):
            cr2 = CuriosityReflex()
        cr2._load()
        assert cr2._total_queries == 5
        assert cr2._daily_queries == 3
        assert len(cr2._history) == 1
        assert cr2._history[0].topic == "test"

    def test_missing_file(self, isolate, tmp_path, monkeypatch):
        """Fichier absent → pas d'erreur."""
        state_file = str(tmp_path / "nonexistent.json")
        monkeypatch.setattr("core.curiosity_reflex.CURIOSITY_STATE_FILE", state_file)
        isolate._load()  # Ne doit pas lever d'exception

    def test_corrupt_file(self, isolate, tmp_path, monkeypatch):
        """Fichier corrompu → pas d'erreur."""
        state_file = str(tmp_path / "corrupt.json")
        monkeypatch.setattr("core.curiosity_reflex.CURIOSITY_STATE_FILE", state_file)
        with open(state_file, "w") as f:
            f.write("{invalid json")
        isolate._load()  # Ne doit pas lever d'exception

    def test_cooldowns_persisted(self, isolate, tmp_path, monkeypatch):
        """Les cooldowns per-topic sont persistes."""
        state_file = str(tmp_path / "cooldowns_test.json")
        monkeypatch.setattr("core.curiosity_reflex.CURIOSITY_STATE_FILE", state_file)

        isolate._topic_cooldowns = {"test": 1234.0, "other": 5678.0}
        isolate._save()

        CuriosityReflex.reset_singleton()
        with patch.object(CuriosityReflex, "_load"):
            cr2 = CuriosityReflex()
        cr2._load()
        assert cr2._topic_cooldowns.get("test") == 1234.0
        assert cr2._topic_cooldowns.get("other") == 5678.0


# ================================================================
# TestBusHandlers
# ================================================================

class TestBusHandlers:
    @pytest.mark.asyncio
    async def test_knowledge_gap_triggers(self, isolate):
        """KNOWLEDGE_GAP_DETECTED cree une task de trigger."""
        with patch.object(isolate, "_maybe_trigger", new_callable=AsyncMock) as mock_trigger:
            # Simuler le handler directement (sans passer par create_task)
            event = {"topic": "design patterns"}
            # Appeler le handler qui fait create_task
            with patch("asyncio.create_task") as mock_ct:
                await isolate._on_knowledge_gap(event)
            mock_ct.assert_called_once()

    @pytest.mark.asyncio
    async def test_curiosity_spark_triggers(self, isolate):
        """CURIOSITY_SPARK cree une task de trigger."""
        with patch("asyncio.create_task") as mock_ct:
            await isolate._on_curiosity_spark({"topic": "SOLID principles"})
        mock_ct.assert_called_once()

    @pytest.mark.asyncio
    async def test_council_question_triggers(self, isolate):
        """COUNCIL_QUESTION cree une task de trigger."""
        with patch("asyncio.create_task") as mock_ct:
            await isolate._on_council_question({"topic": "event sourcing"})
        mock_ct.assert_called_once()


# ================================================================
# TestStats
# ================================================================

class TestStats:
    def test_stats_structure(self, isolate):
        """get_stats retourne les cles attendues."""
        stats = isolate.get_stats()
        assert "total_queries" in stats
        assert "daily_queries" in stats
        assert "daily_cap" in stats
        assert "recent_resolved_2h" in stats
        assert "history_size" in stats
        assert "processing" in stats
        assert "last_topics" in stats

    def test_stats_values(self, isolate):
        """Les valeurs sont coherentes."""
        from datetime import date
        isolate._total_queries = 10
        isolate._daily_queries = 3
        isolate._daily_date = date.today().isoformat()
        q = CuriosityQuery(topic="test", asked_at=time.time() - 60, resolved=True)
        isolate._history = [q]

        stats = isolate.get_stats()
        assert stats["total_queries"] == 10
        assert stats["daily_queries"] == 3
        assert stats["recent_resolved_2h"] == 1
        assert stats["history_size"] == 1
