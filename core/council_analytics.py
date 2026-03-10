"""
Council Analytics — Analyse deterministe des metriques systeme pour councils data-driven.

0 LLM. Lit les state files et produit des resumes chiffres pour alimenter
des debats Council sur donnees reelles avec verdicts applicables.
"""

import re
import logging

logger = logging.getLogger("CouncilAnalytics")

# Mapping pulsion → intent associe pour la recommandation
DRIVE_INTENT_MAP = {
    "CURIOSITE": "VEILLE_TECHNO",
    "CREATION": "EXPANSION_CODE",
    "MAITRISE": "EVOLUTION_PIPELINE",
    "CROISSANCE": "EXPANSION_CODE",
    "COMPREHENSION": "COUNCIL_DEBATE",
    "CONNEXION": "COUNCIL_DEBATE",
    "STABILITE": "AUDIT_STRUCTURE",
}

# Actions de verdict reconnues
VERDICT_ACTIONS = {"PRIORISER", "DEPRIORISER", "ABANDONNER", "MAINTENIR"}


# ---------------------------------------------------------------------------
# Analyseur 1 : Performance des routines
# ---------------------------------------------------------------------------

def analyze_routine_performance(routine_history: list) -> dict:
    """Agregge par intent : count, avg quality, success rate, failure types.

    Identifie le pire intent (quality < 0.4 + count >= 3) et le meilleur.
    """
    if not routine_history:
        return {"summary": "Aucune routine executee.", "worst_intent": None,
                "best_intent": None, "stats": {}}

    stats = {}
    for entry in routine_history:
        intent = entry.get("intent", "UNKNOWN")
        if intent not in stats:
            stats[intent] = {"count": 0, "total_quality": 0.0,
                             "successes": 0, "failures": 0, "failure_types": {}}
        s = stats[intent]
        s["count"] += 1
        s["total_quality"] += entry.get("quality_score", 0.0)
        if entry.get("status") == "success":
            s["successes"] += 1
        else:
            s["failures"] += 1
            ft = entry.get("failure_type", "unknown")
            s["failure_types"][ft] = s["failure_types"].get(ft, 0) + 1

    # Calculer moyennes
    for intent, s in stats.items():
        s["avg_quality"] = round(s["total_quality"] / s["count"], 2) if s["count"] > 0 else 0.0
        s["success_rate"] = round(s["successes"] / s["count"], 2) if s["count"] > 0 else 0.0

    # Identifier pire et meilleur intent
    worst_intent = None
    worst_quality = 1.0
    best_intent = None
    best_quality = 0.0

    for intent, s in stats.items():
        if s["count"] >= 3 and s["avg_quality"] < 0.4 and s["avg_quality"] < worst_quality:
            worst_quality = s["avg_quality"]
            worst_intent = intent
        if s["count"] >= 2 and s["avg_quality"] > best_quality:
            best_quality = s["avg_quality"]
            best_intent = intent

    # Construire le resume chiffre
    lines = []
    for intent, s in sorted(stats.items(), key=lambda x: x[1]["avg_quality"]):
        lines.append(
            f"- {intent}: {s['count']} exec, qualite moy {s['avg_quality']:.2f}, "
            f"succes {s['success_rate']:.0%}"
        )
    summary = "METRIQUES ROUTINES (dernieres 40):\n" + "\n".join(lines)
    if worst_intent:
        summary += f"\n⚠ PIRE: {worst_intent} (qualite {worst_quality:.2f})"
    if best_intent:
        summary += f"\n✓ MEILLEUR: {best_intent} (qualite {best_quality:.2f})"

    return {"summary": summary, "worst_intent": worst_intent,
            "best_intent": best_intent, "stats": stats}


# ---------------------------------------------------------------------------
# Analyseur 2 : Triage evolution (specs echouees/retentables)
# ---------------------------------------------------------------------------

def analyze_evolution_triage(catalog_specs: dict) -> dict:
    """Filtre specs failed ou available avec attempts > 0.

    Trie par proximite du succes (attempts restants, types de failure).
    catalog_specs: dict {spec_id: spec_object} avec attributs status, attempts, max_attempts, failure_reasons
    """
    candidates_abandon = []
    candidates_retry = []

    for spec_id, spec in catalog_specs.items():
        status = getattr(spec, "status", spec.get("status", "")) if isinstance(spec, dict) else spec.status
        attempts = getattr(spec, "attempts", spec.get("attempts", 0)) if isinstance(spec, dict) else spec.attempts
        max_attempts = getattr(spec, "max_attempts", spec.get("max_attempts", 3)) if isinstance(spec, dict) else spec.max_attempts
        failure_reasons = getattr(spec, "failure_reasons", spec.get("failure_reasons", [])) if isinstance(spec, dict) else spec.failure_reasons

        if status == "failed" or (status == "available" and attempts > 0):
            remaining = max_attempts - attempts
            entry = {
                "spec_id": spec_id,
                "status": status,
                "attempts": attempts,
                "remaining": remaining,
                "failure_reasons": failure_reasons[-3:] if failure_reasons else [],
            }
            if remaining <= 0 or status == "failed":
                candidates_abandon.append(entry)
            else:
                candidates_retry.append(entry)

    # Trier retry par proximite du succes (plus de tentatives restantes en premier)
    candidates_retry.sort(key=lambda x: x["remaining"], reverse=True)
    candidates_abandon.sort(key=lambda x: x["attempts"], reverse=True)

    lines = []
    if candidates_retry:
        lines.append(f"SPECS RETENTABLES ({len(candidates_retry)}):")
        for c in candidates_retry[:5]:
            reasons = ", ".join(c["failure_reasons"][-1:]) if c["failure_reasons"] else "aucune"
            lines.append(f"  - {c['spec_id']}: {c['attempts']} tentatives, "
                         f"{c['remaining']} restantes, dernier echec: {reasons}")
    if candidates_abandon:
        lines.append(f"SPECS A ABANDONNER ({len(candidates_abandon)}):")
        for c in candidates_abandon[:5]:
            lines.append(f"  - {c['spec_id']}: {c['attempts']} tentatives epuisees")

    summary = "\n".join(lines) if lines else "Aucune spec en echec ou retentable."

    return {"summary": summary, "candidates_abandon": candidates_abandon,
            "candidates_retry": candidates_retry}


# ---------------------------------------------------------------------------
# Analyseur 3 : Equilibre des pulsions
# ---------------------------------------------------------------------------

def analyze_drive_balance(drives_state: dict) -> dict:
    """Trouve les pulsions les plus privees (deprivation > 70).

    drives_state: dict {name: {"deprivation": float}} ou dict {name: Drive}
    """
    deprived = []
    for name, drive in drives_state.items():
        dep = drive.get("deprivation", 0.0) if isinstance(drive, dict) else getattr(drive, "deprivation", 0.0)
        deprived.append((name, dep))

    deprived.sort(key=lambda x: x[1], reverse=True)

    most_deprived = deprived[0][0] if deprived and deprived[0][1] > 70 else None
    suggested_intent = DRIVE_INTENT_MAP.get(most_deprived) if most_deprived else None

    lines = ["EQUILIBRE DES PULSIONS:"]
    for name, dep in deprived:
        marker = " ⚠" if dep > 70 else ""
        lines.append(f"  - {name}: deprivation {dep:.1f}/100{marker}")

    summary = "\n".join(lines)
    if most_deprived:
        summary += f"\n→ Pulsion la plus privee: {most_deprived} → intent suggere: {suggested_intent}"

    return {"summary": summary, "most_deprived": most_deprived,
            "suggested_intent": suggested_intent}


# ---------------------------------------------------------------------------
# Selecteur de sujet data-driven
# ---------------------------------------------------------------------------

# Ordre de rotation des topics data-driven
DATA_TOPICS = ["data_routine_audit", "data_evolution_triage", "data_drive_balance"]

def select_data_topic(routine_history: list, recent_subjects: list = None) -> dict | None:
    """Selectionne un topic data-driven par rotation, si donnees suffisantes.

    Retourne None si aucun topic disponible (fallback sur Priorite 2 existante).
    """
    recent = recent_subjects or []

    for topic_key in DATA_TOPICS:
        if topic_key in recent:
            continue

        if topic_key == "data_routine_audit":
            if len(routine_history) < 10:
                continue
            analysis = analyze_routine_performance(routine_history)
            if not analysis["worst_intent"] and not analysis["best_intent"]:
                continue
            # Construire question fermee
            question_parts = []
            if analysis["worst_intent"]:
                question_parts.append(
                    f"Faut-il DEPRIORISER {analysis['worst_intent']} "
                    f"(qualite {analysis['stats'][analysis['worst_intent']]['avg_quality']:.2f}) ?")
            if analysis["best_intent"]:
                question_parts.append(
                    f"Faut-il PRIORISER {analysis['best_intent']} "
                    f"(qualite {analysis['stats'][analysis['best_intent']]['avg_quality']:.2f}) ?")
            question = " ".join(question_parts)
            return {
                "participants": ["strategist", "coder", "architect"],
                "mission": _build_data_mission(analysis["summary"], question),
                "subject_key": topic_key,
                "needs_research": False,
                "research_query": None,
                "verdict_type": "routine_audit",
            }

        elif topic_key == "data_evolution_triage":
            try:
                from core.evolution_catalog import EvolutionCatalog
                catalog = EvolutionCatalog()
                analysis = analyze_evolution_triage(catalog.specs)
            except Exception:
                continue
            if not analysis["candidates_abandon"] and not analysis["candidates_retry"]:
                continue
            question_parts = []
            if analysis["candidates_abandon"]:
                ids = ", ".join(c["spec_id"] for c in analysis["candidates_abandon"][:3])
                question_parts.append(f"Faut-il ABANDONNER les specs {ids} ?")
            if analysis["candidates_retry"]:
                ids = ", ".join(c["spec_id"] for c in analysis["candidates_retry"][:3])
                question_parts.append(f"Faut-il retenter les specs {ids} ?")
            question = " ".join(question_parts)
            return {
                "participants": ["strategist", "evolution", "architect"],
                "mission": _build_data_mission(analysis["summary"], question),
                "subject_key": topic_key,
                "needs_research": False,
                "research_query": None,
                "verdict_type": "evolution_triage",
            }

        elif topic_key == "data_drive_balance":
            try:
                from core.desire_engine import desires
                analysis = analyze_drive_balance(desires.drives)
            except Exception:
                continue
            if not analysis["most_deprived"]:
                continue
            question = (
                f"La pulsion {analysis['most_deprived']} est en carence. "
                f"Faut-il PRIORISER {analysis['suggested_intent']} pour la satisfaire, "
                f"ou MAINTENIR l'equilibre actuel ?"
            )
            return {
                "participants": ["strategist", "coder", "researcher"],
                "mission": _build_data_mission(analysis["summary"], question),
                "subject_key": topic_key,
                "needs_research": False,
                "research_query": None,
                "verdict_type": "drive_balance",
            }

    return None


def _build_data_mission(summary: str, question: str) -> str:
    """Construit le prompt de mission data-driven avec guardrail verdict."""
    return (
        f"[DEBAT AUTONOME — DONNEES SYSTEME]\n\n"
        f"{summary}\n\n"
        f"QUESTION : {question}\n\n"
        f"IMPORTANT : Votre conclusion DOIT commencer par :\n"
        f"VERDICT: PRIORISER [intent] ou DEPRIORISER [intent] ou ABANDONNER [spec] ou MAINTENIR\n"
        f"suivi de votre justification (minimum 100 caracteres)."
    )


# ---------------------------------------------------------------------------
# Extracteur de verdict
# ---------------------------------------------------------------------------

def extract_verdict(transcript: list, verdict_type: str) -> dict | None:
    """Extrait un verdict structure du transcript Council.

    Cherche VERDICT: dans le texte du consensus (derniers tours, agents non-etudiant).
    Fallback heuristique si pas de format VERDICT explicite.
    """
    if not transcript:
        return None

    # Collecter le texte des derniers entries (non-etudiant, non-avocat)
    texts = []
    for entry in reversed(transcript):
        if entry.get("is_student") or entry.get("is_advocate"):
            continue
        content = entry.get("content", "")
        if content:
            texts.append(content)
        if len(texts) >= 6:
            break

    full_text = "\n".join(texts)
    if not full_text:
        return None

    # Recherche format explicite VERDICT:
    pattern = r"VERDICT\s*:\s*(PRIORISER|DEPRIORISER|ABANDONNER|MAINTENIR)(?:\s+(\S+))?"
    match = re.search(pattern, full_text, re.IGNORECASE)
    if match:
        action = match.group(1).upper()
        target = (match.group(2) or "").strip(".,;:!?")
        # Extraire la raison (texte apres le target)
        rest = full_text[match.end():].strip()
        reason = rest[:200].strip() if rest else "Verdict Council"
        # Nettoyer la raison
        reason = re.sub(r"^\s*[-—:]\s*", "", reason)
        if len(reason) < 10:
            reason = "Verdict Council"
        return {"action": action, "target": target, "reason": reason}

    # Fallback heuristique : chercher intent names + verbes d'action
    return _heuristic_verdict(full_text, verdict_type)


_THEME_INTENT_MAP = {
    "budget": "COUNCIL_DEBATE",
    "exploration": "ROADMAP_RESEARCH",
    "explorer": "ROADMAP_RESEARCH",
    "recherche": "ROADMAP_RESEARCH",
    "securite": "SECURITY_AUDIT",
    "sécurité": "SECURITY_AUDIT",
    "audit": "SECURITY_AUDIT",
    "stabilit": "AUDIT_STRUCTURE",
    "consolid": "MEMORY_CONSOLIDATION",
    "code": "EXPANSION_CODE",
    "expansion": "EXPANSION_CODE",
    "develop": "EXPANSION_CODE",
    "veille": "VEILLE_SILENCIEUSE",
    "memoire": "MEMORY_CONSOLIDATION",
    "mémoire": "MEMORY_CONSOLIDATION",
    "curiosit": "VEILLE_SILENCIEUSE",
    "isolement": "SOLILOQUE_INTERNE",
    "refactor": "REFACTOR_RANDOM",
    "grimoire": "GRIMOIRE_INVOKE",
    "roadmap": "ROADMAP_SPEC",
}


def _heuristic_verdict(text: str, verdict_type: str) -> dict | None:
    """Fallback heuristique pour extraire un verdict sans format explicite."""
    text_upper = text.upper()
    text_lower = text.lower()

    # Pass 1 : patterns directs "il faut prioriser X"
    for action in ("PRIORISER", "DEPRIORISER", "ABANDONNER"):
        pattern = rf"(?:FAUT|DEVONS|DEVRAIT|DOIT)\s+{action}\s+(\S+)"
        match = re.search(pattern, text_upper)
        if match:
            target = match.group(1).strip(".,;:!?")
            return {"action": action, "target": target,
                    "reason": "Verdict heuristique extrait du consensus"}

    # Pass 2 : verbes d'action + thèmes → mapping vers intents
    # Cherche le thème le PLUS PROCHE du verbe (pas le premier du dict)
    action_verbs = [
        ("reduire", "DEPRIORISER"), ("réduire", "DEPRIORISER"),
        ("diminuer", "DEPRIORISER"), ("limiter", "DEPRIORISER"),
        ("suspendre", "DEPRIORISER"), ("moins de", "DEPRIORISER"),
        ("augmenter", "PRIORISER"), ("renforcer", "PRIORISER"),
        ("concentrer", "PRIORISER"), ("prioriser", "PRIORISER"),
        ("privilegier", "PRIORISER"), ("privilégier", "PRIORISER"),
        ("plus de", "PRIORISER"), ("davantage de", "PRIORISER"),
    ]
    best_match = None  # (distance, verdict_action, intent, verb, theme)
    for verb, verdict_action in action_verbs:
        verb_pos = text_lower.find(verb)
        if verb_pos < 0:
            continue
        for theme, intent in _THEME_INTENT_MAP.items():
            theme_pos = text_lower.find(theme, max(0, verb_pos - 20))
            if theme_pos < 0:
                continue
            distance = abs(theme_pos - verb_pos)
            if distance <= 50:
                if best_match is None or distance < best_match[0]:
                    best_match = (distance, verdict_action, intent, verb, theme)

    if best_match:
        _, verdict_action, intent, verb, theme = best_match
        return {"action": verdict_action, "target": intent,
                "reason": f"Verdict heuristique : {verb} {theme}"}

    return None
