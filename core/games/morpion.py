"""Morpion (Tic-Tac-Toe) — Jeu 2 de Promethee.

Grille 3x3, deux joueurs (X et O). Jouable vs humain ou Alfred.
IA : minimax pur (jeu resolu, toujours optimal).
0 appel LLM, 0 GPU — logique deterministe pure.
"""

import copy
import random
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict, Any

logger = logging.getLogger(__name__)

EMPTY = " "
X = "X"
O = "O"


@dataclass
class MorpionGame:
    """Une partie de morpion."""
    board: List[List[str]] = field(default_factory=lambda: [[EMPTY]*3 for _ in range(3)])
    current_player: str = X
    winner: Optional[str] = None
    game_over: bool = False
    moves_count: int = 0
    history: List[Tuple[int, int, str]] = field(default_factory=list)

    def reset(self):
        """Reinitialise la partie."""
        self.board = [[EMPTY]*3 for _ in range(3)]
        self.current_player = X
        self.winner = None
        self.game_over = False
        self.moves_count = 0
        self.history = []

    def play(self, row: int, col: int) -> Dict[str, Any]:
        """Joue un coup. Retourne le resultat."""
        if self.game_over:
            return {"valid": False, "reason": "Partie terminee"}
        if not (0 <= row < 3 and 0 <= col < 3):
            return {"valid": False, "reason": "Position hors grille"}
        if self.board[row][col] != EMPTY:
            return {"valid": False, "reason": "Case occupee"}

        self.board[row][col] = self.current_player
        self.history.append((row, col, self.current_player))
        self.moves_count += 1

        # Verifier victoire
        if self._check_winner(self.current_player):
            self.winner = self.current_player
            self.game_over = True
            return {"valid": True, "status": "win", "winner": self.winner}

        # Verifier match nul
        if self.moves_count >= 9:
            self.game_over = True
            return {"valid": True, "status": "draw"}

        # Tour suivant
        self.current_player = O if self.current_player == X else X
        return {"valid": True, "status": "continue"}

    def _check_winner(self, player: str) -> bool:
        """Verifie si le joueur a gagne."""
        b = self.board
        # Lignes
        for row in range(3):
            if all(b[row][c] == player for c in range(3)):
                return True
        # Colonnes
        for col in range(3):
            if all(b[r][col] == player for r in range(3)):
                return True
        # Diagonales
        if all(b[i][i] == player for i in range(3)):
            return True
        if all(b[i][2-i] == player for i in range(3)):
            return True
        return False

    def get_valid_moves(self) -> List[Tuple[int, int]]:
        """Retourne les cases vides."""
        moves = []
        for r in range(3):
            for c in range(3):
                if self.board[r][c] == EMPTY:
                    moves.append((r, c))
        return moves

    def get_state(self) -> Dict[str, Any]:
        """Retourne l'etat complet du jeu."""
        return {
            "game": "morpion",
            "board": [row[:] for row in self.board],
            "current_player": self.current_player,
            "winner": self.winner,
            "game_over": self.game_over,
            "moves_count": self.moves_count,
            "valid_moves": self.get_valid_moves(),
        }

    def render(self) -> str:
        """Rendu texte de la grille."""
        lines = []
        for r in range(3):
            cells = " | ".join(self.board[r][c] if self.board[r][c] != EMPTY else str(r*3+c+1) for c in range(3))
            lines.append(f" {cells} ")
            if r < 2:
                lines.append("-----------")
        if self.game_over:
            if self.winner:
                lines.append(f"\n{self.winner} gagne!")
            else:
                lines.append("\nMatch nul!")
        else:
            lines.append(f"\nTour de {self.current_player}")
        return "\n".join(lines)


# --- IA Minimax (jeu resolu) ---

def _minimax(board: List[List[str]], is_maximizing: bool, player: str, opponent: str) -> int:
    """Minimax pur — toujours optimal."""
    # Verifier etat terminal
    if _has_won(board, player):
        return 1
    if _has_won(board, opponent):
        return -1
    if not _get_empty(board):
        return 0

    if is_maximizing:
        best = -2
        for r, c in _get_empty(board):
            board[r][c] = player
            score = _minimax(board, False, player, opponent)
            board[r][c] = EMPTY
            best = max(best, score)
        return best
    else:
        best = 2
        for r, c in _get_empty(board):
            board[r][c] = opponent
            score = _minimax(board, True, player, opponent)
            board[r][c] = EMPTY
            best = min(best, score)
        return best


def _has_won(board: List[List[str]], player: str) -> bool:
    for r in range(3):
        if all(board[r][c] == player for c in range(3)):
            return True
    for c in range(3):
        if all(board[r][c] == player for r in range(3)):
            return True
    if all(board[i][i] == player for i in range(3)):
        return True
    if all(board[i][2-i] == player for i in range(3)):
        return True
    return False


def _get_empty(board: List[List[str]]) -> List[Tuple[int, int]]:
    return [(r, c) for r in range(3) for c in range(3) if board[r][c] == EMPTY]


def ai_move(game: MorpionGame, difficulty: str = "hard") -> Optional[Tuple[int, int]]:
    """Calcule le meilleur coup pour le joueur courant.

    difficulty:
      - "easy" : coups aleatoires
      - "medium" : minimax 50% du temps, aleatoire sinon
      - "hard" : minimax pur (imbattable)
    """
    moves = game.get_valid_moves()
    if not moves:
        return None

    if difficulty == "easy":
        return random.choice(moves)

    if difficulty == "medium" and random.random() < 0.5:
        return random.choice(moves)

    # Minimax
    player = game.current_player
    opponent = O if player == X else X
    board = copy.deepcopy(game.board)

    best_score = -2
    best_moves = []
    for r, c in moves:
        board[r][c] = player
        score = _minimax(board, False, player, opponent)
        board[r][c] = EMPTY
        if score > best_score:
            best_score = score
            best_moves = [(r, c)]
        elif score == best_score:
            best_moves.append((r, c))

    return random.choice(best_moves) if best_moves else moves[0]
