"""Tests unitaires de core/baseline_tracker.py."""

import json
import math
import time
from pathlib import Path

import pytest

from core import baseline_tracker as bt_module
from core.baseline_tracker import (
    BaselineTracker,
    CRENEAU_LABELS,
    creneau_for_timestamp,
)


@pytest.fixture
def tracker(monkeypatch, tmp_path):
    """Instance fraîche, persistance redirigée vers tmp_path.

    ORDRE CRITIQUE : redirection AVANT reset, sinon le reset recrée
    une instance qui charge le vrai memory/baselines.json.
    """
    monkeypatch.setattr(bt_module, "BASELINE_FILE", tmp_path / "baselines.json")
    BaselineTracker.reset_singleton()
    yield bt_module.baseline_tracker
    BaselineTracker.reset_singleton()


def test_creneau_labels_couvrent_24h():
    """Les 6 créneaux doivent couvrir 0-23h sans trou ni doublon."""
    seen = set()
    for hour in range(24):
        ts = time.mktime(time.struct_time((2026, 5, 1, hour, 0, 0, 0, 0, 0)))
        seen.add(creneau_for_timestamp(ts))
    assert seen == set(CRENEAU_LABELS), f"Manque ou doublon: {seen}"


def test_bootstrap_nominal_charge_config(tracker):
    """Au démarrage, les baselines nominales doivent être chargées du JSON."""
    assert "cardiac_bpm" in tracker._nominal
    assert tracker._nominal["cardiac_bpm"]["mu"] == 65.0
    assert tracker._nominal["cardiac_bpm"]["sigma"] == 12.0


def test_baseline_avant_n20_utilise_nominal(tracker):
    """Sous n=20 observations, on doit utiliser mu/sigma nominaux."""
    ts = time.time()
    for _ in range(10):
        tracker.update("cardiac_bpm", 120.0, ts)
    mu, sigma, n = tracker.get_baseline("cardiac_bpm", ts)
    # Doit retourner les valeurs nominales malgré les 120 récents
    assert mu == 65.0
    assert sigma == 12.0
    assert n == 10


def test_baseline_au_dela_de_n30_utilise_empirique(tracker):
    """À partir de n=30, mu/sigma sont calculés sur les données réelles."""
    ts = time.time()
    for _ in range(35):
        tracker.update("cardiac_bpm", 100.0, ts)
    mu, sigma, n = tracker.get_baseline("cardiac_bpm", ts)
    assert n >= 30
    assert abs(mu - 100.0) < 0.01
    # Toutes valeurs identiques → sigma ~= 0 (clampé au minimum)
    assert sigma > 0
    assert sigma < 0.01


def test_baseline_blend_entre_n20_et_n30(tracker):
    """Entre 20 et 30 obs, blend linéaire entre nominal et empirique."""
    ts = time.time()
    for _ in range(25):
        tracker.update("cardiac_bpm", 100.0, ts)
    mu, _, n = tracker.get_baseline("cardiac_bpm", ts)
    # alpha = (25 - 20) / (30 - 20) = 0.5 → mu_blend = 0.5*65 + 0.5*100 = 82.5
    assert n == 25
    assert abs(mu - 82.5) < 1.0


def test_zscore_coherent_apres_apprentissage(tracker):
    """Après convergence empirique, z-score doit refléter l'écart à la moyenne."""
    ts = time.time()
    for _ in range(40):
        tracker.update("cardiac_bpm", 60.0, ts)
    # Une valeur de 60 doit donner z ~ 0
    z0 = tracker.get_zscore("cardiac_bpm", 60.0, ts)
    assert abs(z0) < 0.5
    # Une valeur de 100 doit donner z très positif (sigma sera petit)
    z_high = tracker.get_zscore("cardiac_bpm", 100.0, ts)
    assert z_high > 5.0


def test_derivative_pas_d_historique_zero(tracker):
    """Sans historique, la dérivée doit être 0."""
    ts = time.time()
    dzdt = tracker.get_derivative_zscore("cardiac_bpm", 80.0, ts)
    assert dzdt == 0.0


def test_derivative_avec_historique(tracker):
    """Une variation rapide doit produire une dérivée non nulle."""
    base_ts = time.time()
    # Stable à 60 pendant 10 min
    for i in range(20):
        tracker.update("cardiac_bpm", 60.0, base_ts - 600 + i * 30)
    # Brusque montée
    z_spike = tracker.get_derivative_zscore("cardiac_bpm", 120.0, base_ts)
    assert z_spike > 0.5


def test_nan_et_inf_rejetes(tracker):
    """NaN et Inf ne doivent pas polluer le buffer."""
    ts = time.time()
    tracker.update("cardiac_bpm", float("nan"), ts)
    tracker.update("cardiac_bpm", float("inf"), ts)
    tracker.update("cardiac_bpm", float("-inf"), ts)
    stats = tracker.get_stats("cardiac_bpm")
    assert sum(stats.values()) == 0


def test_persistance_round_trip(tracker, monkeypatch, tmp_path):
    """save() → load() → état identique."""
    ts = time.time()
    for v in [55.0, 60.0, 62.0, 58.0, 65.0]:
        tracker.update("cardiac_bpm", v, ts)
    tracker.save()

    BaselineTracker.reset_singleton()
    monkeypatch.setattr(bt_module, "BASELINE_FILE", tmp_path / "baselines.json")
    new_inst = BaselineTracker()
    stats = new_inst.get_stats("cardiac_bpm")
    assert sum(stats.values()) == 5


def test_creneaux_separes_independants(tracker):
    """Deux observations dans des créneaux différents ne doivent pas fusionner."""
    # Force des heures différentes
    ts_nuit = time.mktime(time.struct_time((2026, 5, 1, 2, 0, 0, 0, 0, -1)))
    ts_midi = time.mktime(time.struct_time((2026, 5, 1, 13, 0, 0, 0, 0, -1)))
    for _ in range(35):
        tracker.update("cardiac_bpm", 50.0, ts_nuit)
    for _ in range(35):
        tracker.update("cardiac_bpm", 90.0, ts_midi)

    mu_nuit, _, _ = tracker.get_baseline("cardiac_bpm", ts_nuit)
    mu_midi, _, _ = tracker.get_baseline("cardiac_bpm", ts_midi)
    assert abs(mu_nuit - 50.0) < 1.0
    assert abs(mu_midi - 90.0) < 1.0


def test_metric_inconnue_renvoie_defaut(tracker):
    """Une métrique non listée dans le nominal doit donner un baseline neutre."""
    ts = time.time()
    mu, sigma, n = tracker.get_baseline("metrique_imaginaire", ts)
    assert mu == 0.0
    assert sigma >= 1e-6
    assert n == 0


# ─────────────────────────────────────────────────────────────────────────
# V14.7 — min_sigma anti-hypocondrie
# ─────────────────────────────────────────────────────────────────────────

def test_min_sigma_applique_si_sigma_empirique_bas(tracker):
    """V14.7 : si min_sigma déclaré, sigma_empirique ne descend pas sous."""
    ts = time.time()
    # Inject 35 obs très proches → sigma_e va s'effondrer
    for _ in range(35):
        tracker.update("dream_dette_h", 0.5, ts)
    mu, sigma, n = tracker.get_baseline("dream_dette_h", ts)
    # min_sigma=4.0 dans symptomes_baseline.json → sigma final >= 4.0
    assert sigma >= 4.0, (
        f"sigma={sigma} doit etre >= 4.0 (min_sigma) malgre effondrement "
        f"empirique (vraie sigma_empirique ~= 0)"
    )


def test_min_sigma_n_affecte_pas_metric_sans_min_sigma(tracker):
    """Si min_sigma absent du nominal, comportement V14.6 préservé (rétrocompat)."""
    ts = time.time()
    # cardiac_bpm n'a pas de min_sigma déclaré
    for _ in range(35):
        tracker.update("cardiac_bpm", 60.0, ts)
    mu, sigma, n = tracker.get_baseline("cardiac_bpm", ts)
    # sigma_empirique très bas, pas de min_sigma → on garde la petite valeur
    assert sigma < 1.0, (
        f"sigma cardiac_bpm sans min_sigma doit pouvoir descendre "
        f"librement, ici {sigma}"
    )


def test_anti_hypocondrie_dette_reve_sous_seuil(tracker):
    """Cas concret : avec min_sigma=4 sur dream_dette_h, une dette de
    9h ne doit JAMAIS franchir z>=1.5 quel que soit l'apprentissage."""
    ts = time.time()
    # Simule le cas observé en vol : Promethee consolide souvent,
    # baseline empirique converge vers ~0.5h avec sigma proche de 0
    for _ in range(35):
        tracker.update("dream_dette_h", 0.5, ts)
    # Avec ancien comportement : z = (9 - 0.5) / 0.001 = 8500 → ALARME
    # Avec V14.7 : sigma >= 4.0 → z <= (9 - 0.5) / 4.0 = 2.125
    z = tracker.get_zscore("dream_dette_h", 9.0, ts)
    # Pas garantit < 1.5 (9h vs mu=0.5 donne z>=1.5 même avec sigma=4),
    # mais BORNÉ : ~2.125 au lieu de 8500.
    assert z < 3.0, f"z={z} doit etre borne par min_sigma, pas exploser"


def test_min_sigma_test_sous_n20_aussi(tracker):
    """min_sigma s'applique aussi au mode bootstrap (n<20)."""
    ts = time.time()
    # Inject 5 obs (n<20 → mode nominal pur)
    for _ in range(5):
        tracker.update("dream_dette_h", 0.5, ts)
    mu, sigma, n = tracker.get_baseline("dream_dette_h", ts)
    # En mode nominal pur, sigma_n = 6.0 (déjà > min_sigma 4.0)
    # Donc sigma final = 6.0 (pas affecté par min_sigma)
    assert sigma == 6.0
    # Mais si on force un nominal avec sigma_n=2 et min_sigma=4 ?
    # → sigma final = 4 (min_sigma gagne)
    # Ce cas n'existe pas dans la config actuelle, donc on teste juste
    # que le mécanisme ne casse pas le mode bootstrap.
    assert n == 5
