# core/neural_compiler.py — NeuralCompiler : Distillation des appels LLM en règles déterministes
# Knowledge distillation / imitation learning : le LLM est le "professeur",
# le Neural Compiler est l'"élève" qui apprend par observation.
# 0 LLM, pur déterministe.

import json
import os
import re
import time
import math
import random
import hashlib
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List, Tuple
from collections import Counter

from core.event_bus.bus import bus

logger = logging.getLogger("NeuralCompiler")

# --- Fichier de persistance ---

COMPILER_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "neural_compiler_state.json"
)

# --- Constantes ---

MAX_OBSERVATIONS = 500           # FIFO
MAX_RULES = 200
OBSERVATION_TTL_DAYS = 7
INTERCEPT_CONFIDENCE_THRESHOLD = 0.85  # Conservateur
MIN_OBSERVATIONS_FOR_RULE = 10
MIN_SUCCESS_RATE = 0.7
RULE_DISABLE_THRESHOLD = 0.5
AB_TEST_PROBABILITY = 0.10       # 10% vérification parallèle LLM
AUTOSAVE_INTERVAL = 10
COMPILE_COOLDOWN = 300           # 5 min entre compilations

# --- Stopwords FR/EN (~60 mots) ---

_STOPWORDS = frozenset({
    # Français
    "les", "des", "une", "pour", "dans", "avec", "sur", "par", "est", "que",
    "qui", "sont", "pas", "mais", "plus", "aussi", "cette", "ces", "tout",
    "comme", "peut", "fait", "bien", "nous", "vous", "leur", "entre", "sans",
    "autre", "elle", "elles", "avoir", "être", "faire", "dire", "tous", "très",
    # Anglais
    "the", "and", "for", "that", "this", "with", "from", "are", "was", "not",
    "but", "have", "has", "been", "will", "would", "could", "should", "they",
    "their", "there", "what", "when", "which", "your", "about", "each", "into",
})

# --- Classification task_type ---

TASK_TYPE_MARKERS = {
    "complexity_eval": ["Évalue la complexité", "évalue la complexité"],
    "evolution_select": ["CATALOGUE_EVOLUTION", "Choisis une spec"],
    "evolution_generate": ["EVOLUTION_PIPELINE"],
    "security_audit": ["vulnérabilité", "audit sécurité"],
    "code_generate": ["Génère", "génère"],
    "code_review": ["revue", "qualité code"],
    "council_debate": ["CONSEIL", "council"],
    "veille": ["MODE VEILLE", "veille"],
    "research": ["synthèse", "recherche"],
    "formatting": ["formatage", "FICHIER_CIBLE"],
}


# --- Structures de données ---

@dataclass
class PromptFingerprint:
    """Empreinte compacte d'un appel LLM (~300 octets)."""
    agent_name: str
    task_type: str
    prompt_length: int           # 0=court(<500), 1=moyen, 2=long(>2000)
    has_rag_context: bool
    has_code_block: bool
    has_json_request: bool
    dominant_keywords: List[str]  # Top 5 mots significatifs
    markers: List[str]           # _LOCAL_FORCE_MARKERS détectés (max 3)
    mood: str
    cognitive_state: str
    dopamine_bucket: int         # 0=bas(<0.3), 1=normal, 2=haut(>0.7)
    threat_bucket: int           # 0=calme(<2), 1=alerte, 2=danger(>6)
    error_streak: int            # Cap 5


@dataclass
class Observation:
    """Un appel LLM capturé avec contexte et résultat."""
    obs_id: str
    timestamp: float
    fingerprint: PromptFingerprint
    response_text: str           # Cap 2000 chars (RAM only, pas persisté)
    response_hash: str           # MD5 des 200 premiers chars normalisés
    response_structure: str      # "text" | "json" | "code" | "verdict"
    quality: float               # [0,1] ou -1.0 = pas encore évaluée
    was_cloud: bool


@dataclass
class CompiledRule:
    """Règle compilée = condition sur fingerprint -> template de réponse."""
    rule_id: str
    agent_name: str
    task_type: str
    condition_mood: Optional[str]          # None = wildcard
    condition_cognitive: Optional[str]
    condition_keywords: List[str]          # ALL doivent matcher
    response_template: str                 # Max 2000 chars
    response_structure: str
    confidence: float                      # [0, 1]
    observations_count: int
    success_rate: float
    intercept_count: int = 0
    positive_feedback: int = 0
    negative_feedback: int = 0
    created_at: float = 0.0
    last_used: float = 0.0


# --- Helpers ---

def _classify_task_type(agent_name: str, prompt: str) -> str:
    """Classification déterministe du task_type basée sur les marqueurs."""
    for task_type, markers in TASK_TYPE_MARKERS.items():
        for marker in markers:
            if marker in prompt:
                return task_type
    return f"{agent_name}_general"


def _extract_keywords(text: str, top_n: int = 5) -> List[str]:
    """Extraction déterministe des mots-clés significatifs (pas de NLTK)."""
    words = re.findall(r'[a-zA-Zéèêàùûôîïëç]+', text.lower())
    filtered = [w for w in words if len(w) >= 4 and w not in _STOPWORDS]
    counts = Counter(filtered)
    return [w for w, _ in counts.most_common(top_n)]


def _prompt_length_bucket(length: int) -> int:
    """Bucket de longueur du prompt."""
    if length < 500:
        return 0
    elif length > 2000:
        return 2
    return 1


def _dopamine_bucket(level: float) -> int:
    """Bucket du niveau de dopamine."""
    if level < 0.3:
        return 0
    elif level > 0.7:
        return 2
    return 1


def _threat_bucket(level: float) -> int:
    """Bucket du niveau de menace."""
    if level < 2.0:
        return 0
    elif level > 6.0:
        return 2
    return 1


def _detect_response_structure(text: str) -> str:
    """Détecte la structure de la réponse."""
    stripped = text.strip()
    # JSON
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(stripped)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass
    # Code (blocs ``` ou def/class en début de ligne)
    if "```" in text or re.search(r'^(?:def |class |import |from )', text, re.MULTILINE):
        return "code"
    # Verdict (réponses courtes type OUI/NON/VALIDE/REJETÉ)
    if len(stripped) < 100 and re.match(
        r'^(?:OUI|NON|VALIDE|REJET[ÉE]|APPROUV[ÉE]|OK|SIMPLE|COMPLEXE)',
        stripped.upper()
    ):
        return "verdict"
    return "text"


def _response_hash(text: str) -> str:
    """Hash MD5 des 200 premiers chars normalisés."""
    normalized = re.sub(r'\s+', ' ', text[:200].lower().strip())
    return hashlib.md5(normalized.encode("utf-8", errors="replace")).hexdigest()


def _keyword_overlap(kw1: List[str], kw2: List[str]) -> float:
    """Pourcentage de mots-clés communs."""
    if not kw1 or not kw2:
        return 0.0
    s1 = set(kw1)
    s2 = set(kw2)
    intersection = s1 & s2
    union = s1 | s2
    if not union:
        return 0.0
    return len(intersection) / len(union)


def _get_current_mood() -> str:
    """Récupère l'humeur cardiaque courante."""
    try:
        from core.cardiac_engine import heart
        return heart.dominant_emotion
    except Exception:
        return "neutre"


def _get_cognitive_state() -> str:
    """Récupère l'état cognitif du corpus callosum."""
    try:
        from core.corpus_callosum import callosum
        return callosum.cognitive_state
    except Exception:
        return "standard"


def _get_dopamine_level() -> float:
    """Récupère le niveau de dopamine courant."""
    try:
        from core.dopamine_system import dopamine
        return dopamine.dopamine_level
    except Exception:
        return 0.5


def _get_threat_level() -> float:
    """Récupère le niveau de menace reptilien."""
    try:
        from core.reptilian_core import reptilian
        return reptilian.threat_level
    except Exception:
        return 0.0


def _get_error_streak() -> int:
    """Récupère le streak d'erreurs courant."""
    try:
        from core.dopamine_system import dopamine
        recent = list(dopamine._recent_intents)
        streak = 0
        for intent in reversed(recent):
            if isinstance(intent, dict) and intent.get("quality", 1.0) < 0.4:
                streak += 1
            else:
                break
        return min(streak, 5)
    except Exception:
        return 0


def _detect_markers(prompt: str) -> List[str]:
    """Détecte les _LOCAL_FORCE_MARKERS présents dans le prompt (max 3)."""
    from core.base_agent import BaseAgent
    found = []
    for marker in BaseAgent._LOCAL_FORCE_MARKERS:
        if marker in prompt:
            found.append(marker)
            if len(found) >= 3:
                break
    return found


# ================================================================
# SINGLETON
# ================================================================

class NeuralCompiler:
    """Singleton — Distillation des appels LLM en règles déterministes."""

    _instance: Optional["NeuralCompiler"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._observations: List[Observation] = []
        self._rules: List[CompiledRule] = []
        self._subscribed: bool = False
        self._obs_since_save: int = 0
        self._last_compile_time: float = 0.0

        # Tracking des intercepts récents : agent_name → (rule_id, timestamp)
        self._last_intercepts: Dict[str, Tuple[str, float]] = {}

        # Métriques globales
        self._total_observations: int = 0
        self._total_intercepts: int = 0
        self._total_llm_calls: int = 0
        self._total_ab_tests: int = 0
        self._ab_divergences: int = 0

        self._load()
        self._subscribe_events()

    def reset(self):
        """Reset l'état (pour tests)."""
        self._observations = []
        self._rules = []
        self._subscribed = False
        self._obs_since_save = 0
        self._last_compile_time = 0.0
        self._last_intercepts = {}
        self._total_observations = 0
        self._total_intercepts = 0
        self._total_llm_calls = 0
        self._total_ab_tests = 0
        self._ab_divergences = 0

    @classmethod
    def reset_singleton(cls):
        """Détruit le singleton (pour tests)."""
        cls._instance = None

    def init(self):
        """Initialisation explicite (appelé depuis main.py)."""
        self._subscribe_events()
        logger.info(
            f"COMPILER: Neural Compiler initialisé ({len(self._rules)} règles, "
            f"{len(self._observations)} observations)."
        )

    # ================================================================
    # PERSISTANCE
    # ================================================================

    def _load(self):
        """Charge l'état depuis le fichier JSON."""
        if not os.path.exists(COMPILER_STATE_FILE):
            return
        try:
            with open(COMPILER_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Observations (sans response_text)
            for obs_data in data.get("observations", []):
                try:
                    fp_data = obs_data.get("fingerprint", {})
                    fp = PromptFingerprint(**fp_data)
                    obs = Observation(
                        obs_id=obs_data["obs_id"],
                        timestamp=obs_data["timestamp"],
                        fingerprint=fp,
                        response_text="",  # Pas persisté
                        response_hash=obs_data.get("response_hash", ""),
                        response_structure=obs_data.get("response_structure", "text"),
                        quality=obs_data.get("quality", -1.0),
                        was_cloud=obs_data.get("was_cloud", False),
                    )
                    self._observations.append(obs)
                except Exception as e:
                    logger.debug(f"COMPILER: Skip observation corrompue: {e}")

            # Règles
            for rule_data in data.get("rules", []):
                try:
                    rule = CompiledRule(
                        rule_id=rule_data["rule_id"],
                        agent_name=rule_data["agent_name"],
                        task_type=rule_data["task_type"],
                        condition_mood=rule_data.get("condition_mood"),
                        condition_cognitive=rule_data.get("condition_cognitive"),
                        condition_keywords=rule_data.get("condition_keywords", []),
                        response_template=rule_data.get("response_template", ""),
                        response_structure=rule_data.get("response_structure", "text"),
                        confidence=rule_data.get("confidence", 0.0),
                        observations_count=rule_data.get("observations_count", 0),
                        success_rate=rule_data.get("success_rate", 0.0),
                        intercept_count=rule_data.get("intercept_count", 0),
                        positive_feedback=rule_data.get("positive_feedback", 0),
                        negative_feedback=rule_data.get("negative_feedback", 0),
                        created_at=rule_data.get("created_at", 0.0),
                        last_used=rule_data.get("last_used", 0.0),
                    )
                    self._rules.append(rule)
                except Exception as e:
                    logger.debug(f"COMPILER: Skip règle corrompue: {e}")

            # Métriques
            metrics = data.get("metrics", {})
            self._total_observations = metrics.get("total_observations", 0)
            self._total_intercepts = metrics.get("total_intercepts", 0)
            self._total_llm_calls = metrics.get("total_llm_calls", 0)
            self._total_ab_tests = metrics.get("total_ab_tests", 0)
            self._ab_divergences = metrics.get("ab_divergences", 0)
            self._last_compile_time = metrics.get("last_compile_time", 0.0)

            logger.info(
                f"COMPILER: Chargé {len(self._observations)} observations, "
                f"{len(self._rules)} règles."
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"COMPILER: Fichier corrompu, reset: {e}")
            self._observations = []
            self._rules = []
        except Exception as e:
            logger.warning(f"COMPILER: Erreur chargement: {e}")

    def _save(self):
        """Sauvegarde atomique de l'état."""
        try:
            data = {
                "observations": [],
                "rules": [],
                "metrics": {
                    "total_observations": self._total_observations,
                    "total_intercepts": self._total_intercepts,
                    "total_llm_calls": self._total_llm_calls,
                    "total_ab_tests": self._total_ab_tests,
                    "ab_divergences": self._ab_divergences,
                    "last_compile_time": self._last_compile_time,
                },
            }

            # Observations (sans response_text pour économiser l'espace)
            for obs in self._observations:
                obs_data = {
                    "obs_id": obs.obs_id,
                    "timestamp": obs.timestamp,
                    "fingerprint": asdict(obs.fingerprint),
                    "response_hash": obs.response_hash,
                    "response_structure": obs.response_structure,
                    "quality": obs.quality,
                    "was_cloud": obs.was_cloud,
                }
                data["observations"].append(obs_data)

            # Règles
            for rule in self._rules:
                data["rules"].append(asdict(rule))

            # Écriture atomique
            tmp_path = COMPILER_STATE_FILE + ".tmp"
            os.makedirs(os.path.dirname(COMPILER_STATE_FILE), exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, COMPILER_STATE_FILE)
        except Exception as e:
            logger.warning(f"COMPILER: Erreur sauvegarde: {e}")

    def _maybe_autosave(self):
        """Sauvegarde si le seuil d'observations est atteint."""
        self._obs_since_save += 1
        if self._obs_since_save >= AUTOSAVE_INTERVAL:
            self._save()
            self._obs_since_save = 0

    # ================================================================
    # BUS EVENTS
    # ================================================================

    def _subscribe_events(self):
        """Souscrit aux événements du bus."""
        if self._subscribed:
            return
        self._subscribed = True
        bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
        bus.subscribe("MISSION_FINISHED", self._on_mission_finished)

    async def _on_routine_complete(self, data: dict):
        """Feedback sur les observations récentes basé sur la qualité de la routine."""
        try:
            quality = data.get("quality_score", -1.0)
            if quality < 0:
                return
            agent = data.get("agent", "")
            # Mettre à jour les observations récentes (< 5 min) sans quality
            cutoff = time.time() - 300
            updated = 0
            for obs in reversed(self._observations):
                if obs.timestamp < cutoff:
                    break
                if obs.quality < 0 and obs.fingerprint.agent_name == agent:
                    obs.quality = quality
                    updated += 1
            if updated > 0:
                logger.debug(f"COMPILER: {updated} observations mises à jour (quality={quality:.2f})")

            # Feedback sur les intercepts récents pour cette agent
            self._feedback_recent_intercept(agent, quality)

            # Auto-compilation toutes les 50 observations
            if self._total_observations > 0 and self._total_observations % 50 == 0:
                created = self.compile_rules()
                if created > 0:
                    logger.info(f"COMPILER: Auto-compile déclenché, {created} règles créées.")
        except Exception as e:
            logger.debug(f"COMPILER: Erreur on_routine_complete: {e}")

    async def _on_mission_finished(self, data: dict):
        """Feedback sur les observations de missions utilisateur."""
        try:
            agent = data.get("agent", "")
            status = data.get("status", "")
            # Qualité heuristique : mission réussie = 0.7, erreur = 0.2
            quality = 0.7 if status == "success" else 0.2
            cutoff = time.time() - 300
            updated = 0
            for obs in reversed(self._observations):
                if obs.timestamp < cutoff:
                    break
                if obs.quality < 0 and obs.fingerprint.agent_name == agent:
                    obs.quality = quality
                    updated += 1
            if updated > 0:
                logger.debug(f"COMPILER: {updated} observations (mission) mises à jour (quality={quality:.2f})")

            # Feedback sur les intercepts récents pour cet agent
            self._feedback_recent_intercept(agent, quality)
        except Exception as e:
            logger.debug(f"COMPILER: Erreur on_mission_finished: {e}")

    def _feedback_recent_intercept(self, agent: str, quality: float):
        """Met à jour la règle si cet agent a eu un intercept récent (< 5 min)."""
        intercept = self._last_intercepts.get(agent)
        if intercept is None:
            return
        rule_id, ts = intercept
        if time.time() - ts > 300:
            return  # Trop ancien
        self.record_feedback(rule_id, quality)
        del self._last_intercepts[agent]

    # ================================================================
    # FINGERPRINT
    # ================================================================

    def extract_fingerprint(self, agent_name: str, prompt: str) -> PromptFingerprint:
        """Extrait les features du prompt + contexte organes."""
        task_type = _classify_task_type(agent_name, prompt)
        keywords = _extract_keywords(prompt, top_n=5)
        markers = _detect_markers(prompt)

        return PromptFingerprint(
            agent_name=agent_name,
            task_type=task_type,
            prompt_length=_prompt_length_bucket(len(prompt)),
            has_rag_context="[SOUVENIRS]:" in prompt,
            has_code_block="```" in prompt or bool(re.search(r'\bdef\s+\w+|class\s+\w+', prompt)),
            has_json_request="JSON" in prompt or "format structuré" in prompt.lower(),
            dominant_keywords=keywords,
            markers=markers,
            mood=_get_current_mood(),
            cognitive_state=_get_cognitive_state(),
            dopamine_bucket=_dopamine_bucket(_get_dopamine_level()),
            threat_bucket=_threat_bucket(_get_threat_level()),
            error_streak=min(_get_error_streak(), 5),
        )

    # ================================================================
    # OBSERVATION
    # ================================================================

    def record_observation(self, agent_name: str, prompt: str, response: str,
                           quality: float, was_cloud: bool):
        """Capture un appel LLM avec contexte et résultat."""
        self._total_llm_calls += 1

        fp = self.extract_fingerprint(agent_name, prompt)
        r_hash = _response_hash(response)

        # Déduplication : skip si même hash dans les 20 dernières observations
        recent_hashes = {o.response_hash for o in self._observations[-20:]}
        if r_hash in recent_hashes:
            return

        obs = Observation(
            obs_id=hashlib.md5(f"{time.time()}{agent_name}{r_hash}".encode()).hexdigest()[:12],
            timestamp=time.time(),
            fingerprint=fp,
            response_text=response[:2000],
            response_hash=r_hash,
            response_structure=_detect_response_structure(response),
            quality=quality,
            was_cloud=was_cloud,
        )

        self._observations.append(obs)
        self._total_observations += 1

        # FIFO : garder les MAX_OBSERVATIONS plus récentes
        if len(self._observations) > MAX_OBSERVATIONS:
            self._observations = self._observations[-MAX_OBSERVATIONS:]

        # TTL : purger les observations trop anciennes
        cutoff = time.time() - (OBSERVATION_TTL_DAYS * 86400)
        self._observations = [o for o in self._observations if o.timestamp > cutoff]

        self._maybe_autosave()

    # ================================================================
    # INTERCEPT
    # ================================================================

    def try_intercept(self, agent_name: str, prompt: str) -> Optional[str]:
        """Tente une réponse compilée (pré-LLM). Retourne None si aucune règle."""
        if not self._rules:
            return None

        fp = self.extract_fingerprint(agent_name, prompt)
        best_rule, best_score = self._match_rule(fp)

        if best_rule is None or best_score < INTERCEPT_CONFIDENCE_THRESHOLD:
            return None

        # A/B Test : 10% du temps, on laisse passer au LLM pour vérifier
        if random.random() < AB_TEST_PROBABILITY:
            self._total_ab_tests += 1
            return None

        # Vérifier que la règle n'est pas désactivée
        if best_rule.success_rate < RULE_DISABLE_THRESHOLD:
            return None

        # Interception réussie
        best_rule.intercept_count += 1
        best_rule.last_used = time.time()
        self._total_intercepts += 1

        # Tracker cet intercept pour feedback ultérieur
        self._last_intercepts[agent_name] = (best_rule.rule_id, time.time())

        try:
            import asyncio
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(bus.publish("COMPILER_INTERCEPT", {
                    "agent": agent_name,
                    "task_type": best_rule.task_type,
                    "rule_id": best_rule.rule_id,
                    "confidence": best_score,
                }))
        except Exception:
            pass

        return best_rule.response_template

    def _match_rule(self, fp: PromptFingerprint) -> Tuple[Optional[CompiledRule], float]:
        """Trouve la meilleure règle pour un fingerprint donné."""
        best_rule = None
        best_score = 0.0

        for rule in self._rules:
            # Filtre strict : agent + task_type
            if rule.agent_name != fp.agent_name or rule.task_type != fp.task_type:
                continue

            # Score de matching
            score = rule.confidence

            # Mood matching (wildcard si None)
            if rule.condition_mood is not None:
                if rule.condition_mood == fp.mood:
                    score *= 1.1
                else:
                    score *= 0.7

            # Cognitive state matching
            if rule.condition_cognitive is not None:
                if rule.condition_cognitive == fp.cognitive_state:
                    score *= 1.1
                else:
                    score *= 0.7

            # Keywords matching (tous doivent être présents)
            if rule.condition_keywords:
                fp_kw_set = set(fp.dominant_keywords)
                matched = sum(1 for kw in rule.condition_keywords if kw in fp_kw_set)
                total = len(rule.condition_keywords)
                if total > 0:
                    kw_ratio = matched / total
                    score *= (0.5 + 0.5 * kw_ratio)

            # Cap à 1.0
            score = min(score, 1.0)

            if score > best_score:
                best_score = score
                best_rule = rule

        return best_rule, best_score

    # ================================================================
    # COMPILATION
    # ================================================================

    def compile_rules(self) -> int:
        """Compile les observations en règles déterministes. 0 LLM."""
        now = time.time()
        if now - self._last_compile_time < COMPILE_COOLDOWN:
            return 0

        self._last_compile_time = now
        created = 0

        # Grouper par (agent, task_type)
        groups: Dict[Tuple[str, str], List[Observation]] = {}
        for obs in self._observations:
            key = (obs.fingerprint.agent_name, obs.fingerprint.task_type)
            groups.setdefault(key, []).append(obs)

        for (agent, task_type), obs_list in groups.items():
            if len(obs_list) < MIN_OBSERVATIONS_FOR_RULE:
                continue

            # Sous-grouper par overlap de keywords >= 60%
            clusters = self._cluster_observations(obs_list)

            for cluster in clusters:
                if len(cluster) < MIN_OBSERVATIONS_FOR_RULE:
                    continue

                rule = self._compile_cluster(agent, task_type, cluster)
                if rule is not None:
                    # Vérifier si une règle similaire existe déjà
                    existing = self._find_existing_rule(rule.agent_name, rule.task_type,
                                                        rule.condition_keywords)
                    if existing is not None:
                        # Mise à jour
                        existing.confidence = rule.confidence
                        existing.observations_count = rule.observations_count
                        existing.success_rate = rule.success_rate
                        existing.response_template = rule.response_template
                        existing.response_structure = rule.response_structure
                    else:
                        # Nouvelle règle
                        if len(self._rules) >= MAX_RULES:
                            # Retirer la règle la moins utilisée
                            self._rules.sort(key=lambda r: r.last_used)
                            self._rules.pop(0)
                        self._rules.append(rule)
                    created += 1

        if created > 0:
            self._save()
            stats = self.get_stats()
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.create_task(bus.publish("NEURAL_COMPILED", {
                        "rules_created": created,
                        "rules_total": len(self._rules),
                        "coverage_pct": stats.get("intercept_rate", 0),
                    }))
            except Exception:
                pass

        return created

    def _cluster_observations(self, obs_list: List[Observation]) -> List[List[Observation]]:
        """Cluster les observations par overlap de keywords >= 60%."""
        clusters: List[List[Observation]] = []
        used = set()

        for i, obs in enumerate(obs_list):
            if i in used:
                continue
            cluster = [obs]
            used.add(i)
            for j, other in enumerate(obs_list):
                if j in used:
                    continue
                overlap = _keyword_overlap(
                    obs.fingerprint.dominant_keywords,
                    other.fingerprint.dominant_keywords
                )
                if overlap >= 0.6:
                    cluster.append(other)
                    used.add(j)
            clusters.append(cluster)

        return clusters

    def _compile_cluster(self, agent: str, task_type: str,
                         cluster: List[Observation]) -> Optional[CompiledRule]:
        """Compile un cluster d'observations en une règle."""
        # Vérifier consensus sur la structure de réponse
        structure_counts = Counter(o.response_structure for o in cluster)
        dominant_structure, count = structure_counts.most_common(1)[0]
        if count / len(cluster) < 0.6:
            return None  # Pas assez de consensus

        # Confiance = ratio d'observations de qualité >= 0.6
        quality_obs = [o for o in cluster if o.quality >= 0.6]
        low_quality = [o for o in cluster if 0 <= o.quality < 0.6]
        unevaluated = [o for o in cluster if o.quality < 0]

        if not quality_obs and not unevaluated:
            return None

        total_evaluated = len(quality_obs) + len(low_quality)
        if total_evaluated > 0:
            confidence = len(quality_obs) / total_evaluated
        else:
            confidence = 0.5  # Pas encore évalué -> confiance neutre

        success_rate = confidence

        if confidence < MIN_SUCCESS_RATE:
            return None

        # Template : prendre la réponse de la meilleure observation
        best_obs = max(
            [o for o in cluster if o.response_text],
            key=lambda o: o.quality if o.quality >= 0 else 0.5,
            default=None
        )
        if best_obs is None or not best_obs.response_text:
            return None

        # Mood dominant (ou None si trop varié)
        mood_counts = Counter(o.fingerprint.mood for o in cluster)
        dominant_mood, mood_count = mood_counts.most_common(1)[0]
        condition_mood = dominant_mood if mood_count / len(cluster) >= 0.6 else None

        # Cognitive state dominant
        cog_counts = Counter(o.fingerprint.cognitive_state for o in cluster)
        dominant_cog, cog_count = cog_counts.most_common(1)[0]
        condition_cognitive = dominant_cog if cog_count / len(cluster) >= 0.6 else None

        # Keywords communs
        all_kw = []
        for obs in cluster:
            all_kw.extend(obs.fingerprint.dominant_keywords)
        kw_counts = Counter(all_kw)
        # Garder les mots présents dans >= 50% des observations
        threshold_count = len(cluster) * 0.5
        condition_keywords = [kw for kw, c in kw_counts.most_common(5) if c >= threshold_count]

        rule_id = hashlib.md5(
            f"{agent}:{task_type}:{':'.join(condition_keywords)}:{time.time()}".encode()
        ).hexdigest()[:10]

        return CompiledRule(
            rule_id=rule_id,
            agent_name=agent,
            task_type=task_type,
            condition_mood=condition_mood,
            condition_cognitive=condition_cognitive,
            condition_keywords=condition_keywords,
            response_template=best_obs.response_text[:2000],
            response_structure=dominant_structure,
            confidence=confidence,
            observations_count=len(cluster),
            success_rate=success_rate,
            created_at=time.time(),
        )

    def _find_existing_rule(self, agent: str, task_type: str,
                            keywords: List[str]) -> Optional[CompiledRule]:
        """Trouve une règle existante avec les mêmes critères."""
        for rule in self._rules:
            if (rule.agent_name == agent
                    and rule.task_type == task_type
                    and set(rule.condition_keywords) == set(keywords)):
                return rule
        return None

    # ================================================================
    # FEEDBACK
    # ================================================================

    def record_feedback(self, rule_id: str, quality: float):
        """Feedback post-exécution sur une règle."""
        for rule in self._rules:
            if rule.rule_id == rule_id:
                if quality >= 0.6:
                    rule.positive_feedback += 1
                else:
                    rule.negative_feedback += 1

                # Recalculer success_rate
                total = rule.positive_feedback + rule.negative_feedback
                if total > 0:
                    rule.success_rate = rule.positive_feedback / total

                # Désactivation automatique si trop de feedbacks négatifs
                if rule.success_rate < RULE_DISABLE_THRESHOLD and total >= 5:
                    logger.info(
                        f"COMPILER: Règle {rule_id} désactivée "
                        f"(success_rate={rule.success_rate:.2f})"
                    )

                self._save()
                return
        logger.debug(f"COMPILER: Règle {rule_id} non trouvée pour feedback")

    def record_ab_divergence(self, rule_id: str):
        """Enregistre une divergence A/B (réponse compilée != réponse LLM)."""
        self._ab_divergences += 1
        for rule in self._rules:
            if rule.rule_id == rule_id:
                # Baisser la confiance
                rule.confidence = max(0.0, rule.confidence - 0.05)
                break

    # ================================================================
    # MÉTRIQUES
    # ================================================================

    def get_stats(self) -> dict:
        """Métriques de couverture et performance."""
        active_rules = [r for r in self._rules if r.success_rate >= RULE_DISABLE_THRESHOLD]

        # Taux d'interception
        intercept_rate = 0.0
        if self._total_llm_calls > 0:
            intercept_rate = self._total_intercepts / (self._total_llm_calls + self._total_intercepts)

        # Agents couverts
        agents_covered = list(set(r.agent_name for r in active_rules))

        # Task types couverts
        task_types_covered = list(set(r.task_type for r in active_rules))

        # Confiance moyenne
        avg_confidence = 0.0
        if active_rules:
            avg_confidence = sum(r.confidence for r in active_rules) / len(active_rules)

        return {
            "observations_count": len(self._observations),
            "rules_count": len(self._rules),
            "active_rules_count": len(active_rules),
            "total_observations": self._total_observations,
            "total_llm_calls": self._total_llm_calls,
            "total_intercepts": self._total_intercepts,
            "intercept_rate": intercept_rate,
            "agents_covered": agents_covered,
            "task_types_covered": task_types_covered,
            "avg_confidence": avg_confidence,
            "ab_tests": self._total_ab_tests,
            "ab_divergences": self._ab_divergences,
            "last_compile": self._last_compile_time,
        }


# --- Singleton global ---
compiler = NeuralCompiler()
