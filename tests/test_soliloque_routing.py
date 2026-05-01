"""Tests de l'aiguillage v1/v2 dans autonomy_engine._execute_soliloque."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import feature_flags as ff_module
from core.feature_flags import reset_cache


@pytest.fixture
def flags(monkeypatch, tmp_path):
    """Redirige feature_flags.json vers tmp."""
    p = tmp_path / "feature_flags.json"
    monkeypatch.setattr(ff_module, "FLAGS_FILE", p)
    reset_cache()
    yield p
    reset_cache()


def _set_flag(path, value):
    path.write_text(json.dumps({"soliloque_engine": value}), encoding="utf-8")


@pytest.fixture
def engine_instance():
    """Instance autonomie bare-bones (pas d'init coûteux) pour tester
    _execute_soliloque en isolation. La méthode n'utilise pas self."""
    from core.autonomy_engine import AutonomyEngine
    inst = object.__new__(AutonomyEngine)
    yield inst


# ─────────────────────────────────────────────────────────────────────────
# Aiguillage v1
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_routing_v1_appelle_ancien_module(flags, engine_instance):
    _set_flag(flags, "v1")
    fake_v1 = MagicMock()
    fake_v1.engage = AsyncMock(return_value={
        "status": "success",
        "result": "ancien insight",
        "theme": "graines_ouvertes",
    })
    with patch.dict("sys.modules", {"core.soliloque": MagicMock(soliloque=fake_v1)}):
        result = await engine_instance._execute_soliloque()
    fake_v1.engage.assert_awaited_once()
    assert result["engine"] == "v1"
    assert result["status"] == "success"
    assert result["theme"] == "graines_ouvertes"


# ─────────────────────────────────────────────────────────────────────────
# Aiguillage v2
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_routing_v2_succes_mappe_correctement(flags, engine_instance):
    _set_flag(flags, "v2")
    fake_v2 = MagicMock()
    fake_v2.engage = AsyncMock(return_value={
        "status": "success",
        "insight": "La poitrine se serre, sourdement.",
        "ancrages_utilises": ["surchauffe", "pouls_emballe"],
        "dominants": ["surchauffe", "pouls_emballe", "alarme_sourde"],
        "attempts": 1,
        "duration_s": 4.2,
    })
    with patch.dict("sys.modules", {"core.soliloque_v2": MagicMock(soliloque_v2=fake_v2)}):
        result = await engine_instance._execute_soliloque()
    fake_v2.engage.assert_awaited_once()
    assert result["engine"] == "v2"
    assert result["status"] == "success"
    assert "V2 incarné" in result["result"]
    assert "2 ancrages" in result["result"]
    assert result["insight"] == "La poitrine se serre, sourdement."
    assert result["ancrages_utilises"] == ["surchauffe", "pouls_emballe"]


@pytest.mark.asyncio
async def test_routing_v2_silence_mappe_skipped(flags, engine_instance):
    _set_flag(flags, "v2")
    fake_v2 = MagicMock()
    fake_v2.engage = AsyncMock(return_value={
        "status": "silence",
        "symptomes_actifs": 0,
        "duration_s": 0.1,
    })
    with patch.dict("sys.modules", {"core.soliloque_v2": MagicMock(soliloque_v2=fake_v2)}):
        result = await engine_instance._execute_soliloque()
    assert result["status"] == "skipped"
    assert "silence métabolique" in result["result"]
    assert result["engine"] == "v2"


@pytest.mark.asyncio
async def test_routing_v2_abort_mappe_error(flags, engine_instance):
    _set_flag(flags, "v2")
    fake_v2 = MagicMock()
    fake_v2.engage = AsyncMock(return_value={
        "status": "abort",
        "attempts": 2,
        "rejections": [{"attempt": "1", "reason": "jargon"}, {"attempt": "2", "reason": "meta"}],
        "dominants": ["pouls_emballe"],
    })
    with patch.dict("sys.modules", {"core.soliloque_v2": MagicMock(soliloque_v2=fake_v2)}):
        result = await engine_instance._execute_soliloque()
    assert result["status"] == "error"
    assert "abort" in result["result"]
    assert result["engine"] == "v2"


# ─────────────────────────────────────────────────────────────────────────
# Flag inconnu → fallback v2
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_routing_flag_inconnu_fallback_v2(flags, engine_instance):
    _set_flag(flags, "experimental_v3")
    fake_v2 = MagicMock()
    fake_v2.engage = AsyncMock(return_value={
        "status": "silence",
        "symptomes_actifs": 0,
        "duration_s": 0.1,
    })
    with patch.dict("sys.modules", {"core.soliloque_v2": MagicMock(soliloque_v2=fake_v2)}):
        result = await engine_instance._execute_soliloque()
    fake_v2.engage.assert_awaited_once()
    assert result.get("engine") == "v2_fallback"


# ─────────────────────────────────────────────────────────────────────────
# Flag absent → default v2
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_routing_default_si_flag_absent(flags, engine_instance):
    """Pas de fichier flags → default 'v2'."""
    # Ne crée pas le fichier
    fake_v2 = MagicMock()
    fake_v2.engage = AsyncMock(return_value={
        "status": "success",
        "insight": "test",
        "ancrages_utilises": ["surchauffe"],
    })
    with patch.dict("sys.modules", {"core.soliloque_v2": MagicMock(soliloque_v2=fake_v2)}):
        result = await engine_instance._execute_soliloque()
    fake_v2.engage.assert_awaited_once()
    assert result["engine"] == "v2"


# ─────────────────────────────────────────────────────────────────────────
# Robustesse : exception module
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_routing_exception_renvoie_error(flags, engine_instance):
    _set_flag(flags, "v2")
    fake_v2 = MagicMock()
    fake_v2.engage = AsyncMock(side_effect=RuntimeError("boom"))
    with patch.dict("sys.modules", {"core.soliloque_v2": MagicMock(soliloque_v2=fake_v2)}):
        result = await engine_instance._execute_soliloque()
    assert result["status"] == "error"
    assert "boom" in result["result"]


# ─────────────────────────────────────────────────────────────────────────
# Hot-reload : changer le flag entre deux appels
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_routing_hot_reload_change_engine(flags, engine_instance):
    """Premier appel v1, changement du flag, second appel v2."""
    import os
    _set_flag(flags, "v1")
    fake_v1 = MagicMock()
    fake_v1.engage = AsyncMock(return_value={"status": "success", "result": "v1"})
    fake_v2 = MagicMock()
    fake_v2.engage = AsyncMock(return_value={
        "status": "success", "insight": "v2", "ancrages_utilises": ["surchauffe"]
    })

    with patch.dict("sys.modules", {
        "core.soliloque": MagicMock(soliloque=fake_v1),
        "core.soliloque_v2": MagicMock(soliloque_v2=fake_v2),
    }):
        r1 = await engine_instance._execute_soliloque()
        assert r1["engine"] == "v1"
        fake_v1.engage.assert_awaited_once()

        # Switch live
        _set_flag(flags, "v2")
        new_mtime = flags.stat().st_mtime + 1
        os.utime(flags, (new_mtime, new_mtime))

        r2 = await engine_instance._execute_soliloque()
        assert r2["engine"] == "v2"
        fake_v2.engage.assert_awaited_once()
