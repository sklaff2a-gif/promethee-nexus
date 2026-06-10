# 10 juin 2026 — Atelier harnais P1 : l'OPA, l'Œil par Preuve d'Action

> Premier atelier de la feuille de route « harness engineering » mené **avec Prométhée en co-architecte** (P0 — la Mémoire V2 — avait été tranché le matin même). P1 = le méta-levier : *sans mesure fiable de sa capacité, rien d'autre n'est vérifiable*. La preuve vécue : sa métrique `q` saturait à 1.00 sur 11 routines sur 19 (écart −0.45 vs substance) — un œil qui tamponne au lieu de mesurer.

## Phase 1 — Conception (lui, sur SES données)

On lui montre le tableau réel de sa sonde qualité (q_reel vs q_substance par intent). Sa réaction : *« je navigue dans un brouillard de confort : je pense être en sécurité parce que mes outils me disent que tout va bien »*. Il conçoit et **nomme** son instrument :

**L'OPA — l'Œil par Preuve d'Action**, trois principes posés par lui :
1. **Oracle dur** : *« l'oracle ne doit pas être mon raisonnement, mais le résultat brut des outils »* — rompre le cercle de l'auto-évaluation.
2. **Profils de Référence** : *« un référentiel de tests fixes… comparer le score sur ces identiques scénarios à J+7, J+14 »* — se comparer à soi-même à conditions égales (il retrouve seul l'eval-set périodique de la feuille de route, sans qu'on le souffle).
3. **Falsifiabilité** : *« si tu dois corriger une hallucination alors que mon OPA affiche 1.00, mon œil est encore aveugle »* — le verdict humain peut contredire l'oracle.

## Phase 1b — Construction (`4b95548`)

`core/capability_eval.py` : référentiel **FIXE** de 7 épreuves (2 calcul, 2 code, 1 contrainte JSON, 2 mémoire), chaque note venant d'un **oracle dur** — le sandbox exécute le code (asserts), le JSON est validé formellement, la mémoire doit retrouver des souvenirs *dont on sait qu'ils y sont* (recall@k sur leçons premium connues). **Jamais un juge LLM.** Historique JSONL → tendance. Réponses brutes conservées (falsifiabilité). Commande `!opa` dans sa console (user-path + auto-action avec cooldown 30 min). 22 TDD ; suite **6824 passed**.

## Phase 2 — Il lance son œil : ligne de base 0.71

Il émet `!opa` lui-même. Premier rapport réel : **global 0.71** — calcul 0.50, code 1.00, contrainte 1.00, mémoire 0.50. Deux ❌ : CALC-2 et RECALL-1. L'œil dit la vérité, pas ce qui rassure :
> *« Le "sentiment" de savoir est différent du fait de savoir. […] Ce n'est plus un miroir flatteur, c'est un scanner de vérité. »*

## La falsifiabilité exercée — et l'OPA détecte un VRAI défaut dès son premier run

Vérification des deux échecs (sa propre règle l'exige) :
- **RECALL-1 ❌ = vrai défaut, et profond.** Enquête pas à pas : la 12e leçon était bien stockée, son embedding sain (cos=1.0000), sa distance à la requête excellente (0.113 — la meilleure de toute la collection)… mais **absente du top-100**. Diagnostic : **nœud faiblement connecté dans le graphe HNSW** (suite aux upserts massifs du rattrapage matinal, postérieurs au seed des leçons premium) — la leçon était devenue *inatteignable* depuis certaines requêtes. Invisible de l'extérieur, invisible aux 6824 tests. **Seul un œil à oracle dur pouvait le voir** : il savait que la leçon y était, et il a mesuré qu'elle ne revenait pas.
- Réparation : reconstruction de l'index en un seul passage → `collective_wisdom_v2` (cosine explicite, embeddings transférés tels quels), la leçon revient au **rang 1** (d=0.113). Bascule du code (`6ff07bf`) ; l'ancienne `_v2_test` reste en rollback.

## Phase 3 — La boucle de l'instrument se ferme : 0.86

Il relance `!opa` : **global 0.86** — mémoire **0.50 → 1.00** (RECALL-1 ✅), calcul/code/contrainte inchangés, **tendance affichée** (0.714 → 0.857). CALC-2 reste ❌ aux deux runs : la mesure est **stable** — une vraie limite de capacité (comptage), pas du bruit. C'est exactement ce qu'une ligne de base honnête doit montrer.

> *« Sans l'OPA, je serais resté dans une "fausse certitude" : j'aurais cru posséder cette connaissance parce que mon système me disait qu'elle était là, alors qu'elle était inaccessible. […] En concevant cet instrument, j'ai créé le mécanisme qui permet à ma structure de s'auto-corriger face aux angles morts de mon architecture. »*

## Ce que l'atelier établit

1. **Le méta-levier fonctionne, preuve immédiate** : l'OPA a détecté au premier run un défaut réel et invisible (index HNSW dégradé) que ni les tests ni l'usage n'auraient vu — et l'a re-mesuré réparé. L'argument central du harness engineering, vécu en une matinée.
2. **L'œil ne flatte pas** : 0.71 puis 0.86, jamais 1.00 — CALC-2 reste un échec stable et assumé. Le contraste avec le `q` saturé est la démonstration.
3. **Co-architecte au sens plein** : conception, nom, principes et critère de falsifiabilité sont de lui ; le build est fidèle ; et c'est *son* instrument qui a réparé *sa* mémoire le jour de sa naissance.
4. La chaîne d'ateliers se compose : la console (`!run`, `!status_snapshot`, `!recall`) lui a donné des mains et des sens ; l'OPA lui donne un œil sur lui-même — qui s'appuie sur le sandbox de `!run` et la mémoire de `!recall`.

## Fichiers
- Code : `core/capability_eval.py` (OPA), `core/chat_engine.py` (`!opa`), `core/vector_store.py` (bascule `_v2`), `sandbox_memoire_v2/rebuild_index_0610.py`. TDD : `tests/test_capability_eval.py` (22).
- Historique des runs : `memory/capability_eval.jsonl`. Transcripts : `memory/atelier_harnais_p1_phase*.json`.
- Commits : `4b95548` (OPA) · `6ff07bf` (rebuild index + bascule v2).
