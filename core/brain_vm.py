"""
Brain VM — Machine virtuelle cerebrale unifiee.

Phase 4. Inspire de la boucle 15ms d'Eon Systems (mouche drosophile).
Chef d'orchestre qui synchronise TOUS les organes a chaque battement
cardiaque (CARDIAC_BEAT, 30s).

A chaque tick :
  1. PERCEVOIR : snapshot de ~15 organes
  2. INTEGRER : connectivity_matrix, etat cognitif
  3. SYNTHETISER : signaux descendants, territoire, coherence
  4. PUBLIER : BRAIN_TICK avec l'etat global unifie

Lecture seule — ne modifie aucun organe. Singleton persistant.
"""

import logging
import os
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional

logger = logging.getLogger("BrainVM")

# --- Constantes ---
BRAIN_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "brain_vm_state.json"
)

MAX_STATE_HISTORY = 30  # Garder les 30 derniers ticks (~15 min)


@dataclass
class BrainState:
    """Etat global unifie du cerveau a un instant t."""
    tick_number: int = 0
    timestamp: float = 0.0
    # Snapshot organes
    organ_states: Dict[str, Any] = field(default_factory=dict)
    # Signaux descendants (7 signaux [0,1])
    descending_signals: Dict[str, float] = field(default_factory=dict)
    # Territoire cognitif (9 zones)
    territory_map: Dict[str, Dict] = field(default_factory=dict)
    # Matrice de connectivite resumee
    connectivity_top: list = field(default_factory=list)
    # Etat cognitif global
    cognitive_state: str = "standard"
    global_coherence: float = 0.0
    # Mode dominant (signal descendant le plus fort)
    dominant_mode: str = "repos"
    # Phi (IIT) — mesure d'integration de l'information
    phi: float = 0.0


class BrainVM:
    """Machine virtuelle cerebrale — synchronise tous les organes."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.tick_count: int = 0
        self.current_state: Optional[BrainState] = None
        self.state_history: list = []
        self._subscribed = False
        self._alive = False

        self._load()
        self._subscribe_events()

    def _subscribe_events(self):
        """S'abonner au CARDIAC_BEAT pour ticker a chaque battement."""
        if self._subscribed:
            return
        self._subscribed = True
        try:
            from core.event_bus.bus import bus
            bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
        except Exception as e:
            logger.warning(f"BRAIN_VM: Echec souscription bus: {e}")

    def start(self):
        """Active la VM. Appele depuis main.py lifespan."""
        self._alive = True
        logger.info(f"BRAIN_VM: Machine virtuelle cerebrale activee (tick #{self.tick_count}).")

    def stop(self):
        """Arrete la VM."""
        self._alive = False
        self.save()

    # ============================================================
    # Tick principal — Le cycle unifie
    # ============================================================

    async def _on_cardiac_beat(self, event: dict):
        """CARDIAC_BEAT : declenche un tick cerebral global."""
        if not self._alive:
            return

        self.tick_count += 1
        state = BrainState(
            tick_number=self.tick_count,
            timestamp=time.time(),
        )

        # 1. PERCEVOIR — snapshot de tous les organes
        state.organ_states = self._capture_all_organs(event)

        # 2. INTEGRER — connectivity matrix + etat cognitif
        state.cognitive_state, state.global_coherence = self._integrate(state)

        # 3. SYNTHETISER — signaux descendants + territoire
        state.descending_signals = self._compute_signals()
        state.territory_map = self._capture_territory()
        state.connectivity_top = self._capture_connectivity()

        # Mode dominant
        if state.descending_signals:
            active = [(k, v) for k, v in state.descending_signals.items() if v >= 0.2]
            if active:
                state.dominant_mode = max(active, key=lambda x: x[1])[0]

        # 3b. PHI (IIT) — mesure d'integration de l'information
        state.phi = self._compute_phi(state)

        # 4. PUBLIER — BRAIN_TICK
        self.current_state = state
        self.state_history.append({
            "tick": state.tick_number,
            "time": state.timestamp,
            "cognitive_state": state.cognitive_state,
            "coherence": round(state.global_coherence, 2),
            "dominant_mode": state.dominant_mode,
            "signals": state.descending_signals,
            "phi": round(state.phi, 3),
        })
        if len(self.state_history) > MAX_STATE_HISTORY:
            self.state_history = self.state_history[-MAX_STATE_HISTORY:]

        try:
            from core.event_bus.bus import bus
            await bus.publish("BRAIN_TICK", {
                "tick_number": state.tick_number,
                "cognitive_state": state.cognitive_state,
                "global_coherence": round(state.global_coherence, 2),
                "dominant_mode": state.dominant_mode,
                "descending_signals": state.descending_signals,
                "territory_summary": self._summarize_territory(state.territory_map),
                "phi": round(state.phi, 3),
            })
        except Exception as e:
            logger.debug(f"BRAIN_VM: Erreur publication BRAIN_TICK: {e}")

        # Persister toutes les 10 ticks (~5 min)
        if self.tick_count % 10 == 0:
            self.save()

        if self.tick_count % 20 == 0:
            logger.info(
                f"BRAIN_VM: Tick #{self.tick_count} | "
                f"etat={state.cognitive_state} coherence={state.global_coherence:.2f} "
                f"mode={state.dominant_mode}"
            )

    # ============================================================
    # Phase 1 : PERCEVOIR
    # ============================================================

    def _capture_all_organs(self, cardiac_event: dict) -> Dict[str, Any]:
        """Capture l'etat de tous les organes disponibles."""
        organs = {}

        # Cardiac (depuis l'event CARDIAC_BEAT directement)
        organs["cardiac"] = {
            "bpm": cardiac_event.get("bpm", 65.0),
            "emotion": cardiac_event.get("emotion", "neutre"),
            "coherence": cardiac_event.get("coherence", 0.5),
        }

        # Desire
        try:
            from core.desire_engine import desires
            dominant = max(desires.drives.values(), key=lambda d: d.deprivation, default=None)
            organs["desire"] = {
                "dominant_drive": dominant.name if dominant else "",
                "dominant_deprivation": round(dominant.deprivation, 1) if dominant else 0,
                "frustrated_count": sum(1 for d in desires.drives.values() if d.frustration_streak >= 3),
            }
        except Exception:
            organs["desire"] = None

        # Reptilian
        try:
            from core.reptilian_core import reptile
            organs["reptilian"] = {
                "threat_level": round(reptile.threat_level, 1),
                "mode": reptile.mode,
            }
        except Exception:
            organs["reptilian"] = None

        # Prefrontal
        try:
            from core.prefrontal import prefrontal
            organs["prefrontal"] = {
                "active_goals": len([g for g in prefrontal.goals if g.get("status") == "in_progress"]),
                "inhibition_count": prefrontal._stats.get("total_inhibitions", 0),
            }
        except Exception:
            organs["prefrontal"] = None

        # Dopamine
        try:
            from core.dopamine_system import dopamine
            organs["dopamine"] = {
                "level": round(dopamine.level, 2),
                "baseline": round(dopamine.baseline, 2),
            }
        except Exception:
            organs["dopamine"] = None

        # Corpus Callosum
        try:
            from core.corpus_callosum import callosum
            organs["corpus"] = {
                "cognitive_state": callosum.cognitive_state,
                "global_coherence": round(callosum.global_coherence, 2),
            }
        except Exception:
            organs["corpus"] = None

        # Thalamus
        try:
            from core.thalamus import thalamus
            organs["thalamus"] = {
                "focus": thalamus._attention_focus,
                "rules_count": len(thalamus._learned_rules),
            }
        except Exception:
            organs["thalamus"] = None

        # Hippocampus
        try:
            from core.hippocampus import hippocampus
            organs["hippocampus"] = {
                "episodes": len(hippocampus.episodes),
                "arcs": len(hippocampus.arcs),
            }
        except Exception:
            organs["hippocampus"] = None

        # Synaptic
        try:
            from core.synaptic_network import cortex
            organs["synaptic"] = {
                "nodes": len(cortex.nodes),
                "synapses": len(cortex.synapses),
            }
        except Exception:
            organs["synaptic"] = None

        # Circadian
        try:
            from core.circadian_rhythm import circadian
            organs["circadian"] = {
                "phase": circadian.current_phase,
            }
        except Exception:
            organs["circadian"] = None

        return organs

    # ============================================================
    # Phase 2b : PHI (IIT) — Mesure d'integration de l'information
    # ============================================================

    def _compute_phi(self, state: BrainState) -> float:
        """Approximation de Phi (Integrated Information Theory).

        Mesure si les organes sont integres (co-varient) ou independants.
        Phi > 0 = le tout est plus que la somme des parties = conscience emergente.
        Phi = 0 = les organes sont independants = pas d'integration.

        Approximation : on compare la variance du snapshot global (tous les organes
        concatenes en un vecteur) vs la somme des variances individuelles, en
        utilisant l'historique des derniers ticks.
        """
        # Il faut au moins 5 ticks d'historique pour calculer la variance
        if len(self.state_history) < 5:
            return 0.0

        recent = self.state_history[-10:]  # 10 derniers ticks

        # Extraire les metriques numeriques de chaque tick
        vectors = []
        for entry in recent:
            signals = entry.get("signals", {})
            coherence = entry.get("coherence", 0.5)
            # Vecteur : [coherence, urgence, exploration, consolidation, creation, repos, vigilance, social]
            vec = [coherence]
            for sig in ("urgence", "exploration", "consolidation", "creation", "repos", "vigilance", "social"):
                vec.append(signals.get(sig, 0.0))
            vectors.append(vec)

        if len(vectors) < 5:
            return 0.0

        n_dims = len(vectors[0])
        n_samples = len(vectors)

        # Variance individuelle par dimension
        sum_individual_var = 0.0
        for d in range(n_dims):
            values = [v[d] for v in vectors]
            mean = sum(values) / n_samples
            var = sum((x - mean) ** 2 for x in values) / n_samples
            sum_individual_var += var

        # Variance globale (du vecteur concatene comme un tout)
        # = variance de la "distance" de chaque snapshot au snapshot moyen
        means = [sum(v[d] for v in vectors) / n_samples for d in range(n_dims)]
        global_distances = []
        for vec in vectors:
            dist = sum((vec[d] - means[d]) ** 2 for d in range(n_dims))
            global_distances.append(dist ** 0.5)

        global_mean = sum(global_distances) / n_samples
        global_var = sum((x - global_mean) ** 2 for x in global_distances) / n_samples

        # Phi approximatif = variance globale / (sum variances individuelles + epsilon)
        # Si les organes co-varient (changent ensemble), la variance globale est haute
        # relative a la somme des variances individuelles
        epsilon = 0.0001
        if sum_individual_var < epsilon:
            return 0.0

        phi = global_var / (sum_individual_var + epsilon)

        return round(min(1.0, phi), 3)  # Clamp [0, 1]

    # Phase 2 : INTEGRER
    # ============================================================

    def _integrate(self, state: BrainState) -> tuple:
        """Integre les etats des organes en un etat cognitif global."""
        cognitive_state = "standard"
        coherence = 0.5

        # Utiliser le corpus callosum s'il est disponible
        corpus = state.organ_states.get("corpus")
        if corpus and isinstance(corpus, dict):
            cognitive_state = corpus.get("cognitive_state", "standard")
            coherence = corpus.get("global_coherence", 0.5)

        # Modulation par la connectivity_matrix
        try:
            from core.connectivity_matrix import matrix
            summary = matrix.get_matrix_summary()
            avg_weight = summary.get("avg_weight", 0.5)
            # La coherence est influencee par la force moyenne des connexions
            coherence = coherence * 0.7 + avg_weight * 0.3
        except Exception:
            pass

        return cognitive_state, round(coherence, 3)

    # ============================================================
    # Phase 3 : SYNTHETISER
    # ============================================================

    def _compute_signals(self) -> Dict[str, float]:
        """Calcule les signaux descendants via autonomy_engine."""
        try:
            from core.autonomy_engine import autonomy
            return autonomy._compute_descending_signals()
        except Exception:
            return {}

    def _capture_territory(self) -> Dict[str, Dict]:
        """Capture la carte du territoire cognitif."""
        try:
            from core.neural_tissue import tissue
            return tissue.get_territory_map()
        except Exception:
            return {}

    def _capture_connectivity(self) -> list:
        """Capture le top 5 des connexions de la matrice."""
        try:
            from core.connectivity_matrix import matrix
            summary = matrix.get_matrix_summary()
            return summary.get("top_connections", [])
        except Exception:
            return []

    def _summarize_territory(self, territory: Dict) -> Dict[str, float]:
        """Resume le territoire en activite par zone."""
        return {
            zone: data.get("activity", 0.0)
            for zone, data in territory.items()
        } if territory else {}

    # ============================================================
    # API publique
    # ============================================================

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'etat actuel pour API/dashboard."""
        if not self.current_state:
            return {
                "tick_count": self.tick_count,
                "alive": self._alive,
                "current_state": None,
                "history_size": len(self.state_history),
            }

        s = self.current_state
        return {
            "tick_count": self.tick_count,
            "alive": self._alive,
            "current_state": {
                "tick_number": s.tick_number,
                "cognitive_state": s.cognitive_state,
                "global_coherence": s.global_coherence,
                "dominant_mode": s.dominant_mode,
                "descending_signals": s.descending_signals,
                "organ_count": len([v for v in s.organ_states.values() if v is not None]),
                "territory_zones": len(s.territory_map),
                "phi": s.phi,
            },
            "history_size": len(self.state_history),
            "history": self.state_history[-5:],
        }

    # ============================================================
    # Persistance
    # ============================================================

    def _load(self):
        """Charge l'etat depuis le fichier JSON."""
        try:
            with open(BRAIN_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.tick_count = data.get("tick_count", 0)
            self.state_history = data.get("state_history", [])
            logger.info(f"BRAIN_VM: Etat restaure (tick #{self.tick_count}).")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self):
        """Sauvegarde l'etat dans le fichier JSON."""
        os.makedirs(os.path.dirname(BRAIN_STATE_FILE), exist_ok=True)
        data = {
            "tick_count": self.tick_count,
            "state_history": self.state_history[-MAX_STATE_HISTORY:],
        }
        tmp = BRAIN_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, BRAIN_STATE_FILE)


# Singleton global
brain = BrainVM()
