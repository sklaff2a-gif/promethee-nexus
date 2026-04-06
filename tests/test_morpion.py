"""Tests du morpion (tic-tac-toe)."""

import pytest
from core.games.morpion import MorpionGame, ai_move, EMPTY, X, O


class TestMorpionBase:
    """Tests de la mecanique de base."""

    def test_init_empty_board(self):
        game = MorpionGame()
        assert all(game.board[r][c] == EMPTY for r in range(3) for c in range(3))
        assert game.current_player == X
        assert not game.game_over

    def test_play_valid(self):
        game = MorpionGame()
        result = game.play(0, 0)
        assert result["valid"]
        assert result["status"] == "continue"
        assert game.board[0][0] == X
        assert game.current_player == O

    def test_play_occupied(self):
        game = MorpionGame()
        game.play(0, 0)
        result = game.play(0, 0)
        assert not result["valid"]
        assert "occupee" in result["reason"]

    def test_play_out_of_bounds(self):
        game = MorpionGame()
        result = game.play(3, 0)
        assert not result["valid"]

    def test_play_after_game_over(self):
        game = MorpionGame()
        game.game_over = True
        result = game.play(0, 0)
        assert not result["valid"]

    def test_alternating_players(self):
        game = MorpionGame()
        game.play(0, 0)
        assert game.current_player == O
        game.play(0, 1)
        assert game.current_player == X

    def test_reset(self):
        game = MorpionGame()
        game.play(0, 0)
        game.play(1, 1)
        game.reset()
        assert all(game.board[r][c] == EMPTY for r in range(3) for c in range(3))
        assert game.current_player == X
        assert game.moves_count == 0


class TestMorpionWin:
    """Tests des conditions de victoire."""

    def test_win_row(self):
        game = MorpionGame()
        game.play(0, 0)  # X
        game.play(1, 0)  # O
        game.play(0, 1)  # X
        game.play(1, 1)  # O
        result = game.play(0, 2)  # X gagne ligne 0
        assert result["status"] == "win"
        assert result["winner"] == X

    def test_win_col(self):
        game = MorpionGame()
        game.play(0, 0)  # X
        game.play(0, 1)  # O
        game.play(1, 0)  # X
        game.play(1, 1)  # O
        result = game.play(2, 0)  # X gagne colonne 0
        assert result["status"] == "win"

    def test_win_diagonal(self):
        game = MorpionGame()
        game.play(0, 0)  # X
        game.play(0, 1)  # O
        game.play(1, 1)  # X
        game.play(0, 2)  # O
        result = game.play(2, 2)  # X gagne diagonale
        assert result["status"] == "win"

    def test_win_anti_diagonal(self):
        game = MorpionGame()
        game.play(0, 2)  # X
        game.play(0, 0)  # O
        game.play(1, 1)  # X
        game.play(1, 0)  # O
        result = game.play(2, 0)  # X gagne anti-diagonale
        assert result["status"] == "win"

    def test_draw(self):
        game = MorpionGame()
        # X O X
        # X X O
        # O X O
        moves = [(0,0), (0,1), (0,2), (1,2), (1,0), (2,0), (1,1), (2,2), (2,1)]
        for r, c in moves[:-1]:
            result = game.play(r, c)
            assert result["status"] == "continue"
        result = game.play(*moves[-1])
        assert result["status"] == "draw"
        assert game.winner is None


class TestMorpionState:
    """Tests de l'etat du jeu."""

    def test_get_state(self):
        game = MorpionGame()
        state = game.get_state()
        assert state["game"] == "morpion"
        assert state["current_player"] == X
        assert len(state["valid_moves"]) == 9

    def test_valid_moves_decrease(self):
        game = MorpionGame()
        game.play(0, 0)
        game.play(1, 1)
        assert len(game.get_valid_moves()) == 7

    def test_render_contains_grid(self):
        game = MorpionGame()
        text = game.render()
        assert "---" in text
        assert "Tour de X" in text

    def test_render_after_win(self):
        game = MorpionGame()
        game.board = [[X, X, X], [O, O, EMPTY], [EMPTY]*3]
        game.winner = X
        game.game_over = True
        text = game.render()
        assert "X gagne" in text


class TestMorpionAI:
    """Tests de l'IA minimax."""

    def test_ai_returns_valid_move(self):
        game = MorpionGame()
        move = ai_move(game)
        assert move is not None
        r, c = move
        assert 0 <= r < 3 and 0 <= c < 3

    def test_ai_blocks_opponent_win(self):
        game = MorpionGame()
        # X menace en ligne 0, O ne peut pas gagner immediatement
        game.board = [[X, X, EMPTY], [O, EMPTY, EMPTY], [EMPTY, EMPTY, O]]
        game.current_player = O
        game.moves_count = 4
        move = ai_move(game, difficulty="hard")
        # O doit bloquer en (0,2)
        assert move == (0, 2)

    def test_ai_takes_winning_move(self):
        game = MorpionGame()
        game.board = [[X, X, EMPTY], [O, O, EMPTY], [EMPTY]*3]
        game.current_player = X
        move = ai_move(game, difficulty="hard")
        assert move == (0, 2)

    def test_ai_easy_returns_valid(self):
        game = MorpionGame()
        move = ai_move(game, difficulty="easy")
        assert move is not None

    def test_ai_hard_never_loses_as_x(self):
        """L'IA hard ne doit jamais perdre en commencant."""
        game = MorpionGame()
        # Simuler 20 parties IA(hard) vs IA(easy)
        for _ in range(20):
            game.reset()
            while not game.game_over:
                if game.current_player == X:
                    move = ai_move(game, "hard")
                else:
                    move = ai_move(game, "easy")
                if move:
                    game.play(move[0], move[1])
            assert game.winner != O  # hard(X) ne perd jamais

    def test_ai_no_move_when_full(self):
        game = MorpionGame()
        game.board = [[X, O, X], [X, O, X], [O, X, O]]
        game.game_over = True
        move = ai_move(game)
        assert move is None
