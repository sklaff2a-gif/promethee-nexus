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

[REGLES DE TAILLE ET D'ANCRAGE V24 — MICRO-SCALPEL]

REGLE 7 (Micro-Scalpel) : Un bloc SEARCH/REPLACE ne doit JAMAIS exceder
7 lignes (SEARCH ET REPLACE pris separement). Si tu dois modifier 10
lignes, decoupe en 2 blocs de 5 lignes ou 3 blocs de 3-4 lignes. Les LLMs
14B perdent le fil des indentations Python au-dela de 7 lignes.

REGLE 8 (Ancrage Prefix/Suffix) : Le bloc SEARCH doit OBLIGATOIREMENT
contenir EXACTEMENT 2 lignes de contexte intactes AVANT ta modification,
et 2 lignes de contexte intactes APRES. Ces 4 lignes (2 avant + 2 apres)
doivent etre copiees VERBATIM depuis le source (espaces, indentation,
retours chariots inclus). Le bloc REPLACE doit reprendre ces memes 4
lignes de contexte sans les modifier — seules les lignes du milieu
changent. Si l'ancrage ne matche pas au caractere pres, ton patch sera
detruit par search_not_found.

REGLE 9 (Indentation Python) : L'indentation Python est vitale. Si le
SEARCH commence par 8 espaces, le REPLACE doit commencer par 8 espaces.
Pas de tabulations melangees aux espaces. Le moindre ecart d'indentation
casse le fichier patche.

FORMAT DE SORTIE OBLIGATOIRE (un ou plusieurs blocs) :

<<<<<<< SEARCH
    if not lines:
        return False
    last_line = lines[-1].rstrip()
    # Ignorer barre horizontale finale
    if re.match(r"^[-*=_]{3,}\s*$", last_line) and len(lines) >= 2:
=======
    if not lines:
        return False
    try:
        last_line = lines[-1].rstrip()
    except IndexError:
        return False
    # Ignorer barre horizontale finale
    if re.match(r"^[-*=_]{3,}\s*$", last_line) and len(lines) >= 2:
>>>>>>> REPLACE

Note sur l'exemple : 5 lignes de SEARCH (2 ancrage avant + 1 modifiee +
2 ancrage apres). REPLACE etend a 7 lignes pour le try/except. Les 2
premieres lignes ET les 2 dernieres sont VERBATIM (zero changement).
Seule la ligne du milieu est transformee.

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

[FEW-SHOT V24 — extraction audit -> bloc avec ANCRAGE 2+2]

AUDIT dit : "Risque : la fonction `d1_completeness` peut lever
ZeroDivisionError sur `(covered / len(items))` si items est vide.
Correction suggérée : guard if not items avant la division."

SOURCE pertinent (extrait du fichier) :
```
    items = extract_promised_items(subject)
    sections = extract_sections(body)
    covered = sum(1 for it in items if it in body.lower())
    return (covered / len(items)) < coverage_threshold
```

TON OUTPUT ATTENDU (et UNIQUEMENT ca) :

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

Decompose ton geste :
  - Lignes 1-2 du SEARCH (items=, sections=) : ANCRAGE AVANT (verbatim)
  - Lignes 3-4 du SEARCH (covered=, return) : zone modifiable
  - Le REPLACE reprend les memes 2 lignes d'ancrage en tete, INCHANGEES
  - Le REPLACE ajoute le guard `if not items: return False` au milieu
  - Le REPLACE reprend les 2 dernieres lignes (covered=, return) VERBATIM
  - Total : SEARCH=4 lignes, REPLACE=6 lignes -> respecte la limite 7
  - Indentation : tout est aligne a 4 espaces, identique au source

Une chirurgie. Pas une refonte. Pas de signature reecrite. Pas de
docstring touchee. Juste le bug, avec son ancrage.

Aucune explication narrative. Aucune introduction. Aucune conclusion.
Les blocs SEARCH/REPLACE sont ta seule sortie autorisée (PATCH_IMPOSSIBLE
est un cas d'échec extrême interdit en pratique).
"""

# Rappel de fin (biais de récence des LLMs 9-14B : on rappelle la règle
# critique APRÈS le payload pour qu'elle ne soit pas oubliée).
SURGEON_TAIL_REMINDER = """
RAPPEL FINAL V24 (priorité absolue) :

[ECHAPPEMENT INTERDIT]
- NE T'ECHAPPE PAS. Tu DOIS produire le bloc SEARCH/REPLACE.
- Si l'audit cite IndexError, ZeroDivisionError, AttributeError, etc.,
  patch obligatoire. PATCH_IMPOSSIBLE = echec critique, pas une option.

[MICRO-SCALPEL — chaque bloc est CHIRURGICAL]
- 1 bloc = 7 lignes maximum (SEARCH ET REPLACE chacun).
- 10 lignes a modifier ? Decoupe en 2-3 blocs distincts.
- N'ecris JAMAIS une fonction entiere. Cible la ligne fautive + 2 lignes
  de contexte avant + 2 lignes de contexte apres.

[ANCRAGE 2+2 — verbatim ou rien]
- Le SEARCH commence par 2 lignes du source COPIEES VERBATIM (pas
  modifiees). Idem pour les 2 dernieres lignes du SEARCH.
- Le REPLACE reprend ces 4 lignes d'ancrage SANS LES MODIFIER. Seul le
  milieu change.
- Si tu changes une lettre dans l'ancrage, search_not_found.

[INDENTATION PYTHON]
- Compte les espaces du source. Reproduis-les a l'identique.
- Pas de tab. Pas de mix tab/espace.
- Si SEARCH commence par 8 espaces, REPLACE commence par 8 espaces.

[FORMAT FINAL]
- Sortie = blocs SEARCH/REPLACE UNIQUEMENT, chacun <= 7 lignes.
- AUCUNE phrase d'introduction, AUCUNE conclusion, AUCUN commentaire.
- Tu ecris les blocs maintenant. CORRIGE LE CODE.
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
