"""
Tests de l'interrupteur d'urgence : BREATH_GASP → reptilien → checkpoint.

Couvre :
- save_all_organs : sauvegarde tous les organes, ISOLE les exceptions,
  ignore les organes sans .save(), et renvoie le signal {total, saved, failed}.
- failed NON VIDE = signal de catastrophe-dans-la-catastrophe pour le reptile.
- _on_breath_gasp (nocicepteur) : un gasp de SURVIE monte la menace (bornée),
  un gasp ÉPISTÉMIQUE est ignoré, alarme_sourde est exclu (anti-boucle),
  cooldown par symptôme.
- FREEZE déclenche le checkpoint d'urgence (save_all_organs) avant le gel.
"""

import time

import pytest

from core import organ_registry as reg
from core.organ_registry import save_all_organs


# ------------------------------------------------------------------
# Doubles de test
# ------------------------------------------------------------------

class _SpyOrgan:
    def __init__(self):
        self.saved = False

    def save(self):
        self.saved = True


class _FailingOrgan:
    def __init__(self):
        self.attempted = False

    def save(self):
        self.attempted = True
        raise IOError("disque plein — impossible de verrouiller l'état")


class _NoSaveOrgan:
    """Organe sans persistance (ex: la respiration, mémoire de travail)."""
    pass


@pytest.fixture
def clean_registry():
    """Isole le registre : snapshot → vide → restaure après le test."""
    snapshot = reg.get_all_organs()
    reg.reset_registry()
    yield reg
    reg.reset_registry()
    for name, organ in snapshot.items():
        reg.register_organ(name, organ)


@pytest.fixture
def reptile_clean():
    """Reptile réel, menace remise à zéro et cooldowns vidés autour du test."""
    from core.reptilian_core import reptile
    saved_threat = reptile.threat_level
    reptile.threat_level = 0.0
    reptile._gasp_cooldowns.clear()
    yield reptile
    reptile.threat_level = saved_threat
    reptile._gasp_cooldowns.clear()


# ------------------------------------------------------------------
# save_all_organs
# ------------------------------------------------------------------

def test_save_all_organs_calls_every_save(clean_registry):
    s1, s2 = _SpyOrgan(), _SpyOrgan()
    reg.register_organ("spy1", s1)
    reg.register_organ("spy2", s2)

    result = save_all_organs()

    assert s1.saved and s2.saved
    assert result["saved"] == 2
    assert result["failed"] == []
    assert result["total"] == 2


def test_save_all_organs_isolates_exceptions(clean_registry):
    """Un organe qui échoue n'empêche pas les autres de sauvegarder."""
    good = _SpyOrgan()
    bad = _FailingOrgan()
    reg.register_organ("bon", good)
    reg.register_organ("casse", bad)

    result = save_all_organs()

    assert good.saved is True          # le bon a quand même sauvegardé
    assert bad.attempted is True       # le mauvais a été tenté
    assert result["saved"] == 1
    assert result["failed"] == ["casse"]
    assert result["total"] == 2


def test_save_all_organs_signal_failed_is_truthy(clean_registry):
    """Le signal minimal au reptile : failed NON VIDE = avertissement."""
    reg.register_organ("casse", _FailingOrgan())
    result = save_all_organs()
    assert result["failed"]            # truthy → le reptile sait qu'il faut alerter


def test_save_all_organs_skips_organs_without_save(clean_registry):
    """Un organe sans .save() (respiration) est ignoré, pas compté."""
    reg.register_organ("spy", _SpyOrgan())
    reg.register_organ("volatile", _NoSaveOrgan())

    result = save_all_organs()

    assert result["total"] == 1        # seul l'organe avec .save() compte
    assert result["saved"] == 1
    assert result["failed"] == []


# ------------------------------------------------------------------
# _on_breath_gasp — nocicepteur
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_survival_gasp_raises_threat(reptile_clean):
    await reptile_clean._on_breath_gasp({"symptome": "pouls_emballe", "saillance": 4.0})
    assert reptile_clean.threat_level == 4.0


@pytest.mark.asyncio
async def test_survival_gasp_threat_is_capped(reptile_clean):
    """Un seul souffle ne mène jamais directement au FREEZE (cap 6.0)."""
    await reptile_clean._on_breath_gasp({"symptome": "surchauffe", "saillance": 99.0})
    assert reptile_clean.threat_level == 6.0


@pytest.mark.asyncio
async def test_epistemic_gasp_ignored(reptile_clean):
    """Un symptôme épistémique (STABILITE) n'escalade PAS la menace."""
    await reptile_clean._on_breath_gasp({"symptome": "vertige_du_sol", "saillance": 9.0})
    assert reptile_clean.threat_level == 0.0


@pytest.mark.asyncio
async def test_alarme_sourde_excluded_anti_loop(reptile_clean):
    """alarme_sourde (signal propre du reptilien) est exclu → pas de boucle."""
    await reptile_clean._on_breath_gasp({"symptome": "alarme_sourde", "saillance": 9.0})
    assert reptile_clean.threat_level == 0.0


@pytest.mark.asyncio
async def test_gasp_cooldown_blocks_reprocessing(reptile_clean):
    await reptile_clean._on_breath_gasp({"symptome": "pouls_emballe", "saillance": 4.0})
    assert reptile_clean.threat_level == 4.0

    # On abaisse la menace puis on renvoie le même gasp dans le cooldown :
    reptile_clean.threat_level = 0.0
    await reptile_clean._on_breath_gasp({"symptome": "pouls_emballe", "saillance": 5.0})
    assert reptile_clean.threat_level == 0.0  # bloqué par le cooldown

    # Cooldown expiré → le gasp repasse
    reptile_clean._gasp_cooldowns.clear()
    await reptile_clean._on_breath_gasp({"symptome": "pouls_emballe", "saillance": 5.0})
    assert reptile_clean.threat_level == 5.0


# ------------------------------------------------------------------
# FREEZE → checkpoint d'urgence
# ------------------------------------------------------------------

def test_freeze_triggers_emergency_checkpoint(clean_registry, reptile_clean):
    """Le réflexe FREEZE verrouille l'état de tous les organes avant le gel."""
    spy = _SpyOrgan()
    reg.register_organ("spy", spy)

    reptile_clean._activate_freeze(time.time())

    assert spy.saved is True
    assert reptile_clean._freeze_until > 0  # le gel est bien armé ensuite


def test_freeze_survives_a_failing_organ(clean_registry, reptile_clean):
    """Un organe qui échoue pendant le checkpoint ne fait pas planter le FREEZE."""
    reg.register_organ("casse", _FailingOrgan())
    reg.register_organ("bon", _SpyOrgan())

    # Ne doit lever AUCUNE exception malgré l'organe défaillant.
    reptile_clean._activate_freeze(time.time())
    assert reptile_clean._freeze_until > 0
