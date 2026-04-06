"""Puissance 4 (Connect Four) — Jeu 3 de Promethee.

Grille 7 colonnes x 6 lignes. Deux joueurs (R et J).
IA : minimax alpha-beta, profondeur limitee.
0 appel LLM, 0 GPU — logique deterministe pure.
"""

import copy
import random
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict, Any

logger = logging.getLogger(__name__)

EMPTY = "."
RED = "R"    # Joueur 1
YELLOW = "J"  # Joueur 2
COLS = 7
ROWS = 6
WIN_LENGTH = 4
AI_MAX_DEPTH = 6


@dataclass
class Puissance4Game:
    """Une partie de puissance 4."""
    board: List[List[str]] = field(default_factory=lambda: [[EMPTY]*COLS for _ in range(ROWS)])
    current_player: str = RED
    winner: Optional[str] = None
    game_over: bool = False
    moves_count: int = 0
    history: List[Tuple[int, str]] = field(default_factory=list)  # (colonne, joueur)

    def reset(self):
        """Reinitialise la partie."""
        self.board = [[EMPTY]*COLS for _ in range(ROWS)]
        self.current_player = RED
        self.winner = None
        self.game_over = False
        self.moves_count = 0
        self.history = []

    def play(self, col: int) -> Dict[str, Any]:
        """Joue un jeton dans la colonne. Retourne le resultat."""
        if self.game_over:
            return {"valid": False, "reason": "Partie terminee"}
        if not (0 <= col < COLS):
            return {"valid": False, "reason": f"Colonne {col} hors grille (0-{COLS-1})"}

        # Trouver la ligne la plus basse disponible
        row = self._drop_row(col)
        if row is None:
            return {"valid": False, "reason": f"Colonne {col} pleine"}

        self.board[row][col] = self.current_player
        self.history.append((col, self.current_player))
        self.moves_count += 1

        # Verifier victoire
        if self._check_winner(row, col, self.current_player):
            self.winner = self.current_player
            self.game_over = True
            return {"valid": True, "status": "win", "winner": self.winner, "row": row, "col": col}

        # Verifier match nul
        if self.moves_count >= ROWS * COLS:
            self.game_over = True
            return {"valid": True, "status": "draw", "row": row, "col": col}

        # Tour suivant
        self.current_player = YELLOW if self.current_player == RED else RED
        return {"valid": True, "status": "continue", "row": row, "col": col}

    def _drop_row(self, col: int) -> Optional[int]:
        """Retourne la ligne ou le jeton tombe, ou None si colonne pleine."""
        for row in range(ROWS - 1, -1, -1):
            if self.board[row][col] == EMPTY:
                return row
        return None

    def _check_winner(self, row: int, col: int, player: str) -> bool:
        """Verifie si le dernier coup (row, col) cree un alignement de 4."""
        directions = [(0, 1), (1, 0), (1, 1), (1, -1)]  # H, V, diag1, diag2
        for dr, dc in directions:
            count = 1
            # Direction positive
            for i in range(1, WIN_LENGTH):
                r, c = row + dr * i, col + dc * i
                if 0 <= r < ROWS and 0 <= c < COLS and self.board[r][c] == player:
                    count += 1
                else:
                    break
            # Direction negative
            for i in range(1, WIN_LENGTH):
                r, c = row - dr * i, col - dc * i
                if 0 <= r < ROWS and 0 <= c < COLS and self.board[r][c] == player:
                    count += 1
                else:
                    break
            if count >= WIN_LENGTH:
                return True
        return False

    def get_valid_columns(self) -> List[int]:
        """Retourne les colonnes jouables."""
        return [c for c in range(COLS) if self.board[0][c] == EMPTY]

    def get_state(self) -> Dict[str, Any]:
        """Retourne l'etat complet du jeu."""
        return {
            "game": "puissance4",
            "board": [row[:] for row in self.board],
            "current_player": self.current_player,
            "winner": self.winner,
            "game_over": self.game_over,
            "moves_count": self.moves_count,
            "valid_columns": self.get_valid_columns(),
        }

    def render(self) -> str:
        """Rendu texte de la grille."""
        lines = []
        # Numeros de colonnes
        lines.append(" " + " ".join(str(c) for c in range(COLS)))
        lines.append(" " + "-" * (COLS * 2 - 1))
        # Grille (ligne 0 = haut)
        for row in range(ROWS):
            cells = "|".join(self.board[row][c] if self.board[row][c] != EMPTY else " " for c in range(COLS))
            lines.append(f"|{cells}|")
        lines.append(" " + "=" * (COLS * 2 - 1))
        if self.game_over:
            if self.winner:
                lines.append(f"{self.winner} gagne!")
            else:
                lines.append("Match nul!")
        else:
            lines.append(f"Tour de {self.current_player}")
        return "\n".join(lines)


# --- IA Minimax Alpha-Beta ---

def _evaluate_window(window: List[str], player: str, opponent: str) -> int:
    """Evalue un segment de 4 cases."""
    p_count = window.count(player)
    o_count = window.count(opponent)
    empty_count = window.count(EMPTY)

    if p_count == 4:
        return 1000
    if o_count == 4:
        return -1000
    if p_count == 3 and empty_count == 1:
        return 10
    if o_count == 3 and empty_count == 1:
        return -10
    if p_count == 2 and empty_count == 2:
        return 3
    if o_count == 2 and empty_count == 2:
        return -3
    return 0


def _evaluate_board(board: List[List[str]], player: str, opponent: str) -> int:
    """Heuristique d'evaluation de la grille."""
    score = 0

    # Bonus colonne centrale
    center_col = COLS // 2
    center_count = sum(1 for r in range(ROWS) if board[r][center_col] == player)
    score += center_count * 4

    # Lignes horizontales
    for r in range(ROWS):
        for c in range(COLS - 3):
            window = [board[r][c+i] for i in range(4)]
            score += _evaluate_window(window, player, opponent)

    # Colonnes verticales
    for c in range(COLS):
        for r in range(ROWS - 3):
            window = [board[r+i][c] for i in range(4)]
            score += _evaluate_window(window, player, opponent)

    # Diagonales montantes
    for r in range(3, ROWS):
        for c in range(COLS - 3):
            window = [board[r-i][c+i] for i in range(4)]
            score += _evaluate_window(window, player, opponent)

    # Diagonales descendantes
    for r in range(ROWS - 3):
        for c in range(COLS - 3):
            window = [board[r+i][c+i] for i in range(4)]
            score += _evaluate_window(window, player, opponent)

    return score


def _is_terminal(board: List[List[str]], player: str, opponent: str) -> bool:
    """Verifie si la partie est terminee."""
    # Victoire ?
    for r in range(ROWS):
        for c in range(COLS):
            if board[r][c] != EMPTY:
                # Check horizontal
                if c + 3 < COLS and all(board[r][c+i] == board[r][c] for i in range(4)):
                    return True
                # Check vertical
                if r + 3 < ROWS and all(board[r+i][c] == board[r][c] for i in range(4)):
                    return True
                # Check diag descendante
                if r + 3 < ROWS and c + 3 < COLS and all(board[r+i][c+i] == board[r][c] for i in range(4)):
                    return True
                # Check diag montante
                if r - 3 >= 0 and c + 3 < COLS and all(board[r-i][c+i] == board[r][c] for i in range(4)):
                    return True
    # Grille pleine ?
    if all(board[0][c] != EMPTY for c in range(COLS)):
        return True
    return False


def _minimax(board: List[List[str]], depth: int, alpha: int, beta: int,
             is_maximizing: bool, player: str, opponent: str) -> int:
    """Minimax avec alpha-beta pruning."""
    if depth == 0 or _is_terminal(board, player, opponent):
        return _evaluate_board(board, player, opponent)

    valid_cols = [c for c in range(COLS) if board[0][c] == EMPTY]

    if is_maximizing:
        max_eval = -100000
        for col in valid_cols:
            row = next(r for r in range(ROWS - 1, -1, -1) if board[r][col] == EMPTY)
            board[row][col] = player
            eval_score = _minimax(board, depth - 1, alpha, beta, False, player, opponent)
            board[row][col] = EMPTY
            max_eval = max(max_eval, eval_score)
            alpha = max(alpha, eval_score)
            if beta <= alpha:
                break
        return max_eval
    else:
        min_eval = 100000
        for col in valid_cols:
            row = next(r for r in range(ROWS - 1, -1, -1) if board[r][col] == EMPTY)
            board[row][col] = opponent
            eval_score = _minimax(board, depth - 1, alpha, beta, True, player, opponent)
            board[row][col] = EMPTY
            min_eval = min(min_eval, eval_score)
            beta = min(beta, eval_score)
            if beta <= alpha:
                break
        return min_eval


def ai_move(game: Puissance4Game, difficulty: str = "hard") -> Optional[int]:
    """Calcule le meilleur coup (colonne) pour le joueur courant.

    difficulty:
      - "easy" : coups aleatoires
      - "medium" : profondeur 3
      - "hard" : profondeur 6 (alpha-beta)
    """
    valid_cols = game.get_valid_columns()
    if not valid_cols:
        return None

    if difficulty == "easy":
        return random.choice(valid_cols)

    depth = 3 if difficulty == "medium" else AI_MAX_DEPTH
    player = game.current_player
    opponent = YELLOW if player == RED else RED
    board = copy.deepcopy(game.board)

    # Victoire immediate ?
    for col in valid_cols:
        row = next(r for r in range(ROWS - 1, -1, -1) if board[r][col] == EMPTY)
        board[row][col] = player
        if _is_terminal(board, player, opponent):
            # Verifier que c'est bien une victoire pour nous
            eval_score = _evaluate_board(board, player, opponent)
            board[row][col] = EMPTY
            if eval_score > 0:
                return col
        board[row][col] = EMPTY

    # Bloquer victoire adverse immediate
    for col in valid_cols:
        row = next(r for r in range(ROWS - 1, -1, -1) if board[r][col] == EMPTY)
        board[row][col] = opponent
        if _is_terminal(board, player, opponent):
            eval_score = _evaluate_board(board, player, opponent)
            board[row][col] = EMPTY
            if eval_score < 0:
                return col
        board[row][col] = EMPTY

    # Minimax
    best_score = -100000
    best_cols = []
    for col in valid_cols:
        row = next(r for r in range(ROWS - 1, -1, -1) if board[r][col] == EMPTY)
        board[row][col] = player
        score = _minimax(board, depth - 1, -100000, 100000, False, player, opponent)
        board[row][col] = EMPTY
        if score > best_score:
            best_score = score
            best_cols = [col]
        elif score == best_score:
            best_cols.append(col)

    return random.choice(best_cols) if best_cols else valid_cols[0]
