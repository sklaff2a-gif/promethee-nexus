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

# Tour minimum avant d'autoriser le consensus (force au moins 2 tours de critique)
MIN_ROUNDS_BEFORE_CONSENSUS = 3

# Longueur minimale du contenu d'un consensus pour qu'il soit considéré substantiel
MIN_CONSENSUS_CONTENT_LENGTH = 100

# --- Président évaluateur (architect) ---
PRESIDENT_AGENT_NAME = "architect"
MIN_ROUNDS_BEFORE_PRESIDENT = 2

# Contexte projet injecté dans tous les prompts Council
# Note: base_agent.generate_content() injecte aussi un header projet court — garder cohérent
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


from core.prompt_templates import get_project_structure as _get_project_structure


# --- Scoring des arguments du Council ---
# Critères objectifs pour pondérer la force d'un argument
_FILE_PATTERN = re.compile(r'(?:core/|Agents/|config[./]|tests/|main\.py)\S+')
_ACTION_VERBS = re.compile(
    r'(?:ajouter|modifier|créer|supprimer|remplacer|implémenter|refactorer|déplacer|'
    r'renommer|corriger|injecter|extraire|vérifier|valider)',
    re.IGNORECASE
)
_CODE_BLOCK = re.compile(r'```(?:python)?\s*\n.+?\n```', re.DOTALL)


def _score_argument(content: str) -> dict:
    """Score un argument de Council sur des critères objectifs.
    Retourne {"score": 0.0-1.0, "confidence": 0.0-1.0, "breakdown": {...}}."""
    if not content or len(content.strip()) < 20:
        return {"score": 0.0, "confidence": 0.0, "breakdown": {}}

    points = 0.0
    breakdown = {}

    # 1. Citations de fichiers réels du projet (0-3 pts)
    files_cited = _FILE_PATTERN.findall(content)
    unique_files = set(files_cited)
    file_score = min(len(unique_files), 3)
    points += file_score
    breakdown["fichiers_cités"] = len(unique_files)

    # 2. Verbes d'action concrets (0-2 pts)
    actions = _ACTION_VERBS.findall(content)
    action_score = min(len(actions), 2)
    points += action_score
    breakdown["actions_proposées"] = len(actions)

    # 3. Blocs de code (0-2 pts)
    code_blocks = _CODE_BLOCK.findall(content)
    code_score = min(len(code_blocks), 2)
    points += code_score
    breakdown["blocs_code"] = len(code_blocks)

    # 4. Longueur substantielle (0-2 pts) — plus c'est développé, mieux c'est
    length = len(content.strip())
    if length >= 500:
        length_score = 2.0
    elif length >= 200:
        length_score = 1.0
    else:
        length_score = 0.5
    points += length_score
    breakdown["longueur"] = length

    # 5. Pénalité : contenu en anglais (-1 pt)
    english_markers = len(re.findall(
        r'\b(?:should|would|could|implement|function|however|therefore|moreover)\b',
        content, re.IGNORECASE
    ))
    if english_markers >= 3:
        points -= 1.0
        breakdown["pénalité_anglais"] = english_markers

    # Normaliser sur [0, 1]
    max_points = 9.0  # 3+2+2+2
    score = max(0.0, min(points / max_points, 1.0))

    # Confidence = à quel point le score est fiable (basé sur la richesse du contenu)
    confidence = min(1.0, (len(unique_files) + len(actions) + len(code_blocks)) / 5.0)

    return {"score": round(score, 2), "confidence": round(confidence, 2), "breakdown": breakdown}


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
    """Vérifie que la réponse COMMENCE par un marqueur de consensus (tolère le markdown)
    ET que le contenu après le marqueur est substantiel (>= MIN_CONSENSUS_CONTENT_LENGTH chars)."""
    cleaned = _strip_markdown_prefix(text)
    cleaned_upper = cleaned.upper()
    for marker in CONSENSUS_MARKERS:
        if cleaned_upper.startswith(marker):
            # Extraire le contenu après le marqueur
            remainder = cleaned[len(marker):].strip(" :\n\t-—")
            if len(remainder) < MIN_CONSENSUS_CONTENT_LENGTH:
                return False  # Shallow consensus — contenu insuffisant
            return True
    return False


def _parse_president_verdict(response: str) -> Dict[str, str]:
    """Parse la réponse du président (architect) pour extraire le verdict.
    Retourne {"verdict": "PERTINENT"|"REDIRECT"|"ABORT", "feedback": str}."""
    if not response or not response.strip():
        return {"verdict": "PERTINENT", "feedback": ""}

    cleaned = _strip_markdown_prefix(response.strip())
    first_line = cleaned.split("\n", 1)[0].strip()
    first_word = first_line.split()[0].upper().rstrip(":") if first_line.split() else ""

    for verdict in ("PERTINENT", "REDIRECT", "ABORT"):
        if first_word == verdict:
            feedback = first_line[len(verdict):].strip(" :\t—-")
            if not feedback and "\n" in cleaned:
                feedback = cleaned.split("\n", 1)[1].strip()
            return {"verdict": verdict, "feedback": feedback}

    # Fallback : scanner les 100 premiers caractères
    head = cleaned[:100].upper()
    for verdict in ("ABORT", "REDIRECT", "PERTINENT"):
        if verdict in head:
            feedback = cleaned[head.index(verdict) + len(verdict):].strip(" :\t—-\n")
            return {"verdict": verdict, "feedback": feedback[:200]}

    return {"verdict": "PERTINENT", "feedback": ""}


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
        """Formate le transcript pour l'injecter dans le prompt des agents.
        Inclut le score de pertinence de chaque argument."""
        if not self.transcript:
            return "(Aucune contribution précédente)"
        lines = []
        for entry in self.transcript:
            score = entry.get("score", 0)
            score_label = "★★★" if score >= 0.6 else "★★" if score >= 0.3 else "★"
            lines.append(
                f"[Tour {entry['round']}] {entry['agent'].upper()} "
                f"(pertinence: {score_label} {score:.0%}) :\n{entry['content']}"
            )
        return "\n---\n".join(lines)

    def _build_prompt(self, agent_name: str, current_round: int, president_feedback: str = "") -> str:
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

        # Feedback du président (si REDIRECT au tour précédent)
        president_block = ""
        if president_feedback:
            president_block = (
                f"\n⚠️ FEEDBACK DU PRÉSIDENT (architect) :\n"
                f"{president_feedback}\n"
                f"Tu DOIS prendre en compte ce feedback dans ta réponse.\n\n"
            )

        return (
            f"Tu participes à un CONSEIL multi-agents.\n"
            f"LANGUE OBLIGATOIRE : Réponds UNIQUEMENT en français. Pas d'anglais.\n"
            f"{_COUNCIL_PROJECT_CONTEXT}\n"
            f"{project_files}\n\n"
            f"MISSION : {self.mission}\n"
            f"PARTICIPANTS : {', '.join(p.upper() for p in self.participants)}\n"
            f"TOUR : {current_round}/{self.max_rounds}\n"
            f"TON RÔLE : {agent_name.upper()}\n"
            f"{personality_line}\n"
            f"HISTORIQUE DU DÉBAT :\n{history}\n\n"
            f"{round_instructions}\n"
            f"{president_block}"
            f"--- RAPPEL FINAL ---\n"
            f"RÉPONDS EN FRANÇAIS UNIQUEMENT. Pas d'anglais, même pour les termes techniques courants.\n"
            f"Cite des fichiers EXISTANTS du projet (core/, Agents/).\n"
        )

    def _build_president_prompt(self, round_num: int) -> str:
        """Construit le prompt court pour le président (architect) évaluateur."""
        # Contributions du tour courant (tronquées)
        round_entries = [e for e in self.transcript if e["round"] == round_num]
        contributions = "\n".join(
            f"- {e['agent'].upper()} : {e['content'][:300]}"
            for e in round_entries
        )

        return (
            f"Tu es le PRÉSIDENT du conseil. Tu évalues la QUALITÉ du débat (tu ne participes PAS).\n"
            f"MISSION : {self.mission}\n"
            f"TOUR {round_num}/{self.max_rounds}\n\n"
            f"CONTRIBUTIONS DE CE TOUR :\n{contributions}\n\n"
            f"CRITÈRES D'ÉVALUATION :\n"
            f"1. Les propositions sont-elles pertinentes pour la mission ?\n"
            f"2. Y a-t-il des technologies HORS PÉRIMÈTRE (Kubernetes, Docker, Kafka, blockchain, microservices) ?\n"
            f"3. Les fichiers mentionnés existent-ils réellement dans le projet ?\n"
            f"4. Le débat tourne-t-il en rond (répétitions entre tours) ?\n\n"
            f"VERDICT — Réponds par UN SEUL MOT en première ligne :\n"
            f"- PERTINENT : le débat avance bien, continuer\n"
            f"- REDIRECT : le débat dérive, suivi d'une consigne de recadrage\n"
            f"- ABORT : le débat est irrémédiablement hors-sujet, arrêter\n"
        )

    async def _evaluate_round(self, round_num: int) -> Dict[str, str]:
        """Évalue le tour via le président (architect). Retourne le verdict."""
        # Skip si trop tôt
        if round_num < MIN_ROUNDS_BEFORE_PRESIDENT:
            return {"verdict": "PERTINENT", "feedback": ""}

        # Skip si architect est participant (double rôle)
        if PRESIDENT_AGENT_NAME in self.participants:
            return {"verdict": "PERTINENT", "feedback": ""}

        # Skip si architect absent
        if PRESIDENT_AGENT_NAME not in self.agents:
            return {"verdict": "PERTINENT", "feedback": ""}

        try:
            architect = self.agents[PRESIDENT_AGENT_NAME]
            prompt = self._build_president_prompt(round_num)
            response = await architect.generate_content(prompt)
            result = _parse_president_verdict(response)

            # Publication événement
            await bus.publish("COUNCIL_PRESIDENT_VERDICT", {
                "council_id": self.council_id,
                "round": round_num,
                "verdict": result["verdict"],
                "feedback": result["feedback"],
            })

            logger.info(
                f"Council {self.council_id} Tour {round_num} — "
                f"Président: {result['verdict']}"
            )
            return result

        except Exception as e:
            logger.warning(f"Council {self.council_id} — Erreur président: {e}")
            return {"verdict": "PERTINENT", "feedback": ""}

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
        aborted = False
        abort_reason = ""
        president_feedback = ""

        for round_num in range(1, self.max_rounds + 1):
            rounds_used = round_num
            round_consensus_count = 0

            for participant in self.participants:
                agent = self.agents[participant]
                prompt = self._build_prompt(participant, round_num, president_feedback)

                # Appel via generate_content (pas process_task)
                content = await agent.generate_content(prompt)

                # Scorer l'argument
                arg_score = _score_argument(content)

                # Enregistrer dans le transcript
                entry = {
                    "agent": participant,
                    "round": round_num,
                    "content": content,
                    "score": arg_score["score"],
                    "confidence": arg_score["confidence"],
                    "breakdown": arg_score["breakdown"],
                    "timestamp": time.time()
                }
                self.transcript.append(entry)

                # Publication tour de parole (avec score)
                await bus.publish("COUNCIL_TURN", {
                    "council_id": self.council_id,
                    "agent": participant,
                    "round": round_num,
                    "max_rounds": self.max_rounds,
                    "content": content,
                    "score": arg_score["score"],
                    "confidence": arg_score["confidence"]
                })

                # Consensus ignoré avant MIN_ROUNDS_BEFORE_CONSENSUS
                if round_num >= MIN_ROUNDS_BEFORE_CONSENSUS and _is_consensus(content):
                    round_consensus_count += 1

            # --- Évaluation présidentielle ---
            verdict = await self._evaluate_round(round_num)
            if verdict["verdict"] == "ABORT":
                aborted = True
                abort_reason = verdict["feedback"]
                break
            elif verdict["verdict"] == "REDIRECT":
                president_feedback = verdict["feedback"]
            else:
                president_feedback = ""

            # Consensus = majorité qualifiée (>= 2/3 des participants)
            quorum = max(2, (len(self.participants) * 2 + 2) // 3)  # ceil(2/3)
            if round_consensus_count >= quorum:
                consensus_reached = True
                break

        # Résumé final
        if aborted:
            status = "aborted"
        elif consensus_reached:
            status = "consensus"
        else:
            status = "max_rounds"
        last_contributions = self.transcript[-len(self.participants):]
        final_summary = "\n".join(
            f"[{e['agent'].upper()}] {e['content'][:200]}" for e in last_contributions
        )
        if aborted:
            final_summary = f"[PRÉSIDENT — ABORT] {abort_reason}\n\n{final_summary}"

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

        # Métriques de scoring agrégées
        all_scores = [e.get("score", 0) for e in self.transcript if e.get("score") is not None]
        avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
        best_entry = max(self.transcript, key=lambda e: e.get("score", 0)) if self.transcript else {}

        result = {
            "status": status,
            "rounds_used": rounds_used,
            "transcript": self.transcript,
            "final_summary": final_summary,
            "scoring": {
                "avg_score": round(avg_score, 2),
                "best_agent": best_entry.get("agent", ""),
                "best_score": best_entry.get("score", 0),
            }
        }
        if abort_reason:
            result["abort_reason"] = abort_reason
        return result
