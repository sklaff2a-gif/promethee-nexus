# core/signal_bus.py — SignalBus : Bus de Signaux Neuraux
# Wrapper transparent sur InMemoryEventBus.
# Ajoute : typage (NeuralSignal), priorites, throttling, metriques, anti-flood.
# Monkey-patche bus.publish via init() — zero modification aux organes existants.
# 0 LLM, 100% deterministe.

import json
import os
import time
import logging
import uuid
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Deque
from collections import deque

from core.event_bus.bus import bus

logger = logging.getLogger("SignalBus")

# --- Fichier de persistance ---

SIGNAL_BUS_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "signal_bus_state.json"
)

# --- Constantes ---

AUTO_FLOOD_WINDOW = 60.0        # Fenetre de detection flood (secondes)
AUTO_FLOOD_THRESHOLD = 50       # >50 signaux/60s → auto-throttle
AUTO_FLOOD_LIMIT = 10           # Limite imposee apres detection flood
AUTO_FLOOD_DURATION = 300.0     # Duree du throttle auto (5 min)

THROUGHPUT_WINDOW = 60.0        # Fenetre pour calcul debit (secondes)
METRICS_TOP_VOLUME = 10         # Top N par volume dans get_metrics()
METRICS_TOP_LATENCY = 5         # Top N par latence dans get_metrics()

SALIENCE_PROMOTION_THRESHOLD = 0.8  # Seuil thalamus pour promotion HIGH


# --- Enums & Dataclasses ---

class SignalPriority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


@dataclass
class NeuralSignal:
    signal_type: str
    origin: str
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: SignalPriority = SignalPriority.NORMAL
    timestamp: float = 0.0
    ttl: Optional[float] = None
    correlation_id: Optional[str] = None

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class SignalMetrics:
    published: int = 0
    delivered: int = 0
    dropped_throttle: int = 0
    dropped_ttl: int = 0
    errors: int = 0
    total_latency_ms: float = 0.0

    def to_dict(self) -> dict:
        avg_latency = (self.total_latency_ms / self.delivered) if self.delivered > 0 else 0.0
        return {
            "published": self.published,
            "delivered": self.delivered,
            "dropped_throttle": self.dropped_throttle,
            "dropped_ttl": self.dropped_ttl,
            "errors": self.errors,
            "avg_latency_ms": round(avg_latency, 3),
        }


@dataclass
class ThrottleRule:
    max_per_window: int
    window_seconds: float


# --- Priority mapping statique ---

_PRIORITY_MAP: Dict[str, SignalPriority] = {
    # CRITICAL
    "REPTILIAN_ALERT": SignalPriority.CRITICAL,
    "OLLAMA_UNRESPONSIVE": SignalPriority.CRITICAL,
    "HYPOTHALAMUS_ALARM": SignalPriority.CRITICAL,
    "SMART_RESTART_REQUESTED": SignalPriority.CRITICAL,
    # HIGH
    "DOPAMINE_SURGE": SignalPriority.HIGH,
    "HALLUCINATION_DETECTED": SignalPriority.HIGH,
    "EUREKA_BRIDGE": SignalPriority.HIGH,
    "KILL_SWITCH_ACTIVATED": SignalPriority.HIGH,
    "REPTILIAN_REFLEX": SignalPriority.HIGH,
    # LOW
    "THOUGHT_STREAM": SignalPriority.LOW,
    "AGENT_STREAM": SignalPriority.LOW,
    "CARDIAC_BEAT": SignalPriority.LOW,
    "THALAMUS_SALIENCE": SignalPriority.LOW,
}

# --- Throttle par defaut ---

_DEFAULT_THROTTLES: Dict[str, ThrottleRule] = {
    "CARDIAC_BEAT": ThrottleRule(max_per_window=4, window_seconds=60.0),
    "THOUGHT_STREAM": ThrottleRule(max_per_window=10, window_seconds=60.0),
    "AGENT_STREAM": ThrottleRule(max_per_window=20, window_seconds=60.0),
    "THALAMUS_SALIENCE": ThrottleRule(max_per_window=4, window_seconds=60.0),
}

# --- Origin mapping (prefixe → organe) ---

_ORIGIN_MAP: Dict[str, str] = {
    "REPTILIAN": "reptilian",
    "DOPAMINE": "dopamine",
    "CARDIAC": "cardiac",
    "HEART": "cardiac",
    "PSYCHE": "psyche",
    "DESIRE": "desire",
    "PREFRONTAL": "prefrontal",
    "HIPPOCAMPUS": "hippocampus",
    "HIPPO": "hippocampus",
    "CORPUS": "corpus_callosum",
    "CALLOSUM": "corpus_callosum",
    "INNER_VOICE": "inner_voice",
    "THOUGHT": "inner_voice",
    "THALAMUS": "thalamus",
    "HYPOTHALAMUS": "hypothalamus",
    "AMYGDALA": "amygdala",
    "INSULA": "insula",
    "CINGULATE": "cingulate",
    "BASAL": "basal_ganglia",
    "DMN": "default_mode",
    "AGENT": "agent",
    "EVOLUTION": "evolution",
    "MISSION": "orchestrator",
    "ARTIFACT": "factory",
    "SMART_RESTART": "system",
    "KILL_SWITCH": "system",
    "OLLAMA": "system",
    "EUREKA": "corpus_callosum",
    "HALLUCINATION": "coder",
    "COUNCIL": "council",
    "MEMORY": "memory",
}


# ================================================================
# CLASSE PRINCIPALE
# ================================================================

class SignalBus:
    """Singleton — Bus de signaux neuraux avec typage, throttling et metriques."""

    _instance: Optional["SignalBus"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Reference au publish original (AVANT monkey-patch)
        self._original_publish = None
        self._patched = False

        # Metriques par signal_type
        self._metrics: Dict[str, SignalMetrics] = {}

        # Throttling
        self._throttle_rules: Dict[str, ThrottleRule] = dict(_DEFAULT_THROTTLES)
        self._throttle_windows: Dict[str, Deque[float]] = {}

        # Auto-flood tracking
        self._flood_windows: Dict[str, Deque[float]] = {}
        self._auto_throttle_until: Dict[str, float] = {}

        # Throughput global
        self._recent_signals: Deque[float] = deque()

        # Compteurs globaux
        self.total_published: int = 0
        self.total_delivered: int = 0
        self.total_dropped: int = 0
        self.total_errors: int = 0

        self._load()

    @classmethod
    def reset_singleton(cls):
        """Detruit le singleton (pour tests)."""
        if cls._instance is not None and cls._instance._patched:
            cls._instance._restore_publish()
        cls._instance = None

    # ================================================================
    # INITIALISATION & SHUTDOWN
    # ================================================================

    def init(self):
        """Installe le wrapper sur bus.publish. Appele depuis main.py."""
        if self._patched:
            logger.warning("SIGNAL_BUS: Deja patche, skip.")
            return

        self._original_publish = bus.publish
        bus.publish = self._wrapped_publish
        self._patched = True

        logger.info(
            f"SIGNAL_BUS: Bus neural actif "
            f"({len(self._throttle_rules)} throttles, "
            f"{len(_PRIORITY_MAP)} priorites mappees)."
        )

    def shutdown(self):
        """Restaure bus.publish original et sauvegarde."""
        self._restore_publish()
        self._save()
        logger.info("SIGNAL_BUS: Shutdown, bus.publish restaure.")

    def _restore_publish(self):
        """Restaure bus.publish a l'original (supprime le monkey-patch)."""
        if self._patched:
            # Supprimer l'attribut d'instance pour restaurer la methode de classe
            if 'publish' in vars(bus):
                del vars(bus)['publish']
            self._patched = False

    # ================================================================
    # WRAPPER TRANSPARENT
    # ================================================================

    async def _wrapped_publish(self, event_type: str, payload: Any = None):
        """Wrapper transparent pour bus.publish.

        Cree un NeuralSignal, applique throttle/TTL/metriques,
        puis delegue au publish original.
        """
        now = time.time()
        signal = NeuralSignal(
            signal_type=event_type,
            origin=self._infer_origin(event_type),
            payload=payload if isinstance(payload, dict) else {"data": payload},
            priority=_PRIORITY_MAP.get(event_type, SignalPriority.NORMAL),
            timestamp=now,
        )
        await self.emit(signal)

    async def emit(self, signal: NeuralSignal) -> bool:
        """Publie un signal type. Retourne True si delivre, False si drop."""
        now = time.time()
        st = signal.signal_type
        metrics = self._get_or_create_metrics(st)

        # 1. TTL check
        if signal.ttl is not None:
            age = now - signal.timestamp
            if age > signal.ttl:
                metrics.dropped_ttl += 1
                self.total_dropped += 1
                return False

        # 2. Throttle check
        if not self._check_throttle(st, now):
            metrics.dropped_throttle += 1
            self.total_dropped += 1
            return False

        # 3. Auto-flood check
        if not self._check_auto_flood(st, now):
            metrics.dropped_throttle += 1
            self.total_dropped += 1
            return False

        # 4. Priority promotion (thalamus)
        self._maybe_promote_priority(signal)

        # 5. Publier via le bus original
        metrics.published += 1
        self.total_published += 1

        t0 = time.perf_counter()
        try:
            await self._original_publish(signal.signal_type, signal.payload)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            metrics.delivered += 1
            metrics.total_latency_ms += elapsed_ms
            self.total_delivered += 1

            # Tracking throughput
            self._recent_signals.append(now)

            return True

        except Exception as e:
            metrics.errors += 1
            self.total_errors += 1
            logger.error(f"SIGNAL_BUS: Erreur emission {st}: {e}")
            return False

    # ================================================================
    # THROTTLING
    # ================================================================

    def _check_throttle(self, signal_type: str, now: float) -> bool:
        """Verifie si le signal passe le throttle. True = OK."""
        rule = self._throttle_rules.get(signal_type)
        if rule is None:
            return True

        if signal_type not in self._throttle_windows:
            self._throttle_windows[signal_type] = deque()

        window = self._throttle_windows[signal_type]

        # Purger les timestamps hors fenetre
        cutoff = now - rule.window_seconds
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= rule.max_per_window:
            return False

        window.append(now)
        return True

    def _check_auto_flood(self, signal_type: str, now: float) -> bool:
        """Detecte flood automatique (>50 signaux/60s sans regle explicite).
        True = OK, False = drop.
        """
        # Si deja en auto-throttle
        if signal_type in self._auto_throttle_until:
            deadline = self._auto_throttle_until[signal_type]
            if now < deadline:
                # Appliquer la limite auto-flood
                if signal_type not in self._throttle_windows:
                    self._throttle_windows[signal_type] = deque()
                window = self._throttle_windows[signal_type]
                cutoff = now - AUTO_FLOOD_WINDOW
                while window and window[0] < cutoff:
                    window.popleft()
                if len(window) >= AUTO_FLOOD_LIMIT:
                    return False
                window.append(now)
                return True
            else:
                del self._auto_throttle_until[signal_type]

        # Pas de regle explicite → surveiller le flood
        if signal_type in self._throttle_rules:
            return True  # Deja gere par _check_throttle

        if signal_type not in self._flood_windows:
            self._flood_windows[signal_type] = deque()

        flood_win = self._flood_windows[signal_type]
        cutoff = now - AUTO_FLOOD_WINDOW
        while flood_win and flood_win[0] < cutoff:
            flood_win.popleft()

        flood_win.append(now)

        if len(flood_win) > AUTO_FLOOD_THRESHOLD:
            self._auto_throttle_until[signal_type] = now + AUTO_FLOOD_DURATION
            logger.warning(
                f"SIGNAL_BUS: Auto-flood detecte pour {signal_type} "
                f"({len(flood_win)} signaux/60s). Throttle {AUTO_FLOOD_LIMIT}/min pendant 5 min."
            )
            return True  # Le signal courant passe, les suivants seront throttles

        return True

    def set_throttle(self, signal_type: str, max_per_window: int, window_seconds: float = 60.0):
        """Ajoute/modifie une regle de throttle."""
        self._throttle_rules[signal_type] = ThrottleRule(
            max_per_window=max_per_window,
            window_seconds=window_seconds,
        )
        logger.info(f"SIGNAL_BUS: Throttle {signal_type} → {max_per_window}/{window_seconds}s")

    def remove_throttle(self, signal_type: str):
        """Supprime une regle de throttle."""
        self._throttle_rules.pop(signal_type, None)
        self._throttle_windows.pop(signal_type, None)

    # ================================================================
    # PRIORITY
    # ================================================================

    def _maybe_promote_priority(self, signal: NeuralSignal):
        """Consulte le thalamus pour promouvoir un signal NORMAL → HIGH."""
        if signal.priority != SignalPriority.NORMAL:
            return
        try:
            from core.thalamus import thalamus
            salience = thalamus.compute_salience(signal.signal_type)
            if salience is not None and salience > SALIENCE_PROMOTION_THRESHOLD:
                signal.priority = SignalPriority.HIGH
        except Exception:
            pass  # Thalamus non disponible → pas de promotion

    # ================================================================
    # ORIGIN INFERENCE
    # ================================================================

    @staticmethod
    def _infer_origin(event_type: str) -> str:
        """Deduit l'organe source du prefixe de l'event type."""
        # Essayer chaque prefixe du plus long au plus court
        for prefix, origin in _ORIGIN_MAP.items():
            if event_type.startswith(prefix):
                return origin
        return "unknown"

    # ================================================================
    # METRIQUES
    # ================================================================

    def _get_or_create_metrics(self, signal_type: str) -> SignalMetrics:
        if signal_type not in self._metrics:
            self._metrics[signal_type] = SignalMetrics()
        return self._metrics[signal_type]

    def get_metrics(self) -> Dict[str, Any]:
        """Metriques completes."""
        now = time.time()

        # Par signal type
        per_type = {}
        for st, m in self._metrics.items():
            per_type[st] = m.to_dict()

        # Top volume
        sorted_vol = sorted(
            self._metrics.items(),
            key=lambda x: x[1].published,
            reverse=True
        )[:METRICS_TOP_VOLUME]
        top_volume = [{
            "signal_type": st,
            "published": m.published,
        } for st, m in sorted_vol]

        # Top latence
        sorted_lat = sorted(
            ((st, m) for st, m in self._metrics.items() if m.delivered > 0),
            key=lambda x: x[1].total_latency_ms / x[1].delivered,
            reverse=True
        )[:METRICS_TOP_LATENCY]
        top_latency = [{
            "signal_type": st,
            "avg_latency_ms": round(m.total_latency_ms / m.delivered, 3),
        } for st, m in sorted_lat]

        return {
            "global": {
                "total_published": self.total_published,
                "total_delivered": self.total_delivered,
                "total_dropped": self.total_dropped,
                "total_errors": self.total_errors,
                "throughput_per_sec": round(self.get_throughput(), 2),
                "active_throttles": len(self._throttle_rules),
                "auto_flood_active": len(self._auto_throttle_until),
            },
            "per_type": per_type,
            "top_volume": top_volume,
            "top_latency": top_latency,
            "throttle_rules": {
                st: {"max": r.max_per_window, "window_s": r.window_seconds}
                for st, r in self._throttle_rules.items()
            },
        }

    def get_stats(self) -> Dict[str, Any]:
        """Version allégée pour self_awareness."""
        return {
            "total_published": self.total_published,
            "total_delivered": self.total_delivered,
            "total_dropped": self.total_dropped,
            "total_errors": self.total_errors,
            "throughput_per_sec": round(self.get_throughput(), 2),
            "signal_types_seen": len(self._metrics),
        }

    def get_throughput(self) -> float:
        """Signaux par seconde sur la fenetre de 60s."""
        now = time.time()
        cutoff = now - THROUGHPUT_WINDOW
        while self._recent_signals and self._recent_signals[0] < cutoff:
            self._recent_signals.popleft()
        count = len(self._recent_signals)
        return count / THROUGHPUT_WINDOW if count > 0 else 0.0

    # ================================================================
    # PERSISTANCE
    # ================================================================

    def _load(self):
        """Charge les throttles custom et compteurs depuis JSON."""
        try:
            if os.path.exists(SIGNAL_BUS_STATE_FILE):
                with open(SIGNAL_BUS_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Restaurer compteurs
                self.total_published = data.get("total_published", 0)
                self.total_delivered = data.get("total_delivered", 0)
                self.total_dropped = data.get("total_dropped", 0)
                self.total_errors = data.get("total_errors", 0)

                # Restaurer throttles custom
                custom = data.get("custom_throttles", {})
                for st, rule_dict in custom.items():
                    self._throttle_rules[st] = ThrottleRule(
                        max_per_window=rule_dict["max_per_window"],
                        window_seconds=rule_dict["window_seconds"],
                    )

                logger.info(
                    f"SIGNAL_BUS: Etat restaure "
                    f"(published={self.total_published}, custom_throttles={len(custom)})."
                )
        except Exception as e:
            logger.warning(f"SIGNAL_BUS: Echec chargement: {e}")

    def _save(self):
        """Sauvegarde atomique de l'etat."""
        try:
            # Identifier les throttles custom (pas dans les defaults)
            custom = {}
            for st, rule in self._throttle_rules.items():
                if st not in _DEFAULT_THROTTLES:
                    custom[st] = {
                        "max_per_window": rule.max_per_window,
                        "window_seconds": rule.window_seconds,
                    }

            data = {
                "total_published": self.total_published,
                "total_delivered": self.total_delivered,
                "total_dropped": self.total_dropped,
                "total_errors": self.total_errors,
                "custom_throttles": custom,
                "saved_at": time.time(),
            }
            tmp = SIGNAL_BUS_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, SIGNAL_BUS_STATE_FILE)
        except Exception as e:
            logger.warning(f"SIGNAL_BUS: Echec sauvegarde: {e}")


# --- Singleton global ---
signal_bus = SignalBus()
