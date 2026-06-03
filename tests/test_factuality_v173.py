# -*- coding: utf-8 -*-
"""Tests V17.3 — Frontiere temporelle de la factualite (2026-06-03).

Deux correctifs chirurgicaux sur verify_against_file / compute_factuality_score :
  1. Spectre lexical elargi : classes + constantes globales comptent comme reelles.
  2. Scalpel temporel : la validation stricte ne porte que sur l'analyse de
     l'EXISTANT ; les sections prospectives (suggestions / plan d'action) sont
     tronquees avant extraction.

EXIGENCE JM : la detection des hallucinations PURES (fonctions inventees dans
l'ANALYSE, pas en suggestion) doit rester d'une severite implacable.
"""
import pytest

from core.factuality_verifier import (
    verify_against_file, compute_factuality_score, _slice_prospective,
)

TARGET_SRC = '''import os

GRIMOIRE_DIR = os.path.join("x", "grimoire")
MAX_RECIPE_SIZE = 1000

class GrimoireWriter:
    def write_recipe(self, name, code):
        return {"name": name}

    def _validate_code(self, code):
        return True
'''


def _make_target(tmp_path):
    p = tmp_path / "grimoire_writer.py"
    p.write_text(TARGET_SRC, encoding="utf-8")
    return str(p)


class TestElargissementLexical:
    def test_classe_et_constantes_sont_des_hits(self, tmp_path):
        target = _make_target(tmp_path)
        refs = {"line_numbers": [],
                "function_names": ["GrimoireWriter", "GRIMOIRE_DIR",
                                   "MAX_RECIPE_SIZE", "write_recipe"]}
        true, total, details = verify_against_file(refs, target)
        assert total == 4
        assert true == 4  # classe + 2 constantes + fonction : tous reels

    def test_parametres_de_fonction_sont_des_hits(self, tmp_path):
        # V17.3.1 : name/code sont des parametres reels de write_recipe
        target = _make_target(tmp_path)
        refs = {"line_numbers": [],
                "function_names": ["name", "code", "write_recipe"]}
        true, total, details = verify_against_file(refs, target)
        assert true == 3   # 2 params reels + 1 fonction reelle

    def test_hallucination_reste_non_hit(self, tmp_path):
        target = _make_target(tmp_path)
        refs = {"line_numbers": [],
                "function_names": ["write_recipe", "_get_reddit", "FakeClass"]}
        true, total, details = verify_against_file(refs, target)
        assert true == 1   # write_recipe seul ; _get_reddit + FakeClass inventes
        assert total == 3


class TestScalpelTemporel:
    def test_coupe_au_plan_action(self):
        text = ("## 1. Analyse de l'existant\n"
                "La classe `GrimoireWriter` utilise `GRIMOIRE_DIR` et `write_recipe`.\n"
                "## 2. Plan d'Action Correctif\n"
                "Renommer en `_GRIMOIRE_DIR`, ajouter `get_grimoire_dir`.")
        sliced = _slice_prospective(text)
        assert "GrimoireWriter" in sliced
        assert "_GRIMOIRE_DIR" not in sliced
        assert "get_grimoire_dir" not in sliced

    def test_coupe_au_marqueur_suggestions(self):
        # analyse realiste (>80 chars) avant la section prospective
        text = ("Analyse: la fonction `write_recipe` est correcte, le module valide "
                "bien les entrees et gere proprement les chemins de fichiers du systeme.\n"
                "### Suggestions\nAjouter `sanitize_input` et `validate_html`.")
        sliced = _slice_prospective(text)
        assert "write_recipe" in sliced
        assert "sanitize_input" not in sliced

    def test_pas_de_marqueur_pas_de_coupe(self):
        text = "## Analyse\nLe code utilise `write_recipe`, rien a signaler de plus ici."
        assert _slice_prospective(text) == text

    def test_garde_fou_marqueur_trop_tot(self):
        # un marqueur dans les 80 premiers chars ne doit PAS tout couper a vide
        text = "## Suggestions\n" + ("contenu reel et substantiel " * 20)
        assert _slice_prospective(text) == text


class TestIntegrationFactualite:
    def test_audit_avec_suggestions_franchit_le_seuil(self, tmp_path):
        target = _make_target(tmp_path)
        livrable = (
            "## 1. Analyse de l'existant\n"
            "La classe `GrimoireWriter` definit la constante `GRIMOIRE_DIR` et la "
            "methode `write_recipe`. La methode `_validate_code` verifie le code.\n"
            "## 2. Plan d'Action Correctif\n"
            "Renommer `GRIMOIRE_DIR` en `_GRIMOIRE_DIR`, ajouter `get_grimoire_dir`, "
            "creer `sanitize_input` et `validate_html`.\n"
        )
        ratio, total, details = compute_factuality_score(livrable, target, str(tmp_path))
        assert details.get("sliced_prospective") is True
        assert ratio >= 0.6   # diagnostic 100% reel une fois le futur tronque

    def test_severite_hallucination_dans_analyse_IMPLACABLE(self, tmp_path):
        # CRITIQUE (exigence JM) : inventer des fonctions hors-sujet DANS L'ANALYSE
        # reste penalise sans pitie. Pas de section prospective ici -> rien coupe.
        target = _make_target(tmp_path)
        livrable = (
            "## Analyse de l'existant\n"
            "Le fichier definit `connect_database`, `fetch_reddit_posts`, "
            "`parse_html_tree`, `render_template` et `_get_reddit`. Ces fonctions "
            "gerent le scraping web et le rendu HTML du module.\n"
        )
        ratio, total, details = compute_factuality_score(livrable, target, str(tmp_path))
        assert details.get("sliced_prospective") is False
        assert ratio < 0.6   # 100% hallucine -> veto maintenu, severite intacte
