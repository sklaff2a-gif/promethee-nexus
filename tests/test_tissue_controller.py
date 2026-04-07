"""Tests du tissue controller — pont tissu neural / physics playground."""

import pytest
from unittest.mock import MagicMock, patch
from core.games.tissue_controller import TissueGameController, run_tissue_simulation


class FakeCell:
    def __init__(self, x, y, energy=50.0):
        self.x = x
        self.y = y
        self.energy = energy


class FakeTissue:
    def __init__(self):
        self.cells = [
            FakeCell(1, 1, 50),   # emotion zone
            FakeCell(7, 7, 60),   # cognition zone
            FakeCell(13, 7, 40),  # creativity zone
            FakeCell(13, 13, 55), # goals zone
            FakeCell(1, 7, 45),   # stability zone
        ]
        self._cognitive_state = {
            "emotion_intensity": 0.5,
            "threat_level": 0.0,
            "dopamine_level": 0.5,
            "goal_count": 0,
            "desire_intensity": 50.0,
            "memory_activity": 0.3,
            "stability": 0.7,
            "creativity": 0.3,
            "cognition_level": 0.3,
            "thermal_stress": 0.0,
            "somatic_load": 0.0,
            "suffocation": 0.0,
            "vitality_level": 0.5,
        }

    def get_zone_signals(self):
        return {
            "emotion": {"activity": 0.5},
            "threat": {"activity": 0.2},
            "dopamine": {"activity": 0.5},
            "goals": {"activity": 0.6},
            "stability": {"activity": 0.7},
            "creativity": {"activity": 0.3},
            "cognition": {"activity": 0.4},
        }


@pytest.fixture
def controller():
    ctrl = TissueGameController()
    ctrl._tissue = FakeTissue()
    return ctrl


class TestTissueControllerBase:

    def test_decide_returns_valid_action(self, controller):
        state = {
            "ball_x": 5.0, "ball_y": 3.0, "ball_vx": 1.0,
            "on_ground": True, "obstacle_distance": 3.0,
            "distance_to_goal": 10.0, "platform_nearby": False,
            "status": "ALIVE", "level_height": 5, "level_width": 15,
        }
        action = controller.decide(state)
        assert action in ("AVANCER", "RECULER", "SAUTER", "RIEN")

    def test_decide_without_tissue_fallback(self):
        ctrl = TissueGameController()
        ctrl._tissue = None
        state = {"ball_x": 0, "ball_y": 0, "ball_vx": 0,
                 "on_ground": True, "obstacle_distance": -1,
                 "distance_to_goal": 10, "platform_nearby": False,
                 "status": "ALIVE", "level_height": 5, "level_width": 15}
        action = ctrl.decide(state)
        assert action == "AVANCER"

    def test_state_to_signals(self, controller):
        state = {
            "ball_x": 5.0, "ball_y": 2.0, "ball_vx": 2.0,
            "on_ground": True, "obstacle_distance": 2.0,
            "distance_to_goal": 5.0, "platform_nearby": True,
            "level_height": 5, "level_width": 15,
        }
        signals = controller._state_to_signals(state)
        assert 0.0 <= signals["threat"] <= 1.0
        assert signals["stability"] == 1.0
        assert signals["creativity"] == 1.0
        assert 0.0 <= signals["goals"] <= 1.0

    def test_inject_modulates_cognitive_state(self, controller):
        tissue = controller._tissue
        original_threat = tissue._cognitive_state["threat_level"]
        signals = {"threat": 0.8, "stability": 1.0, "dopamine": 0.5,
                   "emotion": 0.3, "goals": 0.7, "creativity": 0.0}
        controller._inject_game_signals(tissue, signals)
        # Threat devrait avoir augmente (blend 30% jeu)
        assert tissue._cognitive_state["threat_level"] > original_threat * 0.5

    def test_steps_counter(self, controller):
        state = {"ball_x": 0, "ball_y": 0, "ball_vx": 0,
                 "on_ground": True, "obstacle_distance": -1,
                 "distance_to_goal": 10, "platform_nearby": False,
                 "status": "ALIVE", "level_height": 5, "level_width": 15}
        controller.decide(state)
        controller.decide(state)
        assert controller._steps == 2


class TestTissueControllerReward:

    def test_reward_on_win(self, controller):
        prev = {"ball_x": 10.0, "status": "ALIVE"}
        new = {"ball_x": 13.0, "status": "WIN"}
        controller.apply_reward(prev, new, "AVANCER")
        assert controller._total_reward > 0

    def test_reward_on_death(self, controller):
        prev = {"ball_x": 5.0, "status": "ALIVE"}
        new = {"ball_x": 5.0, "status": "DEAD"}
        controller.apply_reward(prev, new, "AVANCER")
        assert controller._total_reward < 0

    def test_reward_on_progress(self, controller):
        prev = {"ball_x": 3.0, "status": "ALIVE"}
        new = {"ball_x": 5.0, "status": "ALIVE"}
        controller.apply_reward(prev, new, "AVANCER")
        assert controller._total_reward > 0

    def test_stuck_detection(self, controller):
        prev = {"ball_x": 5.0, "status": "ALIVE"}
        new = {"ball_x": 5.1, "status": "ALIVE"}
        for _ in range(25):
            controller.apply_reward(prev, new, "RIEN")
        assert controller._total_reward < 0  # Stagnation penalisee

    def test_reward_injects_energy(self, controller):
        tissue = controller._tissue
        goals_cell = [c for c in tissue.cells if c.x == 13 and c.y == 13][0]
        energy_before = goals_cell.energy
        prev = {"ball_x": 10.0, "status": "ALIVE"}
        new = {"ball_x": 13.0, "status": "WIN"}
        controller.apply_reward(prev, new, "AVANCER")
        assert goals_cell.energy > energy_before


class TestTissueControllerZones:

    def test_high_threat_triggers_jump(self, controller):
        controller._tissue.get_zone_signals = lambda: {
            "threat": {"activity": 0.8}, "goals": {"activity": 0.3},
            "stability": {"activity": 0.5}, "creativity": {"activity": 0.2},
            "emotion": {"activity": 0.3}, "cognition": {"activity": 0.3},
        }
        state = {"ball_x": 5, "ball_y": 3, "ball_vx": 1,
                 "on_ground": True, "obstacle_distance": 1.0,
                 "distance_to_goal": 8, "platform_nearby": False,
                 "status": "ALIVE", "level_height": 5, "level_width": 15}
        assert controller.decide(state) == "SAUTER"

    def test_high_goals_triggers_advance(self, controller):
        controller._tissue.get_zone_signals = lambda: {
            "threat": {"activity": 0.1}, "goals": {"activity": 0.7},
            "stability": {"activity": 0.5}, "creativity": {"activity": 0.2},
            "emotion": {"activity": 0.3}, "cognition": {"activity": 0.3},
        }
        state = {"ball_x": 12, "ball_y": 3, "ball_vx": 1,
                 "on_ground": True, "obstacle_distance": -1,
                 "distance_to_goal": 2, "platform_nearby": False,
                 "status": "ALIVE", "level_height": 5, "level_width": 15}
        assert controller.decide(state) == "AVANCER"


class TestTissueControllerStats:

    def test_get_stats(self, controller):
        stats = controller.get_stats()
        assert "steps" in stats
        assert "total_reward" in stats
        assert "tissue_connected" in stats
        assert stats["tissue_connected"] is True

    def test_reset(self, controller):
        controller._steps = 100
        controller._total_reward = 50.0
        controller.reset()
        assert controller._steps == 0
        assert controller._total_reward == 0.0


class TestRunTissueSimulation:

    def test_fallback_without_tissue(self):
        """Sans tissu, tombe sur RuleBased."""
        import os
        from core.physics_playground import Level
        lvl_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "config", "levels", "level_1.json")
        if not os.path.exists(lvl_path):
            pytest.skip("level_1.json manquant")
        level = Level.from_json(lvl_path)
        with patch("core.games.tissue_controller.TissueGameController._get_tissue",
                    return_value=None):
            result = run_tissue_simulation(level, max_ticks=200)
        assert result["controller"] == "rulebased_fallback"
        assert "status" in result
