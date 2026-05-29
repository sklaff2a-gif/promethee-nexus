"""Tests pour core/atrophy_monitor.py.

Spec atelier audace 27/05 (Option 2 — Détecteur de Bruit), recalibrée
29/05 avec Schmitt trigger + timer de stase (cf doctrine "junk food
cognitive" dans config.py).

Couverture :
1. Pas de stase si STAB <= 80 (entrée)
2. Pas de stase si CROIS >= 10 (entrée)
3. Stase détectée si STAB > 80 ET CROIS < 10 (entrée stricte)
4. Pas d'alarme avant STASIS_DURATION_S (45 min)
5. Alarme publiée si stase soutenue >= 45 min
6. DRY_RUN — alarme calculée mais pas de bus.publish
7. Hystérésis : micro-drop dans zone morte 75-80 ne casse pas le timer
8. Hystérésis : chute < 75 casse vraiment le timer
9. Jaccard rumination détectée → cancel alarme
10. Jaccard nodes diversifiés → alarme persiste
11. Sanity Jaccard helper
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
    """Config par défaut (mode OBSERVE = DRY_RUN True) avec seuils Schmitt
    recalibres 29/05."""
    from config import Config
    monkeypatch.setattr(Config, "ATROPHY_ENABLED", True, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_DRY_RUN", True, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_STABILITE_HEGEMONIC_ENTRY_THRESHOLD", 80.0, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_STABILITE_HEGEMONIC_EXIT_THRESHOLD", 75.0, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_CROISSANCE_MECHANICAL_ENTRY_THRESHOLD", 10.0, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_CROISSANCE_MECHANICAL_EXIT_THRESHOLD", 15.0, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_STASIS_DURATION_S", 2700, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_ALARM_DURATION_S", 600, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_JACCARD_WINDOW", 5, raising=False)  # petit pour tests
    monkeypatch.setattr(Config, "ATROPHY_JACCARD_REDUNDANT_THRESHOLD", 0.7, raising=False)


def _mock_drives(stab_dep: float, crois_dep: float):
    """Construit un mock pour desire_engine.drives."""
    drives = {
        "STABILITE": MagicMock(deprivation=stab_dep),
        "CROISSANCE": MagicMock(deprivation=crois_dep),
    }
    return MagicMock(drives=drives)


# ---------- Tests — Entrée Schmitt ----------


@pytest.mark.asyncio
async def test_no_stasis_when_stab_below_entry(config_default, isolate_monitor):
    """Cas 1 : STAB=70 (< 80) → pas d'entrée en stase."""
    monitor = AtrophyMonitor()
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(stab_dep=70.0, crois_dep=5.0)
    )}):
        await monitor.check_balance()
    assert monitor._stasis_started_at == 0.0
    assert monitor.get_stats()["stasis_detections"] == 0
    assert monitor.get_stats()["alarms_published"] == 0


@pytest.mark.asyncio
async def test_no_stasis_when_crois_above_entry(config_default, isolate_monitor):
    """Cas 2 : CROIS=20 (>= 10) → pas d'entrée en stase malgré STAB hegemonique."""
    monitor = AtrophyMonitor()
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(stab_dep=85.0, crois_dep=20.0)
    )}):
        await monitor.check_balance()
    assert monitor._stasis_started_at == 0.0
    assert monitor.get_stats()["stasis_detections"] == 0


@pytest.mark.asyncio
async def test_stasis_detected_when_entry_thresholds_met(config_default, isolate_monitor):
    """Cas 3 : STAB=85 + CROIS=5 → stase détectée (timestamp posé) MAIS pas
    d'alarme immédiate (cf timer 45 min)."""
    monitor = AtrophyMonitor()
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(stab_dep=85.0, crois_dep=5.0)
    )}):
        await monitor.check_balance()
    assert monitor._stasis_started_at > 0.0
    assert monitor.get_stats()["stasis_detections"] == 1
    assert monitor.get_stats()["alarms_published"] == 0  # pas encore
    assert not monitor.is_alarm_active()


# ---------- Tests — Timer 45 min ----------


@pytest.mark.asyncio
async def test_no_alarm_before_stasis_duration(config_default, isolate_monitor):
    """Cas 4 : stase détectée depuis 30 min seulement (< 45 min) → pas d'alarme."""
    monitor = AtrophyMonitor()
    # Simuler : stase entamée il y a 30 min
    monitor._stasis_started_at = time.time() - (30 * 60)  # 1800s
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(stab_dep=85.0, crois_dep=5.0)
    )}):
        await monitor.check_balance()
    assert not monitor.is_alarm_active()
    assert monitor.get_stats()["alarms_published"] == 0


@pytest.mark.asyncio
async def test_alarm_published_after_stasis_duration(config_default, isolate_monitor):
    """Cas 5 : stase détectée depuis 46 min (> 45 min) → alarme publiée."""
    monitor = AtrophyMonitor()
    # Simuler : stase entamée il y a 46 min (au-delà du seuil 45 min)
    monitor._stasis_started_at = time.time() - (46 * 60)  # 2760s
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(stab_dep=85.0, crois_dep=5.0)
    )}):
        await monitor.check_balance()
    assert monitor.is_alarm_active()
    assert monitor.get_stats()["alarms_published"] == 1
    # Apres publication, le timer de stase doit etre reset
    assert monitor._stasis_started_at == 0.0


@pytest.mark.asyncio
async def test_dry_run_does_not_publish_bus(config_default, isolate_monitor):
    """Cas 6 : DRY_RUN=True → alarme calculée + log, mais bus.publish PAS appelé."""
    monitor = AtrophyMonitor()
    monitor._stasis_started_at = time.time() - 2800  # > 45 min
    mock_bus = MagicMock()
    mock_bus.publish = AsyncMock()
    with patch.dict("sys.modules", {
        "core.desire_engine": MagicMock(desire_engine=_mock_drives(85.0, 5.0)),
        "core.event_bus.bus": MagicMock(bus=mock_bus),
    }):
        await monitor.check_balance()
    assert monitor.is_alarm_active()
    assert monitor.get_stats()["would_boost_dry_run"] == 1
    mock_bus.publish.assert_not_called()


# ---------- Tests — Hystérésis (Schmitt) ----------


@pytest.mark.asyncio
async def test_hysteresis_micro_drop_does_not_break(config_default, isolate_monitor):
    """Cas 7 : stase entamée à STAB=81, micro-drop à STAB=79.5 (entre 75 et 80
    = zone morte du Schmitt) → timer continue à courir, pas de rupture."""
    monitor = AtrophyMonitor()
    # Tick 1 : entrée en stase à STAB=81
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(stab_dep=81.0, crois_dep=5.0)
    )}):
        await monitor.check_balance()
    initial_stasis_ts = monitor._stasis_started_at
    assert initial_stasis_ts > 0.0
    # Tick 2 : micro-drop dans la zone morte (STAB=79.5, entre 75 et 80)
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(stab_dep=79.5, crois_dep=5.0)
    )}):
        await monitor.check_balance()
    # Le timer ne doit PAS être reset
    assert monitor._stasis_started_at == initial_stasis_ts
    assert monitor.get_stats()["stasis_breaks"] == 0


@pytest.mark.asyncio
async def test_hysteresis_real_drop_breaks(config_default, isolate_monitor):
    """Cas 8 : stase entamée à STAB=85, vraie chute à STAB=74 (< 75 = sortie de
    la zone morte) → timer reset, log stasis_broken."""
    monitor = AtrophyMonitor()
    # Tick 1 : entrée en stase
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(stab_dep=85.0, crois_dep=5.0)
    )}):
        await monitor.check_balance()
    assert monitor._stasis_started_at > 0.0
    # Tick 2 : vraie chute (STAB=74 < 75)
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(stab_dep=74.0, crois_dep=5.0)
    )}):
        await monitor.check_balance()
    assert monitor._stasis_started_at == 0.0
    assert monitor.get_stats()["stasis_breaks"] == 1


# ---------- Tests — Coupe-circuit Jaccard (logique inchangée) ----------


@pytest.mark.asyncio
async def test_jaccard_rumination_cancels_alarm(config_default, isolate_monitor):
    """Cas 9 : alarme active + 5 nodes identiques dans 2 fenêtres glissantes
    (Jaccard=1.0 > 0.7) → cancel rumination."""
    monitor = AtrophyMonitor()
    # Forcer alarme active (raccourci pour tester le coupe-circuit)
    monitor._alarm_active = True
    monitor._alarm_started_at = time.time()

    # Simuler des nodes identiques dans les 2 fenêtres glissantes
    same_nodes = ["concept_X", "concept_Y", "concept_Z", "concept_W", "concept_V"]
    for nid in same_nodes:
        await monitor._on_synaptic_update({"change": "node_new", "id": nid})
    for nid in same_nodes:
        await monitor._on_synaptic_update({"change": "node_new", "id": nid})

    # Tick : devrait détecter rumination et cancel
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(85.0, 5.0)
    )}):
        await monitor.check_balance()
    assert not monitor._alarm_active
    assert monitor.get_stats()["alarms_cancelled_rumination"] >= 1


@pytest.mark.asyncio
async def test_jaccard_diverse_keeps_alarm(config_default, isolate_monitor):
    """Cas 10 : alarme active + nodes diversifiés (Jaccard=0) → alarme persiste."""
    monitor = AtrophyMonitor()
    monitor._alarm_active = True
    monitor._alarm_started_at = time.time()

    # win_old : 5 nodes ; win_new : 5 nodes complètement différents
    for i in range(5):
        await monitor._on_synaptic_update({"change": "node_new", "id": f"old_{i}"})
    for i in range(5):
        await monitor._on_synaptic_update({"change": "node_new", "id": f"new_{i}"})

    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(85.0, 5.0)
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
