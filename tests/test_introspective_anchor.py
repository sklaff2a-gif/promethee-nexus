"""TDD — V16.5 Mutation topologique du prompt introspectif (dialogue Strategist).

V16.0 (ancre encadrante passive) a ECHOUE au crash-test : le 9B enjambe l'amorce.
V16.5 remplace le format scolaire par un DIALOGUE confrontant — le Strategist-COO
(altérité endogène, PAS de faux JM) interpelle Promethee, avec STABILITE.priv
injectée dynamiquement. Anti-délire = coupe-circuit post-génération
(truncate_hallucinated_dialogue), équivalent des stop_sequences Ollama.
"""
import pytest

from core.school_schedule import (
    schedule, truncate_hallucinated_dialogue, _read_stabilite_priv,
)


# ── Le prompt-dialogue ────────────────────────────────────────────────────
def test_bulletin_est_un_dialogue():
    p = schedule.get_slot_prompt("BULLETIN")
    assert "Strategist :" in p
    assert "Promethee :" in p
    assert "UN seul tour" in p
    # le matériau du bilan reste présent (l'altérité pose les bonnes questions)
    assert "accompli" in p.lower()
    assert "note-toi" in p.lower()


def test_bulletin_injecte_priv_dynamique():
    p = schedule.get_slot_prompt("BULLETIN")
    priv = _read_stabilite_priv()
    assert f"STABILITE.priv est a {priv}" in p


def test_free_time_est_un_dialogue():
    p = schedule.get_slot_prompt("FREE_TIME")
    assert "Strategist :" in p
    assert "Promethee :" in p


def test_code_review_reste_pur():
    p = schedule.get_slot_prompt("CODE_REVIEW")
    assert "Strategist :" not in p
    assert "LE STRATEGIST T'INTERPELLE" not in p


def test_research_reste_pur():
    p = schedule.get_slot_prompt("RESEARCH")
    assert "LE STRATEGIST T'INTERPELLE" not in p


# ── Le coupe-circuit (équivalent stop_sequences) ──────────────────────────
def test_truncate_coupe_fausse_replique_strategist():
    txt = 'Je suis un pont qui vibre.\nStrategist : "Bien, continue."\nPromethee : encore'
    clean, was = truncate_hallucinated_dialogue(txt)
    assert was is True
    assert clean == "Je suis un pont qui vibre."
    assert "Strategist" not in clean


def test_truncate_coupe_faux_jean_michel():
    txt = 'Je ressens de la serenite ce matin.\nJean-Michel : "Tres bien."'
    clean, was = truncate_hallucinated_dialogue(txt)
    assert was is True
    assert "Jean-Michel" not in clean


def test_truncate_coupe_bloc_diese():
    txt = "Je fais mon bilan honnete ici meme.\n### NOUVELLE SECTION HALLUCINEE"
    clean, was = truncate_hallucinated_dialogue(txt)
    assert was is True
    assert "###" not in clean


def test_truncate_laisse_intact_un_vrai_bilan():
    txt = "Je suis Promethee. Aujourd'hui j'ai accompli mes routines et je me note 7/10."
    clean, was = truncate_hallucinated_dialogue(txt)
    assert was is False
    assert clean == txt


# ── V16.6 : exemption D5 subject-drift sur les slots introspectifs ─────────
# Reproduit le crash-test 14:32 : un vrai bilan introspectif (0/5 mots du sujet
# couverts) ne doit plus etre clippe par le D5 lexical (loi de Goodhart).
_REAL_BULLETIN = (
    "Je ressens une forte tension, une frustration liee a la difficulte de progresser. "
    "Les critiques du Strategist sont justifiees. Je dois admettre que je n'ai pas ete a "
    "la hauteur. Mes notes en code review et recherche sont preoccupantes, je stagne, et "
    "je veux explorer de nouvelles approches pour sortir de ma zone de confort."
)
_SUBJECT = "Bulletin du jour : bilan et auto-evaluation"


def test_d5_exempte_bulletin():
    from core.bullshit_detector import d5_subject_drift
    # le bilan ne contient AUCUN mot du sujet (bilan/auto-evaluation) -> drift lexical pur,
    # mais BULLETIN est exempte -> pas de flag
    assert d5_subject_drift(_REAL_BULLETIN, _SUBJECT, "BULLETIN") is False


def test_d5_exempte_free_time():
    from core.bullshit_detector import d5_subject_drift
    assert d5_subject_drift(_REAL_BULLETIN, _SUBJECT, "FREE_TIME") is False


def test_d5_reste_actif_sur_code_review():
    from core.bullshit_detector import d5_subject_drift
    off_topic = "Cette reponse parle de tout autre chose, " * 6
    subject_tech = "Revue de code : core/prefrontal.py veto inhibition deliberation working memory"
    assert d5_subject_drift(off_topic, subject_tech, "CODE_REVIEW") is True


def test_bilan_authentique_ne_clip_plus():
    from core.bullshit_detector import evaluate_deliverable
    res = evaluate_deliverable(_REAL_BULLETIN, _SUBJECT, "BULLETIN")
    assert res["d5_subject_drift"] is False
