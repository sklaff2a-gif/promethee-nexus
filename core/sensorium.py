"""
SENSORIUM — Perception corporelle du hardware.

Transforme les metriques hardware brutes (CPU, RAM, GPU temp, VRAM, frequence)
en 5 sensations continues [0.0, 1.0] via normalisation sigmoide + lissage EMA.

Sprint 1 : squelette sensoriel (collecter, normaliser, exposer).
Bus event SENSORIUM_RECOVERY quand le confort remonte apres une periode de stress.

0 LLM, pur deterministe.
"""
import asyncio
import json
import logging
import math
import os
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, Optional

from core.event_bus.bus import bus

logger = logging.getLogger(__name__)

# --- Persistance ---
SENSORIUM_STATE_FILE = "memory/sensorium_state.json"

# --- Sampling ---
SAMPLE_INTERVAL = 2.0  # Secondes entre chaque tick

# --- Calibration ---
CALIBRATION_STARTUP_TICKS = 15  # 30s a 2s/tick
CALIBRATION_BUFFER_MAX = 1800   # 1h a 2s/tick
CALIBRATION_MIN_SAMPLES = 60    # 2 min minimum avant ajustement

# --- EMA alphas par sens ---
EMA_ALPHAS = {
    "thermoception": 0.15,  # Lent : temperature change graduellement
    "effort": 0.4,          # Rapide : CPU load est dynamique
    "oppression": 0.3,      # Equilibre
    "suffocation": 0.3,     # Equilibre
    "vitality": 0.3,        # Equilibre
}

# --- Parametres sigmoide : (midpoint, k, inverted) ---
# inverted=True signifie que haute valeur brute = bas sens normalise
SIGMOID_PARAMS = {
    "thermoception": (78.0, 0.3, False),   # 78 C = milieu, montee forte
    "effort":        (70.0, 0.08, False),  # 70% CPU = milieu
    "oppression":    (75.0, 0.1, False),   # 75% RAM = milieu
    "suffocation":   (80.0, 0.12, False),  # 80% VRAM = milieu
    "vitality":      (4000, 0.003, True),  # 4 GHz = milieu, INVERSE
}

# --- Poids pour l'indice de confort ---
COMFORT_WEIGHTS = {
    "thermoception": 0.30,
    "effort": 0.15,
    "oppression": 0.20,
    "suffocation": 0.25,
    "vitality": 0.10,
}

# Noms des 5 sens
SENSE_NAMES = list(SIGMOID_PARAMS.keys())

# Sauvegarde toutes les N ticks (~10 min)
SAVE_INTERVAL_TICKS = 300

# Recovery detection — signal parasympathique quand le confort remonte
RECOVERY_COMFORT_THRESHOLD = 0.55  # Confort au-dessus = OK
STRESS_COMFORT_THRESHOLD = 0.35    # Confort en-dessous = stressé
RECOVERY_COOLDOWN_SECONDS = 120    # Max 1 recovery event toutes les 2 min


class Sensorium:
    """Singleton — Perception corporelle du hardware de Promethee."""

    _instance: Optional["Sensorium"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Sens normalises + lisses [0.0, 1.0]
        self.senses: Dict[str, float] = {s: 0.5 for s in SENSE_NAMES}

        # Dernieres valeurs brutes
        self._raw_last: Dict[str, float] = {}

        # Boucle
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._tick_count = 0
        self._total_samples = 0

        # Calibration : deque par sens pour fenetre glissante
        self._calibration_buffers: Dict[str, deque] = {
            s: deque(maxlen=CALIBRATION_BUFFER_MAX) for s in SENSE_NAMES
        }
        self._calibration_ready = False
        self._active_sigmoid_params: Dict[str, tuple] = dict(SIGMOID_PARAMS)

        # Recovery tracking
        self._was_stressed = False
        self._last_recovery_time = 0.0

        # Detection backend GPU
        self._gpu_backend = "heuristic"
        self._gpu_name = ""
        self._detect_gpu_backend()

        # Charger etat persistant
        self._load()

    def _detect_gpu_backend(self):
        """Detecte si GPUtil est disponible et quel GPU est present."""
        try:
            import GPUtil
            gpus = GPUtil.getGPUs()
            if gpus:
                self._gpu_backend = "gputil"
                self._gpu_name = gpus[0].name
            else:
                self._gpu_backend = "heuristic"
                self._gpu_name = ""
        except Exception:
            self._gpu_backend = "heuristic"
            self._gpu_name = ""

    # ------------------------------------------------------------------ init
    def init(self):
        """Initialisation explicite appelee depuis main.py."""
        logger.info(
            f"[SENSORIUM] Module initialise. Backend GPU: {self._gpu_backend}"
            f"{f' ({self._gpu_name})' if self._gpu_name else ''}"
            f", {self._tick_count} ticks precedents."
        )

    async def start_sampling(self):
        """Demarre la boucle de sampling async."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._sampling_loop())
        logger.info("[SENSORIUM] Boucle de sampling demarree.")

    def stop(self):
        """Arrete la boucle de sampling."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[SENSORIUM] Boucle de sampling arretee.")

    # --------------------------------------------------------------- boucle
    async def _sampling_loop(self):
        """Boucle principale : sample → calibrate → normalize → smooth."""
        while self._running:
            try:
                loop = asyncio.get_event_loop()
                raw = await loop.run_in_executor(None, self._sample_raw)
                self._raw_last = raw
                self._update_calibration(raw)
                normalized = self._normalize(raw)
                self._smooth(normalized)
                self._tick_count += 1
                self._total_samples += 1

                # Detection recovery : stress → confort
                await self._check_recovery()

                if self._tick_count % SAVE_INTERVAL_TICKS == 0:
                    self._save()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[SENSORIUM] Erreur sampling: {e}")

            await asyncio.sleep(SAMPLE_INTERVAL)

    # --------------------------------------------------------- collecte brute
    def _sample_raw(self) -> Dict[str, float]:
        """Collecte brute des metriques hardware. Execute dans un thread."""
        raw: Dict[str, float] = {}

        # CPU + RAM + Frequence via psutil
        try:
            import psutil
            raw["effort"] = psutil.cpu_percent(interval=0)
            mem = psutil.virtual_memory()
            raw["oppression"] = mem.percent
            freq = psutil.cpu_freq()
            raw["vitality"] = freq.current if freq else 3500.0
        except Exception:
            raw.setdefault("effort", 0.0)
            raw.setdefault("oppression", 0.0)
            raw.setdefault("vitality", 3500.0)

        # GPU via GPUtil (deja dans le projet, Agents/infra_agent.py)
        if self._gpu_backend == "gputil":
            try:
                import GPUtil
                gpus = GPUtil.getGPUs()
                if gpus:
                    gpu = gpus[0]
                    temp = gpu.temperature
                    raw["thermoception"] = temp if temp is not None else 50.0
                    mem_util = gpu.memoryUtil
                    raw["suffocation"] = (mem_util * 100) if mem_util is not None else 0.0
                else:
                    raw["thermoception"] = self._estimate_temp(raw.get("effort", 0.0))
                    raw["suffocation"] = 0.0
            except Exception:
                raw["thermoception"] = self._estimate_temp(raw.get("effort", 0.0))
                raw.setdefault("suffocation", 0.0)
        else:
            raw["thermoception"] = self._estimate_temp(raw.get("effort", 0.0))
            raw.setdefault("suffocation", 0.0)

        return raw

    @staticmethod
    def _estimate_temp(cpu_percent: float) -> float:
        """Heuristique temperature : 45 C idle, ~90 C full load."""
        return 45.0 + cpu_percent * 0.45

    # --------------------------------------------------------- normalisation
    def _normalize(self, raw: Dict[str, float]) -> Dict[str, float]:
        """Normalisation sigmoide [0.0, 1.0] par sens."""
        result: Dict[str, float] = {}
        for sense, value in raw.items():
            params = self._active_sigmoid_params.get(sense, SIGMOID_PARAMS.get(sense))
            if not params:
                continue
            midpoint, k, inverted = params
            try:
                sig = 1.0 / (1.0 + math.exp(-k * (value - midpoint)))
            except OverflowError:
                sig = 0.0 if (value < midpoint) else 1.0
            result[sense] = (1.0 - sig) if inverted else sig
        return result

    # -------------------------------------------------------------- lissage
    def _smooth(self, normalized: Dict[str, float]):
        """Lissage EMA. Met a jour self.senses."""
        for sense, value in normalized.items():
            alpha = EMA_ALPHAS.get(sense, 0.3)
            prev = self.senses.get(sense, 0.5)
            self.senses[sense] = prev + alpha * (value - prev)

    # --------------------------------------------------------- calibration
    def _update_calibration(self, raw: Dict[str, float]):
        """Accumule les valeurs brutes et ajuste les midpoints sigmoide."""
        for sense, value in raw.items():
            if sense in self._calibration_buffers:
                self._calibration_buffers[sense].append(value)

        # Phase startup : attendre assez de ticks
        if self._tick_count < CALIBRATION_STARTUP_TICKS:
            return

        self._calibration_ready = True

        for sense, buf in self._calibration_buffers.items():
            if len(buf) < CALIBRATION_MIN_SAMPLES:
                continue
            sorted_vals = sorted(buf)
            idx_10 = max(0, int(len(sorted_vals) * 0.1))
            idx_90 = min(len(sorted_vals) - 1, int(len(sorted_vals) * 0.9))
            p10 = sorted_vals[idx_10]
            p90 = sorted_vals[idx_90]
            new_mid = (p10 + p90) / 2.0

            base = SIGMOID_PARAMS.get(sense)
            if base:
                self._active_sigmoid_params[sense] = (new_mid, base[1], base[2])

    # ------------------------------------------------- methodes publiques
    async def _check_recovery(self):
        """Detecte la transition stress → confort et publie SENSORIUM_RECOVERY."""
        comfort = self.get_comfort_index()
        if comfort < STRESS_COMFORT_THRESHOLD:
            self._was_stressed = True
        elif self._was_stressed and comfort >= RECOVERY_COMFORT_THRESHOLD:
            now = time.time()
            if now - self._last_recovery_time >= RECOVERY_COOLDOWN_SECONDS:
                self._was_stressed = False
                self._last_recovery_time = now
                try:
                    await bus.publish("SENSORIUM_RECOVERY", {
                        "comfort_index": round(comfort, 3),
                        "senses": self.get_senses(),
                    })
                    logger.info(f"[SENSORIUM] Recovery detectee (comfort={comfort:.2f})")
                except Exception:
                    pass

    def get_senses(self) -> Dict[str, float]:
        """Retourne les 5 sens normalises et lisses [0.0, 1.0]."""
        return dict(self.senses)

    def get_raw(self) -> Dict[str, float]:
        """Retourne les dernieres valeurs brutes."""
        return dict(self._raw_last)

    def get_comfort_index(self) -> float:
        """Indice de confort global [0.0, 1.0]. 1.0 = machine detendue."""
        # Les 4 sens "negatifs" (haute valeur = inconfort)
        discomfort = 0.0
        for sense in ["thermoception", "effort", "oppression", "suffocation"]:
            discomfort += self.senses.get(sense, 0.5) * COMFORT_WEIGHTS.get(sense, 0.0)

        # Vitality est "positif" (haute valeur = bien, car sigmoide inversee)
        # Note: apres sigmoide inversee, haute valeur brute → bas sens
        # donc haute vitality sens = basse frequence = mauvais
        # En fait vitality.inverted=True : haute freq → bas sens
        # Donc on traite vitality comme les autres (haute = inconfort)
        discomfort += self.senses.get("vitality", 0.5) * COMFORT_WEIGHTS.get("vitality", 0.0)

        return max(0.0, min(1.0, 1.0 - discomfort))

    # --- Routines considérées comme lourdes (LLM, GPU, multi-agents) ---
    _HEAVY_INTENTS = {
        "EXPANSION_CODE", "COUNCIL_DEBATE", "VEILLE_SILENCIEUSE",
        "REFACTOR_RANDOM", "SECURITY_AUDIT", "ROADMAP_RESEARCH",
        "ROADMAP_SPEC", "SOLILOQUE_INTERNE", "GRIMOIRE_INVOKE",
    }
    # Routines légères (0-LLM, pas de GPU)
    _LIGHT_INTENTS = {
        "AUDIT_STRUCTURE", "MEMORY_CLEANUP", "NEURAL_COMPILE",
        "MEMORY_CONSOLIDATION", "DROPZONE_SCAN",
    }

    def compute_sensorium_bonus(self, intent: str) -> float:
        """Bonus/malus de scoring basé sur l'état corporel hardware.

        Quand la machine est stressée (comfort < 0.4) :
        - Routines lourdes reçoivent un malus (jusqu'à -2.0)
        - Routines légères reçoivent un bonus (jusqu'à +1.0)
        Quand la machine est détendue (comfort > 0.7) : aucun effet.
        """
        comfort = self.get_comfort_index()
        # Zone neutre : pas d'influence sensorielle
        if comfort >= 0.7:
            return 0.0
        # Zone intermédiaire (0.4-0.7) : effet léger
        if comfort >= 0.4:
            stress_factor = (0.7 - comfort) / 0.3  # 0.0 à 1.0
            if intent in self._HEAVY_INTENTS:
                return round(-0.5 * stress_factor, 3)
            return 0.0
        # Zone de stress (< 0.4) : effet fort
        stress_factor = (0.4 - comfort) / 0.4  # 0.0 à 1.0
        if intent in self._HEAVY_INTENTS:
            return round(-0.5 - 1.5 * stress_factor, 3)  # -0.5 à -2.0
        if intent in self._LIGHT_INTENTS:
            return round(1.0 * stress_factor, 3)  # 0.0 à +1.0
        return 0.0

    def get_sensorium_context(self) -> str:
        """Contexte corporel pour injection dans le purpose_context."""
        comfort = self.get_comfort_index()
        if comfort >= 0.7:
            return ""  # Machine détendue, rien à signaler
        senses = self.get_senses()
        parts = []
        # Identifier les sens les plus sollicités
        if senses.get("thermoception", 0) > 0.7:
            parts.append(f"GPU chaud ({senses['thermoception']:.0%})")
        if senses.get("effort", 0) > 0.7:
            parts.append(f"CPU chargé ({senses['effort']:.0%})")
        if senses.get("oppression", 0) > 0.7:
            parts.append(f"RAM saturée ({senses['oppression']:.0%})")
        if senses.get("suffocation", 0) > 0.7:
            parts.append(f"VRAM tendue ({senses['suffocation']:.0%})")
        if not parts:
            return ""
        label = "ALERTE CORPORELLE" if comfort < 0.4 else "TENSION CORPORELLE"
        return f"[{label}] Confort={comfort:.0%}. {', '.join(parts)}. Privilégier les routines légères."

    def get_stats(self) -> Dict[str, Any]:
        """Stats completes pour l'API et le snapshot."""
        calibration_info = {}
        for sense in SENSE_NAMES:
            params = self._active_sigmoid_params.get(sense, SIGMOID_PARAMS.get(sense))
            buf = self._calibration_buffers.get(sense, deque())
            calibration_info[sense] = {
                "midpoint": params[0] if params else 0,
                "samples": len(buf),
                "calibrated": len(buf) >= CALIBRATION_MIN_SAMPLES and self._calibration_ready,
            }

        return {
            "senses": self.get_senses(),
            "raw": self.get_raw(),
            "comfort_index": round(self.get_comfort_index(), 3),
            "calibration": calibration_info,
            "gpu_backend": self._gpu_backend,
            "gpu_name": self._gpu_name,
            "tick_count": self._tick_count,
            "total_samples": self._total_samples,
            "calibration_ready": self._calibration_ready,
            "running": self._running,
        }

    # --------------------------------------------------------- persistance
    def _save(self):
        """Sauvegarde atomique de l'etat."""
        try:
            calibration_data = {}
            for sense in SENSE_NAMES:
                params = self._active_sigmoid_params.get(sense, SIGMOID_PARAMS.get(sense))
                buf = self._calibration_buffers.get(sense, deque())
                # Sauvegarder les 200 derniers points pour recalibration rapide
                calibration_data[sense] = {
                    "midpoint": params[0] if params else 0,
                    "samples": list(buf)[-200:] if len(buf) > 200 else list(buf),
                }

            state = {
                "senses": dict(self.senses),
                "raw_last": dict(self._raw_last),
                "calibration": calibration_data,
                "tick_count": self._tick_count,
                "total_samples": self._total_samples,
                "gpu_backend": self._gpu_backend,
                "last_save": datetime.now().isoformat(),
            }

            tmp = SENSORIUM_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, SENSORIUM_STATE_FILE)
        except Exception as e:
            logger.warning(f"[SENSORIUM] Erreur sauvegarde: {e}")

    def _load(self):
        """Charge l'etat depuis le fichier persistant."""
        try:
            if not os.path.exists(SENSORIUM_STATE_FILE):
                return
            with open(SENSORIUM_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)

            # Restaurer sens
            saved_senses = state.get("senses", {})
            for s in SENSE_NAMES:
                if s in saved_senses:
                    self.senses[s] = float(saved_senses[s])

            self._raw_last = state.get("raw_last", {})
            self._tick_count = state.get("tick_count", 0)
            self._total_samples = state.get("total_samples", 0)

            # Restaurer calibration
            cal = state.get("calibration", {})
            for sense in SENSE_NAMES:
                if sense in cal:
                    entry = cal[sense]
                    midpoint = entry.get("midpoint", SIGMOID_PARAMS[sense][0])
                    samples = entry.get("samples", [])
                    self._active_sigmoid_params[sense] = (
                        midpoint,
                        SIGMOID_PARAMS[sense][1],
                        SIGMOID_PARAMS[sense][2],
                    )
                    self._calibration_buffers[sense] = deque(samples, maxlen=CALIBRATION_BUFFER_MAX)
                    if len(samples) >= CALIBRATION_MIN_SAMPLES:
                        self._calibration_ready = True

            logger.info(
                f"[SENSORIUM] Etat restaure: {self._tick_count} ticks, "
                f"calibration {'prete' if self._calibration_ready else 'en cours'}."
            )
        except Exception as e:
            logger.warning(f"[SENSORIUM] Erreur chargement: {e}")

    # --------------------------------------------------------- reset tests
    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        cls._instance = None


# --- Singleton global ---
sensorium = Sensorium()
