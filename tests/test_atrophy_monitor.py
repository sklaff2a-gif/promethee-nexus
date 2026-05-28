"""Tests pour core/atrophy_monitor.py (atelier audace 27/05/2026).

Couvre les 6 cas spec atelier R7 (Option 2 — Détecteur de Bruit) :
1. Stabilité affamée → pas d'alarme
2. Croissance repue → pas d'alarme
3. Stab repue + croiss affamée → alarme déclenchée + log
4. DRY_RUN — alarme mais pas de publication bus
5. Rumination Jaccard → cancel
6. Nodes diversifiés → alarme persiste
"""

import asyncio
import json
import os
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
    """Config par défaut (mode OBSERVE = DRY_RUN True)."""
    from config import Config
    monkeypatch.setattr(Config, "ATROPHY_ENABLED", True, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_DRY_RUN", True, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_STABILITE_FED_THRESHOLD", 20.0, raising=False)
    monkeypatch.setattr(Config, "ATROPHY_CROISSANCE_STARVED_THRESHOLD", 70.0, raising=False)
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


# ---------- Tests ----------


@pytest.mark.asyncio
async def test_no_alarm_when_stability_starved(config_default, isolate_monitor):
    """Cas 1 : STABILITE.privation au-dessus du seuil 'repue' → pas d'alarme."""
    monitor = AtrophyMonitor()
    with patch("core.atrophy_monitor.AtrophyMonitor.__module__", "core.atrophy_monitor"):
        with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
            desire_engine=_mock_drives(stab_dep=60.0, crois_dep=85.0)
        )}):
            await monitor.check_balance()
    assert not monitor.is_alarm_active()
    assert monitor.get_stats()["alarms_published"] == 0


@pytest.mark.asyncio
async def test_no_alarm_when_croissance_fed(config_default, isolate_monitor):
    """Cas 2 : CROISSANCE rassasiée (<70) → pas d'alarme malgré stab repue."""
    monitor = AtrophyMonitor()
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(stab_dep=10.0, crois_dep=40.0)
    )}):
        await monitor.check_balance()
    assert not monitor.is_alarm_active()
    assert monitor.get_stats()["alarms_published"] == 0


@pytest.mark.asyncio
async def test_alarm_published_when_conditions_met(config_default, isolate_monitor):
    """Cas 3 : stab repue (<20) + croiss affamée (>70) → alarme publiée + log."""
    monitor = AtrophyMonitor()
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(stab_dep=10.0, crois_dep=85.0)
    )}):
        await monitor.check_balance()
    assert monitor.is_alarm_active()
    stats = monitor.get_stats()
    assert stats["alarms_published"] == 1
    # Log écrit ?
    assert os.path.exists(str(isolate_monitor))
    with open(str(isolate_monitor), encoding="utf-8") as f:
        lines = f.readlines()
    assert any("alarm_published" in line for line in lines)


@pytest.mark.asyncio
async def test_dry_run_does_not_publish_bus(config_default, isolate_monitor):
    """Cas 4 : DRY_RUN=True → log mais aucun bus.publish."""
    monitor = AtrophyMonitor()
    mock_bus = MagicMock()
    mock_bus.publish = AsyncMock()
    with patch.dict("sys.modules", {
        "core.desire_engine": MagicMock(desire_engine=_mock_drives(10.0, 85.0)),
        "core.event_bus.bus": MagicMock(bus=mock_bus),
    }):
        await monitor.check_balance()
    assert monitor.is_alarm_active()
    assert monitor.get_stats()["would_boost_dry_run"] == 1
    # En DRY_RUN, _publish_alarm n'appelle pas bus.publish
    mock_bus.publish.assert_not_called()


@pytest.mark.asyncio
async def test_jaccard_rumination_cancels_alarm(config_default, isolate_monitor):
    """Cas 5 : 2 fenêtres avec 5 nodes identiques (Jaccard=1.0) → cancel rumination."""
    monitor = AtrophyMonitor()
    # Forcer alarme active
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(10.0, 85.0)
    )}):
        await monitor.check_balance()
    assert monitor.is_alarm_active()

    # Simuler des nodes identiques dans les 2 fenêtres glissantes
    same_nodes = ["concept_X", "concept_Y", "concept_Z", "concept_W", "concept_V"]
    for nid in same_nodes:
        await monitor._on_synaptic_update({"change": "node_new", "id": nid})
    # Maintenant on bascule certains dans win_old, et on rajoute les mêmes dans win_new
    for nid in same_nodes:
        await monitor._on_synaptic_update({"change": "node_new", "id": nid})
    # Tick : devrait détecter rumination
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(10.0, 85.0)
    )}):
        await monitor.check_balance()
    assert not monitor.is_alarm_active()
    assert monitor.get_stats()["alarms_cancelled_rumination"] >= 1


@pytest.mark.asyncio
async def test_jaccard_diverse_keeps_alarm(config_default, isolate_monitor):
    """Cas 6 : nodes diversifiés (Jaccard=0) → alarme persiste."""
    monitor = AtrophyMonitor()
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(10.0, 85.0)
    )}):
        await monitor.check_balance()
    assert monitor.is_alarm_active()

    # win_old : 5 nodes ; win_new : 5 nodes complètement différents
    for i in range(5):
        await monitor._on_synaptic_update({"change": "node_new", "id": f"old_{i}"})
    for i in range(5):
        await monitor._on_synaptic_update({"change": "node_new", "id": f"new_{i}"})
    # Tick
    with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
        desire_engine=_mock_drives(10.0, 85.0)
    )}):
        await monitor.check_balance()
    assert monitor.is_alarm_active()
    assert monitor.get_stats()["alarms_cancelled_rumination"] == 0


def test_jaccard_helper():
    """Sanity-check de la fonction _jaccard."""
    assert _jaccard(set(), set()) == 0.0
    assert _jaccard({"a"}, {"a"}) == 1.0
    assert _jaccard({"a", "b"}, {"a", "c"}) == pytest.approx(1.0 / 3.0)
    assert _jaccard({"a", "b", "c"}, {"d", "e", "f"}) == 0.0
