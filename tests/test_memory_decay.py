import math
import time
import uuid
import pytest
from unittest.mock import MagicMock
from core.vector_store import ChromaMemoryManager


@pytest.fixture(autouse=True)
def isolate_chroma(monkeypatch, tmp_path):
    """Force tous les tests à utiliser un répertoire temporaire."""
    monkeypatch.chdir(tmp_path)
    ChromaMemoryManager.reset_all()
    yield
    ChromaMemoryManager.reset_all()


# ─── Helpers ───

def _make_manager():
    """Crée un manager avec un project_id unique pour éviter les collisions."""
    pid = f"decay_{uuid.uuid4().hex[:8]}"
    return ChromaMemoryManager.get_instance(pid)


def _add_doc(mgr, text, timestamp, doc_id=None, collection="collective_wisdom"):
    """Ajoute un document avec un timestamp donné."""
    doc_id = doc_id or f"id_{uuid.uuid4().hex[:8]}"
    mgr.add_documents(
        [text],
        [{"agent": "test", "timestamp": str(timestamp)}],
        [doc_id],
        collection,
    )


def _make_agent(monkeypatch, project_id=None):
    """Crée un BaseAgent minimal connecté à un ChromaMemoryManager isolé."""
    pid = project_id or f"decay_{uuid.uuid4().hex[:8]}"

    mock_config = MagicMock()
    mock_config.GOOGLE_API_KEY = None
    mock_config.OLLAMA_URL = "http://localhost:11434/api/generate"
    mock_config.AGENT_MODEL_ROUTING = {}
    mock_config.PROJECT_ID = pid
    mock_config.AGENT_SPECIFIC_LOCAL_MODELS = {}

    monkeypatch.setattr("core.base_agent.Config", mock_config)

    from core.base_agent import BaseAgent
    agent = BaseAgent("test_agent", "testeur", "agent de test")
    return agent


# ─── TestDecayScoring ───

class TestDecayScoring:

    def test_recent_scores_higher_than_old(self):
        """Un souvenir récent doit avoir un score > un souvenir ancien à distance égale."""
        mgr = _make_manager()
        now = time.time()

        _add_doc(mgr, "souvenir récent", now - 3600, "recent")       # 1h
        _add_doc(mgr, "souvenir ancien", now - 60 * 86400, "old")    # 60 jours

        res = mgr.query_with_metadata(
            ["souvenir"], n_results=2, collection_name="collective_wisdom"
        )
        assert res is not None
        assert len(res["documents"][0]) == 2
        assert "metadatas" in res
        assert "distances" in res

    def test_no_timestamp_gets_intermediate_score(self):
        """Un souvenir sans timestamp valide reçoit un decay intermédiaire."""
        half_life = 30
        age_days = half_life  # fallback = demi-vie
        decay = math.exp(-age_days * 0.693 / half_life)
        # exp(-0.693) ≈ 0.5
        assert 0.49 < decay < 0.51

    def test_double_halflife_gives_quarter_score(self):
        """Après 2x la demi-vie (60j), le decay vaut ~25%."""
        half_life = 30
        age_days = 60
        decay = math.exp(-age_days * 0.693 / half_life)
        # exp(-2*0.693) ≈ 0.25
        assert 0.24 < decay < 0.26


# ─── TestDecayRecall ───

class TestDecayRecall:

    def test_recall_favors_recent(self, monkeypatch):
        """recall() re-rank par decay : un doc récent passe devant un doc ancien à distance égale."""
        agent = _make_agent(monkeypatch)
        now = time.time()

        # Mock query_with_metadata pour contrôler distances et timestamps
        mock_result = {
            "documents": [["doc ancien", "doc récent"]],
            "metadatas": [[
                {"agent": "test", "timestamp": str(now - 60 * 86400)},  # 60j
                {"agent": "test", "timestamp": str(now - 3600)},         # 1h
            ]],
            "distances": [[0.3, 0.3]],  # même distance vectorielle
        }
        agent.memory_manager.query_with_metadata = MagicMock(return_value=mock_result)

        result = agent.recall("query test", limit=2)
        lines = result.strip().split("\n")
        assert len(lines) == 2
        # Le récent doit être en premier grâce au decay
        assert lines[0] == "doc récent"
        assert lines[1] == "doc ancien"

    def test_recall_limit_respected(self, monkeypatch):
        """recall(limit=1) retourne bien 1 seul résultat après re-ranking."""
        agent = _make_agent(monkeypatch)
        mgr = agent.memory_manager
        now = time.time()

        _add_doc(mgr, "doc alpha", now - 3600, "a")
        _add_doc(mgr, "doc beta", now - 7200, "b")
        _add_doc(mgr, "doc gamma", now - 10800, "c")

        result = agent.recall("doc", limit=1)
        lines = [l for l in result.strip().split("\n") if l]
        assert len(lines) == 1

    def test_recall_no_timestamp_penalized(self, monkeypatch):
        """Un doc sans timestamp valide reçoit un score intermédiaire (~50% du frais)."""
        agent = _make_agent(monkeypatch)
        now = time.time()

        mock_result = {
            "documents": [["doc sans ts", "doc frais"]],
            "metadatas": [[
                {"agent": "test"},  # pas de timestamp
                {"agent": "test", "timestamp": str(now - 60)},  # 1 minute
            ]],
            "distances": [[0.3, 0.3]],
        }
        agent.memory_manager.query_with_metadata = MagicMock(return_value=mock_result)

        result = agent.recall("query", limit=2)
        lines = result.strip().split("\n")
        # Le frais doit être devant car le sans-timestamp a decay ≈ 0.5
        assert lines[0] == "doc frais"


# ─── TestPurgeExpired ───

class TestPurgeExpired:

    def test_purge_removes_old_docs(self):
        """purge_expired(max_age_days=1) supprime les docs vieux de 2 jours."""
        mgr = _make_manager()
        now = time.time()

        _add_doc(mgr, "doc frais", now - 3600, "fresh")           # 1h
        _add_doc(mgr, "doc périmé", now - 2 * 86400, "expired")   # 2 jours

        purged = mgr.purge_expired(max_age_days=1)
        assert purged == 1

        # Vérifier qu'il reste bien le doc frais
        res = mgr.query_documents(["doc"], n_results=5, collection_name="collective_wisdom")
        docs = [d for sublist in res["documents"] for d in sublist]
        assert "doc frais" in docs
        assert "doc périmé" not in docs

    def test_purge_empty_collection(self):
        """purge_expired() sur collection vide retourne 0."""
        mgr = _make_manager()
        purged = mgr.purge_expired(max_age_days=1, collection_name="collective_wisdom")
        assert purged == 0

    def test_purge_targeted_collection(self):
        """Purge ciblée sur une seule collection."""
        mgr = _make_manager()
        now = time.time()
        old_ts = now - 100 * 86400

        _add_doc(mgr, "vieux code", old_ts, "code_old", "code_snippets")
        _add_doc(mgr, "vieux wisdom", old_ts, "wisdom_old", "collective_wisdom")

        # Purge uniquement code_snippets
        purged = mgr.purge_expired(max_age_days=1, collection_name="code_snippets")
        assert purged == 1

        # collective_wisdom non touchée
        res = mgr.query_documents(["vieux"], n_results=5, collection_name="collective_wisdom")
        docs = [d for sublist in res["documents"] for d in sublist]
        assert "vieux wisdom" in docs


# ─── TestCountDocuments ───

class TestCountDocuments:

    def test_count_after_add_and_purge(self):
        """count_documents() retourne le bon nombre après add/purge."""
        mgr = _make_manager()
        now = time.time()

        assert mgr.count_documents("collective_wisdom") == 0

        _add_doc(mgr, "doc un", now - 3600, "d1")
        _add_doc(mgr, "doc deux", now - 2 * 86400, "d2")
        assert mgr.count_documents("collective_wisdom") == 2

        mgr.purge_expired(max_age_days=1, collection_name="collective_wisdom")
        assert mgr.count_documents("collective_wisdom") == 1


# ─── TestBackwardCompat ───

class TestBackwardCompat:

    def test_query_documents_still_works(self):
        """query_documents() existant fonctionne toujours (pas de régression)."""
        mgr = _make_manager()
        now = time.time()

        _add_doc(mgr, "backward compat test", now, "bc1")

        res = mgr.query_documents(["backward"], n_results=1, collection_name="collective_wisdom")
        assert res is not None
        assert res["documents"][0][0] == "backward compat test"
