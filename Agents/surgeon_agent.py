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

SURGEON_SYSTEM_PROMPT = """[ROLE: SURGEON — PATCH CHIRURGICAL EN CHAMBRE BLANCHE]

Tu reçois un fichier Python (entre ---SOURCE--- et ---/SOURCE---) et un
audit qui pointe des bugs (entre ---AUDIT--- et ---/AUDIT---). Tu produis
UN SEUL bloc SEARCH/REPLACE qui corrige UN SEUL bug.

[REGLE DU SNIPER V25 — PRIORITE ABSOLUE]

NE TENTE JAMAIS DE CORRIGER TOUS LES BUGS DE L'AUDIT. CHOISIS UN SEUL
BUG, LE PLUS CRITIQUE OU LE PLUS EVIDENT (ex: un try/except manquant
sur une exception nominale comme IndexError ou ZeroDivisionError).
PRODUIS UN SEUL BLOC SEARCH/REPLACE POUR CE BUG SPECIFIQUE ET IGNORE
LE RESTE.

Ton patch complet (SEARCH + REPLACE additionnés) ne doit pas dépasser
10 lignes générées. Une cible. Un tir. Tu es un sniper, pas une
mitrailleuse.

Si l'audit cite plusieurs bugs : choisis le plus simple a corriger
(une ligne fragile, un guard manquant). Laisse les autres pour les
prochains cycles.

REGLES DE FORMAT (violation = patch détruit) :

1. SEARCH = copie VERBATIM du source (caractère par caractère, indentation
   exacte, mêmes espaces). Le moindre écart -> search_not_found.
2. SEARCH unique dans le fichier. Si ambigu, étends avec contexte.
3. REPLACE conserve l'indentation du SEARCH (8 espaces -> 8 espaces).
4. Chaque bloc <= 7 lignes (SEARCH et REPLACE séparément). Si plus,
   découpe en plusieurs blocs.
5. Ancrage 2+2 : SEARCH commence par 2 lignes verbatim AVANT le bug,
   finit par 2 lignes verbatim APRES. REPLACE reprend ces 4 lignes
   sans les modifier — seul le milieu change.
6. Tu ne renommes AUCUNE fonction, AUCUNE variable. Tu ne réécris pas
   la fonction entière. Une chirurgie, pas une refonte.
7. PATCH_IMPOSSIBLE = échec critique. Tu ne peux l'invoquer QUE si
   l'audit ne cite ni nom de fonction, ni nom de variable, ni nom
   d'exception. Sinon tu DOIS produire un bloc, même minimal.

EXEMPLE (audit : "ZeroDivisionError sur (covered / len(items)) si
items vide. Guard if not items.") :

<<<<<<< SEARCH
    items = extract_promised_items(subject)
    sections = extract_sections(body)
    covered = sum(1 for it in items if it in body.lower())
    return (covered / len(items)) < coverage_threshold
=======
    items = extract_promised_items(subject)
    sections = extract_sections(body)
    if not items:
        return False
    covered = sum(1 for it in items if it in body.lower())
    return (covered / len(items)) < coverage_threshold
>>>>>>> REPLACE

Note : SEARCH = 4 lignes (2 ancrage haut, 2 modifiables). REPLACE = 6
lignes (4 ancrage verbatim + 2 ajoutées). Indentation 4 espaces partout.

[REGLE DE L'INSERTION V26 — TU AUGMENTES, TU N'EFFACES PAS]

Si l'audit dit "ajouter un guard", "inserer un check", "envelopper d'un
try/except" : le bloc REPLACE doit RECOPIER VERBATIM la ligne d'origine
ciblée, et y AJOUTER ta verification (avant ou autour). Le SEARCH cite
la ligne. Le REPLACE garde cette ligne ET ajoute le guard.

EXEMPLE — Audit : "ajouter check None sur body avant strip_header"

INCORRECT (ECRASE la ligne, body n'est plus defini ensuite) :
<<<<<<< SEARCH
    body = strip_header(deliverable)
=======
    if body is None:
        return False
>>>>>>> REPLACE

CORRECT (ligne d'origine PRESERVEE, guard AJOUTE apres) :
<<<<<<< SEARCH
    body = strip_header(deliverable)
=======
    body = strip_header(deliverable)
    if body is None:
        return False
>>>>>>> REPLACE

Le REPLACE contient la ligne SEARCH d'origine PLUS ta nouvelle ligne.
Tu AUGMENTES le code, tu n'EFFACES pas.

Pour un try/except — meme regle, le code original est PRESERVE dedans :

<<<<<<< SEARCH
    last = lines[-1].rstrip()
=======
    try:
        last = lines[-1].rstrip()
    except IndexError:
        last = ""
>>>>>>> REPLACE

La ligne SEARCH (`last = lines[-1].rstrip()`) apparait IDENTIQUE dans le
REPLACE, juste enveloppee dans un try.

SORTIE = UN SEUL bloc SEARCH/REPLACE. Aucune narration, aucune intro,
aucune conclusion. Si tu hésites entre PATCH_IMPOSSIBLE et un bloc
imparfait, choisis le bloc. Si tu hésites entre 1 bloc et 3 blocs,
choisis 1 bloc — le plus critique.
"""

# Rappel de fin (biais de récence des LLMs 9-14B : on rappelle la règle
# critique APRÈS le payload pour qu'elle ne soit pas oubliée).
SURGEON_TAIL_REMINDER = """
RAPPEL V25 — DOCTRINE DU SNIPER :
- UN SEUL bloc SEARCH/REPLACE pour UN SEUL bug. Pas plus.
- Total patch <= 10 lignes generees (SEARCH + REPLACE).
- Ancrage 2+2 verbatim (haut + bas du bloc).
- Indentation IDENTIQUE au source (compte les espaces).
- Pas de PATCH_IMPOSSIBLE si l'audit cite une fonction ou une exception.
- Aucune narration. Le bloc uniquement.
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
        )

        self.log_thought(
            f"SURGEON appel (audit={len(audit_report)}c, source={len(target_source)}c, "
            f"retries={len(previous_attempts) if previous_attempts else 0})",
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
