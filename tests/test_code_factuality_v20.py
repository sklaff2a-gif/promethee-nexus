# -*- coding: utf-8 -*-
"""Tests V20.0 — compute_code_factuality (factualite du code ex-nihilo WORKSHOP).

Valide les deux extremes de l'incubateur syntaxique :
- code dense valide -> 1.00 (closure debloquee) ;
- pass / print nu / syntaxe cassee / prose -> atomise sous le seuil de closure ;
et le branchement reel : le livrable WORKSHOP 03/06 02:34 (note 7.9, jamais
closure faute de F5=-1.0) franchit desormais le seuil 0.6.
"""
import pytest

from core.factuality_verifier import (
    compute_code_factuality,
    _extract_python_code,
    MIN_AST_NODES,
)


class TestSyntaxShield:
    def test_code_invalide_atomise_a_zero(self):
        score, d = compute_code_factuality("```python\ndef f(:\n    x =\n```")
        assert score == 0.0
        assert d["reason"] == "syntax_error"

    def test_indentation_cassee_atomise(self):
        # corps de fonction non indente -> IndentationError (sous-classe SyntaxError)
        score, d = compute_code_factuality("```python\ndef f():\nx = 1\n```")
        assert score == 0.0

    def test_prose_pure_atomise(self):
        score, d = compute_code_factuality("Je reflechis a la solitude des machines.")
        assert score == 0.0

    def test_contenu_vide_bypass(self):
        score, d = compute_code_factuality("")
        assert score == -1.0


class TestSubstanceFloor:
    def test_pass_seul_recale(self):
        score, d = compute_code_factuality("```python\ndef f():\n    pass\n```")
        assert score < 0.6
        assert d["ast_nodes"] < MIN_AST_NODES

    def test_print_nu_recale(self):
        score, d = compute_code_factuality("```python\nprint('hello')\n```")
        assert score < 0.6

    def test_code_dense_valide_a_un(self):
        dense = (
            "```python\n"
            "import math\n"
            "def aire_cercle(r):\n"
            "    if r < 0:\n"
            "        raise ValueError('rayon negatif')\n"
            "    return math.pi * r ** 2\n"
            "\n"
            "def main():\n"
            "    for r in range(1, 5):\n"
            "        print(r, aire_cercle(r))\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
            "```"
        )
        score, d = compute_code_factuality(dense)
        assert score == 1.0
        assert d["ast_nodes"] >= 50  # script fonctionnel dense (cf calibration JM)

    def test_seuil_min_nodes_franchi(self):
        # 10 assignations ~= 41 noeuds AST -> au-dessus du plancher
        score, d = compute_code_factuality(
            "```python\na=1\nb=2\nc=3\nd=4\ne=5\nf=6\ng=7\nh=8\ni=9\nj=10\n```"
        )
        assert d["ast_nodes"] >= MIN_AST_NODES
        assert score == 1.0


class TestExtraction:
    def test_extrait_bloc_fence_ignore_prose(self):
        code = _extract_python_code("blabla prose\n```python\nx = 1\n```\nfin")
        assert "x = 1" in code
        assert "blabla" not in code

    def test_fallback_sans_fence(self):
        code = _extract_python_code("def f():\n    return 42")
        assert "def f()" in code


class TestLivrableReel:
    def test_workshop_0234_franchit_le_seuil(self):
        # Livrable reel du 03/06 02:34 : note 7.9, mais factuality=-1.0 le
        # poignardait. Avec V20.0 il doit passer >= 0.6 -> closure possible.
        reel = (
            "Correction effectuee : ajout d'indentation.\n\n"
            "```python\n"
            "def main():\n"
            "    print(\"Execution du programme principal\")\n"
            "\n"
            "if __name__ == \"__main__\":\n"
            "    print(\"Demarrage du programme\")\n"
            "    main()\n"
            "```"
        )
        score, d = compute_code_factuality(reel)
        assert score >= 0.6
        assert d["ast_nodes"] >= MIN_AST_NODES
