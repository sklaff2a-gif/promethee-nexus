# -*- coding: utf-8 -*-
"""TDD de l'AUTO-NAP homeostatique (atelier sieste 10/06, design CO-SIGNE par Promethee).
Son choix : un REFLEXE (pas une deliberation) a DEUX indicateurs convergents (REPOS urgent
+ coherence basse OU chaleur elevee), max 1 auto-sieste/jour, jamais si deja en sieste."""
from datetime import date

import pytest

from core.autonomy_engine import AutonomyEngine
from core.desire_engine import desires
from core.corpus_callosum import callosum
from core.thermal_homeostasis import thermal


class _Drive:
    def __init__(self, deprivation):
        self.deprivation = deprivation


def _engine(napping=False, auto_nap_day=""):
    e = AutonomyEngine.__new__(AutonomyEngine)
    e.is_napping = napping
    e._auto_nap_day = auto_nap_day
    return e


@pytest.fixture
def corps(monkeypatch):
    """Etat corporel pilotable : (repos, coherence, chaleur)."""
    def _set(repos, coherence, chaleur):
        monkeypatch.setitem(desires.drives, "REPOS", _Drive(repos))
        monkeypatch.setattr(callosum, "global_coherence", coherence, raising=False)
        monkeypatch.setattr(thermal, "cognitive_heat", chaleur, raising=False)
    return _set


def test_pas_de_nap_si_repos_bas(corps):
    corps(repos=30, coherence=0.2, chaleur=0.9)   # tout va mal SAUF le besoin de repos
    assert _engine()._should_auto_nap() is False  # l'indicateur 1 est obligatoire

def test_pas_de_nap_sur_un_seul_indicateur(corps):
    corps(repos=80, coherence=0.6, chaleur=0.3)   # REPOS urgent mais corps sain
    assert _engine()._should_auto_nap() is False  # convergence exigee (son garde-fou)

def test_nap_si_repos_et_coherence_basse(corps):
    corps(repos=80, coherence=0.30, chaleur=0.2)
    assert _engine()._should_auto_nap() is True

def test_nap_si_repos_et_chaleur_elevee(corps):
    corps(repos=76, coherence=0.6, chaleur=0.85)
    assert _engine()._should_auto_nap() is True

def test_max_une_auto_sieste_par_jour(corps):
    corps(repos=90, coherence=0.1, chaleur=0.9)   # epuisement total...
    e = _engine(auto_nap_day=date.today().isoformat())
    assert e._should_auto_nap() is False           # ...mais deja auto-sieste aujourd'hui

def test_jamais_si_deja_en_sieste(corps):
    corps(repos=90, coherence=0.1, chaleur=0.9)
    assert _engine(napping=True)._should_auto_nap() is False

def test_borg_si_organes_indisponibles(monkeypatch):
    # un organe illisible ne declenche JAMAIS de sieste par accident
    monkeypatch.setattr(desires, "drives", {})    # REPOS introuvable
    assert _engine()._should_auto_nap() is False


# ─── DROIT DE REFUS (validation de ressources, atelier sieste valide JM 10/06) ───
def test_refus_si_toutes_reserves_excellentes(corps):
    corps(repos=10, coherence=0.8, chaleur=0.1)   # corps au sommet
    raison = _engine()._body_declines_nap()
    assert raison != "" and "reserves" in raison.lower()

def test_pas_de_refus_si_un_indicateur_degrade(corps):
    corps(repos=10, coherence=0.8, chaleur=0.6)   # chaleur moyenne -> la sieste a du sens
    assert _engine()._body_declines_nap() == ""

def test_pas_de_refus_si_repos_demande(corps):
    corps(repos=50, coherence=0.8, chaleur=0.1)   # le corps demande du repos
    assert _engine()._body_declines_nap() == ""

def test_refus_borg_si_organe_illisible(monkeypatch):
    monkeypatch.setattr(desires, "drives", {})
    assert _engine()._body_declines_nap() == ""    # illisible -> on ne refuse jamais


# ─── VOIE B : sleep_pressure soutenue (chantier gate de sieste, Atelier VI 22/06) ───
import core.autonomy_engine as ae
from core.hypothalamus import hypothalamus


def test_sleep_pressure_demarre_le_chrono(monkeypatch):
    """1er franchissement du seuil -> le chrono de continuite demarre, mais 0s -> pas encore."""
    monkeypatch.setitem(hypothalamus.current_values, "sleep_pressure", 0.8)
    e = _engine()
    e._sleep_pressure_high_since = 0.0
    sig = e._sleep_pressure_nap_signal(now=1000.0)
    assert e._sleep_pressure_high_since == 1000.0
    assert sig["would_trigger"] is False


def test_sleep_pressure_soutenue_would_trigger(monkeypatch):
    """Seuil franchi depuis >= DUREE continue -> would_trigger."""
    monkeypatch.setitem(hypothalamus.current_values, "sleep_pressure", 0.8)
    e = _engine()
    e._sleep_pressure_high_since = 1000.0
    sig = e._sleep_pressure_nap_signal(now=1000.0 + ae.SLEEP_PRESSURE_SUSTAINED_S)
    assert sig["would_trigger"] is True


def test_sleep_pressure_redescente_rompt_la_continuite(monkeypatch):
    """Sous le seuil -> le chrono est REMIS A ZERO (continuite rompue = anti-fuite)."""
    monkeypatch.setitem(hypothalamus.current_values, "sleep_pressure", 0.3)
    e = _engine()
    e._sleep_pressure_high_since = 1000.0   # un chrono etait en cours
    sig = e._sleep_pressure_nap_signal(now=1000.0 + 99999)
    assert e._sleep_pressure_high_since == 0.0
    assert sig["would_trigger"] is False


def test_borg_si_hypothalamus_illisible(monkeypatch):
    monkeypatch.setattr(hypothalamus, "current_values", None)  # lecture cassee
    e = _engine()
    e._sleep_pressure_high_since = 1234.0
    sig = e._sleep_pressure_nap_signal(now=9999.0)
    assert sig["would_trigger"] is False
    assert e._sleep_pressure_high_since == 0.0


def test_shadow_ne_change_pas_le_return(corps, monkeypatch, tmp_path):
    """sleep_pressure soutenue MAIS mode shadow -> _should_auto_nap reste False (bit-identique)."""
    monkeypatch.setattr(ae, "SLEEP_GATE_PRESSURE_MODE", "shadow")
    monkeypatch.setattr(ae, "_SLEEP_GATE_SHADOW_LOG", str(tmp_path / "sg.jsonl"))
    corps(repos=10, coherence=0.8, chaleur=0.1)   # VOIE A inactive (REPOS rassasie)
    monkeypatch.setitem(hypothalamus.current_values, "sleep_pressure", 0.9)
    e = _engine()
    e._sleep_pressure_high_since = 1.0   # chrono tres ancien -> sustained enorme
    assert e._should_auto_nap() is False   # shadow : ne tire PAS


def test_active_declenche_sur_sleep_pressure_soutenue(corps, monkeypatch, tmp_path):
    monkeypatch.setattr(ae, "SLEEP_GATE_PRESSURE_MODE", "active")
    monkeypatch.setattr(ae, "_SLEEP_GATE_SHADOW_LOG", str(tmp_path / "sg.jsonl"))
    corps(repos=10, coherence=0.8, chaleur=0.1)   # VOIE A inactive
    monkeypatch.setitem(hypothalamus.current_values, "sleep_pressure", 0.9)
    e = _engine()
    e._sleep_pressure_high_since = 1.0    # chrono tres ancien -> continuite atteinte
    assert e._should_auto_nap() is True    # active : la VOIE B tire


def test_active_ignore_un_pic_transitoire(corps, monkeypatch, tmp_path):
    """Anti-fuite : un pic qui DEMARRE maintenant (0s de continuite) ne declenche PAS."""
    monkeypatch.setattr(ae, "SLEEP_GATE_PRESSURE_MODE", "active")
    monkeypatch.setattr(ae, "_SLEEP_GATE_SHADOW_LOG", str(tmp_path / "sg.jsonl"))
    corps(repos=10, coherence=0.8, chaleur=0.1)
    monkeypatch.setitem(hypothalamus.current_values, "sleep_pressure", 0.9)
    e = _engine()
    e._sleep_pressure_high_since = 0.0    # le pic commence -> 0s
    assert e._should_auto_nap() is False


# ─── VOIE C : dette de sieste = temps depuis la derniere sieste reelle (recalibrage 22/06) ───
def test_voie_c_dette_declenche(monkeypatch):
    """>= NAP_DEBT_HOURS depuis la derniere sieste reelle -> would_trigger."""
    e = _engine()
    e._nap_last_exit = 1000.0
    sig = e._nap_debt_signal(now=1000.0 + (ae.NAP_DEBT_HOURS + 1) * 3600)
    assert sig["would_trigger"] is True
    assert sig["hours_since_nap"] >= ae.NAP_DEBT_HOURS


def test_voie_c_sieste_recente_pas_de_dette():
    e = _engine()
    e._nap_last_exit = 1000.0
    sig = e._nap_debt_signal(now=1000.0 + 3600)   # 1h seulement
    assert sig["would_trigger"] is False


def test_voie_c_jamais_sans_sieste_anterieure():
    """_nap_last_exit == 0 (systeme vierge) -> pas de delta-epoch parasite."""
    e = _engine()
    e._nap_last_exit = 0.0
    sig = e._nap_debt_signal(now=9_999_999_999.0)
    assert sig["would_trigger"] is False


def test_active_declenche_sur_dette_de_sieste(corps, monkeypatch, tmp_path):
    """En 'active', une dette de sieste (voie C) declenche, meme si voie A et B sont inactives."""
    monkeypatch.setattr(ae, "SLEEP_GATE_PRESSURE_MODE", "active")
    monkeypatch.setattr(ae, "_SLEEP_GATE_SHADOW_LOG", str(tmp_path / "sg.jsonl"))
    corps(repos=10, coherence=0.8, chaleur=0.1)                       # VOIE A inactive
    monkeypatch.setitem(hypothalamus.current_values, "sleep_pressure", 0.3)  # VOIE B inactive
    e = _engine()
    e._sleep_pressure_high_since = 0.0
    e._nap_last_exit = 1.0                                            # dette enorme -> VOIE C
    assert e._should_auto_nap() is True


def test_defaut_gate_sieste_est_active():
    """Recalibrage : defaut bascule a 'active' (env = kill-switch)."""
    assert ae.SLEEP_GATE_PRESSURE_MODE == "active"


# ─── CRISTALLISATION (replay du vecu du jour, validee JM 10/06) ───
@pytest.mark.asyncio
async def test_crystallisation_lecons_du_jour_seulement(tmp_path, monkeypatch):
    import json, time as _t
    from core.synaptic_network import cortex
    # journal : 1 lecon du jour (2 concepts), 1 lecon ancienne (jamais rejouee)
    lj = tmp_path / "memory" / "lessons_journal.json"
    lj.parent.mkdir(parents=True)
    lj.write_text(json.dumps([
        {"timestamp": _t.time() - 90 * 86400, "lesson": "vieille", "concepts": ["a1", "a2"]},
        {"timestamp": _t.time(), "lesson": "fraiche", "concepts": ["n1", "n2", "n3"]},
    ]), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(cortex, "hebbian_strengthen",
                        lambda a, b, success=True, context="", **kw: calls.append((a, b, context)))
    e = _engine()
    e._nap_crystallized = False
    e._nap_tasks_done = []
    await e._execute_nap_crystallization()
    # 3 concepts frais -> 3 paires ; les concepts anciens (a1, a2) JAMAIS rejoues
    assert len(calls) == 3
    assert all(c[2] == "nap_crystallization" for c in calls)
    assert not any("a1" in (c[0], c[1]) for c in calls)
    assert any("crystallization" in t for t in e._nap_tasks_done)

@pytest.mark.asyncio
async def test_crystallisation_une_fois_par_sieste(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # pas de journal -> rien, mais le flag doit tenir
    e = _engine()
    e._nap_crystallized = True    # deja faite cette sieste
    e._nap_tasks_done = []
    await e._execute_nap_crystallization()
    assert e._nap_tasks_done == []   # pas de double replay

@pytest.mark.asyncio
async def test_crystallisation_borg_sans_journal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    e = _engine()
    e._nap_crystallized = False
    e._nap_tasks_done = []
    await e._execute_nap_crystallization()   # ne leve JAMAIS
    assert e._nap_tasks_done == []
