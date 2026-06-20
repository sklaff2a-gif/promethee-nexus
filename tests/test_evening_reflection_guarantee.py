# -*- coding: utf-8 -*-
"""Garantie vespérale d'EVENING_REFLECTION (Couche 26f, 20/06).

Diag 20/06 : la réflexion n'avait qu'un canal fiable (post-budget), mort depuis le
16/06 → plus aucune introspection vespérale depuis le 15/06. Fix : forçage garanti le
soir (mirror plancher immersion) + persistance des flags (survit aux reboots).
"""
import datetime

from core.autonomy_engine import AutonomyEngine, AutonomyStatePersistence


class TestReflectionGuaranteePolicy:
    def test_arme_le_soir_si_non_faite(self):
        h = AutonomyEngine.REFLECTION_EVENING_HOUR
        assert AutonomyEngine._reflection_should_arm(h, reflection_done=False, forced_busy=False) is True
        assert AutonomyEngine._reflection_should_arm(23, reflection_done=False, forced_busy=False) is True

    def test_pas_avant_le_soir(self):
        assert AutonomyEngine._reflection_should_arm(10, reflection_done=False, forced_busy=False) is False
        assert AutonomyEngine._reflection_should_arm(17, reflection_done=False, forced_busy=False) is False

    def test_deja_faite_ne_re_arme_pas(self):
        assert AutonomyEngine._reflection_should_arm(20, reflection_done=True, forced_busy=False) is False

    def test_intent_deja_force_cede_la_place(self):
        # un autre intent déjà forcé ne doit pas être écrasé
        assert AutonomyEngine._reflection_should_arm(20, reflection_done=False, forced_busy=True) is False


class TestReflectionPersistence:
    def test_persist_state_inclut_les_flags(self, monkeypatch):
        # capture le dict passé à save() SANS écrire sur disque (pas de pollution état prod)
        from core.autonomy_engine import autonomy
        captured = {}
        monkeypatch.setattr(AutonomyStatePersistence, "save",
                            staticmethod(lambda state, path=None: captured.update(state)))
        autonomy._daily_reflection_done = True
        autonomy._last_reflection_ts = 1781900000.0
        autonomy._persist_state()
        assert captured.get("_daily_reflection_done") is True
        assert captured.get("_last_reflection_ts") == 1781900000.0

    def test_rollover_quotidien_reset_le_flag(self):
        from core.autonomy_engine import autonomy
        autonomy._daily_reflection_done = True
        autonomy.last_reset_day = datetime.date(2000, 1, 1)   # force un jour neuf
        autonomy._check_daily_budget()
        assert autonomy._daily_reflection_done is False
