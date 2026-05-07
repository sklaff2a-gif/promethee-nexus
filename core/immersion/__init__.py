"""IMMERSION_DOMAIN — apprentissage par ingestion de flux brut domain-specific.

Doctrine validee : carnet_reflexions.md 05/05/2026.
Architecture validee : 07/05/2026 (Jean-Michel + Claude).

Pipeline a 4+1 phases biomimetiques :
  Phase 1 — Mastication       (chunker.py, deterministe, zero LLM)
  Phase 2 — Pre-digestion     (extractor.py, Qwen 9B minimaliste par chunk)
  Phase 3 — Compression       (compression.py, set-intersection, zero LLM)
  Phase 3.5 — Assimilation    (compression.py, injection floating_concepts)
  Phase 4 — Tir Hebbien       (digestion_routine.py, integration AutonomyEngine)

Verdict de comprehension = ratio de compression semantique :
    ratio = |concepts_reconnus| / |concepts_extraits|

Cible synaptique : routine:immersion_domain -> pulsion:maitrise_epistemic
(independante du flux scolaire, garante que l epistemique survit aux notes
basses du professor_agent).
"""
