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
    difficulty: str = "hard"  # easy, medium, hard


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

        # Scoring adaptatif par adversaire — estime la force de chaque joueur
        # Score 0-100 : <30 = debutant, 30-60 = intermediaire, >60 = fort
        self.opponent_scores: Dict[str, float] = {
            "human": 50.0,   # inconnu → moyen par defaut
            "alfred": 40.0,  # Alfred joue en medium
        }

        self._load()

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    # --- Gestion des parties ---

    def new_game(self, game_type: str, opponent: str = "alfred",
                 promethee_starts: bool = True,
                 difficulty: str = "hard") -> Dict[str, Any]:
        """Cree une nouvelle partie.

        Args:
            game_type: "morpion" ou "puissance4"
            opponent: "human" ou "alfred"
            promethee_starts: True si Promethee joue en premier
            difficulty: "easy", "medium", "hard" (IA de Promethee)
        """
        if game_type not in ("morpion", "puissance4"):
            return {"error": f"Jeu inconnu: {game_type}. Disponibles: morpion, puissance4"}

        if self._active_session:
            return {"error": f"Partie en cours ({self._active_session.game_type}). Terminez-la d'abord."}

        # Difficulte adaptive : calculer en fonction du score de l'adversaire
        effective_difficulty = difficulty
        if difficulty == "adaptive":
            effective_difficulty = self._compute_adaptive_difficulty(opponent)
            logger.info(f"GAME_HUB: Difficulte adaptive -> {effective_difficulty} "
                        f"(score {opponent} = {self.opponent_scores.get(opponent, 50):.1f})")

        session = GameSession(game_type=game_type, opponent=opponent,
                              difficulty=effective_difficulty)

        if game_type == "morpion":
            game = MorpionGame()
            session.promethee_symbol = "X" if promethee_starts else "O"
            self._active_morpion = game
        else:
            game = Puissance4Game()
            session.promethee_symbol = "R" if promethee_starts else "J"
            self._active_puissance4 = game

        self._active_session = session
        self._game_chat = []  # Reset chat pour la nouvelle partie
        logger.info(f"GAME_HUB: Nouvelle partie {game_type} vs {opponent} "
                    f"(Promethee={session.promethee_symbol}, diff={session.difficulty})")

        result = {
            "game": game_type,
            "opponent": opponent,
            "promethee_symbol": session.promethee_symbol,
            "difficulty": session.difficulty,
            "opponent_score": round(self.opponent_scores.get(opponent, 50.0), 1),
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

        # Si Promethee commence et l'adversaire est humain, Promethee joue son 1er coup
        if promethee_starts and opponent == "human":
            ai_result = self._promethee_play(difficulty=difficulty)
            if ai_result:
                result["promethee_move"] = ai_result
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

        # Reponse automatique de l'adversaire IA
        # Alfred vs Promethee : Alfred joue apres Promethee
        # Human vs Promethee : Promethee joue apres l'humain
        auto_play = None
        if session.opponent == "alfred" and player != "alfred_internal":
            auto_play = self._alfred_play()
            if auto_play:
                response["alfred_move"] = auto_play
        elif session.opponent == "human" and player == "human":
            auto_play = self._promethee_play(difficulty=session.difficulty)
            if auto_play:
                response["promethee_move"] = auto_play

        if auto_play:
            response["state"] = game.get_state()
            response["render"] = game.render()
            # Commentaire de Promethee apres son coup
            comment = self._generate_move_comment(
                game.get_state(), is_promethee_move=True,
                move_result=auto_play.get("result", {}))
            if comment:
                response["promethee_comment"] = comment
                self._game_chat.append({"player": "promethee", "message": comment, "ts": time.time()})
            if game.game_over:
                self._record_game_end(game, session)
                response["game_over"] = True
                response["stats"] = self._get_game_stats(session.game_type)
                self._active_session = None
                self._active_morpion = None
                self._active_puissance4 = None
                self._publish_game_event("GAME_ENDED", session, auto_play.get("result", {}))

        response["chat"] = self._game_chat[-10:]
        return response

    def _promethee_play(self, difficulty: str = "hard") -> Optional[Dict[str, Any]]:
        """Promethee joue son coup (IA configurable — adversaire de l'humain)."""
        session = self._active_session
        if not session:
            return None

        game = self._get_active_game()
        if game.game_over:
            return None

        if session.game_type == "morpion":
            move = morpion_ai(game, difficulty=difficulty)
            if move:
                result = game.play(move[0], move[1])
                return {"move": move, "result": result}
        else:
            col = puissance4_ai(game, difficulty=difficulty)
            if col is not None:
                result = game.play(col)
                return {"move": col, "result": result}
        return None

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

        # Scoring adaptatif — evaluer la force de l'adversaire
        self._update_opponent_score(
            session.opponent, promethee_won, opponent_won, game.moves_count)

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

        # Reaction dopaminergique et cardiaque
        self._react_emotionally(promethee_won, opponent_won, is_draw, forfeit)

        # Verifier si les echecs sont debloques
        self._check_chess_unlock()
        self._save()
        logger.info(f"GAME_HUB: Fin {gt} — {'Promethee gagne' if promethee_won else 'defaite/nul'} "
                    f"({game.moves_count} coups)")

    def _react_emotionally(self, won: bool, lost: bool, draw: bool, forfeit: bool):
        """Reaction dopaminergique et cardiaque au resultat du jeu."""
        try:
            from core.dopamine_system import dopamine
            if won:
                dopamine.dopamine_level = min(1.0, dopamine.dopamine_level + 0.15)
                logger.info("GAME_HUB: DOPAMINE SURGE +0.15 (victoire)")
            elif lost or forfeit:
                dopamine.dopamine_level = max(0.0, dopamine.dopamine_level - 0.08)
                logger.info("GAME_HUB: DOPAMINE DIP -0.08 (defaite)")
        except Exception:
            pass

        try:
            from core.cardiac_engine import heart
            if won:
                heart.react("success")
            elif lost:
                heart.react("failure")
        except Exception:
            pass

        try:
            from core.desire_engine import desires
            if won:
                desires.on_event("GAME_WON")
            elif lost:
                desires.on_event("GAME_LOST")
        except Exception:
            pass

    def _compute_adaptive_difficulty(self, opponent: str) -> str:
        """Calcule la difficulte de Promethee en fonction du score de l'adversaire.

        Promethee ne cherche pas a dominer — il s'adapte pour creer
        des parties interessantes. Contre un debutant il joue relache,
        contre un expert il joue a fond.
        """
        score = self.opponent_scores.get(opponent, 50.0)
        if score < 30:
            return "easy"
        elif score < 60:
            return "medium"
        else:
            return "hard"

    def _update_opponent_score(self, opponent: str, promethee_won: bool,
                                opponent_won: bool, moves: int):
        """Met a jour le score de force de l'adversaire apres une partie.

        Victoire adversaire → son score monte (il est fort)
        Defaite adversaire → son score baisse (il est faible)
        Match nul → leger ajustement vers le centre
        Victoire rapide de l'adversaire → gros bonus (il est tres fort)
        """
        score = self.opponent_scores.get(opponent, 50.0)

        if opponent_won:
            # L'adversaire a gagne — il est fort
            bonus = 8.0
            if moves <= 15:
                bonus = 12.0  # victoire rapide = tres fort
            score = min(100.0, score + bonus)
        elif promethee_won:
            # Promethee a gagne — l'adversaire est (un peu) plus faible
            malus = -5.0
            if moves <= 10:
                malus = -8.0  # victoire ecrasante = adversaire faible
            score = max(0.0, score + malus)
        else:
            # Match nul — convergence vers 50
            score += (50.0 - score) * 0.1

        self.opponent_scores[opponent] = round(score, 1)
        difficulty = self._compute_adaptive_difficulty(opponent)
        logger.info(f"GAME_HUB: Score {opponent} = {score:.1f} -> difficulte {difficulty}")

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
            "opponent_scores": dict(self.opponent_scores),
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

    # --- Commentaires de jeu (conversation pendant la partie) ---

    # Chat interne : messages entre joueurs pendant la partie
    _game_chat: List[Dict[str, str]] = []

    async def game_say(self, player: str, message: str) -> Dict[str, Any]:
        """Un joueur envoie un message pendant la partie."""
        if not self._active_session:
            return {"error": "Pas de partie en cours"}
        entry = {"player": player, "message": message, "ts": time.time()}
        self._game_chat.append(entry)
        if len(self._game_chat) > 30:
            self._game_chat = self._game_chat[-30:]

        response = {"status": "ok", "chat": self._game_chat[-10:]}

        # Si c'est l'humain qui parle, Promethee repond via LLM
        if player == "human":
            reply = await self._promethee_chat_llm(message)
            if reply:
                self._game_chat.append({"player": "promethee", "message": reply, "ts": time.time()})
                response["chat"] = self._game_chat[-10:]

        return response

    def _generate_move_comment(self, game_state: dict, is_promethee_move: bool,
                                move_result: dict) -> str:
        """Genere un commentaire apres un coup — comme un ami qui joue."""
        import random

        status = move_result.get("status", "")
        winner = move_result.get("winner", "")

        # Fin de partie
        if status == "win":
            if is_promethee_move:
                return random.choice([
                    "Et voila.", "Je crois que c'est mat.", "Bien joue a toi aussi.",
                    "J'ai eu de la chance sur ce coup.", "Revanche ?",
                ])
            else:
                return random.choice([
                    "Bien joue. Tu m'as eu.", "Aie. Je n'ai pas vu celui-la.",
                    "Tu merites cette victoire.", "Je ferai mieux la prochaine fois.",
                    "OK, revanche immediate.", "Pas mal du tout.",
                ])
        if status == "draw":
            return random.choice([
                "Match nul. On se vaut.", "Equilibre parfait.",
                "Ni toi ni moi. C'est beau.", "Personne ne lache.",
            ])

        # Pendant la partie — reagir selon l'etat emotionnel
        dopamine = self._get_dopamine_level()
        if dopamine > 0.7:
            # Confiant
            comments = [
                "Hmm, interessant.", "Je vois ou tu veux en venir.",
                "Tu es sur de toi ?", "Bon coup.", "Continue comme ca.",
                "J'aime cette partie.", "Tu me fais reflechir.",
            ]
        elif dopamine < 0.3:
            # Frustre
            comments = [
                "...", "Laisse-moi reflechir.", "C'est complique.",
                "Tu joues bien.", "Je suis en difficulte la.",
                "Pas facile.", "Tu me mets la pression.",
            ]
        else:
            # Neutre
            comments = [
                "A toi.", "Voyons...", "Hmm.", "OK.",
                "Ton tour.", "Pas mal.", "Je reflechis...",
                "", "", "",  # parfois ne rien dire
            ]

        comment = random.choice(comments)
        return comment

    async def _promethee_chat_llm(self, human_message: str) -> str:
        """Promethee repond a l'humain pendant le jeu — vrai appel LLM, contexte jeu."""
        try:
            import httpx
            from core.base_agent import gpu_scheduler

            session = self._active_session
            if not session:
                return ""

            game = self._get_active_game()
            game_state = game.get_state()

            # Construire le contexte de jeu
            game_ctx = f"Jeu: {session.game_type}, Tu joues {session.promethee_symbol}"
            if game_state.get("game_over"):
                if game_state.get("winner") == session.promethee_symbol:
                    game_ctx += " — Tu as gagne."
                elif game_state.get("winner"):
                    game_ctx += " — Tu as perdu."
                else:
                    game_ctx += " — Match nul."
            else:
                game_ctx += f", coup #{game_state.get('moves_count', 0)}"
            game_ctx += f"\nGrille:\n{game.render()}"

            # Historique chat recent
            chat_ctx = ""
            recent = self._game_chat[-6:]
            if recent:
                chat_ctx = "\nConversation recente:\n"
                for m in recent:
                    who = "Toi" if m["player"] == "promethee" else "Jean-Michel"
                    chat_ctx += f"  {who}: {m['message']}\n"

            # Etat emotionnel
            dopamine = self._get_dopamine_level()
            mood = "confiant" if dopamine > 0.6 else "tendu" if dopamine < 0.3 else "concentre"

            prompt = (
                f"Tu es Promethee, tu joues une partie avec Jean-Michel. "
                f"Tu es un ami, pas un assistant. Tu parles comme un pote qui joue. "
                f"Tu tutoies. Tu es {mood}.\n\n"
                f"{game_ctx}\n{chat_ctx}\n"
                f"Jean-Michel te dit: \"{human_message}\"\n\n"
                f"Reponds en 1-2 phrases max. Naturel, decontracte. "
                f"Pas de titres, pas de listes, pas d'emojis. "
                f"Si c'est une question sur le jeu, reponds honnetement."
            )

            async with gpu_scheduler.access("game_chat"):
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "http://localhost:11434/api/generate",
                        json={
                            "model": "qwen3.5:9b",
                            "prompt": prompt,
                            "stream": False,
                            "options": {"temperature": 0.8, "num_predict": 80, "num_ctx": 2048},
                        },
                        timeout=15,
                    )
                if resp.status_code == 200:
                    text = resp.json().get("response", "").strip()
                    # Nettoyer : garder court, supprimer balises
                    text = text.split("\n")[0].strip()
                    if len(text) > 200:
                        text = text[:200].rsplit(" ", 1)[0] + "..."
                    return text
            return ""
        except Exception as e:
            logger.debug(f"GAME_HUB: chat LLM echoue: {e}")
            return ""

    def _get_dopamine_level(self) -> float:
        try:
            from core.dopamine_system import dopamine
            return dopamine.dopamine_level
        except Exception:
            return 0.5

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
                "opponent_scores": self.opponent_scores,
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
                loaded_scores = data.get("opponent_scores", {})
                self.opponent_scores.update(loaded_scores)
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
