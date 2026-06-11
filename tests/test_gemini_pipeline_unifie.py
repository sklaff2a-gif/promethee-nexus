# -*- coding: utf-8 -*-
"""TDD du PIPELINE UNIFIE Gemini (audit 11/06) — « il parlait sans son corps ».

Avant : le chemin Gemini (questions profondes) faisait un return ANTICIPE qui
sautait tout le post-traitement du chat : P16 injection synaptique, auto-actions
+ boucle agentique, anti-boucle, attention conjointe, curiosity seeds,
CHAT_RESPONSE, stimulate_heart. Decouvert a l'atelier des manques (ses !ancre
emis via Gemini etaient ignores), ampleur confirmee par l'audit profond.

Apres : full_response non vide -> sentinelle gemini_handled -> le streaming
local est saute et la reponse Gemini REJOINT le pipeline commun.

Ces tests verrouillent l'invariant STRUCTURELLEMENT (AST sur le source de
chat()) : un return anticipe reintroduit dans le bloc Gemini = rouge immediat.
Les 6896 tests fonctionnels mockent les LLM et ne voyaient pas ce bug — c'est
precisement pourquoi l'invariant est verrouille a la structure.
"""
import ast
import inspect
import textwrap

import pytest

from core.chat_engine import ChatEngine


@pytest.fixture(scope="module")
def chat_tree():
    src = textwrap.dedent(inspect.getsource(ChatEngine.chat))
    return ast.parse(src), src


def _lineno_of(src: str, needle: str) -> int:
    """1-based lineno de la 1re ligne contenant needle (assert si absente)."""
    for i, line in enumerate(src.splitlines(), start=1):
        if needle in line:
            return i
    raise AssertionError(f"introuvable dans chat(): {needle!r}")


# ─── L'invariant central : plus de return anticipe dans le bloc Gemini ───

def test_aucun_return_entre_gemini_et_la_jonction(chat_tree):
    """Entre `full_response = gemini_response` et `gemini_handled =`,
    aucun return ne doit exister : la reponse Gemini DOIT rejoindre le
    pipeline commun (P16, auto-actions, coeur)."""
    tree, src = chat_tree
    l_gemini = _lineno_of(src, "full_response = gemini_response")
    l_jonction = _lineno_of(src, "gemini_handled = bool(full_response)")
    returns = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Return) and l_gemini < n.lineno < l_jonction
    ]
    assert returns == [], (
        f"return anticipe reintroduit dans le bloc Gemini (lignes {returns}) : "
        "le chemin Gemini doit rejoindre le pipeline commun, pas sortir."
    )


def test_le_streaming_local_est_conditionne(chat_tree):
    """La sentinelle `if not gemini_handled:` doit garder le streaming local
    (sinon double generation : Gemini PUIS Ollama sur le meme message)."""
    _, src = chat_tree
    l_sentinelle = _lineno_of(src, "if not gemini_handled:")
    l_stream = _lineno_of(src, 'gpu_scheduler.access("chat_stream")')
    assert l_sentinelle < l_stream, (
        "le streaming local doit etre SOUS la sentinelle gemini_handled"
    )


def test_pas_de_double_append_dans_le_bloc_gemini(chat_tree):
    """Le bloc Gemini ne doit plus faire son propre messages.append /
    _trim_and_save / _satisfy_connexion : le pipeline commun les fait UNE fois
    (sinon la reponse serait dupliquee dans l'historique)."""
    _, src = chat_tree
    l_gemini = _lineno_of(src, "full_response = gemini_response")
    l_jonction = _lineno_of(src, "gemini_handled = bool(full_response)")
    bloc = "\n".join(src.splitlines()[l_gemini - 1:l_jonction - 1])
    for interdit in ("self.messages.append", "_trim_and_save", "_satisfy_connexion"):
        assert interdit not in bloc, (
            f"{interdit} present dans le bloc Gemini : doublon avec le "
            "pipeline commun (append/trim/satisfy y sont deja faits)."
        )


def test_full_response_initialise_avant_le_bloc_gemini(chat_tree):
    """`full_response = \"\"` doit exister AVANT la detection Gemini, sinon
    NameError sur `bool(full_response)` quand la question n'est pas profonde."""
    _, src = chat_tree
    l_init = _lineno_of(src, 'full_response = ""')
    l_deep = _lineno_of(src, "_DEEP_KEYWORD_PATTERNS if p.search")
    assert l_init < l_deep, "full_response doit etre initialise avant le routage Gemini"


# ─── Le pipeline commun reste complet en aval de la jonction ───

def test_pipeline_commun_complet_apres_la_jonction(chat_tree):
    """Les organes du pipeline commun doivent tous etre APRES la jonction :
    c'est eux que le return anticipe privait au chemin Gemini."""
    _, src = chat_tree
    l_jonction = _lineno_of(src, "gemini_handled = bool(full_response)")
    for organe in (
        "_clean_response_commands",       # anti-hallucination
        "CHAT P16: synaptic injection",   # corps synaptique
        "_scan_response_actions",         # auto-actions (!run, !ancre...)
        "_plant_curiosity_seeds",         # graines de curiosite
        "_stimulate_heart",               # le coeur bat
        '"CHAT_RESPONSE"',                # l'evenement de reponse
    ):
        assert _lineno_of(src, organe) > l_jonction, (
            f"{organe} devrait etre dans le pipeline commun (apres la jonction)"
        )


def test_emergent_sources_gemini(chat_tree):
    """Le chemin Gemini doit marquer emergent_sources=[\"gemini\"] pour que
    l'historique trace la provenance (badge UI)."""
    _, src = chat_tree
    assert 'emergent_sources = ["gemini"] if gemini_handled else []' in src
