"""Tissue Controller — Pont entre le Physics Playground et le tissu neural.

Le premier corps de Promethee : le tissu neural controle la balle,
apprend des victoires et des morts, et developpe des strategies emergentes.

Boucle sensori-motrice :
  1. Etat jeu → signaux zones (to_tissue_signals)
  2. Signaux injectes dans _cognitive_state du tissu
  3. Le tissu tick normalement (2s), les cellules reagissent aux signaux
  4. Zones lues → action (from_tissue_zones)
  5. Action → step physique
  6. Resultat → reward (energie + dopamine)

Le tissu ne tick PAS plus vite — ses cellules continuent a 2s.
C'est le jeu qui tick plus vite (100ms) et injecte les signaux
a chaque step. Les cellules voient les signaux changer en temps reel
et ajustent leur comportement graduellement.
"""

import logging
import time
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# Reward : energie injectee dans les zones quand la balle progresse/meurt
REWARD_WIN = 15.0        # Bonus energie pour les cellules des zones actives
REWARD_PROGRESS = 2.0    # Bonus par unite de progression vers le goal
REWARD_DEATH = -5.0      # Drain energie quand la balle meurt
REWARD_STUCK = -1.0      # Drain si la balle ne bouge pas (stagnation)

# Seuil de stagnation : si la balle n'a pas bouge de plus de 1 case en 20 steps
STUCK_THRESHOLD = 20
STUCK_DISTANCE = 1.0


class TissueGameController:
    """Controleur qui utilise le tissu neural pour jouer au Physics Playground."""

    def __init__(self):
        self._tissue = None
        self._last_x: float = 0.0
        self._stuck_counter: int = 0
        self._total_reward: float = 0.0
        self._steps: int = 0
        self._actions_taken: Dict[str, int] = {}

    def _get_tissue(self):
        """Recupere le singleton tissu neural (lazy)."""
        if self._tissue is None:
            try:
                from core.neural_tissue import tissue
                if tissue and hasattr(tissue, 'cells') and len(tissue.cells) > 0:
                    self._tissue = tissue
                else:
                    logger.warning("TISSUE_CTRL: Tissu non initialise ou vide")
            except Exception as e:
                logger.warning(f"TISSUE_CTRL: Import tissu echoue: {e}")
        return self._tissue

    def decide(self, state: Dict[str, Any]) -> str:
        """Decide l'action basee sur l'etat des zones du tissu neural.

        1. Injecte les signaux du jeu dans le cognitive_state du tissu
        2. Lit l'activite des zones
        3. Convertit en action via from_tissue_zones
        """
        tissue = self._get_tissue()
        if tissue is None:
            return "AVANCER"  # Fallback si tissu indisponible

        # 1. Convertir etat jeu → signaux
        from core.physics_playground import PhysicsPlayground
        # Creer une instance temporaire juste pour le mapping
        signals = self._state_to_signals(state)

        # 2. Injecter dans le cognitive_state du tissu
        self._inject_game_signals(tissue, signals)

        # 3. Lire les zones et decider
        zone_signals = tissue.get_zone_signals()
        action = self._zones_to_action(zone_signals)

        # Comptabiliser
        self._steps += 1
        self._actions_taken[action] = self._actions_taken.get(action, 0) + 1

        return action

    def _state_to_signals(self, state: Dict) -> Dict[str, float]:
        """Convertit l'etat du jeu en signaux pour les zones du tissu."""
        obs_dist = state.get("obstacle_distance", -1)
        threat = max(0.0, 1.0 - (obs_dist / 6.0)) if obs_dist > 0 else 0.0
        level_h = max(state.get("level_height", 5), 1)
        level_w = max(state.get("level_width", 15), 1)

        return {
            "threat": round(threat, 3),
            "stability": 1.0 if state.get("on_ground") else 0.0,
            "dopamine": round(min(1.0, abs(state.get("ball_vx", 0)) / 3.0), 3),
            "emotion": round(max(0.0, 1.0 - state.get("ball_y", 0) / level_h), 3),
            "goals": round(max(0.0, 1.0 - state.get("distance_to_goal", 100) / level_w), 3),
            "creativity": 1.0 if state.get("platform_nearby") else 0.0,
        }

    def _inject_game_signals(self, tissue, signals: Dict[str, float]):
        """Injecte les signaux du jeu dans le cognitive_state du tissu.

        Ne remplace PAS les valeurs existantes — les module.
        Le jeu influence le tissu, il ne le controle pas.
        """
        cs = tissue._cognitive_state

        # Modulation douce : blend 30% jeu + 70% etat actuel
        GAME_WEIGHT = 0.3
        ORGAN_WEIGHT = 1.0 - GAME_WEIGHT

        cs["threat_level"] = cs["threat_level"] * ORGAN_WEIGHT + signals["threat"] * 10.0 * GAME_WEIGHT
        cs["stability"] = cs["stability"] * ORGAN_WEIGHT + signals["stability"] * GAME_WEIGHT
        cs["dopamine_level"] = cs["dopamine_level"] * ORGAN_WEIGHT + signals["dopamine"] * GAME_WEIGHT
        cs["emotion_intensity"] = cs["emotion_intensity"] * ORGAN_WEIGHT + signals["emotion"] * GAME_WEIGHT
        cs["creativity"] = cs["creativity"] * ORGAN_WEIGHT + signals["creativity"] * GAME_WEIGHT
        # Goals : nombre de goals actifs (0-3), on utilise la proximite
        cs["goal_count"] = max(cs["goal_count"], int(signals["goals"] * 3))

    def _zones_to_action(self, zone_signals: Dict) -> str:
        """Convertit l'activite des zones en action de jeu."""
        def _act(zone_name: str) -> float:
            zone = zone_signals.get(zone_name, {})
            if isinstance(zone, dict):
                return zone.get("activity", 0.0)
            return float(zone) if isinstance(zone, (int, float)) else 0.0

        threat = _act("threat")
        goals = _act("goals")
        stability = _act("stability")
        creativity = _act("creativity")
        emotion = _act("emotion")
        cognition = _act("cognition")

        # Regles de priorite enrichies (vs le stub original)
        if threat > 0.6:
            return "SAUTER"
        if creativity > 0.5 and stability > 0.3:
            return "SAUTER"  # Plateforme mouvante detectee, sol sous les pieds → sauter
        if cognition > 0.6 and goals > 0.4:
            return "AVANCER"  # Raisonnement + objectif → avancer
        if stability > 0.7 and goals < 0.2 and emotion < 0.3:
            return "RIEN"  # Stable, loin du but, pas d'emotion → attendre
        if emotion > 0.6 and goals < 0.2:
            return "RECULER"  # Emotion forte, loin du but → prudence
        if goals > 0.3:
            return "AVANCER"
        return "AVANCER"

    def apply_reward(self, prev_state: Dict, new_state: Dict, action: str):
        """Applique la recompense basee sur le resultat du step.

        Victoire → grosse injection d'energie + dopamine surge
        Progression → petite injection
        Mort → drain d'energie + dopamine dip
        Stagnation → leger drain
        """
        tissue = self._get_tissue()
        if tissue is None:
            return

        status = new_state.get("status", "ALIVE")
        new_x = new_state.get("ball_x", 0)
        prev_x = prev_state.get("ball_x", 0)
        progress = new_x - prev_x

        reward = 0.0

        if status == "WIN":
            reward = REWARD_WIN
            self._publish_reward_event("DOPAMINE_SURGE", 0.2)
            logger.info(f"TISSUE_CTRL: VICTOIRE — reward +{REWARD_WIN}")
        elif status == "DEAD":
            reward = REWARD_DEATH
            self._publish_reward_event("DOPAMINE_DIP", 0.1)
            logger.info(f"TISSUE_CTRL: MORT — reward {REWARD_DEATH}")
        else:
            # Progression
            if progress > 0.5:
                reward = REWARD_PROGRESS * progress
            # Detection stagnation
            if abs(new_x - self._last_x) < STUCK_DISTANCE:
                self._stuck_counter += 1
                if self._stuck_counter >= STUCK_THRESHOLD:
                    reward += REWARD_STUCK
                    self._stuck_counter = 0
            else:
                self._stuck_counter = 0

        self._last_x = new_x
        self._total_reward += reward

        # Injecter la recompense dans le tissu
        if reward != 0 and tissue:
            self._inject_reward(tissue, reward)

    def _inject_reward(self, tissue, reward: float):
        """Injecte la recompense dans les cellules des zones actives."""
        import random
        zone_names = ["goals", "cognition", "creativity", "dopamine"]
        for cell in tissue.cells:
            # Trouver dans quelle zone est la cellule
            cell_zone = self._get_cell_zone(cell, tissue)
            if cell_zone in zone_names:
                if reward > 0:
                    cell.energy = min(cell.energy + reward * 0.5, 200.0)
                else:
                    cell.energy = max(cell.energy + reward * 0.3, 0.0)

    def _get_cell_zone(self, cell, tissue) -> str:
        """Determine dans quelle zone se trouve une cellule."""
        try:
            from core.neural_tissue import SIGNAL_ZONES
            cx, cy = cell.x, cell.y
            for zone_name, (x1, y1, x2, y2) in SIGNAL_ZONES.items():
                if x1 <= cx < x2 and y1 <= cy < y2:
                    return zone_name
        except Exception:
            pass
        return ""

    def _publish_reward_event(self, event_type: str, magnitude: float):
        """Publie un evenement reward sur le bus."""
        try:
            import asyncio
            from core.event_bus.bus import bus
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(bus.publish(event_type, {
                    "source": "playground",
                    "magnitude": magnitude,
                }))
        except Exception:
            pass

    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques de la session de jeu."""
        return {
            "steps": self._steps,
            "total_reward": round(self._total_reward, 2),
            "actions": dict(self._actions_taken),
            "tissue_connected": self._tissue is not None,
        }

    def reset(self):
        """Reset pour une nouvelle partie."""
        self._last_x = 0.0
        self._stuck_counter = 0
        self._total_reward = 0.0
        self._steps = 0
        self._actions_taken = {}


def run_tissue_simulation(level, max_ticks: int = 500) -> Dict[str, Any]:
    """Lance une simulation complete avec le tissu neural comme controleur.

    Retourne les stats : victoire/mort, ticks, reward total, actions.
    Fallback sur RuleBasedController si le tissu est indisponible.
    """
    from core.physics_playground import PhysicsPlayground, RuleBasedController, run_simulation

    controller = TissueGameController()
    tissue = controller._get_tissue()

    if tissue is None:
        # Fallback : RuleBased si tissu indisponible
        logger.info("TISSUE_CTRL: Tissu indisponible, fallback RuleBased")
        result = run_simulation(level, RuleBasedController(), max_ticks)
        result["controller"] = "rulebased_fallback"
        return result

    # Simulation avec le tissu
    game = PhysicsPlayground(level)
    history = []

    for tick in range(max_ticks):
        if game.ball.won or not game.ball.alive:
            break

        state = game.get_state()
        state["level_height"] = level.height
        state["level_width"] = level.width
        action = controller.decide(state)

        prev_state = state
        game.step(action)
        new_state = game.get_state()

        controller.apply_reward(prev_state, new_state, action)
        history.append({"tick": tick, "action": action, "x": new_state["ball_x"]})

    stats = controller.get_stats()
    return {
        "status": game.ball.status,
        "ticks": game.tick_count,
        "final_x": round(game.ball.x, 2),
        "deaths": game.total_deaths,
        "wins": game.total_wins,
        "controller": "tissue",
        "tissue_stats": stats,
        "history_length": len(history),
    }
