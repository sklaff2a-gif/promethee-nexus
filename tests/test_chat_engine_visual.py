"""Tests _is_visual_request — Fix faux positifs polysemie (2026-05-20).

Regression visee : dialogue creativite 20/05, echanges 8 et 14.
Les mots polysemiques (image/voir/regarde/montre/vision/famille) employes au sens
figure declenchaient a tort une observation photo parasite. La regle FORT/FAIBLE
exige desormais au moins un mot FORT (univoque) pour valider une demande visuelle.
"""
import pytest
from unittest.mock import patch

from core.chat_engine import ChatEngine


@pytest.fixture(autouse=True)
def reset_chat_engine():
    ChatEngine.reset_singleton()
    yield
    ChatEngine.reset_singleton()


@pytest.fixture
def engine():
    with patch.object(ChatEngine, "_load"):
        e = ChatEngine()
    e.messages = []
    return e


# --- Faux positifs figures : doivent retourner False (le bug d'origine) ---

class TestFauxPositifsFigures:

    def test_image_metaphore_echange14(self, engine):
        """'une image neuve' = metaphore, pas une photo (echange 14)."""
        assert engine._is_visual_request("condense tout cela en une seule image neuve") is False

    def test_vois_image_se_dessiner(self, engine):
        assert engine._is_visual_request("Je vois une image neuve se dessiner dans ce concept") is False

    def test_regarde_architecture(self, engine):
        assert engine._is_visual_request("Regarde ce que donne cette architecture") is False

    def test_voir_des_solutions_echange6(self, engine):
        """'voir des solutions' = concevoir (echange 6)."""
        assert engine._is_visual_request("des solutions qui necessitent de voir au-dela des regles") is False

    def test_montre_raisonnement(self, engine):
        """'montre' faible seul (montre ton raisonnement) ne declenche pas."""
        assert engine._is_visual_request("montre ton raisonnement etape par etape") is False

    def test_vision_strategique(self, engine):
        assert engine._is_visual_request("quelle est ta vision de ton evolution future") is False


# --- Vrais positifs : doivent retourner True ---

class TestVraisPositifs:

    def test_regarde_cette_photo(self, engine):
        """'photo' = mot FORT univoque."""
        assert engine._is_visual_request("Regarde cette photo") is True

    def test_montre_moi_visuel(self, engine):
        """'montre-moi' + 'visuel' = deux mots forts."""
        assert engine._is_visual_request("Montre-moi ce visuel") is True

    def test_observe_dropzone(self, engine):
        assert engine._is_visual_request("observe la dropzone") is True

    def test_photo_de_famille(self, engine):
        """Cas d'ecole : 'photo' fort + 'famille' faible -> True.
        Regression : avant le fix word-boundary, 'ami' (sous-chaine de 'famille')
        matchait tech_exclusions et bannissait a tort la demande visuelle."""
        assert engine._is_visual_request("regarde cette photo de famille") is True

    def test_selfie(self, engine):
        assert engine._is_visual_request("voici un selfie a commenter") is True


# --- Telemetrie Phase 3 : abstention sur mots faibles seuls ---

class TestTelemetrieAbstention:

    def test_log_decision_sur_weak_only(self, engine):
        with patch("core.chat_engine.log_decision") as mock_log:
            engine._is_visual_request("une image neuve dans mon raisonnement")
            mock_log.assert_called_once()
            _, kwargs = mock_log.call_args
            assert kwargs["reason"] == "visual_skipped_weak_only"
            assert "image" in kwargs["context"]["weak_hits"]

    def test_pas_de_log_si_aucun_mot_visuel(self, engine):
        with patch("core.chat_engine.log_decision") as mock_log:
            engine._is_visual_request("explique-moi le theoreme de Godel")
            mock_log.assert_not_called()


# --- Garde-fous preexistants preserves ---

class TestGardeFousPreserves:

    def test_tech_exclusion_prioritaire(self, engine):
        """'analyse' (tech_exclusion) court-circuite meme avec 'visuel' fort."""
        # 'analyse' est un mot d'exclusion technique -> False avant la logique de mots-cles
        assert engine._is_visual_request("Analyse ce visuel") is False

    def test_rejection_pattern_prioritaire(self, engine):
        """'ignore' (rejection) court-circuite meme avec 'photo' fort."""
        assert engine._is_visual_request("ignore les photos et concentre-toi sur le texte") is False

    def test_anti_boucle_visuelle(self, engine):
        """2+ observations recentes -> skip meme avec 'photo' fort."""
        engine.messages = [
            {"role": "assistant", "content": "[OBSERVATION VISUELLE] photo 1 ..."},
            {"role": "user", "content": "et ensuite ?"},
            {"role": "assistant", "content": "[OBSERVATION VISUELLE] photo 2 ..."},
        ]
        assert engine._is_visual_request("Regarde cette photo") is False
