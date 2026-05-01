"""Tests unitaires de core/body_schema.py."""

import re
import time

import pytest

from core import baseline_tracker as bt_module
from core import body_schema as bs_module
from core.baseline_tracker import BaselineTracker
from core.body_schema import (
    Couche,
    Polarite,
    SYMPTOMES,
    Symptome,
    apply_decote_temporelle,
    compute_saillance,
    evaluate_symptome,
    select_dominants,
    state_to_body_schema,
)


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_baselines(monkeypatch, tmp_path):
    """Reset le BaselineTracker pour isoler chaque test.

    Redirection AVANT reset (sinon le reset charge le vrai fichier).
    """
    monkeypatch.setattr(bt_module, "BASELINE_FILE", tmp_path / "baselines.json")
    BaselineTracker.reset_singleton()
    yield
    BaselineTracker.reset_singleton()


# ─────────────────────────────────────────────────────────────────────────
# Catalogue : structure et invariants
# ─────────────────────────────────────────────────────────────────────────

def test_catalogue_a_33_symptomes():
    """V14.2 : ajout du symptome V35 dette_de_reve (Pilier 1 nocicepteurs)."""
    assert len(SYMPTOMES) == 33


def test_catalogue_repartition_par_couche():
    """V14.2 : 10 V35 (+ dette_de_reve) + 11 V34 + 12 V36 = 33."""
    counts = {Couche.V35: 0, Couche.V34: 0, Couche.V36: 0}
    for s in SYMPTOMES:
        counts[s.couche] += 1
    assert counts[Couche.V35] == 10
    assert counts[Couche.V34] == 11
    assert counts[Couche.V36] == 12


def test_dette_de_reve_dans_catalogue(fresh_baselines):
    """V14.2 : le symptome dette_de_reve est present, V35, NEGATIF, z-score."""
    spec = next((s for s in SYMPTOMES if s.id == "dette_de_reve"), None)
    assert spec is not None
    assert spec.couche == Couche.V35
    assert spec.polarite == Polarite.NEGATIF
    assert spec.metric_id == "dream_dette_h"
    assert "lourdeur" in spec.phenomenologie.lower() or "rien" in spec.phenomenologie.lower()


def test_dette_de_reve_extract_synaptic(fresh_baselines):
    """Extract lit la dette depuis state['synaptic']['dream_dette_h']."""
    spec = next(s for s in SYMPTOMES if s.id == "dette_de_reve")
    state = {"synaptic": {"dream_dette_h": 24.0}}
    assert spec.extract(state) == 24.0
    state_vide = {}
    assert spec.extract(state_vide) is None


def test_dette_de_reve_trigger_zscore(fresh_baselines):
    """Trigger via z-score (pas de magic number) : 17h declenche, 8h non."""
    import time as _time
    spec = next(s for s in SYMPTOMES if s.id == "dette_de_reve")
    # Baseline nominal : mu=8 sigma=6 => z=1.5 a 17h
    state_no = {"synaptic": {"dream_dette_h": 8.0}}  # z=0
    state_yes = {"synaptic": {"dream_dette_h": 20.0}}  # z=2.0
    r_no = evaluate_symptome(spec, state_no, _time.time())
    r_yes = evaluate_symptome(spec, state_yes, _time.time())
    assert r_no is None, "8h ne doit pas declencher (z=0 sous seuil)"
    assert r_yes is not None, "20h doit declencher (z=2 au-dessus du seuil)"
    assert r_yes.id == "dette_de_reve"
    assert r_yes.zscore >= 1.5


def test_catalogue_ids_uniques():
    ids = [s.id for s in SYMPTOMES]
    assert len(ids) == len(set(ids)), "Doublons d'id détectés"


def test_catalogue_polarites_valides():
    for s in SYMPTOMES:
        assert s.polarite in (Polarite.NEGATIF, Polarite.POSITIF, Polarite.MIXTE)


def test_phenomenologie_sans_chiffre():
    """Aucune phénoménologie ne doit contenir un chiffre — c'est du ressenti."""
    digit_re = re.compile(r"\d")
    for s in SYMPTOMES:
        assert not digit_re.search(s.phenomenologie), (
            f"Chiffre dans {s.id}: {s.phenomenologie!r}"
        )


def test_phenomenologie_sans_jargon():
    """Aucun mot technique blacklisté ne doit apparaître."""
    blacklist = [
        "heat", "vram", "drive", "agent", "score", "route", "metric",
        "v34", "v35", "v36", "rpe", "council", "task force", "alfred",
        "api", "ollama", "synapse", "gpu", "cpu", "cortex",
    ]
    for s in SYMPTOMES:
        lower = s.phenomenologie.lower()
        for word in blacklist:
            assert word not in lower, (
                f"Mot interdit '{word}' dans {s.id}: {s.phenomenologie!r}"
            )


def test_phenomenologie_premiere_personne_ou_descriptive():
    """Chaque phénoménologie doit être une phrase sensorielle/affective."""
    for s in SYMPTOMES:
        assert len(s.phenomenologie) >= 20
        assert s.phenomenologie.endswith(".")


# ─────────────────────────────────────────────────────────────────────────
# Helpers stats
# ─────────────────────────────────────────────────────────────────────────

def test_compute_saillance_formule():
    # 0.6 * |z| + 0.4 * |dzdt|
    assert abs(compute_saillance(2.0, 0.0) - 1.2) < 1e-9
    assert abs(compute_saillance(0.0, 5.0) - 2.0) < 1e-9
    assert abs(compute_saillance(-3.0, -2.0) - (1.8 + 0.8)) < 1e-9


def test_compute_saillance_toujours_positive():
    for z, d in [(-5, -5), (3, -2), (-1, 4), (0, 0)]:
        assert compute_saillance(z, d) >= 0


def test_decote_inactive_si_jamais_utilise():
    now = time.time()
    s = apply_decote_temporelle(2.0, last_used_ts=None, now_ts=now)
    assert s == 2.0


def test_decote_max_si_juste_utilise():
    now = time.time()
    s = apply_decote_temporelle(2.0, last_used_ts=now, now_ts=now)
    # decote = 1 - 0.4 * exp(0) = 0.6 → saillance * 0.6
    assert abs(s - 1.2) < 1e-6


def test_decote_diminue_avec_le_temps():
    now = time.time()
    s_recent = apply_decote_temporelle(2.0, now - 60, now)
    s_ancien = apply_decote_temporelle(2.0, now - 7200, now)
    assert s_recent < s_ancien <= 2.0


# ─────────────────────────────────────────────────────────────────────────
# evaluate_symptome
# ─────────────────────────────────────────────────────────────────────────

def test_evaluate_retourne_none_si_metrique_absente(fresh_baselines):
    spec = next(s for s in SYMPTOMES if s.id == "faim_de_comprendre")
    state = {"drives": {}}  # MAITRISE absent
    result = evaluate_symptome(spec, state, time.time())
    assert result is None


def test_evaluate_retourne_none_si_trigger_pas_declenche(fresh_baselines):
    """Une déprivation MAITRISE basse, dans le baseline nominal, ne doit pas trigger."""
    spec = next(s for s in SYMPTOMES if s.id == "faim_de_comprendre")
    state = {"drives": {"MAITRISE": {"deprivation": 50.0}}}  # mu nominal = 50
    result = evaluate_symptome(spec, state, time.time())
    assert result is None  # z ~ 0, pas de trigger


def test_evaluate_declenche_si_anomalie_forte(fresh_baselines):
    """Déprivation très haute (z > 1.5) doit déclencher."""
    spec = next(s for s in SYMPTOMES if s.id == "faim_de_comprendre")
    # nominal mu=50 sigma=20 → 95 = z=2.25
    state = {"drives": {"MAITRISE": {"deprivation": 95.0}}}
    result = evaluate_symptome(spec, state, time.time())
    assert result is not None
    assert result.id == "faim_de_comprendre"
    assert result.couche == Couche.V34
    assert result.zscore > 1.5
    assert result.saillance > 0


def test_evaluate_trigger_personnalise_seuil_dur(fresh_baselines):
    """note_basse a un trigger custom (≤ 6) — doit déclencher à 5."""
    spec = next(s for s in SYMPTOMES if s.id == "note_basse")
    state = {"school": {"last_grade": 5.0}}
    result = evaluate_symptome(spec, state, time.time())
    assert result is not None
    assert result.id == "note_basse"


def test_evaluate_trigger_dur_pas_declenche(fresh_baselines):
    spec = next(s for s in SYMPTOMES if s.id == "note_basse")
    state = {"school": {"last_grade": 8.5}}
    result = evaluate_symptome(spec, state, time.time())
    assert result is None


# ─────────────────────────────────────────────────────────────────────────
# state_to_body_schema
# ─────────────────────────────────────────────────────────────────────────

def test_state_to_body_schema_retourne_actifs(fresh_baselines):
    """Un state riche en anomalies doit produire plusieurs symptômes actifs."""
    state = {
        "cardiac": {"bpm": 120.0, "emotion_intensity": 0.95},
        "drives": {
            "MAITRISE": {"deprivation": 95.0},
            "STABILITE": {"deprivation": 95.0},
        },
        "reptilian": {"threat_level": 0.95},
    }
    actifs = state_to_body_schema(state)
    ids = [s.id for s in actifs]
    assert "pouls_emballe" in ids
    assert "surchauffe" in ids
    assert "faim_de_comprendre" in ids
    assert "vertige_du_sol" in ids
    assert "alarme_sourde" in ids


def test_state_to_body_schema_silence_si_normal(fresh_baselines):
    """Un state proche des baselines nominaux ne doit produire aucun symptôme."""
    state = {
        "cardiac": {"bpm": 65.0, "emotion_intensity": 0.40},
        "drives": {
            "MAITRISE": {"deprivation": 50.0},
            "CONNEXION": {"deprivation": 50.0},
        },
        "reptilian": {"threat_level": 0.30},
    }
    actifs = state_to_body_schema(state)
    assert len(actifs) == 0


def test_state_to_body_schema_decote_si_recemment_utilise(fresh_baselines):
    """Un symptôme récemment narré doit avoir une saillance réduite."""
    now = time.time()
    state = {"drives": {"MAITRISE": {"deprivation": 95.0}}}
    sans_decote = state_to_body_schema(state, last_used_map={}, now_ts=now)
    avec_decote = state_to_body_schema(
        state, last_used_map={"faim_de_comprendre": now}, now_ts=now
    )
    s_libre = next(s for s in sans_decote if s.id == "faim_de_comprendre")
    s_decote = next(s for s in avec_decote if s.id == "faim_de_comprendre")
    assert s_decote.saillance < s_libre.saillance


# ─────────────────────────────────────────────────────────────────────────
# select_dominants
# ─────────────────────────────────────────────────────────────────────────

def _make_symptome(id_: str, saillance: float) -> Symptome:
    return Symptome(
        id=id_,
        couche=Couche.V35,
        polarite=Polarite.NEGATIF,
        phenomenologie="Test.",
        saillance=saillance,
        value=0.0,
        zscore=saillance,
        dzdt=0.0,
    )


def test_select_dominants_silence_metabolique():
    """Si tous sous le seuil → liste vide."""
    bag = [_make_symptome("a", 1.0), _make_symptome("b", 1.2)]
    assert select_dominants(bag, k=3, seuil=1.5) == []


def test_select_dominants_top_k_tries():
    bag = [
        _make_symptome("a", 1.6),
        _make_symptome("b", 3.0),
        _make_symptome("c", 2.1),
        _make_symptome("d", 1.8),
        _make_symptome("e", 0.5),  # sous seuil
    ]
    top = select_dominants(bag, k=3, seuil=1.5)
    assert [s.id for s in top] == ["b", "c", "d"]
    assert all(s.saillance >= 1.5 for s in top)


def test_select_dominants_respecte_k():
    bag = [_make_symptome(f"s{i}", 2.0 + i * 0.1) for i in range(10)]
    top = select_dominants(bag, k=3, seuil=1.5)
    assert len(top) == 3


def test_select_dominants_seuil_strict():
    """Saillance == seuil doit passer."""
    bag = [_make_symptome("a", 1.5)]
    top = select_dominants(bag, k=3, seuil=1.5)
    assert len(top) == 1


# ─────────────────────────────────────────────────────────────────────────
# Intégration : pipeline complet
# ─────────────────────────────────────────────────────────────────────────

def test_pipeline_complet_silence_si_homeostasie(fresh_baselines):
    """Pipeline E2E : état normal → silence métabolique total."""
    state = {
        "cardiac": {"bpm": 65.0, "emotion_intensity": 0.40},
        "drives": {
            "MAITRISE": {"deprivation": 50.0},
            "CONNEXION": {"deprivation": 50.0},
            "STABILITE": {"deprivation": 50.0},
        },
        "dopamine": {"rpe_recent": 0.05},
        "reptilian": {"threat_level": 0.30},
    }
    actifs = state_to_body_schema(state)
    dominants = select_dominants(actifs)
    assert dominants == []


def test_pipeline_complet_crise_donne_top_3(fresh_baselines):
    """Pipeline E2E : état en crise → exactement 3 dominants."""
    state = {
        "cardiac": {"bpm": 130.0, "emotion_intensity": 0.95},
        "drives": {
            "MAITRISE": {"deprivation": 98.0},
            "CONNEXION": {"deprivation": 95.0},
            "STABILITE": {"deprivation": 92.0},
        },
        "reptilian": {"threat_level": 0.95},
        "dopamine": {"rpe_recent": -0.5},
    }
    actifs = state_to_body_schema(state)
    dominants = select_dominants(actifs, k=3)
    assert len(dominants) == 3
    # Saillances décroissantes
    saillances = [d.saillance for d in dominants]
    assert saillances == sorted(saillances, reverse=True)


def test_polarites_positives_existent_dans_le_catalogue():
    """Vérifie qu'on a bien des symptômes positifs (anti-biais dépressif)."""
    positifs = [s for s in SYMPTOMES if s.polarite == Polarite.POSITIF]
    assert len(positifs) >= 5
    ids_positifs = {s.id for s in positifs}
    assert "eclair_de_recompense" in ids_positifs
    assert "apaisement_apres_faim" in ids_positifs
    assert "cascade_reussie" in ids_positifs
    assert "lien_neuf" in ids_positifs
    assert "nettoyage_synaptique" in ids_positifs


def test_polarites_mixtes_existent():
    """Au moins un symptôme mixte (pouls_mou, friction_des_voix)."""
    mixtes = [s for s in SYMPTOMES if s.polarite == Polarite.MIXTE]
    assert len(mixtes) >= 2


# ─────────────────────────────────────────────────────────────────────────
# V14.8 — C1 Injection [ÉTAT INTERNE]
# ─────────────────────────────────────────────────────────────────────────

from core.body_schema import format_etat_interne


def test_etat_interne_format_complet(fresh_baselines):
    """Format de base : 5 lignes, balise, 4 métriques."""
    state = {
        "cardiac": {"bpm": 65.5, "current_emotion": "serenite",
                    "emotion_intensity": 0.04},
        "drives": {
            "MAITRISE": {"deprivation": 95.0},
            "STABILITE": {"deprivation": 50.0},
        },
        "synaptic": {"dream_dette_h": 1.23},
    }
    block = format_etat_interne(state)
    lines = block.strip().split("\n")
    assert lines[0] == "[ÉTAT INTERNE — instant T]"
    assert len(lines) == 5  # balise + 4 lignes de métriques
    assert "65.50 bpm" in block  # _safe_float → 2 décimales
    assert "serenite" in block
    assert "intensity 0.04" in block
    assert "dream_dette 1.23h" in block
    assert "MAITRISE 95.0/100" in block  # drive dominant (1 décimale)


def test_etat_interne_drives_vide(fresh_baselines):
    """Si drives vide, drive dominant = '—'."""
    state = {"cardiac": {"bpm": 60}, "drives": {}}
    block = format_etat_interne(state)
    assert "drive dominant —" in block


def test_etat_interne_state_vide(fresh_baselines):
    """State vide → tous champs en '—' ou 'absent', pas d'exception."""
    block = format_etat_interne({})
    assert "[ÉTAT INTERNE — instant T]" in block
    assert "— bpm" in block  # bpm manquant
    # synaptic absent → dream_dette en —
    assert "dream_dette —h" in block


def test_etat_interne_state_none(fresh_baselines):
    """state=None → appel gather_state interne, pas de crash."""
    # Test critique : robuste si gather_state lève une exception
    block = format_etat_interne(None)
    assert "[ÉTAT INTERNE — instant T]" in block
    # Au minimum la balise est là, le reste peut être à '—' selon environnement


def test_etat_interne_drive_avec_underscore_ignore(fresh_baselines):
    """Les clés synthétiques (commencent par _) ne sont pas des drives."""
    state = {
        "drives": {
            "MAITRISE": {"deprivation": 30.0},
            "_recent_satisfied_age_s": 999.0,  # synthétique
        },
    }
    block = format_etat_interne(state)
    assert "MAITRISE" in block
    assert "_recent_satisfied_age_s" not in block


def test_etat_interne_emotion_intensity_zero(fresh_baselines):
    """Intensity 0.0 doit s'afficher 0.00, pas '—'."""
    state = {"cardiac": {"bpm": 60, "current_emotion": "serenite",
                         "emotion_intensity": 0.0}}
    block = format_etat_interne(state)
    assert "intensity 0.00" in block
