import re
import logging
import time
import uuid
from typing import Dict, Any, List, Optional

from core.event_bus.bus import bus

logger = logging.getLogger("Council")

# Marqueurs de consensus (détection en début de réponse)
CONSENSUS_MARKERS = ("CONSENSUS", "APPROUVE", "APPROUVÉ", "ACCORD FINAL")


def _is_consensus(text: str) -> bool:
    """Vérifie que la réponse COMMENCE par un marqueur de consensus."""
    cleaned = text.strip().upper()
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
    Les agents discutent, critiquent et convergent vers un consensus.
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
        return (
            f"Tu participes à un CONSEIL multi-agents.\n"
            f"MISSION : {self.mission}\n"
            f"PARTICIPANTS : {', '.join(p.upper() for p in self.participants)}\n"
            f"TOUR : {current_round}/{self.max_rounds}\n"
            f"TON RÔLE : {agent_name.upper()}\n\n"
            f"HISTORIQUE DU DÉBAT :\n{history}\n\n"
            f"INSTRUCTIONS :\n"
            f"- Analyse la mission et les contributions précédentes.\n"
            f"- Apporte ton expertise spécifique ({agent_name}).\n"
            f"- Si tu estimes que le débat a convergé et que la solution est satisfaisante, "
            f"commence ta réponse par CONSENSUS suivi de ton approbation.\n"
            f"- Sinon, expose tes critiques ou suggestions.\n"
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

                if _is_consensus(content):
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
