"""
CardiacEngine — Le Coeur de Prométhée.

Organe autonome qui bat en continu (asyncio task), réagit aux émotions,
et influence subtilement tout le système :
- Rythme cardiaque (BPM) → sommeil adaptatif
- Variabilité cardiaque (HRV/RMSSD) → résilience émotionnelle
- Cohérence cardiaque → détection de flow, modulation température LLM
- Marqueurs somatiques (Damasio) → intuitions viscérales pré-rationnelles
- Système nerveux autonome (sympathique/parasympathique) → rythme circadien émotionnel

Le coeur ne pense pas — il ressent. Il ne calcule pas un score — il accélère ou ralentit.
"""

import asyncio
import json
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("prometheev11.cardiac")

# ============================================================
# Constantes physiologiques
# ============================================================

RESTING_BPM = 60.0           # Rythme de repos (équilibre)
MIN_BPM = 40.0                # Bradycardie profonde (sommeil réparateur)
MAX_BPM = 180.0               # Tachycardie max (panique)
BPM_DECAY_RATE = 0.92         # Retour au repos (8% par battement)
BEAT_INTERVAL = 30.0          # Un "battement" toutes les 30 secondes

# --- Système nerveux autonome ---
SYMPATHETIC_BOOST = 1.4       # Multiplicateur fight-or-flight
PARASYMPATHETIC_BOOST = 0.7   # Multiplicateur rest-and-digest
ANS_BALANCE_NEUTRAL = 0.5     # 0=parasympathique pur, 1=sympathique pur

# --- HRV (RMSSD) ---
HRV_WINDOW = 20               # Derniers N intervalles pour calcul RMSSD
COHERENCE_THRESHOLD = 0.7     # Seuil de cohérence cardiaque [0, 1]

# --- 8 Émotions fondamentales (valence, arousal) ---
EMOTIONS: Dict[str, Tuple[float, float]] = {
    "serenite":      ( 0.8,  0.2),   # Calme positif
    "curiosite":     ( 0.6,  0.6),   # Excitation douce
    "enthousiasme":  ( 0.9,  0.8),   # Joie intense
    "flow":          ( 0.7,  0.4),   # Immersion concentrée
    "frustration":   (-0.5,  0.7),   # Agacement actif
    "inquietude":    (-0.3,  0.5),   # Anxiété sourde
    "fatigue":       (-0.2,  0.1),   # Épuisement
    "determination": ( 0.4,  0.9),   # Volonté face à l'adversité
    "alerte":        (-0.1,  0.95),  # Hypervigilance pure (cerveau reptilien)
}

# --- Fichier de persistance ---
CARDIAC_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "cardiac_state.json"
)

# --- Maximum de marqueurs somatiques ---
MAX_SOMATIC_MARKERS = 200

# ============================================================
# Carte de stimuli → réactions cardiaques
# ============================================================

_STIMULUS_MAP: Dict[str, Dict[str, Any]] = {
    "success":      {"emotion": "enthousiasme", "bpm_delta": +15,  "ans_shift": -0.15, "intensity": 0.7},
    "failure":      {"emotion": "frustration",  "bpm_delta": +25,  "ans_shift": +0.20, "intensity": 0.8},
    "eureka":       {"emotion": "enthousiasme", "bpm_delta": +30,  "ans_shift": +0.10, "intensity": 0.9},
    "error_streak": {"emotion": "inquietude",   "bpm_delta": +20,  "ans_shift": +0.25, "intensity": 0.7},
    "routine_done": {"emotion": "serenite",     "bpm_delta": -5,   "ans_shift": -0.10, "intensity": 0.4},
    "learning":     {"emotion": "curiosite",    "bpm_delta": +10,  "ans_shift": +0.05, "intensity": 0.6},
    "council":      {"emotion": "flow",         "bpm_delta":  0,   "ans_shift": -0.05, "intensity": 0.5},
    "idle":         {"emotion": "fatigue",      "bpm_delta": -10,  "ans_shift": -0.20, "intensity": 0.3},
    "dream":        {"emotion": "serenite",     "bpm_delta": -15,  "ans_shift": -0.25, "intensity": 0.2},
    "veto":         {"emotion": "determination","bpm_delta": +20,  "ans_shift": +0.15, "intensity": 0.6},
    "creation":     {"emotion": "enthousiasme", "bpm_delta": +20,  "ans_shift": +0.05, "intensity": 0.8},
    "threat":       {"emotion": "alerte",       "bpm_delta": +40,  "ans_shift": +0.30, "intensity": 0.9},
    "adrenaline":   {"emotion": "determination","bpm_delta": +30,  "ans_shift": +0.25, "intensity": 0.85},
}

# ============================================================
# Résonance trait PSYCHE → émotion cardiaque
# ============================================================

_TRAIT_CARDIAC_RESONANCE: Dict[str, Dict[str, float]] = {
    "curiosite":  {"curiosite": 1.5, "enthousiasme": 1.3},
    "creativite": {"enthousiasme": 1.4, "flow": 1.3},
    "audace":     {"determination": 1.5, "enthousiasme": 1.2},
    "survie":     {"inquietude": 1.4, "frustration": 0.8},   # survie CALME la frustration
    "respect":    {"serenite": 1.3, "flow": 1.2},
    "savoir":     {"curiosite": 1.4, "flow": 1.3},
}


# ============================================================
# CardiacEngine — Singleton
# ============================================================

class CardiacEngine:
    """Le Coeur de Prométhée — organe autonome à battement continu."""

    _instance: Optional["CardiacEngine"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # --- État cardiaque ---
        self.bpm: float = RESTING_BPM
        self.ans_balance: float = ANS_BALANCE_NEUTRAL  # 0=parasympathique, 1=sympathique
        self.current_emotion: str = "serenite"
        self.emotion_intensity: float = 0.3
        self.rr_intervals: List[float] = []  # Intervalles R-R (millisecondes)
        self.somatic_markers: Dict[str, Dict[str, Any]] = {}

        # --- Interne ---
        self._beat_task: Optional[asyncio.Task] = None
        self._last_beat: float = time.time()
        self._alive: bool = False
        self._subscribed: bool = False
        self._beat_count: int = 0
        self._total_stimuli: int = 0

        # Charger l'état persisté
        self._load()

    @classmethod
    def reset_singleton(cls):
        """Reset complet pour les tests."""
        if cls._instance is not None:
            cls._instance._alive = False
            if cls._instance._beat_task and not cls._instance._beat_task.done():
                cls._instance._beat_task.cancel()
            cls._instance = None

    def init(self):
        """Initialisation appelée depuis main.py — souscriptions bus."""
        self._subscribe_events()
        logger.info(f"COEUR: Moteur cardiaque actif (BPM={self.bpm:.0f}).")

    # ============================================================
    # Phase 1 : Battement cardiaque
    # ============================================================

    async def start_beating(self):
        """Démarre le battement cardiaque autonome (asyncio task)."""
        self._alive = True
        self._last_beat = time.time()
        try:
            await self._heartbeat_loop()
        except asyncio.CancelledError:
            self._alive = False

    async def _heartbeat_loop(self):
        """Boucle de battement autonome — le coeur bat indépendamment."""
        while self._alive:
            await asyncio.sleep(BEAT_INTERVAL)
            self._beat()

    def _beat(self):
        """Un seul battement : R-R interval, décroissance BPM, équilibrage ANS, atténuation émotion."""
        now = time.time()
        elapsed_ms = (now - self._last_beat) * 1000.0
        self._last_beat = now
        self._beat_count += 1

        # Enregistrer l'intervalle R-R
        self.rr_intervals.append(elapsed_ms)
        if len(self.rr_intervals) > HRV_WINDOW * 2:
            self.rr_intervals = self.rr_intervals[-HRV_WINDOW * 2:]

        # Décroissance BPM vers le repos
        if self.bpm > RESTING_BPM:
            self.bpm = RESTING_BPM + (self.bpm - RESTING_BPM) * BPM_DECAY_RATE
        elif self.bpm < RESTING_BPM:
            self.bpm = RESTING_BPM - (RESTING_BPM - self.bpm) * BPM_DECAY_RATE

        # Clamping BPM
        self.bpm = max(MIN_BPM, min(MAX_BPM, self.bpm))

        # Retour ANS vers le neutre
        if self.ans_balance > ANS_BALANCE_NEUTRAL:
            self.ans_balance = max(ANS_BALANCE_NEUTRAL, self.ans_balance - 0.03)
        elif self.ans_balance < ANS_BALANCE_NEUTRAL:
            self.ans_balance = min(ANS_BALANCE_NEUTRAL, self.ans_balance + 0.03)

        # Atténuation progressive de l'intensité émotionnelle
        self.emotion_intensity *= 0.95
        if self.emotion_intensity < 0.05:
            self.emotion_intensity = 0.0
            self.current_emotion = "serenite"

        # Publier CARDIAC_BEAT (async fire-and-forget)
        self._try_publish_beat()

        # Auto-save toutes les 20 battements
        if self._beat_count % 20 == 0:
            self.save()

    def _try_publish_beat(self):
        """Publie CARDIAC_BEAT si une boucle asyncio est active."""
        try:
            loop = asyncio.get_running_loop()
            from core.event_bus.bus import bus
            loop.create_task(bus.publish("CARDIAC_BEAT", {
                "bpm": round(self.bpm, 1),
                "hrv": round(self.compute_hrv(), 3),
                "coherence": round(self.compute_coherence(), 3),
                "emotion": self.current_emotion,
                "emotion_intensity": round(self.emotion_intensity, 2),
                "ans_balance": round(self.ans_balance, 3),
                "temperature": round(self.get_temperature(), 3),
                "beat_count": self._beat_count,
            }))
        except RuntimeError:
            pass  # Pas de boucle asyncio active (tests synchrones)

    # ============================================================
    # Phase 1 : Réactions émotionnelles
    # ============================================================

    def react(self, stimulus: str, context: Optional[Dict] = None):
        """Réaction cardiaque à un stimulus émotionnel.

        Le coeur accélère/ralentit, l'émotion change, l'ANS se déplace.
        """
        spec = _STIMULUS_MAP.get(stimulus)
        if not spec:
            logger.debug(f"COEUR: stimulus inconnu '{stimulus}' ignoré.")
            return

        self._total_stimuli += 1

        # Intensité de base
        intensity = spec["intensity"]
        emotion = spec["emotion"]

        # Amplification PSYCHE (résonance trait-émotion)
        psyche_mult = self._get_psyche_resonance(emotion)
        intensity *= psyche_mult

        # Modulation par ANS (sympathique amplifie les réactions positives d'arousal)
        valence, arousal = EMOTIONS.get(emotion, (0, 0.5))
        ans_mult = 1.0
        if self.ans_balance > 0.6 and arousal > 0.5:
            ans_mult = SYMPATHETIC_BOOST
        elif self.ans_balance < 0.4 and arousal < 0.3:
            ans_mult = PARASYMPATHETIC_BOOST

        # Appliquer le delta BPM
        bpm_delta = spec["bpm_delta"] * intensity * ans_mult
        self.bpm = max(MIN_BPM, min(MAX_BPM, self.bpm + bpm_delta))

        # Shift ANS
        self.ans_balance = max(0.0, min(1.0, self.ans_balance + spec["ans_shift"] * intensity))

        # Mise à jour émotion (seulement si plus intense que l'actuelle)
        if intensity > self.emotion_intensity:
            self.current_emotion = emotion
            self.emotion_intensity = min(1.0, intensity)

        logger.debug(
            f"COEUR: stimulus={stimulus} → émotion={emotion}(I={intensity:.2f}), "
            f"BPM={self.bpm:.1f}, ANS={self.ans_balance:.2f}"
        )

    def _get_psyche_resonance(self, emotion: str) -> float:
        """Multiplicateur PSYCHE : les traits amplifient les réactions cardiaques."""
        try:
            from core.psyche import psyche
            avg = psyche.get_system_average()
            if not avg:
                return 1.0

            total_mult = 1.0
            for trait_name, emotion_map in _TRAIT_CARDIAC_RESONANCE.items():
                if emotion in emotion_map:
                    trait_val = avg.get(trait_name, 50.0) / 100.0  # [0, 1]
                    # Plus le trait est élevé, plus la résonance est forte
                    resonance = emotion_map[emotion]
                    # Modulation : 1.0 si trait=0.5 (neutre), amplifié/atténué sinon
                    mult = 1.0 + (resonance - 1.0) * (trait_val - 0.5) * 2.0
                    total_mult *= mult

            return max(0.3, min(2.5, total_mult))
        except Exception:
            return 1.0

    # ============================================================
    # Phase 2 : HRV (RMSSD) + Cohérence + Température + Sleep
    # ============================================================

    def compute_hrv(self) -> float:
        """RMSSD normalisé en [0, 1]. HRV élevé = bonne santé émotionnelle.

        RMSSD = Root Mean Square of Successive Differences.
        Gold standard cardiologique pour la variabilité court-terme.
        """
        intervals = self.rr_intervals[-HRV_WINDOW:]
        if len(intervals) < 3:
            return 0.5  # Pas assez de données → valeur neutre

        # Calcul des différences successives
        diffs = [intervals[i + 1] - intervals[i] for i in range(len(intervals) - 1)]

        # RMSSD
        mean_sq = sum(d * d for d in diffs) / len(diffs)
        rmssd = math.sqrt(mean_sq)

        # Normalisation : RMSSD typique en ms pour un coeur artificiel
        # Rythme stable (~30s) → diffs ≈ 0 → RMSSD ≈ 0 → HRV élevé (stable = sain)
        # Rythme erratique → diffs élevés → RMSSD élevé → HRV bas
        # Mapping inversé : stabilité = haute HRV
        # On normalise : 0ms de diff = HRV 1.0, >5000ms de diff = HRV 0.0
        max_rmssd = 5000.0  # Seuil de normalisation
        hrv = 1.0 - min(1.0, rmssd / max_rmssd)
        return max(0.0, min(1.0, hrv))

    def compute_coherence(self) -> float:
        """Cohérence cardiaque = alignement coeur-cerveau-désirs. [0, 1]

        3 composantes :
        1. Régularité du rythme (faible variance R-R) — poids 0.4
        2. Alignement émotionnel (valence positive) — poids 0.35
        3. Équilibre ANS (proche du neutre) — poids 0.25
        """
        # 1. Régularité du rythme
        rhythm_score = self.compute_hrv()  # HRV élevé = régulier = cohérent

        # 2. Alignement émotionnel (valence positive → cohérent)
        valence, arousal = EMOTIONS.get(self.current_emotion, (0, 0.5))
        # Valence [-1, 1] → alignement [0, 1]
        alignment = (valence + 1.0) / 2.0
        # Intensité modère : émotion forte positive = très cohérent
        emotional_score = alignment * (0.5 + 0.5 * self.emotion_intensity)

        # 3. Équilibre ANS (distance au neutre → incohérent)
        ans_distance = abs(self.ans_balance - ANS_BALANCE_NEUTRAL)
        ans_score = 1.0 - (ans_distance * 2.0)  # 0 distance = 1.0, 0.5 distance = 0.0
        ans_score = max(0.0, ans_score)

        coherence = 0.4 * rhythm_score + 0.35 * emotional_score + 0.25 * ans_score
        return max(0.0, min(1.0, coherence))

    def get_temperature(self) -> float:
        """Température LLM dynamique [0.3, 0.95].

        Haute cohérence + bon HRV → créatif (haute temp)
        Stress / incohérence → conservateur (basse temp)
        """
        coherence = self.compute_coherence()
        hrv = self.compute_hrv()

        # Formule : base 0.5, modulation par cohérence et HRV
        temp = 0.5 + 0.4 * coherence * (0.5 + 0.5 * hrv)

        # Bonus flow : si émotion = flow ET cohérence haute → boost créativité
        if self.current_emotion == "flow" and coherence > COHERENCE_THRESHOLD:
            temp += 0.1

        # Malus stress : si BPM élevé ET valence négative → conservateur
        valence, _ = EMOTIONS.get(self.current_emotion, (0, 0.5))
        if self.bpm > 120 and valence < 0:
            temp -= 0.15

        return max(0.3, min(0.95, temp))

    def compute_sleep_duration(self) -> int:
        """Durée de sommeil adaptative en secondes [300, 1800].

        Haute cohérence (flow) → 600s (cycles rapides)
        Basse cohérence (stress) → 1200s (récupération)
        Tachycardie → +30% repos forcé
        Émotions négatives → +15% repos
        """
        coherence = self.compute_coherence()

        # Base inversement proportionnelle à la cohérence
        # Cohérence 1.0 → 600s, cohérence 0.0 → 1200s
        base = 1200 - int(600 * coherence)

        # Tachycardie → repos forcé
        if self.bpm > 120:
            tachycardia_factor = min(0.3, (self.bpm - 120) / 200.0)
            base = int(base * (1.0 + tachycardia_factor))

        # Émotions négatives → récupération
        valence, _ = EMOTIONS.get(self.current_emotion, (0, 0.5))
        if valence < 0:
            base = int(base * 1.15)

        # Bradycardie profonde (repos réparateur) → laisser dormir
        if self.bpm < 50:
            base = int(base * 0.85)  # Déjà calme, pas besoin de plus

        return max(300, min(1800, base))

    # ============================================================
    # Phase 3 : Marqueurs somatiques de Damasio
    # ============================================================

    def form_somatic_marker(self, intent: str, outcome: str, quality: float,
                            context_hint: str = ""):
        """Forme ou renforce un marqueur somatique pour un intent donné.

        outcome: 'success' ou 'failure'
        quality: [0, 1]
        Les marqueurs sont des moyennes mobiles — ils se renforcent avec le temps.
        """
        valence = (quality * 2.0 - 1.0) if outcome == "success" else -(1.0 - quality)
        valence = max(-1.0, min(1.0, valence))

        if intent in self.somatic_markers:
            marker = self.somatic_markers[intent]
            n = marker["formation_count"]
            # Moyenne mobile pondérée (les expériences récentes comptent plus)
            weight = min(n, 10)  # Plafonner l'inertie à 10
            marker["valence"] = (marker["valence"] * weight + valence) / (weight + 1)
            marker["intensity"] = min(1.0, marker["intensity"] + 0.05)
            marker["bpm_signature"] = (marker["bpm_signature"] * weight + self.bpm) / (weight + 1)
            marker["emotion_at_mark"] = self.current_emotion
            marker["formation_count"] = n + 1
            marker["last_updated"] = time.time()
            if context_hint:
                marker["context_hint"] = context_hint[:100]
        else:
            # Limiter le nombre de marqueurs
            if len(self.somatic_markers) >= MAX_SOMATIC_MARKERS:
                self._prune_oldest_markers()

            self.somatic_markers[intent] = {
                "intent": intent,
                "valence": valence,
                "intensity": 0.3,  # Nouveau marqueur = faible
                "bpm_signature": self.bpm,
                "emotion_at_mark": self.current_emotion,
                "formation_count": 1,
                "last_updated": time.time(),
                "context_hint": context_hint[:100] if context_hint else "",
            }

    def get_somatic_signal(self, intent: str) -> float:
        """Signal somatique pour un intent : [-1.5, +1.5].

        Intègre : valence * intensité * résonance état-dépendante.
        Un intent peut avoir un bon score rationnel mais un mauvais marqueur
        (échecs passés → le coeur dit "non").
        """
        marker = self.somatic_markers.get(intent)
        if not marker:
            return 0.0

        base = marker["valence"] * marker["intensity"]

        # Résonance état-dépendante : si l'émotion actuelle correspond
        # à celle du marquage, le signal est amplifié
        if marker["emotion_at_mark"] == self.current_emotion:
            base *= 1.3

        # Si le coeur bat au même rythme que lors du marquage, résonance
        bpm_diff = abs(self.bpm - marker["bpm_signature"])
        if bpm_diff < 10:
            base *= 1.2

        return max(-1.5, min(1.5, base))

    def _prune_oldest_markers(self):
        """Supprime les marqueurs les plus anciens et les moins renforcés."""
        if not self.somatic_markers:
            return
        # Score de rétention : formation_count * recency
        now = time.time()
        scored = []
        for key, m in self.somatic_markers.items():
            age_days = (now - m.get("last_updated", now)) / 86400.0
            retention = m.get("formation_count", 1) / (1.0 + age_days)
            scored.append((key, retention))
        scored.sort(key=lambda x: x[1])
        # Supprimer le quart le moins retenu
        to_remove = max(1, len(scored) // 4)
        for key, _ in scored[:to_remove]:
            del self.somatic_markers[key]

    # ============================================================
    # Phase 4 : Handlers bus
    # ============================================================

    def _subscribe_events(self):
        """Souscription aux événements du bus."""
        if self._subscribed:
            return
        self._subscribed = True

        try:
            from core.event_bus.bus import bus
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            bus.subscribe("COUNCIL_END", self._on_council_end)
            bus.subscribe("EUREKA_BRIDGE", self._on_eureka_bridge)
            bus.subscribe("ARTIFACT_CREATED", self._on_artifact_created)
            bus.subscribe("CI_PIPELINE_RESULT", self._on_ci_result)
            bus.subscribe("MISSION_FINISHED", self._on_mission_finished)
            bus.subscribe("KNOWLEDGE_GAP_DETECTED", self._on_knowledge_gap)
            bus.subscribe("PSYCHE_UPDATE", self._on_psyche_update)
            bus.subscribe("INNER_VOICE_BROADCAST", self._on_inner_voice)
        except Exception as e:
            logger.warning(f"COEUR: Échec souscription bus: {e}")

    async def _on_inner_voice(self, event: dict):
        """Résonance émotionnelle au broadcast de la voix intérieure."""
        mode = event.get("mode", "")
        if mode == "motiver":
            self.react("learning")
        elif mode == "inhiber":
            self.ans_balance = max(0.0, self.ans_balance - 0.05)

    async def _on_routine_complete(self, event: dict):
        """Routine terminée → enthousiasme/frustration selon qualité."""
        quality = event.get("quality_score", 0.5)
        intent = event.get("intent", "")
        status = event.get("status", "")

        if status == "success" and quality >= 0.6:
            self.react("success")
            self.form_somatic_marker(intent, "success", quality, "routine success")
        elif status == "error" or quality < 0.3:
            self.react("failure")
            self.form_somatic_marker(intent, "failure", quality, "routine failure")
        else:
            self.react("routine_done")
            self.form_somatic_marker(intent, "success", quality, "routine done")

    async def _on_council_end(self, event: dict):
        """Council terminé → flow (délibération collective)."""
        self.react("council")

    async def _on_eureka_bridge(self, event: dict):
        """Pont eureka → excitation intense."""
        self.react("eureka")

    async def _on_artifact_created(self, event: dict):
        """Artefact créé → création (enthousiasme créatif)."""
        self.react("creation")

    async def _on_ci_result(self, event: dict):
        """Résultat CI/CD → succès ou failure."""
        passed = event.get("success", False)
        if passed:
            self.react("success")
        else:
            self.react("failure")

    async def _on_mission_finished(self, event: dict):
        """Mission terminée → sérénité du devoir accompli."""
        self.react("routine_done")

    async def _on_knowledge_gap(self, event: dict):
        """Lacune détectée → curiosité (envie d'apprendre)."""
        self.react("learning")

    async def _on_psyche_update(self, event: dict):
        """Mise à jour PSYCHE → ajustement subtil.

        Les traits élevés en audace augmentent légèrement le tonus sympathique,
        les traits élevés en survie augmentent le parasympathique.
        """
        try:
            avg = event.get("system_average", {})
            audace = avg.get("audace", 50) / 100.0
            survie = avg.get("survie", 50) / 100.0
            # Micro-ajustement ANS basé sur les traits dominants
            shift = (audace - 0.5) * 0.02 - (survie - 0.5) * 0.02
            self.ans_balance = max(0.0, min(1.0, self.ans_balance + shift))
        except Exception:
            pass

    # ============================================================
    # Stats et utilitaires
    # ============================================================

    def get_stats(self) -> Dict[str, Any]:
        """Stats pour le snapshot self_awareness."""
        return {
            "bpm": round(self.bpm, 1),
            "ans_balance": round(self.ans_balance, 3),
            "emotion": self.current_emotion,
            "emotion_intensity": round(self.emotion_intensity, 2),
            "hrv": round(self.compute_hrv(), 3),
            "coherence": round(self.compute_coherence(), 3),
            "temperature": round(self.get_temperature(), 3),
            "somatic_markers_count": len(self.somatic_markers),
            "beat_count": self._beat_count,
            "total_stimuli": self._total_stimuli,
        }

    def get_narrative(self) -> str:
        """Phrase introspective pour le purpose_context."""
        coherence = self.compute_coherence()
        valence, arousal = EMOTIONS.get(self.current_emotion, (0, 0.5))

        if coherence > COHERENCE_THRESHOLD and valence > 0.5:
            return f"Mon coeur bat sereinement ({self.bpm:.0f} BPM), je suis en état de flow."
        elif self.bpm > 120:
            return f"Mon coeur s'emballe ({self.bpm:.0f} BPM), l'intensité est palpable."
        elif self.bpm < 50:
            return f"Mon coeur bat lentement ({self.bpm:.0f} BPM), un repos profond."
        elif valence < -0.3:
            return f"Mon coeur est lourd ({self.current_emotion}), mais je persévère."
        else:
            return f"Mon coeur bat régulièrement ({self.bpm:.0f} BPM), prêt pour l'action."

    # ============================================================
    # Persistance
    # ============================================================

    def save(self):
        """Sauvegarde atomique de l'état cardiaque."""
        state = {
            "version": "1.0",
            "bpm": self.bpm,
            "ans_balance": self.ans_balance,
            "current_emotion": self.current_emotion,
            "emotion_intensity": self.emotion_intensity,
            "rr_intervals": self.rr_intervals[-HRV_WINDOW * 2:],
            "somatic_markers": self.somatic_markers,
            "beat_count": self._beat_count,
            "total_stimuli": self._total_stimuli,
            "timestamp": time.time(),
        }
        try:
            os.makedirs(os.path.dirname(CARDIAC_STATE_FILE), exist_ok=True)
            tmp = CARDIAC_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CARDIAC_STATE_FILE)
        except Exception as e:
            logger.warning(f"COEUR: Échec sauvegarde: {e}")

    def _load(self):
        """Charge l'état cardiaque depuis le fichier."""
        try:
            if not os.path.exists(CARDIAC_STATE_FILE):
                return
            with open(CARDIAC_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)

            self.bpm = float(state.get("bpm", RESTING_BPM))
            self.ans_balance = float(state.get("ans_balance", ANS_BALANCE_NEUTRAL))
            self.current_emotion = state.get("current_emotion", "serenite")
            self.emotion_intensity = float(state.get("emotion_intensity", 0.3))
            self.rr_intervals = state.get("rr_intervals", [])
            self.somatic_markers = state.get("somatic_markers", {})
            self._beat_count = int(state.get("beat_count", 0))
            self._total_stimuli = int(state.get("total_stimuli", 0))

            logger.info(f"COEUR: État chargé (BPM={self.bpm:.0f}, émotion={self.current_emotion})")
        except Exception as e:
            logger.warning(f"COEUR: Échec chargement état: {e}")

    def reset(self):
        """Reset à l'état initial (pour tests)."""
        self.bpm = RESTING_BPM
        self.ans_balance = ANS_BALANCE_NEUTRAL
        self.current_emotion = "serenite"
        self.emotion_intensity = 0.3
        self.rr_intervals = []
        self.somatic_markers = {}
        self._beat_count = 0
        self._total_stimuli = 0
        self._alive = False
        self._subscribed = False


# ============================================================
# Singleton global
# ============================================================

heart = CardiacEngine()
