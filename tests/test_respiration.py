"""
Tests de la respiration — présence continue de Prométhée.

Couvre :
- get_warm_buffer : fallback sans crash quand la respiration n'a pas tourné
- _inspire : le tampon chaud est rafraîchi (sans LLM)
- valve d'escalade : BREATH_GASP sur seuil HAUT + front montant
- valve : pas de gasp sous le seuil
- cooldown : un même symptôme ne re-gasp pas dans la fenêtre
- non-persistance : le tampon est de la mémoire de travail (volontaire)
- reset_singleton
"""

import time

import pytest

from core import body_schema
from core.body_schema import Couche, Polarite, Symptome
from core.respiration import (
    GASP_COOLDOWN_S,
    GASP_SAILLANCE_THRESHOLD,
    Respiration,
)


@pytest.fixture
def resp():
    """Respiration fraîche, non abonnée au bus global (on pilote à la main)."""
    Respiration.reset_singleton()
    r = Respiration()
    r._alive = True  # on simule init() sans s'abonner au bus
    yield r
    Respiration.reset_singleton()


def _make_symptome(sid: str, saillance: float) -> Symptome:
    return Symptome(
        id=sid,
        couche=Couche.V35,
        polarite=Polarite.NEGATIF,
        phenomenologie="phénoménologie de test",
        saillance=saillance,
        value=1.0,
        zscore=2.0,
        dzdt=1.0,
    )


def _patch_inspiration(monkeypatch, dominants):
    """Rend l'inspiration déterministe (aucune lecture réelle d'organe)."""
    monkeypatch.setattr(
        body_schema, "gather_state",
        lambda *a, **k: {"cardiac": {"bpm": 62.0, "current_emotion": "serenite"}},
    )
    monkeypatch.setattr(
        body_schema, "format_etat_interne", lambda *a, **k: "BUF_TEST",
    )
    monkeypatch.setattr(
        body_schema, "state_to_body_schema", lambda *a, **k: list(dominants),
    )
    monkeypatch.setattr(
        body_schema, "select_dominants", lambda symptomes, *a, **k: list(symptomes),
    )


# ------------------------------------------------------------------
# get_warm_buffer
# ------------------------------------------------------------------

def test_warm_buffer_fallback_no_crash(resp, monkeypatch):
    """Tampon jamais rempli → recalcul à la volée, jamais de crash."""
    monkeypatch.setattr(body_schema, "format_etat_interne", lambda *a, **k: "FALLBACK")
    assert resp.warm_buffer is None
    assert resp.get_warm_buffer() == "FALLBACK"


def test_warm_buffer_fallback_returns_empty_on_error(resp, monkeypatch):
    """Si même le fallback échoue, on retourne "" (jamais de valeur fabriquée)."""
    def boom(*a, **k):
        raise RuntimeError("organe absent")
    monkeypatch.setattr(body_schema, "format_etat_interne", boom)
    assert resp.get_warm_buffer() == ""


# ------------------------------------------------------------------
# _inspire
# ------------------------------------------------------------------

def test_inspire_populates_warm_buffer(resp, monkeypatch):
    _patch_inspiration(monkeypatch, dominants=[])
    resp._inspire()
    assert resp.warm_buffer is not None
    assert resp.warm_buffer["text"] == "BUF_TEST"
    assert resp.warm_buffer["bpm"] == 62.0
    assert resp.warm_buffer["emotion"] == "serenite"
    assert resp._breath_count == 1
    # Le tampon chaud prime sur le fallback
    assert resp.get_warm_buffer() == "BUF_TEST"


# ------------------------------------------------------------------
# Valve d'escalade
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valve_gasp_on_rising_edge_above_threshold(resp, monkeypatch):
    dom = _make_symptome("pouls_emballe", GASP_SAILLANCE_THRESHOLD + 1.0)
    _patch_inspiration(monkeypatch, dominants=[dom])

    received = []
    from core.event_bus.bus import bus

    async def _collector(event):
        received.append(event)

    bus.subscribe("BREATH_GASP", _collector)
    await resp._on_cardiac_beat({})

    assert len(received) == 1
    assert received[0]["symptome"] == "pouls_emballe"
    assert resp._gasp_count == 1


@pytest.mark.asyncio
async def test_valve_no_gasp_below_threshold(resp, monkeypatch):
    dom = _make_symptome("souffle_court", GASP_SAILLANCE_THRESHOLD - 0.5)
    _patch_inspiration(monkeypatch, dominants=[dom])

    received = []
    from core.event_bus.bus import bus

    async def _collector(event):
        received.append(event)

    bus.subscribe("BREATH_GASP", _collector)
    await resp._on_cardiac_beat({})

    assert received == []
    assert resp._gasp_count == 0


@pytest.mark.asyncio
async def test_valve_cooldown_blocks_second_gasp(resp, monkeypatch):
    """Même en montant encore, un symptôme ne re-gasp pas dans le cooldown."""
    received = []
    from core.event_bus.bus import bus

    async def _collector(event):
        received.append(event)

    bus.subscribe("BREATH_GASP", _collector)

    # Souffle 1 : saillance haute → gasp
    dom1 = _make_symptome("alarme_sourde", GASP_SAILLANCE_THRESHOLD + 1.0)
    _patch_inspiration(monkeypatch, dominants=[dom1])
    await resp._on_cardiac_beat({})
    assert len(received) == 1

    # Souffle 2 : saillance ENCORE plus haute (front montant) mais cooldown actif
    dom2 = _make_symptome("alarme_sourde", GASP_SAILLANCE_THRESHOLD + 2.0)
    _patch_inspiration(monkeypatch, dominants=[dom2])
    await resp._on_cardiac_beat({})
    assert len(received) == 1  # toujours 1 : le cooldown a bloqué


@pytest.mark.asyncio
async def test_valve_no_gasp_on_plateau(resp, monkeypatch):
    """Saillance soutenue (pas de front montant) → pas de gasp répété."""
    received = []
    from core.event_bus.bus import bus

    async def _collector(event):
        received.append(event)

    bus.subscribe("BREATH_GASP", _collector)

    dom = _make_symptome("tension_interne", GASP_SAILLANCE_THRESHOLD + 1.0)
    _patch_inspiration(monkeypatch, dominants=[dom])
    await resp._on_cardiac_beat({})          # premier franchissement → gasp
    # Contourne le cooldown pour isoler la logique de front montant
    resp._gasp_cooldowns.clear()
    await resp._on_cardiac_beat({})          # même saillance → pas un front montant
    assert len(received) == 1


@pytest.mark.asyncio
async def test_dead_breath_does_nothing(resp, monkeypatch):
    """Si la respiration n'est pas vivante, un battement ne fait rien."""
    resp._alive = False
    _patch_inspiration(monkeypatch, dominants=[_make_symptome("x", 9.0)])
    await resp._on_cardiac_beat({})
    assert resp._breath_count == 0
    assert resp.warm_buffer is None


# ------------------------------------------------------------------
# Non-persistance & singleton
# ------------------------------------------------------------------

def test_no_persistence(resp):
    """Le tampon est mémoire de travail : aucune API de persistance."""
    assert not hasattr(resp, "save")
    assert not hasattr(resp, "_save")
    assert not hasattr(resp, "_load")


def test_reset_singleton_clears_buffer(monkeypatch):
    Respiration.reset_singleton()
    r1 = Respiration()
    r1._alive = True
    _patch_inspiration(monkeypatch, dominants=[])
    r1._inspire()
    assert r1.warm_buffer is not None

    Respiration.reset_singleton()
    r2 = Respiration()
    assert r2 is not r1
    assert r2.warm_buffer is None


def test_get_stats_shape(resp, monkeypatch):
    _patch_inspiration(monkeypatch, dominants=[])
    resp._inspire()
    stats = resp.get_stats()
    assert stats["alive"] is True
    assert stats["breath_count"] == 1
    assert stats["gasp_count"] == 0
    assert "buffer_age_s" in stats
