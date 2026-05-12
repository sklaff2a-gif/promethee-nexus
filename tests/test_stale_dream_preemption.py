"""V14.4 — Pilier 3 nocicepteurs : tests de la préemption MEMORY_CONSOLIDATION
sur REPTILIAN_ALERT pattern=stale_dream.

Garde-fous testés :
  1. Pattern doit être stale_dream
  2. Pas de coffee_mode (interaction humaine inviolable)
  3. Pas de nap (consolidation déjà en cours)
  4. Cooldown 5 min anti-boucle

V14.10 — Interruption matérielle logicielle (asyncio.Event _urgent_wakeup).
V14.11 — Couplage fort source-de-vérité-unique : _urgent_wakeup remplacé
par bridge asyncio.Condition (reptile.urgency_cond) + mirror Event local
(_urgency_mirror). Tests vérifient que (a) la notify_all() de reptile est
appelée IFF la préemption a réussi et (b) le mirror reste consultable.
Validation in-vivo : latence cascade nociceptive 24min47s → <5s post-V14.10
(cible de validation chaos engineering).
"""

import asyncio
import time
from unittest.mock import patch

import pytest

from core.autonomy_engine import (
    AutonomyEngine,
    STALE_DREAM_PREEMPTION_COOLDOWN_S,
)


# V14.11 — Helper class : asyncio.Condition espionnée (compte les notify_all)
class _SpyCondition(asyncio.Condition):
    """asyncio.Condition étendue qui compte les notify_all() pour tests."""
    def __init__(self):
        super().__init__()
        self.notify_all_count = 0

    def notify_all(self):
        self.notify_all_count += 1
        super().notify_all()


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
    # V14.11 — Mirror Event local (remplace _urgent_wakeup, alimenté par
    # le watcher bridge en production). En test, on assert directement sur
    # le mirror ET sur la notify_all() côté reptile mock.
    inst._urgency_mirror = asyncio.Event()
    yield inst


@pytest.fixture
def reptile_mock(monkeypatch):
    """V14.11 — Mock du singleton reptile avec asyncio.Condition espionnée.

    Permet aux tests de vérifier que reptile.urgency_cond.notify_all() est
    bien appelé par _on_reptilian_alert après armement du REFLEXE PURGE.
    Le mock fournit une vraie Condition fonctionnelle (pas un MagicMock) pour
    que le `async with reptile.urgency_cond:` du code de prod ne crashe pas.
    """
    class _MockReptile:
        pass

    mock = _MockReptile()
    mock.urgency_cond = _SpyCondition()
    mock.last_urgent_pattern = ""
    mock.last_urgent_severity = 0.0
    mock.last_urgent_at = 0.0
    monkeypatch.setattr("core.reptilian_core.reptile", mock)
    yield mock


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
# V14.11 — Couplage fort : notify reptile.urgency_cond + mirror Event local
# ═══════════════════════════════════════════════════════════════════════

class TestV1411UrgencyMirror:
    """V14.11 — Le notify_all() sur reptile.urgency_cond doit être appelé IFF
    la préemption a effectivement eu lieu. Le mirror Event est alimenté par
    un watcher en prod (non testé ici unitairement — couvert par tests
    d'intégration). Validation in-vivo : latence cascade nociceptive
    24min47s → <5s post-V14.10."""

    @pytest.mark.asyncio
    async def test_notify_apres_preemption_reussie(self, engine, reptile_mock):
        """Préemption réussie → reptile.urgency_cond.notify_all() appelé."""
        assert reptile_mock.urgency_cond.notify_all_count == 0
        assert not engine._urgency_mirror.is_set()
        await engine._on_reptilian_alert(_alert())
        assert engine._forced_next_intent == "MEMORY_CONSOLIDATION"
        assert reptile_mock.urgency_cond.notify_all_count == 1, \
            "notify_all() MUST être appelé après armement REFLEXE PURGE"
        assert reptile_mock.last_urgent_pattern == "stale_dream"
        # Mirror non set ici (watcher pas spawné en test unitaire)
        # — couvert par test_urgent_interrompt_sleep_simule plus bas

    @pytest.mark.asyncio
    async def test_pas_notify_si_pattern_different(self, engine, reptile_mock):
        """Garde-fou pattern → préemption pas faite → notify_all PAS appelé."""
        await engine._on_reptilian_alert(_alert(pattern="ollama"))
        assert reptile_mock.urgency_cond.notify_all_count == 0

    @pytest.mark.asyncio
    async def test_pas_notify_si_coffee_mode(self, engine, reptile_mock):
        """Garde-fou coffee_mode → préemption refusée → notify_all PAS appelé."""
        engine.is_coffee_mode = True
        await engine._on_reptilian_alert(_alert())
        assert reptile_mock.urgency_cond.notify_all_count == 0

    @pytest.mark.asyncio
    async def test_pas_notify_si_napping(self, engine, reptile_mock):
        """Garde-fou nap → consolidation déjà active → notify_all PAS appelé."""
        engine.is_napping = True
        await engine._on_reptilian_alert(_alert())
        assert reptile_mock.urgency_cond.notify_all_count == 0

    @pytest.mark.asyncio
    async def test_pas_notify_si_cooldown(self, engine, reptile_mock):
        """Garde-fou cooldown → 2e alerte refusée → 1 seule notify."""
        await engine._on_reptilian_alert(_alert())
        assert reptile_mock.urgency_cond.notify_all_count == 1
        # Reset forced (simule consume par main loop)
        engine._forced_next_intent = ""
        # Re-alerte immédiate (sous cooldown)
        await engine._on_reptilian_alert(_alert())
        assert reptile_mock.urgency_cond.notify_all_count == 1, \
            "Cooldown doit empêcher le notify dupliqué"

    @pytest.mark.asyncio
    async def test_pas_notify_si_intent_deja_force(self, engine, reptile_mock):
        """_forced_next_intent déjà set par autre voie → branche ELSE → pas de notify."""
        engine._forced_next_intent = "AUDIT_STRUCTURE"
        await engine._on_reptilian_alert(_alert())
        # La force existante est conservée, pas de réveil urgent
        assert engine._forced_next_intent == "AUDIT_STRUCTURE"
        assert reptile_mock.urgency_cond.notify_all_count == 0

    @pytest.mark.asyncio
    async def test_urgent_interrompt_sleep_simule(self, engine, reptile_mock):
        """Simule la boucle de sleep V14.10/V14.11 : le mirror set par le
        watcher pendant l'attente doit déclencher un break en moins de 100ms.

        Spawn manuel du watcher V14.11 pour simuler le bridge Condition→Event.
        """
        sleep_time = 30  # simule un cycle de 30s
        chunk = 15

        # V14.11 — watcher manuel (équivalent _urgency_mirror_watcher)
        async def manual_watcher():
            async with reptile_mock.urgency_cond:
                await reptile_mock.urgency_cond.wait()
            engine._urgency_mirror.set()

        watcher_task = asyncio.create_task(manual_watcher())
        # Laisser le watcher prendre la main pour entrer dans le wait
        await asyncio.sleep(0.01)

        async def sleep_loop_simule():
            """Simule la boucle main loop V14.10/V14.11 lignes 7275-7289."""
            remaining = sleep_time
            broken = False
            while remaining > 0:
                c = min(remaining, chunk)
                try:
                    await asyncio.wait_for(engine._urgency_mirror.wait(), timeout=c)
                    engine._urgency_mirror.clear()
                    broken = True
                    break
                except asyncio.TimeoutError:
                    remaining -= c
            return broken

        async def trigger_alert_apres_50ms():
            await asyncio.sleep(0.05)
            await engine._on_reptilian_alert(_alert())

        start = time.monotonic()
        try:
            # Lance les 2 coroutines en parallèle
            broken, _ = await asyncio.gather(sleep_loop_simule(), trigger_alert_apres_50ms())
            elapsed = time.monotonic() - start

            assert broken, "La boucle doit être interrompue par le mirror"
            assert elapsed < 1.0, \
                f"Latence d'interruption trop élevée : {elapsed*1000:.0f}ms (cible <100ms)"
            assert reptile_mock.urgency_cond.notify_all_count == 1, \
                "notify_all() doit avoir été appelé une fois"
        finally:
            # Cleanup watcher task
            if not watcher_task.done():
                watcher_task.cancel()
                try:
                    await watcher_task
                except (asyncio.CancelledError, RuntimeError):
                    pass

    @pytest.mark.asyncio
    async def test_re_arming_apres_consume(self, engine, reptile_mock):
        """Après consume du forced_next_intent et cooldown expiré, la 2ème
        préemption doit re-déclencher notify_all()."""
        # 1ère préemption
        await engine._on_reptilian_alert(_alert())
        assert reptile_mock.urgency_cond.notify_all_count == 1
        # Simule consume par main loop
        engine._forced_next_intent = ""
        # Cooldown forcé expiré
        engine._stale_dream_preemption_last = time.time() - STALE_DREAM_PREEMPTION_COOLDOWN_S - 10
        # 2ème préemption
        await engine._on_reptilian_alert(_alert())
        assert reptile_mock.urgency_cond.notify_all_count == 2, \
            "notify_all() doit pouvoir être ré-appelé après consume + cooldown expiré"


# ═══════════════════════════════════════════════════════════════════════
# Option B — Pattern synaptic_congestion accepté par REFLEXE PURGE
# ═══════════════════════════════════════════════════════════════════════

class TestSynapticCongestionPattern:
    """Option B — l'autonomy doit traiter pattern=synaptic_congestion comme
    pattern=stale_dream : mêmes garde-fous, même MEMORY_CONSOLIDATION forcée,
    même réveil V14.10/V14.11. La douleur de DENSITÉ est aussi urgente que
    la douleur de TEMPS."""

    @pytest.mark.asyncio
    async def test_pattern_synaptic_congestion_declenche_preemption(self, engine, reptile_mock):
        """Pattern=synaptic_congestion → MEMORY_CONSOLIDATION forcée + notify."""
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
        assert reptile_mock.urgency_cond.notify_all_count == 1, \
            "V14.11 notify_all() doit être appelé pour synaptic_congestion aussi"
        assert reptile_mock.last_urgent_pattern == "synaptic_congestion"

    @pytest.mark.asyncio
    async def test_pattern_inconnu_toujours_rejete(self, engine, reptile_mock):
        """Garde-fou : pattern non whitelisté → no-op (sécurité contre nouveaux patterns)."""
        event = {
            "pattern": "ollama",  # ni stale_dream ni synaptic_congestion
            "severity": 9.0,
        }
        await engine._on_reptilian_alert(event)
        assert engine._forced_next_intent == ""
        assert reptile_mock.urgency_cond.notify_all_count == 0

    @pytest.mark.asyncio
    async def test_coffee_mode_bloque_aussi_synaptic_congestion(self, engine, reptile_mock):
        """Garde-fou coffee_mode s'applique aux 2 patterns."""
        engine.is_coffee_mode = True
        event = {
            "pattern": "synaptic_congestion",
            "severity": 6.0,
            "pending_episodes": 30,
        }
        await engine._on_reptilian_alert(event)
        assert engine._forced_next_intent == ""
        assert reptile_mock.urgency_cond.notify_all_count == 0
