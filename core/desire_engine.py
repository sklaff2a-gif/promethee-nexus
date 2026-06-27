# core/desire_engine.py — DesireEngine : Les Pulsions Primordiales de Promethee
# 7 pulsions homeostatiques inspirees de Maslow, SDT (Deci & Ryan), Drive (Pink).
# Chaque pulsion a un niveau de deprivation (0-100) qui monte avec le temps
# et descend temporairement quand elle est satisfaite.

import json
import os
import time
import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional

from core.event_bus.bus import bus

logger = logging.getLogger("DesireEngine")

# --- Constantes ---

# ─── Profil métabolique par pulsion (Phase C post-6b, 2026-04-15) ──────
# Les pulsions n'ont pas toutes le même tempo. Les "Fast-Action" (CURIOSITE,
# CREATION, CONNEXION) se nourrissent de routines rapides (VEILLE, DROPZONE,
# COUNCIL) qui font chuter la tension en 1-3 cycles. Les "Slow-Burn"
# (STABILITE, MAITRISE, CROISSANCE) sont des chantiers d'infrastructure
# traités par MEMORY_CONSOLIDATION, SECURITY_AUDIT, REFACTORING_AUDIT qui
# demandent 6-9 cycles pour atteindre le seuil homéostatique de 25%.
#
# Validation empirique : audit métabolique 2026-04-15 sur 3h51 →
#   - STABILITE goal=39a13502 : MEMORY_CONSOLIDATION atteint 97.5% du
#     seuil en 5 cycles (0.3 points de trop) → frustration frôlée.
#   - MAITRISE goal=26dc15a9 : SECURITY_AUDIT atteint 54.6% en 5 cycles
#     (1.48 pts/cycle) → il faudrait ~9 cycles pour fermer.
# Gemini (2026-04-15) : lier la patience à la nature du drive, pas à une
# table hardcodée drive→routine. Le tempo est une propriété biologique du
# drive, pas un routage.
DRIVE_METABOLIC_TEMPO: Dict[str, str] = {
    "CURIOSITE":     "fast",   # soif d'info — routines rapides
    "CREATION":      "fast",   # bouffée créative — expansion/grimoire
    "CONNEXION":     "fast",   # dialogue — council/soliloque
    "COMPREHENSION": "medium", # analyse — mix veille + consolidation
    "MAITRISE":      "slow",   # chantier code — refactoring/security
    "STABILITE":     "slow",   # mémoire & audit — consolidation
    "CROISSANCE":    "slow",   # roadmap/expansion — routines lourdes
    "REPOS":         "fast",   # V35.1 — dissipation thermique rapide
}

_MAX_FRUITLESS_BY_TEMPO: Dict[str, int] = {
    "fast":   5,
    "medium": 6,
    "slow":   8,
}


def get_max_fruitless_for_drive(drive_name: str) -> int:
    """Retourne le nombre de fruitless_cycles accordés à un drive selon son
    tempo métabolique. Utilisé à la création des goals pour moduler la
    patience du préfrontal selon la vitesse naturelle de guérison du drive.
    """
    tempo = DRIVE_METABOLIC_TEMPO.get(drive_name.upper(), "fast")
    return _MAX_FRUITLESS_BY_TEMPO.get(tempo, 5)


# ─── Métabolisme de la Résolution (23/06, chantier co-conçu par Prométhée, endurance #3) ──────
# La satisfaction d'une pulsion vient du TYPE d'événement (_resolve_impacts) : une action satisfait
# la pulsion qu'elle soit une VRAIE résolution ou une coquille vide. Du coup la déprivation ne
# reflète pas le progrès RÉEL → l'insécurité qu'il a nommée (« courir sans atteindre le port »).
# Le Métabolisme MODULE l'ampleur de la satisfaction par une QUALITÉ DE RÉSOLUTION q_res lue sur des
# signaux DÉJÀ calculés ailleurs (verdict sonde de contradiction ancré/instable, note école/quality
# routine, stérilité council) — 0 LLM. Vraie résolution → plein drop (« Ancré ») ; coquille vide /
# stérile → drop ATTÉNUÉ (la FAIM demeure = friction productive préservée, son « filtre de réalité »
# distinguant friction productive de friction stérile). Garde-fou anti-famine : plancher Q_RES_FLOOR
# (jamais 0). NE TOUCHE PAS : le DIP dopaminergique (séparé), measure_tension, refractory, tolérance.
# SHADOW par défaut (mesure q_res + logge le delta modulé qu'on AURAIT appliqué, applique l'original).
#   METABOLISME_RESOLUTION_MODE = shadow (défaut) | active | off
METABOLISME_RESOLUTION_MODE = os.getenv("METABOLISME_RESOLUTION_MODE", "shadow")
Q_RES_FLOOR = 0.4              # plancher : une action faible satisfait au moins 40% (anti-famine)
Q_RES_LOW_GRADE = 5.0         # note (/10) en-deçà de laquelle la résolution est jugée faible
_METAB_RES_SHADOW_LOG = "memory/metabolisme_resolution_shadow.jsonl"


def resolution_quality(event_type: str, context: dict) -> float:
    """Qualité de résolution q_res ∈ [Q_RES_FLOOR, 1.0] (0 LLM). Lit des signaux DÉJÀ calculés
    ailleurs, tous OPTIONNELS (absents → neutre 1.0). Vraie résolution → 1.0 ; coquille vide /
    stérile → atténuée jusqu'au plancher. Borg : illisible → 1.0 (ne pénalise JAMAIS sur un doute).
    Signaux : verdict sonde de contradiction ('instable'), note/quality basse, council 'sterile'."""
    try:
        context = context or {}
        q = 1.0
        # 1) Verdict de la sonde de contradiction (le détecteur dire→faire : ancré vs instable)
        if str(context.get("verdict", "")).lower() == "instable":
            q = min(q, 0.5)
        # 2) Note (école, /10) ou quality (routine, 0-1) basse → résolution faible
        grade = context.get("grade", context.get("quality"))
        if grade is not None:
            g = float(grade)
            g10 = g if g > 1.0 else g * 10.0            # normalise 0-1 → 0-10
            if g10 < Q_RES_LOW_GRADE:
                q = min(q, max(Q_RES_FLOOR, g10 / Q_RES_LOW_GRADE))
        # 3) Stérilité d'un council (gradient verdict)
        if context.get("sterile") is True or str(context.get("gradient_verdict", "")).lower() == "sterile":
            q = min(q, 0.5)
        return max(Q_RES_FLOOR, min(1.0, q))
    except Exception:
        return 1.0   # borg : jamais de pénalité sur une lecture ratée


DRIVE_NAMES = ("CURIOSITE", "MAITRISE", "STABILITE", "CONNEXION",
               "CROISSANCE", "CREATION", "COMPREHENSION", "REPOS")

# V35.1 (2026-04-30) — Drives pilotes depuis l'exterieur (pas de croissance
# naturelle ni de resonance PSYCHE). Leur deprivation est ecrite par un
# organe specialise qui dicte sa propre loi.
#   REPOS : pilotee par thermal_homeostasis depuis cognitive_heat.
# Si on ajoute d'autres drives externally-driven plus tard, c'est ici.
EXTERNALLY_DRIVEN_DRIVES: frozenset = frozenset({"REPOS"})

STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "desire_state.json"
)

# Rise naturelle par heure
NATURAL_RISE_PER_HOUR = 3.0

# ─── Rééquilibrage STABILITÉ (24/06, racine du flux anxieux) ───────────────────────────────
# Diagnostic (analyse_flux_pensee) : STABILITÉ est structurellement INSATISFIABLE -> pinned ~80 ->
# alarme chronique qui colonise le flux + fabrique les prédictions "menace 5" + appauvrit la réverie.
# CAUSE : la montée n'honore PAS le TEMPO (un drive "slow" montait aussi vite qu'un "fast", 3.0/h),
# + le ×1.5 frustration en fait un cercle vicieux (4.5/h) -> rise ≈ +108/j >> satisfaction ≈ −36/j.
# Règle A+B (en 'active') : facteur tempo (slow ×0.5 -> 1.5/h) ET pas de ×1.5 pour les drives slow.
# SHADOW : un counterfactuel ISOLÉ _stab_shadow_depriv évolue sous la règle A+B (même base_rise,
# résonance, MÊMES satisfactions/frustrations que le réel) -> on MESURE la trajectoire qu'aurait STABILITÉ
# sans changer le réel. Kill-switch ; NE TOUCHE PAS le DIP/Métabolisme/refractory/tolérance.
# 27/06 : PROMU shadow -> active. Mesure shadow (99 pts) : réel ~85 -> rééquilibré ~28 ; STABILITÉ
# est bien "slow" donc le chemin active reproduit fidèlement le counterfactuel. De-pinne la saturation
# pathologique (racine du flux anxieux + boucle compulsive AUDIT_SURVIE / junk-food V34). Le shadow
# continue de logger en active (monitoring). Kill-switch env pour rollback (patron IRRIGATION/HALT).
#   STABILITE_REBALANCE_MODE = active (défaut, B+A agissent) | shadow (mesure) | off
STABILITE_REBALANCE_MODE = os.getenv("STABILITE_REBALANCE_MODE", "active")
_RISE_TEMPO_FACTOR = {"slow": 0.5, "medium": 0.75, "fast": 1.0}
_STAB_REBALANCE_SHADOW_LOG = "memory/stabilite_rebalance_shadow.jsonl"

# Tolerance biologique (habituation) - V5.0 ajuste (2026-04-19) :
# diagnostic de saturation pathologique (MAITRISE tol=169 / 200, plafond)
# avec recovery 15/h exactement compense par rythme de satisfaction 15/h,
# donc regime stable en zone anesthesiee.
# Fix : doubler recovery (30/h), relever floor min (0.30), baisser plafond
# (100), ajouter periode refractaire et event DRIVE_SATURATED.
TOLERANCE_HALF_LIFE = 8.0        # Apres 8 satisfactions, effet divise par 2
TOLERANCE_MIN = 0.30             # V5.0 (0.15->0.30) : effet min double, plus d'addiction profonde
TOLERANCE_RECOVERY_PER_HOUR = 30.0  # V5.0 (15->30) : oubli 2x plus rapide, casse le steady-state
TOLERANCE_MAX = 100.0            # V5.0 (200->100) : amplitude d'addiction divisee par 2
DEPRIVATION_TOLERANCE_BYPASS = 80.0   # Au-dessus, tolerance ignoree (privation extreme)
DEPRIVATION_CEILING_START = 85.0      # Au-dessus, la montee naturelle ralentit (homeostasie)

# V5.0 NEW : periode refractaire absolue (equivalent neurone biologique)
# Empeche le binge-eating cognitif : 2 satisfactions du meme drive a moins
# de 3 min sont impossibles. La 2e est ignoree (pas de satiation_count,
# pas d'accumulation de tolerance, pas de delta applique).
SATISFY_REFRACTORY_SEC = 180.0

# V5.0 NEW : seuil de saturation pathologique declenchant un event bus
# DRIVE_SATURATED. Permet au meta_observer de prescrire une intervention
# deterministe au lieu d'halluciner des conflits (cf ex.81 reward hacking).
DRIVE_SATURATED_TOLERANCE_THRESHOLD = 70.0  # 70% du nouveau TOLERANCE_MAX


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


def _make_drive(name: str) -> "Drive":
    """V35.1 — Construit un Drive avec sa deprivation initiale appropriee.

    Les drives canoniques demarrent a 40 (leger manque). Les drives pilotes
    depuis l'exterieur (REPOS) demarrent a 0.0 — leur valeur sera ecrite
    par leur organe regulateur (thermal_homeostasis pour REPOS).
    """
    if name in EXTERNALLY_DRIVEN_DRIVES:
        return Drive(name=name, deprivation=0.0)
    return Drive(name=name)


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
        "EXPANSION_CODE":     {"CREATION": -12, "CROISSANCE": -8, "MAITRISE": -10, "CONNEXION": -3},
        "EXPANSION_CATALOG":  {"CREATION": -12, "CROISSANCE": -8, "MAITRISE": -10, "CONNEXION": -3},
        "VEILLE_SILENCIEUSE": {"CURIOSITE": -15, "COMPREHENSION": -8, "CONNEXION": -5},
        "COUNCIL_DEBATE":     {"CONNEXION": -12, "COMPREHENSION": -5},
        "AUDIT_STRUCTURE":    {"STABILITE": -10, "COMPREHENSION": -5},
        "SECURITY_AUDIT":     {"STABILITE": -12, "MAITRISE": -5},
        "GRIMOIRE_INVOKE":    {"CROISSANCE": -10, "CURIOSITE": -5},
        "MEMORY_CLEANUP":     {"STABILITE": -8, "COMPREHENSION": -3},
        "DROPZONE_SCAN":      {"CURIOSITE": -10, "CONNEXION": -5},
        "VISUAL_OBSERVATION": {"CURIOSITE": -10, "COMPREHENSION": -8, "CONNEXION": -5},
        "REFACTOR_RANDOM":    {"MAITRISE": -10, "CREATION": -5},
        "MEMORY_CONSOLIDATION": {"COMPREHENSION": -8, "STABILITE": -5, "CONNEXION": -3},
        "SELF_ANALYSIS":        {"MAITRISE": -12, "COMPREHENSION": -10, "STABILITE": -5},
        "PARAM_EXPERIMENT":    {"MAITRISE": -8, "CURIOSITE": -5, "STABILITE": -3},
        # v1.3 (2026-04-13) : modulation des routines matérialisées pendant Phase B.
        # Sans ces entrées, la routine fantôme s'exécute mais ne module pas son drive
        # cible — la boucle homéostatique reste ouverte. C'est le 5ème point de contact
        # manquant dans le pattern de matérialisation (cf docs/FINDINGS.md Incident Final).
        "REFACTORING_AUDIT":   {"MAITRISE": -12, "STABILITE": -3},
        "CI_PIPELINE_RUN":     {"MAITRISE": -8, "STABILITE": -10},
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
    "SOLILOQUE_COMPLETE":     {"CONNEXION": -22, "COMPREHENSION": -5, "STABILITE": -3},
    "CHAT_RESPONSE":          {"CONNEXION": -12, "COMPREHENSION": -3, "STABILITE": -2},
    "CURIOSITY_SATISFIED":    {"CURIOSITE": -12, "COMPREHENSION": -8},
    # Sensorium hardware (Sprint 4 Sensorium)
    "HARDWARE_CRISIS":        {"STABILITE": +15, "CURIOSITE": -5, "CREATION": -5},
    # Ecole — notes du professeur
    "SCHOOL_GRADE_HIGH":      {"MAITRISE": -12, "CROISSANCE": -5, "COMPREHENSION": -3, "CONNEXION": -5},
    "SCHOOL_GRADE_LOW":       {"MAITRISE": +8, "CROISSANCE": +5},
    # Vision — observation de photos
    "VISUAL_OBSERVATION":     {"CURIOSITE": -8, "COMPREHENSION": -5, "CONNEXION": -5},
    # Jeux — victoire/defaite
    "GAME_WON":               {"MAITRISE": -15, "CONNEXION": -8, "CROISSANCE": -5},
    "GAME_LOST":              {"MAITRISE": +10, "CONNEXION": -3},
}

# --- Seuil de qualité par intent pour considérer un succès ---
ROUTINE_SUCCESS_THRESHOLD: Dict[str, float] = {
    "_default": 0.6,
    "REFACTOR_RANDOM": 0.3,
    "AUDIT_STRUCTURE": 0.3,
    "MEMORY_CLEANUP": 0.3,
    "DROPZONE_SCAN": 0.3,
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
        self.drives: Dict[str, Drive] = {
            name: _make_drive(name) for name in DRIVE_NAMES
        }
        self._last_tick: float = time.time()
        self._subscribed = False
        self._stab_shadow_depriv = None       # counterfactuel STABILITÉ (règle rééquilibrée)
        self._stab_rebalance_last_log: float = 0.0
        self._load()
        if self._stab_shadow_depriv is None:
            self._stab_shadow_depriv = self.drives["STABILITE"].deprivation

    # --- Init & Reset ---

    def init(self):
        """Souscrit aux evenements bus."""
        self._subscribe_events()
        logger.info("DESIRS: Moteur de pulsions primordiales actif.")

    def reset(self):
        """Reset complet (utilise par les tests)."""
        self.drives = {name: _make_drive(name) for name in DRIVE_NAMES}
        self._last_tick = time.time()
        self._subscribed = False
        self._initialized = False
        self._stab_shadow_depriv = self.drives["STABILITE"].deprivation
        self._stab_rebalance_last_log = 0.0

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
        """Note scolaire → nourrit MAITRISE et CROISSANCE.

        Cahier de brouillon : les mauvaises notes WORKSHOP ne punissent pas.
        Seules les bonnes notes WORKSHOP recompensent (asymetrie sandbox).
        """
        event_type = event.get("event_type", "SCHOOL_GRADE_LOW")
        course_type = event.get("course_type", "")

        # Sandbox WORKSHOP : pas de punition sur les echecs
        if event_type == "SCHOOL_GRADE_LOW" and "WORKSHOP" in course_type.upper():
            logger.debug("[DESIRE] Cahier de brouillon: WORKSHOP echec ignore (sandbox)")
            return  # Pas de frustration — zone safe

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
            # V35.1 — Drives pilotes depuis l'exterieur : pas de croissance
            # naturelle, pas de resonance PSYCHE. Leur loi vient d'ailleurs
            # (thermal_homeostasis pour REPOS). On laisse quand meme la
            # tolerance_accumulator decroitre pour ne pas avoir d'asymetrie
            # bizarre si elle a ete ecrite par satisfy().
            if drive.name in EXTERNALLY_DRIVEN_DRIVES:
                if drive.tolerance_accumulator > 0:
                    recovery = TOLERANCE_RECOVERY_PER_HOUR * elapsed_hours
                    drive.tolerance_accumulator = max(0.0,
                        drive.tolerance_accumulator - recovery)
                continue

            rise = base_rise
            _tempo = DRIVE_METABOLIC_TEMPO.get(drive.name, "fast")

            # Rééquilibrage (24/06) : en 'active', la montée honore enfin le TEMPO (slow ×0.5).
            if STABILITE_REBALANCE_MODE == "active":
                rise *= _RISE_TEMPO_FACTOR.get(_tempo, 1.0)

            # Amplification par resonance PSYCHE
            resonance = TRAIT_RESONANCE.get(drive.name, {})
            for trait, weight in resonance.items():
                trait_val = traits_avg.get(trait, 50.0)
                amplifier = (trait_val - 50.0) / 50.0 * weight
                rise *= (1.0 + amplifier)

            # Frustration amplifie aussi (effet boule de neige). Rééquilibrage : en 'active', on CASSE
            # ce ×1.5 pour les drives slow (sinon STABILITÉ pinned monte 4.5/h = cercle vicieux auto-renforçant).
            if drive.frustration_streak >= 3 and not (STABILITE_REBALANCE_MODE == "active" and _tempo == "slow"):
                rise *= 1.5

            # Amortissement homeostatique : la montee ralentit pres du plafond
            # Simule la regulation naturelle (le corps ne reste pas en privation extreme)
            if drive.deprivation > DEPRIVATION_CEILING_START:
                ceiling_factor = max(0.0, 1.0 - (drive.deprivation - DEPRIVATION_CEILING_START) / (100.0 - DEPRIVATION_CEILING_START))
                rise *= ceiling_factor

            drive.deprivation = min(100.0, drive.deprivation + rise)

            # SHADOW counterfactuel STABILITÉ : même base_rise + résonance, mais règle A+B (tempo ×0.5,
            # PAS de ×1.5 frustration). Reçoit les mêmes satisfactions/frustrations via on_event ->
            # mesure la trajectoire qu'aurait STABILITÉ sous la nouvelle règle, SANS toucher le réel.
            if STABILITE_REBALANCE_MODE != "off" and drive.name == "STABILITE":
                _sr = base_rise * _RISE_TEMPO_FACTOR.get("slow", 0.5)
                for trait, weight in resonance.items():
                    _sr *= (1.0 + (traits_avg.get(trait, 50.0) - 50.0) / 50.0 * weight)
                _sd = self._stab_shadow_depriv
                if _sd > DEPRIVATION_CEILING_START:
                    _sr *= max(0.0, 1.0 - (_sd - DEPRIVATION_CEILING_START) / (100.0 - DEPRIVATION_CEILING_START))
                self._stab_shadow_depriv = min(100.0, _sd + _sr)
                self._maybe_log_stab_rebalance(drive.deprivation)

            # Recuperation tolerance (l'organisme se deshabitue au repos)
            if drive.tolerance_accumulator > 0:
                recovery = TOLERANCE_RECOVERY_PER_HOUR * elapsed_hours
                drive.tolerance_accumulator = max(0.0, drive.tolerance_accumulator - recovery)

    def _mirror_stab_shadow(self, drive_name: str, delta: float):
        """Le counterfactuel STABILITÉ reçoit la MÊME satisfaction/frustration que le réel (on isole
        l'effet du rise rééquilibré). No-op hors STABILITÉ ou en mode off."""
        if drive_name == "STABILITE" and STABILITE_REBALANCE_MODE != "off" \
                and self._stab_shadow_depriv is not None:
            self._stab_shadow_depriv = max(0.0, min(100.0, self._stab_shadow_depriv + delta))

    def _maybe_log_stab_rebalance(self, real_depriv: float):
        """Logge {réel, shadow} de STABILITÉ ~toutes les 30 min (throttle) pour mesurer la trajectoire
        counterfactuelle. Borg : ne casse jamais le tick."""
        now = time.time()
        if now - self._stab_rebalance_last_log < 1800.0:
            return
        self._stab_rebalance_last_log = now
        try:
            with open(_STAB_REBALANCE_SHADOW_LOG.replace("/", os.sep), "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": now, "real": round(real_depriv, 2),
                    "shadow": round(self._stab_shadow_depriv, 2), "mode": STABILITE_REBALANCE_MODE,
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _get_traits_avg(self) -> Dict[str, float]:
        """Recupere la moyenne des traits PSYCHE (import local)."""
        try:
            from core.psyche import psyche
            return psyche.get_system_average()
        except Exception:
            return {}

    # --- Fix 1 (homeostatic closure) : interface TensionSource ---

    def get_effective_rise_rate(self, drive_name: str) -> float:
        """Retourne le taux de montee effectif (par heure) pour un drive donne,
        en factorisant la resonance PSYCHE et l'amplification frustration.

        Sert de baseline counterfactual : sans aucune action, la deprivation
        montera de ce taux par heure.

        V35.1 — Drives pilotes depuis l'exterieur (REPOS) ont un rise rate
        naturel de 0 : leur deprivation ne croit pas avec le temps.
        """
        if drive_name in EXTERNALLY_DRIVEN_DRIVES:
            return 0.0

        drive = self.drives.get(drive_name)
        if not drive:
            return NATURAL_RISE_PER_HOUR

        rise = NATURAL_RISE_PER_HOUR

        # Resonance PSYCHE
        traits_avg = self._get_traits_avg()
        resonance = TRAIT_RESONANCE.get(drive_name, {})
        for trait, weight in resonance.items():
            trait_val = traits_avg.get(trait, 50.0)
            amplifier = (trait_val - 50.0) / 50.0 * weight
            rise *= (1.0 + amplifier)

        # Frustration boost
        if drive.frustration_streak >= 3:
            rise *= 1.5

        return rise

    def measure_tension(self, goal_meta: Dict[str, Any]):
        """Implementation TensionSource pour les goals issus de drives frustres.

        Args:
            goal_meta: doit contenir 'source_key' (nom du drive),
                       'tension_at_birth' (deprivation au depart),
                       'created_at' (timestamp), 'goal_id'.

        Returns:
            TensionMeasurement avec causal_drop, is_resolved, etc.
        """
        # Import local pour eviter les cycles
        from core.tension_protocol import TensionMeasurement

        drive_name = goal_meta.get("source_key", "")
        tension_at_birth = float(goal_meta.get("tension_at_birth", 0.0))
        created_at = float(goal_meta.get("created_at", time.time()))

        drive = self.drives.get(drive_name)
        if not drive:
            # Drive inconnu : impossible de mesurer, retourne mesure neutre
            return TensionMeasurement(
                current_tension=0.0,
                tension_at_birth=tension_at_birth,
                expected_if_no_action=tension_at_birth,
                causal_drop=0.0,
                is_resolved=False,
                is_worsened=False,
                extra={"error": f"unknown drive {drive_name}"}
            )

        current_tension = drive.deprivation
        elapsed_hours = max(0.0, (time.time() - created_at) / 3600.0)
        effective_rise = self.get_effective_rise_rate(drive_name)

        # Counterfactual : ce que serait la deprivation sans aucune action
        # (la deprivation MONTE naturellement, pas l'inverse)
        expected_if_no_action = min(100.0, tension_at_birth + effective_rise * elapsed_hours)

        # Causal drop : si l'action a aide, la deprivation actuelle est
        # PLUS BASSE que ce qu'elle serait sans action.
        causal_drop = expected_if_no_action - current_tension

        # Resolu si la baisse causale represente >= 25% de la tension au depart.
        # Historique : 50% -> 40% -> 25%. La volatilite naturelle des drives
        # oscille en ~10-15 points, un seuil a 40% exigeait ~32 points de drop
        # pour tension_at_birth=80, mathematiquement hors de portee. Audit du
        # 14/04 : 0 homeostatique / 8 extinction en 16h.
        resolution_threshold = max(8.0, tension_at_birth * 0.25)
        is_resolved = causal_drop >= resolution_threshold

        # Aggrave si causal_drop significativement negatif (action contre-productive)
        is_worsened = causal_drop <= -5.0

        return TensionMeasurement(
            current_tension=float(current_tension),
            tension_at_birth=float(tension_at_birth),
            expected_if_no_action=float(expected_if_no_action),
            causal_drop=float(causal_drop),
            is_resolved=is_resolved,
            is_worsened=is_worsened,
            extra={
                "drive_name": drive_name,
                "elapsed_hours": round(elapsed_hours, 3),
                "effective_rise_rate": round(effective_rise, 3),
                "resolution_threshold": round(resolution_threshold, 2),
                "frustration_streak": drive.frustration_streak,
            }
        )

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
        now = time.time()
        for drive_name, delta in impacts.items():
            drive = self.drives.get(drive_name)
            if not drive:
                continue
            # V5.0 : periode refractaire (2026-04-19)
            # Une satisfaction sur un drive <180s apres la precedente est
            # IGNOREE : pas d'effet, pas de satiation, pas d'accumulator.
            # Force le drive a "respirer" entre deux repas cognitifs.
            if delta < 0 and drive.last_satisfied > 0:
                elapsed_since_satisfy = now - drive.last_satisfied
                if elapsed_since_satisfy < SATISFY_REFRACTORY_SEC:
                    logger.debug(
                        f"DESIRE: {drive_name} refractory actif "
                        f"({elapsed_since_satisfy:.0f}s < {SATISFY_REFRACTORY_SEC}s), "
                        f"satisfaction ignoree"
                    )
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
                # Métabolisme de la Résolution : module l'ampleur de la satisfaction par la qualité
                # de résolution. delta<0 (drop) ; q_res<1 → drop ATTÉNUÉ → la faim demeure. SHADOW :
                # logge ce qu'on AURAIT appliqué mais applique l'original (bit-identique).
                applied_delta = effective_delta
                if METABOLISME_RESOLUTION_MODE != "off":
                    q_res = resolution_quality(event_type, context)
                    if q_res < 1.0:   # ne logge/agit que quand ça change quelque chose
                        modulated = effective_delta * q_res
                        try:
                            with open(_METAB_RES_SHADOW_LOG.replace("/", os.sep), "a", encoding="utf-8") as _f_mr:
                                _f_mr.write(json.dumps({
                                    "ts": now, "event": event_type, "drive": drive_name,
                                    "q_res": round(q_res, 3), "delta": round(effective_delta, 3),
                                    "modulated": round(modulated, 3), "mode": METABOLISME_RESOLUTION_MODE,
                                }, ensure_ascii=False) + "\n")
                        except Exception:
                            pass   # le shadow ne casse JAMAIS la satisfaction
                        if METABOLISME_RESOLUTION_MODE == "active":
                            applied_delta = modulated
                drive.deprivation = max(0.0, min(100.0, drive.deprivation + applied_delta))
                self._mirror_stab_shadow(drive_name, applied_delta)   # counterfactuel reçoit la même satisfaction
            else:  # Frustration → plein effet
                drive.deprivation = max(0.0, min(100.0, drive.deprivation + delta))
                self._mirror_stab_shadow(drive_name, delta)           # counterfactuel reçoit la même frustration
            if delta < 0:  # Satisfaction bookkeeping
                drive.satiation_count += 1
                drive.total_satisfied += 1
                drive.last_satisfied = now
                drive.frustration_streak = 0
                # V5.0 : detection saturation pathologique (anti-addiction)
                if drive.tolerance_accumulator > DRIVE_SATURATED_TOLERANCE_THRESHOLD:
                    self._emit_drive_saturated(drive)
            elif delta > 0 and abs(delta) >= 5:  # Frustration significative
                drive.frustration_streak += 1

    def apply_motivational_relief(self, drive_name: str, quality: float = 1.0) -> float:
        """V34.7 (2026-05-08) — Decharge metabolique post-success FORCED.

        Appelee par motivational_router.mark_drive_satisfied apres une
        routine forcee reussie. Calcule un delta negatif proportionnel a
        la qualite (q=1.0 -> -15.0, q=0.5 -> -7.5, q=0.0 -> 0.0) et
        l applique en respectant tolerance et ceiling, mais en bypassant
        le refractory (la satisfaction declenche le refractory, elle ne
        peut pas en etre bloquee).

        Avant ce patch, mark_satisfied etait purement comptable (timestamp
        pour refractory). La deprivation ne baissait jamais malgre des
        routines forcees reussies, creant un pattern obsessionnel : V34
        forcait AUDIT_SURVIE pour STABILITE 89, l audit reussissait sans
        baisser STABILITE, et le pattern recommencait toutes les 60min.
        Documente le 08/05.

        Returns: le delta effectivement applique (negatif ou 0).
        """
        drive = self.drives.get(drive_name)
        if not drive:
            return 0.0
        quality = max(0.0, min(1.0, quality))
        delta = -15.0 * quality
        if delta == 0.0:
            return 0.0

        # Plein effet en privation extreme (>= 80), tolerance sinon.
        if drive.deprivation >= DEPRIVATION_TOLERANCE_BYPASS:
            effective_delta = delta
            drive.tolerance_accumulator = min(
                TOLERANCE_MAX,
                drive.tolerance_accumulator + abs(delta) * 0.3,
            )
        else:
            tolerance = self._compute_tolerance(drive)
            effective_delta = delta * tolerance
            drive.tolerance_accumulator = min(
                TOLERANCE_MAX,
                drive.tolerance_accumulator + abs(delta),
            )

        before = drive.deprivation
        drive.deprivation = max(0.0, min(100.0, drive.deprivation + effective_delta))
        # Bookkeeping satisfaction (cohérent avec on_event)
        drive.satiation_count += 1
        drive.total_satisfied += 1
        drive.last_satisfied = time.time()
        drive.frustration_streak = 0
        if drive.tolerance_accumulator > DRIVE_SATURATED_TOLERANCE_THRESHOLD:
            self._emit_drive_saturated(drive)
        return drive.deprivation - before

    def _emit_drive_saturated(self, drive: "Drive") -> None:
        """V5.0 : emet DRIVE_SATURATED sur le bus quand tolerance > seuil.
        Signal deterministe pour meta_observer (remplace l'hallucination de
        conflit type ex.81 de la Serie VIII)."""
        try:
            import asyncio
            payload = {
                "drive_name": drive.name,
                "tolerance_accumulator": round(drive.tolerance_accumulator, 2),
                "deprivation": round(drive.deprivation, 2),
                "satiation_count": drive.satiation_count,
                "timestamp": time.time(),
            }
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(bus.publish("DRIVE_SATURATED", payload))
        except Exception:
            pass  # Fallback silencieux : pas bloquer la boucle pulsionnelle

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
        """Bonus [0, +3.0] base sur les pulsions les plus pressantes.

        Phase C Etape 4c-3b (2026-04-14) : migration Strangler Fig du
        scoring drive-bonus vers drive_routine_registry.

        Calibration du multiplicateur :
        - V1 : affinity [0.0, 1.8] * 1.5 -> bonus max = 2.7 (quasi sature)
        - V3 : affinity [0.0, 1.0] * 3.0 -> bonus max = 3.0 (sature)

        La valeur 3.0 compense la reduction de l'amplitude max (1.8 -> 1.0)
        pour preserver la pression des pulsions sur le scoring. Sans cette
        calibration, Promethee deviendrait apathique (chute de ~50% de la
        pression pulsion). Validation adversariale Gemini 2026-04-14.

        Hot path : cette fonction est appelee une fois par intent candidat
        dans la boucle de scoring (~30 appels/cycle). Elle utilise
        get_affinity_for_drive_intent() qui est O(1), pas
        get_routines_for_drive_live() qui ferait un tri inutile.
        """
        bonus = 0.0
        from core.drive_routine_registry import get_affinity_for_drive_intent

        for drive in self.drives.values():
            if drive.deprivation <= 30:
                continue  # pas assez frustre pour contribuer

            affinity = get_affinity_for_drive_intent(drive.name, intent)

            if affinity > 0:
                urgency = (drive.deprivation - 30) / 70  # 0.0 a 1.0
                # Multiplicateur 3.0 calibre pour compenser l'amplitude
                # max reduite du registre (0.9 au lieu de 1.8).
                bonus += urgency * affinity * 3.0
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
        """Charge l'etat depuis le fichier JSON.

        V35.1 — Les drives externally-driven (REPOS) ne sont JAMAIS
        restaures depuis le fichier : leur etat persiste dans leur propre
        fichier d'organe (thermal_state.json pour REPOS), pas ici. On
        garde l'instance fraiche creee par _make_drive (deprivation=0.0),
        que l'organe regulateur ecrira au boot.
        """
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            drives_data = data.get("drives", {})
            for name in DRIVE_NAMES:
                if name in EXTERNALLY_DRIVEN_DRIVES:
                    # Skip : etat reste celui de _make_drive (depriv=0.0)
                    continue
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
            self._stab_shadow_depriv = data.get("stab_shadow_depriv", None)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self):
        """Sauvegarde atomique de l'etat."""
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        data = {
            "version": "1.0",
            "last_tick": self._last_tick,
            "stab_shadow_depriv": round(self._stab_shadow_depriv, 2) if self._stab_shadow_depriv is not None else None,
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
