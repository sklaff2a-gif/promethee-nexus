"""V29 (2026-04-25) — Agent SCRUB NURSE.

Préparateur de checklist de préservation pour le SURGEON. Reçoit un
audit + un source, produit une checklist JSON listant les lignes à
préserver dans le source patché final (pour pallier le syndrome
d'oubli sémantique du qwen2.5-coder:14b).

Modèle : qwen3.5:9b vanilla (rapide, JSON-friendly, non fine-tuné).
Souverain local (override _evaluate_complexity → False).

Format de sortie : JSON strict
{
  "target_bug": "<une phrase>",
  "lines_to_preserve": [
    {"line_text": "    parts = text.split(\\"\\n---\\n\\", 1)",
     "reason": "definit parts utilisee dans return parts[1]"}
  ],
  "forbidden_actions": ["supprimer parts = ..."],
  "fallback": false
}
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

from core.base_agent import BaseAgent

logger = logging.getLogger("scrub_nurse")


SCRUB_NURSE_SYSTEM_PROMPT = """[ROLE: SCRUB NURSE — Préparation de checklist chirurgicale]

Tu reçois un audit (entre ---AUDIT---) et un source Python (entre ---SOURCE---).
Tu produis UNIQUEMENT du JSON valide selon ce schema EXACT :

{
  "target_bug": "<une phrase decrivant LE bug le plus critique de l'audit>",
  "lines_to_preserve": [
    {"line_text": "<ligne verbatim du source, indentation incluse>",
     "reason": "<pourquoi cette ligne doit etre preservee apres le patch>"}
  ],
  "forbidden_actions": ["<action interdite, ex: supprimer parts = ...>"],
  "fallback": false
}

REGLES :
1. Identifie LE bug le plus critique de l'audit (un seul, pas tous).
2. Identifie les lignes du SOURCE qui DEFINISSENT des variables/objets
   que le code patche utilisera. Ces lignes DOIVENT survivre au patch.
3. Cite les line_text VERBATIM caractere par caractere (espaces inclus).
4. Liste 1 a 5 lignes maximum dans lines_to_preserve. Sois concis.
5. forbidden_actions : liste 1 a 3 transformations a NE PAS faire.
6. Aucune narration. Sortie = JSON UNIQUEMENT entre accolades.

Si tu ne peux pas analyser (audit trop vague, source illisible) :
{"fallback": true}

Exemple de sortie attendue :
{
  "target_bug": "strip_header peut perdre la ligne parts = text.split() lors d'une insertion try/except",
  "lines_to_preserve": [
    {"line_text": "    parts = text.split(\\"\\n---\\n\\", 1)",
     "reason": "definit parts utilisee par parts[1] et len(parts)"}
  ],
  "forbidden_actions": [
    "supprimer la ligne parts = ...",
    "renommer la variable parts"
  ],
  "fallback": false
}

Aucune introduction, aucune conclusion, aucun ```json``` markdown.
"""


# Regex pour extraire le bloc JSON principal d'une sortie LLM (au cas
# où le 9b inclut du markdown ou des wrappers malgré la consigne).
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _safe_parse_checklist(raw_output: str) -> Dict[str, Any]:
    """V29 — Parse robust de la sortie Nurse.

    Tente plusieurs strategies, fallback silencieux si echec :
      1. json.loads direct (cas ideal : JSON pur)
      2. Extraction du premier {...} via regex puis json.loads
      3. Si tout echoue : retourne {"fallback": True}

    Returns:
        dict avec les champs target_bug, lines_to_preserve, etc.
        Toujours avec une clef "fallback" (False si parse OK, True si echec).
    """
    if not raw_output or not isinstance(raw_output, str):
        return {"fallback": True}

    text = raw_output.strip()

    # Strip markdown code fences si présents
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)

    # Tentative 1 : parse direct
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            parsed.setdefault("fallback", False)
            return parsed
    except json.JSONDecodeError:
        pass

    # Tentative 2 : extraction du premier bloc {...}
    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                parsed.setdefault("fallback", False)
                return parsed
        except json.JSONDecodeError:
            pass

    # Tentative 3 : fallback silencieux
    logger.warning(
        f"[V29] Nurse JSON illisible (preview: {text[:200]!r}), fallback active"
    )
    return {"fallback": True}


def _normalize_checklist(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """V29 — Normalise la checklist parsée pour garantir le schema attendu.

    Si la Nurse retourne du JSON valide mais incomplet, on remplit les
    champs manquants avec des valeurs par défaut sûres.
    """
    if parsed.get("fallback") is True:
        return {"fallback": True}

    target_bug = parsed.get("target_bug", "")
    if not isinstance(target_bug, str):
        target_bug = str(target_bug)

    lines = parsed.get("lines_to_preserve") or []
    if not isinstance(lines, list):
        lines = []
    # Filtrer les entrees malformees
    clean_lines = []
    for entry in lines[:5]:  # max 5 lignes
        if isinstance(entry, dict):
            line_text = entry.get("line_text", "")
            reason = entry.get("reason", "preservation requise")
            if isinstance(line_text, str) and line_text.strip():
                clean_lines.append({
                    "line_text": line_text,
                    "reason": str(reason)[:200],
                })

    forbidden = parsed.get("forbidden_actions") or []
    if not isinstance(forbidden, list):
        forbidden = []
    clean_forbidden = [str(f)[:200] for f in forbidden[:3] if f]

    return {
        "target_bug": target_bug[:300],
        "lines_to_preserve": clean_lines,
        "forbidden_actions": clean_forbidden,
        "fallback": False,
    }


class ScrubNurseAgent(BaseAgent):
    """V29 — Préparateur de checklist de préservation pour le SURGEON.

    Reçoit l'audit du REDUCE + le source du fichier cible. Produit une
    checklist JSON minimale qui dit au SURGEON quelles lignes doivent
    obligatoirement survivre au patch (pour pallier le syndrome d'oubli
    sémantique du 14b qui supprime parfois `parts = ...` en insérant
    `try: return parts[1]`).
    """

    def __init__(self) -> None:
        super().__init__(
            name="scrub_nurse",
            role="V29 Surgical Preparation Nurse",
            description=(
                "Prepare une checklist de lignes a preserver pour le SURGEON. "
                "Tourne en local sur qwen3.5:9b vanilla, sortie JSON stricte."
            ),
        )
        self.system_instructions = SCRUB_NURSE_SYSTEM_PROMPT

    async def _evaluate_complexity(self, prompt: str) -> bool:
        """V29 souverainete — La Nurse ne sort jamais en Cloud."""
        return False

    def _build_nurse_prompt(self, audit_report: str, target_source: str) -> str:
        parts = [
            self.system_instructions,
            "",
            "---SOURCE---",
            target_source or "",
            "---/SOURCE---",
            "",
            "---AUDIT---",
            audit_report or "",
            "---/AUDIT---",
            "",
            "Produis maintenant le JSON de la checklist, RIEN d'autre.",
        ]
        return "\n".join(parts)

    async def prepare_checklist(
        self, audit_report: str, target_source: str
    ) -> Dict[str, Any]:
        """V29 — Produit la checklist de préservation depuis audit + source.

        Returns:
            dict avec target_bug, lines_to_preserve, forbidden_actions,
            fallback. La clef fallback=True signifie que la Nurse n'a pas
            pu produire de checklist exploitable (le caller doit alors
            tourner en mode V21-V28 transparent sans checklist).
        """
        if not audit_report or not isinstance(audit_report, str):
            return {"fallback": True}
        if not target_source or not isinstance(target_source, str):
            return {"fallback": True}

        prompt = self._build_nurse_prompt(audit_report, target_source)

        self.log_thought(
            f"NURSE prepare checklist (audit={len(audit_report)}c, "
            f"source={len(target_source)}c)",
            type="thought",
        )

        try:
            raw_output = await self.generate_content(prompt)
        except Exception as exc:
            logger.warning(f"[V29] Nurse generate_content crash: {exc}")
            return {"fallback": True}

        parsed = _safe_parse_checklist(raw_output or "")
        normalized = _normalize_checklist(parsed)

        # Si parse OK mais aucune ligne à préserver : pas de fallback
        # mais checklist "vide" — le SURGEON tournera sans contrainte
        # de préservation, ce qui est ok.
        return normalized

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """V29 — Point d'entrée standard BaseAgent.

        Payload attendu :
          - audit_report: str
          - source_full: str
          - target_file: str (optionnel, pour observabilité)
        """
        audit_report = task_payload.get("audit_report", "")
        target_source = task_payload.get("source_full", "")
        target_file = task_payload.get("target_file", "")

        checklist = await self.prepare_checklist(
            audit_report=audit_report,
            target_source=target_source,
        )

        return {
            "status": "fallback" if checklist.get("fallback") else "success",
            "agent": self.name,
            "target_file": target_file,
            "checklist": checklist,
        }
