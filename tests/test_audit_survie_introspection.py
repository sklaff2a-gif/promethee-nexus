"""V14.6 — AUDIT_SURVIE refondu : tests de l'introspection (amygdale + Body Schema).

Avant V14.6, _execute_audit_survie() ne lisait que CPU/RAM/dopamine/drives
et pouvait déclarer "tout nominal" pendant que le reptilien hurlait à la
nécrose synaptique (cas observé en 42h de torpeur du 29/04).

V14.6 ajoute le CYCLE 5 : lecture de threat_memories.stale_dream + dette de
rêve directe via Body Schema. Discursif uniquement, pas de préemption.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from core.autonomy_engine import (
    AUDIT_SURVIE_COOLDOWN_S,
    AUDIT_SURVIE_DREAM_DETTE_ALERT_H,
    AUDIT_SURVIE_STALE_DREAM_SEVERITY_ALERT,
    AutonomyEngine,
)


@pytest.fixture
def engine():
    """Instance bare-bones — _execute_audit_survie utilise très peu d'attributs."""
    inst = object.__new__(AutonomyEngine)
    inst._last_audit_survie_ts = 0.0
    inst.error_streak = 0
    inst.routine_history = []
    yield inst


def _empty_state():
    """State Body Schema sans dette élevée (synaptic vide ou récente)."""
    return {"synaptic": {"dream_dette_h": 1.0}}


def _high_debt_state(dette_h: float = 18.0):
    """State avec dette de rêve élevée."""
    return {"synaptic": {"dream_dette_h": dette_h}}


def _make_threat_memory(severity: float):
    from core.reptilian_core import ThreatMemory
    return ThreatMemory(
        pattern="stale_dream",
        severity=severity,
        occurrences=1,
        last_seen=time.time(),
        conditioned_reflex="ADRENALINE",
    )


# ─────────────────────────────────────────────────────────────────────────
# CYCLE 5 nouveau — amygdale (stale_dream)
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stale_dream_au_seuil_genere_alerte_critique(engine):
    """severity 4.0 (= seuil) → alerte CRITIQUE engorgement synaptique."""
    fake_reptile = MagicMock()
    fake_reptile.threat_memories = {
        "stale_dream": _make_threat_memory(4.0)
    }
    with patch.dict("sys.modules", {
        "core.reptilian_core": MagicMock(
            reptile=fake_reptile,
            STALE_DREAM_PATTERN="stale_dream",
        ),
    }), patch("core.autonomy_engine.bus.publish", new=MagicMock()), \
         patch("core.body_schema.gather_state", return_value=_empty_state()):
        result = await engine._execute_audit_survie()
    assert "Engorgement synaptique" in result["result"]
    assert "stale_dream" in result["result"]
    assert "sommeil d'urgence" in result["result"].lower()


@pytest.mark.asyncio
async def test_stale_dream_sous_seuil_pas_d_alerte(engine):
    """severity 3.5 → pas d'alerte engorgement (sous le seuil 4.0)."""
    fake_reptile = MagicMock()
    fake_reptile.threat_memories = {
        "stale_dream": _make_threat_memory(3.5)
    }
    with patch.dict("sys.modules", {
        "core.reptilian_core": MagicMock(
            reptile=fake_reptile, STALE_DREAM_PATTERN="stale_dream",
        ),
    }), patch("core.autonomy_engine.bus.publish", new=MagicMock()), \
         patch("core.body_schema.gather_state", return_value=_empty_state()):
        result = await engine._execute_audit_survie()
    assert "Engorgement synaptique" not in result["result"]
    assert "nominales" in result["result"].lower() or "aucune alerte" in result["result"].lower()


@pytest.mark.asyncio
async def test_pas_de_stale_dream_audit_classique(engine):
    """Pas de threat_memory stale_dream → comportement V14 préservé."""
    fake_reptile = MagicMock()
    fake_reptile.threat_memories = {}  # vide
    with patch.dict("sys.modules", {
        "core.reptilian_core": MagicMock(
            reptile=fake_reptile, STALE_DREAM_PATTERN="stale_dream",
        ),
    }), patch("core.autonomy_engine.bus.publish", new=MagicMock()), \
         patch("core.body_schema.gather_state", return_value=_empty_state()):
        result = await engine._execute_audit_survie()
    # Pas d'alerte ajoutée, audit "nominal"
    assert "Engorgement" not in result["result"]


# ─────────────────────────────────────────────────────────────────────────
# CYCLE 5 — dette de rêve directe (Body Schema)
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dette_haute_sans_stale_dream_genere_alerte(engine):
    """dream_dette_h=18h SANS stale_dream encore → alerte sur la dette."""
    fake_reptile = MagicMock()
    fake_reptile.threat_memories = {}
    with patch.dict("sys.modules", {
        "core.reptilian_core": MagicMock(
            reptile=fake_reptile, STALE_DREAM_PATTERN="stale_dream",
        ),
    }), patch("core.autonomy_engine.bus.publish", new=MagicMock()), \
         patch("core.body_schema.gather_state", return_value=_high_debt_state(18.0)):
        result = await engine._execute_audit_survie()
    assert "Dette de rêve" in result["result"]
    assert "18.0h" in result["result"]
    assert "MEMORY_CONSOLIDATION" in result["result"]


@pytest.mark.asyncio
async def test_dette_haute_ET_stale_dream_pas_de_double_alerte(engine):
    """dette=18h ET stale_dream sev=5 → 1 seule alerte (pas double)."""
    fake_reptile = MagicMock()
    fake_reptile.threat_memories = {
        "stale_dream": _make_threat_memory(5.0)
    }
    with patch.dict("sys.modules", {
        "core.reptilian_core": MagicMock(
            reptile=fake_reptile, STALE_DREAM_PATTERN="stale_dream",
        ),
    }), patch("core.autonomy_engine.bus.publish", new=MagicMock()), \
         patch("core.body_schema.gather_state", return_value=_high_debt_state(18.0)):
        result = await engine._execute_audit_survie()
    assert "Engorgement synaptique" in result["result"]
    # Pas de double-alerte sur la dette
    assert "Dette de rêve" not in result["result"]


@pytest.mark.asyncio
async def test_dette_sous_seuil_pas_d_alerte(engine):
    """dette 8h (sous seuil 12) → pas d'alerte sur la dette."""
    fake_reptile = MagicMock()
    fake_reptile.threat_memories = {}
    with patch.dict("sys.modules", {
        "core.reptilian_core": MagicMock(
            reptile=fake_reptile, STALE_DREAM_PATTERN="stale_dream",
        ),
    }), patch("core.autonomy_engine.bus.publish", new=MagicMock()), \
         patch("core.body_schema.gather_state", return_value={"synaptic": {"dream_dette_h": 8.0}}):
        result = await engine._execute_audit_survie()
    assert "Dette de rêve" not in result["result"]


# ─────────────────────────────────────────────────────────────────────────
# Robustesse : modules indisponibles
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_amygdale_indisponible_no_crash(engine):
    """Si l'import reptilian_core échoue, l'audit continue sans crash."""
    with patch.dict("sys.modules", {"core.reptilian_core": None}), \
         patch("core.autonomy_engine.bus.publish", new=MagicMock()), \
         patch("core.body_schema.gather_state", return_value=_empty_state()):
        result = await engine._execute_audit_survie()
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_body_schema_indisponible_no_crash(engine):
    """Si l'import body_schema échoue, l'audit continue sans crash."""
    fake_reptile = MagicMock()
    fake_reptile.threat_memories = {}
    with patch.dict("sys.modules", {
        "core.reptilian_core": MagicMock(
            reptile=fake_reptile, STALE_DREAM_PATTERN="stale_dream",
        ),
    }), patch("core.autonomy_engine.bus.publish", new=MagicMock()), \
         patch("core.body_schema.gather_state", side_effect=Exception("boom")):
        result = await engine._execute_audit_survie()
    assert result["status"] == "success"


# ─────────────────────────────────────────────────────────────────────────
# L'audit reste discursif : pas de préemption
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_n_active_pas_de_preemption(engine):
    """V14.6 reste discursif : l'audit ne touche PAS à _forced_next_intent."""
    engine._forced_next_intent = ""  # init
    fake_reptile = MagicMock()
    fake_reptile.threat_memories = {
        "stale_dream": _make_threat_memory(7.0)  # bien au-dessus du seuil
    }
    with patch.dict("sys.modules", {
        "core.reptilian_core": MagicMock(
            reptile=fake_reptile, STALE_DREAM_PATTERN="stale_dream",
        ),
    }), patch("core.autonomy_engine.bus.publish", new=MagicMock()), \
         patch("core.body_schema.gather_state", return_value=_high_debt_state(20.0)):
        await engine._execute_audit_survie()
    # L'audit alerte mais ne préempte PAS — c'est le rôle du Pilier 3 (V14.4)
    assert engine._forced_next_intent == ""


# ─────────────────────────────────────────────────────────────────────────
# Constantes V14.6 disponibles
# ─────────────────────────────────────────────────────────────────────────

def test_constantes_v146_publiques():
    """Les seuils V14.6 doivent être exposés au niveau module."""
    assert AUDIT_SURVIE_STALE_DREAM_SEVERITY_ALERT == 4.0
    assert AUDIT_SURVIE_DREAM_DETTE_ALERT_H == 12.0
    # Cohérence : le seuil audit doit être STRICTEMENT INFÉRIEUR au seuil
    # REPTILIAN_ALERT pour que l'audit voie le danger AVANT que le réflexe se déclenche.
    from core.reptilian_core import STALE_DREAM_ALERT_THRESHOLD
    assert AUDIT_SURVIE_STALE_DREAM_SEVERITY_ALERT < STALE_DREAM_ALERT_THRESHOLD
