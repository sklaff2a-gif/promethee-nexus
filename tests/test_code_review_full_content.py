# -*- coding: utf-8 -*-
"""Tests V17.1 — injection du code INTEGRAL pour les fichiers courts (2026-06-03).

Une fois la derive tuee par la V17.0, qwen2.5-coder REFUSAIT d'auditer le
squelette de signatures ("code partiel/incomplet"). V17.1 : pour les fichiers
courts (<= 300 lignes ET <= 10000 chars), on injecte le code complet (corps
inclus, numerotation consecutive). Au-dela : squelette (garde-fou contexte).
"""
import pytest

from core import school_schedule
from core.school_schedule import (
    schedule, CODE_REVIEW_FULL_MAX_LINES, CODE_REVIEW_FULL_MAX_CHARS,
)


def _write(tmp_path, monkeypatch, name, content):
    (tmp_path / name).write_text(content, encoding="utf-8")
    monkeypatch.setattr(school_schedule, "_PROJECT_ROOT", str(tmp_path))
    return name


class TestSeuils:
    def test_constantes(self):
        assert CODE_REVIEW_FULL_MAX_LINES == 300
        assert CODE_REVIEW_FULL_MAX_CHARS == 10000


class TestFichierCourtIntegral:
    def test_corps_de_fonction_present(self, tmp_path, monkeypatch):
        src = (
            "import os\n"
            "\n"
            "def addition(a, b):\n"
            "    resultat = a + b  # corps reel\n"
            "    return resultat\n"
        )
        name = _write(tmp_path, monkeypatch, "petit.py", src)
        out = schedule._read_file_for_review(name)
        # numerotation consecutive
        assert "L1: import os" in out
        assert "L3: def addition(a, b):" in out
        # LE CORPS est present (pas juste la signature) — c'est tout l'enjeu
        assert "resultat = a + b" in out
        assert "return resultat" in out

    def test_numerotation_consecutive_sans_trous(self, tmp_path, monkeypatch):
        src = "a = 1\nb = 2\nc = 3\n"
        name = _write(tmp_path, monkeypatch, "consec.py", src)
        out = schedule._read_file_for_review(name)
        assert "L1: a = 1" in out
        assert "L2: b = 2" in out
        assert "L3: c = 3" in out


class TestGrosFichierSquelette:
    def test_au_dela_300_lignes_squelette(self, tmp_path, monkeypatch):
        body = "import os\n"
        for i in range(400):
            body += f"def fonction_{i}():\n    valeur_interne = {i}\n    return valeur_interne\n"
        name = _write(tmp_path, monkeypatch, "gros.py", body)
        out = schedule._read_file_for_review(name)
        # squelette : signatures presentes, corps absents, sortie bornee
        assert "def fonction_0" in out
        assert "valeur_interne = 0" not in out          # le corps n'est PAS injecte
        assert out.count("\n") < 400                      # tronque (pas integral)

    def test_lignes_longues_basculent_en_squelette(self, tmp_path, monkeypatch):
        # peu de lignes (<300) mais > 10000 chars -> le double-cap bascule en squelette
        data = "x = '" + ("A" * 11000) + "'\n"
        src = "import os\ndef f():\n    return 1\n" + data
        name = _write(tmp_path, monkeypatch, "longlines.py", src)
        out = schedule._read_file_for_review(name)
        assert ("A" * 11000) not in out                   # la data geante n'est PAS injectee
        assert "import os" in out                          # mais la signature reste


class TestCasReelGrimoire:
    def test_grimoire_writer_passe_en_integral(self):
        # Le cas qui faisait refuser le modele : grimoire_writer.py (98 lignes)
        out = schedule._read_file_for_review("core/grimoire_writer.py")
        if out.startswith("# FICHIER INTROUVABLE"):
            pytest.skip("grimoire_writer.py absent de cet arbre")
        assert "L1:" in out
        # de la vraie matiere (corps), pas un squelette maigre
        assert len(out) > 1500
