"""V35.1 (2026-04-30) — Tests Thermal Homeostasis.

Couvre les 10 invariants doctrinaux validés par Jean-Michel :

  1. Decay passif lineaire (sans event, heat decroit a 0.01/min)
  2. Routine connue applique son delta (event AUTONOMY_ROUTINE_COMPLETE)
  3. Routine inconnue est no-op (pas dans THERMAL_SIGNATURES)
  4. HEAT_CEILING capture les rafales (5 EXPANSION_CODE -> heat <= 1.5)
  5. Pas de heat negative (decay sature a 0)
  6. Heat to deprivation lineaire sous le seuil
  7. Heat to deprivation embrasement au-dessus du seuil (>=80)
  8. Pulsion REPOS reflete heat dans desires.drives
  9. Persistance survit a un reboot (save -> load identique)
 10. Decay rattrape le temps ecoule pendant le down (last_decay_ts ancien)

Plus deux invariants doctrinaux complementaires (decouplage strict) :
 11. ThermalHomeostasis ne contient aucune methode qui retourne un intent
 12. La pulsion REPOS demarre a 0 (pas de croissance naturelle)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture
def fresh_thermal(tmp_path, monkeypatch):
    """Reset complet du singleton + STATE_FILE redirige vers tmp."""
    import core.thermal_homeostasis as th_mod
    th_mod.ThermalHomeostasis.reset_singleton()
    state_file = tmp_path / "thermal_state.json"
    monkeypatch.setattr(th_mod, "STATE_FILE", str(state_file))
    th = th_mod.ThermalHomeostasis()
    yield th
    th_mod.ThermalHomeostasis.reset_singleton()


@pytest.fixture
def fresh_desires(tmp_path, monkeypatch):
    """Reset desire_engine pour tests isoles.

    Important : on redirige aussi STATE_FILE vers tmp pour que _load() ne
    ramene pas un etat persiste de prod, ET on recupere le singleton via
    DesireEngine() (pas via 'from core.desire_engine import desires' qui
    bindrait l'ancienne reference module-level).
    """
    import core.desire_engine as de_mod
    state_file = tmp_path / "desire_state.json"
    monkeypatch.setattr(de_mod, "STATE_FILE", str(state_file))
    de_mod.DesireEngine.reset_singleton()
    desires = de_mod.DesireEngine()  # nouvelle instance via singleton pattern
    # Patch aussi le binding module-level pour que thermal_homeostasis le voie
    monkeypatch.setattr(de_mod, "desires", desires)
    yield desires
    de_mod.DesireEngine.reset_singleton()


# ═══════════════════════════════════════════════════════════════════════
# 1-5. Mecanique du scalaire (decay, ceiling, deltas)
# ═══════════════════════════════════════════════════════════════════════

def test_passive_decay_linear_per_minute(fresh_thermal):
    """Sans event, heat decroit a 0.01/min lineaire (= 0.01/60 par sec)."""
    th = fresh_thermal
    th.cognitive_heat = 0.50
    # Backdate de 60 secondes
    th.last_decay_ts = time.time() - 60.0
    th._apply_decay()
    # Decay attendu : 0.01 * 60s / 60s = 0.01
    assert th.cognitive_heat == pytest.approx(0.50 - 0.01, abs=0.001)


def test_routine_complete_applies_thermal_delta(fresh_thermal, fresh_desires):
    """Event AUTONOMY_ROUTINE_COMPLETE pour un intent connu => heat += signature."""
    th = fresh_thermal
    th.cognitive_heat = 0.30
    th.last_decay_ts = time.time()
    # EXPANSION_CODE = +0.30
    asyncio.run(th._on_routine_complete({"intent": "EXPANSION_CODE"}))
    assert th.cognitive_heat == pytest.approx(0.60, abs=0.005)


def test_unknown_intent_is_noop(fresh_thermal, fresh_desires):
    """Intent absent de THERMAL_SIGNATURES => heat inchange."""
    th = fresh_thermal
    th.cognitive_heat = 0.42
    th.last_decay_ts = time.time()
    asyncio.run(th._on_routine_complete({"intent": "ROUTINE_INVENTEE_INEXISTANTE"}))
    # Decay quasi-nul sur dt~0, heat doit rester ~0.42
    assert th.cognitive_heat == pytest.approx(0.42, abs=0.001)


def test_heat_ceiling_caps_burst(fresh_thermal, fresh_desires):
    """5 EXPANSION_CODE consecutifs (5 * 0.30 = 1.50) ne depassent pas HEAT_CEILING."""
    th = fresh_thermal
    th.cognitive_heat = 0.0
    th.last_decay_ts = time.time()
    for _ in range(10):  # plus que necessaire
        asyncio.run(th._on_routine_complete({"intent": "EXPANSION_CODE"}))
    assert th.cognitive_heat <= th.HEAT_CEILING
    assert th.cognitive_heat == pytest.approx(th.HEAT_CEILING, abs=0.01)


def test_decay_floor_at_zero(fresh_thermal):
    """Decay ne peut pas rendre heat negative (clamp a 0)."""
    th = fresh_thermal
    th.cognitive_heat = 0.05
    th.last_decay_ts = time.time() - 600.0  # 10 min de decay = -0.10
    th._apply_decay()
    assert th.cognitive_heat == 0.0


# ═══════════════════════════════════════════════════════════════════════
# 6-7. Traduction heat -> deprivation REPOS
# ═══════════════════════════════════════════════════════════════════════

def test_heat_to_deprivation_linear_below_threshold(fresh_thermal):
    """Sous 0.70 : depriv = heat * 100 (lineaire)."""
    th = fresh_thermal
    assert th._heat_to_deprivation(0.0) == 0.0
    assert th._heat_to_deprivation(0.30) == 30.0
    assert th._heat_to_deprivation(0.50) == 50.0
    # Juste sous le seuil d'embrasement
    assert th._heat_to_deprivation(0.69) == pytest.approx(69.0)


def test_heat_to_deprivation_embrasement_above_threshold(fresh_thermal):
    """A heat=0.70, le mapping lineaire donnerait 70, l'embrasement releve a 80.
    Au-dela, la valeur lineaire reprend si > 80."""
    th = fresh_thermal
    # heat=0.70 : lineaire=70, embrasement force depriv >= 80
    assert th._heat_to_deprivation(0.70) == 80.0
    # heat=0.85 : lineaire=85 (deja > 80), pas de boost supplementaire
    assert th._heat_to_deprivation(0.85) == 85.0
    # heat=1.0 : sature a 100
    assert th._heat_to_deprivation(1.0) == 100.0
    # heat=1.5 (HEAT_CEILING) : aussi sature a 100
    assert th._heat_to_deprivation(1.5) == 100.0


# ═══════════════════════════════════════════════════════════════════════
# 8. Pulsion REPOS dans desire_engine
# ═══════════════════════════════════════════════════════════════════════

def test_repos_deprivation_published_to_desires(fresh_thermal, fresh_desires):
    """_publish_repos ecrit la deprivation dans desires.drives['REPOS']."""
    th = fresh_thermal
    desires = fresh_desires
    th.cognitive_heat = 0.85
    th._publish_repos()
    assert "REPOS" in desires.drives
    assert desires.drives["REPOS"].deprivation == pytest.approx(85.0, abs=0.1)


# ═══════════════════════════════════════════════════════════════════════
# 9-10. Persistance et rattrapage post-reboot
# ═══════════════════════════════════════════════════════════════════════

def test_persistence_save_load_roundtrip(fresh_thermal, tmp_path, monkeypatch):
    """save() puis _load() doit retrouver heat et last_decay_ts."""
    import core.thermal_homeostasis as th_mod
    th = fresh_thermal
    th.cognitive_heat = 0.42
    th.last_decay_ts = 1234567.89
    th._save()

    # Verifier le fichier ecrit
    state_file = th_mod.STATE_FILE
    assert os.path.exists(state_file)
    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)
    assert state["cognitive_heat"] == 0.42
    assert state["last_decay_ts"] == 1234567.89

    # Reload depuis fichier
    th_mod.ThermalHomeostasis.reset_singleton()
    th2 = th_mod.ThermalHomeostasis()
    assert th2.cognitive_heat == 0.42
    assert th2.last_decay_ts == 1234567.89


def test_decay_catches_up_after_long_down(fresh_thermal):
    """Si Promethee est down 1h, le decay rattrape les 60 min ecoulees au prochain tick.
    heat=0.50, dt=3600s, decay = 0.01/min * 60min = 0.60, sature a 0."""
    th = fresh_thermal
    th.cognitive_heat = 0.50
    th.last_decay_ts = time.time() - 3600.0  # 1h de down
    th._apply_decay()
    # 0.50 - 0.60 = -0.10 -> clamp 0
    assert th.cognitive_heat == 0.0


# ═══════════════════════════════════════════════════════════════════════
# 11-12. Invariants doctrinaux (decouplage architectural)
# ═══════════════════════════════════════════════════════════════════════

def test_thermal_does_not_select_routines(fresh_thermal):
    """Doctrine architecturale : aucune methode publique de l'organe ne doit
    retourner un nom d'intent / routine. Selection = router uniquement."""
    th = fresh_thermal
    # On verifie qu'aucune methode publique ne retourne une string ressemblant
    # a un intent (UPPER_CASE_AVEC_UNDERSCORES). Approche pragmatique : on
    # liste les methodes a tester et on s'assure que le retour n'est pas un
    # intent. Ce test grave l'invariant — toute future methode qui violerait
    # devra etre justifiee (ou ce test mis a jour explicitement).
    state = th.get_state()
    assert isinstance(state, dict)
    for v in state.values():
        if isinstance(v, str):
            # Pas d'intent canonique dans un retour public
            assert not (v.isupper() and "_" in v), (
                f"get_state retourne ce qui ressemble a un intent: {v!r}"
            )


def test_repos_drive_starts_at_zero(fresh_desires):
    """V35.1 — La pulsion REPOS est externally-driven : elle demarre a 0
    et ne grandit pas naturellement avec le temps. Sa valeur est ecrite
    par thermal_homeostasis."""
    desires = fresh_desires
    assert "REPOS" in desires.drives
    assert desires.drives["REPOS"].deprivation == 0.0


def test_repos_does_not_rise_naturally(fresh_desires):
    """V35.1 — desire_engine.tick() ne doit PAS faire monter REPOS. Sa
    deprivation reste celle ecrite par thermal_homeostasis (ou 0 par
    defaut)."""
    desires = fresh_desires
    # Backdate pour forcer un elapsed_hours non-nul
    desires._last_tick = time.time() - 3600.0  # 1h
    desires.tick()
    # Les autres drives ont monte (CURIOSITE etait a 40, doit avoir monte)
    assert desires.drives["CURIOSITE"].deprivation > 40.0
    # REPOS doit rester a 0
    assert desires.drives["REPOS"].deprivation == 0.0


def test_repos_in_drive_genome_contains_only_dissipators(fresh_thermal):
    """Coherence doctrinale : tous les candidats du genome REPOS doivent
    avoir un delta thermique negatif (dissipateur) dans THERMAL_SIGNATURES.
    Un producteur dans REPOS serait une contradiction doctrinale."""
    from core.drive_routine_registry import DRIVE_GENOME, THERMAL_SIGNATURES
    assert "REPOS" in DRIVE_GENOME
    repos_intents = DRIVE_GENOME["REPOS"]
    assert len(repos_intents) >= 5  # garde-fou : assez de candidats
    for intent, weight in repos_intents.items():
        delta = THERMAL_SIGNATURES.get(intent, 0.0)
        assert delta < 0, (
            f"REPOS contient {intent} avec delta={delta} (devrait etre < 0). "
            f"Un producteur de chaleur n'a rien a faire dans REPOS."
        )
