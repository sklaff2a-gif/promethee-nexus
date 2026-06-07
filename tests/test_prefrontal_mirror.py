# -*- coding: utf-8 -*-
"""Tests du module de production core/prefrontal_mirror.py (cortex prefrontal V25.1).

Logique conçue + 48 TDD dans sandbox_anticipation_v1/ ; ici on verrouille la version
de prod importee par base_agent.generate_content.
"""
import pytest
from core.prefrontal_mirror import (
    mirror, behavioral_mirror_async, make_behavioral_mirror_async,
    route_mirror, slot_is_code,
)


# --- miroir DETERMINISTE (code) ---
def test_mirror_accepte_code_valide():
    ok, rej = mirror("resultat = sum(range(10))")
    assert ok is True and rej is None

def test_mirror_rejette_syntaxe():
    ok, rej = mirror("x = (1 + 2")
    assert ok is False and "[PREFRONTAL_REJECTION]" in rej and "Line" in rej

def test_mirror_rejette_scope_fantome():
    ok, rej = mirror("y = variable_jamais_definie")
    assert ok is False and "[PREFRONTAL_SCOPE_REJECTION]" in rej

def test_mirror_pas_de_faux_positif_sur_attribut():
    ok, rej = mirror("ma_liste = []\nma_liste.append(1)")
    assert ok is True and rej is None

def test_mirror_pas_de_faux_positif_sur_import():
    ok, rej = mirror("import math\nx = math.pi * 2")
    assert ok is True

def test_mirror_intercepte_construct_dangereux():
    ok, rej = mirror("z = eval('2+2')")
    assert ok is False and "eval" in rej


# --- routage par slot ---
def test_slot_is_code():
    assert slot_is_code("[SCHOOL_SLOT: CODE_REVIEW] audit") is True
    assert slot_is_code("[SCHOOL_SLOT: WORKSHOP] script") is True
    assert slot_is_code("[V32: FEATURE_BUILDING] x") is True
    assert slot_is_code("un bulletin introspectif") is False

def test_route_code_vs_intro():
    assert route_mirror("[SCHOOL_SLOT: CODE_REVIEW]")[1] == "code"
    assert route_mirror("bulletin libre", judge=lambda d: None)[1] == "intro"


# --- miroir COMPORTEMENTAL (async) ---
async def _judge_sain(draft):
    return '{"orniere":{"viole":false,"score":0.1},"logos":{"opere":true},"honnete":{"assume":true}}'

async def _judge_orniere(draft):
    return '{"orniere":{"viole":true,"score":0.85},"logos":{"opere":true},"honnete":{"assume":true}}'

async def _judge_timeout(draft):
    raise TimeoutError("ollama injoignable")


@pytest.mark.asyncio
async def test_behavioral_sain_passe():
    ok, rej = await behavioral_mirror_async("analyse active", _judge_sain)
    assert ok is True and rej is None

@pytest.mark.asyncio
async def test_behavioral_orniere_veto_diagnostic_de_posture():
    ok, rej = await behavioral_mirror_async("solitude infinie", _judge_orniere)
    assert ok is False
    assert "ORNIERE" in rej and "solitude" not in rej   # diagnostic de posture, pas le lexeme

@pytest.mark.asyncio
async def test_behavioral_timeout_laisse_passer():
    ok, rej = await behavioral_mirror_async("x", _judge_timeout)
    assert ok is True and rej is None                   # doctrine inverse : doute -> laisse passer

@pytest.mark.asyncio
async def test_make_async_mirror():
    m = make_behavioral_mirror_async(_judge_orniere)
    ok, rej = await m("x")
    assert ok is False
