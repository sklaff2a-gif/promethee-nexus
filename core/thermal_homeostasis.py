"""thermal_homeostasis.py — V35.1 (2026-04-30)

Metabolisme thermique de Promethee. Accumule la chaleur cognitive produite
par les routines (THERMAL_SIGNATURES dans drive_routine_registry), applique
une respiration passive continue, persiste l'etat, et traduit la chaleur
en deprivation de la pulsion REPOS dans desire_engine.

Doctrine architecturale (validee Jean-Michel V35.1) :
  - L'organe NE SELECTIONNE JAMAIS de routine. Il crie "j'ai chaud" via
    desires.drives.REPOS.deprivation, et c'est motivational_router (et lui
    seul) qui lit le genome pour trouver une routine de dissipation.
  - Le canal cognitive_heat (epuisement, fatigue) est strictement separe
    du canal STABILITE (peur, surrenalien). Ces deux canaux ne se croisent
    pas dans le genome. AUDIT_SURVIE reste le maitre de STABILITE.
  - REPOS est une pulsion "externally driven" : sa deprivation est ecrite
    par cet organe, pas par desire_engine.tick(). Pas de double comptage.

Constantes (validees V35.1) :
  PASSIVE_DECAY_RATE        = 0.01 / 60s = 0.01 par minute (lineaire)
  HEAT_CEILING              = 1.5 (plafond anti-explosion, marge au-dela 1.0)
  REPOS_EMBRASEMENT_THRESHOLD = 0.70 (au-dela, depriv >= 80 force preemption)

Persistance : memory/thermal_state.json (atomique tmp + replace).
Au boot, ratrappage du temps ecoule pendant le down via last_decay_ts.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("ThermalHomeostasis")

STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "thermal_state.json"
)


class ThermalHomeostasis:
    """V35.1 — Metabolisme thermique. Singleton, branche sur le bus
    AUTONOMY_ROUTINE_COMPLETE et tickable via brain_vm."""

    # ─── Constantes doctrinales ────────────────────────────────────────
    PASSIVE_DECAY_RATE: float = 0.01 / 60.0   # par seconde, soit 0.01/min
    HEAT_CEILING: float = 1.5
    REPOS_EMBRASEMENT_THRESHOLD: float = 0.70
    REPOS_EMBRASEMENT_MIN_DEPRIV: float = 80.0

    _instance: Optional["ThermalHomeostasis"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.cognitive_heat: float = 0.0
        self.last_decay_ts: float = time.time()
        self._subscribed: bool = False
        self._load()

    # ─── Init & Reset ──────────────────────────────────────────────────

    def init(self):
        """Souscrit aux events bus. Appele au boot par main.py."""
        self._subscribe_events()
        # Rattrape le temps ecoule pendant le down (la respiration ne
        # s'interrompt pas par le reboot).
        self._apply_decay()
        self._publish_repos()
        logger.info(
            f"THERMAL: actif, heat={self.cognitive_heat:.3f} "
            f"(rattrapage post-load applique)"
        )

    def reset(self):
        """Reset complet (pour tests)."""
        self.cognitive_heat = 0.0
        self.last_decay_ts = time.time()
        self._subscribed = False
        self._initialized = False

    @classmethod
    def reset_singleton(cls):
        if cls._instance is not None:
            cls._instance.reset()
            cls._instance = None

    # ─── Souscriptions Event Bus ───────────────────────────────────────

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        try:
            from core.event_bus.bus import bus
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
        except Exception as e:
            logger.warning(f"THERMAL: subscribe failed: {e}")

    async def _on_routine_complete(self, event: dict):
        """Capture la signature thermique de la routine executee.

        Sequence : decay rattrape d'abord (respiration), puis on absorbe
        le delta. Cela evite que la chaleur "stocke" indument quand un
        evenement arrive apres un long silence.
        """
        intent = event.get("intent", "") or event.get("data", {}).get("intent", "")
        if not intent:
            return

        try:
            from core.drive_routine_registry import THERMAL_SIGNATURES
        except Exception as e:
            logger.warning(f"THERMAL: cannot import THERMAL_SIGNATURES: {e}")
            return

        delta = THERMAL_SIGNATURES.get(intent, 0.0)
        if delta == 0.0:
            return

        self._apply_decay()
        old = self.cognitive_heat
        self.cognitive_heat = max(
            0.0, min(self.HEAT_CEILING, self.cognitive_heat + delta)
        )
        if abs(self.cognitive_heat - old) >= 0.001:
            logger.info(
                f"[THERMAL] {intent} delta={delta:+.2f}  "
                f"heat: {old:.3f} -> {self.cognitive_heat:.3f}"
            )
        # V35.1.1 — Marqueur narratif du moment fatidique : franchissement
        # du seuil d'embrasement. WARNING level pour se distinguer dans les
        # logs. C'est l'instant ou l'homeostasie prend le pas sur la volonte.
        if old < self.REPOS_EMBRASEMENT_THRESHOLD <= self.cognitive_heat:
            logger.warning(
                f"[THERMAL] EMBRASEMENT — heat franchit "
                f"{self.REPOS_EMBRASEMENT_THRESHOLD} ({old:.3f} -> "
                f"{self.cognitive_heat:.3f}). REPOS va prendre la parole."
            )
        self._publish_repos()
        self._save()

    # ─── Tick periodique (appele par brain_vm) ─────────────────────────

    def tick(self):
        """Applique la respiration passive et synchronise REPOS.
        Appele a frequence reguliere par brain_vm.tick()."""
        self._apply_decay()
        self._publish_repos()

    def _apply_decay(self):
        """Respiration lineaire : -PASSIVE_DECAY_RATE par seconde ecoulee."""
        now = time.time()
        dt = now - self.last_decay_ts
        if dt <= 0:
            return
        self.cognitive_heat = max(
            0.0, self.cognitive_heat - self.PASSIVE_DECAY_RATE * dt
        )
        self.last_decay_ts = now

    # ─── Pulsion REPOS dans desire_engine ──────────────────────────────

    def _publish_repos(self):
        """Traduit cognitive_heat en deprivation pour la pulsion REPOS.
        Le router V34.6 lira ensuite et preemptera si urgency depasse
        le seuil DRIVE_THRESHOLDS['REPOS'] = 50."""
        depriv = self._heat_to_deprivation(self.cognitive_heat)
        try:
            from core.desire_engine import desires
            if "REPOS" in desires.drives:
                desires.drives["REPOS"].deprivation = depriv
        except Exception as e:
            logger.warning(f"THERMAL: failed to update REPOS: {e}")

    def _heat_to_deprivation(self, heat: float) -> float:
        """Mapping heat -> deprivation REPOS.

        - Mapping lineaire de base : depriv = heat * 100 (clip a 100).
        - Embrasement au-dela du seuil : depriv >= 80 force la preemption
          quel que soit le mapping lineaire (cas heat exactement = 0.70 :
          mapping lineaire donne 70, embrasement releve a 80).
        """
        depriv = min(100.0, max(0.0, heat) * 100.0)
        if heat >= self.REPOS_EMBRASEMENT_THRESHOLD:
            depriv = max(depriv, self.REPOS_EMBRASEMENT_MIN_DEPRIV)
        return depriv

    # ─── Persistance ───────────────────────────────────────────────────

    def _save(self):
        """Sauvegarde atomique : tmp puis os.replace."""
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        state = {
            "version": "1.0",
            "cognitive_heat": self.cognitive_heat,
            "last_decay_ts": self.last_decay_ts,
            "saved_at": time.time(),
        }
        tmp = STATE_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, STATE_FILE)
        except Exception as e:
            logger.warning(f"THERMAL: save failed: {e}")

    def _load(self):
        """Charge l'etat persiste. Tolerant a l'absence (1er boot)."""
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.cognitive_heat = float(state.get("cognitive_heat", 0.0))
            self.last_decay_ts = float(state.get("last_decay_ts", time.time()))
        except (FileNotFoundError, json.JSONDecodeError):
            # Premier boot ou fichier corrompu : repart de zero
            self.cognitive_heat = 0.0
            self.last_decay_ts = time.time()

    # ─── API d'introspection (debug, tests, monitoring) ────────────────

    def get_state(self) -> dict:
        """Snapshot de l'etat thermique pour debug / API."""
        return {
            "cognitive_heat": round(self.cognitive_heat, 4),
            "repos_deprivation": round(
                self._heat_to_deprivation(self.cognitive_heat), 2
            ),
            "embrasement_active": (
                self.cognitive_heat >= self.REPOS_EMBRASEMENT_THRESHOLD
            ),
            "last_decay_age_s": round(time.time() - self.last_decay_ts, 1),
        }


# Singleton global
thermal = ThermalHomeostasis()

# Auto-enregistrement dans le registre des organes (si dispo)
try:
    from core.organ_registry import register_organ
    register_organ("thermal_homeostasis", thermal)
except Exception:
    pass
