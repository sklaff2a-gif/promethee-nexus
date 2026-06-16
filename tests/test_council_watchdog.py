"""TDD — WATCHDOG DU COUNCIL (chien de garde de la délibération).

Doctrine vérifiée ici :
- TIMEOUT FIXE & DÉCOUPLÉ : une deadline absolue avorte un council trop long,
  indépendamment du reptile (status dédié `watchdog_timeout`).
- AVORTEMENT SUR LA DÉRIVÉE : un RISE de menace depuis l'ouverture avorte ;
  un plateau (le « Fantôme du 14 juin » à 5.0, plat) n'avorte JAMAIS (rise=0).
- PRÉSIDENT BORNÉ : un architect qui hang → verdict NEUTRE (pas d'ABORT ni de
  consensus fabriqué).
- ANTI-SPIRALE : un abort-watchdog n'est PAS classé stérile → pas de DIP dopamine.
- `_read_threat_level` défensif : 0.0 si le reptile est absent ou en erreur.
"""

import asyncio

import pytest

from core import council as council_mod
from core.council import Council, WATCHDOG_TIMEOUT_STATUS


class _MockAgent:
    def __init__(self, text):
        self.text = text

    async def generate_content(self, prompt):
        return self.text


def _run_council(**kw):
    agents = {"coder": _MockAgent("alpha bravo charlie"),
              "writer": _MockAgent("delta echo foxtrot")}
    return Council(agents=agents, participants=["coder", "writer"], mission="m",
                   enable_student=False, enable_advocate=False, **kw)


# ── _read_threat_level : lecture défensive (dérivée seulement) ─────────────

def test_read_threat_level_returns_zero_when_reptile_absent(monkeypatch):
    import core.organ_registry as oreg
    monkeypatch.setattr(oreg, "get_organ", lambda name: None)
    assert council_mod._read_threat_level() == 0.0


def test_read_threat_level_swallows_errors(monkeypatch):
    import core.organ_registry as oreg

    def boom(name):
        raise RuntimeError("reptile indisponible")
    monkeypatch.setattr(oreg, "get_organ", boom)
    assert council_mod._read_threat_level() == 0.0  # fail-safe, jamais d'exception


# ── TIMEOUT FIXE & DÉCOUPLÉ : la deadline absolue ──────────────────────────

@pytest.mark.asyncio
async def test_deadline_aborts_with_watchdog_status(monkeypatch):
    monkeypatch.setattr(council_mod, "COUNCIL_MAX_WALL_S", -1.0)   # deadline déjà dépassée
    monkeypatch.setattr(council_mod, "_read_threat_level", lambda: 0.0)
    result = await _run_council().run()
    assert result["status"] == WATCHDOG_TIMEOUT_STATUS
    # anti-spirale : le gradient est neutralisé, jamais "sterile"
    assert result["gradient"]["verdict"] == WATCHDOG_TIMEOUT_STATUS


# ── AVORTEMENT SUR LA DÉRIVÉE de menace ────────────────────────────────────

@pytest.mark.asyncio
async def test_threat_rise_aborts(monkeypatch):
    monkeypatch.setattr(council_mod, "COUNCIL_MAX_WALL_S", 999.0)
    # ouverture à 1.0, puis pic à 4.0 pendant le débat → rise 3.0 >= 2.0 → abort
    vals = iter([1.0, 4.0])
    monkeypatch.setattr(council_mod, "_read_threat_level", lambda: next(vals, 4.0))
    result = await _run_council().run()
    assert result["status"] == WATCHDOG_TIMEOUT_STATUS


@pytest.mark.asyncio
async def test_threat_plateau_does_not_abort_the_ghost(monkeypatch):
    """Le Fantôme du 14 juin (menace de fond chronique ~5.0) est PLAT → rise=0
    → ne doit JAMAIS déclencher le watchdog (sinon délibération étranglée 24/7)."""
    monkeypatch.setattr(council_mod, "COUNCIL_MAX_WALL_S", 999.0)
    monkeypatch.setattr(council_mod, "_read_threat_level", lambda: 5.0)  # plateau
    result = await _run_council().run()
    assert result["status"] != WATCHDOG_TIMEOUT_STATUS


# ── PRÉSIDENT BORNÉ : timeout → verdict neutre ─────────────────────────────

@pytest.mark.asyncio
async def test_president_timeout_returns_neutral(monkeypatch):
    monkeypatch.setattr(council_mod, "COUNCIL_PRESIDENT_TIMEOUT_S", 0.05)

    class _SlowArchitect:
        async def generate_content(self, prompt):
            await asyncio.sleep(0.5)
            return "ABORT: tout est nul"   # même un ABORT ne doit pas être fabriqué sur timeout

    c = Council(agents={"architect": _SlowArchitect()},
                participants=["coder", "writer"], mission="m",
                enable_student=False, enable_advocate=False)
    monkeypatch.setattr(c, "_build_president_prompt", lambda rn: "p")
    verdict = await c._evaluate_round(2)
    assert verdict["verdict"] == "PERTINENT"   # neutre
    assert verdict["feedback"] == ""           # ni ABORT ni consensus fabriqué


# ── ANTI-SPIRALE : abort-watchdog non puni comme un stérile ────────────────

def test_watchdog_abort_not_penalized_like_sterile():
    """Un abort-watchdog ne doit PAS plafonner la note (sinon DIP dopamine →
    spirale menace↑→abort→échec→menace↑↑)."""
    from core.autonomy_engine import autonomy
    _LONG = ("Un resume de debat correct, en francais, suffisamment long pour ne pas "
             "etre penalise par le plancher de longueur du scoring mecanique du moteur.")
    resp = {"status": WATCHDOG_TIMEOUT_STATUS, "result": _LONG,
            "gradient": {"verdict": WATCHDOG_TIMEOUT_STATUS}}
    q = autonomy._score_result_quality(resp, "COUNCIL_DEBATE")
    assert q > 0.3, "un abort-watchdog n'est pas un débat stérile : pas de DIP"
