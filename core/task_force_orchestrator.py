"""V36.0 (2026-04-30) — Task Force Orchestrator (skeleton).
V36.1 (2026-04-30 pm) — Implementation Ollama + semaphore VRAM + garde-fou prompt.

Couche d'execution interposee entre le motivational_router (qui choisit
QUEL intent executer) et autonomy_engine (qui dispatchait jusqu'ici en
solo). Permet de transformer certains intents complexes (EXPANSION_CODE,
FEATURE_BUILDING, CODE_REVIEW, COUNCIL_DEBATE) en pipelines multi-agents.

Doctrine V36 (validee Jean-Michel 2026-04-30) :
  "Un seul corps qui fatigue, plusieurs ouvriers qui se relaient."

  - cognitive_heat reste GLOBAL. La routine est le grain thermodynamique.
  - L'orchestrator NE TOUCHE JAMAIS desire_engine, thermal_homeostasis,
    motivational_router, drive_routine_registry. Aucun import.
  - 1 SEUL AUTONOMY_ROUTINE_COMPLETE publie a la fin (pas un par agent).
  - Cooldown LOCAL par agent (anti-emballement operationnel), independant
    de tout refractory de pulsion ou de heat globale.
  - Feature flag par intent + global. Desactivable a tout moment.

V36.1 — runtime Ollama :
  - _default_agent_runner() appelle Ollama via httpx (POST /api/generate)
  - Semaphore VRAM (asyncio.Semaphore(1)) garantit max 1 LLM concurrent
    sur le GPU local. La topologie PARALLEL devient logique uniquement,
    physiquement sequentielle. Evite OOM sur RTX 5070 Ti 16GB.
  - Garde-fou prompt : MAX_PROMPT_CHARS = 24000 (~6k tokens). Au-dela,
    troncature FIFO des outputs blackboard les plus anciens (V36.1 simple).
    V36.2+ pourra raffiner avec resume LLM des outputs anciens.

Transmission inter-agents : Blackboard partage. Chaque agent recoit un
prompt qui inclut tous les outputs precedents (formates par role).
Cf TaskForceState.build_prompt_for_agent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("TaskForceOrchestrator")

STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "task_force_state.json"
)

# V36.1 — Constantes runtime
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MAX_PROMPT_CHARS = 24000   # ~6000 tokens (heuristique 4 chars/token)
DEFAULT_TEMPERATURE = 0.5

# Semaphore VRAM : max 1 LLM concurrent sur le GPU local.
# Garantit que la topologie PARALLEL_THEN_SYNTH ne crashe pas l'Ollama
# sur LLMs 9b/14b (RTX 5070 Ti 16GB partagee). Le parallele devient
# logique cote orchestrator, sequentiel physique cote API.
_VRAM_SEMAPHORE: Optional[asyncio.Semaphore] = None  # cree au premier usage


def _get_vram_semaphore() -> asyncio.Semaphore:
    """Lazy init du semaphore (necessite un event loop actif)."""
    global _VRAM_SEMAPHORE
    if _VRAM_SEMAPHORE is None:
        _VRAM_SEMAPHORE = asyncio.Semaphore(1)
    return _VRAM_SEMAPHORE


# ═══════════════════════════════════════════════════════════════════════
# Topologies d'execution
# ═══════════════════════════════════════════════════════════════════════

class Topology(Enum):
    SEQUENTIAL = "sequential"
    """A puis B puis C. Chaque agent voit les outputs precedents."""

    SEQUENTIAL_FEEDBACK = "sequential_feedback"
    """Apres le dernier agent, le pipeline peut reboucler vers le premier
    pour un nb max d'iterations. Permet revision/amendement."""

    PARALLEL_THEN_SYNTH = "parallel_then_synth"
    """N premiers agents en parallele logique (sequentiel physique sur
    Ollama via semaphore VRAM en V36.1), dernier agent synthetise."""


# ═══════════════════════════════════════════════════════════════════════
# Structures de donnees
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class AgentRole:
    """Un ouvrier de la task force. Localise par role (architect, coder...).

    Le refractory_seconds est LOCAL : empeche cet agent de tourner trop
    souvent en cascade, sans aucun lien avec les refractory de pulsion
    du motivational_router.
    """
    role: str
    llm_model: str
    system_prompt: str
    refractory_seconds: int = 0
    timeout_seconds: int = 90


@dataclass
class TaskForce:
    """Equipe assignee a un intent. Topologie + sequence d'agents."""
    name: str
    agents: List[AgentRole]
    topology: Topology
    max_iterations: int = 1


@dataclass
class TaskForceState:
    """Etat d'execution d'une taskforce (in-flight, non-persiste).

    Contient le blackboard partage entre agents. Cf build_prompt_for_agent.
    """
    intent: str
    mission: str
    context: Dict[str, Any] = field(default_factory=dict)
    blackboard: Dict[str, str] = field(default_factory=dict)
    trace: List[Dict[str, Any]] = field(default_factory=list)
    iteration: int = 0
    started_at: float = field(default_factory=time.time)

    def build_prompt_for_agent(
        self, agent: AgentRole, max_chars: int = MAX_PROMPT_CHARS,
    ) -> str:
        """Construit le prompt complet d'un agent en exposant le blackboard.

        Le prompt inclut :
          1. system_prompt specifique au role
          2. MISSION textuelle
          3. CONTRIBUTIONS PRECEDENTES (tous les outputs accumules)
          4. Marqueur "[role] A toi."

        V36.1 — Garde-fou taille : si prompt > max_chars, on tronque FIFO
        les outputs les plus anciens du blackboard jusqu'a passer sous
        le seuil. Les contributions recentes sont preservees (le critic
        veut surtout voir le coder qui vient de repondre).
        Logge en INFO si troncature appliquee — donne les donnees
        empiriques pour V36.2+ (resume LLM sophistique si necessaire).
        """
        # Construction blackboard ordonnee (insertion order Python 3.7+)
        blackboard_items = list(self.blackboard.items())

        prompt = self._compose_prompt(agent, blackboard_items)
        truncated_count = 0

        while len(prompt) > max_chars and len(blackboard_items) > 1:
            # Retire le plus ancien output (FIFO, preserve les recents)
            blackboard_items.pop(0)
            truncated_count += 1
            prompt = self._compose_prompt(
                agent, blackboard_items, was_truncated=truncated_count
            )

        if truncated_count > 0:
            logger.info(
                f"V36.1: prompt [{agent.role}] tronque ({truncated_count} "
                f"output(s) ancien(s) retire(s), len={len(prompt)})"
            )

        return prompt

    def _compose_prompt(
        self, agent: AgentRole,
        blackboard_items: List[tuple],
        was_truncated: int = 0,
    ) -> str:
        """Helper interne : compose le prompt a partir d'items ordonnes."""
        parts = [agent.system_prompt.strip()]
        parts.append(f"\nMISSION:\n{self.mission.strip()}")
        if blackboard_items:
            parts.append("\nCONTRIBUTIONS PRECEDENTES:")
            if was_truncated > 0:
                parts.append(
                    f"\n(Note: {was_truncated} contribution(s) ancienne(s) "
                    f"ont ete elaguees pour respecter la fenetre de contexte.)"
                )
            for prev_role, prev_output in blackboard_items:
                parts.append(f"\n[{prev_role}]\n{prev_output.strip()}")
        if self.iteration > 0:
            parts.append(
                f"\n(Iteration {self.iteration + 1} — tu peux amender ta "
                f"contribution precedente.)"
            )
        parts.append(f"\n[{agent.role}] A toi.")
        return "\n".join(parts)

    def add_output(self, role: str, output: str) -> None:
        """Ajoute (ou ecrase) la contribution d'un role au blackboard."""
        self.blackboard[role] = output


# ═══════════════════════════════════════════════════════════════════════
# Mapping INTENT → TASKFORCE (V36.0 : 4 intents complexes)
# ═══════════════════════════════════════════════════════════════════════

_ARCHITECT_PROMPT = (
    "Tu es l'Architecte. Tu reflechis avant de coder. Concois la structure "
    "(modules, classes, interfaces) en 200 mots maximum, sans implementation."
)
_CODER_PROMPT = (
    "Tu es le Codeur. Tu implementes l'architecture proposee en Python "
    "lisible et idiomatique. Cite uniquement le code final, pas de meta-commentaire."
)
_CRITIC_PROMPT = (
    "Tu es le Critique. Identifie 3 faiblesses concretes dans le code "
    "ou l'architecture proposee. Pour chaque faiblesse, propose un fix actionnable."
)
_TESTER_PROMPT = (
    "Tu es le Testeur. Genere les tests unitaires pytest qui couvrent "
    "les cas nominaux et au moins 2 cas limites du code propose."
)
_SECURITY_PROMPT = (
    "Tu es le Specialiste Securite. Identifie les vulnerabilites OWASP "
    "potentielles (injection, XSS, command injection, secrets exposes, etc.)."
)
_SYNTHESIZER_PROMPT = (
    "Tu synthetises les contributions precedentes en bilan unifie de 150 mots. "
    "Tu hierarchises les points par criticite, tu ne repetes pas, tu tranches."
)


INTENT_TO_TASKFORCE: Dict[str, TaskForce] = {
    "EXPANSION_CODE": TaskForce(
        name="code_generation",
        agents=[
            AgentRole("architect", "qwen3.5:9b", _ARCHITECT_PROMPT,
                      refractory_seconds=300, timeout_seconds=60),
            AgentRole("coder", "qwen2.5-coder:14b", _CODER_PROMPT,
                      refractory_seconds=120, timeout_seconds=180),
            AgentRole("critic", "qwen3.5:9b", _CRITIC_PROMPT,
                      refractory_seconds=180, timeout_seconds=60),
        ],
        topology=Topology.SEQUENTIAL_FEEDBACK,
        max_iterations=2,
    ),

    "FEATURE_BUILDING": TaskForce(
        name="feature_pipeline",
        agents=[
            AgentRole("architect", "qwen3.5:9b", _ARCHITECT_PROMPT,
                      refractory_seconds=300, timeout_seconds=60),
            AgentRole("coder", "qwen2.5-coder:14b", _CODER_PROMPT,
                      refractory_seconds=120, timeout_seconds=180),
            AgentRole("tester", "qwen3.5:9b", _TESTER_PROMPT,
                      refractory_seconds=240, timeout_seconds=90),
        ],
        topology=Topology.SEQUENTIAL,
    ),

    "CODE_REVIEW": TaskForce(
        name="code_review",
        agents=[
            AgentRole("critic", "qwen3.5:9b", _CRITIC_PROMPT,
                      refractory_seconds=120, timeout_seconds=60),
            AgentRole("security", "qwen3.5:9b", _SECURITY_PROMPT,
                      refractory_seconds=180, timeout_seconds=60),
            AgentRole("synthesizer", "qwen3.5:9b", _SYNTHESIZER_PROMPT,
                      refractory_seconds=60, timeout_seconds=60),
        ],
        topology=Topology.PARALLEL_THEN_SYNTH,
    ),

    "COUNCIL_DEBATE": TaskForce(
        name="council_debate",
        agents=[
            AgentRole("strategist", "promethee-strategist", _ARCHITECT_PROMPT,
                      refractory_seconds=300, timeout_seconds=60),
            AgentRole("evolution", "qwen3.5:9b", _CRITIC_PROMPT,
                      refractory_seconds=300, timeout_seconds=60),
            AgentRole("synthesizer", "qwen3.5:9b", _SYNTHESIZER_PROMPT,
                      refractory_seconds=60, timeout_seconds=60),
        ],
        topology=Topology.PARALLEL_THEN_SYNTH,
    ),
}


# ═══════════════════════════════════════════════════════════════════════
# Feature flags
# ═══════════════════════════════════════════════════════════════════════
# V36.0 : tout OFF (skeleton dormant)
# V36.1.1 (2026-04-30 pm) : activation EXPANSION_CODE en sandbox
# Les autres intents restent OFF tant qu'ils ne sont pas valides en runtime.
# Coupure d'urgence : remettre TASKFORCE_GLOBAL_ENABLED = False et reboot.

TASKFORCE_GLOBAL_ENABLED: bool = True  # V36.1.1 : activation runtime

TASKFORCE_INTENT_ENABLED: Dict[str, bool] = {
    "EXPANSION_CODE":   True,   # V36.1.1 : 1er intent active en sandbox
    "FEATURE_BUILDING": False,
    "CODE_REVIEW":      False,
    "COUNCIL_DEBATE":   False,
}


# ═══════════════════════════════════════════════════════════════════════
# Type alias : provider d'agent runner (injection de dependance)
# ═══════════════════════════════════════════════════════════════════════

# Signature : (agent, prompt) -> output_text
AgentRunnerFn = Callable[[AgentRole, str], Awaitable[str]]


# ═══════════════════════════════════════════════════════════════════════
# Orchestrator
# ═══════════════════════════════════════════════════════════════════════

class TaskForceOrchestrator:
    """Singleton — orchestre les pipelines multi-agents.

    V36.0 : skeleton. _run_agent() est un stub qui raise NotImplementedError.
    Pour les tests, injecter un agent_runner via set_agent_runner().
    V36.1 implementera _run_agent() avec Ollama + semaphore VRAM.
    """

    _instance: Optional["TaskForceOrchestrator"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        # Cooldowns locaux par role (timestamp dernier run)
        self._agent_last_run: Dict[str, float] = {}
        # Provider d'execution agent (injection pour tests, sera Ollama en V36.1)
        self._agent_runner: Optional[AgentRunnerFn] = None
        # Historique succinct (pour observabilite)
        self._history: List[Dict[str, Any]] = []
        self.MAX_HISTORY = 100

    def reset(self) -> None:
        """Reset complet (tests)."""
        self._agent_last_run = {}
        self._agent_runner = None
        self._history = []

    @classmethod
    def reset_singleton(cls) -> None:
        if cls._instance is not None:
            cls._instance.reset()
            cls._instance = None

    # ─── Injection de dependance ───────────────────────────────────────

    def set_agent_runner(self, fn: Optional[AgentRunnerFn]) -> None:
        """Enregistre la fonction d'execution d'agent.

        En runtime production : laisse a None — _resolve_runner() utilisera
        le default_agent_runner V36.1 (Ollama + semaphore VRAM).
        En tests : injecter un mock async qui retourne des outputs predetermines.
        """
        self._agent_runner = fn

    def use_default_ollama_runner(self) -> None:
        """V36.1 — Branche le runner Ollama par defaut (httpx + semaphore VRAM).
        A appeler explicitement au boot par main.py si on veut activer V36.
        En tests, on prefere injecter un mock via set_agent_runner().
        """
        self._agent_runner = _default_agent_runner

    # ─── API publique : CONTRAT D'INTERFACE ────────────────────────────

    async def execute(
        self,
        intent: str,
        mission: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Point d'entree unique depuis autonomy_engine.

        INPUT (contrat strict) :
            intent   : str    nom canonique de l'intent
            mission  : str    mission textuelle
            context  : dict   etat systeme (drives, heat, etc.) optionnel

        OUTPUT (contrat strict, format autonomy_engine) :
            {
                "status":            "success" | "skipped" | "error",
                "result":            str,    synthese de l'execution
                "quality_score":     float,  0.0-1.0
                "reason":            str,    pour skipped/error
                "task_force_trace":  list,   observabilite granulaire
            }
        """
        ctx = dict(context or {})

        # 1. Feature flag : si desactive pour cet intent → fallback solo
        if not self._is_enabled(intent):
            return await self._fallback_solo(intent, mission, ctx,
                                              reason="taskforce_disabled")

        # 2. Recuperer la task force assignee
        tf = INTENT_TO_TASKFORCE.get(intent)
        if not tf:
            return await self._fallback_solo(intent, mission, ctx,
                                              reason="no_taskforce_for_intent")

        # 3. Verifier cooldowns locaux des agents
        blocked = self._agents_in_refractory(tf)
        if blocked:
            return {
                "status": "skipped",
                "result": f"Agents en cooldown local : {', '.join(blocked)}",
                "quality_score": 0.0,
                "reason": "agent_refractory",
                "task_force_trace": [],
            }

        # 4. Verifier qu'un agent_runner est branche
        # En runtime : appeler use_default_ollama_runner() au boot
        # En tests : injecter un mock via set_agent_runner()
        if self._agent_runner is None:
            return await self._fallback_solo(intent, mission, ctx,
                                              reason="no_agent_runner")

        # 5. Executer selon topologie
        state = TaskForceState(intent=intent, mission=mission, context=ctx)
        try:
            await self._execute_topology(tf, state)
        except asyncio.TimeoutError:
            return {
                "status": "error",
                "result": "Taskforce timeout",
                "quality_score": 0.0,
                "reason": "timeout",
                "task_force_trace": state.trace,
            }
        except Exception as e:
            logger.error(f"V36: TaskForce {intent} failed: {e}")
            return {
                "status": "error",
                "result": str(e),
                "quality_score": 0.0,
                "reason": "exception",
                "task_force_trace": state.trace,
            }

        # 6. Synthese finale + scoring
        final_result = self._synthesize_final_result(state)
        quality = self._score_quality(state)

        # 7. Historiser pour observabilite
        self._record_history(tf, state, "success", quality)

        return {
            "status": "success",
            "result": final_result,
            "quality_score": quality,
            "reason": "",
            "task_force_trace": state.trace,
        }

    # ─── Fallback solo (chemin legacy V34/V35) ─────────────────────────

    async def _fallback_solo(
        self, intent: str, mission: str,
        context: Dict[str, Any], reason: str,
    ) -> Dict[str, Any]:
        """Delegue a autonomy_engine._execute_legacy_solo (chemin V34/V35).

        V36.0 : la methode autonomy._execute_legacy_solo n'existe pas encore.
        Tant que le hook n'est pas branche cote autonomy_engine, on retourne
        un payload skipped propre. Apres branchement V36.1, ce sera transparent.
        """
        try:
            from core.autonomy_engine import autonomy
            if hasattr(autonomy, "_execute_legacy_solo"):
                return await autonomy._execute_legacy_solo(intent, mission, context)
        except Exception as e:
            logger.debug(f"V36: autonomy hook absent: {e}")
        return {
            "status": "skipped",
            "result": f"V36 inactif (raison: {reason}), pas de fallback solo branche",
            "quality_score": 0.0,
            "reason": reason,
            "task_force_trace": [],
        }

    # ─── Topologies ────────────────────────────────────────────────────

    async def _execute_topology(
        self, tf: TaskForce, state: TaskForceState,
    ) -> None:
        if tf.topology is Topology.SEQUENTIAL:
            await self._execute_sequential(tf, state)
        elif tf.topology is Topology.SEQUENTIAL_FEEDBACK:
            await self._execute_sequential_feedback(tf, state)
        elif tf.topology is Topology.PARALLEL_THEN_SYNTH:
            await self._execute_parallel_then_synth(tf, state)
        else:
            raise ValueError(f"Topology inconnue: {tf.topology}")

    async def _execute_sequential(
        self, tf: TaskForce, state: TaskForceState,
    ) -> None:
        """A puis B puis C. Blackboard accumule."""
        for agent in tf.agents:
            await self._run_one_agent(agent, state)

    async def _execute_sequential_feedback(
        self, tf: TaskForce, state: TaskForceState,
    ) -> None:
        """SEQUENTIAL puis re-passes (max_iterations). Blackboard ecrase
        a chaque iteration (un agent peut amender)."""
        for it in range(tf.max_iterations):
            state.iteration = it
            for agent in tf.agents:
                await self._run_one_agent(agent, state)

    async def _execute_parallel_then_synth(
        self, tf: TaskForce, state: TaskForceState,
    ) -> None:
        """V36.0 : parallele LOGIQUE seulement (sequentiel physique).
        V36.1 : parallele physique avec semaphore VRAM (max 1 LLM concurrent
        local pour eviter OOM Ollama).

        Le dernier agent de tf.agents est le synthesizer (synthese finale)
        et tourne apres tous les autres."""
        if len(tf.agents) < 2:
            raise ValueError("PARALLEL_THEN_SYNTH requiert >= 2 agents")
        parallel_agents = tf.agents[:-1]
        synthesizer = tf.agents[-1]
        # V36.0 : sequentiel pour eviter de saturer VRAM Ollama
        for agent in parallel_agents:
            await self._run_one_agent(agent, state)
        await self._run_one_agent(synthesizer, state)

    # ─── Execution d'un agent ──────────────────────────────────────────

    async def _run_one_agent(
        self, agent: AgentRole, state: TaskForceState,
    ) -> None:
        """Construit le prompt, appelle _agent_runner, met a jour blackboard."""
        prompt = state.build_prompt_for_agent(agent)
        t0 = time.time()
        try:
            output = await asyncio.wait_for(
                self._agent_runner(agent, prompt),
                timeout=agent.timeout_seconds,
            )
        except asyncio.TimeoutError:
            duration = time.time() - t0
            state.trace.append({
                "role": agent.role,
                "model": agent.llm_model,
                "duration_s": round(duration, 2),
                "output_preview": "",
                "status": "timeout",
                "timestamp": time.time(),
            })
            raise

        duration = time.time() - t0
        state.add_output(agent.role, output)
        state.trace.append({
            "role": agent.role,
            "model": agent.llm_model,
            "duration_s": round(duration, 2),
            "output_preview": (output[:200] + "...") if len(output) > 200 else output,
            "status": "success",
            "timestamp": time.time(),
        })
        # Pose le cooldown local
        self._agent_last_run[agent.role] = time.time()

    # ─── Cooldowns locaux ──────────────────────────────────────────────

    def _agents_in_refractory(self, tf: TaskForce) -> List[str]:
        """Retourne la liste des roles encore en cooldown local."""
        now = time.time()
        blocked = []
        for agent in tf.agents:
            if agent.refractory_seconds <= 0:
                continue
            last = self._agent_last_run.get(agent.role, 0.0)
            if now - last < agent.refractory_seconds:
                blocked.append(agent.role)
        return blocked

    # ─── Synthese & scoring ────────────────────────────────────────────

    def _synthesize_final_result(self, state: TaskForceState) -> str:
        """Retourne le dernier output non vide du blackboard (V36.0 simple).
        V36.1+ pourra raffiner (ex: priorite au synthesizer si present)."""
        if not state.blackboard:
            return ""
        # Dernier role inscrit (insertion order Python 3.7+)
        last_role = list(state.blackboard.keys())[-1]
        return state.blackboard[last_role]

    def _score_quality(self, state: TaskForceState) -> float:
        """Heuristique simple V36.0 :
          - 1.0 si tous les agents ont output non-vide
          - proportion des agents reussis sinon
        V36.1+ pourra raffiner (parser confidence des LLMs, etc.)."""
        if not state.trace:
            return 0.0
        ok = sum(1 for t in state.trace if t.get("status") == "success")
        return round(ok / len(state.trace), 2)

    # ─── Feature flags ─────────────────────────────────────────────────

    def _is_enabled(self, intent: str) -> bool:
        if not TASKFORCE_GLOBAL_ENABLED:
            return False
        return TASKFORCE_INTENT_ENABLED.get(intent, False)

    # ─── Persistance & historique ──────────────────────────────────────

    def _record_history(
        self, tf: TaskForce, state: TaskForceState,
        status: str, quality: float,
    ) -> None:
        self._history.append({
            "intent": state.intent,
            "taskforce": tf.name,
            "started_at": state.started_at,
            "duration_s": round(time.time() - state.started_at, 2),
            "status": status,
            "quality_score": quality,
            "n_agents": len(tf.agents),
            "n_iterations": state.iteration + 1,
        })
        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY:]

    def get_history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    def get_state(self) -> Dict[str, Any]:
        """Snapshot pour /api/v36/state (V36.1+) ou debug."""
        now = time.time()
        return {
            "global_enabled": TASKFORCE_GLOBAL_ENABLED,
            "intent_enabled": dict(TASKFORCE_INTENT_ENABLED),
            "agent_runner_branched": self._agent_runner is not None,
            "agent_cooldowns_remaining": {
                role: max(0, refractory_for(role) - (now - last))
                for role, last in self._agent_last_run.items()
                for refractory_for in [self._lookup_refractory]
            },
            "history_size": len(self._history),
        }

    def _lookup_refractory(self, role: str) -> int:
        """Cherche le refractory_seconds le plus eleve pour ce role
        a travers toutes les taskforces (un role peut apparaitre dans
        plusieurs taskforces avec des refractory differents)."""
        max_r = 0
        for tf in INTENT_TO_TASKFORCE.values():
            for agent in tf.agents:
                if agent.role == role and agent.refractory_seconds > max_r:
                    max_r = agent.refractory_seconds
        return max_r


# ═══════════════════════════════════════════════════════════════════════
# V36.1 — Default agent runner (Ollama + semaphore VRAM)
# ═══════════════════════════════════════════════════════════════════════

async def _default_agent_runner(agent: AgentRole, prompt: str) -> str:
    """V36.1 — Execute un agent via Ollama local (httpx).

    Acquiert le semaphore VRAM avant l'appel — garantit max 1 LLM
    concurrent sur le GPU local. Le parallele logique d'orchestrator
    devient sequentiel physique a ce niveau, evitant OOM.

    Le timeout est gere a un niveau plus haut (asyncio.wait_for dans
    _run_one_agent), donc ici on appelle httpx avec un timeout aligne.

    Retourne le texte genere brut. En cas d'erreur Ollama, raise
    l'exception — _run_one_agent l'attrapera et marquera la trace.
    """
    sem = _get_vram_semaphore()
    async with sem:
        try:
            import httpx  # lazy import (pas une dep dure du module)
        except ImportError as e:
            raise RuntimeError(f"V36.1 requiert httpx: {e}")

        payload = {
            "model": agent.llm_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": DEFAULT_TEMPERATURE,
                "num_predict": 1024,  # ~750 mots, suffisant V36.1
            },
        }

        # Timeout client httpx aligne sur le timeout agent (avec marge 5s)
        client_timeout = max(30, agent.timeout_seconds + 5)

        async with httpx.AsyncClient(timeout=client_timeout) as client:
            response = await client.post(OLLAMA_URL, json=payload)
            response.raise_for_status()
            data = response.json()
            output = data.get("response", "").strip()
            if not output:
                raise RuntimeError(
                    f"Ollama a retourne une reponse vide pour {agent.role}"
                )
            return output


# Singleton global
orchestrator = TaskForceOrchestrator()
