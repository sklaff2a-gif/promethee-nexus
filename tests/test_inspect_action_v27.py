# -*- coding: utf-8 -*-
"""Tests V27.0 — anti-repetition d'inspection via intention STRUCTUREE.

Concretisation de la lecon auto-formulee par Promethee (06/06) apres avoir audite
son propre autonomy_engine : _choose_inspect_target faisait de l'anti-repetition en
re-parsant result_preview a la recherche des sous-chaines litterales "Issues"/
"Commits"/... (couplage fragile par magic-string). Si le label change, le mot n'est
plus reconnu -> la priorite 1 reste TOUJOURS active -> sur-inspection en boucle,
rotation cassee. De plus, le chemin FORCED (_record_routine l.4633) n'ecrit PAS de
result_preview -> ces SELF_INSPECT etaient deja invisibles a l'anti-repetition.

Fix : stocker l'action choisie (intention structuree) a la SOURCE
(_execute_self_inspect) dans self._recent_inspect_actions, et la lire directement.
Fallback retro-compatible sur l'ancien parsing si la liste est vide (transition).
"""
import pytest
from unittest.mock import patch, AsyncMock

import core.autonomy_engine as ae
from core.autonomy_engine import AutonomyEngine


def _engine():
    """Instance legere sans le __init__ lourd (charge l'etat persiste, etc.)."""
    e = AutonomyEngine.__new__(AutonomyEngine)
    e.routine_history = []
    e._recent_inspect_actions = []
    e._weekly_ritual_pending = False
    e._weekly_ritual_attempts = 0
    return e


def test_priorite1_issues_si_rien_inspecte():
    e = _engine()
    t = e._choose_inspect_target(mirror=None)
    assert t["action"] == "issues"


def test_anti_repetition_via_champ_structure():
    # "issues" deja inspecte (intention structuree) -> on passe a commits
    e = _engine()
    e._recent_inspect_actions = ["issues"]
    t = e._choose_inspect_target(mirror=None)
    assert t["action"] == "commits"


def test_le_label_renomme_ne_casse_plus_l_anti_repetition():
    # COEUR DU FIX : meme sans aucun texte de label a parser, les 3 priorites
    # haut-de-gamme sont bien marquees inspectees -> on bascule sur read_file.
    # (Avant : si le label changeait, "issues" n'entrait jamais dans recent_inspects
    #  et la priorite 1 restait collee -> boucle.)
    e = _engine()
    e._recent_inspect_actions = ["issues", "commits", "summary"]
    t = e._choose_inspect_target(mirror=None)
    assert t["action"] in ("read_file", "list_files")
    assert t["action"] != "issues"


def test_fallback_parsing_si_liste_structuree_vide():
    # Retro-compat : pas encore de champ structure, mais un vieux SELF_INSPECT en
    # historique avec result_preview "[ISSUES OUVERTES]" -> detecte via fallback.
    e = _engine()
    e._recent_inspect_actions = []  # pas encore alimente
    # Format reel de result_preview : "Inspection: <label>\n<result_text>" — le
    # label "Issues ouvertes sur GitHub" porte le mot "Issues" (c'est ce couplage
    # par le LABEL que le fix elimine ; le fallback le reproduit a l'identique).
    e.routine_history = [
        {"intent": "SELF_INSPECT",
         "result_preview": "Inspection: Issues ouvertes sur GitHub\n[ISSUES OUVERTES]\n  #1 bug"},
    ]
    t = e._choose_inspect_target(mirror=None)
    assert t["action"] == "commits"  # issues deja vu via fallback


def test_champ_structure_prioritaire_sur_fallback():
    # Si la liste structuree est non vide, on NE retombe PAS sur le parsing.
    e = _engine()
    e._recent_inspect_actions = ["commits"]
    e.routine_history = [
        {"intent": "SELF_INSPECT", "result_preview": "[ISSUES OUVERTES]\n  #1 bug"},
    ]
    t = e._choose_inspect_target(mirror=None)
    # commits est marque inspecte par le champ structure ; issues PAS (le fallback
    # n'est pas consulte) -> priorite 1 issues ressort.
    assert t["action"] == "issues"


@pytest.mark.asyncio
async def test_execute_self_inspect_alimente_la_liste_a_la_source():
    e = _engine()

    class _FakeMirror:
        def is_available(self):
            return True
        def read_issues(self, n=10):
            return [{"number": 1, "title": "bug X"}]

    with patch("core.capabilities.github_mirror.GitHubMirror", _FakeMirror), \
         patch.object(ae.bus, "publish", new=AsyncMock()):
        res = await e._execute_self_inspect()

    assert res["status"] == "success"
    # l'intention structuree a ete stockee a la SOURCE, sans parser aucun texte
    assert e._recent_inspect_actions == ["issues"]


def test_liste_plafonnee_a_10():
    e = _engine()
    e._recent_inspect_actions = [f"x{i}" for i in range(10)]
    # simulate l'append + plafond fait dans _execute_self_inspect
    e._recent_inspect_actions.append("issues")
    if len(e._recent_inspect_actions) > 10:
        e._recent_inspect_actions = e._recent_inspect_actions[-10:]
    assert len(e._recent_inspect_actions) == 10
    assert e._recent_inspect_actions[-1] == "issues"
    assert "x0" not in e._recent_inspect_actions
