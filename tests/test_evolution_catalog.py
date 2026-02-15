import pytest
import json
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from core.evolution_catalog import (
    EvolutionCatalog, ImprovementSpec, _build_catalog, CATALOG_STATE_FILE, catalog
)

# Fixture module-level : empêche la persistance entre tests
_FAKE_STATE_FILE = os.path.join(tempfile.gettempdir(), "test_evolution_catalog_state.json")


@pytest.fixture(autouse=True)
def isolate_catalog_state():
    """Isole chaque test de la persistance catalogue."""
    if os.path.exists(_FAKE_STATE_FILE):
        os.remove(_FAKE_STATE_FILE)
    with patch("core.evolution_catalog.CATALOG_STATE_FILE", _FAKE_STATE_FILE):
        EvolutionCatalog.reset_singleton()
        yield
        EvolutionCatalog.reset_singleton()
    if os.path.exists(_FAKE_STATE_FILE):
        os.remove(_FAKE_STATE_FILE)


class TestImprovementSpec:

    def test_spec_defaults(self):
        spec = ImprovementSpec(
            id="TEST-001", name="Test", description="desc",
            category="performance", target_file="core/test.py",
            target_method="run", difficulty=1, code_template="pass",
            validation="check"
        )
        assert spec.status == "available"
        assert spec.attempts == 0
        assert spec.max_attempts == 3
        assert spec.last_attempt is None
        assert spec.deployed_at is None
        assert spec.failure_reasons == []
        assert spec.tags == []
        assert spec.dependencies == []
        assert spec.combinable_with == []

    def test_spec_with_all_fields(self):
        spec = ImprovementSpec(
            id="TEST-002", name="Full", description="full desc",
            category="resilience", target_file="core/x.py",
            target_method="y", difficulty=3, code_template="code",
            validation="validate", tags=["a", "b"],
            dependencies=["TEST-001"], combinable_with=["TEST-003"],
            status="deployed", attempts=2, max_attempts=5,
            last_attempt="2026-01-01T00:00:00",
            deployed_at="2026-01-02T00:00:00",
            failure_reasons=["err1"]
        )
        assert spec.status == "deployed"
        assert spec.attempts == 2
        assert spec.tags == ["a", "b"]
        assert spec.dependencies == ["TEST-001"]


class TestBuildCatalog:

    def test_catalog_has_28_specs(self):
        specs = _build_catalog()
        assert len(specs) == 28

    def test_all_categories_present(self):
        specs = _build_catalog()
        categories = {s.category for s in specs.values()}
        assert categories == {"performance", "resilience", "intelligence", "memory", "observability", "security"}

    def test_category_counts(self):
        specs = _build_catalog()
        by_cat = {}
        for s in specs.values():
            by_cat[s.category] = by_cat.get(s.category, 0) + 1
        assert by_cat["performance"] == 7
        assert by_cat["resilience"] == 5
        assert by_cat["intelligence"] == 5
        assert by_cat["memory"] == 4
        assert by_cat["observability"] == 4
        assert by_cat["security"] == 3

    def test_all_ids_unique(self):
        specs = _build_catalog()
        ids = list(specs.keys())
        assert len(ids) == len(set(ids))

    def test_all_specs_have_required_fields(self):
        specs = _build_catalog()
        for spec_id, spec in specs.items():
            assert spec.id == spec_id
            assert spec.name, f"{spec_id} missing name"
            assert spec.description, f"{spec_id} missing description"
            assert spec.category in ("performance", "resilience", "intelligence", "memory", "observability", "security")
            assert spec.target_file, f"{spec_id} missing target_file"
            assert spec.target_method, f"{spec_id} missing target_method"
            assert 1 <= spec.difficulty <= 3, f"{spec_id} difficulty {spec.difficulty} out of range"
            assert spec.code_template, f"{spec_id} missing code_template"
            assert spec.validation, f"{spec_id} missing validation"

    def test_all_dependencies_exist(self):
        specs = _build_catalog()
        all_ids = set(specs.keys())
        for spec_id, spec in specs.items():
            for dep in spec.dependencies:
                assert dep in all_ids, f"{spec_id} depends on {dep} which doesn't exist"

    def test_all_combinable_with_exist(self):
        specs = _build_catalog()
        all_ids = set(specs.keys())
        for spec_id, spec in specs.items():
            for combo in spec.combinable_with:
                assert combo in all_ids, f"{spec_id} combinable_with {combo} which doesn't exist"

    def test_target_files_are_valid_paths(self):
        specs = _build_catalog()
        valid_prefixes = ("core/", "Agents/", "config.py", "main.py")
        for spec_id, spec in specs.items():
            assert any(spec.target_file.startswith(p) for p in valid_prefixes), \
                f"{spec_id} target {spec.target_file} doesn't start with a valid prefix"


class TestEvolutionCatalogSingleton:

    def test_singleton_pattern(self):
        a = EvolutionCatalog()
        b = EvolutionCatalog()
        assert a is b

    def test_reset_singleton(self):
        a = EvolutionCatalog()
        EvolutionCatalog.reset_singleton()
        b = EvolutionCatalog()
        assert a is not b

    def test_initial_state(self):
        cat = EvolutionCatalog()
        assert len(cat.specs) == 28
        stats = cat.get_stats()
        assert stats["total_deployed"] == 0
        assert stats["total_failed"] == 0
        assert stats["total_attempted"] == 0


class TestSpecAccess:

    def setup_method(self):
        self.cat = EvolutionCatalog()

    def test_get_spec_existing(self):
        spec = self.cat.get_spec("PERF-001")
        assert spec is not None
        assert spec.name == "Cache LRU Router"

    def test_get_spec_nonexistent(self):
        assert self.cat.get_spec("FAKE-999") is None

    def test_get_all_specs(self):
        all_specs = self.cat.get_all_specs()
        assert len(all_specs) == 28

    def test_get_specs_by_category(self):
        perf = self.cat.get_specs_by_category("performance")
        assert len(perf) == 7
        assert all(s.category == "performance" for s in perf)

    def test_get_specs_by_status(self):
        available = self.cat.get_specs_by_status("available")
        assert len(available) == 28  # Toutes par défaut


class TestEligibility:

    def setup_method(self):
        self.cat = EvolutionCatalog()

    def test_all_initially_eligible(self):
        eligible = self.cat.get_eligible_specs()
        # OBS-004 dépend de OBS-001, donc elle n'est pas éligible
        expected = 28 - 1  # OBS-004 a une dépendance
        assert len(eligible) == expected

    def test_attempted_not_eligible_within_24h(self):
        self.cat.mark_attempted("PERF-001")
        # PERF-001 est maintenant "attempted" → pas éligible (status != available)
        # mais mark_failed le remet en available si attempts < max
        self.cat.mark_failed("PERF-001", "test")
        # Maintenant c'est available mais last_attempt < 24h
        eligible = self.cat.get_eligible_specs()
        perf001_in = any(s.id == "PERF-001" for s in eligible)
        assert not perf001_in

    def test_max_attempts_excludes(self):
        for i in range(3):
            self.cat.mark_attempted("PERF-002")
            self.cat.mark_failed("PERF-002", f"error {i}")
        # Après 3 attempts, status = "failed"
        spec = self.cat.get_spec("PERF-002")
        assert spec.status == "failed"
        eligible = self.cat.get_eligible_specs()
        assert not any(s.id == "PERF-002" for s in eligible)

    def test_deployed_not_eligible(self):
        self.cat.mark_attempted("PERF-003")
        self.cat.mark_deployed("PERF-003")
        eligible = self.cat.get_eligible_specs()
        assert not any(s.id == "PERF-003" for s in eligible)

    def test_dependency_blocks_eligibility(self):
        """OBS-004 dépend de OBS-001. Sans OBS-001 déployé, OBS-004 n'est pas éligible."""
        eligible = self.cat.get_eligible_specs()
        assert not any(s.id == "OBS-004" for s in eligible)

    def test_dependency_resolved(self):
        """OBS-004 devient éligible une fois OBS-001 déployé."""
        self.cat.mark_attempted("OBS-001")
        self.cat.mark_deployed("OBS-001")
        eligible = self.cat.get_eligible_specs()
        assert any(s.id == "OBS-004" for s in eligible)


class TestScoring:

    def setup_method(self):
        self.cat = EvolutionCatalog()

    def test_default_scoring(self):
        eligible = self.cat.get_eligible_specs()
        scored = self.cat.score_specs(eligible)
        assert len(scored) > 0
        # Scores décroissants
        scores = [s[1] for s in scored]
        assert scores == sorted(scores, reverse=True)

    def test_error_streak_boosts_resilience(self):
        state = {"error_streak": 5, "success_rate": 1.0, "cloud_budget_pct": 0, "health": "GO"}
        eligible = self.cat.get_eligible_specs()
        scored = self.cat.score_specs(eligible, system_state=state)
        # Les specs resilience devraient avoir un score plus élevé
        resilience_scores = [(s.id, sc) for s, sc in scored if s.category == "resilience"]
        other_scores = [(s.id, sc) for s, sc in scored if s.category not in ("resilience", "observability")]
        if resilience_scores and other_scores:
            avg_res = sum(sc for _, sc in resilience_scores) / len(resilience_scores)
            avg_other = sum(sc for _, sc in other_scores) / len(other_scores)
            assert avg_res > avg_other

    def test_cloud_budget_boosts_performance(self):
        state = {"error_streak": 0, "success_rate": 1.0, "cloud_budget_pct": 90, "health": "GO"}
        eligible = self.cat.get_eligible_specs()
        scored = self.cat.score_specs(eligible, system_state=state)
        perf_scores = [(s.id, sc) for s, sc in scored if s.category == "performance"]
        assert any(sc > 2.0 for _, sc in perf_scores)

    def test_difficulty_bonus(self):
        eligible = self.cat.get_eligible_specs()
        scored = self.cat.score_specs(eligible)
        scored_dict = {s.id: sc for s, sc in scored}
        # PERF-001 (diff=1) vs PERF-006 (diff=3) : PERF-001 devrait avoir un meilleur score
        if "PERF-001" in scored_dict and "PERF-006" in scored_dict:
            assert scored_dict["PERF-001"] > scored_dict["PERF-006"]

    def test_attempts_penalty(self):
        self.cat.specs["PERF-001"].attempts = 2
        eligible = self.cat.get_eligible_specs()
        scored = self.cat.score_specs(eligible)
        scored_dict = {s.id: sc for s, sc in scored}
        # PERF-001 avec 2 attempts devrait avoir un score plus bas que PERF-003 (0 attempts, même diff)
        if "PERF-001" in scored_dict and "PERF-003" in scored_dict:
            assert scored_dict["PERF-001"] < scored_dict["PERF-003"]

    def test_combo_bonus(self):
        """Si une spec combinable est déployée, bonus +1.0."""
        self.cat.mark_attempted("PERF-003")
        self.cat.mark_deployed("PERF-003")
        eligible = self.cat.get_eligible_specs()
        scored = self.cat.score_specs(eligible)
        scored_dict = {s.id: sc for s, sc in scored}
        # PERF-001 est combinable avec PERF-003 → bonus
        assert "PERF-001" in scored_dict
        assert scored_dict["PERF-001"] >= 2.5  # 1.0 base + 0.5 diff + 1.0 combo


class TestTopCandidates:

    def setup_method(self):
        self.cat = EvolutionCatalog()

    def test_returns_top_5(self):
        candidates = self.cat.get_top_candidates(5)
        assert len(candidates) == 5

    def test_returns_fewer_if_not_enough(self):
        # Marquer presque toutes les specs comme failed
        for spec in list(self.cat.specs.values())[:-2]:
            spec.status = "failed"
        candidates = self.cat.get_top_candidates(5)
        assert len(candidates) <= 2


class TestSelectionPrompt:

    def setup_method(self):
        self.cat = EvolutionCatalog()

    def test_prompt_format(self):
        candidates = self.cat.get_top_candidates(5)
        prompt = self.cat.build_selection_prompt(candidates)
        assert "1." in prompt
        assert "5." in prompt
        assert "numéro (1-5)" in prompt

    def test_prompt_contains_spec_ids(self):
        candidates = self.cat.get_top_candidates(5)
        prompt = self.cat.build_selection_prompt(candidates)
        for spec, _ in candidates:
            assert spec.id in prompt


class TestLLMChoice:

    def setup_method(self):
        self.cat = EvolutionCatalog()

    def test_parse_valid_choice(self):
        candidates = self.cat.get_top_candidates(5)
        result = self.cat.parse_llm_choice("3", candidates)
        assert result is candidates[2][0]

    def test_parse_choice_in_text(self):
        candidates = self.cat.get_top_candidates(5)
        result = self.cat.parse_llm_choice("Je choisis le numéro 2.", candidates)
        assert result is candidates[1][0]

    def test_parse_invalid_fallback_to_first(self):
        candidates = self.cat.get_top_candidates(5)
        result = self.cat.parse_llm_choice("je ne sais pas", candidates)
        assert result is candidates[0][0]

    def test_parse_out_of_range_fallback(self):
        candidates = self.cat.get_top_candidates(5)
        result = self.cat.parse_llm_choice("9", candidates)
        # 9 > 5 → fallback
        assert result is candidates[0][0]

    def test_parse_empty_response(self):
        candidates = self.cat.get_top_candidates(5)
        result = self.cat.parse_llm_choice("", candidates)
        assert result is candidates[0][0]

    def test_parse_no_candidates(self):
        result = self.cat.parse_llm_choice("1", [])
        assert result is None


class TestGetNextSpec:

    def setup_method(self):
        self.cat = EvolutionCatalog()

    def test_returns_spec(self):
        spec = self.cat.get_next_spec()
        assert spec is not None
        assert isinstance(spec, ImprovementSpec)

    def test_returns_none_when_exhausted(self):
        for s in self.cat.specs.values():
            s.status = "failed"
        spec = self.cat.get_next_spec()
        assert spec is None


class TestTracking:

    def setup_method(self):
        self.cat = EvolutionCatalog()

    def test_mark_attempted(self):
        self.cat.mark_attempted("PERF-001")
        spec = self.cat.get_spec("PERF-001")
        assert spec.status == "attempted"
        assert spec.attempts == 1
        assert spec.last_attempt is not None
        assert self.cat._stats["total_attempted"] == 1

    def test_mark_deployed(self):
        self.cat.mark_attempted("PERF-001")
        self.cat.mark_deployed("PERF-001")
        spec = self.cat.get_spec("PERF-001")
        assert spec.status == "deployed"
        assert spec.deployed_at is not None
        assert self.cat._stats["total_deployed"] == 1

    def test_mark_failed_below_max(self):
        self.cat.mark_attempted("PERF-001")
        self.cat.mark_failed("PERF-001", "syntax error")
        spec = self.cat.get_spec("PERF-001")
        assert spec.status == "available"  # Remis en available
        assert spec.failure_reasons == ["syntax error"]

    def test_mark_failed_at_max(self):
        for i in range(3):
            self.cat.mark_attempted("PERF-001")
            self.cat.mark_failed("PERF-001", f"error {i}")
        spec = self.cat.get_spec("PERF-001")
        assert spec.status == "failed"
        assert spec.attempts == 3
        assert len(spec.failure_reasons) == 3

    def test_mark_available(self):
        self.cat.mark_attempted("PERF-001")
        self.cat.mark_available("PERF-001")
        spec = self.cat.get_spec("PERF-001")
        assert spec.status == "available"

    def test_mark_nonexistent_spec(self):
        # Pas d'exception
        self.cat.mark_attempted("FAKE-999")
        self.cat.mark_deployed("FAKE-999")
        self.cat.mark_failed("FAKE-999")


class TestPersistence:
    """La fixture autouse isolate_catalog_state isole déjà le fichier via _FAKE_STATE_FILE."""

    def test_save_and_load(self):
        cat = EvolutionCatalog()
        cat.mark_attempted("PERF-001")
        cat.mark_deployed("PERF-001")
        cat.mark_attempted("RES-001")
        cat.mark_failed("RES-001", "test error")
        cat._save()

        # Vérifier que le fichier existe
        assert os.path.exists(_FAKE_STATE_FILE)

        # Charger dans une nouvelle instance
        EvolutionCatalog.reset_singleton()
        cat2 = EvolutionCatalog()

        perf = cat2.get_spec("PERF-001")
        assert perf.status == "deployed"
        assert perf.attempts == 1

        res = cat2.get_spec("RES-001")
        assert res.status == "available"
        assert res.attempts == 1
        assert res.failure_reasons == ["test error"]

    def test_load_missing_file(self):
        # Le fichier n'existe pas encore (la fixture le supprime)
        cat = EvolutionCatalog()
        assert len(cat.specs) == 28

    def test_load_corrupt_file(self):
        with open(_FAKE_STATE_FILE, "w") as f:
            f.write("not json")
        EvolutionCatalog.reset_singleton()
        cat = EvolutionCatalog()
        assert len(cat.specs) == 28

    def test_json_structure(self):
        cat = EvolutionCatalog()
        cat.mark_attempted("PERF-001")
        cat._save()

        with open(_FAKE_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "version" in data
        assert data["version"] == 1
        assert "specs" in data
        assert "PERF-001" in data["specs"]
        assert "stats" in data


class TestMetaEvolution:

    def setup_method(self):
        self.cat = EvolutionCatalog()

    def test_no_combinations_with_few_deployed(self):
        """Moins de 3 specs déployées → pas de combinaisons."""
        self.cat.mark_attempted("PERF-001")
        self.cat.mark_deployed("PERF-001")
        combos = self.cat.generate_combinations()
        assert len(combos) == 0

    def test_combinations_with_3_deployed(self):
        """3+ specs déployées → combinaisons possibles."""
        # Déployer PERF-001, PERF-003, RES-001
        # PERF-001 est combinable_with PERF-003
        self.cat.mark_attempted("PERF-001")
        self.cat.mark_deployed("PERF-001")
        self.cat.mark_attempted("PERF-003")
        self.cat.mark_deployed("PERF-003")
        self.cat.mark_attempted("RES-001")
        self.cat.mark_deployed("RES-001")

        combos = self.cat.generate_combinations()
        assert len(combos) > 0
        combo_ids = [c.id for c in combos]
        # PERF-001 + PERF-003 devrait produire une combo
        assert any("PERF-001" in cid and "PERF-003" in cid for cid in combo_ids)

    def test_combination_added_to_catalog(self):
        self.cat.mark_attempted("PERF-001")
        self.cat.mark_deployed("PERF-001")
        self.cat.mark_attempted("PERF-003")
        self.cat.mark_deployed("PERF-003")
        self.cat.mark_attempted("RES-001")
        self.cat.mark_deployed("RES-001")

        initial_count = len(self.cat.specs)
        self.cat.generate_combinations()
        assert len(self.cat.specs) > initial_count

    def test_no_duplicate_combinations(self):
        self.cat.mark_attempted("PERF-001")
        self.cat.mark_deployed("PERF-001")
        self.cat.mark_attempted("PERF-003")
        self.cat.mark_deployed("PERF-003")
        self.cat.mark_attempted("RES-001")
        self.cat.mark_deployed("RES-001")

        combos1 = self.cat.generate_combinations()
        combos2 = self.cat.generate_combinations()
        # 2e appel ne devrait pas dupliquer
        assert len(combos2) == 0

    def test_cross_target_cache(self):
        """Si une spec 'cache' est déployée, cross-target génère des variantes."""
        # PERF-001 a le tag "cache"
        self.cat.mark_attempted("PERF-001")
        self.cat.mark_deployed("PERF-001")
        self.cat.mark_attempted("PERF-003")
        self.cat.mark_deployed("PERF-003")
        self.cat.mark_attempted("RES-001")
        self.cat.mark_deployed("RES-001")

        combos = self.cat.generate_combinations()
        cross_ids = [c.id for c in combos if c.id.startswith("CROSS-")]
        assert len(cross_ids) > 0

    def test_combined_spec_difficulty_increases(self):
        self.cat.mark_attempted("PERF-001")
        self.cat.mark_deployed("PERF-001")
        self.cat.mark_attempted("PERF-003")
        self.cat.mark_deployed("PERF-003")
        self.cat.mark_attempted("RES-001")
        self.cat.mark_deployed("RES-001")

        combos = self.cat.generate_combinations()
        for combo in combos:
            if combo.id.startswith("COMBO-"):
                # La difficulté combinée = min(a.diff + 1, 3)
                assert combo.difficulty >= 2

    def test_combined_spec_is_available(self):
        self.cat.mark_attempted("PERF-001")
        self.cat.mark_deployed("PERF-001")
        self.cat.mark_attempted("PERF-003")
        self.cat.mark_deployed("PERF-003")
        self.cat.mark_attempted("RES-001")
        self.cat.mark_deployed("RES-001")

        combos = self.cat.generate_combinations()
        for combo in combos:
            assert combo.status == "available"


class TestSummary:

    def setup_method(self):
        self.cat = EvolutionCatalog()

    def test_summary_format(self):
        summary = self.cat.get_summary()
        assert "Catalogue" in summary
        assert "28" in summary
        assert "available" in summary

    def test_summary_after_changes(self):
        self.cat.mark_attempted("PERF-001")
        self.cat.mark_deployed("PERF-001")
        summary = self.cat.get_summary()
        assert "deployed" in summary


class TestSystemState:

    def setup_method(self):
        self.cat = EvolutionCatalog()

    def test_default_system_state(self):
        """Sans SelfAwareness, retourne des valeurs par défaut."""
        with patch.dict("sys.modules", {"core.self_awareness": None}):
            state = self.cat._get_system_state()
            assert state["error_streak"] == 0
            assert state["success_rate"] == 1.0
            assert state["cloud_budget_pct"] == 0
            assert state["health"] == "GO"

    def test_system_state_with_snapshot(self):
        mock_awareness = MagicMock()
        mock_awareness.get_latest_snapshot.return_value = {
            "performance": {
                "error_streak": 5,
                "success_rate": 0.4,
                "cloud_budget_used": 80,
                "cloud_budget_max": 100,
            },
            "health": {"verdict": "DEGRADED"}
        }
        mock_module = MagicMock()
        mock_module.awareness = mock_awareness

        with patch.dict("sys.modules", {"core.self_awareness": mock_module}):
            state = self.cat._get_system_state()
            assert state["error_streak"] == 5
            assert state["success_rate"] == 0.4
            assert state["cloud_budget_pct"] == 80.0
            assert state["health"] == "DEGRADED"
