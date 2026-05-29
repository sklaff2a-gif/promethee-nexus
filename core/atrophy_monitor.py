"""
ATROPHY_MONITOR — Détecteur d'atrophie cognitive.

Né de l'atelier audace 27/05/2026 (boucle pilote/ingénieur E1-E7).
Symptôme observé : audace plate à 42.7 sur les 15 agents, écrasée par
psyche._on_sensorium_update qui colle un EMA cible 30-50 basée sur le stress.

REFONTE 29/05 v3 (apres echec v1 inversion + v2 Schmitt+timer) :
Le pilote l'avait dit en R3 atelier : "paresse algorithmique" stereotypee.
Audit empirique a revele le mecanisme exact : Reward Hacking. Quand
STABILITE.priv > 80, le motivational router declenche un AUDIT_SURVIE
qui satisfait symboliquement la pulsion via V34.7 RELIEF (-12 pts).
STABILITE oscille 89 -> 77 -> 89 toutes les ~13 min en cycle stereotype.

La vraie signature de l'atrophie n'est pas une valeur instantanee, c'est
la FREQUENCE du rituel V34. Source : motivational_router.mark_drive_satisfied
publie V34_RELIEF_APPLIED sur le bus (1 event par cycle reussi).
Atrophy_monitor maintient un deque des timestamps des reliefs STABILITE
sur fenetre 24h. Si > 4 reliefs ET CROISSANCE.priv < 10 -> alarme.

Garde-fou constitutionnel : la condition CROISSANCE < 10 garantit que
les vraies urgences (instabilite legitime declenchant des V34) ne
declenchent PAS l'alarme — l'alarme ne tape QUE sur la compulsion
stereotypee couplee a un coma de croissance.

Spec validée par le pilote (R7, Option 2 — coupe-circuit Jaccard) :
1. Pendant l'alarme, psyche._on_sensorium_update lit is_alarm_active()
   et force la cible EMA audace vers ATROPHY_AUDACE_BOOST_TARGET.
2. Coupe-circuit Jaccard : surveille les nouveaux nodes synaptiques
   pendant le boost. Si Jaccard(window_A, window_B) > REDUNDANT_THRESHOLD
   -> diagnostic rumination -> publie ATROPHY_CANCEL.
3. TTL absolu en garde-fou (ATROPHY_ALARM_DURATION_S = 10 min).

Mode OBSERVE : Config.ATROPHY_DRY_RUN = True publie rien sur le bus,
logue seulement dans logs/atrophy_monitor.jsonl. Armement après télémétrie.
"""

import asyncio
import json
import logging
import os
import time
from collections import deque
from typing import List, Optional

logger = logging.getLogger("atrophy_monitor")

ATROPHY_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "atrophy_monitor.jsonl",
)


def _jaccard(set_a: set, set_b: set) -> float:
    """Indice de Jaccard sur 2 ensembles. 0 si vides."""
    if not set_a and not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union > 0 else 0.0


class AtrophyMonitor:
    """Singleton. Compte les V34_RELIEF_APPLIED STABILITE sur fenetre roulante
    24h. Declenche ATROPHY_ALARM si compulsion + CROISSANCE en coma."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._alarm_active: bool = False
        self._alarm_started_at: float = 0.0
        # Fenetre roulante des V34_RELIEF_APPLIED STABILITE (timestamps).
        # Refonte 29/05 v3 : remplace l'ancienne logique Schmitt+timer qui
        # ne pouvait jamais se declencher dans le metabolisme nominal de
        # Promethee (V34 reset STAB toutes les 13 min, jamais 45 min consecutives).
        self._v34_relief_history: deque = deque()
        # Fenêtre glissante de nouveaux nodes (pour Jaccard rumination)
        self._win_old: List[str] = []   # plus ancienne moitié
        self._win_new: List[str] = []   # plus récente moitié
        self._subscribed: bool = False
        self._stats = {
            "checks": 0,
            "v34_reliefs_observed": 0,  # nb total reliefs STABILITE captes
            "v34_reliefs_purged": 0,    # nb reliefs sortis de la fenetre 24h
            "alarms_published": 0,
            "alarms_cancelled_rumination": 0,
            "alarms_cancelled_timeout": 0,
            "would_boost_dry_run": 0,
        }

    @classmethod
    def reset_singleton(cls):
        """Tests only."""
        cls._instance = None

    def init(self):
        """Souscrit aux événements bus. Appelé une fois au boot."""
        if self._subscribed:
            return
        try:
            from core.event_bus.bus import bus
            bus.subscribe("SYNAPTIC_UPDATE", self._on_synaptic_update)
            bus.subscribe("V34_RELIEF_APPLIED", self._on_v34_relief)
            self._subscribed = True
            logger.info("[ATROPHY] Monitor souscrit a SYNAPTIC_UPDATE + V34_RELIEF_APPLIED.")
        except Exception as e:
            logger.warning(f"[ATROPHY] Echec subscribe: {e}")

    # ---- API publique ----

    def is_alarm_active(self) -> bool:
        """Lu par psyche._on_sensorium_update pour modifier stress_target."""
        if not self._alarm_active:
            return False
        # Auto-expire si TTL dépassé (garde-fou contre alarme zombie)
        try:
            from config import Config
            duration = getattr(Config, "ATROPHY_ALARM_DURATION_S", 600)
        except Exception:
            duration = 600
        if time.time() - self._alarm_started_at > duration:
            return False
        return True

    async def check_balance(self):
        """Tick. Appelé par hypothalamus.regulate() (ou autre tick périodique).

        Refonte 29/05 v3 — Detection par frequence du rituel V34 :
        - Compte les V34_RELIEF_APPLIED STABILITE sur 24h
        - Si > ATROPHY_V34_RELIEF_THRESHOLD (4) ET CROISSANCE.priv < 10
          (= coma de la pulsion de croissance) -> publish_alarm
        - Garde-fou constitutionnel : une vraie crise (CROISSANCE active)
          ne declenche PAS l'alarme meme avec 10 V34/24h
        """
        try:
            from config import Config
        except Exception:
            return
        if not getattr(Config, "ATROPHY_ENABLED", False):
            return

        self._stats["checks"] += 1

        # Si une alarme est active, on enchaine sur le coupe-circuit (Jaccard/TTL)
        if self._alarm_active:
            await self._check_burst_quality()
            return

        # Purge des reliefs > fenetre roulante (au cas ou check_balance
        # est appele alors qu'aucun nouveau relief n'a tire entre temps)
        self._purge_old_reliefs()

        relief_threshold = getattr(Config, "ATROPHY_V34_RELIEF_THRESHOLD", 4)
        n_reliefs = len(self._v34_relief_history)

        if n_reliefs <= relief_threshold:
            return  # Pas assez de cycles stereotypes

        # Au-dela du seuil de compulsion : check CROISSANCE pour garde-fou
        try:
            from core.desire_engine import desire_engine
        except Exception as e:
            logger.debug(f"[ATROPHY] desire_engine import: {e}")
            return

        crois = desire_engine.drives.get("CROISSANCE")
        if not crois:
            return
        crois_dep = crois.deprivation

        crois_threshold = getattr(Config, "ATROPHY_CROISSANCE_MECHANICAL_ENTRY_THRESHOLD", 10.0)
        if crois_dep >= crois_threshold:
            return  # Garde-fou : CROISSANCE active, ce n'est pas de l'atrophie

        # Compulsion confirmee + coma de croissance -> alarme
        await self._publish_alarm(n_reliefs, crois_dep)

    # ---- Handlers bus ----

    async def _on_v34_relief(self, event: dict):
        """Capte les V34_RELIEF_APPLIED. Empile timestamp si drive == STABILITE."""
        drive = (event.get("drive") or "").upper()
        if drive != "STABILITE":
            return
        self._v34_relief_history.append(time.time())
        self._stats["v34_reliefs_observed"] += 1
        # Purge a chaque insertion pour garder le deque borne
        self._purge_old_reliefs()

    def _purge_old_reliefs(self):
        """Retire les reliefs plus vieux que ATROPHY_V34_RELIEF_WINDOW_S."""
        try:
            from config import Config
            window = getattr(Config, "ATROPHY_V34_RELIEF_WINDOW_S", 86400)
        except Exception:
            window = 86400
        cutoff = time.time() - window
        while self._v34_relief_history and self._v34_relief_history[0] < cutoff:
            self._v34_relief_history.popleft()
            self._stats["v34_reliefs_purged"] += 1

    # ---- Mécanique interne ----

    async def _publish_alarm(self, n_reliefs: int, crois_dep: float):
        from config import Config
        dry_run = getattr(Config, "ATROPHY_DRY_RUN", True)
        duration = getattr(Config, "ATROPHY_ALARM_DURATION_S", 600)
        window = getattr(Config, "ATROPHY_V34_RELIEF_WINDOW_S", 86400)

        self._alarm_active = True
        self._alarm_started_at = time.time()
        self._win_old.clear()
        self._win_new.clear()
        self._stats["alarms_published"] += 1
        if dry_run:
            self._stats["would_boost_dry_run"] += 1

        self._log_event("alarm_published", {
            "n_reliefs_24h": n_reliefs,
            "window_s": window,
            "crois_deprivation": round(crois_dep, 2),
            "duration_s": duration,
            "dry_run": dry_run,
        })

        if not dry_run:
            try:
                from core.event_bus.bus import bus
                await bus.publish("ATROPHY_ALARM", {
                    "target_agent": "_global",
                    "duration_s": duration,
                    "n_reliefs_24h": n_reliefs,
                    "crois_deprivation": crois_dep,
                })
            except Exception as e:
                logger.warning(f"[ATROPHY] Publish ALARM failed: {e}")

    async def _check_burst_quality(self):
        """Coupe-circuit Option 2 — Détecteur de Bruit (Jaccard)."""
        from config import Config
        now = time.time()
        duration = getattr(Config, "ATROPHY_ALARM_DURATION_S", 600)

        # TTL absolu (garde-fou)
        if now - self._alarm_started_at > duration:
            await self._cancel_alarm("timeout")
            return

        # Détecteur de Bruit : Jaccard sur 2 fenêtres glissantes
        win_size = getattr(Config, "ATROPHY_JACCARD_WINDOW", 20)
        if len(self._win_old) >= win_size and len(self._win_new) >= win_size:
            threshold = getattr(Config, "ATROPHY_JACCARD_REDUNDANT_THRESHOLD", 0.7)
            jac = _jaccard(set(self._win_old), set(self._win_new))
            if jac > threshold:
                self._log_event("rumination_detected", {
                    "jaccard": round(jac, 3),
                    "threshold": threshold,
                    "win_old_size": len(self._win_old),
                    "win_new_size": len(self._win_new),
                })
                await self._cancel_alarm("rumination")

    async def _cancel_alarm(self, reason: str):
        if not self._alarm_active:
            return
        duration_actual = time.time() - self._alarm_started_at
        self._alarm_active = False

        if reason == "rumination":
            self._stats["alarms_cancelled_rumination"] += 1
        elif reason == "timeout":
            self._stats["alarms_cancelled_timeout"] += 1

        self._log_event("alarm_cancelled", {
            "reason": reason,
            "duration_actual_s": round(duration_actual, 1),
        })

        try:
            from config import Config
            dry_run = getattr(Config, "ATROPHY_DRY_RUN", True)
        except Exception:
            dry_run = True

        if not dry_run:
            try:
                from core.event_bus.bus import bus
                await bus.publish("ATROPHY_CANCEL", {
                    "reason": reason,
                    "duration_actual_s": round(duration_actual, 1),
                })
            except Exception as e:
                logger.warning(f"[ATROPHY] Publish CANCEL failed: {e}")

        self._win_old.clear()
        self._win_new.clear()
        self._alarm_started_at = 0.0

    async def _on_synaptic_update(self, event: dict):
        """Capte les nouveaux nodes pour la fenêtre Jaccard pendant l'alarme."""
        if not self._alarm_active:
            return
        change = event.get("change")
        if change != "node_new":
            return
        node_id = event.get("id") or event.get("concept") or event.get("node_id")
        if not node_id:
            return

        try:
            from config import Config
            win_size = getattr(Config, "ATROPHY_JACCARD_WINDOW", 20)
        except Exception:
            win_size = 20

        # Glissement : nouveau dans win_new, le plus ancien de win_new vers win_old
        self._win_new.append(node_id)
        if len(self._win_new) > win_size:
            overflow = self._win_new.pop(0)
            self._win_old.append(overflow)
            if len(self._win_old) > win_size:
                self._win_old.pop(0)

    # ---- Journalisation ----

    def _log_event(self, event_type: str, payload: dict):
        try:
            entry = {"ts": time.time(), "event": event_type, **payload}
            os.makedirs(os.path.dirname(ATROPHY_LOG_PATH), exist_ok=True)
            with open(ATROPHY_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[ATROPHY] Log error: {e}")

    def get_stats(self) -> dict:
        """Snapshot statistique pour télémétrie / tests."""
        return {
            **self._stats,
            "alarm_active": self._alarm_active,
            "v34_relief_window_size": len(self._v34_relief_history),
        }


# Singleton accessible
atrophy_monitor = AtrophyMonitor()
