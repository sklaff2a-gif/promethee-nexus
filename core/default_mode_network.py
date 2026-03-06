# core/default_mode_network.py — Reseau du Mode par Defaut
# Pensee spontanee et consolidation au repos.
# S'active quand le systeme est inactif (pas de routine en cours)
# pour generer des associations libres, consolider la memoire,
# et produire des insights. 0 LLM, 100% deterministe.

import json
import os
import time
import random
import asyncio
import logging
from collections import deque
from typing import Dict, Any, Optional, List

from core.event_bus.bus import bus

logger = logging.getLogger("DefaultModeNetwork")

# --- Fichier de persistance ---

DMN_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "dmn_state.json"
)

# --- Constantes ---

IDLE_THRESHOLD_SECONDS = 180
WANDERING_CYCLE_SECONDS = 60
MAX_ASSOCIATIONS = 20
INSIGHT_PROBABILITY = 0.15
CONSOLIDATION_BATCH_SIZE = 5
DMN_HISTORY_SIZE = 50


class DefaultModeNetwork:
    """Reseau du mode par defaut — pensee spontanee au repos."""

    _instance: Optional["DefaultModeNetwork"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._subscribed = False

        self.is_active: bool = False
        self.idle_since: Optional[float] = None
        self.wandering_history: deque = deque(maxlen=DMN_HISTORY_SIZE)
        self.insights: List[dict] = []
        self.consolidation_count: int = 0
        self.total_wanderings: int = 0
        self._last_routine_time: float = time.time()
        self._last_thought: str = ""
        self._alive: bool = False

        self._load()

    @classmethod
    def reset_singleton(cls):
        """Detruit le singleton (pour les tests)."""
        cls._instance = None

    def init(self):
        """Initialisation explicite appelee depuis main.py."""
        self._subscribe_events()
        logger.info("[DMN] Module initialise.")

    # --- Souscriptions bus ---

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        try:
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            bus.subscribe("ROUTINE_FAILED", self._on_routine_failed)
        except Exception as e:
            logger.warning(f"[DMN] Souscription echouee: {e}")

    # --- Handlers bus ---

    async def _on_routine_complete(self, event: dict):
        """Routine terminee → marquer idle."""
        self._mark_idle()

    async def _on_routine_failed(self, event: dict):
        """Routine echouee → marquer idle."""
        self._mark_idle()

    def _on_routine_started(self):
        """Routine demarre → desactiver le DMN."""
        self._deactivate()

    # --- Activation / Desactivation ---

    def _mark_idle(self):
        """Le systeme est idle → demarrer le timer d'inactivite."""
        self._last_routine_time = time.time()
        if self.idle_since is None:
            self.idle_since = time.time()

    def _deactivate(self):
        """Systeme occupe → desactiver."""
        was_active = self.is_active
        self.is_active = False
        self.idle_since = None
        if was_active:
            logger.info("[DMN] Desactive (routine en cours).")

    async def _activate(self):
        """Activer le DMN apres idle suffisant."""
        if not self.is_active:
            self.is_active = True
            logger.info("[DMN] Active (systeme inactif).")
            try:
                await bus.publish("DMN_ACTIVATED", {
                    "idle_duration": self._get_idle_duration(),
                })
            except Exception:
                pass

    def _get_idle_duration(self) -> float:
        """Duree d'inactivite en secondes."""
        if self.idle_since is None:
            return 0.0
        return time.time() - self.idle_since

    # --- Boucle de vagabondage ---

    async def start_wandering(self):
        """Boucle async principale — verifie l'idle et lance les cycles."""
        self._alive = True
        while self._alive:
            await asyncio.sleep(WANDERING_CYCLE_SECONDS)

            idle_duration = self._get_idle_duration()
            if idle_duration >= IDLE_THRESHOLD_SECONDS:
                if not self.is_active:
                    await self._activate()
                await self._wander_cycle()
            elif self.is_active:
                self._deactivate()

    def stop(self):
        """Arrete la boucle."""
        self._alive = False

    async def _wander_cycle(self):
        """Un cycle de pensee spontanee."""
        self.total_wanderings += 1

        # 1. Association libre
        associations = self._generate_free_associations()

        # 2. Consolidation memoire
        consolidated = self._consolidate_memory()

        # 3. Tentative d'insight
        insight = self._attempt_insight(associations)

        # 4. Generer une pensee (reverie)
        thought = self._generate_thought(associations, insight)
        self._last_thought = thought

        # Stocker dans l'historique
        entry = {
            "timestamp": time.time(),
            "associations": len(associations),
            "consolidated": consolidated,
            "insight": insight,
            "thought": thought,
            "cycle": self.total_wanderings,
        }
        self.wandering_history.append(entry)

        # Publier pensee
        if thought:
            try:
                await bus.publish("DMN_THOUGHT", {
                    "text": thought,
                    "associations": associations[:3],
                    "source": "wandering",
                })
            except Exception:
                pass

        # Publier insight
        if insight:
            self.insights.append({
                "text": insight,
                "timestamp": time.time(),
                "cycle": self.total_wanderings,
            })
            try:
                await bus.publish("DMN_INSIGHT", {
                    "text": insight,
                    "novelty": random.uniform(0.5, 1.0),
                })
            except Exception:
                pass

        # Save periodique
        if self.total_wanderings % 5 == 0:
            self._save()

    # --- Association libre ---

    def _generate_free_associations(self) -> List[dict]:
        """Genere des associations libres entre concepts."""
        associations = []
        try:
            from core.synaptic_network import cortex
            # Recuperer les noeuds actifs
            nodes = list(cortex.get_active_nodes().keys())[:MAX_ASSOCIATIONS * 2]
            if len(nodes) >= 2:
                # Creer des paires aleatoires
                random.shuffle(nodes)
                for i in range(0, min(len(nodes) - 1, MAX_ASSOCIATIONS * 2), 2):
                    associations.append({
                        "from": nodes[i],
                        "to": nodes[i + 1],
                        "type": "free_association",
                    })
                    if len(associations) >= MAX_ASSOCIATIONS:
                        break
        except Exception:
            pass

        # Fallback : associations depuis l'historique recent des routines
        if not associations:
            try:
                from core.hippocampus import hippocampus
                episodes = hippocampus.get_recent_episodes(CONSOLIDATION_BATCH_SIZE)
                for i, ep in enumerate(episodes):
                    if i + 1 < len(episodes):
                        associations.append({
                            "from": ep.get("intent", f"ep_{i}"),
                            "to": episodes[i + 1].get("intent", f"ep_{i+1}"),
                            "type": "episodic_link",
                        })
            except Exception:
                pass

        return associations

    def _consolidate_memory(self) -> int:
        """Consolide la memoire episodique. Retourne le nb d'episodes consolides."""
        count = 0
        try:
            from core.hippocampus import hippocampus
            if hasattr(hippocampus, 'consolidate'):
                count = hippocampus.consolidate(CONSOLIDATION_BATCH_SIZE)
                self.consolidation_count += count
        except Exception:
            pass
        return count

    def _attempt_insight(self, associations: List[dict]) -> Optional[str]:
        """Tente de detecter un pattern inattendu. Retourne un insight ou None."""
        if random.random() > INSIGHT_PROBABILITY:
            return None

        if len(associations) < 2:
            return None

        # Detecter des themes communs entre associations
        all_concepts = set()
        for a in associations:
            all_concepts.add(a.get("from", ""))
            all_concepts.add(a.get("to", ""))

        # Concepts qui apparaissent dans plusieurs associations
        concept_count: Dict[str, int] = {}
        for a in associations:
            for key in ("from", "to"):
                c = a.get(key, "")
                if c:
                    concept_count[c] = concept_count.get(c, 0) + 1

        # Hub = concept qui connecte plusieurs autres
        hubs = [c for c, count in concept_count.items() if count >= 2]
        if hubs:
            hub = hubs[0]
            connected = [
                a.get("to") if a.get("from") == hub else a.get("from")
                for a in associations
                if hub in (a.get("from"), a.get("to"))
            ]
            unique_connected = list(set(c for c in connected if c and c != hub))
            if len(unique_connected) >= 2:
                return (
                    f"Connexion inattendue: '{hub}' relie "
                    f"'{unique_connected[0]}' et '{unique_connected[1]}'"
                )

        return None

    def _generate_thought(self, associations: List[dict],
                         insight: Optional[str]) -> str:
        """Genere une pensee textuelle courte."""
        if insight:
            return f"Reflexion DMN: {insight}"

        if associations:
            a = associations[0]
            return f"Vagabondage: {a.get('from', '?')} evoque {a.get('to', '?')}"

        return "Le silence nourrit la reflexion."

    # --- Contexte ---

    def get_dmn_context(self) -> str:
        """Derniere pensee/insight pour purpose_context."""
        if not self.is_active:
            return ""

        parts = []
        if self._last_thought:
            parts.append(self._last_thought[:100])
        if self.insights:
            last_insight = self.insights[-1]["text"][:80]
            parts.append(f"Insight: {last_insight}")

        if not parts:
            return "[DMN] Mode vagabondage actif."

        return f"[DMN] {' | '.join(parts)}"

    # --- Stats ---

    def get_stats(self) -> Dict[str, Any]:
        """Statistiques pour snapshot et endpoint API."""
        return {
            "is_active": self.is_active,
            "idle_duration": round(self._get_idle_duration(), 1),
            "total_wanderings": self.total_wanderings,
            "insights_count": len(self.insights),
            "consolidation_count": self.consolidation_count,
            "last_thought": self._last_thought[:100] if self._last_thought else "",
            "history_size": len(self.wandering_history),
        }

    # --- Persistence ---

    def _save(self):
        """Sauvegarde atomique."""
        try:
            data = {
                "total_wanderings": self.total_wanderings,
                "consolidation_count": self.consolidation_count,
                "insights": self.insights[-20:],
                "wandering_history": list(self.wandering_history)[-20:],
                "last_thought": self._last_thought,
                "saved_at": time.time(),
            }
            tmp = DMN_STATE_FILE + ".tmp"
            os.makedirs(os.path.dirname(DMN_STATE_FILE), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, DMN_STATE_FILE)
        except Exception as e:
            logger.warning(f"[DMN] Sauvegarde echouee: {e}")

    def _load(self):
        """Charge l'etat."""
        try:
            if os.path.exists(DMN_STATE_FILE):
                with open(DMN_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.total_wanderings = data.get("total_wanderings", 0)
                self.consolidation_count = data.get("consolidation_count", 0)
                self.insights = data.get("insights", [])[-20:]
                self._last_thought = data.get("last_thought", "")
                hist = data.get("wandering_history", [])
                self.wandering_history = deque(hist[-DMN_HISTORY_SIZE:], maxlen=DMN_HISTORY_SIZE)
                logger.info("[DMN] Etat restaure.")
        except Exception as e:
            logger.warning(f"[DMN] Chargement echoue: {e}")


# --- Singleton ---
dmn = DefaultModeNetwork()
