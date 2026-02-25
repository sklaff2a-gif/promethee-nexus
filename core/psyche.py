# core/psyche.py — PSYCHE : Moteur de Personnalité pour PROMÉTHÉE
# Traits dynamiques par agent, forgés par l'expérience et les débats Council.

import json
import os
import logging
from datetime import date, datetime
from typing import Dict, List, Optional, Any

from core.event_bus.bus import bus

logger = logging.getLogger("Psyche")

STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "psyche_state.json"
)

# --- Les 6 traits et leurs baselines ---
TRAIT_NAMES = ("curiosite", "creativite", "audace", "savoir", "survie", "respect")
BASELINES = {
    "curiosite": 50.0, "creativite": 50.0, "audace": 50.0,
    "savoir": 50.0, "survie": 60.0, "respect": 55.0,
}

# Offsets par agent (delta sur la baseline)
AGENT_OFFSETS: Dict[str, Dict[str, float]] = {
    "strategist":  {"curiosite": 5,  "creativite": 5,   "audace": -5,  "savoir": 10, "survie": 5,   "respect": 10},
    "coder":       {"curiosite": 10, "creativite": 10,  "audace": 10,  "savoir": 5,  "survie": -5,  "respect": 0},
    "architect":   {"curiosite": 0,  "creativite": -5,  "audace": -10, "savoir": 5,  "survie": 15,  "respect": 5},
    "factory":     {"curiosite": -10,"creativite": -10, "audace": 0,   "savoir": -5, "survie": 10,  "respect": 0},
    "evolution":   {"curiosite": 15, "creativite": 15,  "audace": 15,  "savoir": 10, "survie": -10, "respect": 0},
    "infra":       {"curiosite": -5, "creativite": -10, "audace": -5,  "savoir": 0,  "survie": 15,  "respect": 5},
    "security":    {"curiosite": 5,  "creativite": 0,   "audace": -5,  "savoir": 5,  "survie": 15,  "respect": 10},
    "writer":      {"curiosite": 5,  "creativite": 15,  "audace": 5,   "savoir": 10, "survie": -5,  "respect": 5},
    "researcher":  {"curiosite": 15, "creativite": 5,   "audace": 5,   "savoir": 15, "survie": -5,  "respect": 0},
    "formatter":   {"curiosite": -10,"creativite": -5,  "audace": -10, "savoir": 0,  "survie": 10,  "respect": 5},
}

# Matrice d'affinité trait → routine (pour le scoring)
ROUTINE_AFFINITY: Dict[str, Dict[str, float]] = {
    "EXPANSION_CODE":     {"creativite": 0.5, "audace": 0.3, "curiosite": 0.2},
    "AUDIT_STRUCTURE":    {"survie": 0.5, "respect": 0.3, "savoir": 0.2},
    "VEILLE_SILENCIEUSE": {"curiosite": 0.5, "savoir": 0.4, "creativite": 0.1},
    "DROPZONE_SCAN":      {"savoir": 0.4, "curiosite": 0.3, "respect": 0.3},
    "COUNCIL_DEBATE":     {"respect": 0.3, "curiosite": 0.3, "creativite": 0.2, "audace": 0.2},
    "GRIMOIRE_INVOKE":    {"savoir": 0.4, "curiosite": 0.3, "creativite": 0.3},
    "SECURITY_AUDIT":     {"survie": 0.5, "respect": 0.3, "savoir": 0.2},
    "MEMORY_CLEANUP":     {"survie": 0.4, "respect": 0.4, "savoir": 0.2},
    "REFACTOR_RANDOM":    {"respect": 0.4, "creativite": 0.3, "savoir": 0.3},
}

# Decay quotidien : 2% de retour vers la baseline
DECAY_FACTOR = 0.98

# Thèmes de recherche rotatifs pour alimenter les débats Council
RESEARCH_THEMES = [
    {
        "query": "multi-agent AI architecture patterns autonomous systems 2025 2026",
        "participants": ["researcher", "evolution", "coder"],
        "framing": "Le Researcher a découvert des innovations en architecture multi-agents. "
                   "Comment les adapter pour améliorer Prométhée ?",
    },
    {
        "query": "AI agent resource optimization low memory local LLM inference techniques",
        "participants": ["infra", "strategist", "architect"],
        "framing": "Le Researcher a trouvé des techniques d'optimisation des ressources pour les agents IA. "
                   "Lesquelles appliquer pour réduire notre empreinte CPU/RAM ?",
    },
    {
        "query": "autonomous AI self-improvement scaling strategies without human intervention",
        "participants": ["evolution", "strategist", "coder"],
        "framing": "Le Researcher a identifié des stratégies de scalabilité autonome. "
                   "Comment Prométhée pourrait-il grandir par lui-même ?",
    },
    {
        "query": "AI agent skills plugins game changer open source 2025 2026",
        "participants": ["researcher", "coder", "evolution"],
        "framing": "Le Researcher a repéré de nouveaux skills/plugins pour agents IA. "
                   "Lesquels seraient game-changer pour Prométhée ?",
    },
    {
        "query": "LLM orchestration multi-agent debate consensus protocol best practices",
        "participants": ["strategist", "coder", "writer"],
        "framing": "Le Researcher a trouvé des méthodes pour améliorer les débats entre agents IA. "
                   "Comment optimiser nos protocoles Council ?",
    },
    {
        "query": "RAG retrieval augmented generation vector memory optimization agent",
        "participants": ["researcher", "architect", "coder"],
        "framing": "Le Researcher a découvert des avancées en mémoire vectorielle RAG. "
                   "Comment améliorer notre système de mémoire collective ?",
    },
    {
        "query": "AI agent security hardening autonomous system protection self-healing",
        "participants": ["security", "architect", "strategist"],
        "framing": "Le Researcher a identifié des techniques de sécurisation pour systèmes IA autonomes. "
                   "Comment renforcer la résilience de Prométhée ?",
    },
    {
        "query": "event driven architecture pub sub pattern AI agent communication 2025",
        "participants": ["architect", "coder", "infra"],
        "framing": "Le Researcher a trouvé des patterns de communication inter-agents innovants. "
                   "Comment améliorer notre Event Bus ?",
    },
]


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


class PsycheEngine:
    """Singleton — moteur de personnalité dynamique pour tous les agents."""

    _instance: Optional["PsycheEngine"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.agents: Dict[str, Dict[str, float]] = {}
        self.history: List[Dict[str, Any]] = []
        self.last_decay_day: Optional[str] = None
        self._subscribed = False

    # --- Init & Reset ---

    def init(self, agent_names: List[str] = None):
        """Initialise les traits pour les agents donnés, charge l'état persisté."""
        loaded = self._load()
        if loaded:
            self.agents = loaded.get("agents", {})
            self.history = loaded.get("history", [])
            self.last_decay_day = loaded.get("last_decay_day")

        # Initialiser les agents manquants
        names = agent_names or list(AGENT_OFFSETS.keys())
        for name in names:
            if name not in self.agents:
                self.agents[name] = self._default_traits(name)

        self._subscribe_events()
        logger.info(f"PSYCHE: Moteur de personnalité actif ({len(self.agents)} agents)")

    def reset(self):
        """Reset complet (utilisé par les tests)."""
        self.agents = {}
        self.history = []
        self.last_decay_day = None
        self._subscribed = False
        self._initialized = False

    @classmethod
    def reset_singleton(cls):
        """Reset le singleton (utilisé par les tests)."""
        if cls._instance is not None:
            cls._instance.reset()
            cls._instance = None

    # --- Traits par défaut ---

    @staticmethod
    def _default_traits(agent_name: str) -> Dict[str, float]:
        offsets = AGENT_OFFSETS.get(agent_name, {})
        return {
            trait: _clamp(BASELINES[trait] + offsets.get(trait, 0))
            for trait in TRAIT_NAMES
        }

    # --- Accès aux traits ---

    def get_traits(self, agent_name: str) -> Dict[str, float]:
        if agent_name not in self.agents:
            self.agents[agent_name] = self._default_traits(agent_name)
        return dict(self.agents[agent_name])

    def get_all_traits(self) -> Dict[str, Dict[str, float]]:
        return {name: dict(traits) for name, traits in self.agents.items()}

    def get_dominant_trait(self, agent_name: str) -> tuple:
        """Retourne (nom_trait, valeur) du trait le plus élevé pour un agent."""
        traits = self.get_traits(agent_name)
        dominant = max(traits.items(), key=lambda x: x[1])
        return dominant

    def get_system_average(self) -> Dict[str, float]:
        """Moyenne des traits sur tous les agents (pour le radar chart global)."""
        if not self.agents:
            return dict(BASELINES)
        avg = {t: 0.0 for t in TRAIT_NAMES}
        for traits in self.agents.values():
            for t in TRAIT_NAMES:
                avg[t] += traits.get(t, BASELINES[t])
        n = len(self.agents)
        return {t: round(avg[t] / n, 1) for t in TRAIT_NAMES}

    # --- Modification des traits ---

    def apply_deltas(self, agent_name: str, deltas: Dict[str, float],
                     event: str = "manual"):
        if agent_name == "_system":
            for name in self.agents:
                self._apply_deltas_single(name, deltas)
        elif agent_name in self.agents:
            self._apply_deltas_single(agent_name, deltas)
        else:
            self.agents[agent_name] = self._default_traits(agent_name)
            self._apply_deltas_single(agent_name, deltas)

        # Historique (max 100 entrées)
        self.history.append({
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "agents_affected": [agent_name] if agent_name != "_system" else list(self.agents.keys()),
            "trait_changes": deltas,
        })
        if len(self.history) > 100:
            self.history = self.history[-100:]

    def _apply_deltas_single(self, agent_name: str, deltas: Dict[str, float]):
        traits = self.agents.get(agent_name, self._default_traits(agent_name))
        for trait, delta in deltas.items():
            if trait in TRAIT_NAMES:
                traits[trait] = _clamp(traits[trait] + delta)
        self.agents[agent_name] = traits

    # --- Personality bias pour le scoring des routines ---

    def compute_personality_bias(self, intent: str) -> float:
        """Calcule le bonus de personnalité pour une routine donnée.
        Basé sur la moyenne système des traits et la matrice d'affinité.
        Retourne un float dans [-1.0, +1.0] environ.
        """
        affinity = ROUTINE_AFFINITY.get(intent)
        if not affinity:
            return 0.0
        avg = self.get_system_average()
        bias = 0.0
        for trait, weight in affinity.items():
            bias += (avg[trait] - 50.0) / 50.0 * weight
        return round(bias, 3)

    # --- Decay quotidien ---

    def apply_daily_decay(self):
        """Régresse chaque trait de 2% vers sa baseline. Appelé 1x/jour."""
        today = date.today().isoformat()
        if self.last_decay_day == today:
            return  # Déjà fait aujourd'hui

        for agent_name, traits in self.agents.items():
            baselines = {t: BASELINES[t] + AGENT_OFFSETS.get(agent_name, {}).get(t, 0)
                         for t in TRAIT_NAMES}
            for trait in TRAIT_NAMES:
                base = _clamp(baselines[trait])
                current = traits[trait]
                traits[trait] = round(base + (current - base) * DECAY_FACTOR, 2)

        self.last_decay_day = today
        self.save()
        logger.info("PSYCHE: Decay quotidien appliqué")

    # --- Persistance (JSON atomique) ---

    def _load(self) -> Optional[dict]:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def save(self):
        """Sauvegarde atomique de l'état complet."""
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        state = {
            "version": "1.0",
            "last_decay_day": self.last_decay_day,
            "agents": self.agents,
            "history": self.history[-50:],  # Garder les 50 dernières entrées
        }
        tmp_path = STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, STATE_FILE)

    # --- Event Handlers ---

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        bus.subscribe("COUNCIL_END", self._on_council_end)
        bus.subscribe("COUNCIL_TURN", self._on_council_turn)
        bus.subscribe("ARTIFACT_CREATED", self._on_artifact_created)
        bus.subscribe("CI_PIPELINE_RESULT", self._on_ci_result)
        bus.subscribe("AUTONOMY_HEARTBEAT", self._on_heartbeat)
        bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)

    async def _on_council_end(self, event: dict):
        participants = event.get("participants", [])
        status = event.get("status", "")
        if status == "consensus":
            for p in participants:
                self.apply_deltas(p, {"respect": 1.0, "creativite": 0.5, "savoir": 0.3},
                                  event="COUNCIL_END/consensus")
        else:
            for p in participants:
                self.apply_deltas(p, {"audace": 0.5, "respect": -0.2, "curiosite": 0.3},
                                  event="COUNCIL_END/max_rounds")
        await self._publish_update()
        self.save()

    async def _on_council_turn(self, event: dict):
        agent = event.get("agent", "")
        if agent:
            self.apply_deltas(agent, {"curiosite": 0.1, "respect": 0.1},
                              event="COUNCIL_TURN")

    async def _on_artifact_created(self, event: dict):
        self.apply_deltas("factory", {"creativite": 0.5, "audace": 0.3},
                          event="ARTIFACT_CREATED")
        self.save()

    async def _on_ci_result(self, event: dict):
        agent = event.get("agent", "factory")
        if event.get("success"):
            self.apply_deltas(agent, {"survie": 0.5, "savoir": 0.3, "audace": 0.2},
                              event="CI_PIPELINE_RESULT/success")
        else:
            self.apply_deltas(agent, {"survie": 0.8, "audace": -0.3, "respect": 0.2},
                              event="CI_PIPELINE_RESULT/failure")
        await self._publish_update()
        self.save()

    async def _on_heartbeat(self, event: dict):
        health = event.get("health", {})
        verdict = health.get("verdict", "GO")
        if verdict == "NO_GO":
            self.apply_deltas("_system", {"survie": 0.5, "audace": -0.2},
                              event="HEARTBEAT/NO_GO")
            self.save()
        elif verdict == "DEGRADED":
            self.apply_deltas("_system", {"survie": 0.2},
                              event="HEARTBEAT/DEGRADED")
            self.save()

    async def _on_routine_complete(self, event: dict):
        intent = event.get("intent", "")
        participants = event.get("participants", [])
        deltas_map = {
            "VEILLE_SILENCIEUSE": ("researcher", {"curiosite": 0.5, "savoir": 0.4}),
            "EXPANSION_CODE":     ("evolution",  {"creativite": 0.4, "audace": 0.2}),
            "AUDIT_STRUCTURE":    ("architect",  {"survie": 0.3, "respect": 0.1}),
        }
        if intent == "COUNCIL_DEBATE":
            for p in participants:
                self.apply_deltas(p, {"respect": 0.5, "curiosite": 0.3},
                                  event="ROUTINE/COUNCIL_DEBATE")
        elif intent in deltas_map:
            agent, deltas = deltas_map[intent]
            self.apply_deltas(agent, deltas, event=f"ROUTINE/{intent}")
        await self._publish_update()
        self.save()

    async def _publish_update(self):
        """Publie l'état PSYCHE sur le bus → frontend radar chart."""
        await bus.publish("PSYCHE_UPDATE", {
            "system_average": self.get_system_average(),
            "agents": self.get_all_traits(),
            "timestamp": datetime.now().isoformat(),
        })

    # --- Sélection du sujet de débat Council ---

    def select_council_topic(self, error_streak: int = 0,
                             daily_count: int = 0,
                             debate_index: int = 0,
                             recent_subjects: list = None) -> Dict[str, Any]:
        """Choisit le sujet et les participants du débat autonome.
        debate_index sert à faire tourner les thèmes de recherche.
        recent_subjects : liste des identifiants courts des derniers sujets débattus (pour déduplication).
        Retourne {"participants": [...], "mission": str, "needs_research": bool, "research_query": str|None, "subject_key": str}
        """
        avg = self.get_system_average()
        recent = [s.lower() for s in (recent_subjects or [])]

        # Priorité 1 : situations critiques (cooldown élargi — 1 seul débat par sujet critique par session)
        if error_streak >= 2 and "erreurs" not in recent:
            return {
                "participants": ["strategist", "architect", "security"],
                "mission": "Le système accumule des erreurs. Comment stabiliser la situation ?",
                "needs_research": False, "research_query": None,
                "subject_key": "erreurs",
            }
        if daily_count >= 15 and "budget" not in recent:
            return {
                "participants": ["strategist", "evolution"],
                "mission": "Le budget quotidien est presque épuisé. Comment prioriser les actions restantes ?",
                "needs_research": False, "research_query": None,
                "subject_key": "budget",
            }

        # Priorité 2.5 : eureka — pont créatif non exploré (spreading activation)
        if "eureka" not in recent:
            try:
                from core.spreading_activation import activation_engine
                bridges = activation_engine.get_creative_bridges(unused_only=True)
                strong_bridges = [b for b in bridges if b.bridge_strength >= 0.5]
                if strong_bridges:
                    bridge = strong_bridges[0]
                    return {
                        "participants": ["strategist", "coder", "evolution"],
                        "mission": (
                            f"EUREKA: Le système a détecté une connexion créative entre "
                            f"'{bridge.node_a}' ({bridge.collection_a}) et "
                            f"'{bridge.node_b}' ({bridge.collection_b}). "
                            f"{bridge.hypothesis} "
                            f"Comment exploiter cette connexion pour améliorer Prométhée ?"
                        ),
                        "needs_research": False, "research_query": None,
                        "subject_key": "eureka",
                    }
            except Exception:
                pass

        # Priorité 2.3 : pulsion dominante non assouvie
        if "desir" not in recent:
            try:
                from core.desire_engine import desires
                dominant = max(desires.drives.values(), key=lambda d: d.deprivation)
                if dominant.deprivation >= 75:
                    DRIVE_COUNCIL_TOPICS = {
                        "CURIOSITE": {
                            "participants": ["researcher", "evolution", "strategist"],
                            "mission": "Notre curiosite est inassouvie. Quels territoires inexplores meritent notre attention ?",
                        },
                        "CREATION": {
                            "participants": ["coder", "evolution", "architect"],
                            "mission": "Le besoin de creer est pressant. Quel artefact ambitieux pourrions-nous produire ?",
                        },
                        "CONNEXION": {
                            "participants": ["strategist", "writer", "researcher"],
                            "mission": "L'isolement pese. Comment ameliorer nos protocoles de collaboration et d'echange ?",
                        },
                        "CROISSANCE": {
                            "participants": ["evolution", "coder", "strategist"],
                            "mission": "Le besoin de grandir est imperieux. Quelle capacite nouvelle transformer notre potentiel ?",
                        },
                    }
                    topic = DRIVE_COUNCIL_TOPICS.get(dominant.name)
                    if topic:
                        return {**topic, "needs_research": False, "research_query": None,
                                "subject_key": "desir"}
            except Exception:
                pass

        # Priorité 2 (par défaut) : débat alimenté par la recherche web (rotation)
        # Avancer l'index si le thème a déjà été débattu récemment
        n_themes = len(RESEARCH_THEMES)
        for offset in range(n_themes):
            idx = (debate_index + offset) % n_themes
            theme = RESEARCH_THEMES[idx]
            theme_key = theme["query"][:30].lower()
            if theme_key not in recent[-5:]:
                break
        else:
            # Tous les thèmes ont été débattus récemment — prendre le suivant en rotation
            theme = RESEARCH_THEMES[debate_index % n_themes]
            theme_key = theme["query"][:30].lower()

        # Priorité 3 : traits extrêmes — REMPLACE le thème de recherche (rare)
        if avg.get("curiosite", 50) > 80 and "curiosite" not in recent:
            return {
                "participants": ["researcher", "evolution", "coder"],
                "mission": "La curiosité du système est très élevée. Quel domaine explorer en priorité ?",
                "needs_research": True, "research_query": theme["query"],
                "subject_key": "curiosite",
            }
        if avg.get("survie", 50) > 80 and "survie" not in recent:
            return {
                "participants": ["architect", "security", "strategist"],
                "mission": "L'instinct de survie est extrême. Est-ce de la prudence excessive ?",
                "needs_research": True, "research_query": theme["query"],
                "subject_key": "survie",
            }

        return {
            "participants": theme["participants"],
            "mission": theme["framing"],
            "needs_research": True,
            "research_query": theme["query"],
            "subject_key": theme_key,
        }

    def get_debate_index(self) -> int:
        """Retourne l'index de rotation des débats basé sur l'historique."""
        debate_count = sum(1 for h in self.history if "COUNCIL" in h.get("event", ""))
        return debate_count


# Singleton global
psyche = PsycheEngine()
