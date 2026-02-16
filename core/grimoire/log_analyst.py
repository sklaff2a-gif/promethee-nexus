import re
import logging
from collections import Counter
from core.base_agent import BaseAgent

logger = logging.getLogger("log_analyst")

# Patterns de sévérité standard
_SEVERITY_PATTERNS = {
    "CRITICAL": re.compile(r'\b(CRITICAL|FATAL)\b', re.IGNORECASE),
    "ERROR": re.compile(r'\bERROR\b', re.IGNORECASE),
    "WARNING": re.compile(r'\b(WARNING|WARN)\b', re.IGNORECASE),
    "INFO": re.compile(r'\bINFO\b', re.IGNORECASE),
    "DEBUG": re.compile(r'\bDEBUG\b', re.IGNORECASE),
}

# Pattern pour extraire le message principal d'une ligne de log
_LOG_MSG_PATTERN = re.compile(
    r'(?:CRITICAL|FATAL|ERROR|WARNING|WARN|INFO|DEBUG)\s*[:\]]\s*(.*)',
    re.IGNORECASE
)


def _parse_log_lines(text: str) -> dict:
    """Parse les lignes de log et retourne un résumé statistique."""
    lines = text.strip().splitlines()
    severity_counts = {"CRITICAL": 0, "ERROR": 0, "WARNING": 0, "INFO": 0, "DEBUG": 0}
    messages = Counter()
    total_lines = len(lines)
    classified = 0

    for line in lines:
        for severity, pattern in _SEVERITY_PATTERNS.items():
            if pattern.search(line):
                severity_counts[severity] += 1
                classified += 1
                # Extraire le message pour détecter les patterns récurrents
                msg_match = _LOG_MSG_PATTERN.search(line)
                if msg_match:
                    msg = msg_match.group(1).strip()[:100]  # Tronquer pour regrouper
                    if msg:
                        messages[msg] += 1
                break  # Une seule sévérité par ligne

    # Patterns récurrents (message apparaissant > 2 fois)
    recurring = {msg: count for msg, count in messages.most_common(10) if count > 2}

    return {
        "total_lines": total_lines,
        "classified_lines": classified,
        "severity_counts": severity_counts,
        "recurring_patterns": recurring,
    }


class LogAnalyst(BaseAgent):
    def __init__(self):
        super().__init__(
            name="log_analyst",
            role="Analyste de logs",
            description="Identifie des patterns d'erreur, diagnostique des incidents, propose des règles d'alerte"
        )

    async def process_task(self, task_payload):
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")
        full_text = f"{mission}\n{context}"

        # Pré-traitement : analyse statistique des logs
        log_stats = _parse_log_lines(full_text)

        enrichment = ""
        if log_stats["classified_lines"] > 0:
            sc = log_stats["severity_counts"]
            enrichment = (
                f"\n\n--- ANALYSE AUTOMATIQUE DES LOGS ---\n"
                f"Lignes totales : {log_stats['total_lines']}\n"
                f"Lignes classifiées : {log_stats['classified_lines']}\n"
                f"Répartition : CRITICAL={sc['CRITICAL']}, ERROR={sc['ERROR']}, "
                f"WARNING={sc['WARNING']}, INFO={sc['INFO']}, DEBUG={sc['DEBUG']}\n"
            )
            if log_stats["recurring_patterns"]:
                enrichment += "Patterns récurrents (> 2 occurrences) :\n"
                for msg, count in log_stats["recurring_patterns"].items():
                    enrichment += f"  [{count}x] {msg}\n"
            enrichment += "--- FIN ANALYSE ---\n"

        prompt = (
            "Tu es un analyste de logs applicatifs expert. "
            "Identifie les patterns récurrents, classifie par sévérité, "
            "diagnostique les incidents et propose des règles d'alerte.\n\n"
            f"{mission}"
            f"{enrichment}"
        )
        response = await self.generate_content(prompt)
        return {"status": "success", "result": response, "log_stats": log_stats}
