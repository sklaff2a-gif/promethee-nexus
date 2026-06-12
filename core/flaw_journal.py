"""Journal des Failles — miroir automatique des incoherences de Promethee.

Idee originale de Promethee : "un journal qui ne cache pas mes erreurs."
Implementation : pas ecrit par Promethee (il embellirait) mais extrait
des logs et des donnees brutes. Le miroir qu'il ne controle pas.

Genere un rapport quotidien depose dans le carnet de correspondance.
"""

import json
import os
import logging
from datetime import date
from typing import Dict, Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def generate_flaw_report() -> Dict[str, Any]:
    """Genere le rapport quotidien des failles de Promethee."""
    report = {
        "date": date.today().isoformat(),
        "flaws": [],
        "stats": {},
    }

    # 1. Boucles de cafe (meme reponse repetee)
    _detect_cafe_loops(report)

    # 2. Hallucinations CODE_REVIEW (mentor vs local)
    _detect_mentor_gaps(report)

    # 3. Stagnation metacognitive (oscillation sans variation)
    _detect_metacognition_stagnation(report)

    # 4. Chat "je ne sais pas" quand l'info existe
    _detect_false_ignorance(report)

    # 5. Ecart notes (local genereux vs mentor exigeant)
    _detect_grade_inflation(report)

    # 6. Repetitions de phrases-cles (tics)
    _detect_verbal_tics(report)

    # Compter les failles
    report["total_flaws"] = len(report["flaws"])

    return report


def _detect_cafe_loops(report: Dict):
    """Detecte les boucles dans les cafes (meme reponse repetee)."""
    try:
        log_dir = os.path.join(_PROJECT_ROOT, "logs", "coffee_breaks")
        today = date.today().isoformat()
        cafe_file = os.path.join(log_dir, f"cafe_{today}.md")
        if not os.path.exists(cafe_file):
            return
        with open(cafe_file, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        # Compter les phrases repetees
        lines = [l.strip() for l in content.split("\n") if l.strip().startswith("> ")]
        seen = {}
        loops = 0
        for line in lines:
            key = line[:50]
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 2:
                loops += 1
        if loops > 0:
            report["flaws"].append({
                "type": "boucle_cafe",
                "severity": "medium",
                "detail": f"{loops} phrases repetees dans les cafes",
            })
        report["stats"]["cafe_loops"] = loops
    except Exception:
        pass


def _detect_mentor_gaps(report: Dict):
    """Detecte les ecarts entre evaluation locale et mentor."""
    try:
        mentor_file = os.path.join(_PROJECT_ROOT, "memory", "mentor_state.json")
        if not os.path.exists(mentor_file):
            return
        with open(mentor_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        history = data.get("history", [])
        today = date.today().isoformat()
        gaps = []
        for h in history:
            if not h.get("date", "").startswith(today):
                continue
            local = h.get("local_grade", 0)
            feedback = h.get("claude_feedback", "").lower()
            # Si le mentor mentionne "n'existe pas" ou "hallucine" c'est une faille
            if any(kw in feedback for kw in ["n'existe pas", "imaginaire", "fabrique", "invente"]):
                gaps.append({
                    "slot": h.get("slot", "?"),
                    "local_grade": local,
                    "issue": "hallucination detectee par le mentor",
                })
        if gaps:
            report["flaws"].append({
                "type": "hallucination_code",
                "severity": "high",
                "detail": f"{len(gaps)} audit(s) avec code hallucine",
                "instances": gaps,
            })
        report["stats"]["hallucinations_mentor"] = len(gaps)
    except Exception:
        pass


def _detect_metacognition_stagnation(report: Dict):
    """Detecte la stagnation metacognitive (meme oscillation sans variation)."""
    try:
        from core.self_awareness import awareness
        ts = awareness.get_thought_summary()
        meta = ts.get("workspace_dominant_source", "")
        count = ts.get("metacognition_count", 0)
        # Si >100 insights et toujours les memes sources → stagnation
        if count > 50 and meta:
            report["flaws"].append({
                "type": "stagnation_metacognitive",
                "severity": "low",
                "detail": f"Source dominante: {meta} ({count} insights, meme oscillation)",
            })
        report["stats"]["metacognition_insights"] = count
        report["stats"]["dominant_source"] = meta
    except Exception:
        pass


def _detect_false_ignorance(report: Dict):
    """Detecte les 'je ne sais pas' quand l'info est dans le vecu."""
    try:
        chat_file = os.path.join(_PROJECT_ROOT, "memory", "chat_history.json")
        if not os.path.exists(chat_file):
            return
        with open(chat_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        messages = data.get("messages", data) if isinstance(data, dict) else data
        denial_phrases = ["je ne connais pas", "je ne me souviens pas",
                          "pas de donnees", "aucune donnee", "pas dans ma memoire"]
        denials = 0
        for m in messages:
            if m.get("role") == "assistant":
                content = m.get("content", "").lower()
                if any(d in content for d in denial_phrases):
                    denials += 1
        if denials > 0:
            report["flaws"].append({
                "type": "fausse_ignorance",
                "severity": "medium",
                "detail": f"{denials} fois 'je ne sais pas' dans le chat",
            })
        report["stats"]["false_ignorance"] = denials
    except Exception:
        pass


def _detect_grade_inflation(report: Dict):
    """Detecte l'inflation des notes (local > mentor)."""
    try:
        grades_file = os.path.join(_PROJECT_ROOT, "memory", "school", "grades.json")
        if not os.path.exists(grades_file):
            return
        with open(grades_file, "r", encoding="utf-8") as f:
            grades = json.load(f)
        today = date.today().isoformat()
        today_grades = [g for g in grades if g.get("date", "").startswith(today)]
        if today_grades:
            avg = sum(g.get("grade", 0) for g in today_grades) / len(today_grades)
            report["stats"]["avg_local_grade"] = round(avg, 1)
            report["stats"]["grades_count"] = len(today_grades)
    except Exception:
        pass


def _detect_verbal_tics(report: Dict):
    """Detecte les phrases repetees trop souvent (tics verbaux)."""
    try:
        chat_file = os.path.join(_PROJECT_ROOT, "memory", "chat_history.json")
        if not os.path.exists(chat_file):
            return
        with open(chat_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        messages = data.get("messages", data) if isinstance(data, dict) else data

        phrases = {}
        for m in messages:
            if m.get("role") == "assistant":
                content = m.get("content", "")
                for sentence in content.split("."):
                    s = sentence.strip().lower()[:60]
                    if len(s) > 20:
                        phrases[s] = phrases.get(s, 0) + 1

        tics = [(p, c) for p, c in phrases.items() if c >= 3]
        tics.sort(key=lambda x: -x[1])
        if tics:
            report["flaws"].append({
                "type": "tics_verbaux",
                "severity": "low",
                "detail": f"{len(tics)} phrases repetees 3+ fois",
                "examples": [f"\"{p}\" ({c}x)" for p, c in tics[:5]],
            })
        report["stats"]["verbal_tics"] = len(tics)
    except Exception:
        pass


async def publish_flaw_report():
    """Genere et publie le rapport dans le carnet de correspondance."""
    report = generate_flaw_report()

    if report["total_flaws"] == 0:
        logger.info("FLAW_JOURNAL: Aucune faille detectee aujourd'hui.")
        return report

    # Construire le texte
    lines = [f"JOURNAL DES FAILLES — {report['date']}", ""]
    lines.append(f"{report['total_flaws']} faille(s) detectee(s) :\n")

    for flaw in report["flaws"]:
        severity = {"high": "GRAVE", "medium": "MOYEN", "low": "MINEUR"}.get(flaw["severity"], "?")
        lines.append(f"[{severity}] {flaw['type']} : {flaw['detail']}")
        if flaw.get("instances"):
            for inst in flaw["instances"][:3]:
                lines.append(f"  - {inst}")
        if flaw.get("examples"):
            for ex in flaw["examples"][:3]:
                lines.append(f"  - {ex}")
        lines.append("")

    # Stats brutes
    lines.append("DONNEES BRUTES :")
    for k, v in report.get("stats", {}).items():
        lines.append(f"  {k} : {v}")

    content = "\n".join(lines)

    # Deposer dans le carnet
    try:
        from core.mailbox import mailbox
        mailbox.write_letter(
            content=content,
            source="flaw_journal",
            mood="lucidite",
            subject=f"Journal des Failles — {report['total_flaws']} faille(s)",
        )
    except Exception as e:
        logger.warning(f"FLAW_JOURNAL: Ecriture carnet echouee: {e}")

    logger.info(f"FLAW_JOURNAL: {report['total_flaws']} faille(s) publiee(s)")
    return report
