# core/objectives_engine.py — Moteur d'Objectifs Autonomes pour PROMÉTHÉE
# Transforme les patterns SelfAwareness en intentions mesurables
# et oriente le scoring des routines vers les buts du système.

import json
import os
import asyncio
import logging
from datetime import datetime, date
from typing import Optional, List, Dict, Any

from core.event_bus.bus import bus

logger = logging.getLogger("ObjectivesEngine")

STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "objectives_state.json"
)

MAX_ACTIVE_OBJECTIVES = 5
MAX_USER_TOPICS = 3

# Pondération par priorité pour le bonus scoring
PRIORITY_WEIGHTS = {"high": 1.0, "medium": 0.7, "low": 0.4}

# Rewards PSYCHE à la complétion d'un objectif
COMPLETION_REWARDS = {
    "performance":  {"survie": 1.0, "respect": 0.5},
    "exploration":  {"curiosite": 1.0, "savoir": 0.5},
    "maintenance":  {"survie": 0.5, "respect": 0.5},
    "evolution":    {"creativite": 1.0, "audace": 0.5, "curiosite": 0.3},
}

# Templates d'objectifs aspirationnels (rotation quotidienne)
DAILY_OBJECTIVE_TEMPLATES = [
    {
        "title": "Explorer un nouveau sujet technique",
        "type": "exploration",
        "priority": "medium",
        "criteria": {"metric": "total_routines", "operator": ">=", "target": 3},
        "routine_affinities": {"VEILLE_SILENCIEUSE": 1.5, "COUNCIL_DEBATE": 0.5},
        "deadline_routines": 40,
    },
    {
        "title": "Deployer une amelioration Evolution",
        "type": "evolution",
        "priority": "medium",
        "criteria": {"metric": "evolution_deployed", "operator": ">=", "target": 1},
        "routine_affinities": {"EXPANSION_CODE": 1.5, "GRIMOIRE_INVOKE": 0.5},
        "deadline_routines": 50,
    },
    {
        "title": "Securiser un module du projet",
        "type": "maintenance",
        "priority": "low",
        "criteria": {"metric": "security_audits", "operator": ">=", "target": 2},
        "routine_affinities": {"SECURITY_AUDIT": 1.5, "AUDIT_STRUCTURE": 0.5},
        "deadline_routines": 40,
    },
    {
        "title": "Enrichir la memoire collective",
        "type": "exploration",
        "priority": "low",
        "criteria": {"metric": "memories_added", "operator": ">=", "target": 3},
        "routine_affinities": {"VEILLE_SILENCIEUSE": 1.0, "COUNCIL_DEBATE": 1.0},
        "deadline_routines": 40,
    },
    {
        "title": "Atteindre un consensus Council",
        "type": "performance",
        "priority": "medium",
        "criteria": {"metric": "consensus_rate", "operator": ">=", "target": 0.5},
        "routine_affinities": {"COUNCIL_DEBATE": 1.5},
        "deadline_routines": 30,
    },
    {
        "title": "Nettoyer et optimiser le systeme",
        "type": "maintenance",
        "priority": "low",
        "criteria": {"metric": "cleanup_done", "operator": ">=", "target": 1},
        "routine_affinities": {"MEMORY_CLEANUP": 1.5, "AUDIT_STRUCTURE": 1.0, "REFACTOR_RANDOM": 0.5},
        "deadline_routines": 30,
    },
]

# Mapping pattern → objectif auto-généré
PATTERN_OBJECTIVE_MAP = {
    "error_streak": {
        "title": "Stabiliser le système",
        "type": "maintenance",
        "priority": "high",
        "criteria": {"metric": "error_streak", "operator": "<=", "target": 1},
        "routine_affinities": {"AUDIT_STRUCTURE": 1.0, "COUNCIL_DEBATE": 0.5},
        "deadline_routines": 20,
    },
    "low_success_rate": {
        "title": "Améliorer le taux de succès",
        "type": "performance",
        "priority": "high",
        "criteria": {"metric": "success_rate", "operator": ">=", "target": 0.7},
        "routine_affinities": {"EXPANSION_CODE": 0.5, "AUDIT_STRUCTURE": 1.0},
        "deadline_routines": 30,
    },
    "cloud_budget_critical": {
        "title": "Réduire la consommation cloud",
        "type": "maintenance",
        "priority": "medium",
        "criteria": {"metric": "cloud_budget_ratio", "operator": "<=", "target": 0.7},
        "routine_affinities": {"AUDIT_STRUCTURE": 0.5, "VEILLE_SILENCIEUSE": 0.3},
        "deadline_routines": 15,
    },
    "health_degraded": {
        "title": "Restaurer la santé système",
        "type": "maintenance",
        "priority": "high",
        "criteria": {"metric": "health_verdict", "operator": "==", "target": "GO"},
        "routine_affinities": {"AUDIT_STRUCTURE": 1.5},
        "deadline_routines": 10,
    },
    "low_consensus": {
        "title": "Améliorer le consensus Council",
        "type": "performance",
        "priority": "medium",
        "criteria": {"metric": "consensus_rate", "operator": ">=", "target": 0.5},
        "routine_affinities": {"COUNCIL_DEBATE": 1.5},
        "deadline_routines": 25,
    },
    "trait_falling": {
        "title": "Relancer le trait {nom}",
        "type": "evolution",
        "priority": "low",
        "criteria": {"metric": "trait_value:{nom}", "operator": ">=", "target": 55},
        "routine_affinities": {"VEILLE_SILENCIEUSE": 0.5, "COUNCIL_DEBATE": 0.5, "EXPANSION_CODE": 0.3},
        "deadline_routines": 25,
    },
    "trait_rising": {
        "title": "Capitaliser sur le trait {nom}",
        "type": "exploration",
        "priority": "low",
        "criteria": {"metric": "trait_value:{nom}", "operator": ">=", "target": 70},
        "routine_affinities": {"COUNCIL_DEBATE": 1.0, "EXPANSION_CODE": 0.5},
        "deadline_routines": 30,
    },
    "high_success_rate": {
        "title": "Tenter une evolution ambitieuse",
        "type": "evolution",
        "priority": "medium",
        "criteria": {"metric": "evolution_deployed", "operator": ">=", "target": 1},
        "routine_affinities": {"EXPANSION_CODE": 1.5, "GRIMOIRE_INVOKE": 0.5},
        "deadline_routines": 40,
    },
}


class ObjectivesEngine:
    """Singleton — moteur d'objectifs autonomes pour Prométhée."""

    _instance: Optional["ObjectivesEngine"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._objectives: List[Dict[str, Any]] = []
        self._user_topics: List[Dict[str, Any]] = []
        self._subscribed = False
        self._seed_index: int = 0
        self._session_counters: Dict[str, int] = {}
        self._last_counter_reset_day: str = date.today().isoformat()
        self._last_report_day: Optional[str] = None
        self._load()

    # --- Init & Reset ---

    def init(self):
        """Souscrit aux événements bus."""
        if self._subscribed:
            return
        self._subscribed = True
        bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
        logger.info("OBJECTIFS: Moteur d'objectifs actif.")

    def reset(self):
        self._objectives = []
        self._user_topics = []
        self._subscribed = False
        self._initialized = False
        self._seed_index = 0
        self._session_counters = {}
        self._last_counter_reset_day = date.today().isoformat()
        self._last_report_day = None

    @classmethod
    def reset_singleton(cls):
        if cls._instance is not None:
            cls._instance.reset()
            cls._instance = None

    # --- Handler bus ---

    async def _on_routine_complete(self, event: dict):
        """Appelé après chaque routine autonome."""
        # Reset quotidien des compteurs de session
        today = date.today().isoformat()
        if today != self._last_counter_reset_day:
            self._session_counters = {}
            self._last_counter_reset_day = today
            logger.info("OBJECTIFS: Reset quotidien des compteurs de session.")

        # Incrémenter les compteurs de session
        intent = event.get("intent", "")
        status = event.get("status", "")
        if intent and status == "success":
            if intent == "EXPANSION_CODE":
                self._session_counters["evolution_deployed"] = self._session_counters.get("evolution_deployed", 0) + 1
            elif intent == "SECURITY_AUDIT":
                self._session_counters["security_audits"] = self._session_counters.get("security_audits", 0) + 1
            elif intent == "VEILLE_SILENCIEUSE":
                self._session_counters["memories_added"] = self._session_counters.get("memories_added", 0) + 1
            elif intent == "MEMORY_CLEANUP":
                self._session_counters["cleanup_done"] = self._session_counters.get("cleanup_done", 0) + 1

        # Incrémenter routines_since_creation pour tous les objectifs actifs
        for obj in self._objectives:
            if obj["status"] == "active":
                obj["routines_since_creation"] += 1

        # Évaluer la progression
        self.evaluate_progress()

        # Expirer les objectifs dépassés
        self.expire_objectives()

        # Auto-générer toutes les 5 routines
        total_routines = sum(o["routines_since_creation"] for o in self._objectives if o["status"] == "active")
        # Utiliser le total_routines du système pour décider
        try:
            from core.autonomy_engine import autonomy
            sys_total = autonomy.total_routines_executed
        except Exception:
            sys_total = total_routines
        if sys_total > 0 and sys_total % 5 == 0:
            try:
                from core.self_awareness import awareness
                patterns = awareness.detect_patterns()
                if patterns:
                    self.auto_generate_from_patterns(patterns)
            except Exception as e:
                logger.warning(f"OBJECTIFS: Auto-génération échouée: {e}")

        self._save()

    # --- Création ---

    def create_objective(self, title: str, obj_type: str, priority: str,
                         source: str, criteria: dict, routine_affinities: dict,
                         deadline_routines: int = 30) -> Optional[Dict[str, Any]]:
        """Crée un objectif. Retourne None si max atteint."""
        active = [o for o in self._objectives if o["status"] == "active"]
        if len(active) >= MAX_ACTIVE_OBJECTIVES:
            logger.warning(f"OBJECTIFS: Max atteint ({MAX_ACTIVE_OBJECTIVES}), création refusée: {title}")
            return None

        obj_id = f"obj_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self._objectives):03d}"
        obj = {
            "id": obj_id,
            "title": title,
            "type": obj_type,
            "priority": priority,
            "source": source,
            "created_at": datetime.now().isoformat(),
            "deadline_routines": deadline_routines,
            "criteria": {**criteria, "current": None},
            "routine_affinities": dict(routine_affinities),
            "progress": 0.0,
            "status": "active",
            "routines_since_creation": 0,
            "history": [{"timestamp": datetime.now().isoformat(), "event": "created", "detail": f"Source: {source}"}],
        }

        self._objectives.append(obj)
        self._save()
        logger.info(f"OBJECTIFS: Créé [{obj_id}] '{title}' (type={obj_type}, priorité={priority})")

        # Publier l'événement (fire-and-forget)
        self._fire_event("OBJECTIVE_CREATED", {"id": obj_id, "title": title, "type": obj_type})

        return obj

    # --- Évaluation ---

    def evaluate_progress(self):
        """Met à jour current/progress pour tous les objectifs actifs."""
        snapshot = None
        try:
            from core.self_awareness import awareness
            snapshot = awareness.get_latest_snapshot()
        except Exception:
            pass

        for obj in self._objectives:
            if obj["status"] != "active":
                continue
            criteria = obj["criteria"]
            metric = criteria["metric"]
            operator = criteria["operator"]
            target = criteria["target"]

            current = self._read_metric(metric, snapshot)
            if current is None:
                continue

            criteria["current"] = current
            obj["progress"] = self._calc_progress(current, target, operator)

            # Auto-complétion si progress >= 1.0
            if obj["progress"] >= 1.0:
                self.complete_objective(obj["id"], "Critère atteint automatiquement")

    def _read_metric(self, metric: str, snapshot: Optional[dict]) -> Any:
        """Lit une métrique depuis le snapshot SelfAwareness."""
        if snapshot is None:
            return None

        perf = snapshot.get("performance", {})
        health = snapshot.get("health", {})
        traits_avg = snapshot.get("traits", {}).get("average", {})

        if metric == "success_rate":
            return perf.get("success_rate")
        elif metric == "error_streak":
            return perf.get("error_streak")
        elif metric == "consensus_rate":
            return perf.get("council_consensus_rate")
        elif metric == "cloud_budget_ratio":
            used = perf.get("cloud_budget_used", 0)
            max_budget = perf.get("cloud_budget_max", 100)
            if max_budget > 0:
                return round(used / max_budget, 2)
            return 0.0
        elif metric == "health_verdict":
            return health.get("verdict")
        elif metric.startswith("trait_value:"):
            trait_name = metric.split(":", 1)[1]
            return traits_avg.get(trait_name)
        elif metric == "total_routines":
            try:
                from core.autonomy_engine import autonomy
                return autonomy.total_routines_executed
            except Exception:
                return 0
        elif metric in ("evolution_deployed", "security_audits", "memories_added", "cleanup_done"):
            return self._session_counters.get(metric, 0)

        return None

    @staticmethod
    def _calc_progress(current, target, operator: str) -> float:
        """Calcule la progression (0-1) selon l'opérateur."""
        if operator == "==":
            return 1.0 if current == target else 0.0
        elif operator == ">=":
            if target == 0:
                return 1.0
            progress = current / target
            return max(0.0, min(1.0, progress))
        elif operator == "<=":
            if current == 0:
                return 1.0
            if target == 0:
                return 0.0
            progress = target / current
            return max(0.0, min(1.0, progress))
        return 0.0

    # --- Complétion ---

    def complete_objective(self, obj_id: str, reason: str = ""):
        """Marque un objectif comme complété et applique les rewards PSYCHE."""
        obj = self._find_objective(obj_id)
        if not obj or obj["status"] != "active":
            return

        obj["status"] = "completed"
        obj["progress"] = 1.0
        obj["history"].append({
            "timestamp": datetime.now().isoformat(),
            "event": "completed",
            "detail": reason,
        })

        # Rewards PSYCHE
        rewards = COMPLETION_REWARDS.get(obj["type"], {})
        if rewards:
            try:
                from core.psyche import psyche
                psyche.apply_deltas("_system", rewards)
                logger.info(f"OBJECTIFS: Rewards PSYCHE appliqués pour [{obj_id}]: {rewards}")
            except Exception as e:
                logger.warning(f"OBJECTIFS: Rewards PSYCHE échoués: {e}")

        self._save()
        logger.info(f"OBJECTIFS: Complété [{obj_id}] '{obj['title']}' — {reason}")
        self._fire_event("OBJECTIVE_COMPLETED", {"id": obj_id, "title": obj["title"], "reason": reason})

    # --- Expiration ---

    def expire_objectives(self):
        """Expire les objectifs dont routines_since_creation >= deadline."""
        for obj in self._objectives:
            if obj["status"] != "active":
                continue
            if obj["routines_since_creation"] >= obj["deadline_routines"]:
                obj["status"] = "failed"
                obj["history"].append({
                    "timestamp": datetime.now().isoformat(),
                    "event": "expired",
                    "detail": f"Deadline atteinte ({obj['deadline_routines']} routines)",
                })
                logger.info(f"OBJECTIFS: Expiré [{obj['id']}] '{obj['title']}'")
                self._fire_event("OBJECTIVE_FAILED", {"id": obj["id"], "title": obj["title"], "reason": "expired"})

        self._save()

    # --- Seed quotidien ---

    def seed_daily_objectives(self):
        """Crée des objectifs aspirationnels si < 3 actifs. Rotation parmi les templates."""
        active = [o for o in self._objectives if o["status"] == "active"]
        if len(active) >= 3:
            return

        # Métriques déjà couvertes par des objectifs actifs
        active_metrics = {o["criteria"]["metric"] for o in active}

        created = 0
        max_to_create = 2
        templates_count = len(DAILY_OBJECTIVE_TEMPLATES)

        for _ in range(templates_count):
            if created >= max_to_create:
                break
            template = DAILY_OBJECTIVE_TEMPLATES[self._seed_index % templates_count]
            self._seed_index += 1

            metric = template["criteria"]["metric"]
            if metric in active_metrics:
                continue

            obj = self.create_objective(
                title=template["title"],
                obj_type=template["type"],
                priority=template["priority"],
                source="daily_seed",
                criteria=dict(template["criteria"]),
                routine_affinities=dict(template["routine_affinities"]),
                deadline_routines=template["deadline_routines"],
            )
            if obj:
                active_metrics.add(metric)
                created += 1

        if created > 0:
            self._save()
            logger.info(f"OBJECTIFS: Seed quotidien — {created} objectif(s) aspirationnel(s) créé(s).")

    # --- Bilan quotidien ---

    def generate_daily_report(self) -> str:
        """Génère un bilan des objectifs : actifs, complétés, expirés."""
        active = [o for o in self._objectives if o["status"] == "active"]
        completed = [o for o in self._objectives if o["status"] == "completed"]
        failed = [o for o in self._objectives if o["status"] == "failed"]

        lines = [f"## Bilan Objectifs — {datetime.now().strftime('%Y-%m-%d %H:%M')}"]

        if active:
            lines.append(f"\n**Objectifs actifs ({len(active)})** :")
            for o in active:
                remaining = max(0, o["deadline_routines"] - o["routines_since_creation"])
                lines.append(f"- {o['title']} — {o['progress']:.0%} (reste {remaining} routines)")
        else:
            lines.append("\n**Aucun objectif actif.**")

        if completed:
            lines.append(f"\n**Complétés ({len(completed)})** :")
            for o in completed[-5:]:
                lines.append(f"- ✅ {o['title']}")

        if failed:
            lines.append(f"\n**Expirés ({len(failed)})** :")
            for o in failed[-5:]:
                lines.append(f"- ❌ {o['title']}")

        report = "\n".join(lines)

        # Écrire dans le journal stratégique
        try:
            from core.strategic_journal import journal as strat_journal
            strat_journal.append_objectives_report(report)
        except Exception as e:
            logger.warning(f"OBJECTIFS: Écriture journal échouée: {e}")

        self._last_report_day = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"OBJECTIFS: Bilan quotidien généré ({len(active)} actifs, {len(completed)} complétés, {len(failed)} expirés).")
        return report

    # --- Bonus scoring ---

    def get_routine_bonus(self, intent: str) -> float:
        """Somme des bonus de tous les objectifs actifs pour un intent donné."""
        bonus = 0.0
        for obj in self._objectives:
            if obj["status"] != "active":
                continue
            affinity = obj["routine_affinities"].get(intent, 0.0)
            weight = PRIORITY_WEIGHTS.get(obj["priority"], 0.4)
            bonus += affinity * weight
        return bonus

    # --- Auto-génération depuis patterns ---

    def auto_generate_from_patterns(self, patterns: List[Dict[str, Any]]):
        """Crée des objectifs correctifs depuis les patterns SelfAwareness."""
        # Métriques déjà couvertes par des objectifs actifs
        active_metrics = set()
        for obj in self._objectives:
            if obj["status"] == "active":
                active_metrics.add(obj["criteria"]["metric"])

        for pattern in patterns:
            p_type = pattern.get("type", "")
            template = PATTERN_OBJECTIVE_MAP.get(p_type)
            if not template:
                continue

            # Gérer les cas spéciaux dynamiques (trait_falling, trait_rising)
            if p_type in ("trait_falling", "trait_rising"):
                trait_name = self._extract_trait_name(pattern.get("message", ""))
                if not trait_name:
                    continue
                metric = f"trait_value:{trait_name}"
                if metric in active_metrics:
                    continue
                title = template["title"].replace("{nom}", trait_name)
                criteria = {**template["criteria"], "metric": metric}
            else:
                metric = template["criteria"]["metric"]
                if metric in active_metrics:
                    continue
                title = template["title"]
                criteria = dict(template["criteria"])

            self.create_objective(
                title=title,
                obj_type=template["type"],
                priority=template["priority"],
                source="pattern",
                criteria=criteria,
                routine_affinities=dict(template["routine_affinities"]),
                deadline_routines=template["deadline_routines"],
            )

    @staticmethod
    def _extract_trait_name(message: str) -> Optional[str]:
        """Extrait le nom du trait depuis le message du pattern (ex: 'Trait curiosite en baisse')."""
        # Format attendu : "Trait '{nom}' en baisse constante (...)"
        if "'" in message:
            parts = message.split("'")
            if len(parts) >= 2:
                return parts[1]
        return None

    # --- Accesseurs ---

    def get_active_objectives(self) -> List[Dict[str, Any]]:
        return [o for o in self._objectives if o["status"] == "active"]

    def get_all_objectives(self) -> List[Dict[str, Any]]:
        return list(self._objectives)

    def _find_objective(self, obj_id: str) -> Optional[Dict[str, Any]]:
        for obj in self._objectives:
            if obj["id"] == obj_id:
                return obj
        return None

    # --- Persistance ---

    def _load(self):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._objectives = data.get("objectives", [])
            self._user_topics = data.get("user_topics", [])
            self._session_counters = data.get("session_counters", {})
            self._seed_index = data.get("seed_index", 0)
            self._last_counter_reset_day = data.get("last_counter_reset_day", date.today().isoformat())
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        data = {
            "version": "1.0",
            "objectives": self._objectives,
            "user_topics": self._user_topics,
            "session_counters": self._session_counters,
            "seed_index": self._seed_index,
            "last_counter_reset_day": self._last_counter_reset_day,
        }
        tmp_path = STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, STATE_FILE)

    # --- User Research Topics ---

    def add_user_topic(self, subject: str) -> Optional[Dict[str, Any]]:
        """Ajoute un sujet de recherche utilisateur. Crée l'objectif + goal préfrontal liés."""
        active_topics = [t for t in self._user_topics if t["status"] == "active"]
        if len(active_topics) >= MAX_USER_TOPICS:
            logger.warning(f"OBJECTIFS: Max topics utilisateur atteint ({MAX_USER_TOPICS})")
            return None

        topic_id = f"topic_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        topic = {
            "id": topic_id,
            "subject": subject,
            "created_at": datetime.now().isoformat(),
            "status": "active",
            "objective_id": None,
            "goal_id": None,
            "research_count": 0,
        }

        # Créer l'objectif lié (haute priorité, affinités recherche)
        obj = self.create_objective(
            title=f"Recherche: {subject[:80]}",
            obj_type="exploration",
            priority="high",
            source="user",
            criteria={"metric": "total_routines", "operator": ">=", "target": 10},
            routine_affinities={
                "VEILLE_SILENCIEUSE": 2.0,
                "EXPANSION_CODE": 1.0,
                "COUNCIL_DEBATE": 0.5,
            },
            deadline_routines=100,
        )
        if obj:
            topic["objective_id"] = obj["id"]

        # Créer le goal préfrontal (focus scoring)
        try:
            from core.prefrontal import prefrontal
            goal = prefrontal.create_goal(
                title=f"Recherche: {subject[:80]}",
                horizon="medium",
                priority=8.0,
                steps=[
                    {"intent": "VEILLE_SILENCIEUSE", "description": f"Rechercher: {subject[:100]}"},
                    {"intent": "EXPANSION_CODE", "description": f"Intégrer: {subject[:80]}"},
                ],
                source="user",
                cost_estimated=6,
                drive_alignment={"CURIOSITE": 1.0, "COMPREHENSION": 0.8},
            )
            if goal:
                topic["goal_id"] = getattr(goal, "id", None)
        except Exception as e:
            logger.warning(f"OBJECTIFS: Goal préfrontal non créé: {e}")

        self._user_topics.append(topic)
        self._save()
        logger.info(f"OBJECTIFS: Topic recherche utilisateur créé [{topic_id}]: {subject}")
        self._fire_event("USER_TOPIC_CREATED", {"id": topic_id, "subject": subject})
        return topic

    def get_active_user_topics(self) -> List[Dict[str, Any]]:
        """Retourne les topics utilisateur actifs."""
        return [t for t in self._user_topics if t["status"] == "active"]

    def get_user_topic_for_veille(self) -> Optional[str]:
        """Retourne le sujet du prochain topic actif (rotation). Incrémente research_count."""
        active = self.get_active_user_topics()
        if not active:
            return None
        # Rotation basée sur le nombre total de recherches
        total_research = sum(t["research_count"] for t in active)
        topic = active[total_research % len(active)]
        topic["research_count"] += 1
        self._save()
        return topic["subject"]

    def cancel_user_topic(self, topic_id: str) -> bool:
        """Annule un topic utilisateur et son objectif lié."""
        for topic in self._user_topics:
            if topic["id"] == topic_id and topic["status"] == "active":
                topic["status"] = "cancelled"
                # Annuler l'objectif lié
                if topic.get("objective_id"):
                    for obj in self._objectives:
                        if obj["id"] == topic["objective_id"] and obj["status"] == "active":
                            obj["status"] = "failed"
                            obj["history"].append({
                                "timestamp": datetime.now().isoformat(),
                                "event": "cancelled",
                                "detail": "Topic utilisateur annulé",
                            })
                self._save()
                logger.info(f"OBJECTIFS: Topic utilisateur annulé [{topic_id}]")
                return True
        return False

    # --- Utilitaires ---

    def _fire_event(self, event_type: str, data: dict):
        """Publie un événement sur le bus (fire-and-forget depuis contexte sync)."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(bus.publish(event_type, data))
        except RuntimeError:
            pass  # Pas de boucle asyncio (tests sync, etc.)


# Singleton global
objectives = ObjectivesEngine()
