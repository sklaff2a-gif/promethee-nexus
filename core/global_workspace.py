"""
Global Workspace — Theorie de Baars appliquee a Promethee.

Phase 7. La conscience emerge quand l'information traverse un goulot
d'etranglement ou tous les systemes competissent pour l'attention.

Les organes soumettent des "contenus de conscience" (pensees, alertes,
intuitions). Le workspace selectionne les plus saillants (max 7 slots).
Ce qui entre dans le workspace est "conscient" — injecte dans le
purpose_context des agents. Le reste est traite inconsciemment.

Singleton. 0 appel LLM.
"""

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("GlobalWorkspace")

# --- Constantes ---
MAX_CONSCIOUS_SLOTS = 7     # Capacite du workspace (theorie de Miller : 7±2)
MIN_CONSCIOUS_SLOTS = 3     # Minimum de contenus (eviter le vide)
CONTENT_TTL = 120.0         # Duree de vie d'un contenu (2 min)
SALIENCE_DECAY = 0.1        # Decay par cycle pour les contenus non renouveles

# --- Reentrant processing (inspire Dehaene GNW) ---
REENTRANT_CYCLES = 3        # Nombre de cycles iteratifs par tick
PERSISTENCE_BONUS = 0.05    # Bonus saillance pour contenus qui survivent un cycle

# --- Ignition non-lineaire (inspire GWT/LIDA) ---
# Le seuil de conscience est module par l'arousal cardiaque.
# En panique (BPM haut) : seuil bas → plus de contenus accedent a la conscience
# Au repos (BPM bas) : seuil haut → seuls les plus saillants passent
IGNITION_BASE_THRESHOLD = 0.35   # Seuil de base (saillance minimale pour acceder)
IGNITION_K = 5.0                 # Pente de la sigmoid
IGNITION_MIDPOINT = 0.5          # Point d'inflexion de l'arousal [0,1]

# Categories de contenus
CATEGORIES = frozenset({
    "urgence", "cognition", "emotion", "memory",
    "body", "motivation", "social", "creation",
})

# Priorites (critical bypass le filtre)
PRIORITIES = ("critical", "high", "normal", "low")

# Bonus de saillance par mode dominant (signaux descendants)
# Quand le mode dominant est "urgence", les contenus "urgence" ont un boost
_MODE_CATEGORY_AFFINITY = {
    "urgence": {"urgence": 0.3, "body": 0.1},
    "exploration": {"cognition": 0.3, "memory": 0.1},
    "consolidation": {"memory": 0.3, "cognition": 0.1},
    "creation": {"creation": 0.3, "motivation": 0.1},
    "repos": {"body": 0.2, "emotion": 0.1},
    "vigilance": {"urgence": 0.2, "body": 0.2},
    "social": {"social": 0.3, "emotion": 0.1},
}


@dataclass
class ConsciousContent:
    """Un contenu de conscience propose par un organe."""
    source: str           # Organe source (ex: "cardiac", "prefrontal")
    content: str          # Le texte de la pensee
    salience: float       # [0, 1] importance de base
    category: str         # Categorie du contenu
    priority: str = "normal"  # "critical" bypass le filtre
    timestamp: float = field(default_factory=time.time)

    @property
    def is_critical(self) -> bool:
        return self.priority == "critical"

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.timestamp) > CONTENT_TTL


class GlobalWorkspace:
    """Espace de travail global — goulot d'etranglement conscient."""

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

        # Contenus soumis par les organes (buffer)
        self._submissions: List[ConsciousContent] = []
        # Contenus actuellement "conscients" (dans le workspace)
        self.conscious_contents: List[ConsciousContent] = []
        # Mode dominant courant (des signaux descendants)
        self._dominant_mode: str = ""
        # Stats
        self._stats = {
            "total_submissions": 0,
            "total_competitions": 0,
            "total_conscious": 0,
            "total_filtered": 0,
        }
        self._subscribed = False
        self._subscribe_events()

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        try:
            from core.event_bus.bus import bus
            bus.subscribe("BRAIN_TICK", self._on_brain_tick)
        except Exception as e:
            logger.warning(f"WORKSPACE: Echec souscription bus: {e}")

    # ============================================================
    # Soumission de contenus
    # ============================================================

    def submit(self, source: str, content: str, salience: float,
               category: str = "cognition", priority: str = "normal"):
        """Un organe soumet un contenu de conscience au workspace.

        Le contenu sera evalue lors de la prochaine competition.
        """
        if not content or not content.strip():
            return
        if category not in CATEGORIES:
            category = "cognition"
        if priority not in PRIORITIES:
            priority = "normal"

        salience = max(0.0, min(1.0, salience))

        self._submissions.append(ConsciousContent(
            source=source,
            content=content.strip(),
            salience=salience,
            category=category,
            priority=priority,
        ))
        self._stats["total_submissions"] += 1

    # ============================================================
    # Competition pour la conscience
    # ============================================================

    def compete(self, dominant_mode: str = "") -> List[ConsciousContent]:
        """Competition : selectionne les contenus les plus saillants.

        1. Les contenus critical entrent directement (bypass)
        2. Les contenus expires sont supprimes
        3. Les contenus sont tries par saillance effective (base + mode bonus)
        4. Top MAX_CONSCIOUS_SLOTS selectionnes
        """
        self._dominant_mode = dominant_mode
        self._stats["total_competitions"] += 1

        # Purger les contenus expires des soumissions
        now = time.time()
        self._submissions = [
            c for c in self._submissions
            if (now - c.timestamp) <= CONTENT_TTL
        ]

        # Combiner soumissions + contenus conscients encore valides
        all_candidates = list(self._submissions)
        for c in self.conscious_contents:
            if not c.is_expired:
                # Decay de saillance pour les anciens contenus
                c.salience = max(0.0, c.salience - SALIENCE_DECAY)
                if c.salience > 0.05:
                    all_candidates.append(c)

        if not all_candidates:
            self.conscious_contents = []
            return []

        # Deduplication par source (garder le plus recent)
        by_source = {}
        for c in all_candidates:
            existing = by_source.get(c.source)
            if not existing or c.timestamp > existing.timestamp:
                by_source[c.source] = c
        candidates = list(by_source.values())

        # Separer critical (bypass) et normaux
        critical = [c for c in candidates if c.is_critical]
        normal = [c for c in candidates if not c.is_critical]

        # Saillance effective = base + bonus mode dominant + bonus binding
        synced_sources = set()
        try:
            from core.brain_vm import brain
            for pair in brain.get_synchronized_pairs(threshold=0.5):
                synced_sources.update(pair)
        except Exception:
            pass

        for c in normal:
            mode_bonus = _MODE_CATEGORY_AFFINITY.get(dominant_mode, {}).get(c.category, 0.0)
            # Bonus binding : les contenus d'organes synchronises sont plus saillants
            binding_bonus = 0.1 if c.source in synced_sources else 0.0
            c._effective_salience = c.salience + mode_bonus + binding_bonus

        # Trier par saillance effective decroissante
        normal.sort(key=lambda c: getattr(c, "_effective_salience", c.salience), reverse=True)

        # Ignition non-lineaire : filtrer par seuil dynamique (arousal cardiaque)
        ignition_threshold = self._compute_ignition_threshold()
        above_threshold = [c for c in normal
                           if getattr(c, "_effective_salience", c.salience) >= ignition_threshold]
        # Garantir un minimum de contenus (meme sous le seuil)
        if len(above_threshold) < MIN_CONSCIOUS_SLOTS:
            above_threshold = normal[:MIN_CONSCIOUS_SLOTS]

        # Assembler : critical d'abord, puis normaux au-dessus du seuil
        remaining_slots = MAX_CONSCIOUS_SLOTS - len(critical)
        selected_normal = above_threshold[:max(0, remaining_slots)]

        self.conscious_contents = critical + selected_normal
        self._stats["total_conscious"] += len(self.conscious_contents)
        self._stats["total_filtered"] += len(candidates) - len(self.conscious_contents)

        # Vider le buffer de soumissions (consomme)
        self._submissions = []

        return self.conscious_contents

    # ============================================================
    # Generation du purpose_context conscient
    # ============================================================

    def get_conscious_context(self, dominant_mode: str = "") -> str:
        """Genere le purpose_context a partir des contenus conscients.

        Appele par autonomy_engine au lieu de la concatenation de 16 sources.
        """
        contents = self.compete(dominant_mode)

        if not contents:
            return ""

        lines = ["[CONSCIENCE GLOBALE]"]
        for i, c in enumerate(contents, 1):
            prefix = "!!" if c.is_critical else f"{i}."
            lines.append(f"  {prefix} [{c.source.upper()}] {c.content}")

        return "\n".join(lines)

    # ============================================================
    # Collecte depuis les organes
    # ============================================================

    def collect_from_organs(self):
        """Collecte les contenus de conscience depuis tous les organes.

        Chaque organe fournit un get_*_context() — on le wrappe en
        ConsciousContent avec une saillance estimee.
        """
        # Mapping : (module, singleton, method, category, base_salience, priority)
        _ORGAN_SOURCES = [
            ("core.self_awareness", "awareness", "get_purpose_context", "cognition", 0.5, "normal"),
            ("core.prefrontal", "prefrontal", "get_deliberation_context", "cognition", 0.7, "high"),
            ("core.inner_voice", "voice", "get_voice_context", "emotion", 0.4, "normal"),
            ("core.corpus_callosum", "callosum", "get_cognitive_context", "cognition", 0.6, "normal"),
            ("core.hippocampus", "hippocampus", "get_hippocampus_context", "memory", 0.4, "normal"),
            ("core.roadmap_engine", "roadmap", "get_roadmap_context", "cognition", 0.5, "normal"),
            ("core.neural_tissue", "tissue", "get_tissue_context", "body", 0.3, "normal"),
            ("core.hypothalamus", "hypothalamus", "get_homeostasis_context", "body", 0.4, "normal"),
            ("core.insula", "insula", "get_body_awareness_context", "body", 0.3, "normal"),
            ("core.cingulate_cortex", "cingulate", "get_conflict_context", "urgence", 0.6, "high"),
            ("core.basal_ganglia", "ganglia", "get_habit_context", "cognition", 0.3, "low"),
            ("core.default_mode_network", "dmn", "get_dmn_context", "creation", 0.3, "low"),
            ("core.incubation_cognitive", "incubation", "get_incubation_context", "creation", 0.4, "normal"),
            ("core.curiosity_reflex", "curiosity", "get_curiosity_context", "cognition", 0.5, "normal"),
            ("core.sensorium", "sensorium", "get_sensorium_context", "body", 0.3, "normal"),
        ]

        for mod_path, singleton_name, method_name, category, base_salience, priority in _ORGAN_SOURCES:
            try:
                import importlib
                mod = importlib.import_module(mod_path)
                singleton = getattr(mod, singleton_name)
                method = getattr(singleton, method_name)
                ctx = method()
                if ctx and isinstance(ctx, str) and ctx.strip():
                    # Ajuster la saillance selon la longueur (plus long = plus informatif)
                    length_bonus = min(0.2, len(ctx) / 500.0)
                    self.submit(
                        source=singleton_name,
                        content=ctx,
                        salience=min(1.0, base_salience + length_bonus),
                        category=category,
                        priority=priority,
                    )
            except Exception:
                pass

        # Reptilian : toujours soumis si menace > 3
        try:
            from core.reptilian_core import reptile
            if reptile.threat_level >= 3.0:
                self.submit(
                    source="reptilian",
                    content=f"ALERTE REPTILIENNE: menace niveau {reptile.threat_level:.1f}, mode {reptile.mode}",
                    salience=min(1.0, reptile.threat_level / 10.0),
                    category="urgence",
                    priority="critical" if reptile.threat_level >= 6.0 else "high",
                )
        except Exception:
            pass

    # ============================================================
    # Handler bus
    # ============================================================

    def _compute_ignition_threshold(self) -> float:
        """Ignition non-lineaire : seuil de conscience module par l'arousal cardiaque.

        Inspire de GWT/LIDA : en situation de stress (BPM eleve, emotion intense),
        le seuil baisse et plus d'informations accedent a la conscience.
        Au repos, seules les plus saillantes percent.
        Sigmoid : threshold = base * sigmoid(arousal)
        """
        arousal = 0.5  # Valeur par defaut (neutre)
        try:
            from core.cardiac_engine import heart
            # Arousal = combinaison BPM normalise + intensite emotionnelle
            bpm_norm = min(1.0, max(0.0, (heart.bpm - 50) / 60))  # 50-110 BPM → 0-1
            intensity = getattr(heart, "emotion_intensity", 0.3)
            arousal = bpm_norm * 0.6 + intensity * 0.4
        except Exception:
            pass

        # Sigmoid inversee : arousal haut → seuil bas (plus de contenus passent)
        sigmoid = 1.0 / (1.0 + math.exp(IGNITION_K * (arousal - IGNITION_MIDPOINT)))
        threshold = IGNITION_BASE_THRESHOLD * (0.5 + sigmoid)  # [0.175, 0.525]

        return threshold

    async def _on_brain_tick(self, event: dict):
        """BRAIN_TICK : collecter les organes et mettre a jour la conscience.

        Reentrant processing (inspire Dehaene GNW) : au lieu d'une seule
        competition, on fait REENTRANT_CYCLES iterations. A chaque cycle,
        les contenus qui survivent recoivent un bonus de persistance.
        Seuls les contenus stables (presents apres 3 cycles) deviennent
        "veritablement conscients" — les contenus ephemeres sont filtres.
        """
        self._dominant_mode = event.get("dominant_mode", "")
        try:
            self.collect_from_organs()
            for cycle in range(REENTRANT_CYCLES):
                previous_sources = {c.source for c in self.conscious_contents}
                self.compete(self._dominant_mode)
                # Bonus persistance : les contenus qui etaient deja conscients
                # au cycle precedent recoivent un bonus de saillance
                if cycle > 0:
                    for c in self.conscious_contents:
                        if c.source in previous_sources:
                            c.salience = min(1.0, c.salience + PERSISTENCE_BONUS)
        except Exception:
            pass

    # ============================================================
    # API
    # ============================================================

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'etat du workspace pour API/dashboard."""
        return {
            "conscious_count": len(self.conscious_contents),
            "max_slots": MAX_CONSCIOUS_SLOTS,
            "dominant_mode": self._dominant_mode,
            "contents": [
                {
                    "source": c.source,
                    "category": c.category,
                    "salience": round(c.salience, 2),
                    "priority": c.priority,
                    "preview": c.content[:80],
                }
                for c in self.conscious_contents
            ],
            "pending_submissions": len(self._submissions),
            "stats": self._stats,
        }


# Singleton global
workspace = GlobalWorkspace()
