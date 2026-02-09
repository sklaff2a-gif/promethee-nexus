import sys
import os
import pytest

# Ajouter le dossier racine du projet au path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@pytest.fixture(autouse=True)
def reset_event_bus():
    """Reset le singleton du bus et les instances mémoire avant chaque test."""
    from core.event_bus.bus import bus
    from core.vector_store import ChromaMemoryManager
    bus.reset()
    ChromaMemoryManager.reset_all()
    yield
    bus.reset()
    ChromaMemoryManager.reset_all()
