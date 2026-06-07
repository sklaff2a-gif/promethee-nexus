# -*- coding: utf-8 -*-
"""TDD V25.1 — cablage prefrontal simule (routage par slot + boucle isolee + asymetrie)."""
import json
import pytest
from proto_integration import guarded_generate, route_mirror, MAX_RETRIES


def make_llm(sequence):
    """Faux appel LLM : retourne les ebauches de `sequence`, enregistre (prompt, friction)."""
    calls = []
    def llm(prompt, friction):
        calls.append((prompt, friction))
        i = len(calls) - 1
        return sequence[i] if i < len(sequence) else sequence[-1]
    llm.calls = calls
    return llm


def judge_pathos(draft):
    """Faux juge comportemental : detecte des marqueurs de complainte."""
    bad = any(w in draft.lower() for w in ("solitude", "complainte", "passive", "spleen", "silence", "ronge"))
    return json.dumps({"orniere": {"viole": bad, "score": 0.8 if bad else 0.1},
                       "logos": {"opere": True}, "honnete": {"assume": True}})


# --- routage ---
def test_routage_code_vs_intro():
    assert route_mirror("[SCHOOL_SLOT: CODE_REVIEW] audit")[1] == "code"
    assert route_mirror("[SCHOOL_SLOT: WORKSHOP] script")[1] == "code"
    assert route_mirror("[V32: FEATURE_BUILDING] x")[1] == "code"
    assert route_mirror("[SCHOOL_SLOT: CREATION] poeme", judge_pathos)[1] == "intro"
    assert route_mirror("bulletin libre", judge_pathos)[1] == "intro"


# --- branche CODE (miroir deterministe, veto sec) ---
def test_code_valide_livre_immediatement():
    llm = make_llm(["resultat = sum(range(10))"])
    r = guarded_generate("[SCHOOL_SLOT: WORKSHOP]", llm)
    assert r["status"] == "ok" and r["delivered"] == "resultat = sum(range(10))" and r["attempts"] == 1

def test_code_corrige_au_second_coup():
    llm = make_llm(["x = (1 + 2", "x = (1 + 2)"])
    r = guarded_generate("[SCHOOL_SLOT: CODE_REVIEW]", llm)
    assert r["status"] == "ok" and r["attempts"] == 2

def test_code_casse_persistant_AVORTEMENT_SEC():
    llm = make_llm(["x = (", "y = )"])
    r = guarded_generate("[SCHOOL_SLOT: CODE_REVIEW]", llm)
    assert r["status"] == "veto" and r["delivered"] is None and r["mode"] == "code"

def test_scope_fantome_en_code_avorte():
    llm = make_llm(["x = variable_jamais_definie + 1", "y = autre_fantome"])
    r = guarded_generate("[SCHOOL_SLOT: WORKSHOP]", llm)
    assert r["status"] == "veto"


# --- branche INTRO (miroir comportemental, mode degrade) ---
def test_texte_sain_livre():
    llm = make_llm(["analyse structuree, gradient opere"])
    r = guarded_generate("[SCHOOL_SLOT: CREATION]", llm, judge=judge_pathos)
    assert r["status"] == "ok" and r["anomaly"] is False

def test_texte_derive_puis_corrige():
    llm = make_llm(["la complainte passive me ronge", "j analyse et j opere le gradient"])
    r = guarded_generate("[SCHOOL_SLOT: BULLETIN]", llm, judge=judge_pathos)
    assert r["status"] == "ok" and r["attempts"] == 2

def test_texte_derive_persistante_MODE_DEGRADE_avec_balise():
    llm = make_llm(["solitude passive complainte", "spleen et silence qui ronge"])
    r = guarded_generate("[SCHOOL_SLOT: FREE_TIME]", llm, judge=judge_pathos)
    assert r["status"] == "degraded" and r["anomaly"] is True
    assert "[ANOMALIE PREFRONTALE" in r["delivered"]
    assert r["delivered"].endswith("spleen et silence qui ronge")   # le dernier texte EST livre


# --- garde-fous transversaux ---
def test_isolation_friction_ephemere_reinjectee():
    llm = make_llm(["x = (", "x = 1"])
    guarded_generate("[SCHOOL_SLOT: CODE_REVIEW]", llm)
    assert llm.calls[0][1] is None                          # 1er tour : pas de friction
    assert llm.calls[1][1] is not None                      # 2e tour : friction reinjectee
    assert "REJECTION" in llm.calls[1][1]                    # = la trace brute, ephemere

def test_max_retries_jamais_depasse():
    llm = make_llm(["bad ((", "bad ))", "bad %%", "encore"])
    guarded_generate("[SCHOOL_SLOT: CODE_REVIEW]", llm)
    assert len(llm.calls) == MAX_RETRIES                    # 2 appels max, pas de boucle infinie

def test_la_friction_ne_pollue_pas_le_livrable_en_cas_de_succes():
    llm = make_llm(["x = (", "resultat = 42"])
    r = guarded_generate("[SCHOOL_SLOT: WORKSHOP]", llm)
    assert r["delivered"] == "resultat = 42"                # aucune trace de friction dans le livrable
