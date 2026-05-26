import sys
import os

# Barriere sanitaire — redirige les chemins d'etat de production vers
# des fichiers temporaires. DOIT etre pose AVANT tout import de `core.*`
# car certains modules (synaptic_network) resolvent STATE_FILE a l'import.
os.environ["PROMETHEE_TEST_MODE"] = "1"

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
    from core.spreading_activation import SpreadingActivationEngine
    from core.silent_failures import SilentFailuresJournal

    # Isolation disque : tous les chemins relatifs pointent vers tmp_path
    monkeypatch.chdir(tmp_path)

    bus.reset()
    ChromaMemoryManager.reset_all()
    PsycheEngine.reset_singleton()
    StrategicJournal.reset_singleton()
    SelfAwarenessEngine.reset_singleton()
    ObjectivesEngine.reset_singleton()
    SpreadingActivationEngine.reset_singleton()
    # Isole le Journal des Echecs Silencieux : tests prefrontal/autres
    # appelant compute_inhibition declenchent le hook -> ne doit pas polluer
    # le fichier de prod memory/silent_failures.json
    SilentFailuresJournal.reset_singleton()
    _sf_journal = SilentFailuresJournal()
    _sf_journal._file = str(tmp_path / "silent_failures_test.json")
    _sf_journal._entries = []
    _sf_journal._active_amnesties = {}

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
    SpreadingActivationEngine.reset_singleton()
    SilentFailuresJournal.reset_singleton()
    for f in [STATE_FILE, JOURNAL_FILE, AWARENESS_STATE_FILE, OBJECTIVES_STATE_FILE]:
        if os.path.exists(f):
            os.remove(f)
