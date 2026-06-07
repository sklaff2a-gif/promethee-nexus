# Sandbox Memoire V2 — chantier de la memoire associative a 3 tiers

Prototypes ISOLES (ne touchent PAS le runtime live). Conception + plan de migration
de la Memoire V2 de Promethee, nee du debat energie/cerveau (06/06) et de la
decouverte de la cause racine : **embedder anglais sur 5600+ entrees francaises**.

## Briques (48 TDD verts)
| Fichier | Role |
|---|---|
| `proto_retrieval_v2.py` | A — entree semantique (embedding) + etincelle locale ; top-3 5/5 vs hash MD5 0/5 |
| `schema_tiers.py` (+test, 11) | B-i — chassis des tiers + validateur d'ecriture ; faille 2 gravee (flag ne degrade jamais le premium) |
| `proto_integre_AB.py` | B-ii — fusion A+B : routeur qui LIT injected_label, assemblage hybride (consigne globale + prefixes) |
| `proto_review_decay.py` (+test, 9) | R3 — amortisseur temporel : registre + decay d'influence (poids Hebbian intact) + alerte saturation |
| `PROPOSED_shadow_read_diff.md` | Phase 1 — diff de greffe du shadow reader dans `core/vector_store.py:query_documents` (NON applique) |
| `proto_shadow_reader.py` (+test, 5) | Phase 1 — dual-retriever passif (kill-switch, retourne toujours l'ancien) |
| `migrator_v2.py` (+test, 8) | Phase 2 — reindexation anglais->multilingue : streaming chunks 500, checkpoint reprenable, upsert idempotent. `infer_tier` = OPTION A (tout CHURN) |
| `scan_tags.py` | Reco — scan READ-ONLY des metadonnees live (ro&immutable) |
| `seed_premium_lessons.py` (+test, 5) | Ancre sacree — seed des 12 lecons certifiees en PREMIUM/[CERTIFIE] |
| `benchmark_ann_scale.py` | Dry run — estime la Phase 2 : ~443 docs/s CPU -> 5709 noeuds en ~30s-2min |
| `review_cli.py` (+test, 10) | Arme d'arbitrage du Gardien : `ReviewBoard` (logique pure) + facades argparse/REPL. status (tri priorite+erosion) / promote / purge --all-decayed / diff |

## Plan de tir a froid (geste separe, hors sandbox)
1. Appliquer le diff Phase 1 (`SHADOW_READ_ENABLED=False`) — verifier que le code charge.
2. Lancer `migrator_v2` sur le vrai `collective_wisdom` (lecture seule -> collection neuve) + `seed_premium_lessons`.
3. `SHADOW_READ_ENABLED=True` -> collecte des ecarts a froid.
4. Analyser -> Phase 3 (Canary FREE_TIME) -> Phase 4 (Full Switch).

Le ChromaDB live aux 5600+ entrees reste INTOUCHE tant que la boucle n'est pas certifiee etanche.
