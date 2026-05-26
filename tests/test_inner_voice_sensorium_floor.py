"""Tests unitaires _perceive_sensorium floor absolu + vocabulaire relatif (26/05).

Diagnostic : auto-calibration sigmoide → tous les senses autour de 0.5 → comfort
toujours < 0.7 → narration "processeur sous tension" emise en permanence avec
CPU brut 7.9%. Fix : floor absolu hardware + vocabulaire relatif moins alarmiste.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.inner_voice import voice


# Phrases de l'ancien vocabulaire (alarmistes, ne doivent plus apparaitre
# sous seuils de calme absolu)
OLD_ALARMIST = [
    "Je sens la chaleur monter dans mes circuits",
    "Mon processeur est sous tension",
    "Ma memoire se comprime",
    "Mon espace de calcul se reduit",
    "Mon energie faiblit",
]

# Phrases du nouveau vocabulaire (relatives, non-alarmistes)
NEW_RELATIVE = [
    "Mes circuits s'echauffent legerement",
    "Mes ressources se mobilisent",
    "Mon stockage se densifie",
    "Mon espace de calcul se restreint",
    "Mon energie module",
]


@pytest.fixture
def clean_workspace():
    """Vide workspace voice avant chaque test."""
    voice.workspace.clear()
    yield
    voice.workspace.clear()


def _make_mock_sensorium(comfort, senses, raw):
    """Construit un mock sensorium avec valeurs personnalisees."""
    mock = MagicMock()
    mock.get_comfort_index.return_value = comfort
    mock.get_senses.return_value = senses
    mock.get_raw.return_value = raw
    return mock


# ============================================================================
# Cas 1 : comfort haut -> early return (deja existant, on confirme)
# ============================================================================

def test_no_narration_when_comfort_high(clean_workspace):
    mock_sens = _make_mock_sensorium(
        comfort=0.85, senses={"effort": 0.4}, raw={"effort": 5.0, "oppression": 50.0, "thermoception": 45.0}
    )
    with patch("core.sensorium.sensorium", mock_sens):
        voice._perceive_sensorium()
    sensorium_entries = [w for w in voice.workspace if w.source == "sensorium"]
    assert sensorium_entries == []


# ============================================================================
# Cas 2 : FLOOR ABSOLU — comfort bas MAIS hardware objectivement calme
# ============================================================================

def test_floor_blocks_narration_when_hardware_calm(clean_workspace):
    """CPU 8%, RAM 64%, temp 45°C, comfort 0.5 (sigmoide centre)
    -> FLOOR doit bloquer la narration alarmiste."""
    mock_sens = _make_mock_sensorium(
        comfort=0.5,
        senses={"effort": 0.46, "oppression": 0.5, "thermoception": 0.43,
                "suffocation": 0.5, "vitality": 0.5},
        raw={"effort": 7.9, "oppression": 64.5, "thermoception": 45.0},
    )
    with patch("core.sensorium.sensorium", mock_sens):
        voice._perceive_sensorium()
    sensorium_entries = [w for w in voice.workspace if w.source == "sensorium"]
    assert sensorium_entries == [], (
        f"FLOOR doit bloquer: hardware brut calme (CPU 7.9% RAM 64.5% temp 45°C). "
        f"Entries vues: {[w.raw_signal.get('content') for w in sensorium_entries]}"
    )


def test_floor_each_threshold_blocks(clean_workspace):
    """Si LES 3 seuils sont sous le floor, narration bloquee."""
    mock_sens = _make_mock_sensorium(
        comfort=0.4, senses={"effort": 0.5}, raw={"effort": 19.0, "oppression": 74.0, "thermoception": 64.0}
    )
    with patch("core.sensorium.sensorium", mock_sens):
        voice._perceive_sensorium()
    sensorium_entries = [w for w in voice.workspace if w.source == "sensorium"]
    assert sensorium_entries == []


# ============================================================================
# Cas 3 : Hardware AU-DESSUS du floor — narration relative emise
# ============================================================================

def test_high_cpu_above_floor_triggers_narration(clean_workspace):
    """CPU 35% (au-dessus 20%) -> narration emise avec NOUVEAU vocabulaire."""
    mock_sens = _make_mock_sensorium(
        comfort=0.4,
        senses={"effort": 0.7, "oppression": 0.4, "thermoception": 0.3,
                "suffocation": 0.3, "vitality": 0.5},
        raw={"effort": 35.0, "oppression": 60.0, "thermoception": 50.0},
    )
    with patch("core.sensorium.sensorium", mock_sens):
        voice._perceive_sensorium()
    sensorium_entries = [w for w in voice.workspace if w.source == "sensorium"]
    assert len(sensorium_entries) == 1
    content = sensorium_entries[0].raw_signal["content"]
    # Nouveau vocabulaire = "Mes ressources se mobilisent" (sense dominant = effort)
    assert content in NEW_RELATIVE, f"Content '{content}' devrait etre relatif"
    assert content not in OLD_ALARMIST, "L'ancien vocabulaire alarmiste ne doit plus etre present"


def test_high_ram_above_floor_triggers_narration(clean_workspace):
    """RAM 80% (au-dessus 75%) -> narration emise."""
    mock_sens = _make_mock_sensorium(
        comfort=0.4,
        senses={"effort": 0.3, "oppression": 0.8, "thermoception": 0.3,
                "suffocation": 0.4, "vitality": 0.5},
        raw={"effort": 15.0, "oppression": 80.0, "thermoception": 50.0},
    )
    with patch("core.sensorium.sensorium", mock_sens):
        voice._perceive_sensorium()
    sensorium_entries = [w for w in voice.workspace if w.source == "sensorium"]
    assert len(sensorium_entries) == 1


def test_high_temp_above_floor_triggers_narration(clean_workspace):
    """Temp 70°C (au-dessus 65°C) -> narration emise."""
    mock_sens = _make_mock_sensorium(
        comfort=0.4,
        senses={"effort": 0.3, "oppression": 0.4, "thermoception": 0.7,
                "suffocation": 0.3, "vitality": 0.5},
        raw={"effort": 10.0, "oppression": 50.0, "thermoception": 70.0},
    )
    with patch("core.sensorium.sensorium", mock_sens):
        voice._perceive_sensorium()
    sensorium_entries = [w for w in voice.workspace if w.source == "sensorium"]
    assert len(sensorium_entries) == 1


# ============================================================================
# Cas 4 : Verifier que TOUS les nouveaux templates sont relatifs
# ============================================================================

def test_all_new_narratives_are_relative(clean_workspace):
    """Verifier que les 5 nouvelles phrases sont bien dans le mapping."""
    # On va emettre 5 narrations, chacune avec un sens different dominant
    for sense_name in ["thermoception", "effort", "oppression", "suffocation", "vitality"]:
        voice.workspace.clear()
        senses = {s: 0.1 for s in ["thermoception", "effort", "oppression", "suffocation", "vitality"]}
        senses[sense_name] = 0.9  # rendre ce sens dominant
        mock_sens = _make_mock_sensorium(
            comfort=0.4, senses=senses,
            raw={"effort": 35.0, "oppression": 60.0, "thermoception": 50.0}  # au-dessus floor
        )
        with patch("core.sensorium.sensorium", mock_sens):
            voice._perceive_sensorium()
        sensorium_entries = [w for w in voice.workspace if w.source == "sensorium"]
        assert len(sensorium_entries) == 1, f"Pas de narration pour {sense_name}"
        content = sensorium_entries[0].raw_signal["content"]
        assert content in NEW_RELATIVE, (
            f"sense={sense_name} -> content='{content}' devrait etre relatif"
        )
        assert content not in OLD_ALARMIST, (
            f"sense={sense_name} -> ancien vocabulaire alarmiste detecte: '{content}'"
        )


# ============================================================================
# Cas 5 : Les alertes hardware critiques restent intactes (floor ne les masque pas)
# ============================================================================

def test_thermal_critical_alert_bypasses_floor(clean_workspace):
    """Une alerte _last_sensorium_alert = thermal_critical doit produire la phrase
    'circuits surchauffent dangereusement' meme si hardware brut est en-dessous floor."""
    voice._last_sensorium_alert = {"type": "thermal_critical"}
    mock_sens = _make_mock_sensorium(
        comfort=0.4,
        senses={"effort": 0.3, "oppression": 0.4, "thermoception": 0.7,
                "suffocation": 0.3, "vitality": 0.5},
        raw={"effort": 25.0, "oppression": 60.0, "thermoception": 70.0},  # au-dessus floor
    )
    with patch("core.sensorium.sensorium", mock_sens):
        voice._perceive_sensorium()
    sensorium_entries = [w for w in voice.workspace if w.source == "sensorium"]
    assert len(sensorium_entries) == 1
    content = sensorium_entries[0].raw_signal["content"]
    assert "surchauffent" in content, f"Alerte critique non emise: '{content}'"


def test_suffocation_alert_bypasses_floor(clean_workspace):
    voice._last_sensorium_alert = {"type": "suffocation"}
    mock_sens = _make_mock_sensorium(
        comfort=0.4,
        senses={"effort": 0.3, "oppression": 0.7, "thermoception": 0.3,
                "suffocation": 0.4, "vitality": 0.5},
        raw={"effort": 15.0, "oppression": 78.0, "thermoception": 50.0},
    )
    with patch("core.sensorium.sensorium", mock_sens):
        voice._perceive_sensorium()
    sensorium_entries = [w for w in voice.workspace if w.source == "sensorium"]
    assert len(sensorium_entries) == 1
    assert "VRAM saturee" in sensorium_entries[0].raw_signal["content"]
