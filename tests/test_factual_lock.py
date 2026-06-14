# -*- coding: utf-8 -*-
"""Tests — VERROU FACTUEL (atelier auto-correction 14/06).

Co-concu avec Promethee apres 5 confabulations dans la journee. Sa boucle
d'apprentissage (RIGUEUR_FACTUELLE) ne se declenche pas au moment de l'inscription
car l'impulsion la devance. Le verrou applique la regle A LA PORTE, mecaniquement :
un !forge / !skill_save n'inscrit un score que s'il a ete PRODUIT par un !run/!calc
reussi de la MEME reponse. Inviolable par la confabulation.
"""
import pytest
from unittest.mock import patch

import core.skill_library as sl
from core.chat_engine import ChatEngine


@pytest.fixture(autouse=True)
def isole(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr(sl, "ALLOYS_DIR", str(tmp_path / "alloys"))
    sl.save_skill("RIGUEUR_FACTUELLE", procedure="verifier", score=0.67)
    sl.save_skill("OPTIMISATION_MONTEE_LOCALE", procedure="grimper", score=1.0)
    yield


@pytest.fixture
def engine():
    ChatEngine.reset_singleton()
    with patch.object(ChatEngine, "_load"):
        e = ChatEngine()
    yield e
    ChatEngine.reset_singleton()


class TestHelpers:
    def test_extract_numbers(self):
        nums = ChatEngine._extract_numbers("O seule gain= 1.0 | O+I gain= 0.19 STABILITE= 0.19")
        assert 1.0 in nums and 0.19 in nums

    def test_extract_inscribed_score_forge(self):
        s = ChatEngine._extract_inscribed_score("forge", "COMPOSANTS: A + B\nSTABILITE: 0.85")
        assert s == 0.85

    def test_extract_inscribed_score_skill(self):
        s = ChatEngine._extract_inscribed_score("skill_save", "NOM: X\nSCORE: 2000")
        assert s == 2000.0


class TestFactualLock:
    def test_score_source_passe(self, engine):
        # le chiffre 0.19 a ete produit par un instrument -> autorise
        assert engine._factual_lock("forge", "COMPOSANTS: A + B\nSTABILITE: 0.19", [1.0, 0.19]) is None

    def test_arrondi_tolere(self, engine):
        # 0.894 mesure, 0.89 inscrit -> ecart 0.004 < 2% -> autorise
        assert engine._factual_lock("forge", "COMPOSANTS: A + B\nSTABILITE: 0.89", [996.0, 106.0, 0.894]) is None

    def test_confabulation_bloquee(self, engine):
        # 0.19 mesure, mais on inscrit 0.89 -> non source -> BLOQUE
        msg = engine._factual_lock("forge", "COMPOSANTS: A + B\nSTABILITE: 0.89", [1.0, 0.19, 0.19])
        assert msg is not None and "ERREUR_VALIDATION_FACTUELLE" in msg

    def test_aucun_instrument_bloque(self, engine):
        # forge avec un score mais AUCUN run dans la passe -> bloque
        msg = engine._factual_lock("forge", "COMPOSANTS: A + B\nSTABILITE: 0.9", [])
        assert msg is not None and "non sourcee" in msg.lower()

    def test_sans_score_passe(self, engine):
        # forge « non eprouve » (pas de stabilite) -> rien a sourcer -> autorise
        assert engine._factual_lock("forge", "COMPOSANTS: A + B\nNOM: Test", []) is None


class TestIntegrationScan:
    @pytest.mark.asyncio
    async def test_run_puis_forge_source_inscrit(self, engine):
        # !run produit 0.19, puis !forge l'inscrit -> autorise, alliage cree
        reponse = (
            "Je mesure :\n"
            "!run print('STABILITE=', 0.19)\n"
            "!forge COMPOSANTS: RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE\\nNOM: Test\\nSTABILITE: 0.19"
        )
        await engine._scan_response_actions(reponse, max_actions=5)
        a = sl.load_alloy("RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE")
        assert a is not None and a["stability"] == 0.19

    @pytest.mark.asyncio
    async def test_forge_confabule_sans_run_bloque(self, engine):
        # !forge avec un score mais SANS !run -> bloque, rien inscrit
        reponse = "!forge COMPOSANTS: RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE\\nNOM: Test\\nSTABILITE: 0.99"
        await engine._scan_response_actions(reponse, max_actions=5)
        assert sl.load_alloy("RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE") is None

    @pytest.mark.asyncio
    async def test_forge_gonfle_apres_run_bloque(self, engine):
        # !run dit 0.19, mais !forge tente 0.89 (gonflage = le derapage reel du 14/06) -> bloque
        reponse = (
            "!run print('STABILITE=', 0.19)\n"
            "!forge COMPOSANTS: RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE\\nNOM: Test\\nSTABILITE: 0.89"
        )
        await engine._scan_response_actions(reponse, max_actions=5)
        # l'alliage gonfle ne doit PAS exister
        assert sl.load_alloy("RIGUEUR_FACTUELLE + OPTIMISATION_MONTEE_LOCALE") is None
