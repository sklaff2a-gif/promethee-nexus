"""TDD — aveuglement chirurgical du radar RAG ecole pour les slots introspectifs.

Bug prouve in-vivo 01/06 (BULLETIN 04:18, famine 359h) : un auto-bilan liste ses
routines du jour (CODE_REVIEW, EXPANSION_CODE...). Le radar Bloom V15.3 prenait
ces noms pour des intents techniques et injectait leur CODE -> Promethee derivait
vers une analyse de main() au lieu de son bilan -> D5 subject drift -> note < 4
-> pas de closure -> famine BULLETIN.

Fix V15.9 : _INTROSPECTIVE_SLOTS (BULLETIN, FREE_TIME) exclus du radar -> aucun
chunk de code injecte. Les slots techniques (RESEARCH, CODE_REVIEW...) inchanges.
"""
import pytest
from unittest.mock import MagicMock

from core.autonomy_engine import AutonomyEngine


@pytest.fixture
def spy_indexer(monkeypatch):
    """Espionne indexer.query (return [] : on mesure l'ACTIVATION, pas le contenu)."""
    spy = MagicMock()
    spy.query.return_value = []
    monkeypatch.setattr("core.capabilities.source_code_indexer.indexer", spy)
    return spy


# Prompt+subject reproduisant un vrai BULLETIN : il CITE ses routines du jour
# (CODE_REVIEW, EXPANSION_CODE, COUNCIL_DEBATE = MAJUSCULE_UNDERSCORE).
_PROMPT_BULLETIN = (
    "BULLETIN DU JOUR — Auto-evaluation\n"
    "Livrables du jour :\n- CODE_REVIEW: note 0.05/10\n- EXPANSION_CODE: note 1/10\n"
    "Redige ton bulletin : ce que tu as accompli aujourd'hui."
)
_INFO_BULLETIN = {
    "target_file": "",
    "subject": "Bulletin du jour : bilan et auto-evaluation. "
               "Meilleure routine : EXPANSION_CODE. Moins performante : COUNCIL_DEBATE.",
}


def test_radar_muet_pour_bulletin(spy_indexer):
    out = AutonomyEngine._build_v15_school_context("BULLETIN", _PROMPT_BULLETIN, _INFO_BULLETIN)
    assert out == "", "un BULLETIN ne doit recevoir AUCUN chunk de code"
    assert spy_indexer.query.call_count == 0, "le radar ne doit jamais s'activer sur un BULLETIN"


def test_radar_muet_pour_free_time(spy_indexer):
    out = AutonomyEngine._build_v15_school_context(
        "FREE_TIME", _PROMPT_BULLETIN, {"target_file": "", "subject": "temps libre, CODE_REVIEW evoque"}
    )
    assert out == ""
    assert spy_indexer.query.call_count == 0


def test_radar_actif_pour_research(spy_indexer):
    """Non-regression : un slot technique garde son radar (intents + fichiers)."""
    prompt = ("Analyse core/prefrontal.py et compare les intents "
              "CODE_REVIEW et MEMORY_CONSOLIDATION.")
    AutonomyEngine._build_v15_school_context(
        "RESEARCH", prompt, {"target_file": "", "subject": "veille technique"}
    )
    assert spy_indexer.query.call_count >= 1, "le radar doit rester actif pour RESEARCH"


def test_radar_actif_pour_code_review_target(spy_indexer):
    """Non-regression : CODE_REVIEW avec target_file injecte toujours le fichier."""
    AutonomyEngine._build_v15_school_context(
        "CODE_REVIEW", "audit du fichier", {"target_file": "core/prefrontal.py", "subject": "review"}
    )
    assert spy_indexer.query.call_count >= 1, "CODE_REVIEW target_file doit injecter le fichier cible"
