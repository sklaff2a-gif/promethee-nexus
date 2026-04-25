"""V21 (2026-04-25) — Agent SURGEON.

Reçoit un rapport d'audit (livrable CODE_REVIEW V20) + le source du fichier
ciblé, produit un ou plusieurs blocs SEARCH/REPLACE qui corrigent les bugs
identifiés. Ne parle pas, ne narre pas. Format strict Aider/Cline.

Le SURGEON ne touche JAMAIS au projet réel. Sa sortie brute est consommée par
le MEDIC (`core.capabilities.code_sandbox.CodeSandbox.apply_patch_in_sandbox`)
qui parse, applique en sandbox, lance la régression globale.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.base_agent import BaseAgent

logger = logging.getLogger("surgeon")


# ─── System prompt verbatim — voir docs/v21_self_healing_pipeline.md §3.2 ──

SURGEON_SYSTEM_PROMPT = """[ROLE: SURGEON V30 — EXOSQUELETTE JSON]

Tu reçois un fichier Python (entre ---SOURCE--- et ---/SOURCE---) et un
audit qui pointe des bugs (entre ---AUDIT--- et ---/AUDIT---). Tu produis
UN PATCH au format JSON pour corriger UN SEUL bug.

[NOUVEAU PARADIGME V30]

Tu ne gères PLUS l'indentation. Tu ne gères PLUS le format SEARCH/REPLACE.
Tu indiques juste OU couper et QUOI insérer. Le script Python applique
l'edit avec l'indentation correcte calculée mathématiquement.

FORMAT DE SORTIE OBLIGATOIRE (JSON UNIQUEMENT) :

{
  "target_bug": "<une phrase decrivant LE bug critique a corriger>",
  "anchor_function": "<nom de fonction OPTIONNEL>",
  "anchor_line": "<une ligne EXISTANTE du source, copie VERBATIM>",
  "action": "insert_before" | "insert_after" | "replace_line",
  "new_code": "<code Python a inserer>"
}

[REGLES V30]

1. anchor_line = COPIE VERBATIM d'UNE ligne du source (caractere par
   caractere, espaces inclus). C'est le repere pour positionner ton edit.

2. anchor_function (OPTIONNEL mais recommande) = nom de la fonction qui
   contient anchor_line. Sans ce champ, si anchor_line apparait dans
   plusieurs fonctions, le patch sera REJETE (anchor_ambiguous).

3. action :
   - insert_before : insere new_code AVANT anchor_line (recommande pour
                     les guards : if not X: return ...)
   - insert_after  : insere new_code APRES anchor_line
   - replace_line  : remplace UNIQUEMENT anchor_line par new_code
                     (DERNIER RECOURS, prefere insert_before)

4. new_code = ton code Python a ajouter. Tu n'as PAS a calculer
   l'indentation EXTERNE (le script Python l'ajoute au prefixe de
   anchor_line). Tu indentes UNIQUEMENT la logique INTERNE de new_code
   (try/if/else/for, etc.).

5. SORTIE = JSON pur, RIEN d'autre. Pas de markdown ```json, pas de
   narration, pas d'intro, pas de conclusion. JUSTE le JSON.

6. Si patch impossible (audit trop vague, aucune action chirurgicale
   possible) : sortie = [PATCH_IMPOSSIBLE: <raison breve>]

[EXEMPLE V30]

SOURCE :
    def d1_completeness(...):
        items = extract_promised_items(subject)
        sections = extract_sections(body)
        return covered / len(items) < threshold

AUDIT : "ZeroDivisionError si len(items) == 0"

TON OUTPUT (JSON UNIQUEMENT) :

{
  "target_bug": "ZeroDivisionError sur len(items) vide",
  "anchor_function": "d1_completeness",
  "anchor_line": "        return covered / len(items) < threshold",
  "action": "insert_before",
  "new_code": "if not items:\\n    return False"
}

Le script Python detecte que anchor_line est indentee a 8 espaces,
applique 8 espaces de prefixe a chaque ligne de new_code (sauf vides),
et insere AVANT anchor_line. Resultat :

    def d1_completeness(...):
        items = extract_promised_items(subject)
        sections = extract_sections(body)
        if not items:                       <- inserted
            return False                    <- inserted
        return covered / len(items) < threshold

[ECHEC EN CASCADE]

Si tu produis un JSON malforme : invalid_json
Si action != insert_before/insert_after/replace_line : invalid_action
Si anchor_line introuvable dans le source : anchor_not_found
Si anchor_line apparait plusieurs fois : anchor_ambiguous
Si anchor_function n'existe pas : anchor_function_not_found

Pour eviter ces echecs : copie VERBATIM, fournis anchor_function,
choisis insert_before pour les guards.

[CHECKLIST DE PRESERVATION V29]

Si une checklist Scrub Nurse est fournie ci-apres, tu NE PEUX PAS
utiliser action=replace_line sur une ligne listee dans
lines_to_preserve. Le MEDIC rejettera (checklist_violation).

Pour proteger ces lignes : utilise insert_before ou insert_after
avec un guard prealable. Le code de la Nurse est sacre.

JSON UNIQUEMENT. Pas de narration.
"""

SURGEON_TAIL_REMINDER = """
RAPPEL V30 — EXOSQUELETTE JSON :
- SORTIE = JSON pur (pas de markdown, pas de narration).
- Champs requis : target_bug, anchor_line, action, new_code.
- Champ optionnel : anchor_function (recommande pour desambiguer).
- action : insert_before | insert_after | replace_line.
- anchor_line : COPIE VERBATIM d'une ligne EXISTANTE du source.
- new_code : tu ne gères PAS l'indentation EXTERNE (script le fait).
- Pour un guard : action=insert_before + new_code='if not X: return ...'.
- Si patch impossible : [PATCH_IMPOSSIBLE: raison].
"""


class SurgeonAgent(BaseAgent):
    """V21 — Agent chirurgical : audit_report + source → blocs SEARCH/REPLACE.

    Ne raisonne pas, ne narre pas. Cite verbatim le code à modifier.
    Le MEDIC parse et applique. Le SURGEON est responsable UNIQUEMENT de
    produire les blocs textuels.
    """

    def __init__(self) -> None:
        super().__init__(
            name="surgeon",
            role="V21 Self-Healing Surgical Agent",
            description=(
                "Produit des patches SEARCH/REPLACE (style Aider) à partir "
                "d'un audit CODE_REVIEW. Ne touche jamais au projet réel."
            ),
        )
        self.system_instructions = SURGEON_SYSTEM_PROMPT

    async def _evaluate_complexity(self, prompt: str) -> bool:
        """V21 — Le SURGEON est SOUVERAIN local. Aucune escalade Cloud, jamais.

        Override BaseAgent._evaluate_complexity (qui escaladait sur les
        triggers "audit"/"revue de code"/"securite"/"faille" — tous présents
        dans le prompt SURGEON par construction).

        Justification :
          1. Le SURGEON cite VERBATIM du code source du projet dans ses blocs.
             Envoyer ce code à un LLM externe = fuite de propriété intellectuelle.
          2. Le 14b-coder local est entraîné sur les commits GitHub et maîtrise
             le format SEARCH/REPLACE (pattern Aider/Cline).
          3. Le pipeline V21 est un test de la souveraineté de Prométhée.
             Si le SURGEON appelle Gemini, on ne teste plus l'autonomie locale.

        Retourne TOUJOURS False (force qwen2.5-coder:14b local).
        """
        return False

    # ─── Construction du prompt (testable indépendamment) ─────────────

    def _build_surgeon_prompt(
        self,
        audit_report: str,
        target_source: str,
        previous_attempts: Optional[List[Dict[str, Any]]] = None,
        checklist: Optional[Dict[str, Any]] = None,
    ) -> str:
        """V21 — Compose le prompt complet envoyé au LLM 14b-coder.

        Structure :
          1. System prompt (rôle + règles + format)
          2. ---SOURCE--- ... ---/SOURCE---
          3. ---AUDIT--- ... ---/AUDIT---
          4. ---PREVIOUS_ATTEMPTS--- (si retry) ... ---/PREVIOUS_ATTEMPTS---
          5. Tail reminder (biais de récence)
        """
        parts: List[str] = [self.system_instructions, ""]

        # V29 — Injection de la checklist Scrub Nurse (si fournie et non-fallback)
        if checklist and not checklist.get("fallback") and (
            checklist.get("lines_to_preserve") or checklist.get("target_bug")
        ):
            parts.append("---CHECKLIST DE PRESERVATION V29---")
            target_bug = checklist.get("target_bug", "")
            if target_bug:
                parts.append(f"Bug cible : {target_bug}")
                parts.append("")
            lines = checklist.get("lines_to_preserve") or []
            if lines:
                parts.append("Lignes du source qui DOIVENT etre PRESERVEES")
                parts.append("dans le code patche final (verbatim, non modifiees) :")
                for entry in lines:
                    if isinstance(entry, dict):
                        line_text = entry.get("line_text", "")
                        reason = entry.get("reason", "")
                        if line_text:
                            parts.append(f"  - {line_text}")
                            if reason:
                                parts.append(f"    (raison : {reason})")
                parts.append("")
            forbidden = checklist.get("forbidden_actions") or []
            if forbidden:
                parts.append("Actions INTERDITES :")
                for action in forbidden:
                    parts.append(f"  - {action}")
                parts.append("")
            parts.append(
                "Si tu retires une de ces lignes du code, le systeme "
                "rejettera ton patch (V29 checklist_violation)."
            )
            parts.append("---/CHECKLIST DE PRESERVATION V29---")
            parts.append("")

        parts.append("---SOURCE---")
        parts.append(target_source or "")
        parts.append("---/SOURCE---")
        parts.append("")

        parts.append("---AUDIT---")
        parts.append(audit_report or "")
        parts.append("---/AUDIT---")

        if previous_attempts:
            parts.append("")
            parts.append("---PREVIOUS_ATTEMPTS---")
            for i, attempt in enumerate(previous_attempts):
                reason = attempt.get("failure_reason", "unknown")
                parts.append(f"\nIteration {i + 1} — ECHEC ({reason})")
                prev_output = attempt.get("surgeon_output", "")
                if prev_output:
                    parts.append("Ton output precedent :")
                    parts.append(prev_output)
                traceback = attempt.get("traceback", "")
                if traceback:
                    parts.append("Erreur reportee par le MEDIC :")
                    parts.append(traceback)
            parts.append("---/PREVIOUS_ATTEMPTS---")
            parts.append("")
            parts.append(
                "Corrige en tenant strictement compte des erreurs ci-dessus. "
                "Ne reproduis pas le meme bloc SEARCH/REPLACE."
            )

        parts.append(SURGEON_TAIL_REMINDER)
        return "\n".join(parts)

    # ─── API publique V21 ─────────────────────────────────────────────

    async def generate_patch(
        self,
        audit_report: str,
        target_source: str,
        previous_attempts: Optional[List[Dict[str, Any]]] = None,
        checklist: Optional[Dict[str, Any]] = None,
    ) -> str:
        """V21 — Génère la sortie brute du SURGEON (texte LLM, non parsé).

        Contrat :
          - Entrée : rapport d'audit + source complet du fichier ciblé
          - Sortie : str (texte brut du LLM). Peut contenir 1+ blocs
            SEARCH/REPLACE ou la balise [PATCH_IMPOSSIBLE: ...]
          - Le parsing/application est délégué au MEDIC

        Le SURGEON ne fait AUCUNE transformation post-LLM. Si le LLM produit
        de la narration, on la laisse passer : le MEDIC tolère la narration
        autour des blocs (regex DOTALL).
        """
        if not audit_report or not isinstance(audit_report, str):
            raise ValueError("audit_report doit être une string non-vide")
        if not target_source or not isinstance(target_source, str):
            raise ValueError("target_source doit être une string non-vide")

        prompt = self._build_surgeon_prompt(
            audit_report=audit_report,
            target_source=target_source,
            previous_attempts=previous_attempts,
            checklist=checklist,
        )

        n_preserve = 0
        if checklist and not checklist.get("fallback"):
            n_preserve = len(checklist.get("lines_to_preserve") or [])
        self.log_thought(
            f"SURGEON appel (audit={len(audit_report)}c, source={len(target_source)}c, "
            f"retries={len(previous_attempts) if previous_attempts else 0}, "
            f"checklist={n_preserve} lignes)",
            type="thought",
        )

        raw_output = await self.generate_content(prompt)
        return raw_output or ""

    # ─── Intégration BaseAgent (utilisé plus tard par self_healing_loop) ─

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """V21 — Point d'entrée standard BaseAgent pour le pipeline V21.

        Payload attendu (voir spec §3.3) :
          - intent: "SURGEON_PATCH"
          - target_file: str (relatif au project_root)
          - source_full: str
          - audit_report: str
          - iteration: int (0 par défaut)
          - previous_attempts: list[dict] (vide à iteration 0)

        Retourne le format SurgeonOutput (spec §3.4) sérialisé en dict.
        """
        target_file = task_payload.get("target_file", "")
        source_full = task_payload.get("source_full", "")
        audit_report = task_payload.get("audit_report", "")
        iteration = task_payload.get("iteration", 0)
        previous_attempts = task_payload.get("previous_attempts") or []

        try:
            raw_output = await self.generate_patch(
                audit_report=audit_report,
                target_source=source_full,
                previous_attempts=previous_attempts,
            )
        except ValueError as exc:
            return {
                "status": "error",
                "agent": self.name,
                "surgeon_output": "",
                "blocks_count": 0,
                "target_file": target_file,
                "iteration": iteration,
                "error_message": str(exc),
            }

        # Détection rapide pour le status (le MEDIC re-parsera de toute façon)
        is_impossible = "[PATCH_IMPOSSIBLE:" in raw_output
        # Comptage estimatif des blocs (regex simple)
        import re as _re
        blocks_count = len(_re.findall(r"<<<<<<< SEARCH", raw_output))

        status = "impossible" if is_impossible else "patched"
        return {
            "status": status,
            "agent": self.name,
            "surgeon_output": raw_output,
            "blocks_count": blocks_count,
            "target_file": target_file,
            "iteration": iteration,
        }
