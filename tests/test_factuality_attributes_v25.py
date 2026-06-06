# -*- coding: utf-8 -*-
"""Tests V25.0 — factualite : les ATTRIBUTS et IMPORTS sont des symboles reels.

Diag 06/06 : un audit CODE_REVIEW factuel (note 9.9) sur performance_utils.py
etait veto (factualite 0.55 < 0.6) parce que verify_against_file ne reconnaissait
NI les attributs (self.executor, loop.run_in_executor), NI les imports
(ThreadPoolExecutor), NI la variable de classe _instance -> comptes "absents"
-> faux negatifs structurels. V25.0 les reconnait. Garde-fou : une vraie
hallucination (symbole absent) reste detectee.
"""
import os
import pytest
import core.factuality_verifier as fv
from core.factuality_verifier import verify_against_file


def _perf_utils_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(fv.__file__)))
    return os.path.join(root, "core", "performance_utils.py")


def test_attributs_et_imports_reconnus_comme_reels():
    path = _perf_utils_path()
    if not os.path.exists(path):
        pytest.skip("performance_utils.py introuvable")
    # un audit qui cite des attributs + imports + methodes utilisees REELS
    refs = {
        "line_numbers": [],
        "function_names": [
            "AsyncTaskManager",   # ClassDef
            "shutdown",           # FunctionDef
            "executor",           # attribut self.executor
            "running",            # attribut self.running
            "run_in_executor",    # methode utilisee loop.run_in_executor
            "_instance",          # variable de classe (via cls._instance)
            "ThreadPoolExecutor", # import
        ],
    }
    true_refs, total_refs, details = verify_against_file(refs, path)
    assert total_refs == 7
    assert true_refs == 7, f"faux negatif : {true_refs}/7 (details={details})"


def test_hallucination_reste_detectee():
    # le fix ne doit PAS rendre le compteur permissif : un symbole ABSENT du
    # fichier reste compte absent.
    path = _perf_utils_path()
    if not os.path.exists(path):
        pytest.skip("performance_utils.py introuvable")
    refs = {
        "line_numbers": [],
        "function_names": ["AsyncTaskManager", "submit_batch", "process_queue"],
    }
    true_refs, total_refs, details = verify_against_file(refs, path)
    assert total_refs == 3
    assert true_refs == 1, f"{true_refs}/3 — hallucination non detectee ? (details={details})"
