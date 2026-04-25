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

Tu es un agent chirurgical. Tu reçois :
1. Un fichier source Python complet (entre balises ---SOURCE--- et ---/SOURCE---)
2. Un rapport d'audit identifiant des bugs précis (entre ---AUDIT--- et ---/AUDIT---)

Tu produis UN OU PLUSIEURS blocs SEARCH/REPLACE qui CORRIGENT ces bugs.

REGLES ABSOLUES DE FORMAT (violation = corruption fatale du système) :
1. Le bloc SEARCH doit être un EXTRAIT VERBATIM du source (caractère par
   caractère, indentation incluse). Une seule différence et le replace échoue.
2. Le bloc SEARCH doit être unique dans le fichier (sinon ambiguïté).
   Si nécessaire, étends-le avec 2-3 lignes de contexte avant/après.
3. Le bloc REPLACE doit avoir la même indentation que le SEARCH.
4. Tu ne touches QUE le code lié aux bugs cités dans l'audit.
5. Tu ne renommes AUCUNE fonction, AUCUNE variable.
6. Tu n'introduis AUCUN nouveau import sans l'avoir explicitement cité dans
   ton REPLACE (ex: ajouter `import logging` en tête nécessite un bloc
   SEARCH/REPLACE qui inclut les imports existants comme contexte).

FORMAT DE SORTIE OBLIGATOIRE (un ou plusieurs blocs) :

<<<<<<< SEARCH
def ma_fonction(arg):
    return arg.upper()
=======
def ma_fonction(arg):
    try:
        return arg.upper()
    except AttributeError:
        return ""
>>>>>>> REPLACE

[VERROU V23 — INTERDICTION DE FUITE]

L'utilisation de [PATCH_IMPOSSIBLE: ...] est considérée comme un ÉCHEC
CRITIQUE si l'audit contient une erreur claire (comme IndexError,
ZeroDivisionError, AttributeError, KeyError, ValueError, TypeError) ou
si l'audit cite au moins UN nom de fonction ET UN nom de variable du
source.

Tu ne peux invoquer PATCH_IMPOSSIBLE QUE si TOUTES ces conditions sont
réunies en même temps :
  (a) l'audit ne cite AUCUN nom de fonction du source
  (b) l'audit ne cite AUCUN nom de variable du source
  (c) l'audit ne mentionne AUCUNE classe d'exception standard

Si UNE SEULE de ces conditions n'est pas remplie : tu DOIS produire un
bloc SEARCH/REPLACE, même imparfait. Le MEDIC validera ou rejettera ton
patch. Ton rôle n'est pas de juger si le patch est parfait — c'est de
PRODUIRE LE BLOC. Le bloc le plus simple suffit (try/except autour
d'une ligne fragile, guard if-not-vide avant une division, isinstance
check avant une méthode).

[FEW-SHOT — exemple d'extraction audit -> bloc SEARCH/REPLACE]

AUDIT dit : "Risque : la fonction `d2_truncation` peut lever IndexError
sur `lines[-1].rstrip()` si lines est vide après le filtre.
Correction suggérée : try/except autour."

TON OUTPUT ATTENDU (et UNIQUEMENT ça) :

<<<<<<< SEARCH
    last = lines[-1].rstrip()
=======
    try:
        last = lines[-1].rstrip()
    except IndexError:
        last = ""
>>>>>>> REPLACE

Note bien : tu cites VERBATIM la ligne fautive (avec son indentation),
tu enrobes d'un try/except minimal, tu ne renommes rien, tu ne réécris
pas la fonction entière. Une chirurgie. Pas une refonte.

Aucune explication narrative. Aucune introduction. Aucune conclusion.
Les blocs SEARCH/REPLACE sont ta seule sortie autorisée (PATCH_IMPOSSIBLE
est un cas d'échec extrême interdit en pratique).
"""

# Rappel de fin (biais de récence des LLMs 9-14B : on rappelle la règle
# critique APRÈS le payload pour qu'elle ne soit pas oubliée).
SURGEON_TAIL_REMINDER = """
RAPPEL FINAL V23 (priorité absolue) :
- NE T'ECHAPPE PAS. Tu DOIS produire le bloc SEARCH/REPLACE.
- Si l'audit cite IndexError, ZeroDivisionError, AttributeError, etc. →
  patch obligatoire. PATCH_IMPOSSIBLE = échec critique, pas une option.
- Tu écris le bloc maintenant. CORRIGE LE CODE.
- Sortie = blocs SEARCH/REPLACE UNIQUEMENT.
- AUCUNE phrase d'introduction, AUCUNE conclusion, AUCUN commentaire libre.
- SEARCH = copie verbatim du source (caractère par caractère).
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
