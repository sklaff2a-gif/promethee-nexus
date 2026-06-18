"""Plancher quotidien garanti d'immersion (Couche 26quinquies, 2026-06-18).

Diagnostic 18/06 : IMMERSION_DOMAIN est scoree (+4.0 d'appetit) mais ne gagne
JAMAIS la selection contre l'ecole (8-14) -> _execute_immersion_domain jamais
appele -> portes A/B/C jamais atteintes -> nourriture jamais digeree
(stale=999h), famine epistemique 689h en hausse.

Coeur teste : la POLITIQUE PURE du plancher (food present ET famine critique).
Le forcage reel reste borne par les gardes de l'appelant (1x/jour,
daily_count>=10, pas d'intent deja force) et les portes metaboliques A/B/C
restent juges de l'execution.
"""

from core.autonomy_engine import AutonomyEngine


class TestImmersionFloorPolicy:
    def test_arme_si_nourriture_et_famine_critique(self):
        """Cas nominal : repas en file + famine >= 48h -> plancher arme."""
        assert AutonomyEngine._immersion_floor_should_arm(food_count=4, famine_hours=689.0) is True

    def test_pas_de_nourriture_pas_de_plancher(self):
        """Frigo vide : meme une famine extreme n'arme rien (rien a digerer)."""
        assert AutonomyEngine._immersion_floor_should_arm(food_count=0, famine_hours=999.0) is False

    def test_famine_sous_seuil_pas_de_plancher(self):
        """Nourriture presente mais famine non critique : l'appetit opportuniste
        suffit, le plancher (mecanisme de survie) ne se declenche pas."""
        assert AutonomyEngine._immersion_floor_should_arm(food_count=5, famine_hours=12.0) is False

    def test_seuils_exacts_inclusifs(self):
        """Les seuils MIN_FOOD et FAMINE_HOURS sont inclusifs (>=)."""
        f = AutonomyEngine.IMMERSION_FLOOR_MIN_FOOD
        h = AutonomyEngine.IMMERSION_FLOOR_FAMINE_HOURS
        assert AutonomyEngine._immersion_floor_should_arm(f, h) is True
        assert AutonomyEngine._immersion_floor_should_arm(f - 1, h) is False
        assert AutonomyEngine._immersion_floor_should_arm(f, h - 0.1) is False


class TestImmersionFloorWiring:
    def test_flag_initialise_a_false(self):
        """Le flag du plancher existe et demarre a False (rien arme au boot)."""
        from core.autonomy_engine import autonomy
        assert hasattr(autonomy, "_daily_immersion_done")
        assert isinstance(autonomy._daily_immersion_done, bool)

    def test_reset_quotidien_remet_le_flag(self):
        """Le rollover quotidien (_check_daily_budget) rearme le plancher."""
        import datetime
        from core.autonomy_engine import autonomy
        autonomy._daily_immersion_done = True
        # Forcer un rollover : last_reset_day dans le passe
        autonomy.last_reset_day = datetime.date(2000, 1, 1)
        autonomy._check_daily_budget()
        assert autonomy._daily_immersion_done is False
