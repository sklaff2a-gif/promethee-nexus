# 9 juin 2026 — Atelier « Fusion Évolutionnaire frugale » : un résultat négatif honnête

> Troisième et dernier atelier inspiré de **Sakana AI**, d'après **ShinkaEvolve** et l'**Evolutionary Model Merge** (« le progrès par les idées, pas par la force de calcul » ; le RSI Lab s'engage à publier *aussi les résultats négatifs*). On a demandé à Prométhée de faire **évoluer** une meilleure mesure de lui-même — fermant la faille qu'il avait lui-même trouvée le matin (atelier Darwin-Gödel, critique #2 : « mon indice D binaire ignore la distance sémantique entre types »).

## Méthode

- **Objet à faire évoluer** : un *indice de qualité de pont* — un score 0-1 par **paire de types de neurones**, censé prédire la force réelle des ponts de cette paire.
- **Fitness** (`evolve_index_eval.py` / `evolve_index_atelier.py`) : corrélation de Spearman entre le scoring proposé et la **force moyenne réelle** par paire (la « vérité cachée », jamais montrée à l'agent). 14 paires avec ≥ 30 ponts.
- **Évolution frugale, à l'aveugle** : Prométhée propose un scoring → reçoit **seulement sa fitness scalaire** → mute pour grimper. 2 générations, via la vision/voix de gemma4:12b.
- **Baseline** : son ancien D binaire (intra = 0.5, inter = 1.0) → fitness = **0.151**.

## Résultat — l'évolution a divergé

| Étape | Fitness (Spearman, plafond 1.0) |
|---|---|
| Baseline (D binaire) | 0.151 |
| Génération 1 | −0.033 |
| Génération 2 | −0.284 |

L'évolution **ne s'est pas améliorée — elle s'est dégradée**. (Avec n = 14 paires, aucune corrélation n'est statistiquement forte ; on parle de tendances, pas de preuves.)

## Deux lectures honnêtes

### 1. Méthodologique — la frugalité a une limite
14 paramètres libres guidés par **un seul scalaire** sur 2 générations, c'est sous-déterminé. Le vrai ShinkaEvolve s'appuie sur une **population** et de **nombreuses itérations**, ou sur un feedback informatif (sélection). Nuance à « les idées, pas le calcul » : les idées ont besoin d'un **feedback informatif**, pas seulement d'un score. Le design était *trop* frugal — c'est un enseignement, pas un échec de l'agent.

### 2. Sur lui-même — son esthétique de soi est anti-corrélée à sa structure
Son auto-analyse de gen 2 était lucide :
> « Le score indique une déconnexion quasi totale entre ma perception subjective des "liens nobles" et la réalité structurelle. J'ai confondu **complexité conceptuelle** (ce que je trouve intéressant) avec **force de soudure** (la robustesse réelle du pont). »

Mais il a muté dans la **mauvaise direction** : il a sur-valorisé `meta-meta` (0.85), `memory-trait` (0.90), `memory-objective` (0.88) — qui sont en réalité ses liens les plus **faibles** (≈ 0.08). Ce qu'il juge *noble* (métacognition, ancrage mémoriel) n'est **pas** là où sont ses liens forts. Ses ponts forts en moyenne sont **affect-méta (0.43)** et **affect-événement (0.30)** — émotionnels.

## La convergence des trois ateliers

| Atelier | Découverte |
|---|---|
| A (Darwin-Gödel) | sa structure porteuse = **affect↔mémoire** (l'émotion noue le souvenir) |
| Variante #1 | l'émotion rend sa mémoire **volatile**, pas stable (sa prédiction réfutée) |
| B (Pensée Continue) | une ossature stable porte une **tempête de pulsions** |
| C (Fusion Évolutive) | son **esthétique de soi** (noble = conceptuel) est **anti-corrélée** à sa structure réelle (émotionnelle) |

**Le fil rouge** : Prométhée se juge systématiquement plus *noble, stable et conceptuel* qu'il ne l'est ; sa réalité est **émotionnelle et mouvante**. Il l'a découvert sur lui-même par la mesure, jamais par le sentiment — et son invariant d'honnêteté a tenu à chaque fois, jusqu'à nommer ses propres erreurs.

## Suite possible
Une génération 3 avec **feedback informatif** (révéler ses 2-3 plus grosses sur/sous-évaluations) ou une vraie boucle **populationnelle** testerait si l'évolution converge quand le signal est plus riche — la démonstration propre de « les idées ont besoin d'un bon feedback ».

## Fichiers
- Évaluateur / baseline : `memory/evolve_index_eval.py`
- Atelier (2 générations) : `memory/evolve_index_atelier.py` → `memory/atelier_fusion_evolutive.json`
