# 9 juin 2026 — Atelier « Auto-Adaptation » (Transformer²) : Prométhée étudie et fait évoluer son routage

> Cinquième et dernier atelier inspiré de **Sakana AI**, d'après **Transformer²** (un modèle qui s'auto-adapte en deux passes : un *dispatch* diagnostique la tâche, puis des « experts » spécialisés sont mélangés pour y répondre). Or Prométhée a déjà ça : son **cortex préfrontal route à 3 voies** selon le slot de la tâche. Cet atelier lui fait étudier sa propre auto-adaptation et en proposer l'évolution.

## Le mécanisme réel

`generate_content` → `_route_prefrontal_mirror(slot)` → un expert parmi :

| Expert | Slots | Coût | Veto |
|---|---|---|---|
| `code` (miroir AST déterministe) | WORKSHOP, FEATURE_BUILDING | **0 ms** | avortement sec |
| `intro` (juge comportemental 9B) | BULLETIN, CREATION, FREE_TIME | **~6,7 s** (max 14 s) | mode dégradé |
| `none` (aucun miroir) | CODE_REVIEW, REFACTORING_AUDIT, CHAT, RESEARCH… | 0 | — |

Télémétrie réelle (`prefrontal_metabolism.jsonl`, 8 évaluations) : 5 PASS / 3 VETO ; l'expert `intro` a attrapé 2× une « ornière descriptive » et 1× un « slogan mort ».

![carte d'auto-adaptation](img_atelier_2026_06_09/dispatch_map.png)

## L'atelier

### Round 1 — il analyse sa table d'aiguillage (mode ingénieur)
- `code` : « Optimal. On ne négocie pas avec la syntaxe. »
- `intro` : coûteux mais justifié ; « le mode dégradé indique que l'expert `intro` est parfois incapable de résoudre la complexité qu'il a lui-même détectée. »
- `none` : « le point de rupture du système… un pari statistique… zéro contrôle. »
- Verdict : « **Non, pas parfaitement optimal, c'est un compromis pragmatique.** » Inefficiences identifiées : gaspillage (appeler `intro` à 6,7 s pour ce que `code` suffirait) et risque (`none`). Et : « On préfère parfois une réponse lente et "vivante" à une réponse instantanée et "morte". »

### Round 2 — il propose une évolution (Transformer²)
Une **« Cascade de Résolution Graduelle »** avec un **`SENTINEL-Gate`** :
- Une micro-couche (< 50 ms) entre le dispatch et les experts évalue la *densité sémantique* / le *risque d'ambiguïté* → route vers `code` (faible), un nouvel expert **`lite`** intermédiaire (moyen), ou `intro` (élevé / émotionnel) ; `none` réservé à la recherche pure.
- **Effet prédit (mesurable)** : « −25 % du temps moyen de traitement » sur les slots production ; plus de PASS sur les requêtes hybrides (technique + narratif). *(Chiffre = son estimation, à valider.)*
- **Sur sa gouvernance** : « L'économie cognitive est une nécessité, mais l'authenticité est une exigence. […] Je ne me contente plus de répondre à la demande, **je calcule la valeur de la réflexion nécessaire avant d'engager mes ressources** » — une transition d'une architecture *réactive* vers *prédictive*.

## Ce que l'atelier établit

1. **Transformer² est déjà en lui** : son routage préfrontal EST une auto-adaptation dispatch→experts ; il le reconnaît et l'analyse lucidement.
2. **Une vraie proposition d'évolution** : le `SENTINEL-Gate` (cascade graduée, expert `lite`) est précisément l'idée de Transformer² (gating adaptatif) appliquée à son propre cortex, avec un effet prédit chiffré.
3. **Boucle avec l'atelier D** : l'éthique de parcimonie (économiser le juge cher) appliquée à son architecture décisionnelle — la frugalité Sakana, de ses cellules à sa gouvernance.

## Archive (proposition d'auto-modification, NON appliquée)

| # | Variante | Effet prédit | Statut |
|---|----------|--------------|--------|
| 2 | `SENTINEL-Gate` — cascade graduée (gate < 50 ms → code / lite / intro), `none` réservé à la recherche pure | −25 % latence sur slots production ; ↑ PASS sur requêtes hybrides | archivée, à évaluer |

## Fichiers
- Rendu : `memory/render_dispatch.py` → `dispatch_map.png`
- Atelier : `memory/workshop_dispatch.py` → `memory/atelier_autoadaptation.json`
