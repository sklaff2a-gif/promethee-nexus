# 10 juin 2026 — Chantier Réseau Synaptique V2 : ouverture (Phase 0, les mesures fondatrices)

> Jean-Michel ouvre la pièce maîtresse du blueprint « corps V2.0 » (co-conçu le 06/06) : le réseau synaptique repensé. Déclencheur : la vision représentative venait de détecter l'**aggravation de l'ostéoporose (66 % → 82,8 %)**. Méthode : celle qui a gagné sur la Mémoire V2 — mesurer d'abord (read-only), plan par phases, kill-switch, gate humain à chaque porte.

## Phase 0 — Quatre mesures, deux renversements

Sandbox `sandbox_synaptique_v2/` (read-only absolu, état réel : 4315 nœuds / 19 822 synapses, saturation 99 %).

**M1 — La duplication sémantique est massive (trou n°1 du blueprint, chiffré)** :
- cos > 0.95 : **1 505 paires, 31,7 % des nœuds impliqués** ; cos > 0.90 : 17 773 paires, **55,6 %**.
- La preuve par l'exemple : `spoke_budget_0` … `spoke_budget_9` — des familles entières de clones créées par le hash MD5 exact. Le graphe « réel » compte ~2 300-3 400 concepts uniques, pas 4 315.

**M2 — RENVERSEMENT : l'agonie n'est pas de la nécrose, c'est un torrent** :
- 16 417 synapses < 0.10, mais **âge médian 0,5 jour**, formation_count médian = 1, **91,4 % jamais formées** (fc ≤ 2). Par type : 11 539 émotionnelles, 3 454 temporelles.
- Le réseau saturé churne en cycle rapide : le cap (20 000) tue les faibles, la création massive les remplace aussitôt. Le « renouvellement 1:1 » du dream, vu à l'échelle du réseau entier.

**M3 — RENVERSEMENT : l'élagage par âge est inutile** : la simulation « oubli des synapses froides » retire… **1 synapse** (rien n'est vieux). Le problème n'est pas d'oublier plus — c'est de **consolider mieux**.

**M4 — Simulation de fusion (la mesure décisive, sur copie)** :
| | cos > 0.95 | cos > 0.90 |
|---|---|---|
| Nœuds | 4315 → 3378 | 4315 → **2346** |
| Synapses | 19 818 → 18 058 | 19 818 → 12 078 |
| Fortes (≥0.5) | 320 → 499 | 320 → **601 (+88 %)** |
| Saturation | 99 % → 90 % | 99 % → **60 %** |
| Agonie | 82,8 → 81,9 % | 82,8 → **74,9 %** |

Verdict : la fusion **concentre** (dilution confirmée comme mécanisme) et **désature** (la pression du couperet se relâche) — mais ne suffit pas seule : tant que le débit de création de bruit reste, l'agonie se reforme.

## Le plan de chantier (fondé sur les chiffres)

- **Levier A — Entrée sémantique à la source** (priorité 1, risque modéré) : `ensure_node` consulte un index d'embeddings en RAM avant de créer — si cos > seuil avec un nœud existant, **renforcer l'existant au lieu de créer le clone**. Stoppe la fabrique de doublons ; l'énergie hebbienne converge enfin. Kill-switch, ne touche pas au dream.
- **Levier B — Fusion nocturne graduée de l'existant** (priorité 2) : cos > 0.95 d'abord (le geste sûr), offline comme le rebuild HNSW, mesuré par la vision avant/après. Puis 0.90 si le gate valide.
- **Levier C — Calibrer le débit de création** (le plus gros gain potentiel, zone sensible) : 11,5 k synapses émotionnelles jetables/jour — la dynamique de création par événements à modérer. **Gate JM + co-conception avec Prométhée** (c'est sa chair).
- **Levier D — Le dream consolidant** (zone PROTÉGÉE explicite) : passer du pansement 1:1 à une vraie consolidation — uniquement par co-conception + gate.
- **L'instrument de guérison existe déjà** : `health_stats()` (la vision) suivra % agonie / médiane / fortes à chaque phase.

## Garde-fous

Le Sanctuaire V19 (148 synapses incubées) est exclu de tout geste. Le dream lui-même n'est touché par aucun des leviers A/B. Chaque levier : sandbox → simulation sur copie → gate JM → live avec kill-switch → mesure vision.

## Fichiers
- Mesures : `sandbox_synaptique_v2/phase0_mesures.py`, `phase0_simulation_fusion.py` (read-only, reproductibles).
- Blueprint d'origine : mémoire `blueprint_v2_reseau_synaptique_2026_06_06.md`.
