"""
Talk Logger - Archivage persistant des conversations agents.
S'abonne au bus d'événements et écrit chaque échange dans un fichier quotidien.
Dossier : logs/talks/  |  Format : talk_YYYY-MM-DD.txt
"""
import os
import logging
from datetime import datetime

from core.event_bus.bus import bus

logger = logging.getLogger("TalkLogger")

# Dossier d'archivage (relatif à la racine du projet)
TALKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "talks")

# Événements à archiver et leur formateur
TRACKED_EVENTS = {
    "USER_COMMAND",
    "AGENT_RESPONSE",
    "AGENT_STREAM",
    "AGENT_TASK_DISPATCH",
    "THOUGHT_STREAM",
    "COUNCIL_START",
    "COUNCIL_TURN",
    "COUNCIL_END",
    "ARTIFACT_CREATED",
    "CI_PIPELINE_START",
    "CI_PIPELINE_STEP",
    "CI_PIPELINE_RESULT",
}


def _get_today_file() -> str:
    """Retourne le chemin du fichier d'archivage du jour."""
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(TALKS_DIR, f"talk_{today}.txt")


def _format_entry(event_type: str, data: dict) -> str:
    """Formate une entrée de log lisible selon le type d'événement."""
    ts = datetime.now().strftime("%H:%M:%S")
    agent = data.get("agent", "").upper()

    if event_type == "USER_COMMAND":
        mission = data.get("mission", "")
        return f"[{ts}] USER > {mission}"

    elif event_type == "AGENT_RESPONSE":
        content = data.get("content", "")
        return f"[{ts}] {agent} > {content}\n[{ts}] --- FIN {agent} ---"

    elif event_type == "AGENT_STREAM":
        # On archive uniquement le signal de fin (texte complet reconstruit côté frontend)
        # et le signal de début pour le contexte
        status = data.get("status", "")
        if status == "start":
            return f"[{ts}] {agent} [STREAM START]"
        elif status == "end":
            return f"[{ts}] {agent} [STREAM END]"
        else:
            return ""  # Chunks intermédiaires ignorés dans l'archive

    elif event_type == "AGENT_TASK_DISPATCH":
        target = data.get("target", "").upper()
        return f"[{ts}] ROUTER > Mission envoyée vers {target}"

    elif event_type == "THOUGHT_STREAM":
        content = data.get("content", "")
        thought_type = data.get("type", "info")
        return f"[{ts}] {agent} ({thought_type}) > {content}"

    elif event_type == "COUNCIL_START":
        participants = ", ".join(p.upper() for p in data.get("participants", []))
        mission = data.get("mission", "")
        max_rounds = data.get("max_rounds", "?")
        return f"[{ts}] CONSEIL OUVERT [{participants}] - \"{mission}\" (max {max_rounds} tours)"

    elif event_type == "COUNCIL_TURN":
        round_n = data.get("round", "?")
        max_r = data.get("max_rounds", "?")
        content = data.get("content", "")
        return f"[{ts}] {agent} [CONSEIL Tour {round_n}/{max_r}] > {content}"

    elif event_type == "COUNCIL_END":
        status = data.get("status", "")
        rounds = data.get("rounds_used", "?")
        verdict = "CONSENSUS" if status == "consensus" else "LIMITE DE TOURS"
        return f"[{ts}] CONSEIL FERMÉ [{verdict}] après {rounds} tour(s)"

    elif event_type == "ARTIFACT_CREATED":
        filename = data.get("filename", "?")
        filepath = data.get("filepath", "?")
        return f"[{ts}] FACTORY > Fichier créé : {filename} ({filepath})"

    elif event_type == "CI_PIPELINE_START":
        filename = data.get("filename", "?")
        return f"[{ts}] CI/CD > Pipeline démarré pour : {filename}"

    elif event_type == "CI_PIPELINE_STEP":
        filename = data.get("filename", "?")
        step = data.get("step", "?")
        status = data.get("status", "?").upper()
        detail = data.get("detail", "")
        return f"[{ts}] CI/CD > [{step}] {status} - {filename} : {detail}"

    elif event_type == "CI_PIPELINE_RESULT":
        filename = data.get("filename", "?")
        success = data.get("success", False)
        detail = data.get("detail", "")
        verdict = "SUCCÈS" if success else "ÉCHEC"
        return f"[{ts}] CI/CD > RÉSULTAT [{verdict}] - {filename} : {detail}"

    return f"[{ts}] {event_type} > {data}"


def _write_to_file(text: str):
    """Écrit une entrée dans le fichier du jour (append + flush immédiat)."""
    if not text:
        return
    try:
        filepath = _get_today_file()
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(text + "\n")
            f.flush()
    except Exception as e:
        logger.error(f"Erreur écriture talk_logger : {e}")


def _on_bus_event(event: dict):
    """Callback wildcard du bus. Filtre et archive les événements pertinents."""
    event_type = event.get("type", "")
    data = event.get("data", {})

    if event_type not in TRACKED_EVENTS:
        return

    entry = _format_entry(event_type, data)
    _write_to_file(entry)


def start(talks_dir: str = None):
    """Initialise le talk_logger : crée le dossier et s'abonne au bus."""
    target_dir = talks_dir or TALKS_DIR
    os.makedirs(target_dir, exist_ok=True)
    bus.subscribe("*", _on_bus_event)
    logger.info(f"Talk Logger activé -> {target_dir}")


def stop():
    """Désabonne le talk_logger du bus."""
    bus.unsubscribe("*", _on_bus_event)
