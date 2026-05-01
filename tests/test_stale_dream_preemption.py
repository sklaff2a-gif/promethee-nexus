"""V14.4 — Pilier 3 nocicepteurs : tests de la préemption MEMORY_CONSOLIDATION
sur REPTILIAN_ALERT pattern=stale_dream.

Garde-fous testés :
  1. Pattern doit être stale_dream
  2. Pas de coffee_mode (interaction humaine inviolable)
  3. Pas de nap (consolidation déjà en cours)
  4. Cooldown 5 min anti-boucle
"""

import time
from unittest.mock import patch

import pytest

from core.autonomy_engine import (
    AutonomyEngine,
    STALE_DREAM_PREEMPTION_COOLDOWN_S,
)


@pytest.fixture
def engine():
    """Instance bare-bones (object.__new__) — _on_reptilian_alert ne dépend
    que de quelques attributs, pas du __init__ massif.
    """
    inst = object.__new__(AutonomyEngine)
    inst._forced_next_intent = ""
    inst._stale_dream_preemption_last = 0.0
    inst.is_coffee_mode = False
    inst.is_napping = False
    yield inst


def _alert(pattern: str = "stale_dream", severity: float = 6.0, dette_h: float = 24.0):
    return {
        "pattern": pattern,
        "severity": severity,
        "zscore": severity / 2.0,
        "dream_dette_h": dette_h,
        "conditioned_reflex": "ADRENALINE",
        "source": "synaptic_debt",
        "timestamp": time.time(),
    }


# ─────────────────────────────────────────────────────────────────────────
# Cas nominal : préemption se fait
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preemption_se_fait_si_garde_fous_ok(engine):
    """Tous garde-fous OK → forced_next_intent = MEMORY_CONSOLIDATION."""
    before_ts = time.time()
    await engine._on_reptilian_alert(_alert())
    assert engine._forced_next_intent == "MEMORY_CONSOLIDATION"
    assert engine._stale_dream_preemption_last >= before_ts


# ─────────────────────────────────────────────────────────────────────────
# Garde-fou 1 : pattern doit matcher
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pattern_different_no_op(engine):
    """Autre pattern (ex: ollama, cpu) → aucun effet."""
    await engine._on_reptilian_alert(_alert(pattern="ollama"))
    assert engine._forced_next_intent == ""
    assert engine._stale_dream_preemption_last == 0.0


@pytest.mark.asyncio
async def test_pattern_absent_no_op(engine):
    """Event sans clé pattern → no-op safe."""
    await engine._on_reptilian_alert({})
    assert engine._forced_next_intent == ""


# ─────────────────────────────────────────────────────────────────────────
# Garde-fou 2 : interaction humaine inviolable
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_coffee_mode_bloque_preemption(engine):
    """Si Alfred parle, on serre les dents — pas de préemption."""
    engine.is_coffee_mode = True
    await engine._on_reptilian_alert(_alert())
    assert engine._forced_next_intent == ""
    assert engine._stale_dream_preemption_last == 0.0


# ─────────────────────────────────────────────────────────────────────────
# Garde-fou 3 : nap en cours = consolidation déjà active
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nap_bloque_preemption(engine):
    """Le sommeil fait déjà la consolidation — préempter serait redondant."""
    engine.is_napping = True
    await engine._on_reptilian_alert(_alert())
    assert engine._forced_next_intent == ""
    assert engine._stale_dream_preemption_last == 0.0


# ─────────────────────────────────────────────────────────────────────────
# Garde-fou 4 : cooldown anti-boucle
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cooldown_bloque_preemption_consecutive(engine):
    """Deux alertes en moins de 5 min → 1 seule préemption."""
    await engine._on_reptilian_alert(_alert())
    assert engine._forced_next_intent == "MEMORY_CONSOLIDATION"
    # Reset _forced_next_intent comme si le scheduler l'avait consommé
    engine._forced_next_intent = ""
    # Re-alerte immédiate
    await engine._on_reptilian_alert(_alert())
    assert engine._forced_next_intent == "", \
        "Cooldown doit empêcher la 2e préemption immédiate"


@pytest.mark.asyncio
async def test_cooldown_expire_libere_preemption(engine):
    """Après cooldown expiré, la préemption se fait à nouveau."""
    # Première préemption
    await engine._on_reptilian_alert(_alert())
    engine._forced_next_intent = ""  # consommé par le scheduler
    # Simule passage du cooldown
    engine._stale_dream_preemption_last = time.time() - STALE_DREAM_PREEMPTION_COOLDOWN_S - 10
    # Deuxième alerte
    await engine._on_reptilian_alert(_alert())
    assert engine._forced_next_intent == "MEMORY_CONSOLIDATION"


# ─────────────────────────────────────────────────────────────────────────
# Interaction avec _forced_next_intent existant
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_n_ecrase_pas_un_forced_existant(engine):
    """Si une autre préemption est déjà programmée, on ne l'écrase pas."""
    engine._forced_next_intent = "AUDIT_STRUCTURE"  # autre force prioritaire
    await engine._on_reptilian_alert(_alert())
    # La force existante est conservée
    assert engine._forced_next_intent == "AUDIT_STRUCTURE"
    # Mais pas de cooldown enregistré (pas vraiment fait de préemption)
    assert engine._stale_dream_preemption_last == 0.0


# ─────────────────────────────────────────────────────────────────────────
# Robustesse aux events malformés
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_minimal_avec_pattern_seul(engine):
    """Event qui contient juste le pattern (severity/dette absents) ne crashe pas."""
    await engine._on_reptilian_alert({"pattern": "stale_dream"})
    # Préemption se fait avec severity et dette par défaut (None/0)
    assert engine._forced_next_intent == "MEMORY_CONSOLIDATION"


@pytest.mark.asyncio
async def test_severity_none_ne_crashe_pas(engine):
    """severity=None doit être géré sans crash."""
    event = _alert()
    event["severity"] = None
    await engine._on_reptilian_alert(event)
    assert engine._forced_next_intent == "MEMORY_CONSOLIDATION"
