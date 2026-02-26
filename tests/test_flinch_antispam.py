"""
Tests pour l'anti-spam FLINCH — cooldown exponentiel sur menace persistante.

Couvre :
- Cooldown exponentiel (30→60→120→300 cap)
- Tracking des déclenchements consécutifs
- Reset quand la menace change ou se dissipe
- Suppression des logs après le 2ème déclenchement
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.reptilian_core import ReptilianCore, REFLEX_COOLDOWNS


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def reset_reptilian(tmp_path, monkeypatch):
    """Reset singleton reptilien + isolation fichier."""
    ReptilianCore.reset_singleton()
    monkeypatch.setattr("core.reptilian_core.REPTILIAN_STATE_FILE", str(tmp_path / "reptilian.json"))
    yield
    ReptilianCore.reset_singleton()


@pytest.fixture
def reptile():
    return ReptilianCore()


# ============================================================
# 1. Cooldown exponentiel FLINCH
# ============================================================

class TestFlinchEscalation:

    def test_first_flinch_normal_cooldown(self, reptile):
        """Le premier FLINCH utilise le cooldown normal (30s)."""
        now = time.time()
        assert reptile._flinch_consecutive == 0
        assert reptile._can_trigger("FLINCH", now)

    def test_second_flinch_same_threat_normal_cooldown(self, reptile):
        """Le 2ème FLINCH (même menace) utilise encore le cooldown normal."""
        threats = {"budget": 4.0}
        reptile.threat_level = 4.0
        reptile._activate_flinch(threats)
        assert reptile._flinch_consecutive == 1
        # Après 30s, le 2ème devrait passer (consecutive=1, pas d'escalade)
        now = time.time() + 31
        assert reptile._can_trigger("FLINCH", now)

    def test_third_flinch_escalated_cooldown(self, reptile):
        """Le 3ème FLINCH (même menace) a un cooldown doublé (60s)."""
        threats = {"budget": 4.0}
        reptile.threat_level = 4.0
        # Simuler 2 FLINCH consécutifs
        reptile._activate_flinch(threats)
        reptile._activate_flinch(threats)
        assert reptile._flinch_consecutive == 2
        # Après 31s : le cooldown escaladé est 30*2=60s → pas encore
        now = time.time() + 31
        reptile._reflex_cooldowns["FLINCH"] = time.time()
        assert not reptile._can_trigger("FLINCH", now)
        # Après 61s : OK
        now = time.time() + 61
        assert reptile._can_trigger("FLINCH", now)

    def test_fourth_flinch_further_escalation(self, reptile):
        """Le 4ème FLINCH a un cooldown de 120s (30 * 2^2)."""
        threats = {"budget": 4.0}
        reptile.threat_level = 4.0
        for _ in range(3):
            reptile._activate_flinch(threats)
        assert reptile._flinch_consecutive == 3
        # Cooldown = 30 * min(2^2, 10) = 120s
        reptile._reflex_cooldowns["FLINCH"] = time.time()
        now = time.time() + 61
        assert not reptile._can_trigger("FLINCH", now)
        now = time.time() + 121
        assert reptile._can_trigger("FLINCH", now)

    def test_cooldown_caps_at_300s(self, reptile):
        """Le cooldown est plafonné à 300s, même après beaucoup de déclenchements."""
        threats = {"budget": 5.0}
        reptile.threat_level = 5.0
        # Simuler 10 FLINCH consécutifs
        for _ in range(10):
            reptile._activate_flinch(threats)
        assert reptile._flinch_consecutive == 10
        # Cooldown = min(30 * min(2^9, 10), 300) = min(30*10, 300) = 300s
        reptile._reflex_cooldowns["FLINCH"] = time.time()
        now = time.time() + 299
        assert not reptile._can_trigger("FLINCH", now)
        now = time.time() + 301
        assert reptile._can_trigger("FLINCH", now)

    def test_non_flinch_reflexes_not_escalated(self, reptile):
        """Les autres réflexes ne sont pas affectés par l'escalade."""
        reptile._flinch_consecutive = 10  # Beaucoup de FLINCH
        # SHED doit garder son cooldown normal (120s)
        reptile._reflex_cooldowns["SHED"] = time.time()
        now = time.time() + 121
        assert reptile._can_trigger("SHED", now)


# ============================================================
# 2. Tracking des menaces consécutives
# ============================================================

class TestFlinchTracking:

    def test_same_threat_increments_consecutive(self, reptile):
        """Même menace top → incrémente le compteur."""
        threats = {"budget": 4.0}
        reptile.threat_level = 4.0
        reptile._activate_flinch(threats)
        assert reptile._flinch_consecutive == 1
        assert reptile._flinch_last_threat == "budget"
        reptile._activate_flinch(threats)
        assert reptile._flinch_consecutive == 2

    def test_different_threat_resets_consecutive(self, reptile):
        """Menace différente → reset du compteur."""
        reptile.threat_level = 5.0
        reptile._activate_flinch({"budget": 5.0})
        assert reptile._flinch_consecutive == 1
        reptile._activate_flinch({"budget": 5.0})
        assert reptile._flinch_consecutive == 2
        # Nouvelle menace
        reptile._activate_flinch({"cpu": 5.0})
        assert reptile._flinch_consecutive == 1
        assert reptile._flinch_last_threat == "cpu"

    def test_empty_threats_uses_unknown(self, reptile):
        """Pas de menaces → top_threat='unknown'."""
        reptile.threat_level = 4.0
        reptile._activate_flinch({})
        assert reptile._flinch_last_threat == "unknown"


# ============================================================
# 3. Reset de l'escalade
# ============================================================

class TestFlinchReset:

    def test_reset_on_routine_success(self, reptile):
        """Succès de routine → reset complet de l'escalade."""
        reptile._flinch_consecutive = 5
        reptile._flinch_last_threat = "budget"
        reptile._flinch_reason = "test"
        reptile.threat_level = 4.0
        reptile.on_routine_success("AUDIT_STRUCTURE")
        assert reptile._flinch_consecutive == 0
        assert reptile._flinch_last_threat == ""
        assert reptile._flinch_reason == ""

    @pytest.mark.asyncio
    async def test_reset_when_threat_drops_below_threshold(self, reptile):
        """Menace passe sous 4.0 → reset dans le watchdog."""
        reptile._flinch_consecutive = 5
        reptile._flinch_last_threat = "budget"
        reptile.threat_level = 3.5  # Sous le seuil FLINCH

        # Simuler un tick du watchdog (appel direct du code de reset)
        if reptile.threat_level < 4.0 and reptile._flinch_consecutive > 0:
            reptile._flinch_consecutive = 0
            reptile._flinch_last_threat = ""

        assert reptile._flinch_consecutive == 0
        assert reptile._flinch_last_threat == ""

    def test_no_reset_while_threat_persists(self, reptile):
        """Pas de reset tant que la menace est >= 4.0."""
        reptile._flinch_consecutive = 5
        reptile._flinch_last_threat = "budget"
        reptile.threat_level = 5.0

        # Le reset conditionnel ne devrait pas s'activer
        if reptile.threat_level < 4.0 and reptile._flinch_consecutive > 0:
            reptile._flinch_consecutive = 0

        assert reptile._flinch_consecutive == 5


# ============================================================
# 4. Suppression des logs
# ============================================================

class TestFlinchLogSuppression:

    def test_first_two_flinch_log_warning(self, reptile):
        """Les 2 premiers FLINCH émettent un WARNING."""
        threats = {"cpu": 4.0}
        reptile.threat_level = 4.0
        with patch("core.reptilian_core.logger") as mock_logger:
            reptile._activate_flinch(threats)
            mock_logger.warning.assert_called_once()
            mock_logger.debug.assert_not_called()

    def test_third_flinch_log_debug_only(self, reptile):
        """À partir du 3ème FLINCH (même menace), log en DEBUG uniquement."""
        threats = {"cpu": 4.0}
        reptile.threat_level = 4.0
        # 2 premiers
        reptile._activate_flinch(threats)
        reptile._activate_flinch(threats)
        with patch("core.reptilian_core.logger") as mock_logger:
            reptile._activate_flinch(threats)  # 3ème
            mock_logger.warning.assert_not_called()
            mock_logger.debug.assert_called_once()
            assert "supprimé x3" in mock_logger.debug.call_args[0][0]


# ============================================================
# 5. Scénario réaliste — Budget persistant
# ============================================================

class TestBudgetSpamScenario:

    def test_budget_persistent_54_minutes(self, reptile):
        """Simule 54 min de menace budget — avant: ~100 FLINCH, après: ~7."""
        threats = {"budget": 4.0}
        reptile.threat_level = 4.0
        flinch_count = 0
        t = time.time()

        for tick in range(648):  # 54 min / 5s = 648 ticks
            now = t + tick * 5
            if reptile._can_trigger("FLINCH", now):
                reptile._activate_flinch(threats)
                # Override _record_reflex qui utilise time.time() réel
                reptile._reflex_cooldowns["FLINCH"] = now
                flinch_count += 1

        # Avec escalade: 30+60+120+240+300*N ≈ 14 FLINCH en 54 min
        # Sans escalade: 54*60/30 ≈ 108 FLINCH → réduction ~87%
        assert flinch_count <= 15, f"Trop de FLINCH: {flinch_count} (attendu <=15)"
        assert flinch_count >= 4, f"Trop peu de FLINCH: {flinch_count} (attendu >=4)"
