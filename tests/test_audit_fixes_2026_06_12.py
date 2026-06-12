# tests/test_audit_fixes_2026_06_12.py — Régressions des fixes de l'audit global.
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from core.games.game_hub import GameHub
from core.games.chess_game import CHESS_AVAILABLE


@pytest.fixture(autouse=True)
def reset_hub(tmp_path, monkeypatch):
    import core.games.game_hub as hub_mod
    monkeypatch.setattr(hub_mod, "STATE_FILE", str(tmp_path / "game_state.json"))
    GameHub.reset_singleton()
    yield
    GameHub.reset_singleton()


# ─── Lot 1 : sous-systeme jeux ───────────────────────────────────────────────

class TestForfeitSemantique:
    """#4 — l'humain qui clique ABANDONNER ne doit PLUS punir Promethee."""

    def test_forfait_humain_compte_victoire_promethee(self):
        hub = GameHub()
        hub.new_game("morpion", opponent="human", promethee_starts=True)
        out = hub.forfeit(forfeiter="human")
        assert out["forfeiter"] == "human"
        assert hub.stats["morpion_wins"] == 1
        assert hub.stats["morpion_losses"] == 0

    def test_forfait_promethee_compte_defaite(self):
        hub = GameHub()
        hub.new_game("morpion", opponent="human", promethee_starts=True)
        hub.forfeit(forfeiter="promethee")
        assert hub.stats["morpion_losses"] == 1
        assert hub.stats["morpion_wins"] == 0

    def test_defaut_python_est_promethee(self):
        # Defaut Python = comportement historique (nettoyage interne / appels
        # existants inchanges). C'est l'UI qui passe explicitement "human".
        hub = GameHub()
        hub.new_game("morpion", opponent="human")
        out = hub.forfeit()
        assert out["forfeiter"] == "promethee"
        assert hub.stats["morpion_losses"] == 1


@pytest.mark.skipif(not CHESS_AVAILABLE, reason="python-chess absent")
class TestArchivageJeuMemoire:
    """#1 — mgr.add() n'existait pas (AttributeError avalee). Doit appeler
    add_documents avec la bonne signature."""

    def test_souvenir_jeu_utilise_add_documents(self):
        hub = GameHub()
        hub.stats["chess_unlocked"] = True
        hub.new_game("echecs", opponent="human", promethee_starts=False)
        fake_mgr = MagicMock()
        with patch("core.vector_store.ChromaMemoryManager.get_instance", return_value=fake_mgr):
            hub.forfeit(forfeiter="human")  # déclenche _record_game_end -> archive
        assert fake_mgr.add_documents.called
        kwargs = fake_mgr.add_documents.call_args.kwargs
        assert isinstance(kwargs["documents"], list) and len(kwargs["documents"]) == 1
        assert isinstance(kwargs["ids"], list)
        assert not fake_mgr.add.called  # l'ancienne API morte


@pytest.mark.skipif(not CHESS_AVAILABLE, reason="python-chess absent")
class TestChessRaceRevalidation:
    """#2 — chess_ai_move ne doit pas rejouer si la session a change pendant l'await."""

    @pytest.mark.asyncio
    async def test_session_changee_pendant_await_refuse(self):
        hub = GameHub()
        hub.stats["chess_unlocked"] = True
        hub.new_game("echecs", opponent="human", promethee_starts=True)

        async def _ai_qui_simule_un_forfeit(game):
            # Pendant la "reflexion", l'utilisateur abandonne -> session nullifiee
            hub._active_session = None
            return {"move": "e2e4", "san": "e4", "result": {"valid": True}, "comment": "", "assisted": False}

        with patch("core.games.chess_game.promethee_chess_move", side_effect=_ai_qui_simule_un_forfeit):
            out = await hub.chess_ai_move()
        assert "error" in out
        assert "change" in out["error"].lower()


# ─── Lot 2 : pertes silencieuses ─────────────────────────────────────────────

class TestNeurochimieRebranchee:
    """3 handlers etaient morts (events jamais publies). Doivent reagir aux events reels."""

    @pytest.fixture
    def neuro(self):
        from core.neurochemistry import Neurochemistry
        Neurochemistry.reset_singleton()
        n = Neurochemistry()
        yield n
        Neurochemistry.reset_singleton()

    @pytest.mark.asyncio
    async def test_council_end_consensus_booste_serotonine(self, neuro):
        base = neuro.serotonin
        await neuro._on_council_consensus({"status": "consensus"})
        assert neuro.serotonin > base

    @pytest.mark.asyncio
    async def test_council_end_timeout_ne_booste_pas(self, neuro):
        base = neuro.serotonin
        await neuro._on_council_consensus({"status": "timeout"})
        assert neuro.serotonin == base

    @pytest.mark.asyncio
    async def test_school_grade_received_haute_note_booste_ach(self, neuro):
        base = neuro.acetylcholine
        await neuro._on_school_grade_high({"grade": 9.0})
        assert neuro.acetylcholine > base

    @pytest.mark.asyncio
    async def test_school_grade_basse_note_neutre(self, neuro):
        base = neuro.acetylcholine
        await neuro._on_school_grade_high({"grade": 5.0})
        assert neuro.acetylcholine == base

    def test_subscribe_aux_events_reels(self, neuro):
        """Les abonnements doivent viser COUNCIL_END / SCHOOL_GRADE_RECEIVED."""
        from core.event_bus.bus import bus
        bus.reset()
        from core.neurochemistry import Neurochemistry
        Neurochemistry.reset_singleton()
        Neurochemistry()  # re-souscrit
        assert "COUNCIL_END" in bus.subscribers
        assert "SCHOOL_GRADE_RECEIVED" in bus.subscribers
        assert "COUNCIL_CONSENSUS" not in bus.subscribers  # l'event mort


class TestJournalAntiDestruction:
    """A — un journal illisible ne doit PLUS etre ecrase ; il est sauve en .corrupt."""

    def test_fichier_corrompu_sauvegarde_pas_ecrase(self, tmp_path, monkeypatch):
        import core.claude_journal as cj
        journal = tmp_path / "claude_journal.json"
        journal.write_text("{ corrompu pas du json valide", encoding="utf-8")
        monkeypatch.setattr(cj, "JOURNAL_FILE", str(journal))

        entries = cj._load()
        assert entries == []  # repart a vide pour les lecteurs
        assert (tmp_path / "claude_journal.json.corrupt").exists()  # mais le corrompu est SAUVE
        assert not journal.exists()  # deplace, pas ecrase

    def test_journal_valide_charge_normalement(self, tmp_path, monkeypatch):
        import core.claude_journal as cj
        journal = tmp_path / "claude_journal.json"
        journal.write_text(json.dumps([{"content": "vrai"}]), encoding="utf-8")
        monkeypatch.setattr(cj, "JOURNAL_FILE", str(journal))
        assert cj._load() == [{"content": "vrai"}]


class TestOutreachPersistance:
    """C — _delivered et _hourly_timestamps doivent survivre au restart."""

    def test_delivered_et_hourly_persistes(self, tmp_path, monkeypatch):
        import core.outreach as om
        from pathlib import Path
        monkeypatch.setattr(om, "OUTREACH_STATE_FILE", Path(tmp_path / "outreach.json"))
        om.OutreachEngine.reset_singleton()
        eng = om.OutreachEngine()
        eng.init()
        eng._delivered = [{"category": "critical", "text": "msg en attente d'ACK"}]
        eng._hourly_timestamps = [123.0, 456.0]
        eng._save()

        om.OutreachEngine.reset_singleton()
        eng2 = om.OutreachEngine()
        eng2.init()
        assert eng2._delivered == [{"category": "critical", "text": "msg en attente d'ACK"}]
        assert eng2._hourly_timestamps == [123.0, 456.0]
