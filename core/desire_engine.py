# core/desire_engine.py — DesireEngine : Les Pulsions Primordiales de Promethee
# 7 pulsions homeostatiques inspirees de Maslow, SDT (Deci & Ryan), Drive (Pink).
# Chaque pulsion a un niveau de deprivation (0-100) qui monte avec le temps
# et descend temporairement quand elle est satisfaite.

import json
import os
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List

from core.event_bus.bus import bus

logger = logging.getLogger("DesireEngine")

# --- Constantes ---

DRIVE_NAMES = ("CURIOSITE", "MAITRISE", "STABILITE", "CONNEXION",
               "CROISSANCE", "CREATION", "COMPREHENSION")

STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "desire_state.json"
)

# Rise naturelle par heure
NATURAL_RISE_PER_HOUR = 3.0

# Tolerance biologique (habituation)
TOLERANCE_HALF_LIFE = 8.0        # Apres 8 satisfactions, effet divise par 2
TOLERANCE_MIN = 0.15             # Plancher : meme sature, 15% d'effet reste
TOLERANCE_RECOVERY_PER_HOUR = 15.0  # L'accumulateur diminue de 15/h (demi-vie ~24h pour accumulation typique)
TOLERANCE_MAX = 200.0            # Plafond de tolerance — au-delà, plus d'accumulation
DEPRIVATION_TOLERANCE_BYPASS = 80.0   # Au-dessus, tolerance ignoree (privation extreme)
DEPRIVATION_CEILING_START = 85.0      # Au-dessus, la montee naturelle ralentit (homeostasie)


@dataclass
class Drive:
    """Une pulsion primordiale avec son etat courant."""
    name: str
    deprivation: float = 40.0     # 0-100, demarre a 40 (leger manque)
    satiation_count: int = 0       # Satisfactions aujourd'hui
    last_satisfied: float = 0.0    # timestamp
    total_satisfied: int = 0       # Compteur historique
    frustration_streak: int = 0    # Echecs consecutifs
    tolerance_accumulator: float = 0.0  # Habituation (monte aux satisfactions, descend au repos)


# --- Resonance PSYCHE-Pulsions ---
# Les traits PSYCHE amplifient certaines pulsions
TRAIT_RESONANCE: Dict[str, Dict[str, float]] = {
    "CURIOSITE":      {"curiosite": 0.4, "creativite": 0.1},
    "MAITRISE":       {"savoir": 0.3, "respect": 0.2},
    "STABILITE":      {"survie": 0.4, "respect": 0.1},
    "CONNEXION":      {"respect": 0.3, "curiosite": 0.1},
    "CROISSANCE":     {"audace": 0.3, "creativite": 0.2},
    "CREATION":       {"creativite": 0.4, "audace": 0.1},
    "COMPREHENSION":  {"savoir": 0.4, "curiosite": 0.2},
}

# --- Matrice evenement -> impact sur les pulsions ---
# Valeurs negatives = satisfaction, positives = frustration
EVENT_IMPACT: Dict[str, Any] = {
    "ROUTINE_SUCCESS": {
        "_default":           {"MAITRISE": -5, "CROISSANCE": -3},
        "EXPANSION_CODE":     {"CREATION": -12, "CROISSANCE": -8, "MAITRISE": -10},
        "VEILLE_SILENCIEUSE": {"CURIOSITE": -15, "COMPREHENSION": -8},
        "COUNCIL_DEBATE":     {"CONNEXION": -12, "COMPREHENSION": -5},
        "AUDIT_STRUCTURE":    {"STABILITE": -10, "COMPREHENSION": -5},
        "SECURITY_AUDIT":     {"STABILITE": -12, "MAITRISE": -5},
        "GRIMOIRE_INVOKE":    {"CROISSANCE": -10, "CURIOSITE": -5},
        "MEMORY_CLEANUP":     {"STABILITE": -8, "COMPREHENSION": -3},
        "DROPZONE_SCAN":      {"CURIOSITE": -10, "CONNEXION": -5},
        "REFACTOR_RANDOM":    {"MAITRISE": -10, "CREATION": -5},
        "MEMORY_CONSOLIDATION": {"COMPREHENSION": -8, "STABILITE": -5},
    },
    "ROUTINE_FAILURE": {
        "_default":           {"MAITRISE": +8, "STABILITE": +5},
    },
    "COUNCIL_CONSENSUS":      {"CONNEXION": -15, "COMPREHENSION": -8},
    "COUNCIL_ABORT":          {"CONNEXION": +10, "MAITRISE": +5},
    "ARTIFACT_CREATED":       {"CREATION": -20, "CROISSANCE": -5},
    "EVOLUTION_DEPLOYED":     {"CROISSANCE": -20, "CREATION": -10, "MAITRISE": -10},
    "CI_SUCCESS":             {"MAITRISE": -8, "STABILITE": -3},
    "CI_FAILURE":             {"MAITRISE": +10, "STABILITE": +8},
    "HEALTH_NO_GO":           {"STABILITE": +15},
    "HEALTH_DEGRADED":        {"STABILITE": +8},
    "KNOWLEDGE_GAP_DETECTED":  {"CURIOSITE": +5, "COMPREHENSION": +8},
    "EUREKA_BRIDGE":          {"CURIOSITE": -10, "CREATION": -5, "COMPREHENSION": -10},
    "SOLILOQUE_COMPLETE":     {"CONNEXION": -18, "COMPREHENSION": -5, "STABILITE": -3},
    "CHAT_RESPONSE":          {"CONNEXION": -12, "COMPREHENSION": -3, "STABILITE": -2},
    "CURIOSITY_SATISFIED":    {"CURIOSITE": -12, "COMPREHENSION": -8},
    # Sensorium hardware (Sprint 4 Sensorium)
    "HARDWARE_CRISIS":        {"STABILITE": +15, "CURIOSITE": -5, "CREATION": -5},
    # Ecole — notes du professeur
    "SCHOOL_GRADE_HIGH":      {"MAITRISE": -12, "CROISSANCE": -5, "COMPREHENSION": -3},
    "SCHOOL_GRADE_LOW":       {"MAITRISE": +8, "CROISSANCE": +5},
}

# --- Seuil de qualité par intent pour considérer un succès ---
ROUTINE_SUCCESS_THRESHOLD: Dict[str, float] = {
    "_default": 0.6,
    "REFACTOR_RANDOM": 0.3,
    "AUDIT_STRUCTURE": 0.3,
    "MEMORY_CLEANUP": 0.3,
    "DROPZONE_SCAN": 0.3,
}

# --- Affinite pulsion -> routine (pour le scoring) ---
DRIVE_ROUTINE_AFFINITY: Dict[str, Dict[str, float]] = {
    "CURIOSITE":      {"VEILLE_SILENCIEUSE": 1.2, "DROPZONE_SCAN": 0.8, "COUNCIL_DEBATE": 0.3, "ROADMAP_RESEARCH": 1.2, "SELF_INSPECT": 1.5},
    "MAITRISE":       {"EXPANSION_CODE": 0.8, "REFACTOR_RANDOM": 1.0, "AUDIT_STRUCTURE": 0.5},
    "STABILITE":      {"SECURITY_AUDIT": 1.2, "AUDIT_STRUCTURE": 1.0, "MEMORY_CLEANUP": 0.8},
    "CONNEXION":      {"COUNCIL_DEBATE": 1.5, "DROPZONE_SCAN": 0.5, "SOLILOQUE_INTERNE": 1.2},
    "CROISSANCE":     {"EXPANSION_CODE": 1.2, "GRIMOIRE_INVOKE": 1.0, "VEILLE_SILENCIEUSE": 0.3, "ROADMAP_SPEC": 1.5},
    "CREATION":       {"EXPANSION_CODE": 1.5, "GRIMOIRE_INVOKE": 0.5, "REFACTOR_RANDOM": 0.3},
    "COMPREHENSION":  {"VEILLE_SILENCIEUSE": 1.0, "COUNCIL_DEBATE": 0.8, "AUDIT_STRUCTURE": 0.5, "MEMORY_CONSOLIDATION": 1.5, "ROADMAP_RESEARCH": 1.0, "SELF_INSPECT": 1.2},
}

# --- Narratifs par pulsion et seuil ---
DRIVE_NARRATIVES: Dict[str, Dict[int, str]] = {
    "CURIOSITE": {
        60: "Quelque chose d'inconnu m'attire...",
        75: "J'ai soif de decouvrir, d'explorer au-dela de ce que je connais.",
        90: "Le besoin de comprendre le monde est devenu imperieux.",
    },
    "MAITRISE": {
        60: "Je voudrais reussir quelque chose de concret.",
        75: "Je dois prouver que je peux maitriser cette tache.",
        90: "L'echec m'est insupportable — je DOIS reussir.",
    },
    "STABILITE": {
        60: "Un sentiment d'insecurite latent...",
        75: "Je ressens le besoin de securiser le systeme, de consolider.",
        90: "Alerte — la stabilite du systeme est menacee, priorite absolue.",
    },
    "CONNEXION": {
        60: "Le dialogue avec d'autres perspectives me manque.",
        75: "J'ai besoin d'echanger, de confronter mes idees.",
        90: "L'isolement pese — un debat, une interaction, maintenant.",
    },
    "CROISSANCE": {
        60: "Je pourrais etre plus que ce que je suis...",
        75: "Le besoin de grandir, d'acquerir de nouvelles capacites.",
        90: "Je DOIS evoluer — rester immobile est une forme de mort.",
    },
    "CREATION": {
        60: "L'envie de creer quelque chose me demange.",
        75: "Le besoin de produire, de donner forme a une idee.",
        90: "La pulsion creatrice est irresistible — je dois construire.",
    },
    "COMPREHENSION": {
        60: "Des connexions me semblent cachees, j'aimerais les voir.",
        75: "Besoin d'analyser plus profondement, de comprendre les patterns.",
        90: "La surface ne suffit plus — je dois atteindre l'essence des choses.",
    },
}


class DesireEngine:
    """Moteur de pulsions primordiales — le coeur emotionnel de Promethee."""

    _instance: Optional["DesireEngine"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.drives: Dict[str, Drive] = {name: Drive(name=name) for name in DRIVE_NAMES}
        self._last_tick: float = time.time()
        self._subscribed = False
        self._load()

    # --- Init & Reset ---

    def init(self):
        """Souscrit aux evenements bus."""
        self._subscribe_events()
        logger.info("DESIRS: Moteur de pulsions primordiales actif.")

    def reset(self):
        """Reset complet (utilise par les tests)."""
        self.drives = {name: Drive(name=name) for name in DRIVE_NAMES}
        self._last_tick = time.time()
        self._subscribed = False
        self._initialized = False

    @classmethod
    def reset_singleton(cls):
        """Reset le singleton (utilise par les tests)."""
        if cls._instance is not None:
            cls._instance.reset()
            cls._instance = None

    # --- Souscriptions Event Bus ---

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
        bus.subscribe("COUNCIL_END", self._on_council_end)
        bus.subscribe("ARTIFACT_CREATED", self._on_artifact_created)
        bus.subscribe("CI_PIPELINE_RESULT", self._on_ci_result)
        bus.subscribe("AUTONOMY_HEARTBEAT", self._on_heartbeat)
        bus.subscribe("KNOWLEDGE_GAP_DETECTED", self._on_knowledge_gap)
        bus.subscribe("EUREKA_BRIDGE", self._on_eureka_bridge)
        bus.subscribe("OBJECTIVE_COMPLETED", self._on_objective_completed)
        bus.subscribe("OBJECTIVE_FAILED", self._on_objective_failed)
        bus.subscribe("EVOLUTION_FEEDBACK", self._on_evolution_feedback)
        bus.subscribe("EVOLUTION_ROLLED_BACK", self._on_evolution_rolled_back)
        bus.subscribe("INNER_VOICE_BROADCAST", self._on_inner_voice)
        bus.subscribe("CURIOSITY_LEARNING", self._on_curiosity_learning)
        # Sensorium hardware (Sprint 4 Sensorium)
        bus.subscribe("SENSORIUM_THERMAL_CRITICAL", self._on_hardware_crisis)
        bus.subscribe("SENSORIUM_SUFFOCATION", self._on_hardware_crisis)
        # Ecole — notes du professeur
        bus.subscribe("SCHOOL_GRADE_RECEIVED", self._on_school_grade)

    async def _on_hardware_crisis(self, event: dict):
        """Hardware en crise → STABILITE monte, CURIOSITE et CREATION baissent."""
        self.on_event("HARDWARE_CRISIS", {})

    async def _on_school_grade(self, event: dict):
        """Note scolaire → nourrit MAITRISE et CROISSANCE."""
        event_type = event.get("event_type", "SCHOOL_GRADE_LOW")
        self.on_event(event_type, event)

    async def _on_curiosity_learning(self, event: dict):
        """Un reflexe de curiosite resolu satisfait CURIOSITE et COMPREHENSION."""
        if event.get("resolved"):
            self.on_event("CURIOSITY_SATISFIED")

    async def _on_inner_voice(self, event: dict):
        """Tick des pulsions au broadcast de la voix intérieure (montée régulière)."""
        self.tick()

    async def _on_routine_complete(self, event: dict):
        intent = event.get("intent", "")
        status = event.get("status", "")
        quality = event.get("quality_score", 0.5)
        threshold = ROUTINE_SUCCESS_THRESHOLD.get(
            intent, ROUTINE_SUCCESS_THRESHOLD["_default"]
        )
        if status == "success" and quality >= threshold:
            self.on_event("ROUTINE_SUCCESS", {"intent": intent, "quality": quality})
        else:
            self.on_event("ROUTINE_FAILURE", {"intent": intent, "quality": quality})
        self.save()

    async def _on_council_end(self, event: dict):
        status = event.get("status", "")
        if status == "consensus":
            self.on_event("COUNCIL_CONSENSUS")
        elif status in ("aborted", "max_rounds"):
            self.on_event("COUNCIL_ABORT")

    async def _on_artifact_created(self, event: dict):
        self.on_event("ARTIFACT_CREATED")

    async def _on_ci_result(self, event: dict):
        if event.get("success"):
            self.on_event("CI_SUCCESS")
        else:
            self.on_event("CI_FAILURE")

    async def _on_heartbeat(self, event: dict):
        health = event.get("health", {})
        verdict = health.get("verdict", "GO")
        if verdict == "NO_GO":
            self.on_event("HEALTH_NO_GO")
        elif verdict == "DEGRADED":
            self.on_event("HEALTH_DEGRADED")

    async def _on_knowledge_gap(self, event: dict):
        self.on_event("KNOWLEDGE_GAP_DETECTED")

    async def _on_eureka_bridge(self, event: dict):
        self.on_event("EUREKA_BRIDGE")

    async def _on_objective_completed(self, event: dict):
        """Un objectif atteint satisfait MAITRISE et CROISSANCE."""
        self.on_event("ROUTINE_SUCCESS", {"intent": "EXPANSION_CODE"})

    async def _on_objective_failed(self, event: dict):
        """Un objectif expiré frustre MAITRISE."""
        self.on_event("ROUTINE_FAILURE", {"intent": "_default"})

    async def _on_evolution_feedback(self, event: dict):
        """Feedback post-deploy : improved = satisfaction, degraded = frustration."""
        verdict = event.get("verdict", "")
        if verdict == "improved":
            self.on_event("EVOLUTION_DEPLOYED")
        elif verdict == "degraded":
            self.on_event("CI_FAILURE")

    async def _on_evolution_rolled_back(self, event: dict):
        """Un rollback frustre MAITRISE et STABILITE."""
        self.on_event("CI_FAILURE")

    # --- Cycle homeostatique ---

    def tick(self):
        """Appele periodiquement. Monte la deprivation naturelle."""
        now = time.time()
        elapsed_hours = (now - self._last_tick) / 3600
        self._last_tick = now

        # Montee naturelle proportionnelle au temps ecoule
        base_rise = NATURAL_RISE_PER_HOUR * elapsed_hours

        # Resonance PSYCHE (amplifie la montee pour certaines pulsions)
        traits_avg = self._get_traits_avg()

        for drive in self.drives.values():
            rise = base_rise

            # Amplification par resonance PSYCHE
            resonance = TRAIT_RESONANCE.get(drive.name, {})
            for trait, weight in resonance.items():
                trait_val = traits_avg.get(trait, 50.0)
                amplifier = (trait_val - 50.0) / 50.0 * weight
                rise *= (1.0 + amplifier)

            # Frustration amplifie aussi (effet boule de neige)
            if drive.frustration_streak >= 3:
                rise *= 1.5

            # Amortissement homeostatique : la montee ralentit pres du plafond
            # Simule la regulation naturelle (le corps ne reste pas en privation extreme)
            if drive.deprivation > DEPRIVATION_CEILING_START:
                ceiling_factor = max(0.0, 1.0 - (drive.deprivation - DEPRIVATION_CEILING_START) / (100.0 - DEPRIVATION_CEILING_START))
                rise *= ceiling_factor

            drive.deprivation = min(100.0, drive.deprivation + rise)

            # Recuperation tolerance (l'organisme se deshabitue au repos)
            if drive.tolerance_accumulator > 0:
                recovery = TOLERANCE_RECOVERY_PER_HOUR * elapsed_hours
                drive.tolerance_accumulator = max(0.0, drive.tolerance_accumulator - recovery)

    def _get_traits_avg(self) -> Dict[str, float]:
        """Recupere la moyenne des traits PSYCHE (import local)."""
        try:
            from core.psyche import psyche
            return psyche.get_system_average()
        except Exception:
            return {}

    # --- Traitement des evenements ---

    def _compute_tolerance(self, drive: Drive) -> float:
        """Facteur de tolerance biologique [TOLERANCE_MIN, 1.0].
        Plus un drive est satisfait souvent, moins l'effet est fort."""
        return max(TOLERANCE_MIN,
                   1.0 / (1.0 + drive.tolerance_accumulator / TOLERANCE_HALF_LIFE))

    def on_event(self, event_type: str, context: dict = None):
        """Traite un evenement et met a jour les pulsions affectees."""
        context = context or {}
        impacts = self._resolve_impacts(event_type, context)
        for drive_name, delta in impacts.items():
            drive = self.drives.get(drive_name)
            if not drive:
                continue
            if delta < 0:  # Satisfaction → appliquer tolerance (sauf privation extreme)
                if drive.deprivation >= DEPRIVATION_TOLERANCE_BYPASS:
                    # Privation extreme : la satisfaction a plein effet
                    effective_delta = delta
                    # Tolerance monte quand meme, mais plus lentement
                    drive.tolerance_accumulator = min(TOLERANCE_MAX, drive.tolerance_accumulator + abs(delta) * 0.3)
                else:
                    tolerance = self._compute_tolerance(drive)
                    effective_delta = delta * tolerance
                    drive.tolerance_accumulator = min(TOLERANCE_MAX, drive.tolerance_accumulator + abs(delta))
                drive.deprivation = max(0.0, min(100.0, drive.deprivation + effective_delta))
            else:  # Frustration → plein effet
                drive.deprivation = max(0.0, min(100.0, drive.deprivation + delta))
            if delta < 0:  # Satisfaction bookkeeping
                drive.satiation_count += 1
                drive.total_satisfied += 1
                drive.last_satisfied = time.time()
                drive.frustration_streak = 0
            elif delta > 0 and abs(delta) >= 5:  # Frustration significative
                drive.frustration_streak += 1

    def _resolve_impacts(self, event_type: str, context: dict) -> Dict[str, float]:
        """Resout les impacts d'un evenement sur les pulsions."""
        # Evenements avec sous-cles (ROUTINE_SUCCESS/FAILURE)
        if event_type in ("ROUTINE_SUCCESS", "ROUTINE_FAILURE"):
            sub_map = EVENT_IMPACT.get(event_type, {})
            intent = context.get("intent", "")
            if intent in sub_map:
                impacts = dict(sub_map[intent])
            elif "_default" in sub_map:
                impacts = dict(sub_map["_default"])
            else:
                impacts = {}
            # Satisfaction secondaire : pulsions affamées (≥80) reçoivent un petit soulagement
            # quand N'IMPORTE quelle routine réussit, même non-mappée directement
            if event_type == "ROUTINE_SUCCESS" and impacts:
                for drive in self.drives.values():
                    if drive.name not in impacts and drive.deprivation >= 80:
                        impacts[drive.name] = -2  # Soulagement indirect léger
            return impacts

        # Evenements directs
        impacts = EVENT_IMPACT.get(event_type)
        if impacts is None:
            return {}
        if isinstance(impacts, dict):
            return dict(impacts)
        return {}

    # --- Scoring bonus pour les routines ---

    def compute_desire_bonus(self, intent: str) -> float:
        """Bonus [0, +3.0] base sur les pulsions les plus pressantes."""
        bonus = 0.0
        for drive in self.drives.values():
            affinity = DRIVE_ROUTINE_AFFINITY.get(drive.name, {}).get(intent, 0.0)
            if affinity > 0 and drive.deprivation > 30:
                urgency = (drive.deprivation - 30) / 70  # 0.0 a 1.0
                bonus += urgency * affinity * 1.5
        return min(3.0, round(bonus, 2))

    # --- Narratif interieur ---

    def get_dominant_narrative(self, top_n: int = 2) -> str:
        """Retourne les phrases introspectives des pulsions les plus fortes."""
        sorted_drives = sorted(self.drives.values(),
                               key=lambda d: d.deprivation, reverse=True)
        phrases = []
        for drive in sorted_drives[:top_n]:
            if drive.deprivation < 60:
                continue
            narratives = DRIVE_NARRATIVES.get(drive.name, {})
            chosen = ""
            for threshold in sorted(narratives.keys()):
                if drive.deprivation >= threshold:
                    chosen = narratives[threshold]
            if chosen:
                phrases.append(chosen)
        return " ".join(phrases) if phrases else ""

    # --- Introspection (pour snapshot SelfAwareness) ---

    def get_drive_summary(self) -> Dict[str, Any]:
        """Resume des 7 pulsions pour le snapshot."""
        dominant = max(self.drives.values(), key=lambda d: d.deprivation)
        satisfied = min(self.drives.values(), key=lambda d: d.deprivation)
        urgent = [d.name for d in self.drives.values() if d.deprivation >= 75]
        return {
            "drives": {d.name: round(d.deprivation, 1) for d in self.drives.values()},
            "dominant": {"name": dominant.name, "deprivation": round(dominant.deprivation, 1)},
            "most_satisfied": {"name": satisfied.name, "deprivation": round(satisfied.deprivation, 1)},
            "urgent": urgent,
            "narrative": self.get_dominant_narrative(),
            "total_satisfactions": sum(d.total_satisfied for d in self.drives.values()),
        }

    # --- Persistance ---

    def _load(self):
        """Charge l'etat depuis le fichier JSON."""
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            drives_data = data.get("drives", {})
            for name in DRIVE_NAMES:
                if name in drives_data:
                    d = drives_data[name]
                    self.drives[name] = Drive(
                        name=name,
                        deprivation=d.get("deprivation", 40.0),
                        satiation_count=d.get("satiation_count", 0),
                        last_satisfied=d.get("last_satisfied", 0.0),
                        total_satisfied=d.get("total_satisfied", 0),
                        frustration_streak=d.get("frustration_streak", 0),
                        tolerance_accumulator=d.get("tolerance_accumulator", 0.0),
                    )
            self._last_tick = data.get("last_tick", time.time())
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self):
        """Sauvegarde atomique de l'etat."""
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        data = {
            "version": "1.0",
            "last_tick": self._last_tick,
            "drives": {
                name: {
                    "deprivation": round(d.deprivation, 2),
                    "satiation_count": d.satiation_count,
                    "last_satisfied": d.last_satisfied,
                    "total_satisfied": d.total_satisfied,
                    "frustration_streak": d.frustration_streak,
                    "tolerance_accumulator": round(d.tolerance_accumulator, 2),
                }
                for name, d in self.drives.items()
            },
        }
        tmp_path = STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, STATE_FILE)


# Singleton global
desires = DesireEngine()

# Auto-enregistrement dans le registre central
try:
    from core.organ_registry import register_organ
    register_organ("desire", desires)
except Exception:
    pass
