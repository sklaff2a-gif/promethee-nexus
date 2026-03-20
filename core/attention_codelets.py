"""
Attention Codelets — Micro-detecteurs LIDA pour Promethee.

Inspire de LIDA (Learning Intelligent Distribution Agent) :
les codelets sont des micro-agents legers qui scannent l'etat cerebral
a chaque BRAIN_TICK (30s) a la recherche de patterns specifiques
(danger, stagnation, nouveaute, opportunite, contradiction).

Ils ne FONT rien — ils DETECTENT et soumettent des alertes au
Global Workspace avec haute saillance. C'est le workspace qui decide
si l'alerte accede a la conscience.

Architecture :
- Registre de codelets via decorateur @codelet
- Chaque codelet = fonction pure (tick_data, history) -> Optional[CodeletAlert]
- CodeletSystem singleton s'abonne a BRAIN_TICK
- Cooldown configurable par codelet (evite le spam)
- Extensible : ajouter un codelet = ecrire une fonction decoree

Singleton. 0 appel LLM.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("AttentionCodelets")


# ============================================================
# Structures de donnees
# ============================================================

@dataclass
class CodeletAlert:
    """Alerte emise par un codelet quand un pattern est detecte."""
    name: str           # Nom du codelet (ex: "danger")
    content: str        # Description de l'alerte
    salience: float     # [0, 1] importance
    category: str       # Categorie workspace (urgence, cognition, emotion, motivation)
    priority: str = "normal"  # "critical" / "high" / "normal"
    timestamp: float = field(default_factory=time.time)


# ============================================================
# Registre de codelets
# ============================================================

# (func, cooldown_seconds)
_CODELET_REGISTRY: Dict[str, Tuple[Callable, float]] = {}

DEFAULT_COOLDOWN = 300.0  # 5 minutes entre deux alertes du meme codelet


def codelet(name: str, cooldown: float = DEFAULT_COOLDOWN):
    """Decorateur pour enregistrer un codelet d'attention.

    Usage:
        @codelet("danger", cooldown=300)
        def detect_danger(tick_data: dict, history: list) -> Optional[CodeletAlert]:
            ...
    """
    def decorator(func: Callable) -> Callable:
        _CODELET_REGISTRY[name] = (func, cooldown)
        return func
    return decorator


# ============================================================
# Les 5 codelets de base
# ============================================================

@codelet("danger", cooldown=300.0)
def detect_danger(tick_data: dict, history: list) -> Optional[CodeletAlert]:
    """Detecte quand threat_level > 4 (danger imminent).

    Le reptilien monte au-dessus de 4 = situation anormale.
    Au-dessus de 6 = critique (bypass le filtre du workspace).
    """
    organs = tick_data.get("organ_states", {})
    reptilian = organs.get("reptilian")
    if reptilian and isinstance(reptilian, dict):
        threat = reptilian.get("threat_level", 0)
        if threat > 4.0:
            return CodeletAlert(
                name="danger",
                content=f"ALERTE: menace detectee (threat={threat:.1f})",
                salience=0.9,
                category="urgence",
                priority="critical" if threat >= 6.0 else "high",
            )
    return None


@codelet("stagnation", cooldown=300.0)
def detect_stagnation(tick_data: dict, history: list) -> Optional[CodeletAlert]:
    """Detecte quand le meme mode dominant est repete 5+ fois de suite.

    Stagnation cognitive = le systeme est bloque dans une boucle.
    Le prefrontal ou le loop_breaker devraient reagir.
    """
    if len(history) < 5:
        return None
    recent_modes = [h.get("dominant_mode", "") for h in history[-5:]]
    if len(set(recent_modes)) == 1 and recent_modes[0]:
        return CodeletAlert(
            name="stagnation",
            content=f"STAGNATION: mode {recent_modes[0]} repete 5x de suite",
            salience=0.7,
            category="cognition",
        )
    return None


@codelet("novelty", cooldown=180.0)
def detect_novelty(tick_data: dict, history: list) -> Optional[CodeletAlert]:
    """Detecte un changement d'etat cognitif (transition).

    Les transitions cognitives sont des moments importants —
    le systeme change de regime. Cooldown plus court (3 min)
    car les transitions sont naturellement rares.
    """
    if len(history) < 2:
        return None
    prev = history[-2].get("cognitive_state", "standard")
    curr = tick_data.get("cognitive_state", "standard")
    if prev != curr and curr != "standard":
        return CodeletAlert(
            name="novelty",
            content=f"TRANSITION: {prev} -> {curr}",
            salience=0.6,
            category="cognition",
        )
    return None


@codelet("opportunity", cooldown=300.0)
def detect_opportunity(tick_data: dict, history: list) -> Optional[CodeletAlert]:
    """Detecte quand un drive est tres affame (>80) — opportunite d'action.

    Un drive a haute deprivation = besoin non satisfait depuis longtemps.
    C'est une opportunite d'orienter le comportement.
    """
    organs = tick_data.get("organ_states", {})
    desires = organs.get("desire")
    if desires and isinstance(desires, dict):
        drive = desires.get("dominant_drive", "")
        dep = desires.get("dominant_deprivation", 0)
        if dep > 80:
            return CodeletAlert(
                name="opportunity",
                content=f"OPPORTUNITE: {drive} affame (dep={dep:.0f})",
                salience=0.6,
                category="motivation",
            )
    return None


@codelet("contradiction", cooldown=300.0)
def detect_contradiction(tick_data: dict, history: list) -> Optional[CodeletAlert]:
    """Detecte une contradiction entre organes.

    Dopamine haute + emotion negative = dissonance cognitive.
    Le systeme "se sent bien" chimiquement mais "se sent mal" emotionnellement.
    """
    organs = tick_data.get("organ_states", {})
    dopamine = organs.get("dopamine")
    cardiac = organs.get("cardiac")
    if dopamine and cardiac and isinstance(dopamine, dict) and isinstance(cardiac, dict):
        dopa_level = dopamine.get("level", 0.5)
        emotion = cardiac.get("emotion", "")
        negative_emotions = {"frustration", "inquietude", "peur", "panique", "fatigue"}
        if dopa_level > 0.7 and emotion in negative_emotions:
            return CodeletAlert(
                name="contradiction",
                content=f"CONTRADICTION: dopamine={dopa_level:.2f} mais emotion={emotion}",
                salience=0.7,
                category="emotion",
            )
    return None


# ============================================================
# Systeme de codelets (singleton)
# ============================================================

class CodeletSystem:
    """Gestionnaire des codelets d'attention — singleton.

    S'abonne a BRAIN_TICK, execute tous les codelets enregistres,
    soumet les alertes au Global Workspace.
    """

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

        self._cooldowns: Dict[str, float] = {}
        self._last_alerts: List[CodeletAlert] = []
        self._stats = {
            "total_runs": 0,
            "total_alerts": 0,
            "alerts_by_codelet": {},
        }
        self._subscribed = False
        self._subscribe_events()

    def _subscribe_events(self):
        """S'abonner a BRAIN_TICK pour executer les codelets apres chaque tick."""
        if self._subscribed:
            return
        self._subscribed = True
        try:
            from core.event_bus.bus import bus
            bus.subscribe("BRAIN_TICK", self._on_brain_tick)
        except Exception as e:
            logger.warning(f"CODELETS: Echec souscription bus: {e}")

    # ============================================================
    # Execution des codelets
    # ============================================================

    def run_all(self, tick_data: dict, history: list) -> List[CodeletAlert]:
        """Execute tous les codelets enregistres et retourne les alertes.

        Respecte les cooldowns individuels. Soumet les alertes au workspace.
        """
        self._stats["total_runs"] += 1
        now = time.time()
        alerts = []

        for name, (func, cooldown) in _CODELET_REGISTRY.items():
            # Verifier le cooldown
            last_fire = self._cooldowns.get(name, 0)
            if now - last_fire < cooldown:
                continue

            try:
                alert = func(tick_data, history)
                if alert:
                    self._cooldowns[name] = now
                    alerts.append(alert)
                    # Stats
                    self._stats["total_alerts"] += 1
                    counts = self._stats["alerts_by_codelet"]
                    counts[name] = counts.get(name, 0) + 1
            except Exception as e:
                logger.debug(f"CODELET {name}: Erreur: {e}")

        # Stocker les alertes recentes
        self._last_alerts = alerts

        # Soumettre au Global Workspace
        for alert in alerts:
            try:
                from core.global_workspace import workspace
                workspace.submit(
                    source=f"codelet_{alert.name}",
                    content=alert.content,
                    salience=alert.salience,
                    category=alert.category,
                    priority=alert.priority,
                )
            except Exception:
                pass

        if alerts:
            logger.info(
                f"CODELETS: {len(alerts)} alerte(s) — "
                + ", ".join(a.name for a in alerts)
            )

        return alerts

    # ============================================================
    # Handler bus
    # ============================================================

    async def _on_brain_tick(self, event: dict):
        """BRAIN_TICK : executer les codelets sur le tick courant."""
        try:
            from core.brain_vm import brain
            # Construire tick_data depuis l'etat courant du brain
            tick_data = {}
            if brain.current_state:
                tick_data = {
                    "cognitive_state": brain.current_state.cognitive_state,
                    "global_coherence": brain.current_state.global_coherence,
                    "dominant_mode": brain.current_state.dominant_mode,
                    "organ_states": brain.current_state.organ_states,
                    "descending_signals": brain.current_state.descending_signals,
                    "phi": brain.current_state.phi,
                }
            self.run_all(tick_data, brain.state_history)
        except Exception as e:
            logger.debug(f"CODELETS: Erreur on_brain_tick: {e}")

    # ============================================================
    # API publique
    # ============================================================

    def get_recent_alerts(self) -> List[CodeletAlert]:
        """Retourne les dernieres alertes emises."""
        return list(self._last_alerts)

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'etat du systeme de codelets."""
        now = time.time()
        codelet_states = {}
        for name, (_, cooldown) in _CODELET_REGISTRY.items():
            last_fire = self._cooldowns.get(name, 0)
            elapsed = now - last_fire if last_fire > 0 else -1
            codelet_states[name] = {
                "cooldown": cooldown,
                "last_fire_ago": round(elapsed, 0) if elapsed >= 0 else None,
                "ready": elapsed < 0 or elapsed >= cooldown,
                "total_alerts": self._stats["alerts_by_codelet"].get(name, 0),
            }
        return {
            "registered_codelets": len(_CODELET_REGISTRY),
            "total_runs": self._stats["total_runs"],
            "total_alerts": self._stats["total_alerts"],
            "last_alerts": [
                {"name": a.name, "content": a.content, "salience": a.salience}
                for a in self._last_alerts
            ],
            "codelets": codelet_states,
        }

    def get_registered_names(self) -> List[str]:
        """Retourne les noms de tous les codelets enregistres."""
        return list(_CODELET_REGISTRY.keys())


# Singleton global
codelet_system = CodeletSystem()
