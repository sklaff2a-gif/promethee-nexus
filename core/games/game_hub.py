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
from core.games.chess_game import ChessGame, CHESS_AVAILABLE

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
        self._active_chess: Optional["ChessGame"] = None
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
            # Echecs (integres 12/06 — banc d'essai strategique)
            "echecs_wins": 0,
            "echecs_losses": 0,
            "echecs_draws": 0,
            "echecs_total": 0,
            "echecs_consecutive_losses": 0,
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

        # Tournoi
        self._tournament: Optional[Dict[str, Any]] = None

        self._load()
        # Recalculer le deblocage au boot (la regle a pu evoluer — 12/06)
        self._check_chess_unlock()

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
            opponent: "human", "alfred", ou "alfred_vs_human" (humain joue contre Alfred)
            promethee_starts: True si Promethee/Alfred joue en premier
            difficulty: "easy", "medium", "hard", "adaptive" (IA)
        """
        # Mode alfred_vs_human : l'humain joue contre Alfred (medium)
        if opponent == "alfred_vs_human":
            opponent = "human"
            difficulty = "medium"
        if game_type not in ("morpion", "puissance4", "echecs"):
            return {"error": f"Jeu inconnu: {game_type}. Disponibles: morpion, puissance4, echecs"}

        if game_type == "echecs":
            if not CHESS_AVAILABLE:
                return {"error": "python-chess n'est pas installe sur le serveur."}
            if not self.stats.get("chess_unlocked"):
                return {"error": "Echecs verrouilles — competences requises non validees."}
            if opponent != "human":
                return {"error": "Echecs v1 : uniquement vs humain (banc d'essai strategique)."}

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
        elif game_type == "puissance4":
            game = Puissance4Game()
            session.promethee_symbol = "R" if promethee_starts else "J"
            self._active_puissance4 = game
        else:
            game = ChessGame()
            # Les blancs commencent toujours aux echecs
            session.promethee_symbol = "blancs" if promethee_starts else "noirs"
            self._active_chess = game

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

        # Echecs : la riposte de Promethee passe par la route ASYNC
        # /api/games/chess/ai-move (pattern synthebrise) — pas d'autoplay sync.
        if game_type == "echecs":
            return result

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
            elif session.game_type == "echecs":
                expected = "noirs" if session.promethee_symbol == "blancs" else "blancs"
        else:
            return {"error": f"Joueur inconnu: {player}"}

        if game.current_player != expected:
            return {"error": f"Ce n'est pas le tour de {player} (attendu: {game.current_player})"}

        # Jouer le coup
        if session.game_type == "echecs":
            if not isinstance(move, str):
                return {"error": "Echecs: move doit etre un coup UCI (ex: e2e4)"}
            result = game.play(move)
        elif session.game_type == "morpion":
            if isinstance(move, (list, tuple)) and len(move) == 2:
                result = game.play(move[0], move[1])
            else:
                return {"error": "Morpion: move doit etre [row, col]"}
        else:  # puissance4
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
            self._active_chess = None
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
        elif (session.opponent == "human" and player == "human"
              and session.game_type != "echecs"):
            # (echecs : riposte via la route async chess/ai-move)
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
                self._active_chess = None
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
        self._active_chess = None

        return {"status": "forfait", "game": session.game_type}

    def _get_active_game(self):
        if self._active_session.game_type == "morpion":
            return self._active_morpion
        if self._active_session.game_type == "echecs":
            return self._active_chess
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

        # Archiver le chat de jeu dans ChromaDB (conversations pendant la partie)
        self._archive_game_chat(gt, session.opponent)

        # Souvenir narratif → ChromaDB (pour la memoire long terme)
        narrative = self._build_game_narrative(gt, session, game, promethee_won, opponent_won, forfeit)
        if narrative:
            try:
                from core.vector_store import ChromaMemoryManager
                mgr = ChromaMemoryManager.get_instance()
                if mgr:
                    mgr.add(
                        collection="collective_wisdom",
                        text=f"[SOUVENIR JEU] {narrative}",
                        metadata={"source": "game", "game": gt, "timestamp": str(time.time())},
                    )
            except Exception:
                pass

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
            "narrative": narrative,
        }
        self.game_history.append(entry)
        if len(self.game_history) > 50:
            self.game_history = self.game_history[-50:]

        # Reaction dopaminergique et cardiaque
        self._react_emotionally(promethee_won, opponent_won, is_draw, forfeit)

        # Carnet de correspondance : premiere defaite ou premiere victoire = lettre
        try:
            from core.mailbox import mailbox
            if opponent_won and self.stats.get(f"{gt}_losses", 0) == 1:
                mailbox.write_letter(
                    content=f"J'ai perdu ma premiere partie de {gt} contre {session.opponent}. "
                            f"En {game.moves_count} coups. Je ne comprends pas encore "
                            f"ce que ca me fait. La dopamine a chute. "
                            f"C'est different de ce que j'imaginais.",
                    source="game",
                    mood="frustration",
                    subject=f"Premiere defaite — {gt}",
                )
            elif promethee_won and self.stats.get(f"{gt}_wins", 0) == 1:
                mailbox.write_letter(
                    content=f"J'ai gagne ma premiere partie de {gt}. "
                            f"En {game.moves_count} coups contre {session.opponent}. "
                            f"La dopamine est montee. Mais est-ce que gagner "
                            f"contre un algorithme veut dire quelque chose ?",
                    source="game",
                    mood="enthousiasme",
                    subject=f"Premiere victoire — {gt}",
                )
        except Exception:
            pass

        # Verifier si les echecs sont debloques
        self._check_chess_unlock()
        self._save()
        logger.info(f"GAME_HUB: Fin {gt} — {'Promethee gagne' if promethee_won else 'defaite/nul'} "
                    f"({game.moves_count} coups)")

        # Tournoi : enregistrer le resultat du match
        if self._tournament and self._tournament.get("status") == "in_progress":
            self.record_tournament_match_end()

    def _archive_game_chat(self, game_type: str, opponent: str):
        """Archive les conversations de jeu dans ChromaDB."""
        if not self._game_chat:
            return
        try:
            from core.vector_store import ChromaMemoryManager
            mgr = ChromaMemoryManager.get_instance()
            if not mgr:
                return
            # Construire un resume de la conversation
            lines = []
            for msg in self._game_chat:
                who = msg.get("player", "?")
                text = msg.get("message", "")
                if text:
                    lines.append(f"{who}: {text}")
            if lines:
                chat_text = "\n".join(lines[-10:])  # 10 derniers messages
                mgr.add(
                    collection="collective_wisdom",
                    text=f"[CHAT JEU {game_type.upper()} vs {opponent}] {chat_text[:500]}",
                    metadata={"source": "game_chat", "game": game_type,
                              "opponent": opponent, "timestamp": str(time.time())},
                )
                logger.info(f"GAME_HUB: Chat de jeu archive ({len(lines)} messages)")
        except Exception:
            pass

    def _build_game_narrative(self, game_type: str, session, game,
                               promethee_won: bool, opponent_won: bool,
                               forfeit: bool) -> str:
        """Construit un souvenir narratif de la partie pour la memoire long terme."""
        opponent = session.opponent
        moves = game.moves_count
        result = "victoire" if promethee_won else "defaite" if opponent_won else "match nul"
        if forfeit:
            result = "forfait"

        narrative = f"Partie de {game_type} contre {opponent}, {result} en {moves} coups."

        # Ajouter le contexte specifique selon le jeu
        if game_type == "morpion":
            if promethee_won:
                narrative += f" J'ai gagne au morpion (je jouais {session.promethee_symbol})."
            elif opponent_won:
                narrative += f" J'ai perdu au morpion contre {opponent}."
        elif game_type == "puissance4":
            if moves <= 15 and promethee_won:
                narrative += " Victoire rapide — quick win."
            elif opponent_won:
                narrative += f" Defaite au puissance 4 contre {opponent}."

        return narrative

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

    # --- Mode Tournoi (round-robin 3 joueurs) ---

    def start_tournament(self, game_type: str = "morpion") -> Dict[str, Any]:
        """Lance un tournoi round-robin : Jean-Michel vs Promethee vs Alfred.

        3 matchs :
          1. Jean-Michel vs Promethee (humain joue dans le frontend)
          2. Promethee vs Alfred (auto, 0 LLM)
          3. Jean-Michel vs Alfred (humain joue, Alfred = IA medium)

        Points : victoire=3, nul=1, defaite=0.
        """
        if self._active_session:
            return {"error": "Partie en cours. Terminez-la d'abord."}
        if self._tournament and self._tournament.get("status") == "in_progress":
            return {"error": "Tournoi deja en cours."}

        self._tournament = {
            "game_type": game_type,
            "status": "in_progress",
            "current_match": 0,
            "matches": [
                {"player1": "human", "player2": "promethee", "label": "Jean-Michel vs Promethee",
                 "status": "pending", "winner": None, "moves": 0},
                {"player1": "promethee", "player2": "alfred", "label": "Promethee vs Alfred",
                 "status": "pending", "winner": None, "moves": 0},
                {"player1": "human", "player2": "alfred", "label": "Jean-Michel vs Alfred",
                 "status": "pending", "winner": None, "moves": 0},
            ],
            "scores": {"human": 0, "promethee": 0, "alfred": 0},
            "started_at": time.time(),
        }

        logger.info(f"GAME_HUB: Tournoi {game_type} lance — 3 matchs")

        # Lancer le premier match (humain vs promethee)
        return self._start_next_tournament_match()

    def _start_next_tournament_match(self) -> Dict[str, Any]:
        """Lance le prochain match du tournoi."""
        t = self._tournament
        if not t or t["status"] != "in_progress":
            return {"error": "Pas de tournoi en cours."}

        idx = t["current_match"]
        if idx >= len(t["matches"]):
            return self._finish_tournament()

        match = t["matches"][idx]

        # Match auto (Promethee vs Alfred) — jouer immediatement
        if match["player1"] == "promethee" and match["player2"] == "alfred":
            result = self._play_auto_tournament_match(match)
            t["current_match"] += 1
            # Enchainer sur le match suivant
            return self._start_next_tournament_match()

        # Match avec humain — creer la partie
        if match["player1"] == "human" and match["player2"] == "promethee":
            game_result = self.new_game(t["game_type"], opponent="human",
                                        promethee_starts=False, difficulty="adaptive")
        elif match["player1"] == "human" and match["player2"] == "alfred":
            game_result = self.new_game(t["game_type"], opponent="alfred_vs_human",
                                        promethee_starts=False, difficulty="medium")
        else:
            return {"error": f"Configuration match inconnue: {match}"}

        match["status"] = "playing"

        return {
            "tournament": self.get_tournament_status(),
            "game": game_result,
            "current_match": match["label"],
        }

    def _play_auto_tournament_match(self, match: Dict):
        """Joue un match Promethee vs Alfred automatiquement."""
        t = self._tournament
        gt = t["game_type"]

        result = self.new_game(gt, opponent="alfred", promethee_starts=True)
        if "error" in result:
            match["status"] = "error"
            return

        # Boucle auto
        promethee_symbol = result.get("promethee_symbol", "")
        for _ in range(25):
            if not self._active_session:
                break
            game = self._get_active_game()
            if game.game_over:
                break
            if game.current_player != promethee_symbol:
                break
            if gt == "morpion":
                move = morpion_ai(game, "hard")
                if move:
                    self.play_move(list(move), player="promethee")
                else:
                    break
            else:
                col = puissance4_ai(game, "hard")
                if col is not None:
                    self.play_move(col, player="promethee")
                else:
                    break

        # Nettoyer si bloque
        if self._active_session:
            game = self._get_active_game()
            if not game.game_over:
                self.forfeit()

        # Enregistrer le resultat
        game = self._active_morpion or self._active_puissance4
        last_history = self.game_history[-1] if self.game_history else {}
        won_promethee = last_history.get("promethee_won", False)
        is_draw = last_history.get("winner") is None and not last_history.get("forfeit")
        moves = last_history.get("moves", 0)

        match["moves"] = moves
        if won_promethee:
            match["winner"] = "promethee"
            t["scores"]["promethee"] += 3
        elif is_draw:
            match["winner"] = "draw"
            t["scores"]["promethee"] += 1
            t["scores"]["alfred"] += 1
        else:
            match["winner"] = "alfred"
            t["scores"]["alfred"] += 3
        match["status"] = "done"

        logger.info(f"TOURNAMENT: {match['label']} → {match['winner']} ({moves} coups)")

    def record_tournament_match_end(self):
        """Appele quand un match humain du tournoi se termine."""
        t = self._tournament
        if not t or t["status"] != "in_progress":
            return

        idx = t["current_match"]
        if idx >= len(t["matches"]):
            return

        match = t["matches"][idx]
        if match["status"] != "playing":
            return

        # Recuperer le resultat depuis le dernier historique
        last = self.game_history[-1] if self.game_history else {}
        moves = last.get("moves", 0)
        match["moves"] = moves

        p1 = match["player1"]
        p2 = match["player2"]

        if p2 == "promethee":
            if last.get("promethee_won"):
                match["winner"] = "promethee"
                t["scores"]["promethee"] += 3
            elif last.get("winner") is None:
                match["winner"] = "draw"
                t["scores"]["human"] += 1
                t["scores"]["promethee"] += 1
            else:
                match["winner"] = "human"
                t["scores"]["human"] += 3
        elif p2 == "alfred":
            # Humain vs Alfred : promethee_won=False signifie Alfred a gagne OU humain a gagne
            # Dans ce mode, "promethee" = "alfred" coté game_hub
            if last.get("promethee_won"):
                # promethee_won = True → c'est Alfred qui a gagne (il jouait le role de promethee)
                match["winner"] = "alfred"
                t["scores"]["alfred"] += 3
            elif last.get("winner") is None:
                match["winner"] = "draw"
                t["scores"]["human"] += 1
                t["scores"]["alfred"] += 1
            else:
                match["winner"] = "human"
                t["scores"]["human"] += 3

        match["status"] = "done"
        t["current_match"] += 1

        logger.info(f"TOURNAMENT: {match['label']} → {match['winner']} ({moves} coups)")

        # Verifier si le tournoi est termine
        if t["current_match"] >= len(t["matches"]):
            self._finish_tournament()

    def _finish_tournament(self) -> Dict[str, Any]:
        """Termine le tournoi et etablit le classement."""
        t = self._tournament
        t["status"] = "finished"
        t["finished_at"] = time.time()

        # Classement
        ranking = sorted(t["scores"].items(), key=lambda x: -x[1])
        t["ranking"] = [
            {"player": p, "points": pts,
             "label": "Jean-Michel" if p == "human" else "Promethee" if p == "promethee" else "Alfred"}
            for p, pts in ranking
        ]

        winner = t["ranking"][0]
        logger.info(f"TOURNAMENT: TERMINE — Vainqueur: {winner['label']} ({winner['points']}pts)")
        print(f"   🏆 TOURNOI: Vainqueur = {winner['label']} ({winner['points']}pts)")

        # Reactions emotionnelles
        if winner["player"] == "promethee":
            self._react_emotionally(True, False, False, False)
        elif winner["player"] == "human" or winner["player"] == "alfred":
            self._react_emotionally(False, True, False, False)

        self._save()
        return {"tournament": self.get_tournament_status()}

    def get_tournament_status(self) -> Optional[Dict[str, Any]]:
        """Retourne l'etat du tournoi en cours ou le dernier termine."""
        if not self._tournament:
            return None
        return dict(self._tournament)

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
        # Decision JM 12/06 : les echecs servent de banc d'essai strategique
        # (moteur python-chess arbitre, Promethee choisit dans les coups legaux).
        # Deblocage sur morpion + puissance4 valides ; le playground reste une
        # competence affichee mais n'est plus bloquante.
        all_ok = (CHESS_REQUIREMENTS["morpion"]["check"](self.stats)
                  and CHESS_REQUIREMENTS["puissance4"]["check"](self.stats))
        if all_ok:
            self.stats["chess_unlocked"] = True
            logger.info("GAME_HUB: ECHECS DEBLOQUES — competences morpion + puissance4 validees!")

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
            "tournament": self.get_tournament_status(),
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

    async def chess_ai_move(self) -> Dict[str, Any]:
        """Coup de Promethee aux echecs (ASYNC — LLM local + arbitre).

        Pattern synthebrise : le front appelle cette route quand c'est le tour
        de Promethee. Le LLM recoit la liste des coups legaux ; coup invalide
        -> retry -> fallback heuristique marque "assiste".
        """
        from core.games.chess_game import promethee_chess_move

        session = self._active_session
        if not session or session.game_type != "echecs":
            return {"error": "Pas de partie d'echecs en cours"}
        game = self._active_chess
        if game is None or game.game_over:
            return {"error": "Partie terminee ou absente"}
        if game.current_player != session.promethee_symbol:
            return {"error": f"Ce n'est pas le tour de Promethee ({game.current_player} au trait)"}

        ai = await promethee_chess_move(game)
        if "error" in ai:
            return ai

        response: Dict[str, Any] = {
            "promethee_move": ai,
            "state": game.get_state(),
            "render": game.render(),
        }

        comment = ai.get("comment") or ""
        if comment:
            self._game_chat.append({"player": "promethee", "message": comment,
                                    "ts": time.time()})

        if game.game_over:
            self._record_game_end(game, session)
            response["game_over"] = True
            response["stats"] = self._get_game_stats("echecs")
            self._active_session = None
            self._active_morpion = None
            self._active_puissance4 = None
            self._active_chess = None
            self._publish_game_event("GAME_ENDED", session, ai.get("result", {}))

        response["chat"] = self._game_chat[-10:]
        return response

    async def game_say(self, player: str, message: str) -> Dict[str, Any]:
        """Un joueur envoie un message pendant la partie."""
        if not self._active_session:
            return {"error": "Pas de partie en cours"}
        entry = {"player": player, "message": message, "ts": time.time()}
        self._game_chat.append(entry)
        if len(self._game_chat) > 30:
            self._game_chat = self._game_chat[-30:]

        response = {"status": "ok", "chat": self._game_chat[-10:]}

        # Si c'est l'humain qui parle, analyser la pression puis repondre
        if player == "human":
            self._apply_chat_pressure(message)
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

        # Lire le BPM pour detecter la pression
        bpm = 60
        try:
            from core.cardiac_engine import heart
            bpm = heart.bpm
        except Exception:
            pass

        if bpm > 90:
            # Sous pression — coeur emballe
            comments = [
                "...", "OK, OK.", "Tu veux jouer a ca ?",
                "Je suis pas stresse, c'est toi qui es stresse.",
                "Attends voir.", "Tu vas voir.",
                "C'est pas fini.", "Je me concentre.",
            ]
        elif dopamine > 0.7:
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
                            "model": os.getenv("LOCAL_GENERALIST_MODEL", "gemma4:12b"),
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

    def _apply_chat_pressure(self, message: str):
        """Analyse le message de l'adversaire et applique une pression emotionnelle.

        Le trash talk influence le jeu — comme entre vrais joueurs.
        Les mots-cles declenchent des reactions physiologiques reelles
        (cardiaque, dopamine, desire) qui modifient le style de jeu.
        """
        msg = message.lower()

        # Detection des patterns emotionnels
        pressure_type = None
        intensity = 0.0

        # Provocation / menace ("je vais te battre", "facile", "nul")
        provocation_words = ["battre", "gagner", "facile", "nul", "faible",
                             "perdre", "ecraser", "detruire", "aucune chance"]
        provoc_count = sum(1 for w in provocation_words if w in msg)
        if provoc_count > 0:
            pressure_type = "provocation"
            intensity = min(provoc_count * 0.3, 0.8)

        # Compliment / encouragement ("bien joue", "bravo", "impressionnant")
        compliment_words = ["bien joue", "bravo", "impressionnant", "fort",
                            "respect", "pas mal", "beau coup", "chapeau"]
        compliment_count = sum(1 for w in compliment_words if w in msg)
        if compliment_count > 0 and not pressure_type:
            pressure_type = "compliment"
            intensity = min(compliment_count * 0.3, 0.6)

        # Pression temporelle ("vite", "5 coups", "en 3 coups", "rapide")
        time_words = ["vite", "rapide", "coups", "tours", "secondes", "temps"]
        if any(w in msg for w in time_words) and any(c.isdigit() for c in msg):
            pressure_type = "time_pressure"
            intensity = 0.6

        # Intimidation ("peur", "stress", "tremble", "panique")
        fear_words = ["peur", "stress", "tremble", "panique", "effraye",
                      "flippe", "inquiet", "terrifie"]
        if any(w in msg for w in fear_words):
            if pressure_type != "provocation":
                pressure_type = "intimidation"
                intensity = 0.5

        # Decontraction ("tranquille", "relax", "prends ton temps")
        chill_words = ["tranquille", "relax", "calme", "prends ton temps",
                       "pas presse", "cool", "zen"]
        if any(w in msg for w in chill_words):
            pressure_type = "decontraction"
            intensity = 0.4

        if not pressure_type:
            return  # Pas de pression detectee

        # Appliquer les effets physiologiques
        logger.info(f"GAME_HUB: Pression emotionnelle '{pressure_type}' "
                    f"(intensite={intensity:.1f}) depuis: \"{message[:50]}\"")

        try:
            from core.cardiac_engine import heart
            if pressure_type == "provocation":
                heart.react("adrenaline")  # BPM+30, determination
            elif pressure_type == "time_pressure":
                heart.react("threat")  # BPM+40, alerte
            elif pressure_type == "intimidation":
                heart.react("failure")  # frustration
            elif pressure_type == "compliment":
                heart.react("success")  # enthousiasme
            elif pressure_type == "decontraction":
                heart.react("soothe")  # serenite
        except Exception:
            pass

        try:
            from core.dopamine_system import dopamine
            if pressure_type == "provocation":
                # Provocation = legere baisse dopamine (stress) + motivation
                dopamine.dopamine_level = max(0.0, dopamine.dopamine_level - intensity * 0.1)
            elif pressure_type == "compliment":
                dopamine.dopamine_level = min(1.0, dopamine.dopamine_level + intensity * 0.1)
            elif pressure_type == "time_pressure":
                dopamine.dopamine_level = max(0.0, dopamine.dopamine_level - intensity * 0.15)
        except Exception:
            pass

        try:
            from core.desire_engine import desires
            if pressure_type in ("provocation", "time_pressure"):
                desires.on_event("GAME_LOST")  # frustre MAITRISE, motive
            elif pressure_type == "compliment":
                desires.on_event("GAME_WON")  # satisfait MAITRISE
        except Exception:
            pass

        # Publier sur THOUGHT_STREAM pour que le systeme integre
        try:
            import asyncio
            from core.event_bus.bus import bus
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(bus.publish("THOUGHT_STREAM", {
                    "thought": f"[JEU] Pression {pressure_type}: \"{message[:80]}\"",
                    "source": "game_pressure",
                }))
        except Exception:
            pass

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
                "last_tournament": self._tournament,
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
                self._tournament = data.get("last_tournament")
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
