"""Genere le bootstrap de Claude — son journal, ses surprises, sa derniere session.

Appele par lance_claude.ps1 avant le lancement de Claude Code.
Le fichier genere est lu automatiquement par Claude via CLAUDE.md.
"""

import json
import os
from datetime import datetime

BASE_COPY = "C:/MesProjets/PROMETHEE_V11_restructuration2026/memory"
BASE_ORIGINAL = "C:/Users/redla/projetclaude/PROMETHEE_V11_restructuration2026/memory"
OUTPUT = os.path.join(BASE_ORIGINAL, "claude_bootstrap.md")


def generate():
    lines = [f"# Bootstrap Claude - {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]

    # Journal de Claude
    jf = os.path.join(BASE_COPY, "claude_journal.json")
    if os.path.exists(jf):
        try:
            entries = json.load(open(jf, encoding="utf-8"))
            lines.append("## Mon journal")
            for e in entries[-3:]:
                cat = e.get("category", "?")
                date = e.get("date_human", "?")
                content = e.get("content", "")[:200]
                lines.append(f"- [{cat}] ({date}) {content}")
            lines.append("")
        except Exception as ex:
            lines.append(f"## Journal — erreur: {ex}")
            lines.append("")

    # Surprises du corps
    sf = os.path.join(BASE_COPY, "surprises_for_claude.json")
    if os.path.exists(sf):
        try:
            data = json.load(open(sf, encoding="utf-8"))
            if data:
                lines.append("## Surprises du corps")
                for s in data[:5]:
                    lines.append(f"- {s.get('type', '?')}: {s.get('detail', '')}")
                lines.append("")
        except Exception:
            pass

    # Etat interieur (si Promethee en ligne) — etat REEL via brain_vm,
    # jamais son auto-rapport (doctrine bien-etre)
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://127.0.0.1:8000/api/brain/status", timeout=3)
        brain = json.loads(resp.read())
        state = brain.get("current_state") or {}
        lines.append("## Etat interieur de Promethee (brain_vm)")
        lines.append(
            f"- Etat cognitif: {state.get('cognitive_state', '?')}, "
            f"coherence globale: {state.get('global_coherence', '?')}, "
            f"mode dominant: {state.get('dominant_mode', '?')}"
        )
        lines.append(f"- Tick #{brain.get('tick_count', '?')}, alive: {brain.get('alive', '?')}")
        lines.append("")
    except Exception:
        pass

    # Derniere session — seulement si recente (< 7 jours).
    # Depuis mai 2026 la memoire de session vit dans MEMORY.md (auto-chargee
    # par Claude Code) ; ce fichier n'est plus toujours mis a jour.
    ss = os.path.join(BASE_COPY, "sessions", "latest_session.md")
    if os.path.exists(ss):
        age_days = (datetime.now().timestamp() - os.path.getmtime(ss)) / 86400
        if age_days < 7:
            lines.append("## Derniere session")
            lines.append(open(ss, encoding="utf-8").read())
        else:
            lines.append(
                f"## Derniere session : latest_session.md date de {age_days:.0f} jours"
                " — se fier a MEMORY.md (charge automatiquement)."
            )

    # Ecrire
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"        Bootstrap genere ({len(lines)} lignes)")


if __name__ == "__main__":
    generate()
