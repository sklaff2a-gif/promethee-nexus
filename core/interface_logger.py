"""
Interface Logger - Archivage de ce qui s'affiche dans l'interface web.
Reproduit fidèlement les deux panneaux de nexus.js :
  [DIALOGUE] = panneau de conversation (addDialogue)
  [LOG]      = panneau de logs système (addLog)

S'abonne au bus d'événements et écrit dans un fichier quotidien.
Dossier : logs/interface/  |  Format : interface_YYYY-MM-DD.txt
"""
import os
import time
import logging
from datetime import datetime

from core.event_bus.bus import bus

logger = logging.getLogger("InterfaceLogger")

INTERFACE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "interface"
)

# Anti-doublon streaming : agents ayant terminé un stream récemment (TTL 3s)
_recently_streamed: dict[str, float] = {}
_STREAM_TTL = 3.0

# Accumulation des chunks de streaming (clé = stream_id)
_active_streams: dict[str, dict] = {}

TRACKED_EVENTS = {
    "USER_COMMAND",
    "AGENT_TASK_DISPATCH",
    "THOUGHT_STREAM",
    "AGENT_STREAM",
    "AGENT_RESPONSE",
    "ARTIFACT_CREATED",
    "COUNCIL_START",
    "COUNCIL_TURN",
    "COUNCIL_END",
    "COUNCIL_PRESIDENT_VERDICT",
    "CI_PIPELINE_START",
    "CI_PIPELINE_STEP",
    "CI_PIPELINE_RESULT",
}


def _get_today_file() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(INTERFACE_DIR, f"interface_{today}.txt")


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _is_recently_streamed(agent: str) -> bool:
    """Vérifie si l'agent a récemment terminé un stream (anti-doublon)."""
    expire = _recently_streamed.get(agent)
    if expire and time.time() < expire:
        return True
    _recently_streamed.pop(agent, None)
    return False


def _write(line: str):
    """Écrit une ligne dans le fichier du jour."""
    if not line:
        return
    try:
        filepath = _get_today_file()
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
    except Exception as e:
        logger.error(f"Erreur écriture interface_logger : {e}")


def _format_event(event_type: str, data: dict):
    """Formate un événement en lignes DIALOGUE et/ou LOG (miroir de nexus.js)."""
    ts = _ts()
    agent = (data.get("agent") or "").upper()

    # --- USER_COMMAND ---
    if event_type == "USER_COMMAND":
        mission = data.get("mission", "")
        _write(f"[{ts}] [DIALOGUE] [COMMANDER] {mission}")

    # --- AGENT_TASK_DISPATCH ---
    elif event_type == "AGENT_TASK_DISPATCH":
        target = (data.get("target") or "").upper()
        _write(f"[{ts}] [LOG:sys] [ROUTER] Mission envoyée vers -> {target}")

    # --- THOUGHT_STREAM ---
    elif event_type == "THOUGHT_STREAM":
        content = data.get("content", "")
        thought_type = data.get("type", "info")
        if thought_type == "success":
            _write(f"[{ts}] [DIALOGUE] [{agent}] {content}")
        elif thought_type == "error":
            _write(f"[{ts}] [LOG:err] [{agent}] {content}")
        else:
            _write(f"[{ts}] [LOG:info] [{agent}] {content}")

    # --- AGENT_STREAM ---
    elif event_type == "AGENT_STREAM":
        sid = data.get("stream_id", "")
        if data.get("status") == "start":
            _active_streams[sid] = {"agent": agent, "chunks": []}
        elif data.get("done"):
            stream = _active_streams.pop(sid, None)
            if stream:
                full_text = "".join(stream["chunks"])
                _write(f"[{ts}] [DIALOGUE] [{stream['agent']}] {full_text}")
            # Marquer comme récemment streamé
            _recently_streamed[agent.lower()] = time.time() + _STREAM_TTL
        elif data.get("chunk") and sid in _active_streams:
            _active_streams[sid]["chunks"].append(data["chunk"])

    # --- AGENT_RESPONSE ---
    elif event_type == "AGENT_RESPONSE":
        # Anti-doublon : si l'agent vient de streamer, ne pas re-loguer le dialogue
        if not _is_recently_streamed(agent.lower()):
            content = data.get("content", "")
            _write(f"[{ts}] [DIALOGUE] [{agent}] {content}")
        _write(f"[{ts}] [LOG:success] [{agent}] Tâche terminée avec succès")

    # --- ARTIFACT_CREATED ---
    elif event_type == "ARTIFACT_CREATED":
        filename = data.get("filename", "?")
        filepath = data.get("filepath", "?")
        _write(f"[{ts}] [DIALOGUE] [SYSTEM] NOUVEAU FICHIER CREE : {filename}")
        _write(f"[{ts}] [LOG:success] [FACTORY] Ecriture disque: {filepath}")

    # --- COUNCIL_START ---
    elif event_type == "COUNCIL_START":
        agents = ", ".join(p.upper() for p in data.get("participants", []))
        mission = data.get("mission", "")
        max_rounds = data.get("max_rounds", "?")
        _write(f'[{ts}] [DIALOGUE] [SYSTEM] CONSEIL OUVERT [{agents}] - "{mission}" (max {max_rounds} tours)')

    # --- COUNCIL_TURN ---
    elif event_type == "COUNCIL_TURN":
        round_n = data.get("round", "?")
        max_r = data.get("max_rounds", "?")
        content = data.get("content", "")
        if not _is_recently_streamed(agent.lower()):
            _write(f"[{ts}] [DIALOGUE] [{agent}] [CONSEIL Tour {round_n}/{max_r}] {content}")
        _write(f"[{ts}] [LOG:info] [{agent}] Conseil T{round_n}: contribution envoyée")

    # --- COUNCIL_PRESIDENT_VERDICT ---
    elif event_type == "COUNCIL_PRESIDENT_VERDICT":
        round_n = data.get("round", "?")
        pres_verdict = data.get("verdict", "?")
        feedback = data.get("feedback", "")
        icon = {"PERTINENT": "\u2705", "REDIRECT": "\u21a9\ufe0f", "ABORT": "\U0001f6d1"}.get(pres_verdict, "?")
        line = f"[{ts}] [LOG:info] [PRESIDENT] Tour {round_n}: {icon} {pres_verdict}"
        if pres_verdict in ("REDIRECT", "ABORT") and feedback:
            line += f" — {feedback[:150]}"
        _write(line)

    # --- COUNCIL_END ---
    elif event_type == "COUNCIL_END":
        status = data.get("status", "")
        if status == "consensus":
            verdict = "CONSENSUS ATTEINT"
        elif status == "aborted":
            verdict = "ABORT PAR LE PRÉSIDENT"
        else:
            verdict = "LIMITE DE TOURS"
        rounds = data.get("rounds_used", "?")
        _write(f"[{ts}] [DIALOGUE] [SYSTEM] CONSEIL FERME [{verdict}] après {rounds} tour(s).")

    # --- CI_PIPELINE_START ---
    elif event_type == "CI_PIPELINE_START":
        filename = data.get("filename", "?")
        _write(f"[{ts}] [DIALOGUE] [SYSTEM] CI/CD Pipeline démarré pour : {filename}")
        _write(f"[{ts}] [LOG:sys] [CI/CD] Pipeline démarré : {filename}")

    # --- CI_PIPELINE_STEP ---
    elif event_type == "CI_PIPELINE_STEP":
        step = data.get("step", "?")
        status = (data.get("status") or "?").upper()
        filename = data.get("filename", "?")
        detail = data.get("detail", "")
        level = "err" if status == "ERROR" else "success" if status == "SUCCESS" else "sys"
        _write(f"[{ts}] [LOG:{level}] [CI/CD] [{step}] {status} - {filename} : {detail}")

    # --- CI_PIPELINE_RESULT ---
    elif event_type == "CI_PIPELINE_RESULT":
        filename = data.get("filename", "?")
        success = data.get("success", False)
        detail = data.get("detail", "")
        verdict = "DEPLOYE" if success else "ROLLBACK"
        level = "success" if success else "err"
        _write(f"[{ts}] [DIALOGUE] [SYSTEM] CI/CD [{verdict}] {filename} : {detail}")
        _write(f"[{ts}] [LOG:{level}] [CI/CD] RESULTAT [{verdict}] {filename}")


def _on_bus_event(event: dict):
    """Callback wildcard du bus."""
    event_type = event.get("type", "")
    data = event.get("data", {})
    if event_type not in TRACKED_EVENTS:
        return
    _format_event(event_type, data)


def start(interface_dir: str = None):
    """Initialise l'interface logger."""
    target_dir = interface_dir or INTERFACE_DIR
    os.makedirs(target_dir, exist_ok=True)
    bus.subscribe("*", _on_bus_event)
    logger.info(f"Interface Logger activé -> {target_dir}")


def stop():
    """Désabonne l'interface logger du bus."""
    bus.unsubscribe("*", _on_bus_event)
    _active_streams.clear()
    _recently_streamed.clear()
