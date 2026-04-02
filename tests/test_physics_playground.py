"""Tests pour core/physics_playground.py — simulateur physique 2D."""

import json
import os
import pytest

from core.physics_playground import (
    Ball, Level, PhysicsPlayground, MovingPlatform,
    RandomController, RuleBasedController, run_simulation,
    GRAVITY, JUMP_FORCE, MOVE_SPEED, ACTIONS,
    EMPTY, GROUND, PLATFORM, GOAL, SPIKE,
    ALIVE, DEAD, WIN,
)


# --- Fixtures ---

@pytest.fixture
def flat_level():
    """Niveau plat simple : sol continu avec arrivee."""
    return Level(
        name="test_flat", width=15, height=5,
        terrain=[
            [0]*15,
            [0]*15,
            [0]*15,
            [0]*15,
            [1,1,1,1,1,1,1,1,1,1,1,1,1,3,1],
        ],
        start_x=1.0, start_y=3.0,
    )

@pytest.fixture
def gap_level():
    """Niveau avec une crevasse."""
    return Level(
        name="test_gap", width=15, height=5,
        terrain=[
            [0]*15,
            [0]*15,
            [0]*15,
            [0]*15,
            [1,1,1,1,0,0,1,1,1,1,1,1,1,3,1],
        ],
        start_x=1.0, start_y=3.0,
    )

@pytest.fixture
def spike_level():
    """Niveau avec des piques."""
    return Level(
        name="test_spike", width=10, height=5,
        terrain=[
            [0]*10,
            [0]*10,
            [0]*10,
            [0]*10,
            [1,1,1,4,4,1,1,1,3,1],
        ],
        start_x=1.0, start_y=3.0,
    )


# --- Tests Ball ---

class TestBall:
    def test_initial_state(self):
        b = Ball()
        assert b.alive
        assert not b.won
        assert b.status == ALIVE

    def test_dead_status(self):
        b = Ball(alive=False)
        assert b.status == DEAD

    def test_win_status(self):
        b = Ball(won=True)
        assert b.status == WIN


# --- Tests Level ---

class TestLevel:
    def test_get_tile_in_bounds(self, flat_level):
        assert flat_level.get_tile(0, 4) == GROUND
        assert flat_level.get_tile(13, 4) == GOAL
        assert flat_level.get_tile(5, 2) == EMPTY

    def test_get_tile_out_of_bounds(self, flat_level):
        assert flat_level.get_tile(-1, 0) == EMPTY
        assert flat_level.get_tile(100, 0) == EMPTY
        assert flat_level.get_tile(0, 100) == EMPTY

    def test_load_from_json(self):
        levels_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "levels"
        )
        path = os.path.join(levels_dir, "level_1.json")
        if os.path.exists(path):
            level = Level.from_json(path)
            assert level.name == "Terrain plat — juste avancer"
            assert level.width == 15
            assert level.height == 5

    def test_moving_platform_in_tile(self):
        mp = MovingPlatform(x=5, y=3, dx=1, dy=0, range_=3, speed=0.1)
        level = Level(
            name="test_mp", width=15, height=5,
            terrain=[[0]*15 for _ in range(5)],
            moving_platforms=[mp],
        )
        # La plateforme est a (5, 3) initialement
        assert level.get_tile(5, 3) == PLATFORM
        assert level.get_tile(6, 3) == EMPTY


# --- Tests Physique ---

class TestPhysics:
    def test_gravity_pulls_down(self, flat_level):
        game = PhysicsPlayground(flat_level)
        game.ball.y = 1.0
        game.ball.on_ground = False
        state = game.step("RIEN")
        assert game.ball.vy > 0  # Vitesse vers le bas

    def test_jump_when_on_ground(self, flat_level):
        game = PhysicsPlayground(flat_level)
        game.ball.on_ground = True
        game.step("SAUTER")
        assert game.ball.vy < 0  # Vitesse vers le haut

    def test_no_jump_in_air(self, flat_level):
        game = PhysicsPlayground(flat_level)
        game.ball.on_ground = False
        game.ball.vy = 0.0
        game.step("SAUTER")
        assert game.ball.vy >= 0  # Pas de saut (gravite seulement)

    def test_avancer_increases_vx(self, flat_level):
        game = PhysicsPlayground(flat_level)
        game.step("AVANCER")
        assert game.ball.vx > 0

    def test_reculer_decreases_vx(self, flat_level):
        game = PhysicsPlayground(flat_level)
        game.step("RECULER")
        assert game.ball.vx < 0

    def test_friction_slows_down(self, flat_level):
        game = PhysicsPlayground(flat_level)
        game.ball.vx = 5.0
        game.ball.on_ground = True
        game.step("RIEN")
        assert abs(game.ball.vx) < 5.0


# --- Tests Mort et Victoire ---

class TestOutcomes:
    def test_fall_into_gap_dies(self, gap_level):
        game = PhysicsPlayground(gap_level)
        game.ball.x = 4.5
        game.ball.y = 3.0
        game.ball.on_ground = False
        # Laisser tomber
        for _ in range(20):
            game.step("RIEN")
            if game.ball.status == DEAD:
                break
        assert game.ball.status == DEAD

    def test_reach_goal_wins(self, flat_level):
        game = PhysicsPlayground(flat_level)
        # Le sol est a y=4, la balle marche sur y=3 (au dessus du sol)
        game.ball.x = 11.0
        game.ball.y = 3.0
        game.ball.on_ground = True
        game.ball.vy = 0.0
        # Avancer vers le goal (x=13)
        for _ in range(50):
            state = game.step("AVANCER")
            if state["status"] == WIN:
                break
        assert game.ball.status == WIN

    def test_spike_kills(self, spike_level):
        game = PhysicsPlayground(spike_level)
        game.ball.x = 2.5
        game.ball.y = 3.0
        game.ball.on_ground = True
        # Avancer vers les piques
        for _ in range(20):
            game.step("AVANCER")
            if game.ball.status == DEAD:
                break
        assert game.ball.status == DEAD


# --- Tests Controleurs ---

class TestControllers:
    def test_random_controller_valid_action(self):
        ctrl = RandomController()
        state = {"status": ALIVE, "on_ground": True, "ground_ahead": True,
                 "obstacle_distance": -1, "distance_to_goal": 10.0}
        for _ in range(20):
            action = ctrl.decide(state)
            assert action in ACTIONS

    def test_rule_based_jumps_at_gap(self):
        ctrl = RuleBasedController()
        state = {"status": ALIVE, "on_ground": True, "ground_ahead": False,
                 "obstacle_distance": -1, "distance_to_goal": 10.0}
        assert ctrl.decide(state) == "SAUTER"

    def test_rule_based_jumps_at_obstacle(self):
        ctrl = RuleBasedController()
        state = {"status": ALIVE, "on_ground": True, "ground_ahead": True,
                 "obstacle_distance": 1.0, "distance_to_goal": 10.0}
        assert ctrl.decide(state) == "SAUTER"

    def test_rule_based_advances_normally(self):
        ctrl = RuleBasedController()
        state = {"status": ALIVE, "on_ground": True, "ground_ahead": True,
                 "obstacle_distance": -1, "distance_to_goal": 10.0}
        assert ctrl.decide(state) == "AVANCER"

    def test_rule_based_level1_completion(self, flat_level):
        result = run_simulation(flat_level, RuleBasedController(), max_ticks=200)
        assert result["status"] == WIN


# --- Tests Interface Tissu ---

class TestTissueInterface:
    def test_to_tissue_signals_keys(self, flat_level):
        game = PhysicsPlayground(flat_level)
        signals = game.to_tissue_signals()
        expected_keys = {"threat", "stability", "dopamine", "emotion", "goals", "creativity"}
        assert set(signals.keys()) == expected_keys

    def test_to_tissue_signals_range(self, flat_level):
        game = PhysicsPlayground(flat_level)
        signals = game.to_tissue_signals()
        for key, val in signals.items():
            assert 0.0 <= val <= 1.0, f"{key}={val} hors range [0,1]"

    def test_from_tissue_zones_threat(self):
        zones = {"threat": {"activity": 0.9}, "goals": {"activity": 0.1},
                 "stability": {"activity": 0.5}, "creativity": {"activity": 0.1},
                 "emotion": {"activity": 0.3}}
        action = PhysicsPlayground.from_tissue_zones(zones)
        assert action == "SAUTER"

    def test_from_tissue_zones_goals(self):
        zones = {"threat": {"activity": 0.1}, "goals": {"activity": 0.8},
                 "stability": {"activity": 0.3}, "creativity": {"activity": 0.1},
                 "emotion": {"activity": 0.2}}
        action = PhysicsPlayground.from_tissue_zones(zones)
        assert action == "AVANCER"

    def test_from_tissue_zones_stability(self):
        zones = {"threat": {"activity": 0.1}, "goals": {"activity": 0.1},
                 "stability": {"activity": 0.9}, "creativity": {"activity": 0.1},
                 "emotion": {"activity": 0.2}}
        action = PhysicsPlayground.from_tissue_zones(zones)
        assert action == "RIEN"


# --- Tests Simulation Complete ---

class TestSimulation:
    def test_run_simulation_returns_dict(self, flat_level):
        result = run_simulation(flat_level, RandomController(), max_ticks=50)
        assert "status" in result
        assert "ticks" in result
        assert "deaths" in result

    def test_render_ascii(self, flat_level):
        game = PhysicsPlayground(flat_level)
        ascii_art = game.render_ascii()
        assert "O" in ascii_art  # La balle
        assert "#" in ascii_art  # Le sol
        assert "G" in ascii_art  # L'arrivee

    def test_reset_restores_position(self, flat_level):
        game = PhysicsPlayground(flat_level)
        game.ball.x = 10.0
        game.ball.y = 0.0
        game.reset()
        assert game.ball.x == flat_level.start_x
        assert game.ball.y == flat_level.start_y
        assert game.attempts == 2


# --- Tests Moving Platform ---

class TestMovingPlatform:
    def test_platform_moves(self):
        mp = MovingPlatform(x=5, y=3, dx=1, dy=0, range_=3, speed=1.0)
        mp.tick()
        assert mp.current_x != 5 or mp._offset != 0

    def test_platform_bounces(self):
        mp = MovingPlatform(x=5, y=3, dx=1, dy=0, range_=2, speed=1.0)
        positions = []
        for _ in range(10):
            mp.tick()
            positions.append(mp.current_x)
        # Doit osciller (pas toujours la meme valeur)
        assert len(set(positions)) > 1
