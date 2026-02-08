import sys
import os
import pytest

# Ajouter le dossier racine du projet au path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@pytest.fixture(autouse=True)
def reset_event_bus():
    """Reset le singleton du bus avant chaque test."""
    from core.event_bus.bus import bus
    bus.reset()
    yield
    bus.reset()
