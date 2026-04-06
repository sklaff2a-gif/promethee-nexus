"""Tests du game hub — systeme unifie de jeux."""

import pytest
import os
from core.games.game_hub import GameHub, CHESS_REQUIREMENTS


@pytest.fixture(autouse=True)
def reset_hub(tmp_path):
    """Reset le singleton et redirige la persistence vers tmp."""
    GameHub.reset_singleton()
    import core.games.game_hub as ghm
    ghm.STATE_FILE = str(tmp_path / "game_state.json")
    hub = GameHub()
    yield hub
    GameHub.reset_singleton()


class TestGameHubBase:
    """Tests de base du hub."""

    def test_singleton(self):
        a = GameHub()
        b = GameHub()
        assert a is b

    def test_initial_stats(self, reset_hub):
        hub = reset_hub
        assert hub.stats["morpion_wins"] == 0
        assert hub.stats["puissance4_wins"] == 0
        assert hub.stats["chess_unlocked"] is False

    def test_list_available_games(self, reset_hub):
        hub = reset_hub
        games = hub._list_available_games()
        assert len(games) == 4
        names = [g["id"] for g in games]
        assert "playground" in names
        assert "morpion" in names
        assert "puissance4" in names
        assert "echecs" in names
        # Echecs verrouilles
        echecs = [g for g in games if g["id"] == "echecs"][0]
        assert echecs["status"] == "verrouille"

    def test_get_status(self, reset_hub):
        hub = reset_hub
        status = hub.get_status()
        assert "active_game" in status
        assert "stats" in status
        assert "competences" in status
        assert "chess_unlocked" in status


class TestGameHubMorpion:
    """Tests du hub avec le morpion."""

    def test_new_morpion(self, reset_hub):
        hub = reset_hub
        result = hub.new_game("morpion", opponent="human")
        assert "error" not in result
        assert result["game"] == "morpion"
        assert result["promethee_symbol"] == "X"

    def test_new_morpion_alfred(self, reset_hub):
        hub = reset_hub
        result = hub.new_game("morpion", opponent="alfred")
        assert "error" not in result
        assert result["opponent"] == "alfred"

    def test_cannot_start_two_games(self, reset_hub):
        hub = reset_hub
        hub.new_game("morpion")
        result = hub.new_game("puissance4")
        assert "error" in result

    def test_play_morpion_move(self, reset_hub):
        hub = reset_hub
        hub.new_game("morpion", opponent="human")
        result = hub.play_move([0, 0], player="promethee")
        assert result["move_result"]["valid"]

    def test_wrong_turn(self, reset_hub):
        hub = reset_hub
        hub.new_game("morpion", opponent="human")
        # C'est le tour de X (promethee)
        result = hub.play_move([0, 0], player="human")
        assert "error" in result

    def test_morpion_vs_alfred_auto_reply(self, reset_hub):
        hub = reset_hub
        hub.new_game("morpion", opponent="alfred")
        # Promethee joue, Alfred repond automatiquement
        result = hub.play_move([1, 1], player="promethee")
        assert "alfred_move" in result

    def test_morpion_complete_game(self, reset_hub):
        hub = reset_hub
        hub.new_game("morpion", opponent="human")
        # X gagne en ligne 0
        hub.play_move([0, 0], player="promethee")
        hub.play_move([1, 0], player="human")
        hub.play_move([0, 1], player="promethee")
        hub.play_move([1, 1], player="human")
        result = hub.play_move([0, 2], player="promethee")
        assert result.get("game_over")
        assert hub.stats["morpion_wins"] == 1

    def test_forfeit(self, reset_hub):
        hub = reset_hub
        hub.new_game("morpion", opponent="human")
        result = hub.forfeit()
        assert result["status"] == "forfait"
        assert hub.stats["morpion_losses"] == 1


class TestGameHubPuissance4:
    """Tests du hub avec puissance 4."""

    def test_new_puissance4(self, reset_hub):
        hub = reset_hub
        result = hub.new_game("puissance4", opponent="human")
        assert result["game"] == "puissance4"

    def test_play_puissance4_move(self, reset_hub):
        hub = reset_hub
        hub.new_game("puissance4", opponent="human")
        result = hub.play_move(3, player="promethee")
        assert result["move_result"]["valid"]

    def test_puissance4_vs_alfred(self, reset_hub):
        hub = reset_hub
        hub.new_game("puissance4", opponent="alfred")
        result = hub.play_move(3, player="promethee")
        assert "alfred_move" in result


class TestGameHubProgression:
    """Tests de la progression et unlock echecs."""

    def test_chess_locked_by_default(self, reset_hub):
        hub = reset_hub
        assert not hub.stats["chess_unlocked"]

    def test_playground_competence(self, reset_hub):
        hub = reset_hub
        hub.record_playground_clear(1)
        assert hub.stats["playground_level1_cleared"]
        assert not hub.stats["playground_level3_cleared"]
        hub.record_playground_clear(3)
        assert hub.stats["playground_level3_cleared"]

    def test_chess_unlock_all_competences(self, reset_hub):
        hub = reset_hub
        # Valider playground
        hub.record_playground_clear(3)
        # Valider morpion (3 victoires, pas 5 defaites)
        hub.stats["morpion_wins"] = 3
        hub.stats["morpion_consecutive_losses"] = 0
        # Valider puissance4 (3 victoires + quick win)
        hub.stats["puissance4_wins"] = 3
        hub.stats["puissance4_quick_win"] = True
        hub._check_chess_unlock()
        assert hub.stats["chess_unlocked"]

    def test_chess_not_unlock_partial(self, reset_hub):
        hub = reset_hub
        hub.record_playground_clear(3)
        hub.stats["morpion_wins"] = 3
        # Puissance4 pas validee
        hub._check_chess_unlock()
        assert not hub.stats["chess_unlocked"]

    def test_chess_shows_in_available_when_unlocked(self, reset_hub):
        hub = reset_hub
        hub.stats["chess_unlocked"] = True
        games = hub._list_available_games()
        echecs = [g for g in games if g["id"] == "echecs"][0]
        assert echecs["status"] == "disponible"


class TestGameHubPersistence:
    """Tests de la persistence."""

    def test_save_load(self, reset_hub, tmp_path):
        hub = reset_hub
        hub.stats["morpion_wins"] = 5
        hub._save()

        # Reset et reload
        GameHub.reset_singleton()
        import core.games.game_hub as ghm
        ghm.STATE_FILE = str(tmp_path / "game_state.json")
        hub2 = GameHub()
        assert hub2.stats["morpion_wins"] == 5

    def test_history_persisted(self, reset_hub, tmp_path):
        hub = reset_hub
        hub.game_history = [{"game": "morpion", "winner": "X"}]
        hub._save()

        GameHub.reset_singleton()
        import core.games.game_hub as ghm
        ghm.STATE_FILE = str(tmp_path / "game_state.json")
        hub2 = GameHub()
        assert len(hub2.game_history) == 1
