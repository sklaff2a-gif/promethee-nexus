#!/usr/bin/env python
"""claude_chat.py — outil commun des skills Claude pour dialoguer avec Promethee.

Remplace le boilerplate duplique dans 5 skills (exercise, read-story,
research-loop, self-improve, cours-soutien). Patterns durcis par l'usage
(sessions du 11/06/2026) :

- Detection des reponses par TIMESTAMP, jamais par comptage : la compaction
  V26 du chat fait DESCENDRE le nombre de messages de l'historique en pleine
  session (un `count > count_avant` ne devient jamais vrai -> reponse perdue).
- Un timeout HTTP du POST /api/chat ne signifie PAS un echec : le message
  est generalement recu et traite quand meme. On n'abandonne qu'apres avoir
  relu l'historique.
- Attente de STABILITE du fichier (8s sans ecriture) avant de lire : laisse
  finir les auto-actions (!run, !ancre...) et la boucle agentique.
- Series de messages : espacer d'au moins 25s (sinon Promethee saute des
  messages — cause du 0/10 sur l'exercice 1.3 du cours de maths).

Usage :
  python tools/claude_chat.py send "message"
      Envoie sans attendre. Affiche l'epoch d'envoi (pour wait --since).

  python tools/claude_chat.py ask "message" [--timeout 300]
      Envoie PUIS attend la/les reponses posterieures a l'envoi.

  python tools/claude_chat.py wait --since EPOCH [--timeout 300]
      Attend les reponses posterieures a EPOCH.

  python tools/claude_chat.py read [--last N]
      Lit les N derniers messages assistant (defaut 3), sans attendre.

Sortie : texte des reponses sur stdout, separees par '--- REPONSE ---'.
Les auto-actions sont prefixees [AUTO-ACTION]. Exit 0 si au moins une
reponse, 2 si timeout sans reponse.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

CHAT_API = os.environ.get("PROMETHEE_CHAT_API", "http://127.0.0.1:8000/api/chat")
HISTORY_PATH = os.environ.get(
    "PROMETHEE_CHAT_HISTORY",
    "C:/MesProjets/PROMETHEE_V11_restructuration2026/memory/chat_history.json",
)
STABILITY_S = 8     # silence d'ecriture requis avant de lire (auto-actions)
POLL_S = 6


def load_messages():
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("messages", data.get("history", data)) if isinstance(data, dict) else data
    except Exception:
        return []


def send(message: str) -> float:
    """POST /api/chat. Retourne l'epoch d'envoi. Le timeout client est tolere."""
    t_send = time.time()
    payload = json.dumps({"message": message}).encode("utf-8")
    req = urllib.request.Request(CHAT_API, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=120)
    except Exception as e:
        # Le message est generalement recu malgre le timeout — on ne le renvoie PAS.
        print(f"[claude_chat] timeout/erreur client ({e}) — le message est "
              f"probablement recu, verification via l'historique", file=sys.stderr)
    return t_send


def wait_after(t_since: float, timeout: int):
    """Reponses assistant avec timestamp > t_since, apres stabilite du fichier."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(POLL_S)
        msgs = load_messages()
        news = [m for m in msgs
                if m.get("role") == "assistant"
                and float(m.get("timestamp", 0) or 0) > t_since]
        if news:
            try:
                mtime = os.path.getmtime(HISTORY_PATH)
            except OSError:
                continue
            if time.time() - mtime > STABILITY_S:
                return news
    return []


def fmt(messages):
    parts = []
    for m in messages:
        content = m.get("content", "")
        badge = m.get("badge", "")
        prefix = "[AUTO-ACTION] " if badge == "auto_action" else (
            "[CONTINUATION] " if badge == "agentic_continuation" else "")
        parts.append(prefix + content)
    return "\n--- REPONSE ---\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_send = sub.add_parser("send", help="envoyer sans attendre")
    p_send.add_argument("message")

    p_ask = sub.add_parser("ask", help="envoyer et attendre la reponse")
    p_ask.add_argument("message")
    p_ask.add_argument("--timeout", type=int, default=300)

    p_wait = sub.add_parser("wait", help="attendre les reponses apres --since")
    p_wait.add_argument("--since", type=float, required=True)
    p_wait.add_argument("--timeout", type=int, default=300)

    p_read = sub.add_parser("read", help="lire les derniers messages assistant")
    p_read.add_argument("--last", type=int, default=3)

    args = parser.parse_args()

    if args.cmd == "send":
        t = send(args.message)
        print(f"ENVOYE since={t:.2f}")
        return 0

    if args.cmd == "ask":
        t = send(args.message)
        news = wait_after(t, args.timeout)
        if not news:
            print("[claude_chat] TIMEOUT : aucune reponse posterieure a l'envoi "
                  "(verifier 'read --last 3' avant de renvoyer)", file=sys.stderr)
            return 2
        print(fmt(news))
        return 0

    if args.cmd == "wait":
        news = wait_after(args.since, args.timeout)
        if not news:
            print("[claude_chat] TIMEOUT", file=sys.stderr)
            return 2
        print(fmt(news))
        return 0

    if args.cmd == "read":
        msgs = [m for m in load_messages() if m.get("role") == "assistant"]
        if not msgs:
            print("[claude_chat] historique vide ou illisible", file=sys.stderr)
            return 2
        print(fmt(msgs[-args.last:]))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
