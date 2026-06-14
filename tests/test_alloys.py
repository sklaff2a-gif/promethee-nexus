# -*- coding: utf-8 -*-
"""Tests — skill_library v2.0 : les ALLIAGES (atelier audace, 14/06).

Une competence peut FONDRE avec d'autres en un alliage eprouve. La biblio devient
un « diagramme de phases ». Regle d'honnetete (posee par Promethee) : on ne predit
JAMAIS un alliage jamais forge.
"""
import pytest
from unittest.mock import patch

import core.skill_library as sl
from core.chat_engine import ChatEngine


@pytest.fixture(autouse=True)
def isole(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr(sl, "ALLOYS_DIR", str(tmp_path / "alloys"))
    # deux competences de base pour fondre
    sl.save_skill("RIGUEUR_FACTUELLE", procedure="verifier avant d'affirmer", score=0.67)
    sl.save_skill("OPTIMISATION_MONTEE_LOCALE", procedure="chercher le sommet", score=1.0)
    yield


# ─── Coeur metier : alliages ─────────────────────────────────────────────────

class TestForge:
    def test_forge_cree_alliage_stable(self):
        res = sl.forge_alloy(["RIGUEUR_FACTUELLE", "OPTIMISATION_MONTEE_LOCALE"],
                             procedure="boucle de recuit", contexte="haute temperature",
                             stability=0.85)
        assert res["status"] == "forged"
        assert res["verdict"] == "stable"
        assert res["missing_components"] == []
        a = sl.load_alloy("RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE")
        assert a["procedure"] == "boucle de recuit"
        assert a["verdict"] == "stable"

    def test_verdict_cassant_sous_seuil(self):
        res = sl.forge_alloy(["RIGUEUR_FACTUELLE", "OPTIMISATION_MONTEE_LOCALE"], stability=0.2)
        assert res["verdict"] == "cassant"

    def test_verdict_fragile_zone_grise(self):
        res = sl.forge_alloy(["RIGUEUR_FACTUELLE", "OPTIMISATION_MONTEE_LOCALE"], stability=0.5)
        assert res["verdict"] == "fragile"

    def test_ordre_des_composants_indifferent(self):
        sl.forge_alloy(["OPTIMISATION_MONTEE_LOCALE", "RIGUEUR_FACTUELLE"], stability=0.8)
        # meme alliage quel que soit l'ordre
        a = sl.load_alloy("RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE")
        assert a is not None and a["verdict"] == "stable"

    def test_moins_de_deux_composants_refuse(self):
        res = sl.forge_alloy(["RIGUEUR_FACTUELLE"], stability=0.9)
        assert res["status"] == "error"

    def test_composant_inexistant_signale_mais_forge(self):
        res = sl.forge_alloy(["RIGUEUR_FACTUELLE", "EMPATHIE_INEXISTANTE"], stability=0.3)
        assert res["status"] == "forged"
        assert "EMPATHIE_INEXISTANTE" in res["missing_components"]

    def test_mise_a_jour_conditionnelle_stabilite(self):
        sl.forge_alloy(["RIGUEUR_FACTUELLE", "OPTIMISATION_MONTEE_LOCALE"], procedure="v1", stability=0.6)
        # refonte moins stable -> conservee
        kept = sl.forge_alloy(["RIGUEUR_FACTUELLE", "OPTIMISATION_MONTEE_LOCALE"], procedure="v2_pire", stability=0.4)
        assert kept["status"] == "kept"
        assert sl.load_alloy("RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE")["procedure"] == "v1"
        # refonte plus stable -> remplace
        better = sl.forge_alloy(["RIGUEUR_FACTUELLE", "OPTIMISATION_MONTEE_LOCALE"], procedure="v3_mieux", stability=0.9)
        assert better["status"] == "reinforced"
        assert sl.load_alloy("RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE")["procedure"] == "v3_mieux"


class TestAuditFusion:
    def test_alliage_inconnu_non_predit(self):
        # honnetete : jamais forge -> pas predit stable
        res = sl.audit_fusion(["RIGUEUR_FACTUELLE", "OPTIMISATION_MONTEE_LOCALE"])
        assert res["known"] is False
        assert res["verdict"] == "inconnu"
        assert "devine" in res["message"].lower() or "eprouve" in res["message"].lower()

    def test_alliage_connu_rendu_avec_verdict(self):
        sl.forge_alloy(["RIGUEUR_FACTUELLE", "OPTIMISATION_MONTEE_LOCALE"], stability=0.85)
        res = sl.audit_fusion(["OPTIMISATION_MONTEE_LOCALE", "RIGUEUR_FACTUELLE"])  # ordre inverse
        assert res["known"] is True
        assert res["verdict"] == "stable"


class TestPhaseDiagram:
    def test_diagramme_vide(self):
        assert "VIDE" in sl.format_phase_diagram()

    def test_diagramme_groupe_par_verdict(self):
        sl.forge_alloy(["RIGUEUR_FACTUELLE", "OPTIMISATION_MONTEE_LOCALE"], stability=0.9)
        sl.save_skill("EMPATHIE", procedure="resonance", score=0.5)
        sl.forge_alloy(["RIGUEUR_FACTUELLE", "EMPATHIE"], stability=0.2)
        out = sl.format_phase_diagram()
        assert "STABLES" in out and "CASSANTS" in out
        assert "2 alliage" in out


class TestParseAlloyPayload:
    def test_format_etiquete(self):
        txt = ("COMPOSANTS: RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE\n"
               "NOM: Navigation Haute Fidelite\n"
               "PROCEDURE: O propose, R valide, refroidir si faux\n"
               "CONTEXTE: haute temperature\n"
               "STABILITE: 0.85")
        f = sl.parse_alloy_payload(txt)
        assert "RIGUEUR" in f["components_raw"]
        assert f["nom"] == "Navigation Haute Fidelite"
        assert "refroidir" in f["procedure"]
        assert f["stability"] == 0.85

    def test_parse_components_separateurs(self):
        assert sl._parse_components("A + B") == ["A", "B"]
        assert sl._parse_components("A, B, C") == ["A", "B", "C"]


# ─── Branchement console ─────────────────────────────────────────────────────

@pytest.fixture
def engine():
    ChatEngine.reset_singleton()
    with patch.object(ChatEngine, "_load"):
        e = ChatEngine()
    yield e
    ChatEngine.reset_singleton()


class TestConsole:
    def test_parse_forge_charge_brute(self, engine):
        cmd, args = engine._parse_command("!forge COMPOSANTS: A + B | PROCEDURE: l'alliage")
        assert cmd == "forge"
        assert "A + B" in args[0]

    def test_parse_fusion_pas_audit(self, engine):
        # !fusion (pas !audit qui est deja l'audit systeme)
        assert engine._parse_command("!fusion A + B")[0] == "fusion"
        # !audit reste l'audit systeme (shlex, pas notre commande alliage)
        assert engine._parse_command("!audit")[0] == "audit"

    def test_parse_phases_sans_arg(self, engine):
        assert engine._parse_command("!phases") == ("phases", [])
        assert engine._parse_command("!diagram") == ("phases", [])

    def test_whitelist_contient_forge_fusion_phases(self):
        wl = ChatEngine._AUTO_ACTION_WHITELIST
        for c in ("forge", "fusion", "phases", "diagram", "alloys"):
            assert c in wl

    @pytest.mark.asyncio
    async def test_forge_via_commande_format_etiquete(self, engine):
        payload = ("COMPOSANTS: RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE\\n"
                   "PROCEDURE: boucle de recuit\\nCONTEXTE: haute temp\\nSTABILITE: 0.85")
        res = await engine._execute_command("forge", [payload])
        assert "🔥" in res or "forge" in res.lower()
        a = sl.load_alloy("RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE")
        assert a is not None and a["verdict"] == "stable"

    @pytest.mark.asyncio
    async def test_fusion_inconnu_message_honnete(self, engine):
        res = await engine._execute_command("fusion", ["RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE"])
        assert "jamais" in res.lower() or "inconnu" in res.lower() or "eprouve" in res.lower()

    @pytest.mark.asyncio
    async def test_boucle_complete_forge_puis_audit(self, engine):
        await engine._execute_command("forge",
            ["COMPOSANTS: RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE\\nSTABILITE: 0.85"])
        audit = await engine._execute_command("fusion", ["RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE"])
        assert "stable" in audit.lower()
        phases = await engine._execute_command("phases", [])
        assert "STABLES" in phases
