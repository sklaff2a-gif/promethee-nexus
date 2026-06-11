# -*- coding: utf-8 -*-
"""TDD des GARDE-FOUS GPU + CLOUD (audit 11/06).

1. Boucle d'attente thermique GPU : sans plafond, une sonde aberrante figeait
   le scheduler pour TOUJOURS (tout Prométhée muet). Cap : 30 tours x 10s,
   puis reprise forcée loggée (le bridage hardware protège le silicium).
2. Cascade cloud : réponse vide ou erreur non-429 passaient au modèle suivant
   SANS AUCUNE TRACE -> diagnostic impossible. Désormais loggées.
"""
import asyncio
import time

import pytest

from core.base_agent import GpuScheduler


def _scheduler():
    s = GpuScheduler.__new__(GpuScheduler)
    s._total_wait_time = 0.0
    s._total_calls = 0
    s._current_agent = None
    s._last_call_end = 0.0
    s._queue_depth = 1
    return s


# ─── 1. Plafond de la boucle thermique ───

@pytest.mark.asyncio
async def test_sonde_aberrante_ne_fige_plus_le_scheduler(monkeypatch):
    """Sonde bloquée à 95°C pour toujours : acquire() doit REPRENDRE après le
    cap (30 tours), pas attendre l'éternité."""
    s = _scheduler()
    calls = {"n": 0}

    async def _temp_toujours_chaude():
        calls["n"] += 1
        return 95.0

    async def _sleep_instantane(_secs):
        return None   # le temps ne coûte rien au test

    monkeypatch.setattr(s, "_check_gpu_temp", _temp_toujours_chaude)
    monkeypatch.setattr(s, "_check_vram", lambda: asyncio.sleep(0))
    monkeypatch.setattr(asyncio, "sleep", _sleep_instantane)
    monkeypatch.setattr(s, "_ensure_lock", lambda: asyncio.Lock())

    await asyncio.wait_for(s.acquire("test"), timeout=5.0)   # avant le fix : hang infini
    # 1 vérif initiale + 30 tours max de boucle
    assert calls["n"] <= 32


@pytest.mark.asyncio
async def test_gpu_refroidi_sort_normalement(monkeypatch):
    """Comportement nominal préservé : le GPU refroidit -> sortie de boucle
    AVANT le cap (l'hystérésis 5°C fonctionne comme avant)."""
    s = _scheduler()
    temps = iter([90.0, 80.0, 60.0, 50.0])   # refroidi sous l'hystérésis (70) au 3e check

    async def _temp():
        return next(temps)

    async def _sleep_instantane(_secs):
        return None

    monkeypatch.setattr(s, "_check_gpu_temp", _temp)
    monkeypatch.setattr(s, "_check_vram", lambda: asyncio.sleep(0))
    monkeypatch.setattr(asyncio, "sleep", _sleep_instantane)
    monkeypatch.setattr(s, "_ensure_lock", lambda: asyncio.Lock())

    await asyncio.wait_for(s.acquire("test"), timeout=5.0)
    # il reste des températures non consommées = la boucle est sortie tôt
    assert next(temps, None) is not None


@pytest.mark.asyncio
async def test_gpu_froid_aucune_boucle(monkeypatch):
    """GPU déjà froid : aucune attente, un seul check."""
    s = _scheduler()
    calls = {"n": 0}

    async def _temp():
        calls["n"] += 1
        return 45.0

    monkeypatch.setattr(s, "_check_gpu_temp", _temp)
    monkeypatch.setattr(s, "_check_vram", lambda: asyncio.sleep(0))
    monkeypatch.setattr(s, "_ensure_lock", lambda: asyncio.Lock())

    await asyncio.wait_for(s.acquire("test"), timeout=5.0)
    assert calls["n"] == 1


# ─── 2. La cascade cloud n'est plus muette (invariants structurels) ───

def _source_cascade():
    import inspect
    from core import base_agent
    return inspect.getsource(base_agent)


def test_reponse_vide_loggee():
    """response.text vide -> un logger.warning AVANT de passer au modèle
    suivant (la perte silencieuse est le bug de l'audit)."""
    src = _source_cascade()
    assert "a rendu une réponse VIDE" in src


def test_erreur_non_429_loggee():
    """Le continue du except générique doit logger l'erreur tronquée."""
    src = _source_cascade()
    assert "erreur: {err_str[:150]}" in src


def test_reprise_forcee_loggee():
    """La sortie par cap de la boucle GPU doit être un logger.error explicite
    (signal de sonde cassée à investiguer), pas un silence."""
    src = _source_cascade()
    assert "reprise FORCÉE" in src
