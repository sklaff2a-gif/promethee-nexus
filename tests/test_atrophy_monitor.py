"""Tests pour core/atrophy_monitor.py.

Spec atelier audace 27/05 (Option 2 — Détecteur de Bruit), refondue
29/05 v3 sur détection de compulsion V34 (cf doctrine "reward hacking"
dans config.py — la valeur instantanée de STAB.priv ne pouvait jamais
rester au-dessus de 80 pendant 45 min car V34 reset toutes les 13 min).

Couverture (9 cas) :
1. Pas d'alarme si peu de V34_RELIEF (< seuil)
2. Pas d'alarme si CROISSANCE active (garde-fou constitutionnel)
3. Alarme si compulsion V34 + CROISSANCE en coma
4. Purge des reliefs > fenêtre 24h
5. Seuls les reliefs STABILITE sont comptés (CURIOSITE ignorée)
6. DRY_RUN — alarme calculée mais pas de bus.publish
7. Coupe-circuit Jaccard : rumination détectée → cancel
8. Coupe-circuit Jaccard : nodes diversifiés → alarme persiste
9. Sanity Jaccard helper
"""

import time
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from core.atrophy_monitor import AtrophyMonitor, _jaccard


# ---------- Fixtures ----------


@pytest.fixture(autouse=True)
def isolate_monitor(tmp_path, monkeypatch):
    """Reset singleton + redirect log path vers un fichier temporaire."""
    AtrophyMonitor.reset_singleton()
    log_tmp = tmp_path / "atrophy.jsonl"
    monkeypatch.setattr("core.atrophy_monitor.ATROPHY_LOG_PATH", str(log_tmp))
    yield log_tmp
    AtrophyMonitor.reset_singleton()


@pytest.fixture
def config_default(monkeypatch):
    """Config par défaut (mode OBSERVE = DRY_RUN True) avec seuils V34
    de la refonte 29/05 v3."""
    from config import Config
    monkeypatch.setattr(Config, "ATROPHY_ENABLED", True, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_DRY_RUN", True, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_V34_RELIEF_WINDOW_S", 86400, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_V34_RELIEF_THRESHOLD", 4, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_CROISSANCE_MECHANICAL_ENTRY_THRESHOLD", 10.0, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_ALARM_DURATION_S", 600, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_JACCARD_WINDOW", 5, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_JACCARD_REDUNDANT_THRESHOLD", 0.7, raising=False)


def _mock_drives(crois_dep: float):
    """Construit un mock pour desire_engine.drives (seul CROISSANCE compte v3)."""
    drives = {
        "CROISSANCE": MagicMock(deprivation=crois_dep),
    }
    return MagicMock(drives=drives)


async def _emit_v34_relief(monitor: AtrophyMonitor, drive: str = "STABILITE"):
    """Helper : simule un event V34_RELIEF_APPLIED via le handler direct."""
    await monitor._on_v34_relief({"drive": drive, "quality": 0.8, "delta": -12.0})


# ---------- Tests — Détection par fréquence ----------


@pytest.mark.asyncio
async def test_no_alarm_when_few_reliefs(config_default, isolate_monitor):
    """Cas 1 : 2 V34_RELIEF (< seuil 4) → pas d'alarme."""
    monitor = AtrophyMonitor()
    for _ in range(2):
        await _emit_v34_relief(monitor)
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(crois_dep=2.0)
    )}):
        await monitor.check_balance()
    assert not monitor.is_alarm_active()
    assert monitor.get_stats()["alarms_published"] == 0
    assert monitor.get_stats()["v34_reliefs_observed"] == 2


@pytest.mark.asyncio
async def test_no_alarm_when_croissance_active(config_default, isolate_monitor):
    """Cas 2 — GARDE-FOU CONSTITUTIONNEL : 5 V34_RELIEF (> seuil) MAIS
    CROISSANCE.priv=20 (vraie urgence, pas atrophie) → pas d'alarme.

    Garantit qu'une vraie crise legitime ne sera pas etouffee par notre
    bouclier d'audace : si la pulsion de croissance est encore active
    (priv >= 10), les V34 sont legitimes, pas compulsifs.
    """
    monitor = AtrophyMonitor()
    for _ in range(5):
        await _emit_v34_relief(monitor)
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(crois_dep=20.0)
    )}):
        await monitor.check_balance()
    assert not monitor.is_alarm_active()
    assert monitor.get_stats()["alarms_published"] == 0


@pytest.mark.asyncio
async def test_alarm_when_compulsion_and_dead_growth(config_default, isolate_monitor):
    """Cas 3 : 5 V34_RELIEF (> seuil 4) + CROISSANCE.priv=2 (coma) → alarme."""
    monitor = AtrophyMonitor()
    for _ in range(5):
        await _emit_v34_relief(monitor)
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(crois_dep=2.0)
    )}):
        await monitor.check_balance()
    assert monitor.is_alarm_active()
    assert monitor.get_stats()["alarms_published"] == 1


@pytest.mark.asyncio
async def test_v34_relief_window_purge(config_default, isolate_monitor):
    """Cas 4 : un relief vieux de > 24h est purge automatiquement."""
    monitor = AtrophyMonitor()
    # Injecter directement un timestamp ancien dans le deque
    now = time.time()
    monitor._v34_relief_history.append(now - 90000)  # > 24h
    # Et 2 reliefs récents
    monitor._v34_relief_history.append(now - 100)
    monitor._v34_relief_history.append(now - 50)
    assert len(monitor._v34_relief_history) == 3

    # Le check_balance doit purger les vieux
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(crois_dep=2.0)
    )}):
        await monitor.check_balance()
    # Apres purge : seuls les 2 recents restent
    assert len(monitor._v34_relief_history) == 2
    assert monitor.get_stats()["v34_reliefs_purged"] == 1
    # Pas d'alarme (2 reliefs, < seuil)
    assert not monitor.is_alarm_active()


@pytest.mark.asyncio
async def test_only_stabilite_reliefs_counted(config_default, isolate_monitor):
    """Cas 5 : reliefs sur CURIOSITE/MAITRISE/etc. ne sont pas comptes."""
    monitor = AtrophyMonitor()
    # 5 reliefs sur des drives non-STABILITE
    for drive in ("CURIOSITE", "MAITRISE", "CONNEXION", "CREATION", "COMPREHENSION"):
        await _emit_v34_relief(monitor, drive=drive)
    assert len(monitor._v34_relief_history) == 0
    assert monitor.get_stats()["v34_reliefs_observed"] == 0


@pytest.mark.asyncio
async def test_dry_run_does_not_publish_bus(config_default, isolate_monitor):
    """Cas 6 : DRY_RUN=True → alarme + log, mais pas de bus.publish."""
    monitor = AtrophyMonitor()
    for _ in range(5):
        await _emit_v34_relief(monitor)
    mock_bus = MagicMock()
    mock_bus.publish = AsyncMock()
    with patch.dict("sys.modules", {
        "core.desire_engine": MagicMock(desire_engine=_mock_drives(crois_dep=2.0)),
        "core.event_bus.bus": MagicMock(bus=mock_bus),
    }):
        await monitor.check_balance()
    assert monitor.is_alarm_active()
    assert monitor.get_stats()["would_boost_dry_run"] == 1
    mock_bus.publish.assert_not_called()


# ---------- Tests — Coupe-circuit Jaccard (inchanges) ----------


@pytest.mark.asyncio
async def test_jaccard_rumination_cancels_alarm(config_default, isolate_monitor):
    """Cas 7 : alarme active + 5 nodes identiques dans 2 fenêtres → cancel."""
    monitor = AtrophyMonitor()
    # Forcer alarme active
    monitor._alarm_active = True
    monitor._alarm_started_at = time.time()

    same_nodes = ["concept_X", "concept_Y", "concept_Z", "concept_W", "concept_V"]
    for nid in same_nodes:
        await monitor._on_synaptic_update({"change": "node_new", "id": nid})
    for nid in same_nodes:
        await monitor._on_synaptic_update({"change": "node_new", "id": nid})

    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(crois_dep=2.0)
    )}):
        await monitor.check_balance()
    assert not monitor._alarm_active
    assert monitor.get_stats()["alarms_cancelled_rumination"] >= 1


@pytest.mark.asyncio
async def test_jaccard_diverse_keeps_alarm(config_default, isolate_monitor):
    """Cas 8 : alarme active + nodes diversifiés → alarme persiste."""
    monitor = AtrophyMonitor()
    monitor._alarm_active = True
    monitor._alarm_started_at = time.time()

    for i in range(5):
        await monitor._on_synaptic_update({"change": "node_new", "id": f"old_{i}"})
    for i in range(5):
        await monitor._on_synaptic_update({"change": "node_new", "id": f"new_{i}"})

    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(crois_dep=2.0)
    )}):
        await monitor.check_balance()
    assert monitor._alarm_active
    assert monitor.get_stats()["alarms_cancelled_rumination"] == 0


def test_jaccard_helper():
    """Sanity-check de la fonction _jaccard."""
    assert _jaccard(set(), set()) == 0.0
    assert _jaccard({"a"}, {"a"}) == 1.0
    assert _jaccard({"a", "b"}, {"a", "c"}) == pytest.approx(1.0 / 3.0)
    assert _jaccard({"a", "b", "c"}, {"d", "e", "f"}) == 0.0
