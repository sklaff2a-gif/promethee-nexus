import os
import re
import logging
import time
import uuid
from typing import Dict, Any, List, Optional

from core.event_bus.bus import bus

logger = logging.getLogger("Council")

# Marqueurs de consensus (détection en début de réponse)
CONSENSUS_MARKERS = ("CONSENSUS", "CONSENUS", "APPROUVE", "APPROUVÉ", "ACCORD FINAL")

# Tour minimum avant d'autoriser le consensus (force au moins 1 tour de critique)
MIN_ROUNDS_BEFORE_CONSENSUS = 2

# Contexte projet injecté dans tous les prompts Council
_COUNCIL_PROJECT_CONTEXT = (
    "CONTEXTE PROJET PROMÉTHÉE :\n"
    "Système multi-agents IA autonome. Stack : Python 3.11, FastAPI, Ollama (LLM local), "
    "Google Gemini (Cloud), ChromaDB, WebSocket. Tourne sur UN SEUL PC Windows.\n"
    "Modules existants : orchestrator, router (3 niveaux), bus d'événements, "
    "autonomy_engine, ci_pipeline, self_awareness, psyche, vector_store, "
    "10 agents (strategist, coder, architect, factory, formatter, researcher, "
    "writer, security, infra, evolution).\n"
    "CONTRAINTE MATÉRIELLE : Le projet tourne sur UN SEUL PC Windows avec Ollama local. "
    "Pas de cluster, pas de conteneurs, pas de cloud infra.\n"
    "HORS PÉRIMÈTRE : Kubernetes, Docker, Kafka, microservices, blockchain, "
    "Chaos Engineering, load balancing, budget réel."
)


_PROJECT_STRUCTURE_CACHE = None

def _get_project_structure() -> str:
    """Liste dynamique des fichiers réels du projet (lazy, cached)."""
    global _PROJECT_STRUCTURE_CACHE
    if _PROJECT_STRUCTURE_CACHE is not None:
        return _PROJECT_STRUCTURE_CACHE
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lines = ["FICHIERS RÉELS DU PROJET :"]
    for subdir in ["core", "Agents", "core/grimoire", "core/event_bus",
                    "core/capabilities", "core/memory"]:
        dir_path = os.path.join(project_root, subdir.replace("/", os.sep))
        if os.path.isdir(dir_path):
            py_files = sorted(f for f in os.listdir(dir_path) if f.endswith(".py"))
            if py_files:
                lines.append(f"  {subdir}/ : {', '.join(py_files)}")
    root_py = sorted(f for f in os.listdir(project_root)
                     if f.endswith(".py") and os.path.isfile(os.path.join(project_root, f)))
    if root_py:
        lines.append(f"  ./ : {', '.join(root_py)}")
    _PROJECT_STRUCTURE_CACHE = "\n".join(lines)
    return _PROJECT_STRUCTURE_CACHE


def _strip_markdown_prefix(text: str) -> str:
    """Retire les préfixes markdown courants (#, *, >, -) en début de texte."""
    cleaned = text.strip()
    # Retirer les headers markdown (##, ###, etc.)
    cleaned = re.sub(r'^#{1,6}\s*', '', cleaned)
    # Retirer le gras/italique AUTOUR du premier mot (ex: **CONSENSUS**)
    cleaned = re.sub(r'^[\*_]{1,3}(.+?)[\*_]{1,3}', r'\1', cleaned)
    # Retirer le gras/italique en préfixe seul (ex: **CONSENSUS suite)
    cleaned = re.sub(r'^[\*_]{1,3}\s*', '', cleaned)
    # Retirer les blockquotes (>)
    cleaned = re.sub(r'^>\s*', '', cleaned)
    # Retirer les tirets de liste (- )
    cleaned = re.sub(r'^-\s+', '', cleaned)
    return cleaned.strip()


def _is_consensus(text: str) -> bool:
    """Vérifie que la réponse COMMENCE par un marqueur de consensus (tolère le markdown)."""
    cleaned = _strip_markdown_prefix(text).upper()
    return any(cleaned.startswith(marker) for marker in CONSENSUS_MARKERS)


def parse_council_mission(raw_mission: str) -> Optional[Dict[str, Any]]:
    """
    Parse la syntaxe 'agent1, agent2 - mission' ou 'agent1, agent2 : mission'.
    Retourne {"participants": [...], "mission": str} ou None si syntaxe invalide.
    """
    pattern = r"^([\w]+(?:\s*,\s*[\w]+)+)\s*[-:]\s*(.+)$"
    match = re.match(pattern, raw_mission.strip(), re.DOTALL)
    if not match:
        return None
    participants_raw = match.group(1)
    mission = match.group(2).strip()
    participants = [p.strip().lower() for p in participants_raw.split(",")]
    if len(participants) < 2:
        return None
    return {"participants": participants, "mission": mission}


class Council:
    """
    Système de débat multi-tours entre agents.
    V2 : Anti-écho — force la critique au tour 1, consensus interdit avant le tour 2.
    """

    def __init__(self, agents: Dict[str, Any], participants: List[str],
                 mission: str, max_rounds: int = 5):
        self.agents = agents
        self.participants = participants
        self.mission = mission
        self.max_rounds = max_rounds
        self.council_id = str(uuid.uuid4())[:8]
        self.transcript: List[Dict[str, Any]] = []

    def _format_transcript(self) -> str:
        """Formate le transcript pour l'injecter dans le prompt des agents."""
        if not self.transcript:
            return "(Aucune contribution précédente)"
        lines = []
        for entry in self.transcript:
            lines.append(f"[Tour {entry['round']}] {entry['agent'].upper()} :\n{entry['content']}")
        return "\n---\n".join(lines)

    def _build_prompt(self, agent_name: str, current_round: int) -> str:
        """Construit le prompt pour un agent à un tour donné."""
        history = self._format_transcript()

        # Injection du trait dominant (PSYCHE)
        personality_line = ""
        try:
            from core.psyche import psyche
            trait_name, trait_value = psyche.get_dominant_trait(agent_name)
            personality_line = (
                f"TA PERSONNALITÉ: {trait_name.upper()} ({trait_value:.0f}/100). "
                f"Tes réponses reflètent naturellement ce trait dominant.\n"
            )
        except Exception:
            pass

        # Instructions différenciées selon le tour
        if current_round < MIN_ROUNDS_BEFORE_CONSENSUS:
            round_instructions = (
                f"INSTRUCTIONS TOUR {current_round} (CRITIQUE OBLIGATOIRE) :\n"
                f"- Tu ne peux PAS donner ton accord (pas de CONSENSUS, APPROUVE, etc.).\n"
                f"- Tu DOIS identifier au moins UN problème, UN risque ou UNE question.\n"
                f"- Sois précis et technique : cite des fichiers, des fonctions, des cas limites.\n"
                f"- Si la proposition mentionne des technologies que le projet n'utilise pas "
                f"(Kubernetes, Docker, Kafka, blockchain, etc.), signale-le comme hors-périmètre.\n"
            )
        else:
            round_instructions = (
                f"INSTRUCTIONS TOUR {current_round} :\n"
                f"- Analyse les critiques des tours précédents.\n"
                f"- Si toutes les critiques ont été adressées ET que la solution est concrète "
                f"et applicable au projet, commence ta réponse par CONSENSUS.\n"
                f"- Sinon, apporte de nouvelles critiques ou propositions.\n"
                f"- Rappel : une bonne solution est SIMPLE et cible des fichiers EXISTANTS.\n"
            )

        # Structure projet réelle (anti-hallucination de fichiers)
        try:
            project_files = _get_project_structure()
        except Exception:
            project_files = ""

        return (
            f"Tu participes à un CONSEIL multi-agents.\n"
            f"{_COUNCIL_PROJECT_CONTEXT}\n"
            f"{project_files}\n\n"
            f"MISSION : {self.mission}\n"
            f"PARTICIPANTS : {', '.join(p.upper() for p in self.participants)}\n"
            f"TOUR : {current_round}/{self.max_rounds}\n"
            f"TON RÔLE : {agent_name.upper()}\n"
            f"{personality_line}\n"
            f"HISTORIQUE DU DÉBAT :\n{history}\n\n"
            f"{round_instructions}"
        )

    async def run(self) -> Dict[str, Any]:
        """Exécute le débat multi-tours."""
        # Validation : au moins 2 participants
        if len(self.participants) < 2:
            return {"status": "error", "reason": "Il faut au moins 2 participants pour un conseil."}

        # Validation : tous les agents existent
        missing = [p for p in self.participants if p not in self.agents]
        if missing:
            return {"status": "error", "reason": f"Agent(s) introuvable(s) : {', '.join(missing)}"}

        # Publication début
        await bus.publish("COUNCIL_START", {
            "council_id": self.council_id,
            "participants": self.participants,
            "mission": self.mission,
            "max_rounds": self.max_rounds
        })

        rounds_used = 0
        consensus_reached = False

        for round_num in range(1, self.max_rounds + 1):
            rounds_used = round_num
            round_consensus_count = 0

            for participant in self.participants:
                agent = self.agents[participant]
                prompt = self._build_prompt(participant, round_num)

                # Appel via generate_content (pas process_task)
                content = await agent.generate_content(prompt)

                # Enregistrer dans le transcript
                entry = {
                    "agent": participant,
                    "round": round_num,
                    "content": content,
                    "timestamp": time.time()
                }
                self.transcript.append(entry)

                # Publication tour de parole
                await bus.publish("COUNCIL_TURN", {
                    "council_id": self.council_id,
                    "agent": participant,
                    "round": round_num,
                    "max_rounds": self.max_rounds,
                    "content": content
                })

                # Consensus ignoré avant MIN_ROUNDS_BEFORE_CONSENSUS
                if round_num >= MIN_ROUNDS_BEFORE_CONSENSUS and _is_consensus(content):
                    round_consensus_count += 1

            # Consensus = TOUS les participants du round ont approuvé
            if round_consensus_count == len(self.participants):
                consensus_reached = True
                break

        # Résumé final
        status = "consensus" if consensus_reached else "max_rounds"
        last_contributions = self.transcript[-len(self.participants):]
        final_summary = "\n".join(
            f"[{e['agent'].upper()}] {e['content'][:200]}" for e in last_contributions
        )

        # Publication fin
        await bus.publish("COUNCIL_END", {
            "council_id": self.council_id,
            "status": status,
            "rounds_used": rounds_used,
            "participants": self.participants,
            "final_summary": final_summary
        })

        # Publication AGENT_RESPONSE pour le dialogue principal
        await bus.publish("AGENT_RESPONSE", {
            "agent": "CONSEIL",
            "content": f"[{status.upper()}] après {rounds_used} tour(s).\n\n{final_summary}",
            "timestamp": str(time.time())
        })

        return {
            "status": status,
            "rounds_used": rounds_used,
            "transcript": self.transcript,
            "final_summary": final_summary
        }
