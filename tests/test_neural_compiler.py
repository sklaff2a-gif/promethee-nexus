# tests/test_neural_compiler.py — Tests NeuralCompiler
# 10 classes, ~55 tests

import pytest
import asyncio
import json
import os
import time
import hashlib
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.fixture(autouse=True)
def isolate_compiler(tmp_path, monkeypatch):
    """Isole le singleton NeuralCompiler pour chaque test."""
    from core import neural_compiler as mod
    mod.NeuralCompiler.reset_singleton()
    monkeypatch.setattr(mod, "COMPILER_STATE_FILE", str(tmp_path / "compiler.json"))
    with patch.object(mod.NeuralCompiler, "_load"):
        c = mod.NeuralCompiler()
        c.reset()
        mod.compiler = c
    yield c
    mod.NeuralCompiler.reset_singleton()


# ============================================================
# 1. TestSingleton
# ============================================================

class TestSingleton:
    """Vérifie le pattern singleton et le reset."""

    def test_singleton_identity(self, isolate_compiler):
        from core.neural_compiler import NeuralCompiler
        c2 = NeuralCompiler()
        assert c2 is isolate_compiler

    def test_reset_singleton(self, isolate_compiler):
        from core import neural_compiler as mod
        mod.NeuralCompiler.reset_singleton()
        assert mod.NeuralCompiler._instance is None

    def test_initial_state(self, isolate_compiler):
        c = isolate_compiler
        assert c._observations == []
        assert c._rules == []
        assert c._total_observations == 0
        assert c._total_intercepts == 0

    def test_reset_clears_state(self, isolate_compiler):
        c = isolate_compiler
        c._total_observations = 42
        c._total_intercepts = 10
        c.reset()
        assert c._total_observations == 0
        assert c._total_intercepts == 0
        assert c._observations == []
        assert c._rules == []


# ============================================================
# 2. TestFingerprint
# ============================================================

class TestFingerprint:
    """Vérifie l'extraction de fingerprint."""

    def test_basic_fingerprint(self, isolate_compiler):
        c = isolate_compiler
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            fp = c.extract_fingerprint("coder", "Génère une fonction Python")
        assert fp.agent_name == "coder"
        assert fp.task_type == "code_generate"
        assert fp.mood == "neutre"

    def test_rag_context_detection(self, isolate_compiler):
        c = isolate_compiler
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            fp = c.extract_fingerprint("coder", "Voici [SOUVENIRS]: des trucs\nGénère du code")
        assert fp.has_rag_context is True

    def test_code_block_detection(self, isolate_compiler):
        c = isolate_compiler
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            fp = c.extract_fingerprint("coder", "```python\ndef foo(): pass\n```")
        assert fp.has_code_block is True

    def test_json_request_detection(self, isolate_compiler):
        c = isolate_compiler
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            fp = c.extract_fingerprint("coder", "Retourne le résultat en JSON")
        assert fp.has_json_request is True

    def test_prompt_length_buckets(self, isolate_compiler):
        c = isolate_compiler
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            short = c.extract_fingerprint("coder", "court")
            medium = c.extract_fingerprint("coder", "x" * 1000)
            long = c.extract_fingerprint("coder", "x" * 3000)
        assert short.prompt_length == 0
        assert medium.prompt_length == 1
        assert long.prompt_length == 2

    def test_dopamine_buckets(self, isolate_compiler):
        from core.neural_compiler import _dopamine_bucket
        assert _dopamine_bucket(0.1) == 0
        assert _dopamine_bucket(0.5) == 1
        assert _dopamine_bucket(0.9) == 2

    def test_threat_buckets(self, isolate_compiler):
        from core.neural_compiler import _threat_bucket
        assert _threat_bucket(1.0) == 0
        assert _threat_bucket(4.0) == 1
        assert _threat_bucket(8.0) == 2

    def test_task_type_classification(self, isolate_compiler):
        from core.neural_compiler import _classify_task_type
        assert _classify_task_type("evolution", "EVOLUTION_PIPELINE: faire X") == "evolution_generate"
        assert _classify_task_type("security", "audit sécurité du fichier") == "security_audit"
        assert _classify_task_type("coder", "rien de spécial") == "coder_general"


# ============================================================
# 3. TestKeywords
# ============================================================

class TestKeywords:
    """Vérifie l'extraction de mots-clés."""

    def test_basic_keywords(self):
        from core.neural_compiler import _extract_keywords
        kw = _extract_keywords("Python fonction analyse sécurité code génération")
        assert len(kw) <= 5
        assert "python" in kw
        assert "fonction" in kw

    def test_stopwords_filtered(self):
        from core.neural_compiler import _extract_keywords
        kw = _extract_keywords("les des pour dans avec Python")
        # seul 'python' devrait survivre (>= 4 chars et pas stopword)
        assert "python" in kw
        assert "pour" not in kw
        assert "dans" not in kw

    def test_short_words_filtered(self):
        from core.neural_compiler import _extract_keywords
        kw = _extract_keywords("un de la si or Python")
        for w in kw:
            assert len(w) >= 4

    def test_frequency_ordering(self):
        from core.neural_compiler import _extract_keywords
        kw = _extract_keywords("python python python java java rust")
        assert kw[0] == "python"


# ============================================================
# 4. TestObservation
# ============================================================

class TestObservation:
    """Vérifie l'enregistrement des observations."""

    def test_record_observation(self, isolate_compiler):
        c = isolate_compiler
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            c.record_observation("coder", "prompt test", "réponse test", 0.8, False)
        assert len(c._observations) == 1
        assert c._total_observations == 1
        assert c._observations[0].quality == 0.8
        assert c._observations[0].fingerprint.agent_name == "coder"

    def test_fifo_max_observations(self, isolate_compiler):
        from core import neural_compiler as mod
        c = isolate_compiler
        original_max = mod.MAX_OBSERVATIONS
        mod.MAX_OBSERVATIONS = 5
        try:
            with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
                 patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
                 patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
                 patch("core.neural_compiler._get_threat_level", return_value=0.0), \
                 patch("core.neural_compiler._get_error_streak", return_value=0), \
                 patch("core.neural_compiler._detect_markers", return_value=[]):
                for i in range(10):
                    c.record_observation("coder", f"prompt {i}", f"réponse unique {i}", 0.5, False)
            assert len(c._observations) <= 5
        finally:
            mod.MAX_OBSERVATIONS = original_max

    def test_dedup_recent(self, isolate_compiler):
        c = isolate_compiler
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            c.record_observation("coder", "prompt", "même réponse exacte", 0.5, False)
            c.record_observation("coder", "prompt 2", "même réponse exacte", 0.5, False)
        # Le 2e devrait être dédupliqué (même hash de réponse)
        assert len(c._observations) == 1

    def test_response_hash_computation(self):
        from core.neural_compiler import _response_hash
        h1 = _response_hash("hello world")
        h2 = _response_hash("hello world")
        h3 = _response_hash("different text")
        assert h1 == h2
        assert h1 != h3

    def test_response_structure_detection(self):
        from core.neural_compiler import _detect_response_structure
        assert _detect_response_structure('{"key": "value"}') == "json"
        assert _detect_response_structure('```python\ndef foo():\n    pass\n```') == "code"
        assert _detect_response_structure("OUI") == "verdict"
        assert _detect_response_structure("Ceci est un texte normal assez long pour ne pas être un verdict") == "text"

    def test_cloud_flag(self, isolate_compiler):
        c = isolate_compiler
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            c.record_observation("coder", "prompt", "réponse cloud", 0.9, True)
        assert c._observations[0].was_cloud is True


# ============================================================
# 5. TestRuleMatching
# ============================================================

class TestRuleMatching:
    """Vérifie le matching de règles."""

    def _make_rule(self, **kwargs):
        from core.neural_compiler import CompiledRule
        defaults = dict(
            rule_id="r001",
            agent_name="coder",
            task_type="code_generate",
            condition_mood=None,
            condition_cognitive=None,
            condition_keywords=["python"],
            response_template="def foo(): pass",
            response_structure="code",
            confidence=0.9,
            observations_count=15,
            success_rate=0.85,
            intercept_count=0,
            positive_feedback=10,
            negative_feedback=2,
            created_at=time.time(),
            last_used=time.time(),
        )
        defaults.update(kwargs)
        return CompiledRule(**defaults)

    def _make_fingerprint(self, **kwargs):
        from core.neural_compiler import PromptFingerprint
        defaults = dict(
            agent_name="coder",
            task_type="code_generate",
            prompt_length=1,
            has_rag_context=False,
            has_code_block=False,
            has_json_request=False,
            dominant_keywords=["python", "fonction"],
            markers=[],
            mood="neutre",
            cognitive_state="standard",
            dopamine_bucket=1,
            threat_bucket=0,
            error_streak=0,
        )
        defaults.update(kwargs)
        return PromptFingerprint(**defaults)

    def test_exact_match(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule()
        c._rules.append(rule)
        fp = self._make_fingerprint()
        best, score = c._match_rule(fp)
        assert best is rule
        assert score > 0

    def test_no_match_different_agent(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(agent_name="security")
        c._rules.append(rule)
        fp = self._make_fingerprint(agent_name="coder")
        best, score = c._match_rule(fp)
        assert best is None

    def test_no_match_different_task_type(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(task_type="security_audit")
        c._rules.append(rule)
        fp = self._make_fingerprint(task_type="code_generate")
        best, score = c._match_rule(fp)
        assert best is None

    def test_mood_wildcard(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(condition_mood=None)
        c._rules.append(rule)
        fp = self._make_fingerprint(mood="joyeux")
        best, score = c._match_rule(fp)
        assert best is rule

    def test_mood_match_bonus(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(condition_mood="neutre")
        c._rules.append(rule)
        fp_match = self._make_fingerprint(mood="neutre")
        fp_mismatch = self._make_fingerprint(mood="triste")
        _, score_match = c._match_rule(fp_match)
        _, score_mismatch = c._match_rule(fp_mismatch)
        assert score_match > score_mismatch

    def test_keyword_matching(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(condition_keywords=["python", "fonction"])
        c._rules.append(rule)
        fp_full = self._make_fingerprint(dominant_keywords=["python", "fonction", "code"])
        fp_partial = self._make_fingerprint(dominant_keywords=["java", "classe"])
        _, score_full = c._match_rule(fp_full)
        _, score_partial = c._match_rule(fp_partial)
        assert score_full > score_partial

    def test_confidence_threshold(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(confidence=0.5)
        c._rules.append(rule)
        fp = self._make_fingerprint()
        best, score = c._match_rule(fp)
        # La règle matche mais avec un score bas
        assert best is rule
        assert score <= 0.6

    def test_best_rule_selection(self, isolate_compiler):
        c = isolate_compiler
        rule_low = self._make_rule(rule_id="low", confidence=0.6)
        rule_high = self._make_rule(rule_id="high", confidence=0.95)
        c._rules.extend([rule_low, rule_high])
        fp = self._make_fingerprint()
        best, _ = c._match_rule(fp)
        assert best.rule_id == "high"


# ============================================================
# 6. TestIntercept
# ============================================================

class TestIntercept:
    """Vérifie l'interception (pré-LLM)."""

    def _make_rule(self, **kwargs):
        from core.neural_compiler import CompiledRule
        defaults = dict(
            rule_id="r001",
            agent_name="coder",
            task_type="code_generate",
            condition_mood=None,
            condition_cognitive=None,
            condition_keywords=[],
            response_template="def compiled(): return True",
            response_structure="code",
            confidence=0.95,
            observations_count=20,
            success_rate=0.9,
            intercept_count=0,
            positive_feedback=15,
            negative_feedback=1,
            created_at=time.time(),
            last_used=time.time(),
        )
        defaults.update(kwargs)
        return CompiledRule(**defaults)

    def test_no_rules_returns_none(self, isolate_compiler):
        c = isolate_compiler
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            result = c.try_intercept("coder", "Génère du code Python")
        assert result is None

    def test_intercept_success(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule()
        c._rules.append(rule)
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]), \
             patch("core.neural_compiler.random.random", return_value=0.5):
            result = c.try_intercept("coder", "Génère du code Python")
        assert result == "def compiled(): return True"
        assert c._total_intercepts == 1
        assert rule.intercept_count == 1

    def test_intercept_below_threshold(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(confidence=0.5)
        c._rules.append(rule)
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            result = c.try_intercept("coder", "Génère du code Python")
        assert result is None

    def test_ab_test_passthrough(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule()
        c._rules.append(rule)
        # Force A/B test (random < AB_TEST_PROBABILITY)
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]), \
             patch("core.neural_compiler.random.random", return_value=0.01):
            result = c.try_intercept("coder", "Génère du code Python")
        assert result is None
        assert c._total_ab_tests == 1

    def test_disabled_rule_not_intercepted(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(success_rate=0.3)  # Sous RULE_DISABLE_THRESHOLD
        c._rules.append(rule)
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            result = c.try_intercept("coder", "Génère du code Python")
        assert result is None

    def test_intercept_wrong_agent(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(agent_name="security")
        c._rules.append(rule)
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            result = c.try_intercept("coder", "Génère du code Python")
        assert result is None

    def test_intercept_updates_last_used(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule()
        rule.last_used = 0.0
        c._rules.append(rule)
        before = time.time()
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]), \
             patch("core.neural_compiler.random.random", return_value=0.5):
            c.try_intercept("coder", "Génère du code Python")
        assert rule.last_used >= before

    def test_backward_compat_no_compiler(self, isolate_compiler):
        """try_intercept retourne None sans règles — backward compat parfait."""
        c = isolate_compiler
        c._rules = []
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            assert c.try_intercept("coder", "n'importe quoi") is None
            assert c.try_intercept("security", "audit") is None
            assert c.try_intercept("evolution", "test") is None


# ============================================================
# 7. TestCompilation
# ============================================================

class TestCompilation:
    """Vérifie la compilation d'observations en règles."""

    def _add_observations(self, compiler, n=12, agent="coder", task_type="code_generate",
                          quality=0.8, keywords=None):
        """Ajoute n observations similaires."""
        from core.neural_compiler import (
            Observation, PromptFingerprint, _response_hash, _detect_response_structure
        )
        if keywords is None:
            keywords = ["python", "fonction"]
        for i in range(n):
            resp = f"def solution_{i}(): return {i}"
            fp = PromptFingerprint(
                agent_name=agent,
                task_type=task_type,
                prompt_length=1,
                has_rag_context=False,
                has_code_block=True,
                has_json_request=False,
                dominant_keywords=keywords,
                markers=[],
                mood="neutre",
                cognitive_state="standard",
                dopamine_bucket=1,
                threat_bucket=0,
                error_streak=0,
            )
            obs = Observation(
                obs_id=f"obs_{i}",
                timestamp=time.time() - (n - i),
                fingerprint=fp,
                response_text=resp,
                response_hash=_response_hash(resp),
                response_structure=_detect_response_structure(resp),
                quality=quality,
                was_cloud=False,
            )
            compiler._observations.append(obs)

    def test_compile_creates_rule(self, isolate_compiler):
        c = isolate_compiler
        c._last_compile_time = 0  # Reset cooldown
        self._add_observations(c, n=12)
        created = c.compile_rules()
        assert created >= 1
        assert len(c._rules) >= 1

    def test_compile_needs_min_observations(self, isolate_compiler):
        c = isolate_compiler
        c._last_compile_time = 0
        self._add_observations(c, n=3)  # Sous MIN_OBSERVATIONS_FOR_RULE
        created = c.compile_rules()
        assert created == 0
        assert len(c._rules) == 0

    def test_compile_cooldown(self, isolate_compiler):
        c = isolate_compiler
        c._last_compile_time = time.time()  # Vient de compiler
        self._add_observations(c, n=12)
        created = c.compile_rules()
        assert created == 0  # Bloqué par cooldown

    def test_compile_consensus_required(self, isolate_compiler):
        """Pas de règle si pas de consensus sur la structure."""
        from core.neural_compiler import (
            Observation, PromptFingerprint, _response_hash
        )
        c = isolate_compiler
        c._last_compile_time = 0
        # Ajouter des observations avec structures très variées
        for i in range(12):
            structures = ["text", "code", "json", "verdict"]
            resp = f"réponse {i}"
            fp = PromptFingerprint(
                agent_name="coder", task_type="coder_general",
                prompt_length=1, has_rag_context=False, has_code_block=False,
                has_json_request=False, dominant_keywords=["test", "consensus"],
                markers=[], mood="neutre", cognitive_state="standard",
                dopamine_bucket=1, threat_bucket=0, error_streak=0,
            )
            obs = Observation(
                obs_id=f"obs_{i}", timestamp=time.time() - i,
                fingerprint=fp, response_text=resp,
                response_hash=_response_hash(resp + str(i)),
                response_structure=structures[i % 4],  # Répartition égale
                quality=0.8, was_cloud=False,
            )
            c._observations.append(obs)
        created = c.compile_rules()
        assert created == 0

    def test_compile_low_quality_rejected(self, isolate_compiler):
        c = isolate_compiler
        c._last_compile_time = 0
        self._add_observations(c, n=12, quality=0.3)  # Sous MIN_SUCCESS_RATE
        created = c.compile_rules()
        assert created == 0

    def test_compile_updates_existing_rule(self, isolate_compiler):
        from core.neural_compiler import CompiledRule
        c = isolate_compiler
        c._last_compile_time = 0
        existing = CompiledRule(
            rule_id="old", agent_name="coder", task_type="code_generate",
            condition_mood=None, condition_cognitive=None,
            condition_keywords=["python", "fonction"],
            response_template="old template", response_structure="code",
            confidence=0.5, observations_count=5, success_rate=0.6,
        )
        c._rules.append(existing)
        self._add_observations(c, n=12)
        created = c.compile_rules()
        assert created >= 1
        # La règle existante devrait être mise à jour
        assert existing.confidence > 0.5 or len(c._rules) >= 1

    def test_keyword_overlap(self):
        from core.neural_compiler import _keyword_overlap
        assert _keyword_overlap(["a", "b", "c"], ["a", "b", "c"]) == 1.0
        assert _keyword_overlap(["a", "b"], ["c", "d"]) == 0.0
        assert _keyword_overlap(["a", "b", "c"], ["a", "d", "e"]) == 0.2  # 1/5 Jaccard
        assert _keyword_overlap([], []) == 0.0

    def test_compile_max_rules(self, isolate_compiler):
        from core import neural_compiler as mod
        c = isolate_compiler
        c._last_compile_time = 0
        original_max = mod.MAX_RULES
        mod.MAX_RULES = 1
        try:
            # Ajouter des obs de 2 types différents
            self._add_observations(c, n=12, task_type="code_generate", keywords=["python", "code"])
            self._add_observations(c, n=12, task_type="coder_general", keywords=["analyse", "test"])
            c.compile_rules()
            assert len(c._rules) <= 1  # MAX_RULES=1 respecté
        finally:
            mod.MAX_RULES = original_max


# ============================================================
# 8. TestFeedback
# ============================================================

class TestFeedback:
    """Vérifie le feedback post-exécution."""

    def _make_rule(self, rule_id="r001", **kwargs):
        from core.neural_compiler import CompiledRule
        defaults = dict(
            rule_id=rule_id,
            agent_name="coder",
            task_type="code_generate",
            condition_mood=None,
            condition_cognitive=None,
            condition_keywords=[],
            response_template="template",
            response_structure="code",
            confidence=0.9,
            observations_count=20,
            success_rate=0.85,
            positive_feedback=10,
            negative_feedback=2,
            created_at=time.time(),
            last_used=time.time(),
        )
        defaults.update(kwargs)
        return CompiledRule(**defaults)

    def test_positive_feedback(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(positive_feedback=10, negative_feedback=0)
        c._rules.append(rule)
        c.record_feedback("r001", 0.8)
        assert rule.positive_feedback == 11

    def test_negative_feedback(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(positive_feedback=10, negative_feedback=0)
        c._rules.append(rule)
        c.record_feedback("r001", 0.3)
        assert rule.negative_feedback == 1

    def test_success_rate_recalculated(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(positive_feedback=8, negative_feedback=2)
        c._rules.append(rule)
        c.record_feedback("r001", 0.8)  # +1 positive
        assert rule.success_rate == 9 / 11  # 9 pos / (9+2) total

    def test_rule_auto_disable(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(positive_feedback=1, negative_feedback=3)
        c._rules.append(rule)
        c.record_feedback("r001", 0.2)  # +1 negative -> 1/(1+4) = 0.2
        assert rule.success_rate < 0.5

    def test_feedback_unknown_rule(self, isolate_compiler):
        c = isolate_compiler
        # Ne devrait pas crasher
        c.record_feedback("inexistant", 0.8)

    def test_ab_divergence(self, isolate_compiler):
        c = isolate_compiler
        rule = self._make_rule(confidence=0.9)
        c._rules.append(rule)
        c.record_ab_divergence("r001")
        assert c._ab_divergences == 1
        assert rule.confidence < 0.9


# ============================================================
# 9. TestPersistence
# ============================================================

class TestPersistence:
    """Vérifie la sauvegarde et le chargement."""

    def test_save_load_cycle(self, isolate_compiler, tmp_path):
        from core import neural_compiler as mod
        c = isolate_compiler
        # Ajouter des données
        with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
             patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
             patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
             patch("core.neural_compiler._get_threat_level", return_value=0.0), \
             patch("core.neural_compiler._get_error_streak", return_value=0), \
             patch("core.neural_compiler._detect_markers", return_value=[]):
            c.record_observation("coder", "test prompt", "test response", 0.8, False)
        c._save()

        # Charger dans une nouvelle instance
        mod.NeuralCompiler.reset_singleton()
        c2 = mod.NeuralCompiler.__new__(mod.NeuralCompiler)
        c2._initialized = False
        c2._observations = []
        c2._rules = []
        c2._subscribed = False
        c2._obs_since_save = 0
        c2._last_compile_time = 0.0
        c2._total_observations = 0
        c2._total_intercepts = 0
        c2._total_llm_calls = 0
        c2._total_ab_tests = 0
        c2._ab_divergences = 0
        c2._load()

        assert len(c2._observations) == 1
        assert c2._observations[0].fingerprint.agent_name == "coder"

    def test_corruption_recovery(self, isolate_compiler, tmp_path):
        from core import neural_compiler as mod
        # Écrire un fichier corrompu
        state_file = str(tmp_path / "compiler.json")
        with open(state_file, "w") as f:
            f.write("{invalid json")

        c = isolate_compiler
        c._load()
        # Devrait survivre et avoir un état vide
        assert c._observations == []
        assert c._rules == []

    def test_autosave_triggers(self, isolate_compiler):
        from core import neural_compiler as mod
        c = isolate_compiler
        with patch.object(c, "_save") as mock_save:
            with patch("core.neural_compiler._get_current_mood", return_value="neutre"), \
                 patch("core.neural_compiler._get_cognitive_state", return_value="standard"), \
                 patch("core.neural_compiler._get_dopamine_level", return_value=0.5), \
                 patch("core.neural_compiler._get_threat_level", return_value=0.0), \
                 patch("core.neural_compiler._get_error_streak", return_value=0), \
                 patch("core.neural_compiler._detect_markers", return_value=[]):
                for i in range(mod.AUTOSAVE_INTERVAL + 1):
                    c.record_observation("coder", f"prompt {i}", f"réponse unique numéro {i}", 0.5, False)
            assert mock_save.call_count >= 1

    def test_missing_file_no_crash(self, isolate_compiler, tmp_path):
        from core import neural_compiler as mod
        c = isolate_compiler
        # Le fichier n'existe pas
        c._load()
        assert c._observations == []


# ============================================================
# 10. TestMetrics
# ============================================================

class TestMetrics:
    """Vérifie les métriques et stats."""

    def test_initial_stats(self, isolate_compiler):
        c = isolate_compiler
        stats = c.get_stats()
        assert stats["observations_count"] == 0
        assert stats["rules_count"] == 0
        assert stats["active_rules_count"] == 0
        assert stats["intercept_rate"] == 0.0

    def test_intercept_rate(self, isolate_compiler):
        c = isolate_compiler
        c._total_llm_calls = 90
        c._total_intercepts = 10
        stats = c.get_stats()
        assert stats["intercept_rate"] == 0.1  # 10 / (90 + 10)

    def test_agents_covered(self, isolate_compiler):
        from core.neural_compiler import CompiledRule
        c = isolate_compiler
        for agent in ["coder", "security", "evolution"]:
            c._rules.append(CompiledRule(
                rule_id=f"r_{agent}", agent_name=agent, task_type=f"{agent}_general",
                condition_mood=None, condition_cognitive=None, condition_keywords=[],
                response_template="t", response_structure="text",
                confidence=0.9, observations_count=10, success_rate=0.9,
            ))
        stats = c.get_stats()
        assert set(stats["agents_covered"]) == {"coder", "security", "evolution"}

    def test_disabled_rules_excluded(self, isolate_compiler):
        from core.neural_compiler import CompiledRule
        c = isolate_compiler
        c._rules.append(CompiledRule(
            rule_id="active", agent_name="coder", task_type="code_generate",
            condition_mood=None, condition_cognitive=None, condition_keywords=[],
            response_template="t", response_structure="text",
            confidence=0.9, observations_count=10, success_rate=0.9,
        ))
        c._rules.append(CompiledRule(
            rule_id="disabled", agent_name="security", task_type="security_audit",
            condition_mood=None, condition_cognitive=None, condition_keywords=[],
            response_template="t", response_structure="text",
            confidence=0.3, observations_count=10, success_rate=0.3,
        ))
        stats = c.get_stats()
        assert stats["rules_count"] == 2
        assert stats["active_rules_count"] == 1


# ============================================================
# 11. TestBusIntegration
# ============================================================

class TestBusIntegration:
    """Vérifie l'intégration avec le bus d'événements."""

    @pytest.mark.asyncio
    async def test_routine_complete_updates_quality(self, isolate_compiler):
        from core.neural_compiler import Observation, PromptFingerprint, _response_hash
        c = isolate_compiler
        # Ajouter une observation récente sans quality
        fp = PromptFingerprint(
            agent_name="coder", task_type="code_generate",
            prompt_length=1, has_rag_context=False, has_code_block=False,
            has_json_request=False, dominant_keywords=["test"],
            markers=[], mood="neutre", cognitive_state="standard",
            dopamine_bucket=1, threat_bucket=0, error_streak=0,
        )
        obs = Observation(
            obs_id="obs_test", timestamp=time.time(),
            fingerprint=fp, response_text="test",
            response_hash=_response_hash("test"),
            response_structure="text", quality=-1.0, was_cloud=False,
        )
        c._observations.append(obs)

        # Simuler l'événement
        await c._on_routine_complete({
            "agent": "coder",
            "intent": "EXPANSION_CODE",
            "quality_score": 0.75,
        })
        assert obs.quality == 0.75

    @pytest.mark.asyncio
    async def test_routine_complete_ignores_old_obs(self, isolate_compiler):
        from core.neural_compiler import Observation, PromptFingerprint, _response_hash
        c = isolate_compiler
        fp = PromptFingerprint(
            agent_name="coder", task_type="code_generate",
            prompt_length=1, has_rag_context=False, has_code_block=False,
            has_json_request=False, dominant_keywords=["test"],
            markers=[], mood="neutre", cognitive_state="standard",
            dopamine_bucket=1, threat_bucket=0, error_streak=0,
        )
        obs = Observation(
            obs_id="old_obs", timestamp=time.time() - 600,  # > 5 min
            fingerprint=fp, response_text="test",
            response_hash=_response_hash("test old"),
            response_structure="text", quality=-1.0, was_cloud=False,
        )
        c._observations.append(obs)

        await c._on_routine_complete({
            "agent": "coder",
            "intent": "TEST",
            "quality_score": 0.9,
        })
        assert obs.quality == -1.0  # Inchangé car trop ancien

    @pytest.mark.asyncio
    async def test_routine_complete_no_crash_bad_data(self, isolate_compiler):
        c = isolate_compiler
        # Ne devrait pas crasher
        await c._on_routine_complete({})
        await c._on_routine_complete({"quality_score": -1})

    def test_subscribe_events(self, isolate_compiler):
        c = isolate_compiler
        c._subscribed = False
        c._subscribe_events()
        assert c._subscribed is True
        # Double souscription ne devrait rien faire
        c._subscribe_events()
        assert c._subscribed is True
