"""Tests du mentor Claude."""

import pytest
from core.mentor import Mentor


@pytest.fixture(autouse=True)
def reset_mentor(tmp_path):
    Mentor.reset_singleton()
    import core.mentor as mm
    mm.MENTOR_STATE_FILE = str(tmp_path / "mentor_state.json")
    m = Mentor()
    m._api_key = ""  # Pas de cle API dans les tests
    yield m
    Mentor.reset_singleton()


class TestMentorBase:

    def test_singleton(self):
        a = Mentor()
        b = Mentor()
        assert a is b

    def test_not_available_offline(self, reset_mentor):
        m = reset_mentor
        m._mode = "offline"
        assert not m.is_available()

    def test_available_with_cli(self, reset_mentor):
        m = reset_mentor
        m._mode = "cli"
        m._today = ""  # force daily reset
        assert m.is_available()

    def test_available_with_api_key(self, reset_mentor):
        m = reset_mentor
        m._mode = "api"
        m._api_key = "test-key"
        m._today = ""
        assert m.is_available()

    def test_budget_limit(self, reset_mentor):
        m = reset_mentor
        m._api_key = "test-key"
        from datetime import date
        m._today = date.today().isoformat()
        m._calls_today = 5
        assert not m.is_available()

    def test_daily_reset(self, reset_mentor):
        m = reset_mentor
        m._api_key = "test-key"
        m._today = "2020-01-01"
        m._calls_today = 5
        assert m.is_available()  # nouveau jour → reset

    def test_get_status(self, reset_mentor):
        m = reset_mentor
        status = m.get_status()
        assert "available" in status
        assert "calls_today" in status
        assert "budget" in status
        assert status["budget"] == 5

    def test_build_context_empty(self, reset_mentor):
        m = reset_mentor
        ctx = m._build_context()
        assert "premiere session" in ctx.lower()

    def test_build_context_with_history(self, reset_mentor):
        m = reset_mentor
        m._history = [{
            "date": "2026-04-07T03:00:00",
            "slot": "RESEARCH",
            "subject": "Test subject",
            "local_grade": 8.0,
            "claude_feedback": "Bon travail mais...",
        }]
        ctx = m._build_context()
        assert "RESEARCH" in ctx
        assert "Test subject" in ctx


class TestMentorPersistence:

    def test_save_load(self, reset_mentor, tmp_path):
        m = reset_mentor
        m._calls_today = 3
        m._history = [{"slot": "TEST"}]
        m._save()

        Mentor.reset_singleton()
        import core.mentor as mm
        mm.MENTOR_STATE_FILE = str(tmp_path / "mentor_state.json")
        m2 = Mentor()
        assert m2._calls_today == 3
        assert len(m2._history) == 1


# ═══════════════════════════════════════════════════════════════════════
# 03/05/2026 — Slot routing pour pending_directions / pending_challenges
# ═══════════════════════════════════════════════════════════════════════

from core.mentor import _extract_target_slot, SLOT_NAMES


class TestExtractTargetSlot:
    """Helper d'extraction du slot cible depuis le texte libre du mentor."""

    def test_workshop_explicite(self):
        assert _extract_target_slot("le prochain workshop doit reprendre RAG") == "WORKSHOP"

    def test_atelier_synonyme(self):
        assert _extract_target_slot("le prochain atelier porte sur X") == "WORKSHOP"

    def test_research_explicite(self):
        assert _extract_target_slot("Le prochain cours doit être un RESEARCH ciblé") == "RESEARCH"

    def test_recherche_synonyme(self):
        assert _extract_target_slot("la prochaine recherche devra explorer Y") == "RESEARCH"

    def test_code_review_explicite(self):
        assert _extract_target_slot("la prochaine code review portera sur Z") == "CODE_REVIEW"

    def test_revue_de_code_synonyme(self):
        assert _extract_target_slot("Pour la prochaine revue de code, lister les bugs") == "CODE_REVIEW"

    def test_bulletin_explicite(self):
        assert _extract_target_slot("le prochain bulletin doit contenir 3 chiffres") == "BULLETIN"

    def test_bilan_synonyme(self):
        assert _extract_target_slot("Le prochain bilan auto-évaluation devra...") == "BULLETIN"

    def test_creation_synonyme(self):
        assert _extract_target_slot("Le prochain cours doit te forcer à créer") == "CREATION"

    def test_aucun_slot_catch_all(self):
        assert _extract_target_slot("Le prochain cours doit explorer la métacognition") == "*"

    def test_texte_vide(self):
        assert _extract_target_slot("") == "*"
        assert _extract_target_slot(None) == "*"


class TestSlotRouting:
    """Patch 03/05 — pending_directions/challenges indexés par slot.

    Stoppe la fuite observée in-vivo nuit 02-03/05 : direction WORKSHOP-RAG
    consommée par CREATION ci_pipeline.py qui suit.
    """

    def test_consume_direction_slot_match(self, reset_mentor):
        m = reset_mentor
        m._pending_directions = {"WORKSHOP": "Refais RAG", "RESEARCH": "Approfondis MemWalker"}
        # Cours WORKSHOP demande sa direction → reçoit WORKSHOP, RESEARCH reste
        d = m.consume_direction(slot="WORKSHOP")
        assert d == "Refais RAG"
        assert m._pending_directions == {"RESEARCH": "Approfondis MemWalker"}

    def test_consume_direction_pas_de_fuite_cross_slot(self, reset_mentor):
        """LE TEST CRITIQUE — reproduction in-vivo du bug observé.

        Direction écrite après WORKSHOP-RAG, cours suivant = CREATION ci_pipeline.
        AVANT le patch : la direction WORKSHOP-RAG était consommée par CREATION.
        APRÈS le patch : CREATION ne reçoit RIEN, la direction WORKSHOP attend
        un vrai WORKSHOP.
        """
        m = reset_mentor
        m._pending_directions = {
            "WORKSHOP": "le prochain workshop doit reprendre exactement le même sujet — RAG, GraphRAG, MemWalker"
        }
        # CREATION ci_pipeline.py démarre, demande sa direction
        d = m.consume_direction(slot="CREATION")
        assert d == "", "CREATION ne doit PAS consommer la direction WORKSHOP"
        assert "WORKSHOP" in m._pending_directions, (
            "La direction WORKSHOP doit rester intacte pour le prochain vrai WORKSHOP"
        )

    def test_consume_direction_fallback_catch_all(self, reset_mentor):
        m = reset_mentor
        m._pending_directions = {"*": "Direction agnostique applicable partout"}
        # N'importe quel slot peut consommer le catch-all
        d = m.consume_direction(slot="CREATION")
        assert d == "Direction agnostique applicable partout"
        assert m._pending_directions == {}

    def test_consume_direction_slot_match_prioritaire_sur_catch_all(self, reset_mentor):
        m = reset_mentor
        m._pending_directions = {
            "*": "Direction générale",
            "WORKSHOP": "Direction WORKSHOP spécifique",
        }
        d = m.consume_direction(slot="WORKSHOP")
        assert d == "Direction WORKSHOP spécifique"
        # Le catch-all reste disponible pour d'autres slots
        assert m._pending_directions == {"*": "Direction générale"}

    def test_consume_challenge_idem(self, reset_mentor):
        m = reset_mentor
        m._pending_challenges = {"BULLETIN": "Cite 3 chiffres réels"}
        # CODE_REVIEW ne consomme pas le défi BULLETIN
        c = m.consume_challenge(slot="CODE_REVIEW")
        assert c == ""
        assert m._pending_challenges == {"BULLETIN": "Cite 3 chiffres réels"}
        # Mais BULLETIN le consomme
        c = m.consume_challenge(slot="BULLETIN")
        assert c == "Cite 3 chiffres réels"
        assert m._pending_challenges == {}

    def test_consume_sans_arg_backward_compat(self, reset_mentor):
        """Backward compat : appel sans arg → consume catch-all uniquement."""
        m = reset_mentor
        m._pending_directions = {
            "*": "agnostique",
            "WORKSHOP": "spécifique",
        }
        d = m.consume_direction()  # sans slot
        assert d == "agnostique"  # catch-all consommé
        assert m._pending_directions == {"WORKSHOP": "spécifique"}  # WORKSHOP intact

    def test_consume_vide_retourne_string_vide(self, reset_mentor):
        m = reset_mentor
        m._pending_directions = {}
        assert m.consume_direction(slot="RESEARCH") == ""
        assert m.consume_challenge(slot="RESEARCH") == ""

    def test_evaluate_indexe_par_slot_extraction(self, reset_mentor):
        """L'extraction du slot cible depuis la direction du mentor doit
        indexer correctement dans _pending_directions."""
        m = reset_mentor
        # Simule manuellement le flow d'evaluate_deliverable APRES que
        # _extract_challenge_direction ait extrait challenge/direction
        from core.mentor import _extract_target_slot
        direction_text = "le prochain workshop doit reprendre exactement le même sujet"
        target_slot = _extract_target_slot(direction_text)
        assert target_slot == "WORKSHOP"
        m._pending_directions[target_slot] = direction_text
        # Vérification : seul WORKSHOP la verra
        assert m.consume_direction(slot="CREATION") == ""
        assert m.consume_direction(slot="WORKSHOP") == direction_text


class TestSlotRoutingPersistence:
    """Persistence du nouveau format dict + rétrocompat ancien format string."""

    def test_save_load_nouveau_format(self, reset_mentor, tmp_path):
        m = reset_mentor
        m._pending_directions = {
            "WORKSHOP": "Refais RAG",
            "*": "agnostique",
        }
        m._pending_challenges = {"BULLETIN": "Cite 3 chiffres"}
        m._save()

        Mentor.reset_singleton()
        import core.mentor as mm
        mm.MENTOR_STATE_FILE = str(tmp_path / "mentor_state.json")
        m2 = Mentor()
        assert m2._pending_directions == {
            "WORKSHOP": "Refais RAG",
            "*": "agnostique",
        }
        assert m2._pending_challenges == {"BULLETIN": "Cite 3 chiffres"}

    def test_load_retrocompat_ancien_format_string(self, reset_mentor, tmp_path):
        """Si le state file existe avec l'ancien format string non-vide,
        on le migre vers catch-all '*' au load."""
        import core.mentor as mm
        import json
        # Simuler l'ancien format
        old_state = {
            "calls_today": 2,
            "today": "2026-05-02",
            "history": [],
            "pending_direction": "vieille direction agnostique",
            "pending_challenge": "vieux defi agnostique",
        }
        state_file = tmp_path / "mentor_state.json"
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(old_state, f)
        mm.MENTOR_STATE_FILE = str(state_file)

        Mentor.reset_singleton()
        m = Mentor()
        assert m._pending_directions == {"*": "vieille direction agnostique"}
        assert m._pending_challenges == {"*": "vieux defi agnostique"}

    def test_load_retrocompat_ancien_format_string_vide(self, reset_mentor, tmp_path):
        """Ancien format avec strings vides → init dicts vides, pas crash."""
        import core.mentor as mm
        import json
        old_state = {
            "calls_today": 0,
            "today": "2026-05-02",
            "history": [],
            "pending_direction": "",
            "pending_challenge": "",
        }
        state_file = tmp_path / "mentor_state.json"
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(old_state, f)
        mm.MENTOR_STATE_FILE = str(state_file)

        Mentor.reset_singleton()
        m = Mentor()
        assert m._pending_directions == {}
        assert m._pending_challenges == {}

    def test_get_status_expose_dicts(self, reset_mentor):
        m = reset_mentor
        m._pending_directions = {"WORKSHOP": "x"}
        m._pending_challenges = {"BULLETIN": "y"}
        status = m.get_status()
        assert status["pending_directions"] == {"WORKSHOP": "x"}
        assert status["pending_challenges"] == {"BULLETIN": "y"}
