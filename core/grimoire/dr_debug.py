import re
import os
import logging
from core.base_agent import BaseAgent

logger = logging.getLogger("dr_debug")

# Répertoire racine du projet
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _extract_traceback_info(text: str) -> dict:
    """Extrait les informations d'un traceback Python depuis du texte brut."""
    info = {"found": False, "file": None, "line": None, "exception_type": None, "exception_msg": None, "frames": []}

    # Chercher un traceback complet
    tb_match = re.search(
        r'Traceback \(most recent call last\):\s*\n(.*?)(\w+Error|\w+Exception|\w+Warning):\s*(.*?)$',
        text, re.DOTALL | re.MULTILINE
    )
    if not tb_match:
        return info

    info["found"] = True
    frames_text = tb_match.group(1)
    info["exception_type"] = tb_match.group(2)
    info["exception_msg"] = tb_match.group(3).strip()

    # Extraire les frames (File "...", line X, in ...)
    frame_pattern = re.compile(r'File "([^"]+)", line (\d+)(?:, in (\w+))?')
    for match in frame_pattern.finditer(frames_text):
        frame = {"file": match.group(1), "line": int(match.group(2)), "function": match.group(3) or "?"}
        info["frames"].append(frame)

    # Le dernier frame est la source directe de l'erreur
    if info["frames"]:
        last = info["frames"][-1]
        info["file"] = last["file"]
        info["line"] = last["line"]

    return info


def _read_source_context(filepath: str, line_number: int, context_lines: int = 5) -> str:
    """Lit les lignes autour de l'erreur dans le fichier source."""
    # Chercher d'abord dans le projet
    if not os.path.isabs(filepath):
        filepath = os.path.join(_PROJECT_ROOT, filepath.replace("/", os.sep))

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        start = max(0, line_number - context_lines - 1)
        end = min(len(lines), line_number + context_lines)

        result = []
        for i in range(start, end):
            marker = " >>> " if i == line_number - 1 else "     "
            result.append(f"{marker}{i + 1:4d} | {lines[i].rstrip()}")
        return "\n".join(result)
    except Exception:
        return ""


class DrDebug(BaseAgent):
    def __init__(self):
        super().__init__(
            name="dr_debug",
            role="Diagnosticien Python",
            description="Diagnostic de bugs Python, lecture de tracebacks, patchs chirurgicaux"
        )

    async def process_task(self, task_payload):
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")
        full_text = f"{mission}\n{context}"

        # Pré-traitement : extraction de traceback
        tb_info = _extract_traceback_info(full_text)

        enrichment = ""
        if tb_info["found"]:
            enrichment = (
                f"\n\n--- ANALYSE AUTOMATIQUE DU TRACEBACK ---\n"
                f"Exception : {tb_info['exception_type']}: {tb_info['exception_msg']}\n"
                f"Fichier source : {tb_info['file']}\n"
                f"Ligne : {tb_info['line']}\n"
                f"Pile d'appels ({len(tb_info['frames'])} frames) :\n"
            )
            for frame in tb_info["frames"]:
                enrichment += f"  -> {frame['file']}:{frame['line']} in {frame['function']}\n"

            # Tenter de lire le code source autour de l'erreur
            if tb_info["file"] and tb_info["line"]:
                source_ctx = _read_source_context(tb_info["file"], tb_info["line"])
                if source_ctx:
                    enrichment += f"\nCode autour de l'erreur (ligne {tb_info['line']}) :\n{source_ctx}\n"

            enrichment += "--- FIN ANALYSE ---\n"

        prompt = (
            "Tu es un expert en debugging Python. "
            "Analyse le problème suivant, identifie la cause racine, "
            "et propose un patch précis.\n\n"
            f"{mission}"
            f"{enrichment}"
        )
        response = await self.generate_content(prompt)
        return {"status": "success", "result": response, "traceback_info": tb_info}
