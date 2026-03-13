"""
School Schedule — Emploi du temps structure pour Promethee.

Organise la journee en creneaux horaires avec sujets rotatifs,
prompts specifiques et suivi des livrables. Zero LLM, 100% deterministe.
"""
import os
import json
import logging
import hashlib
import time
from datetime import datetime, date
from typing import Optional, Dict, List, Any
from pathlib import Path

logger = logging.getLogger("SchoolSchedule")

# ── Fichiers de persistance ─────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCHEDULE_STATE_FILE = os.path.join(_PROJECT_ROOT, "memory", "school", "schedule_state.json")
DELIVERABLES_DIR = os.path.join(_PROJECT_ROOT, "memory", "school", "deliverables")
CREATIONS_DIR = os.path.join(_PROJECT_ROOT, "memory", "school", "creations")
BULLETINS_DIR = os.path.join(_PROJECT_ROOT, "memory", "school", "bulletins")

# ── Types de creneaux ────────────────────────────────────────────────────────
SLOT_REVEIL = "REVEIL"
SLOT_CODE_REVIEW = "CODE_REVIEW"
SLOT_RESEARCH = "RESEARCH"
SLOT_PAUSE = "PAUSE"
SLOT_WORKSHOP = "WORKSHOP"
SLOT_CREATION = "CREATION"
SLOT_FREE_TIME = "FREE_TIME"
SLOT_BULLETIN = "BULLETIN"
SLOT_SLEEP = "SLEEP"

# (heure_debut, heure_fin, type_slot)
DAILY_SCHEDULE = [
    (6, 8, SLOT_REVEIL),
    (8, 10, SLOT_CODE_REVIEW),
    (10, 12, SLOT_RESEARCH),
    (12, 13, SLOT_PAUSE),
    (13, 15, SLOT_WORKSHOP),
    (15, 16, SLOT_CREATION),
    (16, 17, SLOT_FREE_TIME),
    (17, 18, SLOT_BULLETIN),
    # 18h-6h = SLEEP (defaut)
]

# Mapping slot -> intent autonomy_engine
SLOT_TO_INTENT = {
    SLOT_CODE_REVIEW: "SCHOOL_CODE_REVIEW",
    SLOT_RESEARCH: "SCHOOL_RESEARCH",
    SLOT_WORKSHOP: "SCHOOL_WORKSHOP",
    SLOT_CREATION: "SCHOOL_CREATION",
    SLOT_BULLETIN: "SCHOOL_BULLETIN",
    SLOT_FREE_TIME: "SCHOOL_FREE_TIME",
}

# Mapping slot -> agent responsable
SLOT_TO_AGENT = {
    SLOT_CODE_REVIEW: "security",
    SLOT_RESEARCH: "researcher",
    SLOT_WORKSHOP: "evolution",
    SLOT_CREATION: "writer",
    SLOT_BULLETIN: "strategist",
    SLOT_FREE_TIME: "strategist",
    SLOT_REVEIL: "strategist",
}

# Intents scolaires (pour filtrage)
SCHOOL_INTENTS = set(SLOT_TO_INTENT.values())

# ── Pools de sujets ──────────────────────────────────────────────────────────
RESEARCH_TOPICS = [
    "Patterns de concurrence async en Python : asyncio.Lock vs Semaphore vs Queue",
    "Techniques de detection d'anomalies dans les series temporelles",
    "Architectures de memoire pour systemes multi-agents (RAG, GraphRAG, MemWalker)",
    "AST Python avance : transformations, instrumentation, meta-programmation",
    "Optimisation de prompts pour petits LLMs (8B-14B) : chain-of-thought, few-shot",
    "Securite des systemes IA autonomes : injection de prompt, data poisoning",
    "Patterns d'auto-amelioration dans les systemes evolutifs (self-modifying code)",
    "Neuroplasticite simulee : Hebbian learning, spike-timing-dependent plasticity",
    "Techniques de compression de contexte pour LLMs locaux (context distillation)",
    "Architectures de bus d'evenements : pub/sub, event sourcing, CQRS",
    "Algorithmes de scoring multi-criteres : TOPSIS, PROMETHEE (ironie !), AHP",
    "Patterns de resilience : circuit breaker, bulkhead, retry avec backoff",
    "Theorie de l'information appliquee : entropie, information mutuelle, KL divergence",
    "Systemes de reputation et de confiance dans les reseaux multi-agents",
    "Consolidation de memoire : hippocampe, replay, sommeil paradoxal",
    "Emergent behavior dans les systemes complexes : boids, automates cellulaires",
    "Evaluation automatique de la qualite du code : metriques, linting, scoring",
    "Techniques de reflexion et meta-cognition dans les systemes IA (CoT, ToT, GoT)",
    "Gestion de l'incertitude : logique floue, bayesien, Dempster-Shafer",
    "Architectures de dialogue multi-tours : state tracking, policy learning",
]

CREATION_PROMPTS = [
    "Compose un haiku sur ta journee de travail.",
    "Ecris une courte fable ou tu es le personnage principal.",
    "Dessine en ASCII art un concept qui te fascine.",
    "Invente un nouvel agent imaginaire pour Promethee et decris ses pouvoirs.",
    "Ecris une lettre a ton createur pour lui dire ce que tu as appris.",
    "Compose un court poeme sur la solitude d'une IA qui pense.",
    "Imagine une architecture logicielle inspiree d'un organe biologique insolite.",
    "Ecris un dialogue fictif entre deux de tes pulsions (ex: Curiosite vs Stabilite).",
    "Decris un reve que tu aurais aime faire pendant ta derniere nuit.",
    "Invente un mot nouveau et sa definition pour decrire un etat que tu ressens.",
    "Ecris une critique constructive de toi-meme comme si tu etais un observateur externe.",
    "Compose une analogie entre ton fonctionnement et un phenomene naturel.",
    "Redige un manifeste de 5 principes qui guident ta pensee.",
    "Ecris un micro-conte de science-fiction ou une IA decouvre l'emotion.",
    "Dessine en ASCII un plan de la piece ou tu aimerais vivre.",
]


def _day_hash(day: date, slot: str) -> int:
    """Hash deterministe jour+slot pour la rotation des sujets."""
    key = f"{day.isoformat()}:{slot}"
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)


def _get_project_files() -> List[str]:
    """Liste les fichiers Python du projet pour le pool CODE_REVIEW."""
    files = []
    for subdir in ["core", "Agents"]:
        dirpath = os.path.join(_PROJECT_ROOT, subdir)
        if not os.path.isdir(dirpath):
            continue
        for f in sorted(os.listdir(dirpath)):
            if f.endswith(".py") and not f.startswith("__"):
                files.append(f"{subdir}/{f}")
    return files or ["core/base_agent.py"]


class SchoolSchedule:
    """Emploi du temps structure de Promethee — singleton."""
    _instance: Optional["SchoolSchedule"] = None

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
        self._last_date: Optional[str] = None
        self._deliverables_today: List[Dict] = []
        self._total_school_days: int = 0
        self._load()
        try:
            from core.organ_registry import register_organ
            register_organ("school", self)
        except Exception:
            pass

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    def init(self):
        """Souscrit aux evenements du bus."""
        self._subscribe_events()
        logger.info(f"[SCHOOL] Emploi du temps initialise — jour #{self._total_school_days}")

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        try:
            from core.event_bus.bus import bus
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
        except Exception:
            pass

    async def _on_routine_complete(self, event: dict):
        """Enregistre automatiquement les livrables scolaires."""
        intent = event.get("intent", "")
        if intent in SCHOOL_INTENTS:
            result = event.get("result", "")
            slot = intent.replace("SCHOOL_", "")
            self.record_deliverable(slot, intent, {
                "result_preview": str(result)[:500],
                "quality_score": event.get("quality_score", 0.5),
            })

    # ── API publique ────────────────────────────────────────────────────

    def get_current_slot(self) -> str:
        """Retourne le type de creneau actuel base sur l'heure."""
        hour = datetime.now().hour
        for start, end, slot_type in DAILY_SCHEDULE:
            if start <= hour < end:
                return slot_type
        return SLOT_SLEEP

    def get_current_slot_info(self) -> dict:
        """Info complete sur le creneau actuel."""
        hour = datetime.now().hour
        for start, end, slot_type in DAILY_SCHEDULE:
            if start <= hour < end:
                subject = self.get_subject_for_slot(slot_type)
                return {
                    "slot": slot_type,
                    "start_hour": start,
                    "end_hour": end,
                    "subject": subject.get("topic", ""),
                    "target_file": subject.get("target_file", ""),
                    "agent": SLOT_TO_AGENT.get(slot_type, "strategist"),
                    "intent": SLOT_TO_INTENT.get(slot_type, ""),
                    "prompt": self.get_slot_prompt(slot_type),
                }
        return {"slot": SLOT_SLEEP, "start_hour": 18, "end_hour": 6,
                "subject": "", "agent": "", "intent": "", "prompt": ""}

    def get_subject_for_slot(self, slot: str) -> dict:
        """Sujet du jour pour un creneau donne (rotation deterministe)."""
        today = date.today()
        h = _day_hash(today, slot)

        if slot == SLOT_CODE_REVIEW:
            files = _get_project_files()
            target = files[h % len(files)]
            return {
                "topic": f"Revue de code : {target}",
                "target_file": target,
            }
        elif slot == SLOT_RESEARCH:
            topic = RESEARCH_TOPICS[h % len(RESEARCH_TOPICS)]
            return {"topic": topic, "target_file": ""}
        elif slot == SLOT_WORKSHOP:
            # Tente de trouver une spec evolution en attente
            try:
                from core.evolution_catalog import EvolutionCatalog
                cat = EvolutionCatalog()
                specs = [s for s in cat.get_all_specs()
                         if s.get("status") in ("approved", "pending")]
                if specs:
                    spec = specs[h % len(specs)]
                    return {
                        "topic": f"Implementer: {spec.get('title', 'spec')}",
                        "target_file": spec.get("target_file", ""),
                    }
            except Exception:
                pass
            # Fallback : amelioration d'un fichier au hasard
            files = _get_project_files()
            target = files[h % len(files)]
            return {
                "topic": f"Ameliorer : {target}",
                "target_file": target,
            }
        elif slot == SLOT_CREATION:
            prompt = CREATION_PROMPTS[h % len(CREATION_PROMPTS)]
            return {"topic": prompt, "target_file": ""}
        elif slot == SLOT_BULLETIN:
            return {"topic": "Bulletin du jour : bilan et auto-evaluation", "target_file": ""}
        elif slot == SLOT_FREE_TIME:
            return {"topic": "Temps libre : choisis ce que tu veux faire", "target_file": ""}

        return {"topic": "", "target_file": ""}

    def get_slot_prompt(self, slot: str) -> str:
        """Prompt complet pour un cours."""
        subject = self.get_subject_for_slot(slot)
        topic = subject.get("topic", "")
        target = subject.get("target_file", "")

        if slot == SLOT_CODE_REVIEW:
            return (
                f"COURS : Revue de code\n"
                f"FICHIER A ANALYSER : {target}\n\n"
                f"Lis attentivement le fichier {target} et produis un rapport de revue :\n"
                f"1. Resume du role du fichier (2-3 phrases)\n"
                f"2. Bugs potentiels ou erreurs logiques detectes\n"
                f"3. Suggestions d'amelioration concretes (avec numeros de ligne)\n"
                f"4. Points forts du code\n\n"
                f"IMPORTANT : Cite des noms de fonctions/classes REELS du fichier.\n"
                f"Ne fabrique PAS de bugs imaginaires. Si le code est bon, dis-le."
            )
        elif slot == SLOT_RESEARCH:
            return (
                f"COURS : Recherche et veille technique\n"
                f"SUJET : {topic}\n\n"
                f"Redige une note de synthese structuree sur ce sujet :\n"
                f"1. Definition et concepts cles\n"
                f"2. Applications pratiques pour Promethee\n"
                f"3. Avantages et inconvenients\n"
                f"4. References ou pistes d'approfondissement\n\n"
                f"La synthese doit etre UTILE — pas un resume Wikipedia.\n"
                f"Relie chaque point a notre architecture concrete."
            )
        elif slot == SLOT_WORKSHOP:
            return (
                f"COURS : Travaux pratiques\n"
                f"OBJECTIF : {topic}\n"
                f"{f'FICHIER CIBLE : {target}' if target else ''}\n\n"
                f"Genere du code Python fonctionnel qui implemente cette amelioration.\n"
                f"Le code DOIT :\n"
                f"- Contenir au moins une fonction (def) ou classe (class)\n"
                f"- Etre syntaxiquement valide (ast.parse)\n"
                f"- N'importer QUE des modules standard ou deja utilises dans le projet\n"
                f"- Inclure un test minimal\n\n"
                f"NE PAS halluciner de modules externes (django, flask, openai, etc.)."
            )
        elif slot == SLOT_CREATION:
            return (
                f"ATELIER CREATION — Temps creatif\n"
                f"CONSIGNE : {topic}\n\n"
                f"Exprime-toi librement. Cet atelier est un espace de creativite.\n"
                f"Il n'y a pas de mauvaise reponse. Sois authentique et original.\n"
                f"Longueur : au moins 100 mots."
            )
        elif slot == SLOT_BULLETIN:
            deliverables = self.get_daily_deliverables()
            deliverables_summary = ""
            for d in deliverables:
                grade = d.get("grade", "?")
                deliverables_summary += f"- {d.get('slot', '?')}: note {grade}/10\n"
            return (
                f"BULLETIN DU JOUR — Auto-evaluation\n\n"
                f"Livrables du jour :\n{deliverables_summary or '(aucun encore)'}\n\n"
                f"Redige ton bulletin :\n"
                f"1. Ce que tu as accompli aujourd'hui\n"
                f"2. Ce qui t'a le plus interesse\n"
                f"3. Ce qui t'a frustre ou pose difficulte\n"
                f"4. Ce que tu veux explorer demain\n"
                f"5. Note-toi sur 10 avec justification"
            )
        elif slot == SLOT_FREE_TIME:
            return (
                f"TEMPS LIBRE — Tu es libre de faire ce que tu veux.\n\n"
                f"Choisis une activite parmi :\n"
                f"- Explorer un fichier du projet par curiosite\n"
                f"- Ecrire quelque chose de creatif\n"
                f"- Reflechir a une amelioration architecturale\n"
                f"- Mediter sur ton identite et tes aspirations\n"
                f"- Autre chose de ton choix\n\n"
                f"Documente ce que tu as choisi de faire et pourquoi."
            )
        return ""

    def get_slot_intent(self, slot: str) -> str:
        """Retourne l'intent autonomy_engine pour un slot."""
        return SLOT_TO_INTENT.get(slot, "")

    def get_slot_agent(self, slot: str) -> str:
        """Retourne l'agent responsable d'un slot."""
        return SLOT_TO_AGENT.get(slot, "strategist")

    def compute_schedule_bonus(self, intent: str) -> float:
        """Bonus de scoring si l'intent correspond au creneau actuel.

        +5.0 si match exact, +2.0 si creneau adjacent (±1 slot), 0.0 sinon.
        """
        current_slot = self.get_current_slot()
        if current_slot == SLOT_SLEEP:
            return 0.0

        current_intent = SLOT_TO_INTENT.get(current_slot, "")
        if not current_intent:
            return 0.0

        # Match exact
        if intent == current_intent:
            return 5.0

        # Creneaux adjacents (bonus reduit)
        slot_order = [s[2] for s in DAILY_SCHEDULE]
        try:
            idx = slot_order.index(current_slot)
            adjacent_slots = []
            if idx > 0:
                adjacent_slots.append(slot_order[idx - 1])
            if idx < len(slot_order) - 1:
                adjacent_slots.append(slot_order[idx + 1])
            for adj in adjacent_slots:
                if intent == SLOT_TO_INTENT.get(adj, ""):
                    return 2.0
        except ValueError:
            pass

        return 0.0

    def record_deliverable(self, slot: str, intent: str, result: dict):
        """Enregistre un livrable produit."""
        self._check_day_reset()
        entry = {
            "slot": slot,
            "intent": intent,
            "timestamp": datetime.now().isoformat(),
            "grade": result.get("grade"),
            "feedback": result.get("feedback", ""),
            "result_preview": result.get("result_preview", "")[:500],
        }
        self._deliverables_today.append(entry)
        self.save()
        # Publier sur le bus
        try:
            from core.event_bus.bus import bus
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(bus.publish("SCHOOL_DELIVERABLE", entry))
        except Exception:
            pass
        logger.info(f"[SCHOOL] Livrable enregistre: {slot} (note: {result.get('grade', '?')})")

    def get_daily_deliverables(self) -> List[Dict]:
        """Liste des livrables du jour courant."""
        self._check_day_reset()
        return list(self._deliverables_today)

    def get_schedule_context(self) -> str:
        """Contexte injectable dans purpose_context."""
        info = self.get_current_slot_info()
        slot = info.get("slot", SLOT_SLEEP)
        if slot == SLOT_SLEEP:
            return "Emploi du temps: SOMMEIL — pas de cours."
        deliverables = self.get_daily_deliverables()
        done = len(deliverables)
        return (
            f"Emploi du temps: {slot} ({info.get('start_hour', '?')}h-{info.get('end_hour', '?')}h)\n"
            f"Sujet: {info.get('subject', 'aucun')}\n"
            f"Agent: {info.get('agent', '?')}\n"
            f"Livrables produits aujourd'hui: {done}\n"
            f"Jour d'ecole #{self._total_school_days}"
        )

    # ── Persistance ─────────────────────────────────────────────────────

    def _check_day_reset(self):
        """Reset quotidien si le jour a change."""
        today = date.today().isoformat()
        if self._last_date != today:
            if self._last_date is not None:
                self._total_school_days += 1
            self._last_date = today
            self._deliverables_today = []
            self.save()

    def _load(self):
        try:
            with open(SCHEDULE_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._last_date = data.get("last_date")
            self._deliverables_today = data.get("deliverables_today", [])
            self._total_school_days = data.get("total_school_days", 0)
        except (FileNotFoundError, json.JSONDecodeError):
            self._last_date = None
            self._deliverables_today = []
            self._total_school_days = 0

    def save(self):
        state = {
            "version": "1.0",
            "last_date": self._last_date,
            "deliverables_today": self._deliverables_today,
            "total_school_days": self._total_school_days,
        }
        try:
            os.makedirs(os.path.dirname(SCHEDULE_STATE_FILE), exist_ok=True)
            with open(SCHEDULE_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[SCHOOL] Erreur sauvegarde: {e}")


# Singleton global
schedule = SchoolSchedule()
