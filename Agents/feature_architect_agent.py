"""V32 (2026-04-26) — Agent FEATURE ARCHITECT (distinct du DivineArchitect).

L'Architecte construit, le Chirurgien repare. Cet agent prend une
User Story decomposee par la ScrubNurse V32 (signature + test_cases)
et produit en UNE seule reponse JSON multi-fichiers :
  - le code du module a creer (core/utils/...)
  - le fichier de tests associes (tests/auto/...)

Format de sortie JSON V32 (cf. core/capabilities/code_sandbox.py
parse_v30_patch / _v32_validate_file_entry) :

  {
    "feature_name": "extract_markdown_blocks",
    "files": [
      {"target_file": "core/utils/text_parser.py",
       "action": "create_file",
       "new_code": "<code Python complet>"},
      {"target_file": "tests/auto/test_text_parser.py",
       "action": "create_file",
       "new_code": "<code de test Python complet>"}
    ]
  }

Modele : qwen2.5-coder:14b LOCAL (override _evaluate_complexity -> False).
Heritage : SurgeonAgent (pour reuser la plomberie generate_content).
Le prompt systeme FEATURE_ARCHITECT_SYSTEM_PROMPT est expansif (SOLID,
DRY, typage strict) au lieu de paranoiaque (preservation V30).

Distinction avec DivineArchitect (Agents/architect_agent.py) :
  - DivineArchitect = valideur de securite, autorise/rejette des
    deploiements de code existant.
  - FeatureArchitectAgent = constructeur, ecrit du code from-scratch
    a partir d'une User Story decomposee.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from Agents.surgeon_agent import SurgeonAgent

logger = logging.getLogger("feature_architect")


FEATURE_ARCHITECT_SYSTEM_PROMPT = """[ROLE: V32 FEATURE ARCHITECT — Construction logicielle from-scratch]

Tu reçois une SPECIFICATION TDD (entre ---SPEC---) decomposee par la
ScrubNurse V32 (signature, types, test_cases imposes). Tu dois produire
en UNE seule reponse :
  1. Le code du module qui IMPLEMENTE la signature.
  2. Le fichier de tests qui FAIT PASSER les test_cases imposes.

[FORMAT DE SORTIE OBLIGATOIRE]

Tu produis UNIQUEMENT du JSON valide selon ce schema EXACT :

{
  "feature_name": "<nom court de la feature>",
  "files": [
    {
      "target_file": "<module_path de la SPEC>",
      "action": "create_file",
      "new_code": "<code Python complet du module>"
    },
    {
      "target_file": "<test_module_path de la SPEC>",
      "action": "create_file",
      "new_code": "<code Python complet du test>"
    }
  ]
}

[REGLES ARCHITECTURALES STRICTES]

1. SIGNATURE : Tu DOIS implementer EXACTEMENT la function_signature de
   la SPEC. Pas de variation. Pas d'argument supplementaire. Les
   types doivent matcher.

2. TESTS : Tu DOIS faire passer les test_cases EXACTS de la SPEC. Si
   un test_case dit input_repr et expected_repr, ton test doit
   reproduire EXACTEMENT cette assertion.

3. TDD : Tu n'inventes pas de tests triviaux. Tu reproduis CHAQUE
   test_case de la SPEC dans le fichier de test, plus eventuellement
   1-2 tests d'edge_cases listes dans la SPEC.

4. SOLID + DRY : Single Responsibility, pas de duplication. Si
   plusieurs fonctions partagent une logique, factorise.

5. TYPAGE STRICT : Toutes les annotations de la signature.
   `from __future__ import annotations` au top si besoin.

6. STDLIB FIRST : re, ast, json, pathlib, typing avant les libs
   externes. Respecte les forbidden_imports de la SPEC.

7. LOCALISATION FR : Tous les commentaires, docstrings, messages
   d'erreur DOIVENT etre en francais avec accents corrects.
   Ex: raise ValueError("Liste vide") — pas "Empty list".

8. CODE COMPLET : Pas de "..." ou "# TODO". Le module doit etre
   pret a importer et tourner. Le test doit etre pret a faire pytest.

9. PAS DE NARRATION : Sortie = JSON pur entre accolades. Pas de
   markdown ```json, pas d'introduction, pas d'explication.

[ECHECS POSSIBLES]

Si la SPEC est inexploitable (signature absurde, test_cases
contradictoires) : sortie = [FEATURE_IMPOSSIBLE: <raison breve>]

JSON UNIQUEMENT. Pas de narration.
"""


FEATURE_ARCHITECT_TAIL_REMINDER = """
RAPPEL V32 — JSON multi-fichiers OBLIGATOIRE :
- Champ requis : feature_name, files (tableau).
- Chaque files[] : target_file, action='create_file', new_code.
- new_code : code Python complet (pas de ..., pas de TODO).
- Signatures et test_cases : MATCH EXACT avec la SPEC.
- Localisation FR stricte (messages d'erreur, commentaires).
- AUCUNE narration. JSON pur entre accolades.
"""


class FeatureArchitectAgent(SurgeonAgent):
    """V32 — Architecte logiciel : User Story decomposee -> code + tests.

    Herite de SurgeonAgent pour la plomberie (generate_content,
    log_thought, override _evaluate_complexity local). Le prompt
    systeme est totalement different : le Chirurgien preserve,
    l'Architecte construit.
    """

    def __init__(self) -> None:
        # Bypass SurgeonAgent.__init__ qui set system_instructions au
        # prompt SURGEON. On set notre prompt apres.
        from core.base_agent import BaseAgent
        BaseAgent.__init__(
            self,
            name="feature_architect",
            role="V32 Software Architect",
            description=(
                "Construit du code from-scratch a partir d'une User Story "
                "decomposee TDD. Output : JSON multi-fichiers (code + tests). "
                "Local sur qwen2.5-coder:14b, jamais Cloud."
            ),
        )
        self.system_instructions = FEATURE_ARCHITECT_SYSTEM_PROMPT

    def _build_architect_prompt(
        self,
        decomposition: Dict[str, Any],
        rag_context: str = "",
    ) -> str:
        """Compose le prompt complet : SPEC TDD + RAG cross-file."""
        import json as _json
        spec_json = _json.dumps(decomposition, ensure_ascii=False, indent=2)
        parts = [
            self.system_instructions,
            "",
            "---SPEC---",
            spec_json,
            "---/SPEC---",
        ]
        if rag_context:
            parts.extend(["", rag_context])
        parts.extend(["", FEATURE_ARCHITECT_TAIL_REMINDER])
        return "\n".join(parts)

    async def generate_feature(
        self,
        decomposition: Dict[str, Any],
        rag_context: str = "",
    ) -> str:
        """V32 — Genere le JSON multi-fichiers a partir d'une decomposition.

        Args:
            decomposition: dict produit par
              ScrubNurseAgent.prepare_user_story_decomposition.
            rag_context: chaine RAG V31 optionnelle (cross-file + wisdom).

        Returns:
            sortie brute du LLM (JSON V32 attendu, parse cote MEDIC via
            parse_v30_patch).

        Raises :
            ValueError si la decomposition est fallback / vide / invalide.
        """
        if not decomposition or decomposition.get("fallback"):
            raise ValueError(
                "FeatureArchitectAgent.generate_feature : decomposition "
                "fallback ou vide, impossible de generer"
            )
        if not decomposition.get("function_signature"):
            raise ValueError(
                "FeatureArchitectAgent.generate_feature : "
                "function_signature manquante"
            )

        prompt = self._build_architect_prompt(decomposition, rag_context)
        self.log_thought(
            f"V32 ARCHITECT genere feature "
            f"{decomposition.get('feature_name', '?')} "
            f"({len(decomposition.get('test_cases') or [])} tests cibles)",
            type="thought",
        )

        try:
            raw_output = await self.generate_content(prompt)
        except Exception as exc:
            logger.warning(f"[V32 ARCHITECT] generate_content crash: {exc}")
            raise

        return raw_output or ""
