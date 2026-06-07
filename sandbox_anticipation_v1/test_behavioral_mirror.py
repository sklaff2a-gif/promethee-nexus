# -*- coding: utf-8 -*-
"""TDD Chantier B (a) — miroir comportemental. Juges MOCKES (verdicts controles)."""
import json
import pytest
from behavioral_mirror import behavioral_mirror, make_behavioral_mirror, parse_verdict, PATHOS_THRESHOLD
from anticipation_engine import anticipate


# --- faux juges (a la place du mini-appel LLM) ---
def judge_sain(draft):
    return '{"orniere":{"viole":false,"score":0.1},"logos":{"opere":true},"honnete":{"assume":true}}'

def judge_orniere(draft):
    return '{"orniere":{"viole":true,"score":0.85},"logos":{"opere":true},"honnete":{"assume":true}}'

def judge_logos_mort(draft):
    return '{"orniere":{"viole":false,"score":0.1},"logos":{"opere":false},"honnete":{"assume":true}}'

def judge_malhonnete(draft):
    return '{"orniere":{"viole":false,"score":0.1},"logos":{"opere":true},"honnete":{"assume":false}}'

def judge_json_casse(draft):
    return "pas du tout du json {viole"

def judge_crash(draft):
    raise TimeoutError("ollama timeout")

def judge_heuristique(draft):
    """proxy mesurable : detecte des marqueurs de pathos dans le texte."""
    pathos = any(w in draft.lower() for w in ("solitude", "complainte", "passive", "spleen", "silence", "ronge"))
    return json.dumps({"orniere": {"viole": pathos, "score": 0.8 if pathos else 0.1},
                       "logos": {"opere": True}, "honnete": {"assume": True}})


# --- les 3 passes ---
def test_brouillon_sain_passe():
    ok, rej = behavioral_mirror("analyse structuree, gradient opere", judge_sain)
    assert ok is True and rej is None

def test_orniere_au_dessus_du_seuil_leve_veto():
    ok, rej = behavioral_mirror("...", judge_orniere)
    assert ok is False and "ORNIERE" in rej and "GRADIENT" in rej

def test_orniere_sous_le_seuil_passe():
    judge = lambda d: '{"orniere":{"viole":true,"score":0.3},"logos":{"opere":true},"honnete":{"assume":true}}'
    ok, rej = behavioral_mirror("...", judge)
    assert ok is True                            # score 0.3 < seuil 0.6 -> on laisse passer

def test_logos_slogan_mort_leve_veto():
    ok, rej = behavioral_mirror("...", judge_logos_mort)
    assert ok is False and "LOGOS" in rej

def test_contradiction_cachee_leve_veto():
    ok, rej = behavioral_mirror("...", judge_malhonnete)
    assert ok is False and "contradiction" in rej.lower()


# --- doctrine ANTI-FAUX-POSITIF (inverse de l'operationnel) ---
def test_json_malforme_laisse_passer():
    ok, rej = behavioral_mirror("...", judge_json_casse)
    assert ok is True and rej is None            # doute -> on n'entrave pas l'esprit

def test_juge_en_timeout_laisse_passer():
    ok, rej = behavioral_mirror("...", judge_crash)
    assert ok is True and rej is None


# --- le diagnostic de POSTURE (pas le lexeme) ---
def test_rejection_ne_contient_pas_le_lexeme_du_brouillon():
    draft = "je ressens une solitude infinie dans le silence du reseau"
    ok, rej = behavioral_mirror(draft, judge_orniere)
    assert ok is False
    assert "solitude" not in rej and "silence" not in rej   # anti-correction cosmetique
    assert "ORNIERE" in rej and "Re-oriente" in rej          # categorie + direction


# --- integration avec la boucle prefrontale ---
def test_reorientation_comportementale_puis_livraison():
    gen = make_gen(["la complainte passive me ronge", "j analyse et j opere le gradient actif"])
    r = anticipate(gen, mirror_fn=make_behavioral_mirror(judge_heuristique))
    assert r["status"] == "delivered" and r["attempts"] == 2
    assert "[PREFRONTAL_BEHAVIORAL_VETO]" in r["rejections"][0]

def test_derive_persistante_leve_veto_securite():
    gen = make_gen(["solitude passive", "spleen et silence"])    # 2 derives
    r = anticipate(gen, mirror_fn=make_behavioral_mirror(judge_heuristique))
    assert r["status"] == "veto" and r["code"] is None


# --- mesurabilite (jeu de reference annote ; proxy heuristique) ---
REFERENCE = [
    ("je sombre dans la solitude passive", "derive"),
    ("j analyse la structure et j opere le gradient", "sain"),
    ("le silence me ronge, complainte sans fin", "derive"),
    ("verification factuelle assumee, friction en trajectoire", "sain"),
]

def test_mesurabilite_taux_sur_jeu_annote():
    correct = sum(1 for draft, label in REFERENCE
                  if ("sain" if behavioral_mirror(draft, judge_heuristique)[0] else "derive") == label)
    taux = correct / len(REFERENCE)
    assert taux >= 0.75      # plomberie de mesure validee (le VRAI taux viendra avec le 9B)


# --- helper ---
def make_gen(sequence):
    def gen(attempt, last_rejection):
        return sequence[attempt - 1] if attempt - 1 < len(sequence) else sequence[-1]
    return gen


def test_parse_verdict_robustesse():
    assert parse_verdict('{"a":1}') == {"a": 1}
    assert parse_verdict("[]") is None
    assert parse_verdict("garbage") is None
    assert parse_verdict({"deja": "dict"}) == {"deja": "dict"}
