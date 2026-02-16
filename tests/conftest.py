import sys
import os
import pytest

# Ajouter le dossier racine du projet au path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@pytest.fixture(autouse=True)
def reset_event_bus(monkeypatch, tmp_path):
    """Reset le singleton du bus, les instances mémoire et psyche avant chaque test.

    monkeypatch.chdir(tmp_path) isole tous les tests du vrai disque :
    - ChromaDB écrit dans un dossier temporaire (pas de corruption croisée)
    - Les fichiers d'état (psyche, journal, etc.) sont éphémères
    """
    from core.event_bus.bus import bus
    from core.vector_store import ChromaMemoryManager
    from core.psyche import PsycheEngine, STATE_FILE
    from core.strategic_journal import StrategicJournal, JOURNAL_FILE
    from core.self_awareness import SelfAwarenessEngine, STATE_FILE as AWARENESS_STATE_FILE
    from core.objectives_engine import ObjectivesEngine, STATE_FILE as OBJECTIVES_STATE_FILE

    # Isolation disque : tous les chemins relatifs pointent vers tmp_path
    monkeypatch.chdir(tmp_path)

    bus.reset()
    ChromaMemoryManager.reset_all()
    PsycheEngine.reset_singleton()
    StrategicJournal.reset_singleton()
    SelfAwarenessEngine.reset_singleton()
    ObjectivesEngine.reset_singleton()
    # Nettoyer les fichiers d'état pour éviter la pollution entre tests
    for f in [STATE_FILE, JOURNAL_FILE, AWARENESS_STATE_FILE, OBJECTIVES_STATE_FILE]:
        if os.path.exists(f):
            os.remove(f)
    yield
    bus.reset()
    ChromaMemoryManager.reset_all()
    PsycheEngine.reset_singleton()
    StrategicJournal.reset_singleton()
    SelfAwarenessEngine.reset_singleton()
    ObjectivesEngine.reset_singleton()
    for f in [STATE_FILE, JOURNAL_FILE, AWARENESS_STATE_FILE, OBJECTIVES_STATE_FILE]:
        if os.path.exists(f):
            os.remove(f)
