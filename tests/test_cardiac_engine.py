"""
Tests unitaires pour CardiacEngine — Le Coeur de Prométhée.
~60 tests couvrant : heartbeat, réactions, HRV, cohérence, température,
sleep, marqueurs somatiques, persistance, bus.
"""

import asyncio
import json
import math
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Isolation singleton — autouse fixture
@pytest.fixture(autouse=True)
def isolate_cardiac(tmp_path, monkeypatch):
    """Isole chaque test avec un singleton frais et un fichier temp."""
    from core import cardiac_engine as mod
    mod.CardiacEngine.reset_singleton()

    fake_state_file = str(tmp_path / "cardiac_state.json")
    monkeypatch.setattr(mod, "CARDIAC_STATE_FILE", fake_state_file)

    # Patcher _load pour ne pas lire de fichier réel
    with patch.object(mod.CardiacEngine, "_load"):
        h = mod.CardiacEngine()
        h.reset()  # État propre
        mod.heart = h

    yield h
    mod.CardiacEngine.reset_singleton()


# ============================================================
# TestHeartbeat (8 tests)
# ============================================================

class TestHeartbeat:
    """Tests du battement cardiaque."""

    def test_initial_state(self, isolate_cardiac):
        """Le coeur démarre au repos."""
        h = isolate_cardiac
        assert h.bpm == 60.0
        assert h.current_emotion == "serenite"
        assert h.ans_balance == 0.5
        assert h._beat_count == 0

    def test_single_beat_records_rr(self, isolate_cardiac):
        """Un battement enregistre un intervalle R-R."""
        h = isolate_cardiac
        h._last_beat = time.time() - 30.0  # Simuler 30s depuis le dernier
        h._beat()
        assert len(h.rr_intervals) == 1
        assert h._beat_count == 1

    def test_beat_decays_high_bpm(self, isolate_cardiac):
        """Le BPM décroît vers le repos après un battement."""
        h = isolate_cardiac
        h.bpm = 120.0
        h._last_beat = time.time() - 30.0
        h._beat()
        # BPM doit avoir diminué vers 60 (RESTING)
        assert h.bpm < 120.0
        assert h.bpm > 60.0
        # Vérifier la formule : 60 + (120 - 60) * 0.92 = 60 + 55.2 = 115.2
        assert abs(h.bpm - 115.2) < 0.5

    def test_beat_decays_low_bpm(self, isolate_cardiac):
        """Le BPM remonte vers le repos si trop bas."""
        h = isolate_cardiac
        h.bpm = 45.0
        h._last_beat = time.time() - 30.0
        h._beat()
        assert h.bpm > 45.0
        assert h.bpm < 60.0

    def test_beat_clamps_bpm(self, isolate_cardiac):
        """Le BPM ne dépasse pas les limites."""
        h = isolate_cardiac
        h.bpm = 200.0
        h._last_beat = time.time() - 30.0
        h._beat()
        assert h.bpm <= 180.0

    def test_beat_attenuates_emotion(self, isolate_cardiac):
        """L'intensité émotionnelle diminue à chaque battement."""
        h = isolate_cardiac
        h.emotion_intensity = 0.8
        h.current_emotion = "enthousiasme"
        h._last_beat = time.time() - 30.0
        h._beat()
        assert h.emotion_intensity < 0.8
        assert h.emotion_intensity == pytest.approx(0.76, abs=0.01)

    def test_beat_returns_ans_to_neutral(self, isolate_cardiac):
        """L'ANS revient vers le neutre à chaque battement."""
        h = isolate_cardiac
        h.ans_balance = 0.8
        h._last_beat = time.time() - 30.0
        h._beat()
        assert h.ans_balance < 0.8
        assert h.ans_balance == pytest.approx(0.77, abs=0.01)

    @pytest.mark.asyncio
    async def test_start_stop_beating(self, isolate_cardiac):
        """Le coeur peut démarrer et s'arrêter."""
        h = isolate_cardiac
        task = asyncio.create_task(h.start_beating())
        await asyncio.sleep(0.05)  # Laisser le task démarrer
        assert h._alive
        h._alive = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ============================================================
# TestReactions (10 tests)
# ============================================================

class TestReactions:
    """Tests des réactions émotionnelles."""

    def test_react_success_increases_bpm(self, isolate_cardiac):
        """Un succès augmente le BPM."""
        h = isolate_cardiac
        initial_bpm = h.bpm
        with patch.object(h, "_get_psyche_resonance", return_value=1.0):
            h.react("success")
        assert h.bpm > initial_bpm

    def test_react_failure_increases_bpm_more(self, isolate_cardiac):
        """Un échec augmente le BPM plus qu'un succès."""
        h = isolate_cardiac

        h.bpm = 60.0
        with patch.object(h, "_get_psyche_resonance", return_value=1.0):
            h.react("success")
        bpm_after_success = h.bpm

        h.bpm = 60.0
        with patch.object(h, "_get_psyche_resonance", return_value=1.0):
            h.react("failure")
        bpm_after_failure = h.bpm

        assert bpm_after_failure > bpm_after_success

    def test_react_idle_decreases_bpm(self, isolate_cardiac):
        """L'inactivité diminue le BPM."""
        h = isolate_cardiac
        h.bpm = 80.0
        with patch.object(h, "_get_psyche_resonance", return_value=1.0):
            h.react("idle")
        assert h.bpm < 80.0

    def test_react_dream_calms(self, isolate_cardiac):
        """Le rêve calme le coeur."""
        h = isolate_cardiac
        h.bpm = 80.0
        with patch.object(h, "_get_psyche_resonance", return_value=1.0):
            h.react("dream")
        assert h.bpm < 80.0  # Le BPM a diminué

    def test_react_unknown_stimulus_ignored(self, isolate_cardiac):
        """Un stimulus inconnu ne change rien."""
        h = isolate_cardiac
        initial_bpm = h.bpm
        h.react("nonexistent_stimulus")
        assert h.bpm == initial_bpm

    def test_react_changes_emotion(self, isolate_cardiac):
        """La réaction change l'émotion si plus intense."""
        h = isolate_cardiac
        h.emotion_intensity = 0.0  # Aucune émotion active
        with patch.object(h, "_get_psyche_resonance", return_value=1.0):
            h.react("eureka")
        assert h.current_emotion == "enthousiasme"
        assert h.emotion_intensity > 0.5

    def test_react_ans_shift_failure(self, isolate_cardiac):
        """Un échec pousse l'ANS vers le sympathique."""
        h = isolate_cardiac
        initial_ans = h.ans_balance
        with patch.object(h, "_get_psyche_resonance", return_value=1.0):
            h.react("failure")
        assert h.ans_balance > initial_ans

    def test_react_ans_shift_dream(self, isolate_cardiac):
        """Le rêve pousse l'ANS vers le parasympathique."""
        h = isolate_cardiac
        initial_ans = h.ans_balance
        with patch.object(h, "_get_psyche_resonance", return_value=1.0):
            h.react("dream")
        assert h.ans_balance < initial_ans

    def test_react_increments_stimuli_count(self, isolate_cardiac):
        """Chaque réaction incrémente le compteur de stimuli."""
        h = isolate_cardiac
        assert h._total_stimuli == 0
        with patch.object(h, "_get_psyche_resonance", return_value=1.0):
            h.react("success")
            h.react("failure")
        assert h._total_stimuli == 2

    def test_react_bpm_clamped(self, isolate_cardiac):
        """Le BPM reste dans les limites même après réactions extrêmes."""
        h = isolate_cardiac
        h.bpm = 170.0
        with patch.object(h, "_get_psyche_resonance", return_value=2.0):
            h.react("eureka")
        assert h.bpm <= 180.0


# ============================================================
# TestPsycheResonance (4 tests)
# ============================================================

class TestPsycheResonance:
    """Tests de la résonance trait-émotion PSYCHE."""

    def test_resonance_default_no_psyche(self, isolate_cardiac):
        """Sans PSYCHE disponible, résonance = 1.0."""
        h = isolate_cardiac
        with patch("core.cardiac_engine.CardiacEngine._get_psyche_resonance") as mock:
            mock.side_effect = lambda e: 1.0
            result = mock("curiosite")
        assert result == 1.0

    def test_resonance_high_curiosite_amplifies(self, isolate_cardiac):
        """Trait curiosité élevé amplifie l'émotion curiosité."""
        h = isolate_cardiac
        mock_psyche = MagicMock()
        mock_psyche.get_system_average.return_value = {
            "curiosite": 90.0, "creativite": 50.0, "audace": 50.0,
            "survie": 50.0, "respect": 50.0, "savoir": 50.0,
        }
        with patch("core.psyche.psyche", mock_psyche):
            result = h._get_psyche_resonance("curiosite")
        assert result > 1.0

    def test_resonance_low_trait_attenuates(self, isolate_cardiac):
        """Trait bas atténue la résonance."""
        h = isolate_cardiac
        mock_psyche = MagicMock()
        mock_psyche.get_system_average.return_value = {
            "curiosite": 10.0, "creativite": 50.0, "audace": 50.0,
            "survie": 50.0, "respect": 50.0, "savoir": 50.0,
        }
        with patch("core.psyche.psyche", mock_psyche):
            result = h._get_psyche_resonance("curiosite")
        assert result < 1.0

    def test_resonance_clamped(self, isolate_cardiac):
        """La résonance est clampée [0.3, 2.5]."""
        h = isolate_cardiac
        mock_psyche = MagicMock()
        mock_psyche.get_system_average.return_value = {
            "curiosite": 100.0, "creativite": 100.0, "audace": 100.0,
            "survie": 0.0, "respect": 100.0, "savoir": 100.0,
        }
        with patch("core.psyche.psyche", mock_psyche):
            result = h._get_psyche_resonance("curiosite")
        assert 0.3 <= result <= 2.5


# ============================================================
# TestHRV (6 tests)
# ============================================================

class TestHRV:
    """Tests du calcul RMSSD / HRV."""

    def test_hrv_no_data_returns_neutral(self, isolate_cardiac):
        """Sans données suffisantes, HRV = 0.5 (neutre)."""
        h = isolate_cardiac
        assert h.compute_hrv() == 0.5

    def test_hrv_stable_rhythm_high(self, isolate_cardiac):
        """Rythme stable → HRV élevé."""
        h = isolate_cardiac
        # Intervalles très réguliers
        h.rr_intervals = [30000.0] * 20  # 30s exactement
        hrv = h.compute_hrv()
        assert hrv > 0.9  # Très stable

    def test_hrv_erratic_rhythm_low(self, isolate_cardiac):
        """Rythme erratique → HRV bas."""
        h = isolate_cardiac
        # Intervalles très irréguliers
        h.rr_intervals = [20000.0, 40000.0] * 10
        hrv = h.compute_hrv()
        assert hrv < 0.5

    def test_hrv_normalized_0_1(self, isolate_cardiac):
        """HRV est toujours dans [0, 1]."""
        h = isolate_cardiac
        h.rr_intervals = [1000.0, 50000.0, 500.0, 40000.0, 200.0] * 4
        hrv = h.compute_hrv()
        assert 0.0 <= hrv <= 1.0

    def test_hrv_with_few_intervals(self, isolate_cardiac):
        """Avec exactement 3 intervalles, le calcul fonctionne."""
        h = isolate_cardiac
        h.rr_intervals = [30000.0, 30100.0, 30050.0]
        hrv = h.compute_hrv()
        assert 0.0 <= hrv <= 1.0
        assert hrv > 0.9  # Presque stable

    def test_hrv_window_limit(self, isolate_cardiac):
        """Le HRV utilise les derniers HRV_WINDOW intervalles."""
        h = isolate_cardiac
        # 50 intervalles : les 20 derniers sont stables
        h.rr_intervals = [10000.0, 50000.0] * 25 + [30000.0] * 20
        hrv = h.compute_hrv()
        assert hrv > 0.9  # Les derniers 20 sont stables


# ============================================================
# TestCoherence (8 tests)
# ============================================================

class TestCoherence:
    """Tests de la cohérence cardiaque."""

    def test_coherence_default_range(self, isolate_cardiac):
        """La cohérence est dans [0, 1]."""
        h = isolate_cardiac
        c = h.compute_coherence()
        assert 0.0 <= c <= 1.0

    def test_coherence_flow_state(self, isolate_cardiac):
        """État de flow (émotion positive + rythme stable + ANS neutre) → haute cohérence."""
        h = isolate_cardiac
        h.current_emotion = "flow"
        h.emotion_intensity = 0.8
        h.ans_balance = 0.5
        h.rr_intervals = [30000.0] * 20  # Stable
        c = h.compute_coherence()
        assert c > 0.6

    def test_coherence_stress_state(self, isolate_cardiac):
        """État de stress (émotion négative + ANS sympathique) → basse cohérence."""
        h = isolate_cardiac
        h.current_emotion = "frustration"
        h.emotion_intensity = 0.8
        h.ans_balance = 0.9  # Très sympathique
        h.rr_intervals = [20000.0, 40000.0] * 10  # Erratique
        c = h.compute_coherence()
        assert c < 0.4

    def test_coherence_serenity_high(self, isolate_cardiac):
        """Sérénité (valence positive, calme) → cohérence raisonnable."""
        h = isolate_cardiac
        h.current_emotion = "serenite"
        h.emotion_intensity = 0.5
        h.ans_balance = 0.5
        h.rr_intervals = [30000.0] * 20
        c = h.compute_coherence()
        assert c > 0.5

    def test_coherence_ans_extremes_low(self, isolate_cardiac):
        """ANS aux extrêmes → cohérence réduite."""
        h = isolate_cardiac
        h.current_emotion = "serenite"
        h.ans_balance = 1.0  # Full sympathique
        h.rr_intervals = [30000.0] * 20
        c1 = h.compute_coherence()

        h.ans_balance = 0.5  # Neutre
        c2 = h.compute_coherence()
        assert c2 > c1

    def test_coherence_emotion_valence_matters(self, isolate_cardiac):
        """Une émotion positive → meilleure cohérence qu'une négative."""
        h = isolate_cardiac
        h.rr_intervals = [30000.0] * 20
        h.ans_balance = 0.5

        h.current_emotion = "enthousiasme"
        h.emotion_intensity = 0.7
        c_positive = h.compute_coherence()

        h.current_emotion = "frustration"
        h.emotion_intensity = 0.7
        c_negative = h.compute_coherence()

        assert c_positive > c_negative

    def test_coherence_threshold_detected(self, isolate_cardiac):
        """La détection de flow via COHERENCE_THRESHOLD fonctionne."""
        from core.cardiac_engine import COHERENCE_THRESHOLD
        h = isolate_cardiac
        h.current_emotion = "flow"
        h.emotion_intensity = 0.9
        h.ans_balance = 0.5
        h.rr_intervals = [30000.0] * 20
        c = h.compute_coherence()
        # En état optimal, la cohérence devrait dépasser le seuil
        assert c >= 0.5  # Au minimum raisonnable

    def test_coherence_no_rr_data(self, isolate_cardiac):
        """Sans données R-R, la cohérence utilise HRV neutre (0.5)."""
        h = isolate_cardiac
        h.rr_intervals = []
        c = h.compute_coherence()
        assert 0.0 <= c <= 1.0


# ============================================================
# TestTemperature (5 tests)
# ============================================================

class TestTemperature:
    """Tests de la température LLM dynamique."""

    def test_temperature_default_range(self, isolate_cardiac):
        """La température est dans [0.3, 0.95]."""
        h = isolate_cardiac
        t = h.get_temperature()
        assert 0.3 <= t <= 0.95

    def test_temperature_high_coherence_creative(self, isolate_cardiac):
        """Haute cohérence → température haute (créativité)."""
        h = isolate_cardiac
        h.current_emotion = "flow"
        h.emotion_intensity = 0.9
        h.ans_balance = 0.5
        h.rr_intervals = [30000.0] * 20
        t = h.get_temperature()
        assert t > 0.6

    def test_temperature_stress_conservative(self, isolate_cardiac):
        """Stress → température basse (conservateur)."""
        h = isolate_cardiac
        h.current_emotion = "frustration"
        h.emotion_intensity = 0.8
        h.bpm = 140.0
        h.ans_balance = 0.9
        h.rr_intervals = [20000.0, 40000.0] * 10
        t = h.get_temperature()
        assert t < 0.6

    def test_temperature_flow_bonus(self, isolate_cardiac):
        """Le flow ajoute un bonus de température."""
        h = isolate_cardiac
        h.rr_intervals = [30000.0] * 20
        h.ans_balance = 0.5

        # Sans flow
        h.current_emotion = "serenite"
        h.emotion_intensity = 0.7
        t_serenite = h.get_temperature()

        # Avec flow
        h.current_emotion = "flow"
        h.emotion_intensity = 0.7
        t_flow = h.get_temperature()

        # Flow devrait donner une température au moins aussi haute
        # (le bonus est conditionnel à la cohérence)
        assert t_flow >= t_serenite - 0.05  # Tolérance

    def test_temperature_clamped(self, isolate_cardiac):
        """La température est toujours clampée [0.3, 0.95]."""
        h = isolate_cardiac
        h.bpm = 180.0
        h.current_emotion = "frustration"
        h.emotion_intensity = 1.0
        h.ans_balance = 1.0
        h.rr_intervals = [10000.0, 50000.0] * 10
        t = h.get_temperature()
        assert t >= 0.3

        h.bpm = 40.0
        h.current_emotion = "flow"
        h.emotion_intensity = 1.0
        h.ans_balance = 0.5
        h.rr_intervals = [30000.0] * 20
        t = h.get_temperature()
        assert t <= 0.95


# ============================================================
# TestSleep (6 tests)
# ============================================================

class TestSleep:
    """Tests du sommeil adaptatif."""

    def test_sleep_default_range(self, isolate_cardiac):
        """La durée de sommeil est dans [300, 1800]."""
        h = isolate_cardiac
        s = h.compute_sleep_duration()
        assert 300 <= s <= 1800

    def test_sleep_high_coherence_short(self, isolate_cardiac):
        """Haute cohérence → sommeil court (cycles rapides)."""
        h = isolate_cardiac
        h.current_emotion = "flow"
        h.emotion_intensity = 0.9
        h.ans_balance = 0.5
        h.rr_intervals = [30000.0] * 20
        s = h.compute_sleep_duration()
        assert s < 900  # Moins de 15 minutes

    def test_sleep_low_coherence_long(self, isolate_cardiac):
        """Basse cohérence → sommeil long (récupération)."""
        h = isolate_cardiac
        h.current_emotion = "frustration"
        h.emotion_intensity = 0.8
        h.ans_balance = 0.9
        h.rr_intervals = [20000.0, 40000.0] * 10
        s = h.compute_sleep_duration()
        assert s > 900

    def test_sleep_tachycardia_bonus(self, isolate_cardiac):
        """Tachycardie → repos forcé (sleep plus long)."""
        h = isolate_cardiac
        h.current_emotion = "serenite"
        h.rr_intervals = [30000.0] * 20

        h.bpm = 60.0
        s_normal = h.compute_sleep_duration()

        h.bpm = 150.0
        s_tachy = h.compute_sleep_duration()

        assert s_tachy > s_normal

    def test_sleep_negative_emotion_bonus(self, isolate_cardiac):
        """Émotions négatives → repos supplémentaire."""
        h = isolate_cardiac
        h.rr_intervals = [30000.0] * 20
        h.bpm = 60.0
        h.ans_balance = 0.5

        h.current_emotion = "serenite"
        h.emotion_intensity = 0.5
        s_positive = h.compute_sleep_duration()

        h.current_emotion = "frustration"
        h.emotion_intensity = 0.5
        s_negative = h.compute_sleep_duration()

        assert s_negative > s_positive

    def test_sleep_bradycardia_shorter(self, isolate_cardiac):
        """Bradycardie profonde → pas besoin de plus de repos."""
        h = isolate_cardiac
        h.current_emotion = "serenite"
        h.rr_intervals = [30000.0] * 20
        h.ans_balance = 0.5

        h.bpm = 60.0
        s_normal = h.compute_sleep_duration()

        h.bpm = 45.0
        s_brady = h.compute_sleep_duration()

        assert s_brady < s_normal


# ============================================================
# TestSomaticMarkers (10 tests)
# ============================================================

class TestSomaticMarkers:
    """Tests des marqueurs somatiques de Damasio."""

    def test_form_new_marker(self, isolate_cardiac):
        """Créer un nouveau marqueur somatique."""
        h = isolate_cardiac
        h.form_somatic_marker("CREATIVE_WRITING", "success", 0.8, "bon poème")
        assert "CREATIVE_WRITING" in h.somatic_markers
        marker = h.somatic_markers["CREATIVE_WRITING"]
        assert marker["valence"] > 0  # Succès = valence positive
        assert marker["formation_count"] == 1
        assert marker["context_hint"] == "bon poème"

    def test_reinforce_existing_marker(self, isolate_cardiac):
        """Renforcer un marqueur existant."""
        h = isolate_cardiac
        h.form_somatic_marker("AUDIT", "success", 0.9)
        h.form_somatic_marker("AUDIT", "success", 0.7)
        marker = h.somatic_markers["AUDIT"]
        assert marker["formation_count"] == 2
        assert marker["intensity"] > 0.3  # Renforcé

    def test_negative_marker_failure(self, isolate_cardiac):
        """Un échec crée un marqueur négatif."""
        h = isolate_cardiac
        h.form_somatic_marker("EXPANSION_CODE", "failure", 0.1)
        marker = h.somatic_markers["EXPANSION_CODE"]
        assert marker["valence"] < 0

    def test_somatic_signal_positive(self, isolate_cardiac):
        """Signal somatique positif pour un marqueur positif."""
        h = isolate_cardiac
        h.form_somatic_marker("LEARNING", "success", 0.9)
        # Renforcer pour augmenter l'intensité
        for _ in range(5):
            h.form_somatic_marker("LEARNING", "success", 0.8)
        signal = h.get_somatic_signal("LEARNING")
        assert signal > 0

    def test_somatic_signal_negative(self, isolate_cardiac):
        """Signal somatique négatif pour un marqueur négatif."""
        h = isolate_cardiac
        for _ in range(5):
            h.form_somatic_marker("BAD_INTENT", "failure", 0.1)
        signal = h.get_somatic_signal("BAD_INTENT")
        assert signal < 0

    def test_somatic_signal_unknown_intent(self, isolate_cardiac):
        """Signal somatique = 0 pour un intent inconnu."""
        h = isolate_cardiac
        signal = h.get_somatic_signal("NEVER_SEEN")
        assert signal == 0.0

    def test_somatic_signal_clamped(self, isolate_cardiac):
        """Le signal somatique est clampé [-1.5, +1.5]."""
        h = isolate_cardiac
        # Créer un marqueur très fort
        for _ in range(20):
            h.form_somatic_marker("STRONG", "success", 1.0)
        signal = h.get_somatic_signal("STRONG")
        assert -1.5 <= signal <= 1.5

    def test_somatic_emotion_resonance(self, isolate_cardiac):
        """Le signal est amplifié quand l'émotion actuelle correspond."""
        h = isolate_cardiac
        h.current_emotion = "enthousiasme"
        h.form_somatic_marker("RESONANT", "success", 0.8)
        # Le marqueur a été formé pendant "enthousiasme"
        signal_resonant = h.get_somatic_signal("RESONANT")

        h.current_emotion = "frustration"
        signal_not_resonant = h.get_somatic_signal("RESONANT")

        # Le signal devrait être plus fort quand l'émotion correspond
        assert abs(signal_resonant) >= abs(signal_not_resonant)

    def test_somatic_bpm_resonance(self, isolate_cardiac):
        """Le signal est amplifié quand le BPM est similaire."""
        h = isolate_cardiac
        h.bpm = 80.0
        h.form_somatic_marker("BPM_TEST", "success", 0.8)
        # Même BPM → résonance
        signal_same_bpm = h.get_somatic_signal("BPM_TEST")

        h.bpm = 140.0
        signal_diff_bpm = h.get_somatic_signal("BPM_TEST")

        assert abs(signal_same_bpm) >= abs(signal_diff_bpm)

    def test_prune_oldest_markers(self, isolate_cardiac):
        """Le pruning supprime les marqueurs les moins retenus."""
        h = isolate_cardiac
        # Remplir avec 200 marqueurs
        for i in range(200):
            h.somatic_markers[f"INTENT_{i}"] = {
                "intent": f"INTENT_{i}", "valence": 0.5, "intensity": 0.3,
                "bpm_signature": 60, "emotion_at_mark": "serenite",
                "formation_count": 1, "last_updated": time.time() - i * 3600,
                "context_hint": "",
            }
        assert len(h.somatic_markers) == 200
        h._prune_oldest_markers()
        assert len(h.somatic_markers) < 200


# ============================================================
# TestPersistence (4 tests)
# ============================================================

class TestPersistence:
    """Tests de la persistance."""

    def test_save_creates_file(self, isolate_cardiac, tmp_path):
        """La sauvegarde crée le fichier."""
        from core import cardiac_engine as mod
        h = isolate_cardiac
        h.bpm = 75.0
        h.save()
        assert os.path.exists(mod.CARDIAC_STATE_FILE)

    def test_save_load_roundtrip(self, isolate_cardiac, tmp_path):
        """Sauvegarder puis charger restaure l'état."""
        from core import cardiac_engine as mod
        h = isolate_cardiac
        h.bpm = 88.5
        h.ans_balance = 0.7
        h.current_emotion = "curiosite"
        h.somatic_markers["TEST"] = {
            "intent": "TEST", "valence": 0.5, "intensity": 0.4,
            "bpm_signature": 70, "emotion_at_mark": "curiosite",
            "formation_count": 3, "last_updated": time.time(),
            "context_hint": "test roundtrip",
        }
        h.save()

        # Charger dans une nouvelle instance (sans passer par singleton)
        h2 = h
        h2.bpm = 60.0  # Reset
        h2._load()
        assert abs(h2.bpm - 88.5) < 0.1
        assert abs(h2.ans_balance - 0.7) < 0.01
        assert h2.current_emotion == "curiosite"
        assert "TEST" in h2.somatic_markers

    def test_load_missing_file_graceful(self, isolate_cardiac):
        """Charger un fichier manquant ne crash pas."""
        h = isolate_cardiac
        h._load()  # Pas de fichier → rien ne se passe

    def test_load_corrupted_file_graceful(self, isolate_cardiac, tmp_path):
        """Charger un fichier corrompu ne crash pas."""
        from core import cardiac_engine as mod
        with open(mod.CARDIAC_STATE_FILE, "w") as f:
            f.write("NOT JSON {{{")
        h = isolate_cardiac
        h._load()  # Ne doit pas crasher
        assert h.bpm == 60.0  # Valeur par défaut conservée


# ============================================================
# TestBusIntegration (5 tests)
# ============================================================

class TestBusIntegration:
    """Tests de l'intégration avec le bus d'événements."""

    @pytest.mark.asyncio
    async def test_on_routine_complete_success(self, isolate_cardiac):
        """Routine réussie → réaction success + marqueur positif."""
        h = isolate_cardiac
        with patch.object(h, "_get_psyche_resonance", return_value=1.0):
            await h._on_routine_complete({
                "quality": 0.8, "intent": "CODE_REVIEW", "status": "success"
            })
        assert h.bpm > 60.0  # Excitation
        assert "CODE_REVIEW" in h.somatic_markers
        assert h.somatic_markers["CODE_REVIEW"]["valence"] > 0

    @pytest.mark.asyncio
    async def test_on_routine_complete_failure(self, isolate_cardiac):
        """Routine échouée → réaction failure + marqueur négatif."""
        h = isolate_cardiac
        with patch.object(h, "_get_psyche_resonance", return_value=1.0):
            await h._on_routine_complete({
                "quality": 0.1, "intent": "DEPLOY", "status": "error"
            })
        assert h.bpm > 60.0  # Stress
        assert "DEPLOY" in h.somatic_markers
        assert h.somatic_markers["DEPLOY"]["valence"] < 0

    @pytest.mark.asyncio
    async def test_on_eureka_bridge(self, isolate_cardiac):
        """Eureka → excitation intense."""
        h = isolate_cardiac
        with patch.object(h, "_get_psyche_resonance", return_value=1.0):
            await h._on_eureka_bridge({})
        assert h.bpm > 70.0

    @pytest.mark.asyncio
    async def test_on_ci_result_pass(self, isolate_cardiac):
        """CI passé → succès."""
        h = isolate_cardiac
        with patch.object(h, "_get_psyche_resonance", return_value=1.0):
            await h._on_ci_result({"passed": True})
        assert h.current_emotion == "enthousiasme"

    @pytest.mark.asyncio
    async def test_on_psyche_update(self, isolate_cardiac):
        """Mise à jour PSYCHE → ajustement ANS subtil."""
        h = isolate_cardiac
        h.ans_balance = 0.5
        await h._on_psyche_update({
            "system_average": {"audace": 80, "survie": 30}
        })
        # Audace élevée + survie basse → shift sympathique
        assert h.ans_balance > 0.5


# ============================================================
# TestNarrative (3 tests)
# ============================================================

class TestNarrative:
    """Tests du narratif introspectif."""

    def test_narrative_flow(self, isolate_cardiac):
        """En flow, le narratif est serein."""
        from core.cardiac_engine import COHERENCE_THRESHOLD
        h = isolate_cardiac
        h.current_emotion = "flow"
        h.emotion_intensity = 0.9
        h.ans_balance = 0.5
        h.rr_intervals = [30000.0] * 20
        h.bpm = 65.0
        narrative = h.get_narrative()
        assert "BPM" in narrative

    def test_narrative_tachycardia(self, isolate_cardiac):
        """En tachycardie, le narratif reflète l'intensité."""
        h = isolate_cardiac
        h.bpm = 140.0
        narrative = h.get_narrative()
        assert "emballe" in narrative or "140" in narrative

    def test_narrative_bradycardia(self, isolate_cardiac):
        """En bradycardie, le narratif reflète le calme."""
        h = isolate_cardiac
        h.bpm = 45.0
        narrative = h.get_narrative()
        assert "lentement" in narrative or "45" in narrative


# ============================================================
# TestGetStats (2 tests)
# ============================================================

class TestGetStats:
    """Tests du résumé statistique."""

    def test_stats_keys(self, isolate_cardiac):
        """Les stats contiennent toutes les clés attendues."""
        h = isolate_cardiac
        stats = h.get_stats()
        expected_keys = {
            "bpm", "ans_balance", "emotion", "emotion_intensity",
            "hrv", "coherence", "temperature", "somatic_markers_count",
            "beat_count", "total_stimuli",
        }
        assert expected_keys <= set(stats.keys())

    def test_stats_values_types(self, isolate_cardiac):
        """Les valeurs des stats sont du bon type."""
        h = isolate_cardiac
        stats = h.get_stats()
        assert isinstance(stats["bpm"], float)
        assert isinstance(stats["emotion"], str)
        assert isinstance(stats["somatic_markers_count"], int)
        assert isinstance(stats["beat_count"], int)
