"""V14.4 — Pilier 3 nocicepteurs : tests de la préemption MEMORY_CONSOLIDATION
sur REPTILIAN_ALERT pattern=stale_dream.

Garde-fous testés :
  1. Pattern doit être stale_dream
  2. Pas de coffee_mode (interaction humaine inviolable)
  3. Pas de nap (consolidation déjà en cours)
  4. Cooldown 5 min anti-boucle

V14.10 — Interruption matérielle logicielle (asyncio.Event _urgent_wakeup).
Tests vérifient que l'event est SET ssi la préemption a réussi (pas en cas
de garde-fou bloquant). Validation in-vivo : latence cascade nociceptive
24min47s → <5s post-V14.10 (cible de validation chaos engineering).
"""

import asyncio
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
    # V14.10 — Event pour réveil instantané du main loop
    inst._urgent_wakeup = asyncio.Event()
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


# ═══════════════════════════════════════════════════════════════════════
# V14.10 — Interruption matérielle logicielle (asyncio.Event)
# ═══════════════════════════════════════════════════════════════════════

class TestV1410UrgentWakeup:
    """V14.10 — l'asyncio.Event _urgent_wakeup doit être set IFF la
    préemption a effectivement eu lieu. Validation in-vivo : latence
    cascade nociceptive 24min47s → <5s post-V14.10."""

    @pytest.mark.asyncio
    async def test_urgent_wakeup_set_apres_preemption_reussie(self, engine):
        """Préemption réussie → _urgent_wakeup.is_set() == True."""
        assert not engine._urgent_wakeup.is_set()
        await engine._on_reptilian_alert(_alert())
        assert engine._forced_next_intent == "MEMORY_CONSOLIDATION"
        assert engine._urgent_wakeup.is_set(), \
            "Event MUST be set après armement REFLEXE PURGE"

    @pytest.mark.asyncio
    async def test_urgent_wakeup_pas_set_si_pattern_different(self, engine):
        """Garde-fou pattern → préemption pas faite → event PAS set."""
        await engine._on_reptilian_alert(_alert(pattern="ollama"))
        assert not engine._urgent_wakeup.is_set()

    @pytest.mark.asyncio
    async def test_urgent_wakeup_pas_set_si_coffee_mode(self, engine):
        """Garde-fou coffee_mode → préemption refusée → event PAS set."""
        engine.is_coffee_mode = True
        await engine._on_reptilian_alert(_alert())
        assert not engine._urgent_wakeup.is_set()

    @pytest.mark.asyncio
    async def test_urgent_wakeup_pas_set_si_napping(self, engine):
        """Garde-fou nap → consolidation déjà active → event PAS set."""
        engine.is_napping = True
        await engine._on_reptilian_alert(_alert())
        assert not engine._urgent_wakeup.is_set()

    @pytest.mark.asyncio
    async def test_urgent_wakeup_pas_set_si_cooldown(self, engine):
        """Garde-fou cooldown → 2e alerte refusée → event PAS set la 2e fois."""
        await engine._on_reptilian_alert(_alert())
        # Reset event + forced (simule consume)
        engine._urgent_wakeup.clear()
        engine._forced_next_intent = ""
        # Re-alerte immédiate (sous cooldown)
        await engine._on_reptilian_alert(_alert())
        assert not engine._urgent_wakeup.is_set(), \
            "Cooldown doit empêcher le réveil dupliqué"

    @pytest.mark.asyncio
    async def test_urgent_wakeup_pas_set_si_intent_deja_force(self, engine):
        """_forced_next_intent déjà set par autre voie → branche ELSE → pas de set."""
        engine._forced_next_intent = "AUDIT_STRUCTURE"
        await engine._on_reptilian_alert(_alert())
        # La force existante est conservée, pas de réveil urgent
        assert engine._forced_next_intent == "AUDIT_STRUCTURE"
        assert not engine._urgent_wakeup.is_set()

    @pytest.mark.asyncio
    async def test_urgent_wakeup_interrompt_sleep_simule(self, engine):
        """Simule la boucle de sleep V14.10 : event set pendant l'attente
        doit déclencher un break en moins de 100ms (vs sleep_time complet)."""
        sleep_time = 30  # simule un cycle de 30s
        chunk = 15

        async def sleep_loop_simule():
            """Simule la nouvelle boucle V14.10 lignes 7065-7088."""
            remaining = sleep_time
            broken = False
            while remaining > 0:
                c = min(remaining, chunk)
                try:
                    await asyncio.wait_for(engine._urgent_wakeup.wait(), timeout=c)
                    engine._urgent_wakeup.clear()
                    broken = True
                    break
                except asyncio.TimeoutError:
                    remaining -= c
            return broken

        async def trigger_alert_apres_50ms():
            await asyncio.sleep(0.05)
            await engine._on_reptilian_alert(_alert())

        start = time.monotonic()
        # Lance les 2 coroutines en parallèle
        broken, _ = await asyncio.gather(sleep_loop_simule(), trigger_alert_apres_50ms())
        elapsed = time.monotonic() - start

        assert broken, "La boucle doit être interrompue par l'event"
        assert elapsed < 1.0, \
            f"Latence d'interruption trop élevée : {elapsed*1000:.0f}ms (cible <100ms)"

    @pytest.mark.asyncio
    async def test_event_clear_ne_casse_pas_re_arming(self, engine):
        """Après clear() puis re-arming, l'event doit pouvoir re-set."""
        # 1ère préemption
        await engine._on_reptilian_alert(_alert())
        assert engine._urgent_wakeup.is_set()
        # Simule consume par main loop
        engine._urgent_wakeup.clear()
        engine._forced_next_intent = ""
        # Cooldown forcé expiré
        engine._stale_dream_preemption_last = time.time() - STALE_DREAM_PREEMPTION_COOLDOWN_S - 10
        # 2ème préemption
        await engine._on_reptilian_alert(_alert())
        assert engine._urgent_wakeup.is_set(), \
            "Event doit pouvoir être re-set après clear()"


# ═══════════════════════════════════════════════════════════════════════
# Option B — Pattern synaptic_congestion accepté par REFLEXE PURGE
# ═══════════════════════════════════════════════════════════════════════

class TestSynapticCongestionPattern:
    """Option B — l'autonomy doit traiter pattern=synaptic_congestion comme
    pattern=stale_dream : mêmes garde-fous, même MEMORY_CONSOLIDATION forcée,
    même réveil V14.10. La douleur de DENSITÉ est aussi urgente que la
    douleur de TEMPS."""

    @pytest.mark.asyncio
    async def test_pattern_synaptic_congestion_declenche_preemption(self, engine):
        """Pattern=synaptic_congestion → MEMORY_CONSOLIDATION forcée."""
        event = {
            "pattern": "synaptic_congestion",
            "severity": 6.0,
            "zscore": 3.0,
            "pending_episodes": 30,
            "conditioned_reflex": "ADRENALINE",
            "source": "synaptic_congestion",
            "timestamp": time.time(),
        }
        await engine._on_reptilian_alert(event)
        assert engine._forced_next_intent == "MEMORY_CONSOLIDATION"
        assert engine._urgent_wakeup.is_set(), \
            "V14.10 _urgent_wakeup doit être set pour synaptic_congestion aussi"

    @pytest.mark.asyncio
    async def test_pattern_inconnu_toujours_rejete(self, engine):
        """Garde-fou : pattern non whitelisté → no-op (sécurité contre nouveaux patterns)."""
        event = {
            "pattern": "ollama",  # ni stale_dream ni synaptic_congestion
            "severity": 9.0,
        }
        await engine._on_reptilian_alert(event)
        assert engine._forced_next_intent == ""
        assert not engine._urgent_wakeup.is_set()

    @pytest.mark.asyncio
    async def test_coffee_mode_bloque_aussi_synaptic_congestion(self, engine):
        """Garde-fou coffee_mode s'applique aux 2 patterns."""
        engine.is_coffee_mode = True
        event = {
            "pattern": "synaptic_congestion",
            "severity": 6.0,
            "pending_episodes": 30,
        }
        await engine._on_reptilian_alert(event)
        assert engine._forced_next_intent == ""
        assert not engine._urgent_wakeup.is_set()
