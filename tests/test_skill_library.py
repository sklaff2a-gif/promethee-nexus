# -*- coding: utf-8 -*-
"""Tests — bibliotheque de competences (atelier boucle d'apprentissage, 13/06).

Couvre le coeur metier (skill_library) ET le branchement console (chat_engine) :
  - creation / amelioration conditionnelle / conservation du meilleur / revision
  - rechargement fidele (load) et reconnaissance d'un cas cousin (find)
  - parsing console : charge brute |-separee + score, collapse multi-ligne, whitelist
  - la BOUCLE complete : creer -> charger -> tenter mieux -> garder le meilleur
"""
import pytest
from unittest.mock import patch

import core.skill_library as sl
from core.chat_engine import ChatEngine


@pytest.fixture(autouse=True)
def isole_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "SKILLS_DIR", str(tmp_path / "skills"))
    yield


# ─── Coeur metier : skill_library ────────────────────────────────────────────

class TestSaveLoad:
    def test_creation_v1(self):
        res = sl.save_skill("Sonde Attracteurs", declencheur="routine iterative sur entiers",
                            contexte="analyse", procedure="balayer, iterer, classer",
                            metrique="fraction classee", score=0.7)
        assert res["status"] == "created"
        assert res["version"] == 1
        assert res["best_score"] == 0.7
        fiche = sl.load_skill("Sonde Attracteurs")
        assert fiche["procedure"] == "balayer, iterer, classer"
        assert fiche["contexte"] == "analyse"  # champ ajoute par Promethee

    def test_load_absent_renvoie_none(self):
        assert sl.load_skill("inexistante") is None

    def test_load_par_slug_et_par_nom(self):
        sl.save_skill("Ma Compétence", procedure="p")
        assert sl.load_skill("ma-competence") is not None   # slug
        assert sl.load_skill("Ma Compétence") is not None   # nom exact
        assert sl.load_skill("MA COMPÉTENCE") is not None    # casse


class TestMiseAJourConditionnelle:
    """La REGLE D'OR : on ne remplace la procedure que si le score s'ameliore."""

    def test_score_meilleur_remplace_et_incremente(self):
        sl.save_skill("S", procedure="v1 brouillon", metrique="m", score=0.6)
        res = sl.save_skill("S", procedure="v2 robuste", score=0.9)
        assert res["status"] == "improved"
        assert res["version"] == 2
        assert res["best_score"] == 0.9
        assert sl.load_skill("S")["procedure"] == "v2 robuste"

    def test_score_inferieur_conserve_ancienne(self):
        sl.save_skill("S", procedure="la meilleure", score=0.9)
        res = sl.save_skill("S", procedure="une moins bonne", score=0.5)
        assert res["status"] == "kept"
        assert res["best_score"] == 0.9
        assert res["prev_score"] == 0.5
        # l'ancienne procedure, la meilleure, est INTACTE
        assert sl.load_skill("S")["procedure"] == "la meilleure"
        assert sl.load_skill("S")["version"] == 1  # pas d'increment sur un echec

    def test_score_egal_ne_remplace_pas(self):
        sl.save_skill("S", procedure="originale", score=0.8)
        res = sl.save_skill("S", procedure="copie", score=0.8)
        assert res["status"] == "kept"
        assert sl.load_skill("S")["procedure"] == "originale"

    def test_revision_sans_score(self):
        sl.save_skill("S", procedure="p1", score=0.7)
        res = sl.save_skill("S", procedure="p2 clarifiee")  # pas de score
        assert res["status"] == "revised"
        assert res["version"] == 2
        assert res["best_score"] == 0.7  # inchange
        assert sl.load_skill("S")["procedure"] == "p2 clarifiee"

    def test_premier_score_sur_fiche_sans_best(self):
        sl.save_skill("S", procedure="p")  # cree sans score -> best=None
        res = sl.save_skill("S", procedure="p", score=0.5)
        assert res["status"] in ("improved", "revised")
        assert sl.load_skill("S")["best_score"] == 0.5

    def test_champ_vide_garde_ancien(self):
        sl.save_skill("S", declencheur="decl initial", procedure="proc", score=0.5)
        sl.save_skill("S", declencheur="", procedure="proc v2", score=0.8)
        assert sl.load_skill("S")["declencheur"] == "decl initial"  # pas ecrase par vide

    def test_historique_trace_les_tentatives(self):
        sl.save_skill("S", procedure="p", score=0.6)
        sl.save_skill("S", procedure="q", score=0.4)  # kept
        sl.save_skill("S", procedure="r", score=0.9)  # improved
        hist = sl.load_skill("S")["history"]
        assert len(hist) == 3
        assert any("non retenue" in h["note"] for h in hist)
        assert any("amelioration" in h["note"] for h in hist)


class TestFind:
    def test_find_reconnait_cas_cousin(self):
        sl.save_skill("Sonde", declencheur="routine iterative point fixe cycle entiers",
                      contexte="analyse", procedure="p", score=0.8)
        hit = sl.find_skill("une routine iterative sur les chiffres avec des cycles")
        assert hit is not None
        fiche, score, mots = hit
        assert fiche["nom"] == "Sonde"
        assert "iterative" in mots and score > 0

    def test_find_aucun_recouvrement(self):
        sl.save_skill("Cuisine", declencheur="recette gateau four", procedure="p")
        assert sl.find_skill("calcul matriciel softmax") is None

    def test_find_choisit_meilleur_recouvrement(self):
        sl.save_skill("A", declencheur="point fixe entier", procedure="p")
        sl.save_skill("B", declencheur="point fixe entier cycle attracteur bassin chiffres", procedure="p")
        fiche, score, mots = sl.find_skill("point fixe cycle attracteur chiffres")
        assert fiche["nom"] == "B"


def test_slugify():
    assert sl.slugify("Sonde d'Attracteurs Numériques") == "sonde-d-attracteurs-numeriques"
    assert sl.slugify("") == "competence"


def test_persistance_disque(tmp_path):
    sl.save_skill("Durable", procedure="survit", score=0.5)
    # relecture a froid (nouvelle lecture fichier, pas de cache memoire)
    assert sl.load_skill("Durable")["procedure"] == "survit"
    assert len(sl.list_skills()) == 1


# ─── Branchement console : chat_engine ───────────────────────────────────────

@pytest.fixture
def engine():
    ChatEngine.reset_singleton()
    with patch.object(ChatEngine, "_load"):
        e = ChatEngine()
    yield e
    ChatEngine.reset_singleton()


class TestParsingConsole:
    def test_skill_save_charge_brute_non_shlexee(self, engine):
        # le | et les apostrophes ne doivent PAS etre massacres par shlex
        cmd, args = engine._parse_command("!skill_save Nom | decl | ctx | l'étape A")
        assert cmd == "skill_save"
        assert args[0] == "Nom | decl | ctx | l'étape A"

    def test_skill_list_sans_argument(self, engine):
        assert engine._parse_command("!skill_list") == ("skill_list", [])
        assert engine._parse_command("!skills") == ("skill_list", [])

    def test_skill_load_et_find_charge_brute(self, engine):
        assert engine._parse_command("!skill_load Sonde")[0] == "skill_load"
        assert engine._parse_command("!skill_find un cas neuf")[0] == "skill_find"

    def test_skill_save_dans_whitelist_auto_action(self):
        wl = ChatEngine._AUTO_ACTION_WHITELIST
        for c in ("skill_save", "skill_load", "skill_list", "skills", "skill_find"):
            assert c in wl

    def test_collapse_skill_save_multiligne(self, engine):
        # une procedure ecrite sur plusieurs lignes doit etre repliee (\n litteraux)
        brut = "!skill_save Nom | decl\nsuite procedure | metrique"
        out = engine._collapse_multiline_calc(brut)
        assert "\\n" in out  # newline reel transforme en \n litteral
        assert out.count("\n") == 0 or out.startswith("!skill_save")


class TestExecutionConsole:
    @pytest.mark.asyncio
    async def test_save_via_commande_parse_les_champs(self, engine):
        res = await engine._execute_command(
            "skill_save", ["Sonde | routine iterative | analyse | balayer/iterer/classer | couverture | reviser si cycle long | 0.7"])
        assert "🌱" in res or "creee" in res
        fiche = sl.load_skill("Sonde")
        assert fiche["procedure"] == "balayer/iterer/classer"
        assert fiche["best_score"] == 0.7
        assert fiche["protocole_ajustement"] == "reviser si cycle long"

    @pytest.mark.asyncio
    async def test_load_via_commande(self, engine):
        sl.save_skill("Sonde", declencheur="d", procedure="p", score=0.5)
        res = await engine._execute_command("skill_load", ["Sonde"])
        assert "Sonde" in res and "PROCEDURE" in res

    @pytest.mark.asyncio
    async def test_load_absent_ne_confabule_pas(self, engine):
        res = await engine._execute_command("skill_load", ["fantome"])
        assert "invente" in res.lower() or "aucune" in res.lower()

    @pytest.mark.asyncio
    async def test_save_restaure_newlines_litteraux(self, engine):
        # apres collapse, la charge contient des \n litteraux : la procedure
        # stockee doit retrouver de vrais sauts de ligne (lisible)
        res = await engine._execute_command("skill_save", ["Nom | d | c | etape1\\netape2 | m | a | 0.5"])
        assert sl.load_skill("Nom")["procedure"] == "etape1\netape2"

    @pytest.mark.asyncio
    async def test_boucle_complete_creer_charger_garder_le_meilleur(self, engine):
        # 1. creer (tour 1)
        await engine._execute_command("skill_save", ["Sonde | routine | analyse | v1 | couverture | x | 0.7"])
        # 2. recharger (tour 2 : on ne repart pas de zero)
        charge = await engine._execute_command("skill_find", ["une routine iterative"])
        assert "Sonde" in charge
        # 3. tenter une version moins bonne -> le meilleur est garde
        kept = await engine._execute_command("skill_save", ["Sonde | routine | analyse | v2_pire | couverture | x | 0.5"])
        assert "🛡️" in kept or "CONSERVEE" in kept
        assert sl.load_skill("Sonde")["procedure"] == "v1"
        # 4. tenter une meilleure -> mise a jour
        improved = await engine._execute_command("skill_save", ["Sonde | routine | analyse | v3_mieux | couverture | x | 0.95"])
        assert "📈" in improved or "REMPLACEE" in improved
        assert sl.load_skill("Sonde")["procedure"] == "v3_mieux"
        assert sl.load_skill("Sonde")["best_score"] == 0.95
