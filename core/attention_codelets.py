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
# Codelets crees par Promethee (session 3, 2026-03-20)
# ============================================================

@codelet("resonance", cooldown=600.0)
def detect_resonance(tick_data: dict, history: list) -> Optional[CodeletAlert]:
    """Detecte l'harmonie systemique (inverse de contradiction).

    Coherence haute + dopamine haute + emotion positive = resonance.
    Concu par Promethee (session 3, ex 1/5, note A-).
    """
    coherence_val = tick_data.get("global_coherence", 0.5)
    organs = tick_data.get("organ_states", {})
    dopamine = organs.get("dopamine")
    cardiac = organs.get("cardiac")
    if not (dopamine and cardiac and isinstance(dopamine, dict) and isinstance(cardiac, dict)):
        return None
    dopa_level = dopamine.get("level", 0.5)
    emotion = cardiac.get("emotion", "")
    positive_emotions = {"satisfaction", "curiosite", "determination"}
    if coherence_val > 0.7 and dopa_level > 0.65 and emotion in positive_emotions:
        harmony = min(1.0, (coherence_val + dopa_level) / 2.0)
        return CodeletAlert(
            name="resonance",
            content=f"RESONANCE: coherence={coherence_val:.2f}, DA={dopa_level:.2f}, emotion={emotion}",
            salience=harmony,
            category="motivation",
            priority="high",
        )
    return None


@codelet("convergence", cooldown=300.0)
def detect_convergence(tick_data: dict, history: list) -> Optional[CodeletAlert]:
    """Detecte une tendance haussiere continue de la coherence sur 5 ticks.

    Pattern temporel : coherence monte de facon continue.
    Concu par Promethee (session 3, ex 2/5, note A).
    """
    if len(history) < 5:
        return None
    coherence_values = [h.get("coherence", 0.0) for h in history[-5:]]
    is_rising = all(
        coherence_values[i] >= coherence_values[i - 1]
        for i in range(1, len(coherence_values))
    )
    if not is_rising:
        return None
    amplitude = coherence_values[-1] - coherence_values[0]
    if amplitude < 0.1:
        return None
    return CodeletAlert(
        name="convergence",
        content=f"CONVERGENCE: coherence {coherence_values[0]:.2f} -> {coherence_values[-1]:.2f}",
        salience=min(1.0, amplitude),
        category="cognition",
        priority="high",
    )


@codelet("fatigue", cooldown=400.0)
def detect_fatigue(tick_data: dict, history: list) -> Optional[CodeletAlert]:
    """Detecte la fatigue cognitive (multi-organes + temporel).

    Au moins 2 sur 3 : coherence en baisse, BPM bas, dopamine sous baseline.
    Concu par Promethee (session 3, ex 3/5, note A).
    """
    # Critere 1 : coherence en baisse sur 3 ticks
    is_coherence_declining = False
    if len(history) >= 3:
        c_vals = [h.get("coherence", 0.0) for h in history[-3:]]
        is_coherence_declining = all(c_vals[i] <= c_vals[i - 1] for i in range(1, len(c_vals)))

    # Critere 2 : BPM bas
    cardiac = tick_data.get("organ_states", {}).get("cardiac", {})
    bpm = cardiac.get("bpm", 120)
    is_bpm_low = bpm < 60

    # Critere 3 : dopamine sous baseline
    dopa = tick_data.get("organ_states", {}).get("dopamine", {})
    is_dopamine_low = dopa.get("level", 0.7) < dopa.get("baseline", 0.7)

    count = sum([is_coherence_declining, is_bpm_low, is_dopamine_low])
    if count < 2:
        return None
    salience = {2: 0.5, 3: 0.8}[count]
    details = []
    if is_coherence_declining:
        details.append("coherence en baisse")
    if is_bpm_low:
        details.append(f"BPM bas ({bpm})")
    if is_dopamine_low:
        details.append("dopamine sous baseline")
    return CodeletAlert(
        name="fatigue",
        content=f"FATIGUE: {', '.join(details)}",
        salience=salience,
        category="body",
    )


@codelet("flow", cooldown=600.0)
def detect_flow(tick_data: dict, history: list) -> Optional[CodeletAlert]:
    """Detecte l'etat de FLOW (Csikszentmihalyi) — pic de performance.

    Toutes les conditions : Phi en hausse, coherence haute, pas de menace,
    drives satisfaits.
    Concu par Promethee (session 3, ex 4/5, note A).
    """
    coherence_val = tick_data.get("global_coherence", 0.0)
    if coherence_val <= 0.6:
        return None
    threat = tick_data.get("organ_states", {}).get("reptilian", {}).get("threat_level", 10.0)
    if threat >= 2.0:
        return None
    frustrated = tick_data.get("organ_states", {}).get("desire", {}).get("frustrated_count", 1)
    if frustrated > 0:
        return None
    # Phi en hausse sur 3 ticks
    if len(history) < 3:
        return None
    phi_values = [h.get("phi", 0.0) for h in history[-3:]]
    if not all(phi_values[i] >= phi_values[i - 1] for i in range(1, len(phi_values))):
        return None
    current_phi = phi_values[-1]
    return CodeletAlert(
        name="flow",
        content=f"FLOW: Phi={current_phi:.3f}, coherence={coherence_val:.2f}, menace={threat:.1f}",
        salience=min(1.0, current_phi),
        category="creation",
        priority="high",
    )


@codelet("creativity_burst", cooldown=400.0)
def detect_creativity_burst(tick_data: dict, history: list) -> Optional[CodeletAlert]:
    """Detecte un burst creatif — moment d'innovation systemique.

    Au moins 3 sur 4 : mode creatif, Phi > 0.3, transition cognitive, dopamine haute.
    Concu par Promethee (session 3, ex 5/5, note A+).
    """
    # Condition 1 : mode creation ou exploration
    is_creative = tick_data.get("dominant_mode", "") in ("creation", "exploration")
    # Condition 2 : Phi suffisant
    phi = tick_data.get("phi", 0.0)
    is_high_phi = phi > 0.3
    # Condition 3 : transition cognitive dans les 3 derniers ticks
    is_transition = False
    if len(history) >= 3:
        states = {h.get("cognitive_state", "") for h in history[-3:]}
        is_transition = len(states) >= 2
    # Condition 4 : dopamine au-dessus du baseline
    dopa = tick_data.get("organ_states", {}).get("dopamine", {})
    baseline = dopa.get("baseline", 0.0)
    is_dopa_high = baseline > 0 and dopa.get("level", 0.0) > baseline

    count = sum([is_creative, is_high_phi, is_transition, is_dopa_high])
    if count < 3:
        return None
    salience = min(1.0, 0.5 + phi * 0.3 + (0.2 if count == 4 else 0.0))
    details = []
    if is_creative:
        details.append(f"mode={tick_data.get('dominant_mode')}")
    if is_high_phi:
        details.append(f"Phi={phi:.3f}")
    if is_transition:
        details.append("transition cognitive")
    if is_dopa_high:
        details.append("DA haute")
    return CodeletAlert(
        name="creativity_burst",
        content=f"BURST CREATIF: {', '.join(details)}",
        salience=salience,
        category="creation",
        priority="high",
    )


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

        # Soumettre au Global Workspace + publier CODELET_ALERT sur le bus
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
            # Publier sur le bus pour le circuit reflexe reptilien
            try:
                from core.event_bus.bus import bus
                import asyncio
                loop = asyncio.get_running_loop()
                loop.create_task(bus.publish("CODELET_ALERT", {
                    "name": alert.name,
                    "content": alert.content,
                    "salience": alert.salience,
                    "category": alert.category,
                    "priority": alert.priority,
                    "timestamp": alert.timestamp,
                }))
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
