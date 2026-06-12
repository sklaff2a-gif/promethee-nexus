"""physics_playground.py — Simulateur physique 2D pour Promethee.

Un monde simple ou une balle doit franchir des obstacles pour atteindre l'arrivee.
Gravite, momentum, friction, collision. Le premier corps de Promethee.

Construit le 2 avril 2026. Connexion au tissu neural prevue semaine 3.
"""

import json
import os
import random
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List

logger = logging.getLogger("PhysicsPlayground")

# --- Constantes physiques ---

GRAVITY = 0.8               # Accel vers le bas par tick
JUMP_FORCE = -6.0           # Impulsion verticale du saut (negatif = vers le haut)
MOVE_SPEED = 1.5            # Vitesse horizontale par action AVANCER/RECULER
FRICTION = 0.85             # Coefficient de friction horizontale au sol
AIR_FRICTION = 0.95         # Friction en l'air (moins de friction)
MAX_FALL_SPEED = 10.0       # Vitesse de chute max
TILE_SIZE = 1.0             # Taille d'une case

# Types de terrain
EMPTY = 0                   # Vide (la balle tombe)
GROUND = 1                  # Sol solide
PLATFORM = 2                # Plateforme (sol + marqueur visuel)
GOAL = 3                    # Arrivee
SPIKE = 4                   # Pique (mort instantanee)
MOVING_PLATFORM = 5         # Plateforme mouvante (defini dans le level metadata)

# Actions possibles
ACTIONS = ("RIEN", "AVANCER", "RECULER", "SAUTER")

# Etats de la partie
ALIVE = "ALIVE"
DEAD = "DEAD"
WIN = "WIN"

# --- Balle ---

@dataclass
class Ball:
    """La balle que Promethee controle."""
    x: float = 1.0
    y: float = 1.0
    vx: float = 0.0
    vy: float = 0.0
    on_ground: bool = False
    alive: bool = True
    won: bool = False

    @property
    def status(self) -> str:
        if self.won:
            return WIN
        if not self.alive:
            return DEAD
        return ALIVE

# --- Plateforme mouvante ---

@dataclass
class MovingPlatform:
    """Plateforme qui oscille entre deux positions."""
    x: int
    y: int
    dx: int = 0              # Direction x (-1, 0, 1)
    dy: int = 0              # Direction y (-1, 0, 1)
    range_: int = 3          # Amplitude du mouvement
    speed: float = 0.05      # Vitesse (cases par tick)
    _offset: float = 0.0
    _direction: int = 1

    def tick(self):
        """Avance la plateforme d'un pas."""
        self._offset += self.speed * self._direction
        if abs(self._offset) >= self.range_:
            self._direction *= -1
            self._offset = max(-self.range_, min(self.range_, self._offset))

    @property
    def current_x(self) -> int:
        return self.x + int(self._offset * self.dx)

    @property
    def current_y(self) -> int:
        return self.y + int(self._offset * self.dy)


# --- Niveau ---

@dataclass
class Level:
    """Un niveau du jeu."""
    name: str
    width: int
    height: int
    terrain: List[List[int]]         # [y][x] — 0=vide, 1=sol, 3=arrivee, etc.
    start_x: float = 1.0
    start_y: float = 1.0
    moving_platforms: List[MovingPlatform] = field(default_factory=list)

    @classmethod
    def from_json(cls, path: str) -> "Level":
        """Charge un niveau depuis un fichier JSON."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        terrain = data["terrain"]
        height = len(terrain)
        width = len(terrain[0]) if terrain else 0
        platforms = []
        for mp in data.get("moving_platforms", []):
            platforms.append(MovingPlatform(
                x=mp["x"], y=mp["y"],
                dx=mp.get("dx", 0), dy=mp.get("dy", 0),
                range_=mp.get("range", 3), speed=mp.get("speed", 0.05),
            ))
        return cls(
            name=data.get("name", "unknown"),
            width=width, height=height,
            terrain=terrain,
            start_x=data.get("start_x", 1.0),
            start_y=data.get("start_y", 1.0),
            moving_platforms=platforms,
        )

    def get_tile(self, x: int, y: int) -> int:
        """Retourne le type de terrain a la position (x, y)."""
        if x < 0 or x >= self.width or y < 0 or y >= self.height:
            return EMPTY  # Hors limites = vide (mort)
        # Verifier les plateformes mouvantes
        for mp in self.moving_platforms:
            if mp.current_x == x and mp.current_y == y:
                return PLATFORM
        return self.terrain[y][x]


# --- Simulateur ---

class PhysicsPlayground:
    """Moteur physique 2D — la balle, le terrain, les lois."""

    def __init__(self, level: Level):
        self.level = level
        self.ball = Ball(x=level.start_x, y=level.start_y)
        # Detecter si la balle est sur du sol au demarrage
        tile_below = level.get_tile(int(self.ball.x), int(self.ball.y) + 1)
        if tile_below in (GROUND, PLATFORM):
            self.ball.on_ground = True
        self.tick_count = 0
        self.total_deaths = 0
        self.total_wins = 0
        self.attempts = 1

    def reset(self):
        """Remet la balle au depart."""
        self.ball = Ball(x=self.level.start_x, y=self.level.start_y)
        self.attempts += 1

    def get_state(self) -> Dict[str, Any]:
        """Etat complet du jeu pour le controleur."""
        b = self.ball
        # Distance a l'obstacle le plus proche devant
        obstacle_dist = self._scan_ahead()
        # Sol devant la balle ?
        ground_ahead = self._check_ground_ahead()
        # Distance a l'arrivee
        goal_dist = self._distance_to_goal()
        # Plateforme mouvante a portee ?
        platform_nearby = any(
            abs(mp.current_x - int(b.x)) < 4 and abs(mp.current_y - int(b.y)) < 4
            for mp in self.level.moving_platforms
        )

        return {
            "ball_x": round(b.x, 2),
            "ball_y": round(b.y, 2),
            "ball_vx": round(b.vx, 2),
            "ball_vy": round(b.vy, 2),
            "on_ground": b.on_ground,
            "obstacle_distance": obstacle_dist,
            "ground_ahead": ground_ahead,
            "platform_nearby": platform_nearby,
            "distance_to_goal": goal_dist,
            "status": b.status,
            "tick": self.tick_count,
            "attempts": self.attempts,
        }

    def step(self, action: str) -> Dict[str, Any]:
        """Applique une action et avance la simulation d'un tick."""
        if self.ball.status != ALIVE:
            return self.get_state()

        b = self.ball

        # 1. Appliquer l'action
        if action == "AVANCER":
            b.vx += MOVE_SPEED
        elif action == "RECULER":
            b.vx -= MOVE_SPEED
        elif action == "SAUTER" and b.on_ground:
            b.vy = JUMP_FORCE
            b.on_ground = False

        # 2. Appliquer la gravite
        b.vy += GRAVITY
        b.vy = min(b.vy, MAX_FALL_SPEED)

        # 3. Appliquer la friction
        if b.on_ground:
            b.vx *= FRICTION
        else:
            b.vx *= AIR_FRICTION

        # 4. Deplacer la balle
        new_x = b.x + b.vx
        new_y = b.y + b.vy

        # 5. Collision horizontale
        tile_nx = self.level.get_tile(int(new_x), int(b.y))
        if tile_nx == GROUND or tile_nx == PLATFORM:
            b.vx = 0.0
            new_x = b.x
        elif tile_nx == SPIKE:
            b.alive = False
            self.total_deaths += 1
            return self.get_state()

        # 6. Collision verticale
        tile_at_new = self.level.get_tile(int(new_x), int(new_y))
        tile_below_new = self.level.get_tile(int(new_x), int(new_y) + 1)

        if b.vy > 0:
            # Descente — verifier atterrissage
            if tile_at_new in (GROUND, PLATFORM):
                # La balle est DANS un sol → remonter au-dessus
                b.vy = 0.0
                new_y = float(int(new_y)) - 1.0
                b.on_ground = True
            elif tile_below_new in (GROUND, PLATFORM) and new_y >= float(int(new_y)):
                # Sol juste en dessous → se poser
                b.vy = 0.0
                new_y = float(int(new_y))
                b.on_ground = True
            else:
                b.on_ground = False
        elif b.vy < 0:
            # Montee — verifier plafond
            if tile_at_new in (GROUND, PLATFORM):
                b.vy = 0.0
                new_y = b.y
            b.on_ground = False
        else:
            # Stationnaire — verifier si toujours sur du sol
            b.on_ground = tile_below_new in (GROUND, PLATFORM)

        # 8. Verifier victoire AVANT mise a jour (sur les cases traversees)
        # Verifier la case de la balle ET la case du sol en dessous
        old_bx = b.x
        min_x = int(min(old_bx, new_x))
        max_x = int(max(old_bx, new_x)) + 1
        check_y = int(new_y)
        for check_x in range(max(0, min_x), min(self.level.width, max_x)):
            if (self.level.get_tile(check_x, check_y) == GOAL or
                    self.level.get_tile(check_x, check_y + 1) == GOAL):
                b.x = float(check_x)
                b.y = new_y
                b.won = True
                self.total_wins += 1
                return self.get_state()

        # 9b. Mise a jour position
        b.x = new_x
        b.y = new_y

        # 10. Verifier mort (hors limites ou dans le vide)
        if b.y >= self.level.height or b.x < -1 or b.x >= self.level.width + 1:
            b.alive = False
            self.total_deaths += 1

        # 11. Avancer les plateformes mouvantes
        for mp in self.level.moving_platforms:
            mp.tick()

        self.tick_count += 1
        return self.get_state()

    # --- Helpers de perception ---

    def _scan_ahead(self) -> float:
        """Distance au prochain obstacle devant la balle. -1 si rien."""
        bx, by = int(self.ball.x), int(self.ball.y)
        for dx in range(1, 8):
            tile = self.level.get_tile(bx + dx, by)
            if tile == GROUND or tile == SPIKE:
                return float(dx)
        return -1.0

    def _check_ground_ahead(self) -> bool:
        """Y a-t-il du sol 1-2 cases devant et en dessous ?"""
        bx, by = int(self.ball.x), int(self.ball.y)
        for dx in range(1, 3):
            tile_below = self.level.get_tile(bx + dx, by + 1)
            if tile_below == EMPTY:
                return False
        return True

    def _distance_to_goal(self) -> float:
        """Distance horizontale a l'arrivee la plus proche."""
        bx = int(self.ball.x)
        for y in range(self.level.height):
            for x in range(self.level.width):
                if self.level.terrain[y][x] == GOAL:
                    return float(abs(x - bx))
        return float(self.level.width)

    # --- Interface tissu neural (pret pour semaine 3) ---

    def to_tissue_signals(self, state: Dict = None) -> Dict[str, float]:
        """Convertit l'etat jeu en signaux pour les zones du tissu neural.

        Mapping :
        - threat : distance obstacle (proche = fort)
        - stability : sol sous la balle
        - dopamine : vitesse horizontale
        - emotion : hauteur relative
        - goals : proximite de l'arrivee
        - creativity : plateforme mouvante a portee
        """
        if state is None:
            state = self.get_state()

        obs_dist = state["obstacle_distance"]
        threat = max(0.0, 1.0 - (obs_dist / 6.0)) if obs_dist > 0 else 0.0

        return {
            "threat": round(threat, 3),
            "stability": 1.0 if state["on_ground"] else 0.0,
            "dopamine": round(min(1.0, abs(state["ball_vx"]) / 3.0), 3),
            "emotion": round(max(0.0, 1.0 - state["ball_y"] / max(self.level.height, 1)), 3),
            "goals": round(max(0.0, 1.0 - state["distance_to_goal"] / max(self.level.width, 1)), 3),
            "creativity": 1.0 if state["platform_nearby"] else 0.0,
        }

    @staticmethod
    def from_tissue_zones(zone_signals: Dict[str, Dict]) -> str:
        """Convertit l'activite des zones du tissu en action.

        Regles de priorite :
        1. threat fort → SAUTER
        2. goals fort → AVANCER
        3. stability fort ET goals faible → RIEN
        4. creativity fort → SAUTER (timing)
        5. defaut → AVANCER
        """
        def _get_activity(zone_name: str) -> float:
            zone = zone_signals.get(zone_name, {})
            if isinstance(zone, dict):
                return zone.get("activity", 0.0)
            return float(zone) if isinstance(zone, (int, float)) else 0.0

        threat = _get_activity("threat")
        goals = _get_activity("goals")
        stability = _get_activity("stability")
        creativity = _get_activity("creativity")
        emotion = _get_activity("emotion")

        if threat > 0.7:
            return "SAUTER"
        if creativity > 0.6:
            return "SAUTER"
        if stability > 0.8 and goals < 0.3:
            return "RIEN"
        if emotion > 0.5 and goals < 0.2:
            return "RECULER"
        if goals > 0.3:
            return "AVANCER"
        return "AVANCER"

    # --- Rendu ASCII (debug) ---

    def render_ascii(self) -> str:
        """Rendu ASCII du niveau avec la position de la balle."""
        lines = []
        bx, by = int(self.ball.x), int(self.ball.y)
        tile_chars = {EMPTY: " ", GROUND: "#", PLATFORM: "=", GOAL: "G", SPIKE: "^"}

        for y in range(self.level.height):
            row = ""
            for x in range(self.level.width):
                if x == bx and y == by:
                    row += "O"  # La balle
                else:
                    tile = self.level.get_tile(x, y)
                    row += tile_chars.get(tile, "?")
            lines.append(f"|{row}|")
        lines.append(f"+{'-' * self.level.width}+")
        lines.append(f"Tick:{self.tick_count} Pos:({bx},{by}) V:({self.ball.vx:.1f},{self.ball.vy:.1f}) {self.ball.status}")
        return "\n".join(lines)


# --- Controleurs ---

class RandomController:
    """Controleur aleatoire — pour tester la physique."""
    def decide(self, state: Dict) -> str:
        return random.choice(ACTIONS)


class RuleBasedController:
    """Controleur basique — saute quand y'a un trou, avance sinon."""
    def decide(self, state: Dict) -> str:
        if state["status"] != ALIVE:
            return "RIEN"
        # Trou devant → sauter
        if not state["ground_ahead"] and state["on_ground"]:
            return "SAUTER"
        # Obstacle devant et proche → sauter
        if 0 < state["obstacle_distance"] <= 2 and state["on_ground"]:
            return "SAUTER"
        # Defaut → avancer
        return "AVANCER"


# --- Simulation complete ---

def run_simulation(level: Level, controller, max_ticks: int = 500) -> Dict[str, Any]:
    """Lance une simulation complete. Retourne les stats."""
    game = PhysicsPlayground(level)
    history = []

    for _ in range(max_ticks):
        state = game.get_state()
        if state["status"] != ALIVE:
            break
        action = controller.decide(state)
        new_state = game.step(action)
        history.append({"action": action, "state": new_state})

    final = game.get_state()
    return {
        "status": final["status"],
        "ticks": game.tick_count,
        "final_x": final["ball_x"],
        "deaths": game.total_deaths,
        "wins": game.total_wins,
        "history_length": len(history),
    }


# --- Chargeur de niveaux ---

LEVELS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "levels"
)

def load_level(level_number: int) -> Level:
    """Charge un niveau par son numero (1-5)."""
    path = os.path.join(LEVELS_DIR, f"level_{level_number}.json")
    return Level.from_json(path)
