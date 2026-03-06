# tests/test_roadmap_curator.py — Tests Roadmap Curator (utilitaire stateless)
import pytest
import json
import os
from datetime import datetime


@pytest.fixture
def sample_roadmap(tmp_path):
    """Cree un fichier roadmap.json temporaire avec des modules de test."""
    data = {
        "_comment": "Test roadmap",
        "modules": [
            {
                "id": "mod_a",
                "phase": 1,
                "phase_name": "FONDATIONS",
                "description": "Module A fondation",
                "status": "implemented",
                "depends_on": [],
                "estimated_cost": 4,
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
            },
            {
                "id": "mod_b",
                "phase": 1,
                "phase_name": "FONDATIONS",
                "description": "Module B fondation",
                "status": "planned",
                "depends_on": [],
                "estimated_cost": 3,
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
            },
            {
                "id": "mod_c",
                "phase": 2,
                "phase_name": "ORGANES",
                "description": "Module C organe",
                "status": "planned",
                "depends_on": ["mod_a"],
                "estimated_cost": 5,
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
            },
            {
                "id": "mod_d",
                "phase": 2,
                "phase_name": "ORGANES",
                "description": "Module D organe",
                "status": "planned",
                "depends_on": ["mod_b", "mod_c"],
                "estimated_cost": 6,
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
            },
            {
                "id": "mod_e",
                "phase": 3,
                "phase_name": "AVANCE",
                "description": "Module E avance",
                "status": "planned",
                "depends_on": ["mod_d"],
                "estimated_cost": 7,
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
            },
        ],
    }
    filepath = str(tmp_path / "roadmap.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return filepath


# ===== TestLoadRoadmap =====

class TestLoadRoadmap:
    def test_load_valid(self, sample_roadmap):
        from core.roadmap_curator import load_roadmap
        data = load_roadmap(sample_roadmap)
        assert "modules" in data
        assert len(data["modules"]) == 5

    def test_load_missing_file(self, tmp_path):
        from core.roadmap_curator import load_roadmap
        data = load_roadmap(str(tmp_path / "nonexistent.json"))
        assert data == {}

    def test_load_invalid_json(self, tmp_path):
        from core.roadmap_curator import load_roadmap
        bad_file = str(tmp_path / "bad.json")
        with open(bad_file, "w") as f:
            f.write("NOT JSON {{{")
        data = load_roadmap(bad_file)
        assert data == {}

    def test_load_empty_file(self, tmp_path):
        from core.roadmap_curator import load_roadmap
        empty = str(tmp_path / "empty.json")
        with open(empty, "w") as f:
            f.write("{}")
        data = load_roadmap(empty)
        assert data == {}

    def test_load_has_modules(self, sample_roadmap):
        from core.roadmap_curator import load_roadmap
        data = load_roadmap(sample_roadmap)
        for m in data["modules"]:
            assert "id" in m
            assert "status" in m


# ===== TestNextCandidates =====

class TestNextCandidates:
    def test_candidates_with_satisfied_deps(self, sample_roadmap):
        from core.roadmap_curator import get_next_candidates
        candidates = get_next_candidates(sample_roadmap)
        ids = [c["id"] for c in candidates]
        # mod_b (no deps, planned) et mod_c (dep mod_a implemented) sont candidats
        assert "mod_b" in ids
        assert "mod_c" in ids

    def test_implemented_not_candidate(self, sample_roadmap):
        from core.roadmap_curator import get_next_candidates
        candidates = get_next_candidates(sample_roadmap)
        ids = [c["id"] for c in candidates]
        assert "mod_a" not in ids  # deja implemente

    def test_blocked_not_candidate(self, sample_roadmap):
        from core.roadmap_curator import get_next_candidates
        candidates = get_next_candidates(sample_roadmap)
        ids = [c["id"] for c in candidates]
        # mod_d depend de mod_b (planned) et mod_c (planned) → bloque
        assert "mod_d" not in ids

    def test_deep_blocked_not_candidate(self, sample_roadmap):
        from core.roadmap_curator import get_next_candidates
        candidates = get_next_candidates(sample_roadmap)
        ids = [c["id"] for c in candidates]
        assert "mod_e" not in ids

    def test_empty_roadmap(self, tmp_path):
        from core.roadmap_curator import get_next_candidates
        empty = str(tmp_path / "empty.json")
        with open(empty, "w") as f:
            json.dump({"modules": []}, f)
        assert get_next_candidates(empty) == []

    def test_all_implemented(self, tmp_path):
        from core.roadmap_curator import get_next_candidates
        data = {"modules": [
            {"id": "x", "phase": 1, "status": "implemented", "depends_on": []},
        ]}
        fp = str(tmp_path / "r.json")
        with open(fp, "w") as f:
            json.dump(data, f)
        assert get_next_candidates(fp) == []

    def test_no_deps_always_candidate(self, tmp_path):
        from core.roadmap_curator import get_next_candidates
        data = {"modules": [
            {"id": "solo", "phase": 1, "status": "planned", "depends_on": []},
        ]}
        fp = str(tmp_path / "r.json")
        with open(fp, "w") as f:
            json.dump(data, f)
        candidates = get_next_candidates(fp)
        assert len(candidates) == 1
        assert candidates[0]["id"] == "solo"

    def test_multiple_deps_all_met(self, tmp_path):
        from core.roadmap_curator import get_next_candidates
        data = {"modules": [
            {"id": "a", "phase": 1, "status": "implemented", "depends_on": []},
            {"id": "b", "phase": 1, "status": "implemented", "depends_on": []},
            {"id": "c", "phase": 2, "status": "planned", "depends_on": ["a", "b"]},
        ]}
        fp = str(tmp_path / "r.json")
        with open(fp, "w") as f:
            json.dump(data, f)
        candidates = get_next_candidates(fp)
        assert any(c["id"] == "c" for c in candidates)


# ===== TestBlockedModules =====

class TestBlockedModules:
    def test_blocked_detected(self, sample_roadmap):
        from core.roadmap_curator import get_blocked_modules
        blocked = get_blocked_modules(sample_roadmap)
        ids = [b["id"] for b in blocked]
        assert "mod_d" in ids  # depends on mod_b (not implemented)

    def test_blocked_reason(self, sample_roadmap):
        from core.roadmap_curator import get_blocked_modules
        blocked = get_blocked_modules(sample_roadmap)
        mod_d = next(b for b in blocked if b["id"] == "mod_d")
        assert "mod_b" in mod_d["blocked_by"]

    def test_no_blocked_if_all_deps_met(self, tmp_path):
        from core.roadmap_curator import get_blocked_modules
        data = {"modules": [
            {"id": "a", "phase": 1, "status": "implemented", "depends_on": []},
            {"id": "b", "phase": 2, "status": "planned", "depends_on": ["a"]},
        ]}
        fp = str(tmp_path / "r.json")
        with open(fp, "w") as f:
            json.dump(data, f)
        assert get_blocked_modules(fp) == []

    def test_implemented_not_blocked(self, sample_roadmap):
        from core.roadmap_curator import get_blocked_modules
        blocked = get_blocked_modules(sample_roadmap)
        ids = [b["id"] for b in blocked]
        assert "mod_a" not in ids

    def test_deep_chain_blocked(self, sample_roadmap):
        from core.roadmap_curator import get_blocked_modules
        blocked = get_blocked_modules(sample_roadmap)
        ids = [b["id"] for b in blocked]
        assert "mod_e" in ids


# ===== TestPriorityOrder =====

class TestPriorityOrder:
    def test_phase_priority(self, sample_roadmap):
        from core.roadmap_curator import suggest_priority_order, get_next_candidates
        candidates = get_next_candidates(sample_roadmap)
        ordered = suggest_priority_order(candidates, sample_roadmap)
        # Phase 1 avant Phase 2
        phases = [o.get("phase", 99) for o in ordered]
        assert phases == sorted(phases)

    def test_no_deps_first(self, tmp_path):
        from core.roadmap_curator import suggest_priority_order
        candidates = [
            {"id": "x", "phase": 1, "depends_on": ["z"], "estimated_cost": 3},
            {"id": "y", "phase": 1, "depends_on": [], "estimated_cost": 3},
        ]
        ordered = suggest_priority_order(candidates, str(tmp_path / "fake.json"))
        assert ordered[0]["id"] == "y"

    def test_lower_cost_first_same_phase(self, tmp_path):
        from core.roadmap_curator import suggest_priority_order
        candidates = [
            {"id": "expensive", "phase": 1, "depends_on": [], "estimated_cost": 9},
            {"id": "cheap", "phase": 1, "depends_on": [], "estimated_cost": 2},
        ]
        ordered = suggest_priority_order(candidates, str(tmp_path / "fake.json"))
        assert ordered[0]["id"] == "cheap"

    def test_empty_candidates(self, sample_roadmap):
        from core.roadmap_curator import suggest_priority_order
        assert suggest_priority_order([], sample_roadmap) == []

    def test_impact_priority(self, tmp_path):
        from core.roadmap_curator import suggest_priority_order
        # Module avec beaucoup de dependants → plus haute priorite
        data = {"modules": [
            {"id": "a", "phase": 1, "status": "implemented", "depends_on": []},
            {"id": "hub", "phase": 1, "status": "planned", "depends_on": [], "estimated_cost": 5},
            {"id": "leaf", "phase": 1, "status": "planned", "depends_on": [], "estimated_cost": 5},
            {"id": "c1", "phase": 2, "status": "planned", "depends_on": ["hub"]},
            {"id": "c2", "phase": 2, "status": "planned", "depends_on": ["hub"]},
            {"id": "c3", "phase": 2, "status": "planned", "depends_on": ["hub"]},
        ]}
        fp = str(tmp_path / "r.json")
        with open(fp, "w") as f:
            json.dump(data, f)
        candidates = [
            {"id": "hub", "phase": 1, "depends_on": [], "estimated_cost": 5},
            {"id": "leaf", "phase": 1, "depends_on": [], "estimated_cost": 5},
        ]
        ordered = suggest_priority_order(candidates, fp)
        assert ordered[0]["id"] == "hub"

    def test_returns_all_candidates(self, sample_roadmap):
        from core.roadmap_curator import suggest_priority_order, get_next_candidates
        candidates = get_next_candidates(sample_roadmap)
        ordered = suggest_priority_order(candidates, sample_roadmap)
        assert len(ordered) == len(candidates)

    def test_none_candidates_fetches(self, sample_roadmap):
        from core.roadmap_curator import suggest_priority_order
        ordered = suggest_priority_order(None, sample_roadmap)
        assert len(ordered) > 0


# ===== TestStatusUpdate =====

class TestStatusUpdate:
    def test_update_valid(self, sample_roadmap):
        from core.roadmap_curator import update_module_status, load_roadmap
        result = update_module_status("mod_b", "implementing", sample_roadmap)
        assert result is True
        data = load_roadmap(sample_roadmap)
        mod_b = next(m for m in data["modules"] if m["id"] == "mod_b")
        assert mod_b["status"] == "implementing"

    def test_update_invalid_status(self, sample_roadmap):
        from core.roadmap_curator import update_module_status
        result = update_module_status("mod_b", "INVALID", sample_roadmap)
        assert result is False

    def test_update_nonexistent_module(self, sample_roadmap):
        from core.roadmap_curator import update_module_status
        result = update_module_status("nonexistent", "planned", sample_roadmap)
        assert result is False

    def test_update_sets_timestamp(self, sample_roadmap):
        from core.roadmap_curator import update_module_status, load_roadmap
        update_module_status("mod_b", "implementing", sample_roadmap)
        data = load_roadmap(sample_roadmap)
        mod_b = next(m for m in data["modules"] if m["id"] == "mod_b")
        assert "updated_at" in mod_b
        # Le timestamp doit etre recent
        assert "2026" in mod_b["updated_at"]

    def test_update_missing_file(self, tmp_path):
        from core.roadmap_curator import update_module_status
        result = update_module_status("mod_b", "planned", str(tmp_path / "nope.json"))
        assert result is False


# ===== TestPhaseProgress =====

class TestPhaseProgress:
    def test_progress_computed(self, sample_roadmap):
        from core.roadmap_curator import get_phase_progress
        progress = get_phase_progress(sample_roadmap)
        assert 1 in progress
        assert 2 in progress

    def test_phase1_progress(self, sample_roadmap):
        from core.roadmap_curator import get_phase_progress
        progress = get_phase_progress(sample_roadmap)
        # Phase 1: mod_a (implemented) + mod_b (planned) = 50%
        assert progress[1]["progress"] == 0.5

    def test_phase2_progress(self, sample_roadmap):
        from core.roadmap_curator import get_phase_progress
        progress = get_phase_progress(sample_roadmap)
        # Phase 2: mod_c + mod_d both planned = 0%
        assert progress[2]["progress"] == 0.0


# ===== TestDependencyGraph =====

class TestDependencyGraph:
    def test_graph_structure(self, sample_roadmap):
        from core.roadmap_curator import get_dependency_graph
        graph = get_dependency_graph(sample_roadmap)
        assert "nodes" in graph
        assert "edges" in graph

    def test_correct_node_count(self, sample_roadmap):
        from core.roadmap_curator import get_dependency_graph
        graph = get_dependency_graph(sample_roadmap)
        assert len(graph["nodes"]) == 5

    def test_edges_exist(self, sample_roadmap):
        from core.roadmap_curator import get_dependency_graph
        graph = get_dependency_graph(sample_roadmap)
        # mod_c depends on mod_a → edge from mod_a to mod_c
        edge = {"from": "mod_a", "to": "mod_c"}
        assert edge in graph["edges"]


# ===== TestIntegrity =====

class TestIntegrity:
    def test_valid_roadmap(self, sample_roadmap):
        from core.roadmap_curator import validate_roadmap_integrity
        errors = validate_roadmap_integrity(sample_roadmap)
        assert errors == []

    def test_missing_dep(self, tmp_path):
        from core.roadmap_curator import validate_roadmap_integrity
        data = {"modules": [
            {"id": "a", "phase": 1, "status": "planned", "depends_on": ["nonexistent"]},
        ]}
        fp = str(tmp_path / "r.json")
        with open(fp, "w") as f:
            json.dump(data, f)
        errors = validate_roadmap_integrity(fp)
        assert any("nonexistent" in e for e in errors)
