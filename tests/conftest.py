import sys
import os
import pytest

# Ajouter le dossier racine du projet au path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@pytest.fixture(autouse=True)
def reset_event_bus():
    """Reset le singleton du bus, les instances mémoire et psyche avant chaque test."""
    from core.event_bus.bus import bus
    from core.vector_store import ChromaMemoryManager
    from core.psyche import PsycheEngine, STATE_FILE
    from core.strategic_journal import StrategicJournal, JOURNAL_FILE
    from core.self_awareness import SelfAwarenessEngine, STATE_FILE as AWARENESS_STATE_FILE
    from core.objectives_engine import ObjectivesEngine, STATE_FILE as OBJECTIVES_STATE_FILE
    bus.reset()
    ChromaMemoryManager.reset_all()
    PsycheEngine.reset_singleton()
    StrategicJournal.reset_singleton()
    SelfAwarenessEngine.reset_singleton()
    ObjectivesEngine.reset_singleton()
    # Nettoyer les fichiers d'état pour éviter la pollution entre tests
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    if os.path.exists(JOURNAL_FILE):
        os.remove(JOURNAL_FILE)
    if os.path.exists(AWARENESS_STATE_FILE):
        os.remove(AWARENESS_STATE_FILE)
    if os.path.exists(OBJECTIVES_STATE_FILE):
        os.remove(OBJECTIVES_STATE_FILE)
    yield
    bus.reset()
    ChromaMemoryManager.reset_all()
    PsycheEngine.reset_singleton()
    StrategicJournal.reset_singleton()
    SelfAwarenessEngine.reset_singleton()
    ObjectivesEngine.reset_singleton()
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    if os.path.exists(JOURNAL_FILE):
        os.remove(JOURNAL_FILE)
    if os.path.exists(AWARENESS_STATE_FILE):
        os.remove(AWARENESS_STATE_FILE)
    if os.path.exists(OBJECTIVES_STATE_FILE):
        os.remove(OBJECTIVES_STATE_FILE)
