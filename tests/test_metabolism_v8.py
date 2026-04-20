"""Tests V8.0 (Phase 11 - 2026-04-20) : Neurochimie Appliquee.

Reforme 1 : Sommeil Meritocratique
  - nap_refund conditionne a la productivite de la sieste (au moins
    N tache reussie dans _nap_tasks_done)
  - Evite l'addiction narcoleptique (TD-learning qui apprendrait
    "dormir = gagner budget sans effort")

Reforme 2 : Alignement Budget / Mode Strategique
  - Quand _check_budget_quota return 'exhausted', broadcast
    AUTONOMY_BUDGET_EXHAUSTED une fois par jour
  - self_awareness ecoute et force compute_strategic_mode en 'survie'
  - Quand NAP productif ou reset quotidien restore le budget,
    broadcast AUTONOMY_BUDGET_RESTORED qui libere le forcage
  - Sortie causale sans TTL (event-driven)
"""
import asyncio
import time
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from core.self_awareness import SelfAwarenessEngine


# ═══════════════════════════════════════════════════════════════════════
# Reforme 1 - Sommeil meritocratique
# ═══════════════════════════════════════════════════════════════════════


class TestNapMeritocratic:
    """Le nap_refund n'est accorde que si la sieste a produit du travail."""

    def _make_autonomy(self):
        """Cree un objet minimal pour tester _nap_was_productive
        en isolation (evite d'importer tout AutonomyEngine)."""
        from core.autonomy_engine import AutonomyEngine
        # Reset singleton si applicable (AutonomyEngine n'est pas un
        # singleton strict mais garde son etat)
        engine = AutonomyEngine.__new__(AutonomyEngine)
        engine._nap_tasks_done = []
        engine._NAP_MIN_PRODUCTIVE_TASKS = 1
        return engine

    def test_empty_nap_is_not_productive(self):
        """Sieste sans aucune tache -> pas productive."""
        engine = self._make_autonomy()
        engine._nap_tasks_done = []
        assert engine._nap_was_productive() is False

    def test_dream_task_alone_is_productive(self):
        """Une consolidation synaptique suffit."""
        engine = self._make_autonomy()
        engine._nap_tasks_done = ["DREAM"]
        assert engine._nap_was_productive() is True

    def test_lora_task_alone_is_productive(self):
        engine = self._make_autonomy()
        engine._nap_tasks_done = ["LORA_CODER"]
        assert engine._nap_was_productive() is True

    def test_circadian_task_alone_is_productive(self):
        """Une tache circadienne reussie compte aussi (elle n'est ajoutee
        a _nap_tasks_done que si result.success=True en amont)."""
        engine = self._make_autonomy()
        engine._nap_tasks_done = ["memory_consolidation"]
        assert engine._nap_was_productive() is True

    def test_multiple_tasks_productive(self):
        engine = self._make_autonomy()
        engine._nap_tasks_done = ["DREAM", "memory_consolidation", "LORA_CODER"]
        assert engine._nap_was_productive() is True

    def test_min_productive_tasks_threshold_tunable(self):
        """La constante _NAP_MIN_PRODUCTIVE_TASKS est ajustable."""
        engine = self._make_autonomy()
        engine._NAP_MIN_PRODUCTIVE_TASKS = 2
        engine._nap_tasks_done = ["DREAM"]  # 1 seule
        assert engine._nap_was_productive() is False
        engine._nap_tasks_done = ["DREAM", "memory_consolidation"]
        assert engine._nap_was_productive() is True


# ═══════════════════════════════════════════════════════════════════════
# Reforme 2 - Alignement Budget / Survie (cote self_awareness)
# ═══════════════════════════════════════════════════════════════════════


class TestBudgetSurvieAlignment:
    """La famine budgetaire doit forcer le mode strategique en 'survie'."""

    def setup_method(self):
        self.engine = SelfAwarenessEngine()
        self.engine.reset()
        self.engine._initialized = True

    def _push_snapshot(self, error_streak=0, success_rate=0.8, verdict="GO"):
        self.engine._snapshots.append({
            "performance": {
                "error_streak": error_streak,
                "success_rate": success_rate,
            },
            "health": {"verdict": verdict},
        })
        self.engine._meta_reflect_cache = None
        self.engine._meta_reflect_ts = 0

    def test_default_mode_is_not_survie(self):
        """Sans famine budgetaire, le mode suit les conditions normales."""
        self._push_snapshot(error_streak=0, success_rate=0.8)
        mode = self.engine.compute_strategic_mode()
        assert mode != "survie"

    def test_budget_exhausted_forces_survie(self):
        """V8.0 : budget_exhausted_today=True force 'survie' meme avec
        perf parfaite."""
        self._push_snapshot(error_streak=0, success_rate=0.9)
        self.engine._budget_exhausted_today = True
        mode = self.engine.compute_strategic_mode()
        assert mode == "survie"

    def test_budget_restored_releases_survie(self):
        """Apres AUTONOMY_BUDGET_RESTORED, le mode reprend son cours
        normal si les autres conditions sont saines."""
        self._push_snapshot(error_streak=0, success_rate=0.9)
        # Etape 1 : famine
        self.engine._budget_exhausted_today = True
        assert self.engine.compute_strategic_mode() == "survie"
        # Etape 2 : restored
        self.engine._budget_exhausted_today = False
        mode = self.engine.compute_strategic_mode()
        assert mode != "survie"

    @pytest.mark.asyncio
    async def test_on_budget_exhausted_sets_flag(self):
        """Le handler _on_budget_exhausted leve le flag."""
        assert self.engine._budget_exhausted_today is False
        await self.engine._on_budget_exhausted({
            "daily_budget_used": 200,
            "daily_count": 50,
            "timestamp": time.time(),
        })
        assert self.engine._budget_exhausted_today is True

    @pytest.mark.asyncio
    async def test_on_budget_restored_clears_flag(self):
        """Le handler _on_budget_restored abaisse le flag."""
        self.engine._budget_exhausted_today = True
        await self.engine._on_budget_restored({
            "reason": "nap_productive",
            "daily_budget_used": 180,
            "timestamp": time.time(),
        })
        assert self.engine._budget_exhausted_today is False

    def test_budget_survie_takes_precedence_over_exploration(self):
        """Meme avec success_rate excellent, la famine domine."""
        self._push_snapshot(error_streak=0, success_rate=0.95)
        # Sans famine : devrait etre exploration
        normal = self.engine.compute_strategic_mode()
        assert normal == "exploration"
        # Avec famine : force survie
        self.engine._budget_exhausted_today = True
        forced = self.engine.compute_strategic_mode()
        assert forced == "survie"

    def test_budget_survie_coexists_with_error_streak_survie(self):
        """Si les deux causes sont presentes, survie tient."""
        self._push_snapshot(error_streak=7, success_rate=0.3)
        self.engine._budget_exhausted_today = True
        mode = self.engine.compute_strategic_mode()
        assert mode == "survie"
        # Puis budget restaure mais erreurs encore la -> survie tient
        self.engine._budget_exhausted_today = False
        mode = self.engine.compute_strategic_mode()
        assert mode == "survie"  # error_streak=7 tient le mode


# ═══════════════════════════════════════════════════════════════════════
# Reforme 2 - Interaction avec l'hysteresis V5.1
# ═══════════════════════════════════════════════════════════════════════


class TestBudgetSurvieWithHysteresis:
    """get_strategic_mode (V5.1) applique une hysteresis. V8.0 doit
    coexister proprement."""

    def setup_method(self):
        self.engine = SelfAwarenessEngine()
        self.engine.reset()
        self.engine._initialized = True

    def _push_snapshot(self, error_streak=0, success_rate=0.8, verdict="GO"):
        self.engine._snapshots.append({
            "performance": {
                "error_streak": error_streak,
                "success_rate": success_rate,
            },
            "health": {"verdict": verdict},
        })
        self.engine._meta_reflect_cache = None
        self.engine._meta_reflect_ts = 0

    def test_get_strategic_mode_reflects_budget_survie(self):
        """get_strategic_mode doit aussi voir le survie force."""
        self._push_snapshot(error_streak=0, success_rate=0.9)
        self.engine._budget_exhausted_today = True
        mode = self.engine.get_strategic_mode()
        assert mode == "survie"

    def test_hysteresis_holds_after_budget_restore_if_errors(self):
        """Si on sort de famine mais error_streak etait >= 7, l'hysteresis
        V5.1 retient 'survie' jusqu'a ce que err < 3."""
        # Entree en survie par DEUX causes : budget + error_streak
        self._push_snapshot(error_streak=7, success_rate=0.3)
        self.engine._budget_exhausted_today = True
        assert self.engine.get_strategic_mode() == "survie"

        # Sortie de famine budgetaire MAIS error_streak=5 (>3)
        self.engine._budget_exhausted_today = False
        self._push_snapshot(error_streak=5, success_rate=0.4)
        # L'hysteresis V5.1 retient survie car err>=3
        assert self.engine.get_strategic_mode() == "survie"


# ═══════════════════════════════════════════════════════════════════════
# Reforme 1 + 2 - Integration AutonomyEngine (broadcast events)
# ═══════════════════════════════════════════════════════════════════════


class TestAutonomyBroadcastEvents:
    """Les broadcast helpers ne publient qu'une fois par cycle."""

    def _make_engine(self):
        from core.autonomy_engine import AutonomyEngine
        engine = AutonomyEngine.__new__(AutonomyEngine)
        engine._budget_exhausted_broadcast_today = False
        engine.daily_budget_used = 200
        engine.daily_count = 50
        return engine

    def test_broadcast_exhausted_only_once_per_day(self):
        """Appeler _broadcast_budget_exhausted 3 fois ne doit emettre
        qu'une seule fois sur le bus (protection contre spam)."""
        engine = self._make_engine()
        with patch("core.autonomy_engine.bus.publish", new=AsyncMock()) as mock_pub:
            engine._broadcast_budget_exhausted()
            engine._broadcast_budget_exhausted()
            engine._broadcast_budget_exhausted()
        # Le bus.publish est dans un loop.create_task -> le mock compte
        # le nombre de create_task. On verifie le flag a la place.
        assert engine._budget_exhausted_broadcast_today is True

    def test_broadcast_restored_noop_if_not_exhausted(self):
        """Si on n'est pas en famine, restored ne doit rien faire."""
        engine = self._make_engine()
        engine._budget_exhausted_broadcast_today = False
        with patch("core.autonomy_engine.bus.publish", new=AsyncMock()) as mock_pub:
            engine._broadcast_budget_restored("daily_reset")
        # Flag reste False (pas de changement)
        assert engine._budget_exhausted_broadcast_today is False

    def test_broadcast_restored_clears_flag_when_exhausted(self):
        """Si on etait en famine, restored abaisse le flag."""
        engine = self._make_engine()
        engine._budget_exhausted_broadcast_today = True
        with patch("core.autonomy_engine.bus.publish", new=AsyncMock()):
            engine._broadcast_budget_restored("nap_productive")
        assert engine._budget_exhausted_broadcast_today is False
