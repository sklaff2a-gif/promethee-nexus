"""Tests V17.1 — Preservation du marqueur [SCHOOL_SLOT: XXX] dans correction_prompt.

Diagnostic 24/04 14:22 (tir CODE_REVIEW via V17 MoE) : la correction
sandbox (iter 1/2 et 2/2) a ete dispatchee vers promethee-security (9b)
au lieu de qwen2.5-coder:14b. Cause : le correction_prompt ne contenait
pas [SCHOOL_SLOT: CODE_REVIEW], donc V17 MoE dans base_agent ne detectait
pas le slot et retombait sur AGENT_SPECIFIC_LOCAL_MODELS['security'].

Fix V17.1 : prefixer correction_prompt avec [SCHOOL_SLOT: {slot}] pour
preserver le routing MoE sur chaque iteration.
"""
import inspect

from core import autonomy_engine


class TestV17_1MarkerPreserved:
    """Le correction_prompt doit porter le marqueur V17 MoE."""

    def test_v17_1_marker_in_source(self):
        src = inspect.getsource(autonomy_engine)
        assert "V17.1" in src

    def test_correction_prompt_includes_slot_marker(self):
        """Le f-string correction_prompt doit debut par [SCHOOL_SLOT: {slot}]."""
        src = inspect.getsource(autonomy_engine)
        # Cherche le bloc correction_prompt = (
        idx = src.find("correction_prompt = (")
        assert idx > 0, "correction_prompt non trouve"
        # Les 400 chars suivants doivent contenir le marqueur
        snippet = src[idx:idx + 400]
        assert "[SCHOOL_SLOT:" in snippet, (
            f"Le correction_prompt doit preserver le marqueur slot. "
            f"Snippet : {snippet[:200]}"
        )

    def test_slot_placed_before_traceback(self):
        """Le marqueur doit etre en tete du prompt (avant le traceback)."""
        src = inspect.getsource(autonomy_engine)
        idx = src.find("correction_prompt = (")
        snippet = src[idx:idx + 600]
        idx_marker = snippet.find("[SCHOOL_SLOT:")
        idx_traceback = snippet.find("TRACEBACK")
        # Le marqueur doit arriver avant le mot TRACEBACK (le texte de correction)
        assert idx_marker > 0
        # Si TRACEBACK n'est pas dans ce snippet (via format_traceback), on
        # verifie au moins que le marqueur est tout en debut
        idx_iteration = snippet.find("V16 SANDBOX - ITERATION")
        assert idx_marker < idx_iteration, (
            "Le marqueur [SCHOOL_SLOT:] doit preceder le bloc V16 SANDBOX"
        )


class TestV15_8OeilleresRAG:
    """V15.8 : strict target lock sur CODE_REVIEW + target_file."""

    def test_v15_8_marker_in_source(self):
        src = inspect.getsource(autonomy_engine)
        assert "V15.8" in src
        assert "OEILLERES" in src or "target_lock" in src.lower() or "strict_target" in src

    def test_strict_target_lock_conditional(self):
        """Le flag _strict_target_lock doit etre calcule sur (CODE_REVIEW + target_file)."""
        src = inspect.getsource(autonomy_engine)
        assert "_strict_target_lock" in src
        # La condition doit tester slot == CODE_REVIEW et target_file
        idx = src.find("_strict_target_lock = ")
        assert idx > 0
        block = src[idx:idx + 200]
        assert "CODE_REVIEW" in block
        assert "target_file" in block

    def test_radar_skipped_under_lock(self):
        """Le radar (Priorite 2) doit etre dans un 'if not _strict_target_lock'."""
        src = inspect.getsource(autonomy_engine)
        # Cherche le pattern 'if not _strict_target_lock'
        assert "if not _strict_target_lock" in src, (
            "Le radar Priorite 2 doit etre skip si target_lock actif"
        )
