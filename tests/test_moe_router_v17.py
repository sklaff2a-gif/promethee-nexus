"""Tests V17 — Mixture of Experts Router.

Le routing par routine (ROUTINE_MODELS[slot]) prend priorite sur
AGENT_SPECIFIC_LOCAL_MODELS quand un marqueur [SCHOOL_SLOT: XXX] est
detecte dans le prompt.

Tests statiques : verifient que le dict existe, que la priorite est
codee dans base_agent.py, que les slots code-centric routent vers le
modele specialise.
"""
import inspect

from core import base_agent
import config


class TestV17ConfigRoutineModels:
    """Le dict ROUTINE_MODELS doit etre dans Config."""

    def test_routine_models_defined(self):
        assert hasattr(config.Config, "ROUTINE_MODELS"), (
            "Config.ROUTINE_MODELS doit exister"
        )

    def test_code_review_routes_to_coder(self):
        rm = config.Config.ROUTINE_MODELS
        assert rm.get("CODE_REVIEW") == "qwen2.5-coder:14b"

    def test_creation_routes_to_coder(self):
        rm = config.Config.ROUTINE_MODELS
        assert rm.get("CREATION") == "qwen2.5-coder:14b"

    def test_workshop_routes_to_coder(self):
        rm = config.Config.ROUTINE_MODELS
        assert rm.get("WORKSHOP") == "qwen2.5-coder:14b"

    def test_research_not_overridden(self):
        """RESEARCH et BULLETIN restent en 9b generique (narration/synthese)."""
        rm = config.Config.ROUTINE_MODELS
        assert "RESEARCH" not in rm
        assert "BULLETIN" not in rm


class TestV17BaseAgentRoutingLogic:
    """La logique de resolution doit etre inscrite dans base_agent.py."""

    def test_v17_marker_in_source(self):
        src = inspect.getsource(base_agent)
        assert "V17 MoE" in src

    def test_routine_models_read_from_config(self):
        src = inspect.getsource(base_agent)
        assert 'getattr(Config, "ROUTINE_MODELS"' in src

    def test_slot_regex_detection(self):
        """Le regex cherche [SCHOOL_SLOT: XXX] dans le prompt."""
        src = inspect.getsource(base_agent)
        # Le pattern exact peut varier, on cherche le marqueur
        assert "SCHOOL_SLOT:" in src

    def test_priority_routine_over_agent(self):
        """Verification que si match slot, ecrase specific_locals."""
        src = inspect.getsource(base_agent)
        idx_routine = src.find("routine_models")
        idx_specific = src.rfind("specific_locals.get(self.name")
        assert idx_routine > 0
        assert idx_specific > 0
        # Le lookup routine_models doit preceder le fallback specific_locals
        # (la logique d'override : si routine_models match, on skip specific)
        idx_if_none = src.find("if local_model is None")
        assert idx_if_none > idx_routine, (
            "Le fallback 'if local_model is None' doit etre APRES la detection routine"
        )

    def test_logger_info_moe_line(self):
        """Le routing MoE log un info pour observabilite."""
        src = inspect.getsource(base_agent)
        assert "[V17 MoE]" in src


class TestV17BackwardCompatibility:
    """L'ancien AGENT_SPECIFIC_LOCAL_MODELS doit toujours etre fonctionnel
    quand aucun slot n'est detecte (routines non-ecole)."""

    def test_agent_specific_still_defined(self):
        assert hasattr(config.Config, "AGENT_SPECIFIC_LOCAL_MODELS")
        m = config.Config.AGENT_SPECIFIC_LOCAL_MODELS
        # Les mappings historiques doivent tenir
        assert m.get("coder") == "qwen2.5-coder:14b"
        assert "strategist" in m

    def test_default_model_still_defined(self):
        assert hasattr(config.Config, "DEFAULT_LOCAL_MODEL")
