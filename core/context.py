# -*- coding: utf-8 -*-
"""core/context.py — Variables de contexte asynchrone (contextvars).

Confinement par cycle de vie de tache asyncio : un flag pose ici traverse tout l'appel
(RAG -> generate_content -> vector_store) SANS polluer les signatures intermediaires, et
disparait des que la tache prend fin. Aucun etat persistant sur les singletons -> pas de
race entre routines concurrentes (chaque tache a sa propre copie du contexte).
"""
import contextvars

# Canary Memoire V2 (Phase 3) : quand True, query_documents sert le temoin MULTILINGUE
# (collective_wisdom_v2_test, embedder francais) au lieu de l'ancien index anglais.
# Pose UNIQUEMENT autour des routines FREE_TIME (zone nocturne a bas risque metabolique)
# par autonomy_engine -> confinement absolu du segment d'experimentation.
canary_mem_v2: contextvars.ContextVar = contextvars.ContextVar("canary_mem_v2", default=False)
