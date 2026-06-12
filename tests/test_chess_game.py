# tests/test_chess_game.py — Echecs dans la salle de jeux (12/06/2026)
#
# Design teste : python-chess arbitre tout ; Promethee (LLM) choisit dans la
# liste des coups legaux fournie ; invalide -> retry -> fallback heuristique
# marque "assiste" (lecon de la revanche du 11/06 : il voit quoi prendre,
# pas comment).
import pytest
from unittest.mock import patch, AsyncMock

from core.games.chess_game import (
    ChessGame, heuristic_move, promethee_chess_move, _parse_llm_move,
    CHESS_AVAILABLE,
)
from core.games.game_hub import GameHub

pytestmark = pytest.mark.skipif(not CHESS_AVAILABLE, reason="python-chess absent")


@pytest.fixture(autouse=True)
def reset_hub(tmp_path, monkeypatch):
    import core.games.game_hub as hub_mod
    monkeypatch.setattr(hub_mod, "STATE_FILE", str(tmp_path / "game_state.json"))
    GameHub.reset_singleton()
    yield
    GameHub.reset_singleton()


class TestChessGameMoteur:

    def test_ouverture_legale(self):
        g = ChessGame()
        r = g.play("e2e4")
        assert r["valid"] and r["san"] == "e4"
        assert g.current_player == "noirs"
        assert g.moves_count == 1

    def test_coup_illegal_rejete(self):
        g = ChessGame()
        r = g.play("d1e4")  # la dame ne voit pas e4 — l'erreur exacte de la revanche
        assert not r["valid"]
        assert "illegal" in r["reason"].lower()
        assert g.moves_count == 0

    def test_format_invalide_rejete(self):
        g = ChessGame()
        for bad in ("Qxe4", "e9e4", "", "xyz"):
            assert not g.play(bad)["valid"]

    def test_auto_promotion_dame(self):
        g = ChessGame()
        g.board.set_fen("8/P7/8/8/8/4k3/8/4K3 w - - 0 1")
        r = g.play("a7a8")  # sans suffixe de promotion
        assert r["valid"] and r["uci"] == "a7a8q"

    def test_mat_du_berger(self):
        g = ChessGame()
        for mv in ("e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6", "h5f7"):
            r = g.play(mv)
            assert r["valid"], mv
        assert g.game_over and g.winner == "blancs"

    def test_pat_est_nul(self):
        g = ChessGame()
        g.board.set_fen("7k/4Q3/6K1/8/8/8/8/8 w - - 0 1")
        r = g.play("e7f7")  # Qf7 : roi noir h8 sans coup legal, PAS en echec = pat
        assert r["valid"]
        assert g.game_over and g.winner is None

    def test_state_contient_coups_legaux(self):
        g = ChessGame()
        s = g.get_state()
        assert s["game"] == "echecs"
        assert len(s["legal_moves"]) == 20  # position initiale
        assert "e2e4" in s["legal_moves"]
        assert len(s["board"]) == 8 and len(s["board"][0]) == 8
        assert s["board"][7][4] == "K"  # roi blanc en e1 (rangee 1 = index 7)


class TestHeuristique:

    def test_mat_en_1_prioritaire(self):
        g = ChessGame()
        g.board.set_fen("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")
        assert heuristic_move(g) == "a1a8"  # Ra8#

    def test_capture_nette_choisie(self):
        g = ChessGame()
        # Dame noire en e5 non defendue, prenable par le pion d4
        g.board.set_fen("4k3/8/8/4q3/3P4/8/8/4K3 w - - 0 1")
        assert heuristic_move(g) == "d4e5"

    def test_toujours_legal(self):
        g = ChessGame()
        for _ in range(6):
            mv = heuristic_move(g)
            assert mv in g.legal_moves_uci()
            g.play(mv)


class TestParseLLM:

    def test_format_canonique(self):
        uci, intent = _parse_llm_move("COUP: e2e4\nINTENTION: le centre", ["e2e4", "d2d4"])
        assert uci == "e2e4" and intent == "le centre"

    def test_uci_dans_texte_libre(self):
        uci, _ = _parse_llm_move("Je vais jouer g1f3 pour developper.", ["g1f3", "e2e4"])
        assert uci == "g1f3"

    def test_coup_hors_liste_refuse(self):
        uci, _ = _parse_llm_move("COUP: d1h5", ["e2e4", "d2d4"])
        assert uci is None

    def test_auto_promo_dans_parse(self):
        uci, _ = _parse_llm_move("COUP: a7a8", ["a7a8q", "a7a8n"])
        assert uci == "a7a8q"


class TestPrometheeMove:

    @pytest.mark.asyncio
    async def test_coup_llm_legal_joue_non_assiste(self):
        g = ChessGame()
        with patch("core.games.chess_game._ask_llm", new_callable=AsyncMock,
                   return_value="COUP: e2e4\nINTENTION: occuper le centre"):
            out = await promethee_chess_move(g)
        assert out["move"] == "e2e4" and not out["assisted"]
        assert out["comment"] == "occuper le centre"
        assert g.moves_count == 1

    @pytest.mark.asyncio
    async def test_llm_illegal_2x_fallback_assiste(self):
        g = ChessGame()
        with patch("core.games.chess_game._ask_llm", new_callable=AsyncMock,
                   return_value="COUP: d1e4\nINTENTION: je capture"):
            out = await promethee_chess_move(g)
        assert out["assisted"] is True
        assert out["move"] in [m for m in ChessGame().legal_moves_uci()]
        assert g.assisted_moves == 1

    @pytest.mark.asyncio
    async def test_llm_en_panne_fallback(self):
        g = ChessGame()
        with patch("core.games.chess_game._ask_llm", new_callable=AsyncMock,
                   side_effect=RuntimeError("ollama down")):
            out = await promethee_chess_move(g)
        assert out["assisted"] is True and g.moves_count == 1


class TestHubIntegration:

    def _unlock(self, hub):
        hub.stats["chess_unlocked"] = True

    def test_unlock_regle_assouplie(self):
        hub = GameHub()
        hub.stats["morpion_wins"] = 3
        hub.stats["puissance4_wins"] = 3
        hub.stats["puissance4_quick_win"] = True
        hub._check_chess_unlock()
        assert hub.stats["chess_unlocked"] is True

    def test_new_game_echecs_et_coup_humain(self):
        hub = GameHub()
        self._unlock(hub)
        r = hub.new_game("echecs", opponent="human", promethee_starts=False)
        assert "error" not in r
        assert r["promethee_symbol"] == "noirs"
        # L'humain (blancs) joue
        out = hub.play_move("e2e4", player="human")
        assert out["move_result"]["valid"]
        assert out["state"]["current_player"] == "noirs"
        # Pas de riposte auto sync : c'est la route async chess/ai-move qui jouera
        assert "promethee_move" not in out

    def test_coup_hors_tour_refuse(self):
        hub = GameHub()
        self._unlock(hub)
        hub.new_game("echecs", opponent="human", promethee_starts=True)
        out = hub.play_move("e2e4", player="human")  # c'est aux blancs = Promethee
        assert "error" in out

    def test_echecs_verrouille_sans_unlock(self):
        hub = GameHub()
        hub.stats["chess_unlocked"] = False
        r = hub.new_game("echecs", opponent="human")
        assert "error" in r

    @pytest.mark.asyncio
    async def test_chess_ai_move_complet(self):
        hub = GameHub()
        self._unlock(hub)
        hub.new_game("echecs", opponent="human", promethee_starts=True)
        with patch("core.games.chess_game._ask_llm", new_callable=AsyncMock,
                   return_value="COUP: e2e4\nINTENTION: le centre d'abord"):
            out = await hub.chess_ai_move()
        assert out["promethee_move"]["move"] == "e2e4"
        assert out["state"]["current_player"] == "noirs"
        assert any("centre" in m.get("message", "") for m in out.get("chat", []))

    def test_forfeit_echecs(self):
        hub = GameHub()
        self._unlock(hub)
        hub.new_game("echecs", opponent="human", promethee_starts=False)
        out = hub.forfeit()
        assert out["status"] == "forfait"
        assert hub.stats["echecs_losses"] == 1
        assert hub._active_session is None
