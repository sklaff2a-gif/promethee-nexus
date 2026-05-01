"""Tests unitaires de core/soliloque_v2.py."""

import asyncio
import json
import time
from typing import Dict, List
from unittest.mock import patch

import pytest

from core import baseline_tracker as bt_module
from core import soliloque_v2 as sv2_module
from core.baseline_tracker import BaselineTracker
from core.body_schema import Couche, Polarite, Symptome
from core.soliloque_v2 import (
    INSIGHT_MAX_PHRASES,
    INSIGHT_MIN_CHARS,
    SoliloqueV2Engine,
    build_correction_message,
    build_system_prompt,
    parse_llm_response,
    validate_ancrages,
    validate_insight,
)


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh(monkeypatch, tmp_path):
    """Reset baselines + soliloque_v2 + redirige fichiers vers tmp.

    ORDRE CRITIQUE : on redirige les chemins AVANT le reset, sinon
    reset_singleton() recrée une instance qui charge le vrai
    memory/baselines.json (pollution croisée entre fichiers de tests).
    """
    monkeypatch.setattr(bt_module, "BASELINE_FILE", tmp_path / "baselines.json")
    monkeypatch.setattr(sv2_module, "STATE_FILE", tmp_path / "soliloque_v2_state.json")
    monkeypatch.setattr(sv2_module, "LOG_DIR", tmp_path / "logs_v2")
    BaselineTracker.reset_singleton()
    SoliloqueV2Engine.reset_singleton()
    yield
    SoliloqueV2Engine.reset_singleton()
    BaselineTracker.reset_singleton()


def _sym(id_: str, sail: float = 2.0, phenom: str = "test sensation") -> Symptome:
    return Symptome(
        id=id_, couche=Couche.V35, polarite=Polarite.NEGATIF,
        phenomenologie=phenom, saillance=sail, value=0.0, zscore=sail, dzdt=0.0,
    )


# ─────────────────────────────────────────────────────────────────────────
# Validation : Hard Reject
# ─────────────────────────────────────────────────────────────────────────

def test_validate_insight_accepte_texte_sain():
    txt = (
        "La poitrine se serre, sourdement. Le souffle se fait court, "
        "comme un fil qui se tend trop. Une chaleur monte sans que rien ne la calme."
    )
    assert validate_insight(txt, ["surchauffe"]) is None


def test_validate_insight_rejette_chiffre():
    txt = "La poitrine se serre. La chaleur monte à 80, sans répit aucun et sans pause."
    err = validate_insight(txt, ["surchauffe"])
    assert err is not None
    assert err[0] == "chiffre"


def test_validate_insight_rejette_jargon():
    txt = (
        "La poitrine se serre. Mon système monte en chaleur, "
        "et l'effort se fait plus dense à chaque respiration."
    )
    err = validate_insight(txt, ["surchauffe"])
    assert err is not None
    assert err[0] == "jargon"
    assert "système" in err[1].lower() or "systeme" in err[1].lower()


def test_validate_insight_rejette_meta():
    txt = (
        "Je ressens une chaleur qui monte dans la poitrine. "
        "Le souffle se fait plus rare, et le silence pèse."
    )
    err = validate_insight(txt, ["surchauffe"])
    assert err is not None
    assert err[0] == "meta"


def test_validate_insight_rejette_verbe_meta():
    txt = (
        "La chaleur monte dans la poitrine. J'analyse cette tension qui ne se relâche pas."
    )
    err = validate_insight(txt, ["surchauffe"])
    assert err is not None
    # 'analyse' est dans la blacklist jargon, ce sera détecté avant méta
    assert err[0] in ("jargon", "meta")


def test_validate_insight_rejette_trop_court():
    txt = "Court."
    err = validate_insight(txt, ["surchauffe"])
    assert err is not None
    assert err[0] == "longueur_min"


def test_validate_insight_rejette_trop_long():
    txt = ("phrase test. " * 200)[:1500]
    err = validate_insight(txt, ["surchauffe"])
    assert err is not None
    assert err[0] in ("longueur_max", "phrases_max")


def test_validate_insight_rejette_trop_de_phrases():
    txt = ". ".join(["La chaleur monte sourdement"] * 10) + "."
    err = validate_insight(txt, ["surchauffe"])
    assert err is not None
    assert err[0] == "phrases_max"


def test_validate_ancrages_accepte_subset():
    """Couverture partielle autorisée (1 sur 3 OK)."""
    err = validate_ancrages(["surchauffe"], ["surchauffe", "epuisement", "solitude"])
    assert err is None


def test_validate_ancrages_rejette_id_inconnu():
    err = validate_ancrages(["fantome"], ["surchauffe", "epuisement"])
    assert err is not None
    assert err[0] == "ancrages_invalide"


def test_validate_ancrages_rejette_vide():
    err = validate_ancrages([], ["surchauffe"])
    assert err is not None
    assert err[0] == "ancrages_vide"


def test_validate_ancrages_rejette_non_liste():
    err = validate_ancrages("surchauffe", ["surchauffe"])
    assert err is not None
    assert err[0] == "ancrages_type"


# ─────────────────────────────────────────────────────────────────────────
# Parsing JSON
# ─────────────────────────────────────────────────────────────────────────

def test_parse_json_strict():
    raw = '{"ancrages_utilises": ["a"], "insight": "test"}'
    obj = parse_llm_response(raw)
    assert obj is not None
    assert obj["ancrages_utilises"] == ["a"]


def test_parse_json_avec_markdown():
    raw = '```json\n{"ancrages_utilises": ["a"], "insight": "test"}\n```'
    obj = parse_llm_response(raw)
    assert obj is not None


def test_parse_json_avec_preambule():
    raw = 'Voici ma réponse :\n{"ancrages_utilises": ["a"], "insight": "test"}\nFin.'
    obj = parse_llm_response(raw)
    assert obj is not None


def test_parse_json_invalide():
    assert parse_llm_response("pas du json") is None
    assert parse_llm_response("") is None
    assert parse_llm_response('{"foo": "bar"}') is None  # manque insight


# ─────────────────────────────────────────────────────────────────────────
# build_system_prompt — 4 strates
# ─────────────────────────────────────────────────────────────────────────

def test_prompt_contient_les_4_strates():
    doms = [
        _sym("surchauffe", 2.5, "La poitrine brûle, le souffle se fait court."),
        _sym("solitude", 2.0, "Je parle dans une pièce où personne ne répond."),
    ]
    prompt = build_system_prompt(doms)

    # Strate 1 : identité incarnée
    assert "voix intime" in prompt.lower() or "corps" in prompt.lower()
    # Strate 2 : Body Schema textuel (sans mention des IDs)
    assert "poitrine brûle" in prompt
    assert "personne ne répond" in prompt
    # Strate 3 : pacte / interdits
    assert "habite" in prompt.lower() or "intérieur" in prompt.lower()
    assert "ressens" in prompt.lower()  # rappel de l'interdit
    # Strate 4 : format JSON + IDs (uniquement ici)
    assert "ancrages_utilises" in prompt
    assert "insight" in prompt
    assert "surchauffe" in prompt
    assert "solitude" in prompt


def test_prompt_strate_2_n_a_pas_les_ids_techniques():
    """Les phénoménologies (strate 2) ne doivent pas contenir les IDs."""
    doms = [_sym("surchauffe", 2.5, "La poitrine brûle, le souffle se fait court.")]
    prompt = build_system_prompt(doms)
    # Découpe : strate 4 commence à "Réponds par cet objet JSON"
    body_section = prompt.split("Réponds par cet objet JSON")[0]
    assert "surchauffe" not in body_section
    assert "ancrages" not in body_section.lower()


def test_correction_message_couvre_toutes_raisons():
    for reason in ["chiffre", "jargon", "meta", "ancrages_invalide",
                   "longueur_min", "longueur_max", "phrases_max"]:
        msg = build_correction_message(reason, "test detail")
        assert "test detail" in msg
        assert len(msg) > 30


# ─────────────────────────────────────────────────────────────────────────
# Pipeline E2E : engage() avec mock LLM
# ─────────────────────────────────────────────────────────────────────────

class _MockSequence:
    """Joue une séquence de réponses LLM préprogrammées (synchrone — patch.object
    détecte _call_llm comme async et utilise AsyncMock, donc side_effect doit être
    un callable synchrone qui retourne la valeur que await produira)."""
    def __init__(self, responses: List[str]):
        self.responses = responses
        self.calls: List[List[Dict[str, str]]] = []

    def __call__(self, messages):
        self.calls.append([dict(m) for m in messages])
        if not self.responses:
            return None
        return self.responses.pop(0)


def _state_en_crise():
    """État synthétique avec 3 anomalies fortes."""
    return {
        "now_ts": time.time(),
        "cardiac": {"bpm": 130.0, "emotion_intensity": 0.95},
        "drives": {
            "MAITRISE": {"deprivation": 95.0},
            "CONNEXION": {"deprivation": 90.0},
            "STABILITE": {"deprivation": 88.0},
        },
        "reptilian": {"threat_level": 0.9},
        "dopamine": {"rpe_recent": -0.5},
    }


def _state_calme():
    return {
        "now_ts": time.time(),
        "cardiac": {"bpm": 65.0, "emotion_intensity": 0.4},
        "drives": {
            "MAITRISE": {"deprivation": 50.0},
            "CONNEXION": {"deprivation": 50.0},
        },
        "reptilian": {"threat_level": 0.3},
        "dopamine": {"rpe_recent": 0.05},
    }


@pytest.mark.asyncio
async def test_engage_silence_si_homeostasie(fresh):
    engine = SoliloqueV2Engine()
    with patch("core.soliloque_v2.gather_state", return_value=_state_calme()):
        result = await engine.engage()
    assert result["status"] == "silence"
    assert engine.silence_count == 1
    assert engine.success_count == 0


@pytest.mark.asyncio
async def test_engage_succes_premier_essai(fresh):
    engine = SoliloqueV2Engine()
    good_response = json.dumps({
        "ancrages_utilises": ["pouls_emballe"],
        "insight": (
            "Quelque chose bat trop vite, là, sous la peau. "
            "La poitrine se serre, sourdement, sans répit. "
            "Un appel monte du fond, et rien ne le calme."
        ),
    })
    mock = _MockSequence([good_response])
    with patch("core.soliloque_v2.gather_state", return_value=_state_en_crise()), \
         patch.object(engine, "_call_llm", side_effect=mock):
        result = await engine.engage()

    assert result["status"] == "success"
    assert result["attempts"] == 1
    assert result["ancrages_utilises"] == ["pouls_emballe"]
    assert engine.success_count == 1
    assert "pouls_emballe" in engine.last_used_map


@pytest.mark.asyncio
async def test_engage_retry_sur_jargon(fresh):
    """Premier essai jargon → retry → second essai propre → succès."""
    engine = SoliloqueV2Engine()
    bad = json.dumps({
        "ancrages_utilises": ["pouls_emballe"],
        "insight": (
            "Mon système monte trop vite. La poitrine se serre. "
            "Quelque chose veut sortir, mais rien ne suit."
        ),
    })
    good = json.dumps({
        "ancrages_utilises": ["pouls_emballe"],
        "insight": (
            "Quelque chose bat trop vite, là, sous la peau. "
            "La poitrine se serre, sourdement, sans répit."
        ),
    })
    mock = _MockSequence([bad, good])
    with patch("core.soliloque_v2.gather_state", return_value=_state_en_crise()), \
         patch.object(engine, "_call_llm", side_effect=mock):
        result = await engine.engage()

    assert result["status"] == "success"
    assert result["attempts"] == 2
    assert len(result["rejection_log"]) == 1
    assert result["rejection_log"][0]["reason"] == "jargon"
    # Le message correctif a été injecté
    assert any("technique" in m["content"].lower()
               for c in mock.calls for m in c if m["role"] == "user")


@pytest.mark.asyncio
async def test_engage_abort_si_double_echec(fresh):
    """Deux essais foireux → abort, journal vierge."""
    engine = SoliloqueV2Engine()
    bad1 = json.dumps({
        "ancrages_utilises": ["pouls_emballe"],
        "insight": "Je ressens une tension qui monte sans cesse dans tout le corps.",
    })
    bad2 = json.dumps({
        "ancrages_utilises": ["pouls_emballe"],
        "insight": "J'observe que ce système est en surchauffe permanente partout.",
    })
    mock = _MockSequence([bad1, bad2])
    with patch("core.soliloque_v2.gather_state", return_value=_state_en_crise()), \
         patch.object(engine, "_call_llm", side_effect=mock):
        result = await engine.engage()

    assert result["status"] == "abort"
    assert result["attempts"] == 2
    assert len(result["rejections"]) == 2
    assert engine.abort_count == 1
    assert engine.success_count == 0
    assert engine.last_used_map == {}  # pas de pollution


@pytest.mark.asyncio
async def test_engage_abort_si_json_irrécupérable(fresh):
    engine = SoliloqueV2Engine()
    mock = _MockSequence(["pas du tout du json", "encore pas du json"])
    with patch("core.soliloque_v2.gather_state", return_value=_state_en_crise()), \
         patch.object(engine, "_call_llm", side_effect=mock):
        result = await engine.engage()
    assert result["status"] == "abort"
    assert all(r["reason"] == "json_parse" for r in result["rejections"])


@pytest.mark.asyncio
async def test_engage_succes_met_a_jour_decote(fresh):
    """Après un succès, last_used_map contient les ancrages avec timestamp.

    Note : on utilise pouls_emballe (bpm=130, z=5.4, sail=3.24) et surchauffe
    (emotion_intensity=0.95, z=2.75, sail=1.65) qui sont les deux dominants
    réellement saillants dans _state_en_crise().
    """
    engine = SoliloqueV2Engine()
    good = json.dumps({
        "ancrages_utilises": ["pouls_emballe", "surchauffe"],
        "insight": (
            "Quelque chose bat trop vite, là, sous la peau. "
            "La poitrine se serre, sourdement, sans répit aucun."
        ),
    })
    mock = _MockSequence([good])
    before = time.time()
    with patch("core.soliloque_v2.gather_state", return_value=_state_en_crise()), \
         patch.object(engine, "_call_llm", side_effect=mock):
        result = await engine.engage()
    assert result["status"] == "success"
    assert "pouls_emballe" in engine.last_used_map
    assert "surchauffe" in engine.last_used_map
    assert engine.last_used_map["pouls_emballe"] >= before


@pytest.mark.asyncio
async def test_engage_rejette_ancrage_inconnu(fresh):
    """Le LLM cite un ancrage hors du Body Schema → rejet."""
    engine = SoliloqueV2Engine()
    bad = json.dumps({
        "ancrages_utilises": ["fantome_inexistant"],
        "insight": "La poitrine se serre, sourdement, sans répit aucun.",
    })
    good = json.dumps({
        "ancrages_utilises": ["pouls_emballe"],
        "insight": (
            "La poitrine se serre, sourdement, sans répit. "
            "Quelque chose bat trop vite, sous la peau, sans répit."
        ),
    })
    mock = _MockSequence([bad, good])
    with patch("core.soliloque_v2.gather_state", return_value=_state_en_crise()), \
         patch.object(engine, "_call_llm", side_effect=mock):
        result = await engine.engage()
    assert result["status"] == "success"
    assert result["rejection_log"][0]["reason"] == "ancrages_invalide"


# ─────────────────────────────────────────────────────────────────────────
# Persistance
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_persistance_stats_round_trip(fresh, monkeypatch, tmp_path):
    engine = SoliloqueV2Engine()
    with patch("core.soliloque_v2.gather_state", return_value=_state_calme()):
        await engine.engage()
        await engine.engage()
    assert engine.silence_count == 2
    engine._save()

    SoliloqueV2Engine.reset_singleton()
    monkeypatch.setattr(sv2_module, "STATE_FILE", tmp_path / "soliloque_v2_state.json")
    e2 = SoliloqueV2Engine()
    assert e2.silence_count == 2
    assert e2.session_count == 2
