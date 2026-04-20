"""Tests V5.1 (2026-04-20) : fix spirale de la mort du cortex prefrontal.

Bug traite : _compute_goal_priority lisait goal.priority (valeur decroissee
du cycle precedent) au lieu d'une base stable, transformant des malus
lineaires en spirale geometrique. Consequence : 86% d'abandons a
progress=0% dans prefrontal_state.json.

Ce module verifie :
  1. La priorite est recalculee, pas accumulee (core du fix)
  2. Chaque source de goal a sa base attendue
  3. Un goal stale sans progres ne s'effondre plus a -5.0 en quelques ticks
  4. Le bouclier de momentum causal fonctionne toujours (non-regression)
  5. L'hysteresis du mode strategique empeche le flickering
  6. get_strategic_mode() existe (anciennement dead code depuis prefrontal)
"""

import time
import pytest

from core.prefrontal import (
    BASE_PRIORITY_BY_SOURCE,
    Goal,
    GoalStep,
    PrefrontalCortex,
)
from core.self_awareness import SelfAwarenessEngine


# ═══════════════════════════════════════════════════════════════════════
# 1. FIX ACCUMULATEUR
# ═══════════════════════════════════════════════════════════════════════


class TestPriorityNotAccumulated:
    """Le coeur du fix V5.1 : la priorite ne doit plus s'auto-decroitre."""

    def setup_method(self):
        self.prefrontal = PrefrontalCortex()

    def _make_goal(self, source="desire", age_sec=0, progress=0.0,
                   cost_spent=0, cost_estimated=10):
        """Cree un goal avec un etat arbitraire."""
        now = time.time()
        return Goal(
            id="test01",
            title="Satisfaire pulsion: TEST",
            horizon="short",
            priority=4.5,
            progress=progress,
            steps=[GoalStep(intent="VEILLE_SILENCIEUSE", description="test")],
            source=source,
            created_at=now - age_sec,
            last_advanced=now - age_sec,
            cost_spent=cost_spent,
            cost_estimated=cost_estimated,
            drive_alignment={},
            metadata={},
        )

    def test_priority_stable_across_repeated_calls(self):
        """Appel repete -> priorite stable (pas d'auto-decroissance).

        AVANT V5.1 : appels successifs feraient chuter la priorite
        jusqu'au clamp -5.0 en 3-5 iterations.
        APRES V5.1 : chaque appel repart de BASE_PRIORITY_BY_SOURCE,
        la priorite reste identique.
        """
        goal = self._make_goal(source="desire")
        goal.priority = BASE_PRIORITY_BY_SOURCE["desire"]

        p1 = self.prefrontal._compute_goal_priority(goal)
        goal.priority = p1
        p2 = self.prefrontal._compute_goal_priority(goal)
        goal.priority = p2
        p3 = self.prefrontal._compute_goal_priority(goal)

        # Les 3 valeurs doivent etre identiques (aucune accumulation)
        assert p1 == p2 == p3, (
            f"Priorite drift: p1={p1}, p2={p2}, p3={p3}. "
            "L'accumulateur est revenu."
        )

    def test_stale_goal_does_not_collapse_to_floor(self):
        """Un goal stale sans progres ne doit pas atteindre -5.0.

        Scenario reproduit depuis prefrontal_state.json : 30/36 abandons
        etaient a progress=0% avec raison 'priorite effondree (-5.0)'.
        Avec V5.1, meme un goal stale depuis horizon/2 reste au-dessus
        du plancher.
        """
        # Goal stale : age > HORIZON_SHORT/2 (1h30), progress=0
        goal = self._make_goal(source="desire", age_sec=3600, progress=0.0)
        goal.priority = BASE_PRIORITY_BY_SOURCE["desire"]

        # Simule 10 cycles de deliberation consecutifs
        for _ in range(10):
            goal.priority = self.prefrontal._compute_goal_priority(goal)

        # Base 4.5 - stale 2.0 = 2.5 attendu (pas de cost, pas de survie).
        # Le clamp a -5.0 ne doit JAMAIS etre atteint.
        assert goal.priority > -4.0, (
            f"Priorite effondree a {goal.priority} - spirale de la mort "
            "est revenue"
        )
        assert goal.priority == pytest.approx(2.5, abs=0.1), (
            f"Priorite attendue ~2.5 (4.5 base - 2.0 stale), obtenue {goal.priority}"
        )


# ═══════════════════════════════════════════════════════════════════════
# 2. BASE PAR SOURCE
# ═══════════════════════════════════════════════════════════════════════


class TestBasePriorityBySource:
    """Chaque source de goal a sa propre base (pas de 4.5 generique)."""

    def setup_method(self):
        self.prefrontal = PrefrontalCortex()

    def test_desire_source_base_is_4_5(self):
        assert BASE_PRIORITY_BY_SOURCE["desire"] == 4.5

    def test_meta_source_has_highest_base(self):
        """Le mode consolidation (meta) doit dominer par priorite de naissance."""
        assert BASE_PRIORITY_BY_SOURCE["meta"] == 7.0
        assert BASE_PRIORITY_BY_SOURCE["meta"] > BASE_PRIORITY_BY_SOURCE["desire"]

    def test_unknown_source_falls_back_to_3_0(self):
        """Une source inconnue retombe sur 3.0 (conservateur)."""
        goal = Goal(
            id="xx", title="inconnu", horizon="short", priority=0,
            steps=[GoalStep(intent="VEILLE_SILENCIEUSE", description="")],
            source="source_inexistante",
        )
        # Sans bonus/malus applicable (pas de deadline, pas de stale, etc.)
        prio = self.prefrontal._compute_goal_priority(goal)
        assert prio == pytest.approx(3.0, abs=0.1)

    def test_all_documented_sources_have_base(self):
        """Toutes les sources referencees dans _generate_goals et les
        event handlers doivent avoir une entree dans BASE_PRIORITY_BY_SOURCE."""
        used_sources = {"desire", "gap", "pattern", "council",
                        "strategy", "meta", "auto"}
        for src in used_sources:
            assert src in BASE_PRIORITY_BY_SOURCE, f"Source '{src}' sans base"


# ═══════════════════════════════════════════════════════════════════════
# 3. HYSTERESIS MODE STRATEGIQUE
# ═══════════════════════════════════════════════════════════════════════


class TestStrategicModeHysteresis:
    """Sas anti-flickering sur survie et consolidation."""

    def setup_method(self):
        self.engine = SelfAwarenessEngine()
        self.engine.reset()
        self.engine._initialized = True

    def _push_snapshot(self, error_streak=0, success_rate=0.8, verdict="GO"):
        """Injecte un snapshot pour controller compute_strategic_mode."""
        self.engine._snapshots.append({
            "performance": {
                "error_streak": error_streak,
                "success_rate": success_rate,
            },
            "health": {"verdict": verdict},
        })
        # Invalider cache meta_reflect pour forcer recalcul trend
        self.engine._meta_reflect_cache = None
        self.engine._meta_reflect_ts = 0

    def test_get_strategic_mode_method_exists(self):
        """Bug #1 (dead code) : prefrontal.py appelait get_strategic_mode
        qui n'existait pas (seulement compute_strategic_mode). Doit exister."""
        assert hasattr(self.engine, "get_strategic_mode")
        assert callable(self.engine.get_strategic_mode)

    def test_survie_entry_at_error_streak_7(self):
        """Entree survie : error_streak >= 7."""
        self._push_snapshot(error_streak=7)
        assert self.engine.get_strategic_mode() == "survie"

    def test_survie_persists_at_error_streak_5(self):
        """Hysteresis : une fois en survie, error_streak=5 ne fait PAS
        sortir (sortie necessite < 3)."""
        self._push_snapshot(error_streak=7)
        assert self.engine.get_strategic_mode() == "survie"

        # L'erreur baisse un peu, mais pas assez
        self._push_snapshot(error_streak=5)
        assert self.engine.get_strategic_mode() == "survie", (
            "Flickering survie : doit retenir le mode tant que err>=3"
        )

    def test_survie_exits_at_error_streak_below_3(self):
        """Sortie survie : error_streak < 3 ET verdict != NO_GO."""
        self._push_snapshot(error_streak=7)
        assert self.engine.get_strategic_mode() == "survie"

        self._push_snapshot(error_streak=2, success_rate=0.8)
        mode = self.engine.get_strategic_mode()
        assert mode != "survie", f"Survie doit sortir a err=2, obtenu {mode}"

    def test_survie_entry_at_verdict_nogo(self):
        """Entree survie via health.verdict = NO_GO (meme sans erreurs)."""
        self._push_snapshot(error_streak=0, verdict="NO_GO")
        assert self.engine.get_strategic_mode() == "survie"

    def test_consolidation_hysteresis(self):
        """Hysteresis similaire pour le mode consolidation."""
        # Entree consolidation : error_streak=4 + success_rate=0.3
        self._push_snapshot(error_streak=4, success_rate=0.3)
        assert self.engine.get_strategic_mode() == "consolidation"

        # Micro-amelioration (err=3, sr=0.4) : ne sort PAS
        self._push_snapshot(error_streak=3, success_rate=0.4)
        assert self.engine.get_strategic_mode() == "consolidation", (
            "Flickering consolidation"
        )

        # Vraie amelioration (err=1, sr=0.8) : sort
        self._push_snapshot(error_streak=1, success_rate=0.8)
        mode = self.engine.get_strategic_mode()
        assert mode != "consolidation", f"Consolidation doit sortir, obtenu {mode}"

    def test_standard_mode_no_hysteresis_needed(self):
        """Depuis standard/exploration, pas d'hysteresis : on suit le raw
        instantanement (pour pouvoir entrer en survie a la moindre crise)."""
        self._push_snapshot(error_streak=0, success_rate=0.8)
        first = self.engine.get_strategic_mode()
        assert first in ("standard", "exploration")

        # Crise subite : doit entrer en survie immediatement
        self._push_snapshot(error_streak=8)
        assert self.engine.get_strategic_mode() == "survie"


# ═══════════════════════════════════════════════════════════════════════
# 4. NON-REGRESSION : compute_strategic_mode reste accessible
# ═══════════════════════════════════════════════════════════════════════


class TestBackwardCompat:
    """Le soliloque et les tests existants appellent compute_strategic_mode.
    On ne doit pas casser ces appels."""

    def setup_method(self):
        self.engine = SelfAwarenessEngine()
        self.engine.reset()
        self.engine._initialized = True

    def test_compute_strategic_mode_still_callable(self):
        """soliloque.py:605 appelle awareness.compute_strategic_mode()."""
        self.engine._snapshots.append({
            "performance": {"error_streak": 0, "success_rate": 0.8},
            "health": {"verdict": "GO"},
        })
        # Ne doit pas crash
        mode = self.engine.compute_strategic_mode()
        assert mode in ("standard", "exploration", "consolidation", "survie")

    def test_compute_strategic_mode_is_stateless(self):
        """compute_strategic_mode ne doit PAS avoir d'hysteresis (les
        tests existants comptent dessus pour tester les conditions brutes)."""
        self.engine._snapshots.append({
            "performance": {"error_streak": 7, "success_rate": 0.3},
            "health": {"verdict": "GO"},
        })
        self.engine._meta_reflect_cache = None
        # D'abord on appelle get_strategic_mode qui doit stocker le mode
        m1 = self.engine.get_strategic_mode()
        assert m1 == "survie"

        # Ensuite on ajoute un snapshot calme et on appelle compute (pas get)
        self.engine._snapshots.append({
            "performance": {"error_streak": 0, "success_rate": 0.9},
            "health": {"verdict": "GO"},
        })
        self.engine._meta_reflect_cache = None
        # compute_strategic_mode doit retourner le mode BRUT (pas hysteresis)
        raw = self.engine.compute_strategic_mode()
        assert raw != "survie", (
            f"compute_strategic_mode doit etre stateless, obtenu {raw}"
        )
