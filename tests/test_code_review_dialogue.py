# -*- coding: utf-8 -*-
"""Tests V17.0 — Alterite Technique pour CODE_REVIEW (2026-06-03).

Verifie les 3 choix d'ingenierie :
  1. get_slot_prompt(CODE_REVIEW) porte la structure dialoguee NeuralCompiler,
     avec le target_file ET le contenu reel du fichier (anti-hallucination
     preserve), et amorce "Promethee :".
  2. Le coupe-circuit truncate_code_review_dialogue tronque sur la fausse
     replique NeuralCompiler, mais JAMAIS sur ### / ```python (legitimes dans
     un rapport de code).
  3. D5 (subject drift) reste ACTIF sur CODE_REVIEW (contrairement a BULLETIN) :
     c'est notre allie pour detecter l'audit du mauvais fichier.
"""
import pytest

from core.school_schedule import (
    schedule, SLOT_CODE_REVIEW,
    truncate_code_review_dialogue, _CODE_REVIEW_STOP_MARKERS,
)


class TestCoupeCircuitCodeReview:
    def test_coupe_sur_neuralcompiler_avec_espace(self):
        txt = "Je vois un bug ligne 42.\nNeuralCompiler : Merci, continue."
        clean, was = truncate_code_review_dialogue(txt)
        assert was is True
        assert clean == "Je vois un bug ligne 42."
        assert "NeuralCompiler" not in clean

    def test_coupe_sur_neuralcompiler_sans_espace(self):
        txt = "Audit termine.\nNeuralCompiler: autre chose hallucinee"
        clean, was = truncate_code_review_dialogue(txt)
        assert was is True
        assert clean == "Audit termine."

    def test_ne_coupe_PAS_sur_titres_markdown_ni_code(self):
        # Un rapport de revue de code legitime contient des ### et du ```python.
        # C'est precisement ce que la V16.5 (\n###) aurait detruit a tort.
        txt = (
            "Je resume le role du fichier.\n"
            "### Bugs detectes\n"
            "- ligne 10 : division par zero possible\n"
            "```python\ndef foo():\n    return 1 / 0\n```\n"
            "### Points forts\n"
            "Le code est clair."
        )
        clean, was = truncate_code_review_dialogue(txt)
        assert was is False
        assert clean == txt  # strictement intact

    def test_texte_propre_intact(self):
        txt = "Je n'ai trouve aucun probleme majeur dans ce fichier."
        clean, was = truncate_code_review_dialogue(txt)
        assert was is False
        assert clean == txt

    def test_vide(self):
        assert truncate_code_review_dialogue("") == ("", False)

    def test_markers_constant(self):
        assert "\nNeuralCompiler :" in _CODE_REVIEW_STOP_MARKERS
        assert "\nNeuralCompiler:" in _CODE_REVIEW_STOP_MARKERS


class TestPromptAlteriteTechnique:
    def _mock(self, monkeypatch, target="core/flaw_journal.py",
              content="def journaliser_faille(code):\n    return code"):
        monkeypatch.setattr(schedule, "get_subject_for_slot",
                            lambda slot: {"topic": "", "target_file": target})
        monkeypatch.setattr(schedule, "_read_file_for_review",
                            lambda t: content)
        monkeypatch.setattr(schedule, "get_last_challenge", lambda slot: "")
        monkeypatch.setattr(schedule, "get_difficulty", lambda slot: 1.0)
        monkeypatch.setattr(schedule, "get_weekly_theme", lambda: {})

    def test_prompt_porte_neuralcompiler(self, monkeypatch):
        self._mock(monkeypatch)
        p = schedule.get_slot_prompt(SLOT_CODE_REVIEW)
        assert "NeuralCompiler" in p
        assert "audit EXCLUSIF" in p

    def test_prompt_contient_target_et_contenu_reel(self, monkeypatch):
        # anti-hallucination : le contenu reel du fichier doit etre injecte
        self._mock(monkeypatch)
        p = schedule.get_slot_prompt(SLOT_CODE_REVIEW)
        assert "core/flaw_journal.py" in p
        assert "def journaliser_faille" in p

    def test_prompt_amorce_promethee(self, monkeypatch):
        self._mock(monkeypatch)
        p = schedule.get_slot_prompt(SLOT_CODE_REVIEW)
        assert p.rstrip().endswith("Promethee :")

    def test_prompt_interdit_changer_de_fichier(self, monkeypatch):
        self._mock(monkeypatch, target="core/flaw_journal.py")
        p = schedule.get_slot_prompt(SLOT_CODE_REVIEW)
        # la consigne d'exclusivite cible bien le fichier demande
        assert "pas tes scripts familiers" in p


class TestD5ResteActifSurCodeReview:
    def test_code_review_PAS_exempte_de_d5(self):
        from core.bullshit_detector import D5_SKIP_SLOTS
        # CODE_REVIEW doit GARDER D5 : le subject-drift detecte la derive.
        assert "CODE_REVIEW" not in D5_SKIP_SLOTS
        # sanity : BULLETIN/FREE_TIME restent bien exemptes (V16.6 intacte)
        assert "BULLETIN" in D5_SKIP_SLOTS
        assert "FREE_TIME" in D5_SKIP_SLOTS
