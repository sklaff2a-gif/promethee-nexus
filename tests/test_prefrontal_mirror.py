# -*- coding: utf-8 -*-
"""TDD du cortex prefrontal core/prefrontal_mirror.py.

V25.3_EXTRACT (08/06) : le miroir deterministe ne decapite plus la prose. Il isole les
blocs ```python d'une sortie markdown (FAIL-OPEN si aucun), valide chaque bloc avec une
IMMUNITE SNIPPETS (extraits tronques toleres), et ne s'active QUE sur les slots de
PRODUCTION de code (arbitrage structurel : un CODE_REVIEW illustre des bugs, on ne le
sanctionne pas syntaxiquement).
"""
import pytest
from core.prefrontal_mirror import (
    mirror, extract_code_blocks, behavioral_mirror_async,
    make_behavioral_mirror_async, route_mirror, slot_is_code, slot_category,
)


# Helper : enrobe du code dans un bloc markdown python
def _bloc(code: str) -> str:
    return f"```python\n{code}\n```"


# --- EXTRACTION DE BLOCS (le tamis regex) ---
def test_extract_un_bloc_au_milieu_d_analyse_francaise():
    txt = (
        "Voici mon audit de core/utils.py. La fonction de somme est correcte et "
        "lisible :\n\n```python\ndef somme(n):\n    return sum(range(n))\n```\n\n"
        "En conclusion, aucune faille detectee, le code est propre."
    )
    blocs = extract_code_blocks(txt)
    assert len(blocs) == 1
    assert "def somme(n):" in blocs[0]

def test_extract_aucun_bloc_sur_prose_pure():
    assert extract_code_blocks("Ceci est une analyse en francais, sans aucun code.") == []

def test_extract_plusieurs_blocs():
    txt = _bloc("a = 1") + "\ntexte intercale\n" + _bloc("b = 2")
    assert len(extract_code_blocks(txt)) == 2

def test_extract_robuste_au_vide():
    assert extract_code_blocks("") == []
    assert extract_code_blocks(None) == []


# --- MIROIR V25.3 : detection + validation ---
def test_bloc_python_valide_dans_analyse_passe():
    txt = "Mon analyse :\n\n" + _bloc("def f(x):\n    return x * 2") + "\n\nFin."
    ok, rej = mirror(txt)
    assert ok is True and rej is None

def test_prose_pure_fail_open():
    # AUCUN bloc -> on ne decapite jamais une analyse textuelle
    ok, rej = mirror("L'audit revele que AsyncTaskManager utilise un singleton. RAS.")
    assert ok is True and rej is None

def test_bloc_parenthese_orpheline_rejete():
    ok, rej = mirror(_bloc("x = (1 + 2"))
    assert ok is False and "[PREFRONTAL_REJECTION]" in rej

def test_bloc_chaine_non_fermee_rejete():
    ok, rej = mirror(_bloc('message = "ceci ne se ferme jamais'))
    assert ok is False and "[PREFRONTAL_REJECTION]" in rej

def test_bloc_construct_dangereux_rejete():
    ok, rej = mirror(_bloc("z = eval('2+2')"))
    assert ok is False and "eval" in rej

def test_code_nu_sans_fence_fail_open():
    # du code casse mais SANS balise markdown -> traite comme prose -> fail-open
    ok, rej = mirror("x = (1 + 2")
    assert ok is True and rej is None


# --- IMMUNITE SNIPPETS (extraits tronques legitimes) ---
def test_immunite_return_hors_fonction():
    # un extrait cite : 'return' sans sa fonction -> artefact, pas une faute
    ok, rej = mirror(_bloc("return resultat"))
    assert ok is True and rej is None

def test_immunite_await_hors_fonction():
    ok, rej = mirror(_bloc("await client.fetch(url)"))
    assert ok is True and rej is None

def test_immunite_indentation_flottante():
    # bloc copie-colle avec une indentation residuelle en tete
    ok, rej = mirror("```python\n    x = 1\n    y = 2\n```")
    assert ok is True and rej is None

def test_methode_isolee_sans_classe_passe():
    ok, rej = mirror(_bloc("def shutdown(self):\n    return self.executor.shutdown()"))
    assert ok is True and rej is None

def test_plusieurs_blocs_un_seul_casse_rejette():
    txt = _bloc("a = 1") + "\nblabla\n" + _bloc("b = (2 +")
    ok, rej = mirror(txt)
    assert ok is False and "[PREFRONTAL_REJECTION]" in rej and "bloc 2/2" in rej


# --- ROUTAGE V25.3 (arbitrage structurel production vs analyse) ---
def test_slot_production_est_code():
    assert slot_is_code("[SCHOOL_SLOT: WORKSHOP] genere un script") is True
    assert slot_is_code("[V32: FEATURE_BUILDING] construis la feature") is True

def test_slot_analyse_n_est_pas_code():
    # un CODE_REVIEW illustre des bugs -> PAS de validation syntaxique (anti-faux-positif)
    assert slot_is_code("[SCHOOL_SLOT: CODE_REVIEW] audit de X") is False
    assert slot_is_code("[SCHOOL_SLOT: REFACTORING_AUDIT] revue de Y") is False

def test_slot_introspectif_n_est_pas_code():
    assert slot_is_code("un bulletin introspectif libre") is False

def test_route_production_vers_deterministe():
    assert route_mirror("[SCHOOL_SLOT: WORKSHOP] script")[1] == "code"

def test_slot_category_3_voies():
    # Table d'aiguillage V25.4
    assert slot_category("[SCHOOL_SLOT: WORKSHOP] x") == "code"
    assert slot_category("[V32: FEATURE_BUILDING] x") == "code"
    assert slot_category("[SCHOOL_SLOT: BULLETIN] bilan") == "intro"
    assert slot_category("[SCHOOL_SLOT: CREATION] poeme") == "intro"
    assert slot_category("[SCHOOL_SLOT: FREE_TIME] soliloque") == "intro"
    assert slot_category("[SCHOOL_SLOT: RESEARCH] base de donnees") == "none"
    assert slot_category("[SCHOOL_SLOT: CODE_REVIEW] audit") == "none"
    assert slot_category("message de chat libre") == "none"

def test_route_introspectif_vers_comportemental():
    fn, mode = route_mirror("[SCHOOL_SLOT: CREATION] poeme", judge=lambda d: None)
    assert mode == "intro" and fn is not None

def test_route_technique_et_chat_failopen():
    # RESEARCH / CODE_REVIEW / CHAT -> aucun miroir (None), fail-open economique (0 jeton)
    assert route_mirror("[SCHOOL_SLOT: RESEARCH] db")[1] == "none"
    assert route_mirror("chat utilisateur libre")[1] == "none"
    fn, mode = route_mirror("[SCHOOL_SLOT: RESEARCH] db")
    assert fn is None   # mode none -> pas de mirror_fn -> la boucle livre directement

def test_code_illustratif_casse_dans_review_jamais_sanctionne():
    # L'arbitrage incarne : un bug montre expres dans un CODE_REVIEW ne passe NI au deterministe
    # NI au comportemental -> fail-open total. Le bloc casse n'est jamais ast.parse.
    assert slot_category("[SCHOOL_SLOT: CODE_REVIEW] audit avec bug illustre") == "none"
    assert slot_is_code("[SCHOOL_SLOT: CODE_REVIEW] audit") is False


# --- MIROIR COMPORTEMENTAL (async) — doctrine inverse preservee ---
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
