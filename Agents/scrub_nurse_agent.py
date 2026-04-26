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


# V32 (2026-04-26) — Mode TDD : la Nurse joue Product Owner.
NURSE_V32_TDD_PROMPT = """[ROLE: V32 PRODUCT OWNER — Decomposition TDD d'une User Story]

Tu reçois une User Story (entre ---USER_STORY---) et eventuellement des
hints de doctrine projet. Tu produis UNIQUEMENT du JSON valide selon
ce schema EXACT :

{
  "function_signature": "<signature Python complete avec types, ex: 'extract_markdown_blocks(text: str, language: str = \\"python\\") -> list[str]'>",
  "module_path": "<chemin relatif du fichier a creer, ex: 'core/utils/text_parser.py'>",
  "test_module_path": "<chemin relatif du test, ex: 'tests/auto/test_text_parser.py'>",
  "test_cases": [
    {"description": "happy path basique",
     "input_repr": "<repr Python des arguments, ex: 'text=\\\"```python\\\\nx=1\\\\n```\\\", language=\\\"python\\\"'>",
     "expected_repr": "<repr Python du resultat attendu, ex: '[\\\"x=1\\\"]'>"},
    {"description": "edge case empty",
     "input_repr": "...",
     "expected_repr": "..."}
  ],
  "edge_cases": ["empty string", "nested fences", "missing closing fence"],
  "doctrine_hints": ["return list not iterator", "preserve newlines inside blocks"],
  "forbidden_imports": ["bs4", "lxml"],
  "confidence": 0.85,
  "fallback": false
}

REGLES TDD STRICTES :
1. function_signature : Python valide avec annotations de types completes.
2. test_cases : EXIGE au moins 3 entrees (happy path + 1 edge case + 1 cas
   d'erreur). C'est le contrat que l'Architecte devra honorer.
3. test_cases utilise input_repr / expected_repr en STRING (le code de
   test fera eval() ou reproduira manuellement les valeurs).
4. confidence : 0.0 si User Story ambigue, 1.0 si parfaitement specifiable.
   Si confidence < 0.5, mets "fallback": true et "reason" pour expliquer.
5. forbidden_imports : libs externes que tu refuses au LLM (typiquement
   les libs lourdes quand stdlib suffit : re, ast, json, pathlib, etc.).
6. Aucune narration. Sortie = JSON UNIQUEMENT entre accolades.
7. Tout texte FRANCAIS strict avec accents (description, doctrine_hints).

Si la User Story est trop ambigue ou trop large pour une decomposition
claire :
{"fallback": true, "reason": "<explication breve en francais>"}

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


def _normalize_v32_decomposition(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """V32 — Normalise la decomposition TDD parsee.

    Garantit le schema attendu meme si la Nurse retourne du JSON
    incomplet. fallback=True si invariants critiques manquent.
    """
    if parsed.get("fallback") is True:
        return {"fallback": True, "reason": parsed.get("reason", "")}

    sig = parsed.get("function_signature", "")
    module = parsed.get("module_path", "")
    test_module = parsed.get("test_module_path", "")
    test_cases = parsed.get("test_cases") or []

    # Invariants critiques : signature + module + au moins 1 test_case
    if not isinstance(sig, str) or not sig.strip():
        return {"fallback": True, "reason": "function_signature manquante"}
    if not isinstance(module, str) or not module.endswith(".py"):
        return {"fallback": True, "reason": "module_path invalide"}
    if not isinstance(test_cases, list) or len(test_cases) < 1:
        return {"fallback": True, "reason": "test_cases vide"}

    # Inferer test_module_path si manquant
    if not isinstance(test_module, str) or not test_module:
        # core/utils/text_parser.py -> tests/auto/test_text_parser.py
        import os as _os
        basename = _os.path.basename(module).replace(".py", "")
        test_module = f"tests/auto/test_{basename}.py"

    # Filtrer les test_cases malformes
    clean_cases = []
    for tc in test_cases[:8]:  # max 8
        if not isinstance(tc, dict):
            continue
        desc = str(tc.get("description", ""))[:200]
        ipr = tc.get("input_repr", "")
        epr = tc.get("expected_repr", "")
        if isinstance(ipr, str) and isinstance(epr, str) and ipr and epr:
            clean_cases.append({
                "description": desc or "test_case",
                "input_repr": ipr[:500],
                "expected_repr": epr[:500],
            })

    if len(clean_cases) < 1:
        return {"fallback": True, "reason": "aucun test_case exploitable"}

    return {
        "function_signature": sig.strip()[:300],
        "module_path": module,
        "test_module_path": test_module,
        "test_cases": clean_cases,
        "edge_cases": [str(e)[:150] for e in (parsed.get("edge_cases") or [])[:5]],
        "doctrine_hints": [str(d)[:200] for d in (parsed.get("doctrine_hints") or [])[:5]],
        "forbidden_imports": [str(f)[:50] for f in (parsed.get("forbidden_imports") or [])[:8]],
        "confidence": float(parsed.get("confidence", 0.5)) if isinstance(parsed.get("confidence"), (int, float)) else 0.5,
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

    async def prepare_user_story_decomposition(
        self, user_story: str, project_doctrine_hints: str = ""
    ) -> Dict[str, Any]:
        """V32 (2026-04-26) — Decompose une User Story en spec TDD.

        Mode 'Product Owner' : la Nurse fixe la signature, les types,
        les test_cases AVANT que l'Architecte ne code. L'Architecte
        n'a plus qu'a resoudre l'equation imposee. Empeche le 14b de
        tricher en ecrivant des tests triviaux qui passent toujours.

        Returns dict format JSON strict :
          function_signature : str (ex: 'foo(x: int) -> str')
          module_path : str (ex: 'core/utils/text_parser.py')
          test_module_path : str (ex: 'tests/auto/test_text_parser.py')
          test_cases : list of {input, expected, description}
          edge_cases : list of str (situations a tester)
          doctrine_hints : list of str (contraintes projet)
          forbidden_imports : list of str (libs a eviter)
          confidence : float [0, 1]
          fallback : bool (True si decomposition impossible)
        """
        if not user_story or len(user_story.strip()) < 20:
            return {"fallback": True, "reason": "user_story trop courte"}

        prompt = self._build_v32_decomp_prompt(user_story, project_doctrine_hints)
        self.log_thought(
            f"V32 NURSE decompose User Story ({len(user_story)}c)",
            type="thought",
        )

        try:
            raw_output = await self.generate_content(prompt)
        except Exception as exc:
            logger.warning(f"[V32] Nurse decomposition crash: {exc}")
            return {"fallback": True, "reason": str(exc)[:200]}

        parsed = _safe_parse_checklist(raw_output or "")
        if parsed.get("fallback"):
            return {"fallback": True, "reason": "JSON illisible"}
        return _normalize_v32_decomposition(parsed)

    def _build_v32_decomp_prompt(
        self, user_story: str, project_doctrine_hints: str = ""
    ) -> str:
        parts = [
            NURSE_V32_TDD_PROMPT,
            "",
            "---USER_STORY---",
            user_story,
            "---/USER_STORY---",
        ]
        if project_doctrine_hints:
            parts.extend([
                "",
                "---DOCTRINE_PROJET---",
                project_doctrine_hints[:2000],
                "---/DOCTRINE_PROJET---",
            ])
        parts.extend([
            "",
            "Produis maintenant le JSON de decomposition TDD, RIEN d'autre.",
        ])
        return "\n".join(parts)

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
