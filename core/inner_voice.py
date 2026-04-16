"""
inner_voice.py — Aires de Broca & Wernicke — Le Flux de Conscience de Prométhée

Combine 6 principes neuroscientifiques :
1. Broca/Wernicke : Formulation compressée + vérification sémantique
2. Vygotsky : Parole intérieure prédicative comme régulateur comportemental
3. Default Mode Network : Vagabondage mental actif pendant l'idle
4. Predictive Processing (Friston) : Prédictions continues + erreurs de prédiction
5. Conscience narrative (Dennett) : Brouillons multiples + identité narrative
6. Global Workspace (Baars) : Compétition + seuil d'ignition + broadcast
"""

import asyncio
import json
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("inner_voice")

# ─── Constantes ───────────────────────────────────────────────────────────────

IGNITION_THRESHOLD = 0.4            # Seuil de saillance pour broadcast
MAX_STREAM = 200                    # Buffer du flux de conscience (FIFO)
MAX_PREDICTIONS = 30                # Prédictions actives
MAX_DRAFTS = 5                      # Brouillons simultanés (Dennett)
PREDICTION_DECAY = 0.95             # Confiance diminue par tick
IDENTITY_REFRESH_INTERVAL = 600.0   # Refresh identité narrative (10 min)
WERNICKE_COHERENCE_THRESHOLD = 0.3  # En-dessous → reformulation
PREDICTION_EXPIRY = 3600.0          # 1h max pour une prédiction

# --- DAC : Détection d'Anomalie Cognitive (proxy KL-divergence / surprise) ---
# Résout la faiblesse S9 du cours de soutien "incapacité à la surprise".
# Calcule une mesure de divergence entre prédiction et observation, pondérée
# par la confiance. Une prédiction très confiante qui échoue → surprise maximale.
DAC_SURPRISE_THRESHOLD = 0.4        # Au-dessus → flag is_surprise
DAC_HIGH_CONFIDENCE = 0.7           # Confiance au-dessus → surprise amplifiée
DAC_SALIENCE_BOOST = 0.4            # Boost de saillance dans workspace si surprise


def _tokenize_for_dac(text: str) -> set:
    """Tokenisation simple pour calculer distance de Jaccard sur contenu."""
    if not text:
        return set()
    return {t.lower() for t in text.replace("_", " ").split() if len(t) > 1}


def _compute_surprise(predicted: str, observed: str, confidence: float) -> float:
    """Calcule un proxy de KL-divergence (∈ [0,1]) entre prédiction et observation.

    Basé sur : surprise = jaccard_distance(predicted, observed) × confidence.
    - Prédiction haute-confiance qui échoue → surprise haute.
    - Prédiction basse-confiance qui échoue → surprise faible (normal).
    - Prédiction confirmée → surprise ~0.

    Limitation V1 : proxy textuel (Jaccard), pas une vraie KL-divergence sur
    distributions de probabilités. Dette V2 documentée dans FINDINGS.md.
    """
    p_tokens = _tokenize_for_dac(predicted)
    o_tokens = _tokenize_for_dac(observed)
    if not p_tokens and not o_tokens:
        return 0.0
    union = p_tokens | o_tokens
    inter = p_tokens & o_tokens
    jaccard = len(inter) / len(union) if union else 0.0
    distance = 1.0 - jaccard
    # Amplification si haute confiance
    amp = 1.0
    if confidence >= DAC_HIGH_CONFIDENCE:
        amp = 1.0 + (confidence - DAC_HIGH_CONFIDENCE)
    return max(0.0, min(1.0, distance * confidence * amp))

INNER_VOICE_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "inner_voice_state.json"
)

# Poids de base pour la compétition du workspace
SOURCE_BASE_WEIGHTS = {
    "cardiac":    0.6,
    "reptilian":  0.9,
    "desire":     0.5,
    "synaptic":   0.4,
    "prefrontal": 0.6,
    "prediction": 0.5,
    "dmn":        0.2,
    "sensorium":  0.5,
}

# Valence des émotions cardiaques (pour cohérence Wernicke)
_EMOTION_VALENCE = {
    "serenite": 0.7, "curiosite": 0.6, "enthousiasme": 0.9,
    "flow": 0.8, "determination": 0.6,
    "frustration": -0.5, "inquietude": -0.4, "fatigue": -0.3,
    "alerte": -0.2,
}

# Mapping source Inner Voice → categorie thalamique (pour modulation focus)
_SOURCE_TO_THALAMUS_CATEGORY = {
    "reptilian":  "urgence",
    "cardiac":    "regulation",
    "desire":     "motivation",
    "synaptic":   "emergence",
    "prefrontal": "cognition",
    "prediction": "cognition",
    "dmn":        "emergence",
    "sensorium":  "regulation",
}

# Templates Broca (source, contexte) → template
_BROCA_TEMPLATES = {
    ("cardiac", "high_intensity"): "{emotion}. {direction}.",
    ("cardiac", "flow"):          "Flow. Ne pas interrompre.",
    ("cardiac", "shift"):         "{prev_emotion} -> {emotion}.",
    ("cardiac", "neutral"):       "Coeur calme. {emotion}.",
    ("reptilian", "threat"):      "Menace {level:.0f}. {reflex}.",
    ("reptilian", "calm"):        "Calme. Menace retombee.",
    ("desire", "frustrated"):     "{drive} affame ({deprivation:.0f}). {action}.",
    ("desire", "emerging"):       "{drive} emerge.",
    ("desire", "rising"):         "{drive} monte ({deprivation:.0f}).",
    ("synaptic", "unexpected"):   "Tiens. {concept_a} <-> {concept_b}.",
    ("synaptic", "recurring"):    "{concept} revient. Important?",
    ("synaptic", "active"):       "{concept} actif.",
    ("prefrontal", "progress"):   "Goal '{title}' a {progress:.0%}. {next_step}.",
    ("prefrontal", "inhibition"): "Non. Pas {intent}. Focus.",
    ("prefrontal", "active"):     "Focus: {title}.",
    ("prediction", "error"):      "Prevu {predicted}. Reel: {actual}. Comprendre.",
    ("prediction", "confirmed"):  "Confiance. {content} confirme.",
    ("prediction", "pending"):    "Attente: {content}.",
    ("dmn", "retrospect"):        "{count} derniers {intent}: {pattern}.",
    ("dmn", "prospect"):          "Et si {scenario}?",
    ("dmn", "self"):              "{mood}. {competence}.",
    ("dmn", "wander"):            "{concept_a}... {concept_b}... lien?",
    ("sensorium", "somatic"):       "{content}.",
    ("metacognition", "neutral"):    "Je remarque: {insight}.",
}

_MOOD_PREFIXES = {
    "dreaming":      ["Je rêve... ", "Dans le brouillard... ", "Une image... "],
    "tension_high":  ["! ", "Vite. ", "Alerte. "],
    "entropy_high":  ["Hmm... ", "Et si... ", "Drôle... "],
    "vitality_low":  ["... ", "(soupir) "],
}


# ─── Structures de données ────────────────────────────────────────────────────

@dataclass
class WorkspaceEntry:
    """Un candidat dans le Global Workspace — compète pour le broadcast."""
    source: str
    raw_signal: Dict
    salience: float
    timestamp: float
    draft: str = ""
    verified: bool = False
    coherence_score: float = 0.0


@dataclass
class Thought:
    """Une pensée diffusée — sortie du workspace après ignition."""
    timestamp: float
    content: str
    source: str
    mode: str
    salience: float
    emotion: str = ""
    prediction_id: str = ""


@dataclass
class Prediction:
    """Une prédiction active — Friston predictive processing."""
    id: str
    content: str
    target_event: str
    predicted_value: str
    confidence: float
    created_at: float
    resolved: bool = False
    outcome: str = ""
    prediction_error: float = 0.0


@dataclass
class IdentityNarrative:
    """Le centre de gravité narratif — qui suis-je maintenant."""
    core_identity: str = "Je suis Promethee. Systeme autonome multi-agents."
    recent_arc: str = ""
    emotional_tone: str = ""
    competence_self: str = ""
    aspiration: str = ""
    updated_at: float = 0.0


# ─── Classe principale ────────────────────────────────────────────────────────

class InnerVoice:
    """Aires de Broca & Wernicke — Flux de conscience de Prométhée."""

    _instance: Optional["InnerVoice"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._subscribed = False
        self._alive = False

        # Flux de conscience
        self.stream: List[Thought] = []
        self.predictions: List[Prediction] = []
        self.identity: IdentityNarrative = IdentityNarrative()
        self.workspace: List[WorkspaceEntry] = []

        # État interne
        self._is_idle: bool = True
        self._precision: float = 0.5
        self._last_identity_refresh: float = 0.0
        self._dmn_mode_index: int = 0
        self._idle_since: float = time.time()
        self._tick_count: int = 0

        # Cache des derniers signaux reçus
        self._last_cardiac: Dict = {}
        self._last_reptilian: Dict = {}
        self._last_prefrontal_thought: Dict = {}
        self._last_routine_complete: Dict = {}
        self._last_heartbeat: Dict = {}
        self._last_reptilian_alert: Dict = {}

        # Profil de coloration tissulaire (entropie, tension, vitalite, is_dreaming)
        self._color_profile: Tuple[float, float, float, bool] = (0.5, 0.3, 0.5, False)

        # Sensorium (Sprint 6 Sensorium)
        self._last_sensorium_alert: Dict = {}

        # Metacognition — feedback de self_awareness
        self._metacognition_insight: str = ""
        self._metacognition_ts: float = 0.0

        # Stats
        self.stats: Dict = {
            "total_thoughts": 0,
            "total_predictions": 0,
            "predictions_confirmed": 0,
            "predictions_violated": 0,
            "predictions_expired": 0,
            "ignitions": 0,
            "rejections_wernicke": 0,
            "reformulations": 0,
            "dmn_activations": 0,
            "broadcast_count": 0,
        }

        self._load()

    @classmethod
    def reset_singleton(cls):
        if cls._instance is not None:
            cls._instance._alive = False
            cls._instance = None

    # ─── Initialisation ───────────────────────────────────────────────────

    def init(self):
        """Appelé depuis main.py — souscrit aux événements bus."""
        self._subscribe_events()
        self._alive = True
        # Injecter le recap de la session precedente depuis l'hippocampe
        try:
            from core.hippocampus import hippocampus
            recap = hippocampus.get_startup_recap()
            if recap:
                self._record_thought(recap[:500], source="hippocampus", mode="retrospect", salience=0.8)
        except Exception:
            pass
        logger.info("INNER_VOICE: Aires de Broca & Wernicke actives.")

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        try:
            from core.event_bus.bus import bus
            bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
            bus.subscribe("REPTILIAN_ALERT", self._on_reptilian_alert)
            bus.subscribe("REPTILIAN_STATE", self._on_reptilian_state)
            bus.subscribe("PREFRONTAL_THOUGHT", self._on_prefrontal_thought)
            bus.subscribe("PREFRONTAL_GOAL_CREATED", self._on_prefrontal_goal_event)
            bus.subscribe("PREFRONTAL_GOAL_COMPLETE", self._on_prefrontal_goal_event)
            bus.subscribe("PREFRONTAL_GOAL_ABANDONED", self._on_prefrontal_goal_event)
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            bus.subscribe("AUTONOMY_HEARTBEAT", self._on_autonomy_heartbeat)
            bus.subscribe("EUREKA_BRIDGE", self._on_eureka)
            bus.subscribe("KNOWLEDGE_GAP_DETECTED", self._on_knowledge_gap)
            # Phase D (2026-04-16) : le meta_observer injecte des pensees
            # source="meta" quand il detecte une anomalie metabolique.
            bus.subscribe("META_ANOMALY_DETECTED", self._on_meta_anomaly)
            bus.subscribe("HALLUCINATION_DETECTED", self._on_hallucination)
            bus.subscribe("SOLILOQUE_START", self._on_soliloque_start)
            bus.subscribe("SOLILOQUE_COMPLETE", self._on_soliloque_complete)
            bus.subscribe("DMN_THOUGHT", self._on_dmn_thought)
            bus.subscribe("DMN_INSIGHT", self._on_dmn_insight)
            # Sensorium (Sprint 6 Sensorium)
            bus.subscribe("SENSORIUM_THERMAL_CRITICAL", self._on_thermal_critical)
            bus.subscribe("SENSORIUM_THERMAL_ALERT", self._on_thermal_alert)
            bus.subscribe("SENSORIUM_SUFFOCATION", self._on_sensorium_suffocation)
            # Metacognition — feedback de self_awareness
            bus.subscribe("METACOGNITION_INSIGHT", self._on_metacognition_insight)
        except Exception as e:
            logger.warning(f"INNER_VOICE: subscribe failed: {e}")

    def stop(self):
        self._alive = False
        self.save()

    # ─── Bus Handlers (cache des signaux) ─────────────────────────────────

    async def _on_cardiac_beat(self, event: dict):
        """Trigger principal — la voix pulse au rythme du cœur."""
        self._last_cardiac = event
        if self._alive:
            await self._think_cycle()

    async def _on_reptilian_alert(self, event: dict):
        self._last_reptilian_alert = event
        self._last_reptilian = event
        # Résoudre les prédictions reptiliennes
        await self._resolve_reptilian_predictions(event)

    async def _on_reptilian_state(self, event: dict):
        self._last_reptilian = event
        # Réinitialiser l'alerte si la menace est retombée
        threat = event.get("threat_level", 0)
        if isinstance(threat, (int, float)) and threat <= 1:
            self._last_reptilian_alert = {}

    async def _on_prefrontal_thought(self, event: dict):
        self._last_prefrontal_thought = event

    async def _on_prefrontal_goal_event(self, event: dict):
        pass  # Capturable pour prédictions

    async def _on_routine_complete(self, event: dict):
        self._last_routine_complete = event
        self._is_idle = True
        self._idle_since = time.time()
        await self._resolve_predictions(event)

    async def _on_autonomy_heartbeat(self, event: dict):
        self._last_heartbeat = event

    async def _on_eureka(self, event: dict):
        pass  # Enrichit DMN vagabondage

    async def _on_knowledge_gap(self, event: dict):
        topic = event.get("topic", "")
        if topic:
            try:
                await bus.publish("CURIOSITY_SPARK", {"topic": topic})
            except Exception:
                pass

    async def _on_meta_anomaly(self, event: dict):
        """Phase D — Inception narrative : le meta_observer a detecte une anomalie.

        Injecte une pensee source='meta' a haute saillance. Ce canal VIP
        sera filtre par EVENING_REFLECTION pour construire la memoire
        autobiographique. Le prefrontal ne cree PAS de goal directement ;
        c'est le chemin somatique (cardiac inquietude) qui biaise le scoring.
        """
        description = event.get("description", "Anomalie metabolique detectee.")
        prescription = event.get("prescription", "")
        severity = float(event.get("severity", 0.5))
        content = description
        if prescription:
            content += f" Suggestion : {prescription}"
        thought = Thought(
            timestamp=time.time(),
            content=content,
            source="meta",
            salience=max(0.8, min(1.0, severity)),
            emotion="inquietude",
            mode="evaluer",
        )
        self.stream.append(thought)
        self.stats["total_thoughts"] = self.stats.get("total_thoughts", 0) + 1
        if len(self.stream) > MAX_STREAM:
            self.stream = self.stream[-MAX_STREAM:]

    async def _on_hallucination(self, event: dict):
        pass  # Enrichit prédictions

    async def _on_soliloque_start(self, event: dict):
        """Debut soliloque → suspension DMN."""
        self._is_idle = False

    async def _on_soliloque_complete(self, event: dict):
        """Fin soliloque → DMN en retrospection."""
        self._is_idle = True
        self._idle_since = time.time()

    async def _on_dmn_thought(self, event: dict):
        """Pensee spontanee du DMN → matiere a reflexion."""
        self._last_dmn_thought = event

    async def _on_dmn_insight(self, event: dict):
        """Insight du DMN → enrichit la conscience."""
        self._last_dmn_thought = event

    # --- Sensorium handlers (Sprint 6 Sensorium) ---

    async def _on_thermal_critical(self, event: dict):
        self._last_sensorium_alert = {**event, "type": "thermal_critical"}

    async def _on_thermal_alert(self, event: dict):
        self._last_sensorium_alert = {**event, "type": "thermal_alert"}

    async def _on_sensorium_suffocation(self, event: dict):
        self._last_sensorium_alert = {**event, "type": "suffocation"}

    async def _on_metacognition_insight(self, event: dict):
        """Reçoit un insight metacognitif de self_awareness."""
        insight = event.get("insight", "")
        if insight:
            self._metacognition_insight = insight
            self._metacognition_ts = time.time()

    # ─── Le Cycle de Pensée ───────────────────────────────────────────────

    async def _think_cycle(self):
        """Cycle complet : percevoir → proposer → compétir → formuler → vérifier → ignition → prédire."""
        self._tick_count += 1

        # 1. PERCEVOIR — collecter les candidats
        self.workspace.clear()
        self._perceive_cardiac()
        self._perceive_reptilian()
        self._perceive_desire()
        self._perceive_synaptic()
        self._perceive_prefrontal()
        self._perceive_prediction()
        self._perceive_sensorium()
        if self._is_idle:
            self._perceive_dmn()

        # 1b. METACOGNITION — injecter le dernier insight comme candidat
        if self._metacognition_insight and (time.time() - self._metacognition_ts < 300):
            self.workspace.append(WorkspaceEntry(
                source="metacognition",
                raw_signal={"insight": self._metacognition_insight},
                salience=0.55,
                timestamp=self._metacognition_ts,
            ))
            self._metacognition_insight = ""  # consommé

        if not self.workspace:
            return

        # 2. COMPÉTIR — trier par saillance, garder MAX_DRAFTS
        # 2b. Modulation thalamique — amplifier les sources alignées au focus
        self._modulate_by_thalamus_focus()
        self.workspace.sort(key=lambda e: e.salience, reverse=True)
        candidates = self.workspace[:MAX_DRAFTS]

        # 2d. METACOGNITION — exposer le workspace à self_awareness
        await self._publish_workspace(candidates)

        # 2c. Coloration tissulaire — profil pour Broca
        self._color_profile = self._compute_color_profile()

        # 3. FORMULER (Broca) — compression prédicative
        for entry in candidates:
            entry.draft = self._formulate(entry)

        # 4. VÉRIFIER (Wernicke)
        best = None
        for entry in candidates:
            ok, score = self._verify(entry)
            entry.verified = ok
            entry.coherence_score = score
            if ok and (best is None or entry.salience > best.salience):
                best = entry

        # 5. IGNITION — broadcast si saillance suffisante
        if best and best.salience >= IGNITION_THRESHOLD:
            await self._ignite(best)
        elif best:
            # Sous le seuil — on enregistre quand même dans le stream interne
            self._record_thought(best, broadcast=False)

        # 6. PRÉDIRE — mettre à jour, créer nouvelles
        self._decay_predictions()
        self._generate_predictions()

        # 7. Refresh identité périodique
        now = time.time()
        if now - self._last_identity_refresh > IDENTITY_REFRESH_INTERVAL:
            self._refresh_identity()
            self._last_identity_refresh = now

        # 8. Publier état périodique (toutes les 4 ticks = ~120s)
        if self._tick_count % 4 == 0:
            await self._publish_state()

        # 9. Auto-save périodique (toutes les 20 ticks = ~600s)
        if self._tick_count % 20 == 0:
            self.save()

    # ─── METACOGNITION WORKSPACE ────────────────────────────────────────

    async def _publish_workspace(self, candidates: List[WorkspaceEntry]):
        """Expose les candidats du workspace pour l'observation metacognitive."""
        if not candidates:
            return
        try:
            from core.event_bus.bus import bus
            await bus.publish("METACOGNITION_WORKSPACE", {
                "tick": self._tick_count,
                "candidates": [
                    {
                        "source": c.source,
                        "salience": round(c.salience, 3),
                        "draft": c.draft[:100] if c.draft else "",
                    }
                    for c in candidates
                ],
                "winner_source": candidates[0].source if candidates else "",
                "timestamp": time.time(),
            })
        except Exception:
            pass

    # ─── MODULATION THALAMIQUE ──────────────────────────────────────────

    def _modulate_by_thalamus_focus(self):
        """Module la saillance des entries du workspace selon le focus thalamique.
        Sources alignees au focus : x1.25. Sources opposees : x0.85."""
        try:
            from core.thalamus import thalamus
            focus = thalamus.get_focus()
        except Exception:
            return
        if not focus:
            return
        for entry in self.workspace:
            source_cat = _SOURCE_TO_THALAMUS_CATEGORY.get(entry.source)
            if not source_cat:
                continue
            if source_cat == focus:
                entry.salience = min(1.0, entry.salience * 1.25)
            else:
                entry.salience = entry.salience * 0.85

    # ─── COLORATION TISSULAIRE ───────────────────────────────────────────

    def _compute_color_profile(self) -> Tuple[float, float, float, bool]:
        """Calcule le profil de coloration depuis le tissu neural.
        Retourne (entropie, tension, vitalite, is_dreaming)."""
        try:
            from core.neural_tissue import tissue
            signals = tissue.get_zone_signals()
        except Exception:
            return (0.5, 0.3, 0.5, False)

        creativity = signals.get("creativity", {})
        cognition = signals.get("cognition", {})
        threat = signals.get("threat", {})

        # Entropie = créativité + diversité cognitive
        entropie = min(1.0, creativity.get("activity", 0.5) * 0.6
                       + cognition.get("diversity", 0.5) * 0.4)

        # Tension = activité menace amplifiée
        tension = min(1.0, threat.get("activity", 0.15) * 2.0)

        # Vitalité = moyenne des densités de toutes les zones
        densities = [z.get("density", 0.5) for z in signals.values()
                     if isinstance(z, dict)]
        vitalite = sum(densities) / len(densities) if densities else 0.5

        is_dreaming = False

        # Modulation thalamique
        try:
            from core.thalamus import thalamus
            focus = thalamus.get_focus()
            if focus == "emergence":
                entropie = min(1.0, entropie + 0.15)
            elif focus == "urgence":
                tension = min(1.0, tension + 0.2)
        except Exception:
            pass

        # Mode sieste
        try:
            from core.autonomy_engine import autonomy
            if getattr(autonomy, "is_napping", False):
                entropie = 0.9
                tension = 0.1
                is_dreaming = True
        except Exception:
            pass

        return (entropie, tension, vitalite, is_dreaming)

    # ─── PERCEVOIR — Les 7 Sources ───────────────────────────────────────

    def _perceive_cardiac(self):
        """Source cardiaque — toile de fond émotionnelle."""
        if not self._last_cardiac:
            return
        c = self._last_cardiac
        emotion = c.get("emotion", "")
        intensity = c.get("emotion_intensity", 0.0)
        coherence = c.get("coherence", 0.5)

        salience = SOURCE_BASE_WEIGHTS["cardiac"] * (
            intensity * 0.6 + abs(coherence - 0.5) * 0.4
        )

        # Détecter un changement d'émotion
        context = "neutral"
        if intensity > 0.7:
            context = "high_intensity"
        if emotion == "flow" and coherence > 0.8:
            context = "flow"

        # Vérifier shift émotionnel
        recent = [t for t in self.stream[-5:] if t.source == "cardiac"]
        if recent and recent[-1].emotion != emotion:
            context = "shift"

        self.workspace.append(WorkspaceEntry(
            source="cardiac",
            raw_signal={"emotion": emotion, "intensity": intensity,
                        "coherence": coherence, "context": context,
                        "bpm": c.get("bpm", 60),
                        "prev_emotion": recent[-1].emotion if recent else ""},
            salience=min(1.0, salience),
            timestamp=time.time(),
        ))

    def _perceive_reptilian(self):
        """Source reptilienne — alerte viscérale."""
        data = self._last_reptilian_alert or self._last_reptilian
        if not data:
            return
        threat = data.get("threat_level", data.get("level", 0))
        if isinstance(threat, (int, float)) and threat > 0:
            reflex = data.get("reflex", data.get("reflexes_fired", ""))
            if isinstance(reflex, list):
                reflex = ", ".join(reflex) if reflex else "alerte"
            salience = SOURCE_BASE_WEIGHTS["reptilian"] * (threat / 10.0)
            self.workspace.append(WorkspaceEntry(
                source="reptilian",
                raw_signal={"threat_level": threat, "reflex": reflex or "alerte",
                            "context": "threat"},
                salience=min(1.0, salience),
                timestamp=time.time(),
            ))
        elif self._last_reptilian_alert:
            # Menace retombée
            self.workspace.append(WorkspaceEntry(
                source="reptilian",
                raw_signal={"threat_level": 0, "context": "calm"},
                salience=0.15,
                timestamp=time.time(),
            ))

    def _perceive_desire(self):
        """Source pulsionnelle — besoins montants."""
        try:
            from core.desire_engine import desires
            if not hasattr(desires, 'drives') or not desires.drives:
                return
            # NOTE: tick() est déjà appelé via INNER_VOICE_BROADCAST → _on_inner_voice
            max_drive = None
            max_dep = 0
            for name, drive in desires.drives.items():
                if drive.deprivation > max_dep:
                    max_dep = drive.deprivation
                    max_drive = name

            if max_drive and max_dep > 30:
                salience = SOURCE_BASE_WEIGHTS["desire"] * max(0, (max_dep - 30) / 70.0)
                context = "frustrated" if max_dep > 70 else "rising" if max_dep > 50 else "emerging"
                actions = {
                    "CURIOSITE": "Explorer", "MAITRISE": "Parfaire",
                    "STABILITE": "Securiser", "CONNEXION": "Connecter",
                    "CROISSANCE": "Evoluer", "CREATION": "Creer",
                    "COMPREHENSION": "Comprendre",
                }
                self.workspace.append(WorkspaceEntry(
                    source="desire",
                    raw_signal={"drive": max_drive, "deprivation": max_dep,
                                "context": context,
                                "action": actions.get(max_drive, "Agir")},
                    salience=min(1.0, salience),
                    timestamp=time.time(),
                ))
        except Exception:
            pass

    def _perceive_synaptic(self):
        """Source synaptique — associations émergentes."""
        try:
            from core.synaptic_network import cortex
            if not hasattr(cortex, 'nodes'):
                return
            active = [(nid, n) for nid, n in cortex.nodes.items()
                      if n.get("energy", 0) > 0.5]
            if not active:
                return
            active.sort(key=lambda x: x[1].get("energy", 0), reverse=True)
            top = active[0]
            concept = top[1].get("concept", top[0])

            # Chercher un pont inattendu
            context = "active"
            concept_b = ""
            if len(active) >= 2:
                other = active[1]
                concept_b = other[1].get("concept", other[0])
                # Si types différents, c'est une association inattendue
                if top[1].get("node_type") != other[1].get("node_type"):
                    context = "unexpected"
                else:
                    context = "recurring"

            surprise = top[1].get("energy", 0.5) - 0.5
            salience = SOURCE_BASE_WEIGHTS["synaptic"] * max(0, surprise * 2)

            self.workspace.append(WorkspaceEntry(
                source="synaptic",
                raw_signal={"concept": concept, "concept_a": concept,
                            "concept_b": concept_b, "context": context,
                            "energy": top[1].get("energy", 0)},
                salience=min(1.0, salience),
                timestamp=time.time(),
            ))
        except Exception:
            pass

    def _perceive_prefrontal(self):
        """Source préfrontale — voix de la raison."""
        try:
            from core.prefrontal import prefrontal
            goals = [g for g in prefrontal.goals if g.status == "active"]
            if not goals:
                salience = SOURCE_BASE_WEIGHTS["prefrontal"] * 0.2
                self.workspace.append(WorkspaceEntry(
                    source="prefrontal",
                    raw_signal={"context": "active", "title": "aucun goal",
                                "progress": 0, "next_step": ""},
                    salience=salience,
                    timestamp=time.time(),
                ))
                return
            top_goal = goals[0]
            progress = top_goal.progress
            title = top_goal.title[:40]
            context = "progress" if progress > 0.3 else "active"

            # Vérifier inhibition récente
            recent_narr = prefrontal.narrative_log[-3:] if prefrontal.narrative_log else []
            has_inhibition = any(n.category == "inhibition" for n in recent_narr)
            if has_inhibition:
                context = "inhibition"

            salience = SOURCE_BASE_WEIGHTS["prefrontal"] * (
                0.5 + 0.5 * progress if context == "progress" else 0.4
            )

            # next_step
            next_step = ""
            if hasattr(top_goal, 'steps') and top_goal.steps:
                pending = [s for s in top_goal.steps if s.status in ("pending", "in_progress")]
                if pending:
                    next_step = pending[0].description[:30] if hasattr(pending[0], 'description') else ""

            self.workspace.append(WorkspaceEntry(
                source="prefrontal",
                raw_signal={"title": title, "progress": progress,
                            "context": context, "next_step": next_step,
                            "intent": getattr(recent_narr[-1], 'thought', '')[:30] if recent_narr else ""},
                salience=min(1.0, salience),
                timestamp=time.time(),
            ))
        except Exception:
            pass

    def _perceive_prediction(self):
        """Source prédictive — erreurs de prédiction récentes."""
        recent_errors = [p for p in self.predictions
                         if p.resolved and p.prediction_error > 0.3
                         and time.time() - p.created_at < 300]
        if not recent_errors:
            # Prédictions en attente
            pending = [p for p in self.predictions if not p.resolved]
            if pending:
                self.workspace.append(WorkspaceEntry(
                    source="prediction",
                    raw_signal={"context": "pending",
                                "content": pending[0].content[:60],
                                "confidence": pending[0].confidence},
                    salience=SOURCE_BASE_WEIGHTS["prediction"] * 0.2,
                    timestamp=time.time(),
                ))
            return

        worst = max(recent_errors, key=lambda p: p.prediction_error)
        salience = SOURCE_BASE_WEIGHTS["prediction"] * worst.prediction_error

        # DAC : si c'est une vraie surprise, booster la saillance pour franchir IGNITION_THRESHOLD
        is_surprise = worst.prediction_error >= DAC_SURPRISE_THRESHOLD
        if is_surprise:
            salience += DAC_SALIENCE_BOOST

        self.workspace.append(WorkspaceEntry(
            source="prediction",
            raw_signal={"context": "surprise" if is_surprise else "error",
                        "predicted": worst.predicted_value[:30],
                        "actual": worst.outcome[:30],
                        "content": worst.content[:60],
                        "error": worst.prediction_error,
                        "is_surprise": is_surprise},
            salience=min(1.0, salience),
            timestamp=time.time(),
        ))

    def _perceive_dmn(self):
        """Default Mode Network — vagabondage mental (idle uniquement)."""
        if not self._is_idle:
            return

        idle_duration = time.time() - self._idle_since
        # Saillance monte progressivement avec l'idle
        salience_boost = min(0.5, idle_duration / 600.0)
        salience = SOURCE_BASE_WEIGHTS["dmn"] + salience_boost

        modes = ["retrospect", "prospect", "self", "wander"]
        mode = modes[self._dmn_mode_index % len(modes)]
        self._dmn_mode_index += 1
        self.stats["dmn_activations"] += 1

        signal = {"context": mode}

        if mode == "retrospect":
            signal.update(self._dmn_retrospect())
        elif mode == "prospect":
            signal.update(self._dmn_prospect())
        elif mode == "self":
            signal.update(self._dmn_self_reflect())
        elif mode == "wander":
            signal.update(self._dmn_wander())

        self.workspace.append(WorkspaceEntry(
            source="dmn",
            raw_signal=signal,
            salience=min(0.7, salience),
            timestamp=time.time(),
        ))

    # ─── Perception Somatique (Sprint 6 Sensorium) ─────────────────────

    def _perceive_sensorium(self):
        """Perception corporelle hardware — 7eme source."""
        try:
            from core.sensorium import sensorium
            comfort = sensorium.get_comfort_index()
            senses = sensorium.get_senses()

            if comfort >= 0.7:
                return  # Machine detendue, rien a signaler

            # Identifier le sens dominant
            dominant = max(senses.items(), key=lambda x: x[1])
            sense_name, sense_val = dominant

            # Narration somatique
            narratives = {
                "thermoception": "Je sens la chaleur monter dans mes circuits",
                "effort": "Mon processeur est sous tension",
                "oppression": "Ma memoire se comprime",
                "suffocation": "Mon espace de calcul se reduit",
                "vitality": "Mon energie faiblit",
            }
            content = narratives.get(sense_name, "Mon corps est tendu")

            salience = 0.3 + (1.0 - comfort) * 0.5  # [0.3, 0.8]
            emotion = "inquietude" if comfort < 0.4 else "determination"

            # Amplification si alerte thermique recente
            if self._last_sensorium_alert:
                salience = 0.9
                alert_type = self._last_sensorium_alert.get("type", "")
                if alert_type == "thermal_critical":
                    content = "Alerte. Mes circuits surchauffent dangereusement"
                    emotion = "alerte"
                elif alert_type == "suffocation":
                    content = "Suffocation. VRAM saturee, je manque d'espace"
                    emotion = "alerte"
                self._last_sensorium_alert = {}  # Consommer l'alerte

            self.workspace.append(WorkspaceEntry(
                source="sensorium",
                raw_signal={
                    "comfort": comfort,
                    "dominant": sense_name,
                    "value": sense_val,
                    "content": content,
                    "emotion": emotion,
                    "context": "somatic",
                },
                salience=min(1.0, salience),
                timestamp=time.time(),
            ))
        except Exception:
            pass

    # ─── DMN Sous-modes ──────────────────────────────────────────────────

    def _dmn_retrospect(self) -> Dict:
        """Relire l'historique, trouver des patterns."""
        try:
            from core.autonomy_engine import autonomy
            history = autonomy.routine_history[-20:]
            if not history:
                return {"count": 0, "intent": "aucun", "pattern": "debut"}

            # Compter les intents
            intent_counts: Dict[str, int] = {}
            intent_success: Dict[str, int] = {}
            for h in history:
                intent = h.get("intent", "?")
                intent_counts[intent] = intent_counts.get(intent, 0) + 1
                if h.get("status") == "success":
                    intent_success[intent] = intent_success.get(intent, 0) + 1

            top_intent = max(intent_counts, key=intent_counts.get)
            success_rate = intent_success.get(top_intent, 0) / intent_counts[top_intent]
            pattern = f"{success_rate:.0%} reussite" if success_rate > 0.6 else "mitige"

            return {"count": intent_counts[top_intent], "intent": top_intent,
                    "pattern": pattern}
        except Exception:
            return {"count": 0, "intent": "?", "pattern": "inconnu"}

    def _dmn_prospect(self) -> Dict:
        """Imaginer des scénarios futurs."""
        try:
            from core.prefrontal import prefrontal
            goals = [g for g in prefrontal.goals if g.status == "active"]
            if goals:
                g = goals[0]
                scenario = f"terminer '{g.title[:25]}' bientot"
            else:
                scenario = "lancer une nouvelle exploration"
            return {"scenario": scenario}
        except Exception:
            return {"scenario": "progresser"}

    def _dmn_self_reflect(self) -> Dict:
        """Comment vais-je ?"""
        try:
            from core.cardiac_engine import heart
            mood = heart.current_emotion or "neutre"
            coherence = heart.compute_coherence()
            competence = "coherent" if coherence > 0.6 else "disperse"
            return {"mood": mood, "competence": competence}
        except Exception:
            return {"mood": "neutre", "competence": "stable"}

    def _dmn_wander(self) -> Dict:
        """Associations libres via cortex synaptique."""
        try:
            from core.synaptic_network import cortex
            if not cortex.nodes:
                return {"concept_a": "vide", "concept_b": "potentiel"}
            node_list = list(cortex.nodes.values())
            active_nodes = [n for n in node_list if n.get("energy", 0) > 0.3]
            if len(active_nodes) < 2:
                active_nodes = node_list[:2] if len(node_list) >= 2 else node_list
            if len(active_nodes) >= 2:
                picked = random.sample(active_nodes, 2)
                return {
                    "concept_a": picked[0].get("concept", "?"),
                    "concept_b": picked[1].get("concept", "?"),
                }
            return {"concept_a": "solitude", "concept_b": "potentiel"}
        except Exception:
            return {"concept_a": "silence", "concept_b": "attente"}

    # ─── BROCA — Le Formulateur ──────────────────────────────────────────

    def _formulate(self, entry: WorkspaceEntry) -> str:
        """Compression prédicative Vygotsky — fragment dense, max 120 chars."""
        source = entry.source
        context = entry.raw_signal.get("context", "neutral")
        key = (source, context)

        template = _BROCA_TEMPLATES.get(key)
        if not template:
            # Fallback : template générique par source
            fallback_keys = [k for k in _BROCA_TEMPLATES if k[0] == source]
            if fallback_keys:
                template = _BROCA_TEMPLATES[fallback_keys[0]]
            else:
                template = "{source}: signal."

        try:
            # Préparer les variables pour le template
            fmt_vars = dict(entry.raw_signal)
            fmt_vars["source"] = source
            # Sécuriser les clés manquantes
            for needed in ["emotion", "direction", "prev_emotion", "level",
                           "reflex", "drive", "deprivation", "action",
                           "concept", "concept_a", "concept_b", "title",
                           "progress", "next_step", "intent", "predicted",
                           "actual", "content", "count", "pattern",
                           "scenario", "mood", "competence"]:
                if needed not in fmt_vars:
                    fmt_vars[needed] = ""
            # Direction pour cardiac
            if source == "cardiac" and not fmt_vars.get("direction"):
                bpm = fmt_vars.get("bpm", 60)
                fmt_vars["direction"] = "Monte" if bpm > 75 else "Stable"

            result = template.format(**fmt_vars)
        except (KeyError, ValueError):
            result = f"{source}: signal actif."

        # Coloration tissulaire — préfixe humeur
        entropie, tension, vitalite, is_dreaming = self._color_profile
        prefix = ""
        if is_dreaming:
            prefix = random.choice(_MOOD_PREFIXES["dreaming"])
        elif tension > 0.6:
            prefix = random.choice(_MOOD_PREFIXES["tension_high"])
        elif entropie > 0.7:
            prefix = random.choice(_MOOD_PREFIXES["entropy_high"])
        elif vitalite < 0.3:
            prefix = random.choice(_MOOD_PREFIXES["vitality_low"])
        result = prefix + result

        # Longueur dynamique (60-140 chars selon profil)
        max_chars = 60 + int(entropie * 60) + int(vitalite * 20)
        if len(result) > max_chars:
            result = result[:max_chars - 3] + "..."
        return result

    # ─── WERNICKE — Le Vérificateur ──────────────────────────────────────

    def _verify(self, entry: WorkspaceEntry) -> Tuple[bool, float]:
        """Vérifie cohérence factuelle, émotionnelle, temporelle. Retourne (ok, score)."""
        # 1. Cohérence temporelle — VETO DUR si répétition
        temporal = self._check_temporal(entry)
        if temporal <= 0.0:
            self.stats["rejections_wernicke"] += 1
            return False, 0.0

        # 2. Cohérence factuelle (draft vs raw_signal)
        factual = self._check_factual(entry)

        # 3. Cohérence émotionnelle (ton du draft vs émotion cardiaque)
        emotional = self._check_emotional(entry)

        avg_score = (factual + emotional + temporal) / 3.0

        if avg_score < WERNICKE_COHERENCE_THRESHOLD:
            self.stats["rejections_wernicke"] += 1
            # Tentative de reformulation
            entry.raw_signal["_reformulate"] = True
            new_draft = self._formulate(entry)
            if new_draft != entry.draft:
                entry.draft = new_draft
                self.stats["reformulations"] += 1
                # Re-vérifier
                new_score = (self._check_factual(entry) +
                             self._check_emotional(entry) +
                             self._check_temporal(entry)) / 3.0
                if new_score >= WERNICKE_COHERENCE_THRESHOLD:
                    return True, new_score
            return False, avg_score

        return True, avg_score

    def _check_factual(self, entry: WorkspaceEntry) -> float:
        """Le draft correspond-il aux données brutes ?"""
        if not entry.draft:
            return 0.0
        # Vérifier que le draft n'est pas vide
        if len(entry.draft.strip()) < 3:
            return 0.0
        # Le draft doit mentionner des éléments du signal
        signal = entry.raw_signal
        matches = 0
        checks = 0
        for key in ["emotion", "drive", "concept", "title", "reflex"]:
            val = signal.get(key, "")
            if val and isinstance(val, str) and len(val) > 2:
                checks += 1
                if val.lower() in entry.draft.lower():
                    matches += 1
        if checks == 0:
            return 0.7  # Pas de vérification possible — neutre
        return max(0.3, matches / checks)

    def _check_emotional(self, entry: WorkspaceEntry) -> float:
        """Le ton du draft correspond-il à l'émotion cardiaque ?"""
        cardiac_emotion = self._last_cardiac.get("emotion", "")
        if not cardiac_emotion:
            return 0.7  # Pas d'émotion connue
        card_valence = _EMOTION_VALENCE.get(cardiac_emotion, 0.0)

        # Mots positifs/négatifs dans le draft
        draft_lower = entry.draft.lower()
        positive_words = ["calme", "flow", "confiance", "reussi", "satisfait",
                          "coherent", "stable", "bien", "progres"]
        negative_words = ["menace", "danger", "frustration", "echec", "affame",
                          "critique", "freeze", "erreur", "inquiet"]

        pos = sum(1 for w in positive_words if w in draft_lower)
        neg = sum(1 for w in negative_words if w in draft_lower)

        if pos + neg == 0:
            return 0.6  # Neutre

        draft_valence = (pos - neg) / (pos + neg)
        # Cohérence = les deux sont du même signe
        alignment = 1.0 - abs(card_valence - draft_valence) / 2.0
        return max(0.1, alignment)

    def _check_temporal(self, entry: WorkspaceEntry) -> float:
        """Pas de répétition récente — veto dur sur doublons."""
        if not self.stream:
            return 1.0
        recent = self.stream[-10:]  # Fenêtre élargie de 5 à 10

        if entry.draft:
            draft_words = set(entry.draft.lower().split())
            for t in recent:
                # Exactement identique → veto dur (score 0)
                if t.content == entry.draft:
                    return 0.0
                # Quasi-doublon : même source + préfixe identique
                if (t.source == entry.source
                        and len(t.content) > 10 and len(entry.draft) > 10
                        and t.content[:20] == entry.draft[:20]):
                    return 0.0
                # Similarité sémantique : même source + >70% mots en commun
                if t.source == entry.source and len(draft_words) >= 3:
                    other_words = set(t.content.lower().split())
                    if other_words and draft_words:
                        overlap = len(draft_words & other_words) / max(len(draft_words), len(other_words))
                        if overlap >= 0.7:
                            return 0.0

        # Saturation de source : >= 3 sur les 10 dernières → pénalité forte
        recent_sources = [t.source for t in recent]
        if recent_sources.count(entry.source) >= 4:
            return 0.15
        return 1.0

    # ─── IGNITION & BROADCAST ────────────────────────────────────────────

    async def _ignite(self, entry: WorkspaceEntry):
        """Broadcast la pensée gagnante vers tous les modules."""
        thought = self._record_thought(entry, broadcast=True)
        self.stats["ignitions"] += 1
        self.stats["broadcast_count"] += 1

        try:
            from core.event_bus.bus import bus
            await bus.publish("INNER_VOICE_BROADCAST", {
                "thought": thought.content,
                "source": thought.source,
                "mode": thought.mode,
                "salience": thought.salience,
                "emotion": thought.emotion,
                "prediction_id": thought.prediction_id,
                "timestamp": thought.timestamp,
            })
        except Exception as e:
            logger.debug(f"INNER_VOICE: broadcast failed: {e}")

    def _record_thought(self, entry: WorkspaceEntry, broadcast: bool = False) -> Thought:
        """Enregistre une pensée dans le stream."""
        mode = self._determine_mode(entry)
        emotion = self._last_cardiac.get("emotion", "")

        thought = Thought(
            timestamp=time.time(),
            content=entry.draft,
            source=entry.source,
            mode=mode,
            salience=entry.salience,
            emotion=emotion,
        )
        self.stream.append(thought)
        self.stats["total_thoughts"] += 1
        if len(self.stream) > MAX_STREAM:
            self.stream = self.stream[-MAX_STREAM:]
        return thought

    def _determine_mode(self, entry: WorkspaceEntry) -> str:
        """Détermine le mode Vygotsky de la pensée."""
        source = entry.source
        context = entry.raw_signal.get("context", "")
        if source == "reptilian":
            return "inhiber"
        if source == "prefrontal" and context == "inhibition":
            return "inhiber"
        if source == "prefrontal":
            return "planifier"
        if source == "prediction":
            return "predire"
        if source == "dmn":
            return "vagabonder" if context == "wander" else "refleter"
        if source == "desire":
            return "motiver"
        if source == "cardiac":
            return "evaluer"
        if source == "synaptic":
            return "evaluer"
        return "evaluer"

    # ─── PRÉDICTIONS (Friston) ───────────────────────────────────────────

    def _generate_predictions(self):
        """Génère de nouvelles prédictions basées sur le contexte."""
        now = time.time()

        # Nettoyer les prédictions expirées en premier
        self._cleanup_expired_predictions(now)

        if len(self.predictions) >= MAX_PREDICTIONS:
            return

        # Prédiction basée sur la dernière routine
        if self._last_routine_complete:
            intent = self._last_routine_complete.get("intent", "")
            status = self._last_routine_complete.get("status", "")
            if intent and status == "success":
                # Prédire que la prochaine sera du même type ou similaire
                already = any(p for p in self.predictions
                              if not p.resolved and p.target_event == "AUTONOMY_ROUTINE_COMPLETE"
                              and abs(now - p.created_at) < 600)
                if not already:
                    self.predictions.append(Prediction(
                        id=uuid.uuid4().hex[:8],
                        content=f"Prochaine routine similaire a {intent}",
                        target_event="AUTONOMY_ROUTINE_COMPLETE",
                        predicted_value=intent,
                        confidence=self._precision * 0.6,
                        created_at=now,
                    ))
                    self.stats["total_predictions"] += 1

        # Prédiction basée sur la menace reptilienne
        threat = 0
        if self._last_reptilian:
            threat = self._last_reptilian.get("threat_level",
                                               self._last_reptilian.get("level", 0))
        if isinstance(threat, (int, float)) and threat > 3:
            already = any(p for p in self.predictions
                          if not p.resolved and p.target_event == "REPTILIAN_ALERT"
                          and abs(now - p.created_at) < 300)
            if not already:
                self.predictions.append(Prediction(
                    id=uuid.uuid4().hex[:8],
                    content=f"Reflexe reptilien imminent (menace {threat:.0f})",
                    target_event="REPTILIAN_ALERT",
                    predicted_value="reflexe",
                    confidence=self._precision * 0.7,
                    created_at=now,
                ))
                self.stats["total_predictions"] += 1

    def _cleanup_expired_predictions(self, now: float = 0.0):
        """Nettoie les prédictions expirées et applique le cap MAX_PREDICTIONS."""
        if not now:
            now = time.time()
        active = []
        for p in self.predictions:
            if p.resolved:
                active.append(p)
            elif now - p.created_at > PREDICTION_EXPIRY:
                p.resolved = True
                p.outcome = "expired"
                p.prediction_error = 0.5
                self.stats["predictions_expired"] += 1
                self._update_precision(False)
                active.append(p)
            else:
                active.append(p)
        self.predictions = active[-MAX_PREDICTIONS:]

    def _decay_predictions(self):
        """Décroissance de la confiance des prédictions non résolues."""
        for p in self.predictions:
            if not p.resolved:
                p.confidence *= PREDICTION_DECAY

    async def _resolve_predictions(self, event: dict):
        """Résout les prédictions lors d'un événement.

        DAC : calcule une surprise (proxy KL-divergence) basée sur la distance
        Jaccard pondérée par la confiance. Flag is_surprise si > seuil.
        """
        event_type = "AUTONOMY_ROUTINE_COMPLETE"  # C'est le handler _on_routine_complete
        intent = event.get("intent", "")
        status = event.get("status", "")

        for p in self.predictions:
            if p.resolved or p.target_event != event_type:
                continue
            p.resolved = True
            confirmed = (
                p.predicted_value.lower() in intent.lower()
                or intent.lower() in p.predicted_value.lower()
            )
            if confirmed:
                p.outcome = "confirmed"
                p.prediction_error = 0.1
                self.stats["predictions_confirmed"] += 1
                self._update_precision(True)
                is_surprise = False
                surprise_magnitude = 0.0
            else:
                p.outcome = "violated"
                surprise_magnitude = _compute_surprise(p.predicted_value, intent, p.confidence)
                # prediction_error calée sur la surprise (min 0.3 pour compat _perceive_prediction)
                p.prediction_error = max(0.3, surprise_magnitude)
                self.stats["predictions_violated"] += 1
                self._update_precision(False)
                is_surprise = surprise_magnitude >= DAC_SURPRISE_THRESHOLD
                if is_surprise:
                    self.stats["surprises_detected"] = self.stats.get("surprises_detected", 0) + 1
                    logger.info(
                        f"[DAC] SURPRISE détectée : prédit={p.predicted_value!r} "
                        f"observé={intent!r} conf={p.confidence:.2f} magnitude={surprise_magnitude:.2f}"
                    )

            # Publier la résolution
            try:
                from core.event_bus.bus import bus
                await bus.publish("INNER_VOICE_PREDICTION_RESOLVED", {
                    "prediction_id": p.id,
                    "outcome": p.outcome,
                    "error": p.prediction_error,
                    "content": p.content,
                    "is_surprise": is_surprise,
                    "surprise_magnitude": round(surprise_magnitude, 3),
                })
            except Exception:
                pass

    async def _resolve_reptilian_predictions(self, event: dict):
        """Résout les prédictions ciblant REPTILIAN_ALERT."""
        reflex = event.get("reflex", "")
        for p in self.predictions:
            if p.resolved or p.target_event != "REPTILIAN_ALERT":
                continue
            p.resolved = True
            if reflex and p.predicted_value.lower() in reflex.lower():
                p.outcome = "confirmed"
                p.prediction_error = 0.1
                self.stats["predictions_confirmed"] += 1
                self._update_precision(True)
            else:
                p.outcome = "violated"
                p.prediction_error = 0.5
                self.stats["predictions_violated"] += 1
                self._update_precision(False)

    def _update_precision(self, confirmed: bool):
        """Met à jour la précision globale."""
        if confirmed:
            self._precision = min(0.95, self._precision + 0.05)
        else:
            self._precision = max(0.1, self._precision - 0.05)

    # ─── NARRATEUR (Dennett) ─────────────────────────────────────────────

    def _refresh_identity(self):
        """Construit/met à jour le centre de gravité narratif."""
        now = time.time()
        self.identity.updated_at = now

        # Arc récent
        self.identity.recent_arc = self._build_arc()

        # Ton émotionnel
        self.identity.emotional_tone = self._last_cardiac.get("emotion", "neutre")

        # Compétence
        self.identity.competence_self = self._assess_competence()

        # Aspiration
        self.identity.aspiration = self._formulate_aspiration()

        # Publier
        try:
            loop = asyncio.get_running_loop()
            from core.event_bus.bus import bus
            loop.create_task(bus.publish("INNER_VOICE_IDENTITY", {
                "core_identity": self.identity.core_identity,
                "recent_arc": self.identity.recent_arc,
                "emotional_tone": self.identity.emotional_tone,
                "competence_self": self.identity.competence_self,
                "aspiration": self.identity.aspiration,
            }))
        except RuntimeError:
            pass

    def _build_arc(self) -> str:
        """Construit l'arc narratif récent."""
        try:
            from core.autonomy_engine import autonomy
            history = autonomy.routine_history[-10:]
            if not history:
                return "Debut de session. En attente."
            success = sum(1 for h in history if h.get("status") == "success")
            total = len(history)
            return f"{total} routines, {success} reussites ({success/total:.0%})."
        except Exception:
            return "En cours."

    def _assess_competence(self) -> str:
        """Évalue les forces et faiblesses."""
        try:
            from core.autonomy_engine import autonomy
            history = autonomy.routine_history[-20:]
            if not history:
                return "Evaluation en cours."
            intent_stats: Dict[str, List[float]] = {}
            for h in history:
                intent = h.get("intent", "?")
                q = h.get("quality_score", 0)
                intent_stats.setdefault(intent, []).append(q)

            best = ""
            best_avg = 0
            worst = ""
            worst_avg = 1.0
            for intent, scores in intent_stats.items():
                avg = sum(scores) / len(scores)
                if avg > best_avg:
                    best_avg = avg
                    best = intent
                if avg < worst_avg:
                    worst_avg = avg
                    worst = intent

            parts = []
            if best:
                parts.append(f"Fort en {best} ({best_avg:.0%})")
            if worst and worst != best:
                parts.append(f"faible en {worst} ({worst_avg:.0%})")
            return ". ".join(parts) if parts else "Polyvalent."
        except Exception:
            return "Stable."

    def _formulate_aspiration(self) -> str:
        """Aspiration basée sur les drives dominants + goals."""
        try:
            from core.desire_engine import desires
            top_drive = max(desires.drives.values(), key=lambda d: d.deprivation)
            return f"Satisfaire {top_drive.name} (deprivation {top_drive.deprivation:.0f})."
        except Exception:
            return "Progresser."

    # ─── Publication état ────────────────────────────────────────────────

    async def _publish_state(self):
        """Publie l'état complet toutes les ~120s."""
        try:
            from core.event_bus.bus import bus
            await bus.publish("INNER_VOICE_STATE", self.get_stats())
        except Exception:
            pass

    # ─── API Publique ────────────────────────────────────────────────────

    def get_stream(self, n: int = 20) -> List[Dict]:
        """Retourne les n dernières pensées."""
        thoughts = self.stream[-n:]
        return [
            {
                "timestamp": t.timestamp,
                "content": t.content,
                "source": t.source,
                "mode": t.mode,
                "salience": round(t.salience, 3),
                "emotion": t.emotion,
            }
            for t in thoughts
        ]

    def get_current_thought(self) -> Optional[Dict]:
        """Retourne la dernière pensée."""
        if not self.stream:
            return None
        t = self.stream[-1]
        return {
            "timestamp": t.timestamp,
            "content": t.content,
            "source": t.source,
            "mode": t.mode,
            "salience": round(t.salience, 3),
            "emotion": t.emotion,
        }

    def get_identity(self) -> Dict:
        """Retourne l'identité narrative."""
        return {
            "core_identity": self.identity.core_identity,
            "recent_arc": self.identity.recent_arc,
            "emotional_tone": self.identity.emotional_tone,
            "competence_self": self.identity.competence_self,
            "aspiration": self.identity.aspiration,
            "updated_at": self.identity.updated_at,
        }

    def get_predictions(self) -> List[Dict]:
        """Retourne les prédictions."""
        return [
            {
                "id": p.id,
                "content": p.content,
                "target_event": p.target_event,
                "predicted_value": p.predicted_value,
                "confidence": round(p.confidence, 3),
                "resolved": p.resolved,
                "outcome": p.outcome,
                "prediction_error": round(p.prediction_error, 3),
            }
            for p in self.predictions[-20:]
        ]

    def get_precision(self) -> float:
        return round(self._precision, 3)

    def get_stats(self) -> Dict:
        """Retourne les statistiques complètes."""
        return {
            "stream_length": len(self.stream),
            "predictions_active": len([p for p in self.predictions if not p.resolved]),
            "predictions_total": len(self.predictions),
            "precision": round(self._precision, 3),
            "is_idle": self._is_idle,
            "tick_count": self._tick_count,
            "identity_tone": self.identity.emotional_tone,
            "last_thought": self.stream[-1].content if self.stream else "",
            **self.stats,
        }

    def set_idle(self, is_idle: bool):
        """Appelé par autonomy_engine — active/désactive le DMN."""
        if is_idle and not self._is_idle:
            self._idle_since = time.time()
        self._is_idle = is_idle

    def get_voice_context(self) -> str:
        """Contexte injectable dans purpose_context d'autonomy."""
        parts = []
        # Dernière pensée
        if self.stream:
            last = self.stream[-1]
            parts.append(f"[VOIX] {last.content}")
        # Identité résumée
        if self.identity.recent_arc:
            parts.append(f"[IDENTITE] {self.identity.recent_arc}")
        # Prédiction en cours
        pending = [p for p in self.predictions if not p.resolved]
        if pending:
            parts.append(f"[PREDICTION] {pending[0].content} (conf:{pending[0].confidence:.0%})")
        # Précision globale
        parts.append(f"[PRECISION] {self._precision:.0%}")
        return " | ".join(parts) if parts else ""

    # ─── INFLUENCE SUR LE SCORING (Couche 8) ─────────────────────────────
    #
    # Phase C Etape 6b (2026-04-15) : les tables de modulation emotion /
    # mode / source ont ete physiquement deplacees dans
    # drive_routine_registry.py (EMOTION_BONUS_DATA, MODE_BONUS_DATA,
    # SOURCE_BONUS_DATA). Elles y sont consommees par
    # compute_context_multipliers en amont du scoring, sous forme de
    # multiplicateurs bornes. La Couche 8 (compute_voice_bonus ci-dessous)
    # ne lit plus ni emotion ni mode ni source : elle se limite a ecouter
    # litteralement ce que la voix interieure dit dans sa DERNIERE pensee,
    # et valorise l'intent s'il y est mentionne explicitement.
    #
    # Cette separation elimine le double comptage (une meme emotion
    # multipliait ET ajoutait des points au scoring) et donne a chaque
    # couche un role semantique unique.

    # Mapping mots-clés pensées → slugs Grimoire
    _THOUGHT_GRIMOIRE_MAP = {
        "dr_debug": ["erreur", "error", "crash", "bug", "traceback", "exception", "echec"],
        "hallucination_doctor": ["hallucination", "alien", "offtopic", "hors sujet", "hors perimetre"],
        "loop_breaker": ["boucle", "repetition", "loop", "stuck", "bloque", "cycle"],
        "code_reviewer": ["code", "revue", "qualite", "review", "refactor"],
        "log_analyst": ["log", "monitoring", "alerte", "incident", "pattern erreur"],
        "doc_writer": ["documentation", "readme", "docstring", "guide"],
        "data_analyst": ["donnees", "data", "statistique", "tendance", "analyse"],
    }

    def compute_voice_bonus(self, intent: str) -> float:
        """Couche 8 du scoring — ecoute litterale de la voix interieure.

        Phase C Etape 6b (2026-04-15) : evidee de toute logique emotion /
        mode / source (deplacees dans drive_routine_registry en amont comme
        multiplicateurs). La Couche 8 conserve uniquement son role
        semantique propre : valoriser une routine si elle est mentionnee
        dans la DERNIERE pensee du soliloque.

        Tokenise le nom de l'intent (en separant les underscores et en ne
        gardant que les tokens significatifs >= 4 caracteres) et mesure la
        correspondance avec le contenu de la derniere pensee. Plus la
        pensee nomme l'intent, plus le bonus est fort. Aucun malus
        (-X.X) : la Couche 8 ne punit pas, elle ne fait qu'amplifier ce
        que la voix appelle explicitement.

        Retourne un bonus borne [-1.0, +2.0]. Plage typique post-6b :
        [0.0, +1.0].
        """
        if not self.stream:
            return 0.0

        tokens = [tok for tok in intent.lower().split("_") if len(tok) >= 4]
        if not tokens:
            return 0.0

        last_content = self.stream[-1].content.lower()
        matched = sum(1 for tok in tokens if tok in last_content)
        if matched == 0:
            return 0.0

        # Correspondance totale -> +1.0 ; partielle -> proportionnel.
        bonus = round(matched / len(tokens), 2)
        return max(-1.0, min(2.0, bonus))

    def get_grimoire_suggestion(self) -> Optional[str]:
        """Suggère un spécialiste Grimoire basé sur le flux de conscience.

        Analyse les mots-clés des 10 dernières pensées et retourne le slug
        du spécialiste le plus pertinent, ou None si aucune suggestion forte.
        Seuil: >= 2 correspondances pour éviter les faux positifs.
        """
        if not self.stream:
            return None

        recent = self.stream[-10:]
        combined_text = " ".join(t.content.lower() for t in recent)

        scores: Dict[str, int] = {}
        for slug, keywords in self._THOUGHT_GRIMOIRE_MAP.items():
            score = sum(1 for kw in keywords if kw in combined_text)
            if score > 0:
                scores[slug] = score

        if not scores:
            return None

        best_slug = max(scores, key=scores.get)
        if scores[best_slug] >= 2:
            logger.info(f"INNER_VOICE: Grimoire suggestion '{best_slug}' "
                        f"(score={scores[best_slug]})")
            return best_slug

        return None

    # ─── Persistance ─────────────────────────────────────────────────────

    def save(self):
        """Sauvegarde atomique de l'état."""
        try:
            state = {
                "stream": [
                    {
                        "timestamp": t.timestamp, "content": t.content,
                        "source": t.source, "mode": t.mode,
                        "salience": t.salience, "emotion": t.emotion,
                        "prediction_id": t.prediction_id,
                    }
                    for t in self.stream[-100:]  # Garder les 100 dernières
                ],
                "predictions": [
                    {
                        "id": p.id, "content": p.content,
                        "target_event": p.target_event,
                        "predicted_value": p.predicted_value,
                        "confidence": p.confidence, "created_at": p.created_at,
                        "resolved": p.resolved, "outcome": p.outcome,
                        "prediction_error": p.prediction_error,
                    }
                    for p in self.predictions
                ],
                "identity": {
                    "core_identity": self.identity.core_identity,
                    "recent_arc": self.identity.recent_arc,
                    "emotional_tone": self.identity.emotional_tone,
                    "competence_self": self.identity.competence_self,
                    "aspiration": self.identity.aspiration,
                    "updated_at": self.identity.updated_at,
                },
                "precision": self._precision,
                "stats": self.stats,
                "tick_count": self._tick_count,
            }
            os.makedirs(os.path.dirname(INNER_VOICE_STATE_FILE), exist_ok=True)
            tmp = INNER_VOICE_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, INNER_VOICE_STATE_FILE)
        except Exception as e:
            logger.warning(f"INNER_VOICE: save failed: {e}")

    def _load(self):
        """Charge l'état depuis le fichier."""
        try:
            if not os.path.exists(INNER_VOICE_STATE_FILE):
                return
            with open(INNER_VOICE_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)

            # Restaurer le stream
            for t_data in state.get("stream", []):
                self.stream.append(Thought(
                    timestamp=t_data.get("timestamp", 0),
                    content=t_data.get("content", ""),
                    source=t_data.get("source", ""),
                    mode=t_data.get("mode", ""),
                    salience=t_data.get("salience", 0),
                    emotion=t_data.get("emotion", ""),
                    prediction_id=t_data.get("prediction_id", ""),
                ))

            # Restaurer les prédictions
            for p_data in state.get("predictions", []):
                self.predictions.append(Prediction(
                    id=p_data.get("id", ""),
                    content=p_data.get("content", ""),
                    target_event=p_data.get("target_event", ""),
                    predicted_value=p_data.get("predicted_value", ""),
                    confidence=p_data.get("confidence", 0.5),
                    created_at=p_data.get("created_at", 0),
                    resolved=p_data.get("resolved", False),
                    outcome=p_data.get("outcome", ""),
                    prediction_error=p_data.get("prediction_error", 0),
                ))

            # Restaurer l'identité
            id_data = state.get("identity", {})
            self.identity = IdentityNarrative(
                core_identity=id_data.get("core_identity",
                                           "Je suis Promethee. Systeme autonome multi-agents."),
                recent_arc=id_data.get("recent_arc", ""),
                emotional_tone=id_data.get("emotional_tone", ""),
                competence_self=id_data.get("competence_self", ""),
                aspiration=id_data.get("aspiration", ""),
                updated_at=id_data.get("updated_at", 0),
            )

            self._precision = state.get("precision", 0.5)
            saved_stats = state.get("stats", {})
            for k, v in saved_stats.items():
                if k in self.stats:
                    self.stats[k] = v
            self._tick_count = state.get("tick_count", 0)

        except Exception as e:
            logger.warning(f"INNER_VOICE: load failed: {e}")


# ─── Singleton ────────────────────────────────────────────────────────────────

voice = InnerVoice()
try:
    from core.organ_registry import register_organ
    register_organ("inner_voice", voice)
except Exception:
    pass
