"""Game Hub — Systeme de jeux unifie de Promethee.

3 jeux + echecs debloquables apres validation de competences.
Gere les parties actives, les scores, la progression.
Persistence dans memory/game_state.json.

Jeux disponibles :
  1. Physics Playground (solo) — simulateur 2D, tissu neural
  2. Morpion (vs humain ou Alfred) — tic-tac-toe, minimax
  3. Puissance 4 (vs humain ou Alfred) — connect 4, alpha-beta
  4. Echecs (vs humain ou Alfred) — debloque apres validation des 3 premiers
"""

import json
import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from core.games.morpion import MorpionGame, ai_move as morpion_ai
from core.games.puissance4 import Puissance4Game, ai_move as puissance4_ai

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                          "memory", "game_state.json")

# Competences requises pour debloquer les echecs
CHESS_REQUIREMENTS = {
    "playground": {
        "description": "Completer les niveaux 1 et 3 du Physics Playground",
        "check": lambda stats: stats.get("playground_level1_cleared", False)
                               and stats.get("playground_level3_cleared", False),
    },
    "morpion": {
        "description": "Gagner 3 parties et ne pas perdre 5 d'affilee",
        "check": lambda stats: stats.get("morpion_wins", 0) >= 3
                               and stats.get("morpion_consecutive_losses", 0) < 5,
    },
    "puissance4": {
        "description": "Gagner 3 parties dont 1 en moins de 20 coups",
        "check": lambda stats: stats.get("puissance4_wins", 0) >= 3
                               and stats.get("puissance4_quick_win", False),
    },
}


@dataclass
class GameSession:
    """Une session de jeu active."""
    game_type: str  # "morpion" ou "puissance4"
    opponent: str   # "human" ou "alfred"
    started_at: float = field(default_factory=time.time)
    promethee_symbol: str = ""  # X/O pour morpion, R/J pour puissance4


class GameHub:
    """Hub central des jeux de Promethee."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Parties actives
        self._active_morpion: Optional[MorpionGame] = None
        self._active_puissance4: Optional[Puissance4Game] = None
        self._active_session: Optional[GameSession] = None

        # Statistiques persistantes
        self.stats: Dict[str, Any] = {
            # Playground
            "playground_level1_cleared": False,
            "playground_level3_cleared": False,
            "playground_best_level": 0,
            # Morpion
            "morpion_wins": 0,
            "morpion_losses": 0,
            "morpion_draws": 0,
            "morpion_total": 0,
            "morpion_consecutive_losses": 0,
            # Puissance 4
            "puissance4_wins": 0,
            "puissance4_losses": 0,
            "puissance4_draws": 0,
            "puissance4_total": 0,
            "puissance4_quick_win": False,
            # Global
            "chess_unlocked": False,
            "total_games_played": 0,
        }

        # Historique des parties (dernieres 50)
        self.game_history: List[Dict[str, Any]] = []

        self._load()

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    # --- Gestion des parties ---

    def new_game(self, game_type: str, opponent: str = "alfred",
                 promethee_starts: bool = True) -> Dict[str, Any]:
        """Cree une nouvelle partie.

        Args:
            game_type: "morpion" ou "puissance4"
            opponent: "human" ou "alfred"
            promethee_starts: True si Promethee joue en premier
        """
        if game_type not in ("morpion", "puissance4"):
            return {"error": f"Jeu inconnu: {game_type}. Disponibles: morpion, puissance4"}

        if self._active_session:
            return {"error": f"Partie en cours ({self._active_session.game_type}). Terminez-la d'abord."}

        session = GameSession(game_type=game_type, opponent=opponent)

        if game_type == "morpion":
            game = MorpionGame()
            session.promethee_symbol = "X" if promethee_starts else "O"
            self._active_morpion = game
        else:
            game = Puissance4Game()
            session.promethee_symbol = "R" if promethee_starts else "J"
            self._active_puissance4 = game

        self._active_session = session
        logger.info(f"GAME_HUB: Nouvelle partie {game_type} vs {opponent} "
                    f"(Promethee={session.promethee_symbol})")

        result = {
            "game": game_type,
            "opponent": opponent,
            "promethee_symbol": session.promethee_symbol,
            "state": game.get_state(),
            "render": game.render(),
        }

        # Si Promethee ne commence pas et l'adversaire est Alfred, Alfred joue
        if not promethee_starts and opponent == "alfred":
            ai_result = self._alfred_play()
            if ai_result:
                result["alfred_move"] = ai_result
                result["state"] = self._get_active_game().get_state()
                result["render"] = self._get_active_game().render()

        return result

    def play_move(self, move: Any, player: str = "promethee") -> Dict[str, Any]:
        """Joue un coup dans la partie active.

        Args:
            move: (row, col) pour morpion, col (int) pour puissance4
            player: "promethee" ou "human"
        """
        if not self._active_session:
            return {"error": "Pas de partie en cours"}

        game = self._get_active_game()
        session = self._active_session

        # Verifier que c'est le bon tour
        if player == "promethee":
            expected = session.promethee_symbol
        elif player == "human":
            expected = "O" if session.promethee_symbol == "X" else "X"
            if session.game_type == "puissance4":
                expected = "J" if session.promethee_symbol == "R" else "R"
        else:
            return {"error": f"Joueur inconnu: {player}"}

        if game.current_player != expected:
            return {"error": f"Ce n'est pas le tour de {player} (attendu: {game.current_player})"}

        # Jouer le coup
        if session.game_type == "morpion":
            if isinstance(move, (list, tuple)) and len(move) == 2:
                result = game.play(move[0], move[1])
            else:
                return {"error": "Morpion: move doit etre [row, col]"}
        else:
            if isinstance(move, int) or (isinstance(move, str) and move.isdigit()):
                result = game.play(int(move))
            else:
                return {"error": "Puissance 4: move doit etre un numero de colonne"}

        if not result.get("valid"):
            return result

        response = {
            "move_result": result,
            "state": game.get_state(),
            "render": game.render(),
        }

        # Si la partie est terminee
        if game.game_over:
            self._record_game_end(game, session)
            response["game_over"] = True
            response["stats"] = self._get_game_stats(session.game_type)
            self._active_session = None
            self._active_morpion = None
            self._active_puissance4 = None
            self._publish_game_event("GAME_ENDED", session, result)
            return response

        # Si l'adversaire est Alfred, il joue immediatement
        if session.opponent == "alfred" and player != "alfred_internal":
            ai_result = self._alfred_play()
            if ai_result:
                response["alfred_move"] = ai_result
                response["state"] = game.get_state()
                response["render"] = game.render()
                if game.game_over:
                    self._record_game_end(game, session)
                    response["game_over"] = True
                    response["stats"] = self._get_game_stats(session.game_type)
                    self._active_session = None
                    self._active_morpion = None
                    self._active_puissance4 = None
                    self._publish_game_event("GAME_ENDED", session, ai_result)

        return response

    def _alfred_play(self) -> Optional[Dict[str, Any]]:
        """Alfred joue son coup."""
        session = self._active_session
        if not session:
            return None

        game = self._get_active_game()
        if game.game_over:
            return None

        if session.game_type == "morpion":
            move = morpion_ai(game, difficulty="medium")
            if move:
                result = game.play(move[0], move[1])
                return {"move": move, "result": result}
        else:
            col = puissance4_ai(game, difficulty="medium")
            if col is not None:
                result = game.play(col)
                return {"move": col, "result": result}
        return None

    def forfeit(self) -> Dict[str, Any]:
        """Abandonne la partie en cours."""
        if not self._active_session:
            return {"error": "Pas de partie en cours"}

        session = self._active_session
        game = self._get_active_game()

        # Compter comme une defaite
        game.game_over = True
        game.winner = "forfait"
        self._record_game_end(game, session, forfeit=True)

        self._active_session = None
        self._active_morpion = None
        self._active_puissance4 = None

        return {"status": "forfait", "game": session.game_type}

    def _get_active_game(self):
        if self._active_session.game_type == "morpion":
            return self._active_morpion
        return self._active_puissance4

    # --- Statistiques et progression ---

    def _record_game_end(self, game, session: GameSession, forfeit: bool = False):
        """Enregistre la fin d'une partie."""
        gt = session.game_type
        self.stats["total_games_played"] += 1
        self.stats[f"{gt}_total"] = self.stats.get(f"{gt}_total", 0) + 1

        promethee_won = (game.winner == session.promethee_symbol)
        opponent_won = (game.winner is not None and not promethee_won
                        and game.winner != "forfait")
        is_draw = (game.game_over and game.winner is None)

        if forfeit:
            self.stats[f"{gt}_losses"] += 1
            self.stats[f"{gt}_consecutive_losses"] = self.stats.get(f"{gt}_consecutive_losses", 0) + 1
        elif promethee_won:
            self.stats[f"{gt}_wins"] += 1
            self.stats[f"{gt}_consecutive_losses"] = 0
            # Quick win pour puissance 4
            if gt == "puissance4" and game.moves_count <= 20:
                self.stats["puissance4_quick_win"] = True
        elif opponent_won:
            self.stats[f"{gt}_losses"] += 1
            self.stats[f"{gt}_consecutive_losses"] = self.stats.get(f"{gt}_consecutive_losses", 0) + 1
        elif is_draw:
            self.stats[f"{gt}_draws"] += 1
            # Un nul ne casse pas la serie de defaites

        # Historique
        entry = {
            "game": gt,
            "opponent": session.opponent,
            "winner": game.winner,
            "promethee_symbol": session.promethee_symbol,
            "promethee_won": promethee_won,
            "moves": game.moves_count,
            "timestamp": time.time(),
            "forfeit": forfeit,
        }
        self.game_history.append(entry)
        if len(self.game_history) > 50:
            self.game_history = self.game_history[-50:]

        # Verifier si les echecs sont debloques
        self._check_chess_unlock()
        self._save()
        logger.info(f"GAME_HUB: Fin {gt} — {'Promethee gagne' if promethee_won else 'defaite/nul'} "
                    f"({game.moves_count} coups)")

    def _check_chess_unlock(self):
        """Verifie si toutes les competences sont validees pour les echecs."""
        if self.stats.get("chess_unlocked"):
            return
        all_ok = all(req["check"](self.stats) for req in CHESS_REQUIREMENTS.values())
        if all_ok:
            self.stats["chess_unlocked"] = True
            logger.info("GAME_HUB: ECHECS DEBLOQUES — toutes les competences validees!")

    def _get_game_stats(self, game_type: str) -> Dict[str, Any]:
        gt = game_type
        return {
            "wins": self.stats.get(f"{gt}_wins", 0),
            "losses": self.stats.get(f"{gt}_losses", 0),
            "draws": self.stats.get(f"{gt}_draws", 0),
            "total": self.stats.get(f"{gt}_total", 0),
        }

    # --- Status global ---

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'etat global du hub de jeux."""
        active = None
        if self._active_session:
            game = self._get_active_game()
            active = {
                "game": self._active_session.game_type,
                "opponent": self._active_session.opponent,
                "promethee_symbol": self._active_session.promethee_symbol,
                "state": game.get_state(),
                "render": game.render(),
            }

        competences = {}
        for key, req in CHESS_REQUIREMENTS.items():
            competences[key] = {
                "description": req["description"],
                "validated": req["check"](self.stats),
            }

        return {
            "active_game": active,
            "stats": dict(self.stats),
            "competences": competences,
            "chess_unlocked": self.stats.get("chess_unlocked", False),
            "games_available": self._list_available_games(),
            "recent_history": self.game_history[-10:],
        }

    def _list_available_games(self) -> List[Dict[str, str]]:
        games = [
            {"id": "playground", "name": "Physics Playground", "type": "solo",
             "status": "disponible"},
            {"id": "morpion", "name": "Morpion", "type": "vs humain ou Alfred",
             "status": "disponible"},
            {"id": "puissance4", "name": "Puissance 4", "type": "vs humain ou Alfred",
             "status": "disponible"},
        ]
        if self.stats.get("chess_unlocked"):
            games.append({"id": "echecs", "name": "Echecs", "type": "vs humain ou Alfred",
                          "status": "disponible"})
        else:
            games.append({"id": "echecs", "name": "Echecs", "type": "vs humain ou Alfred",
                          "status": "verrouille"})
        return games

    # --- Bus events ---

    def _publish_game_event(self, event_type: str, session: GameSession, result: dict):
        """Publie un evenement jeu sur le bus (async-safe)."""
        try:
            import asyncio
            from core.event_bus.bus import bus
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(bus.publish(event_type, {
                    "game": session.game_type,
                    "opponent": session.opponent,
                    "result": result,
                    "timestamp": time.time(),
                }))
        except Exception:
            pass

    # --- Persistence ---

    def _save(self):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            data = {
                "version": "1.0",
                "stats": self.stats,
                "game_history": self.game_history[-50:],
            }
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, STATE_FILE)
        except Exception as e:
            logger.warning(f"GAME_HUB: save failed: {e}")

    def _load(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                loaded_stats = data.get("stats", {})
                self.stats.update(loaded_stats)
                self.game_history = data.get("game_history", [])
        except Exception as e:
            logger.warning(f"GAME_HUB: load failed: {e}")

    # --- Playground integration ---

    def record_playground_clear(self, level: int):
        """Enregistre la completion d'un niveau du Physics Playground."""
        if level >= 1:
            self.stats["playground_level1_cleared"] = True
        if level >= 3:
            self.stats["playground_level3_cleared"] = True
        self.stats["playground_best_level"] = max(
            self.stats.get("playground_best_level", 0), level)
        self._check_chess_unlock()
        self._save()


# Singleton
game_hub = GameHub()
