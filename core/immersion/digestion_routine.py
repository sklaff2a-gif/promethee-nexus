"""Phase 4 — Tir Hebbien + integration AutonomyEngine.

[SQUELETTE — a connecter au scoring AutonomyEngine quand IMMERSION_DOMAIN
sera prete a tourner. Voir blueprint Jean-Michel 07/05/2026.]

Routine IMMERSION_DOMAIN :
  Declencheur metabolique (ET logique) :
    - waste_grid bas (corps va bien)
    - pulsion:stabilite hors crise
    - dopamine_level basse OU pulsion:maitrise_epistemic en carence

  Sequence :
    1. Picker un fichier dans data/raw_flux/post_mortems/
       (oldest first, ou random selon politique a definir)
    2. Phase 1 — chunk_post_mortem(text)
       Si too_large : fragmenter en sous-documents pour cycles suivants
    3. Phase 2 — pour chaque chunk : extract_concepts_from_chunk
       (parallelisable, mais respecter budget GPU 15s par appel)
    4. Phase 3 — compute_compression(extracted, known_concepts)
       Si is_meaningful=False (que des RIEN) : ignore sans penalite,
       move file to data/raw_flux/digested/<file>.empty et return.
    5. Phase 3.5 — assimilate_unknowns(unknowns, synaptic_network)
       (avant la Phase 4 : on veut que le DOPAMINE_DIP/SURGE arrive
       APRES que les inconnus soient acquis, pour que le tir Hebbien
       du prochain document touche un graphe deja enrichi).
    6. Phase 4 — tir Hebbien selon verdict :
         - "pain"               -> DOPAMINE_DIP + extinction legere
                                   sur routine:immersion_domain (apprend
                                   a ne pas insister sur ce domaine si
                                   trop nouveau)
         - "micro_consolidation"-> renforcement partial_factor
                                   proportionnel au ratio
         - "full_consolidation" -> renforcement plein +
                                   DOPAMINE_SURGE
       Synapse cible : routine:immersion_domain -> pulsion:maitrise_epistemic
       (independant du flux scolaire — c est l autre voie d alimentation
       de l axe epistemique decretee dans le carnet 05/05).
    7. Archive : move file to data/raw_flux/digested/ avec metadonnees
       (ratio, timestamp, verdict).

Metriques surveillees (cf. carnet 05/05) :
  - documents_ingeres_jour, documents_rejetes_par_satiete
  - ratio moyen glissant (courbe d apprentissage Friston)
  - dopamine_dip_par_doc

Anti-patterns a ne PAS reintroduire :
  - Pas de note externe (pas de professor_agent dans la boucle)
  - Pas de curriculum (flux plat, ordre non-pedagogique)
  - Pas de feedback chat sur la progression
  - Pas d optimisation du ratio comme cible (c est un thermometre,
    pas un objectif)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


# --- Constantes routine ---
IMMERSION_INTENT = "IMMERSION_DOMAIN"
IMMERSION_SOURCE_CONCEPT = "routine:immersion_domain"
IMMERSION_TARGET_CONCEPT = "pulsion:maitrise_epistemic"

# Repertoires de flux (relatifs a la racine du projet)
RAW_FLUX_DIR = "data/raw_flux/post_mortems"
DIGESTED_DIR = "data/raw_flux/digested"

# Cooldowns metaboliques (a calibrer apres premiers cycles in-vivo)
IMMERSION_MIN_INTERVAL_SECONDS = 1800   # 30min entre 2 ingestions
IMMERSION_MAX_PER_DAY = 8


@dataclass
class DigestionReport:
    """Resume d une ingestion (pour journal + audit)."""
    document_path: str
    n_chunks: int
    n_concepts_extracted: int
    n_recognized: int
    n_unknown_assimilated: int
    ratio: Optional[float]
    verdict: str               # ignored / pain / micro_consolidation / full_consolidation
    delta_synaptic: float      # 0 si ignored
    timestamp: float


async def should_trigger_immersion(autonomy_engine) -> bool:
    """Verifie les conditions metaboliques de declenchement.

    [TODO — connecter aux signaux runtime :
        - autonomy_engine.cardiac.bpm
        - autonomy_engine.dopamine.level
        - autonomy_engine.desire.pulsions['STABILITE']
        - autonomy_engine.tissue.waste_grid
        - autonomy_engine.synaptic.last_immersion_time]
    """
    raise NotImplementedError("A cabler quand AutonomyEngine sera pret a recevoir IMMERSION_DOMAIN")


async def run_immersion_cycle(autonomy_engine) -> Optional[DigestionReport]:
    """Cycle complet d ingestion d un document (Phases 1->4).

    [TODO — orchestration des 4 phases. Pseudocode :

        path = pick_next_document(RAW_FLUX_DIR)
        if not path: return None
        text = path.read_text(encoding="utf-8")

        # Phase 1
        chunking = chunk_post_mortem(text)
        if chunking.too_large:
            # Split sur N cycles : marker du document, return
            return None

        # Phase 2
        extracted_all: List[str] = []
        for chunk in chunking.chunks:
            result = await extract_concepts_from_chunk(chunk, llm)
            extracted_all.extend(result.concepts)

        # Phase 3
        known = collect_known_concepts(autonomy_engine.synaptic)
        comp = compute_compression(extracted_all, known)
        if not comp.is_meaningful:
            archive(path, suffix=".empty")
            return None

        # Phase 3.5 (avant la Phase 4 — voir docstring module)
        n_assimilated = assimilate_unknowns(comp.unknown, autonomy_engine.synaptic)

        # Phase 4
        delta = apply_hebbian_immersion_tir(autonomy_engine, comp)
        archive(path, suffix=f".{comp.verdict}")

        return DigestionReport(...)
    ]
    """
    raise NotImplementedError("A cabler quand les Phases 2 et 4 seront finalisees")


def collect_known_concepts(synaptic_network) -> List[str]:
    """Extrait la liste des concepts du synaptic_network pour Phase 3.

    Helper a brancher : iterer sur synaptic_network.nodes et collecter
    les concepts (str). Devra peut-etre exclure les nodes de type
    'reflex', 'pulsion', 'trait' (deja dans une autre couche) — a decider
    apres premier run experimental.
    """
    raise NotImplementedError("Selection des node_types a inclure : a decider apres premier run")


def apply_hebbian_immersion_tir(autonomy_engine, compression_result) -> float:
    """Phase 4 : applique le tir Hebbien selon le verdict de compression.

    Reutilise _apply_causal_delta du synaptic_network avec un partial_factor
    derive du ratio (analogue a la rampe RPE deployee aujourd hui dans
    _learn_from_epistemic_closure pour le flux scolaire).

    La synapse touchee est :
        routine:immersion_domain -> pulsion:maitrise_epistemic

    [TODO — calibrer le delta de base, decider de la trajectoire pour
    le verdict "pain" (extinction legere ? rien ?). Premiere version
    proposee : pas d extinction sur "pain" — c est de l information
    metabolique (DOPAMINE_DIP), mais le systeme doit garder la motivation
    de relire le domaine.]
    """
    raise NotImplementedError("A finaliser apres premier observation in-vivo de la mecanique")
