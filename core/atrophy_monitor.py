"""
ATROPHY_MONITOR — Détecteur d'atrophie cognitive.

Né de l'atelier audace 27/05/2026 (boucle pilote/ingénieur E1-E7).
Symptôme observé : audace plate à 42.7 sur les 15 agents, écrasée par
psyche._on_sensorium_update qui colle un EMA cible 30-50 basée sur le stress.
Diagnostic du pilote : l'audace est verrouillée a priori par la saturation
de STABILITE+SURVIE à 100% — "stabilité 100% = mort thermique" (R3).

Spec validée par le pilote (R7, Option 2) :
1. Tick : si desire_engine.STABILITE est repue (privation basse) ET
   desire_engine.CROISSANCE est affamée (privation haute) → publie
   ATROPHY_ALARM sur le bus.
2. psyche._on_sensorium_update lit l'état atrophy → si actif, force la
   cible EMA de l'audace vers ATROPHY_AUDACE_BOOST_TARGET au lieu de
   la cible basse stress-dépendante.
3. Coupe-circuit Option 2 ("Détecteur de Bruit", validé par le pilote
   contre l'Option 1 chronométrique) : surveille les nouveaux nodes
   synaptiques pendant le boost. Si Jaccard(window_A, window_B) >
   REDUNDANT_THRESHOLD → diagnostic rumination → publie ATROPHY_CANCEL.
4. TTL absolu en garde-fou (ATROPHY_ALARM_DURATION_S).

Mode OBSERVE 48h : Config.ATROPHY_DRY_RUN = True publie rien sur le
bus, logue seulement dans logs/atrophy_monitor.jsonl. Armement après
télémétrie.
"""

import asyncio
import json
import logging
import os
import time
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
    """Singleton. Surveille la balance STABILITE/CROISSANCE et déclenche
    l'alarme d'atrophie si la stabilité étouffe la croissance."""

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
        # Schmitt trigger pour detection de stase soutenue (recalibrage 29/05).
        # 0.0 = pas en stase. Sinon = timestamp d'entree en stase. L'alarme n'est
        # publiee que si time.time() - _stasis_started_at >= STASIS_DURATION_S.
        self._stasis_started_at: float = 0.0
        # Fenêtre glissante de nouveaux nodes (pour Jaccard rumination)
        self._win_old: List[str] = []   # plus ancienne moitié
        self._win_new: List[str] = []   # plus récente moitié
        self._subscribed: bool = False
        self._stats = {
            "checks": 0,
            "stasis_detections": 0,  # nb d'entrees en stase (seuil franchi)
            "stasis_breaks": 0,      # nb de ruptures avant atteindre la duree
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
            self._subscribed = True
            logger.info("[ATROPHY] Monitor souscrit a SYNAPTIC_UPDATE.")
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

        Recalibrage 29/05 — Schmitt trigger sur les privations + timer de stase :
        - Entrée en stase : STAB > 80 ET CROIS < 10 (seuils stricts)
        - Sortie de stase : STAB < 75 OU CROIS > 15 (hystérésis, zone morte 5pts)
        - Alarme publiée si stase soutenue >= STASIS_DURATION_S (45 min)
        """
        try:
            from config import Config
        except Exception:
            return
        if not getattr(Config, "ATROPHY_ENABLED", False):
            return

        self._stats["checks"] += 1

        try:
            from core.desire_engine import desire_engine
        except Exception as e:
            logger.debug(f"[ATROPHY] desire_engine import: {e}")
            return

        stab = desire_engine.drives.get("STABILITE")
        crois = desire_engine.drives.get("CROISSANCE")
        if not stab or not crois:
            return

        stab_dep = stab.deprivation
        crois_dep = crois.deprivation

        # Si une alarme est active, on enchaine sur le coupe-circuit (Jaccard/TTL)
        if self._alarm_active:
            await self._check_burst_quality()
            return

        # Sinon, Schmitt trigger : evaluer les conditions de stase
        if self._stasis_started_at == 0.0:
            # Pas en stase : verifier l'ENTREE (seuils stricts)
            entry_stab = getattr(Config, "ATROPHY_STABILITE_HEGEMONIC_ENTRY_THRESHOLD", 80.0)
            entry_crois = getattr(Config, "ATROPHY_CROISSANCE_MECHANICAL_ENTRY_THRESHOLD", 10.0)
            if stab_dep > entry_stab and crois_dep < entry_crois:
                # Entree en stase confirmee
                self._stasis_started_at = time.time()
                self._stats["stasis_detections"] += 1
                self._log_event("stasis_detected", {
                    "stab_deprivation": round(stab_dep, 2),
                    "crois_deprivation": round(crois_dep, 2),
                    "entry_thresholds": [entry_stab, entry_crois],
                })
            return

        # Deja en stase : verifier la SORTIE (seuils permissifs = hysteresis)
        exit_stab = getattr(Config, "ATROPHY_STABILITE_HEGEMONIC_EXIT_THRESHOLD", 75.0)
        exit_crois = getattr(Config, "ATROPHY_CROISSANCE_MECHANICAL_EXIT_THRESHOLD", 15.0)
        if stab_dep < exit_stab or crois_dep > exit_crois:
            # Rupture metabolique reelle (sortie de zone morte)
            duration = time.time() - self._stasis_started_at
            self._stats["stasis_breaks"] += 1
            self._log_event("stasis_broken", {
                "duration_s": round(duration, 1),
                "stab_deprivation": round(stab_dep, 2),
                "crois_deprivation": round(crois_dep, 2),
                "exit_thresholds": [exit_stab, exit_crois],
            })
            self._stasis_started_at = 0.0
            return

        # Toujours en stase dans la zone morte : verifier si duree atteinte
        stasis_duration = getattr(Config, "ATROPHY_STASIS_DURATION_S", 2700)
        elapsed = time.time() - self._stasis_started_at
        if elapsed >= stasis_duration:
            # Stase soutenue confirmee -> publier alarme
            await self._publish_alarm(stab_dep, crois_dep)

    # ---- Mécanique interne ----

    async def _publish_alarm(self, stab_dep: float, crois_dep: float):
        from config import Config
        dry_run = getattr(Config, "ATROPHY_DRY_RUN", True)
        duration = getattr(Config, "ATROPHY_ALARM_DURATION_S", 600)

        self._alarm_active = True
        self._alarm_started_at = time.time()
        # Reset timer de stase : apres expiration du TTL alarme, il faudra
        # 45 min de nouvelle stase soutenue pour redeclencher.
        self._stasis_started_at = 0.0
        self._win_old.clear()
        self._win_new.clear()
        self._stats["alarms_published"] += 1
        if dry_run:
            self._stats["would_boost_dry_run"] += 1

        self._log_event("alarm_published", {
            "stab_deprivation": round(stab_dep, 2),
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
                    "stab_deprivation": stab_dep,
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
        return {**self._stats, "alarm_active": self._alarm_active}


# Singleton accessible
atrophy_monitor = AtrophyMonitor()
