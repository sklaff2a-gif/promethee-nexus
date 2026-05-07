"""Phase 2 — Pre-digestion par chunk (Qwen 9B minimaliste).

[SQUELETTE — implementation a finaliser quand IMMERSION_DOMAIN sera
connectee a l AutonomyEngine. Voir digestion_routine.py.]

Pour chaque chunk de la Phase 1, un seul prompt court demande au LLM
local d extraire 3-5 concepts techniques saillants — pas un resume,
juste un listing. Pattern "RIEN" du V18 reutilise pour les chunks
boilerplate.

Design intentionnel :
  - LLM 9B et NON 14B : on veut un modele qui echoue lisiblement sur
    l inconnu. Si le 9B sort RIEN sur un chunk, c est de la donnee
    propre. Un 14B fabriquerait 5 concepts confiants a partir de
    boilerplate -> on tricherait avec le thermometre.
  - Pas de chain-of-thought : extraction pure, sortie listee.
  - Validation aval : un parser deterministe filtre la sortie pour
    rejeter les phrases narratives qui auraient passe le RIEN.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from core.immersion.chunker import Chunk


PROMPT_TEMPLATE = """\
Voici un extrait technique. Liste 3 a 5 concepts cles UNIQUEMENT
(noms communs ou acronymes, un par ligne, pas de phrase, pas de
ponctuation finale). Si l extrait ne contient AUCUN concept technique
pertinent (boilerplate administratif, dates, signatures, etc.),
reponds le seul mot RIEN.

Extrait :
\"\"\"
{chunk_text}
\"\"\"

Concepts :"""


# Validation aval : un concept doit faire entre 2 et 50 chars, ne pas
# contenir de ponctuation phrase complete, ne pas etre un mot vide FR/EN.
_STOP_WORDS = frozenset({
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou",
    "que", "qui", "pour", "avec", "sans", "dans", "sur", "the", "a",
    "an", "and", "or", "but", "with", "without", "in", "on", "at",
    "of", "for", "to", "from", "by",
})


@dataclass
class ExtractionResult:
    chunk_position: int
    concepts: List[str]            # liste filtree, normalisee
    raw_response: str
    is_nothing: bool               # True si le LLM a repondu RIEN


async def extract_concepts_from_chunk(
    chunk: Chunk,
    llm_callable=None,  # signature: async (prompt: str) -> str
) -> ExtractionResult:
    """Extrait les concepts saillants d un chunk via LLM 9B.

    [TODO Phase 2 — connecter a l interface LLM existante du projet
    (probablement core.chat_engine ou core.base_agent generate_content
    avec model=qwen3.5:9b et force_local=True).]

    Args:
        chunk : Chunk produit par la Phase 1.
        llm_callable : fonction async injectee pour testabilite. En
            production, sera lie a l interface LLM locale.

    Returns:
        ExtractionResult avec concepts filtres + flag is_nothing.
    """
    raise NotImplementedError(
        "Phase 2 — connexion LLM 9B a finaliser. Voir digestion_routine.py "
        "pour le cablage AutonomyEngine."
    )


def parse_extraction_response(raw: str) -> ExtractionResult:
    """Filtre deterministe applique en aval du LLM.

    Detecte le pattern RIEN, rejette les lignes trop longues (phrases),
    les stop-words seuls, les valeurs numeriques sans contexte.
    """
    raw = (raw or "").strip()
    if not raw:
        return ExtractionResult(
            chunk_position=-1, concepts=[], raw_response=raw, is_nothing=True
        )

    # Detection RIEN robuste (insensible a la casse, tolerant ponctuation)
    first_token = raw.split()[0].strip(".,;:!?").upper() if raw.split() else ""
    if first_token in {"RIEN", "NONE", "EMPTY"}:
        return ExtractionResult(
            chunk_position=-1, concepts=[], raw_response=raw, is_nothing=True
        )

    concepts: List[str] = []
    for line in raw.splitlines():
        c = line.strip().lstrip("-*0123456789. ").strip()
        if not c:
            continue
        # Trop long : probablement une phrase, pas un concept
        if len(c) > 50 or len(c) < 2:
            continue
        # Stop-word seul : pas un concept
        if c.lower() in _STOP_WORDS:
            continue
        # Pas de ponctuation finale
        c = c.rstrip(".,;:!?")
        if not c:
            continue
        concepts.append(c)

    return ExtractionResult(
        chunk_position=-1,
        concepts=concepts,
        raw_response=raw,
        is_nothing=False,
    )
