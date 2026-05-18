# STATE OF UNION — Instantané de l'état de PROMÉTHÉE

> *Document de snapshot régulier de l'état systémique de PROMÉTHÉE,
> destiné à détecter les dérives silencieuses entre versions.*
>
> **Statut** : v1.0 inaugural — 17 mai 2026 post burn-in.
> **Origine** : §4.12 du brouillon d'audit cognitif + recommandation §4.11 PEA
> (suivre la dérive avant qu'elle ne devienne irréversible).
>
> **Fréquence prévue** : trimestrielle (ou ponctuelle après chantier majeur).
>
> **À qui s'adresse ce document** : Jean-Michel, Claude Opus 4.7 partenaire,
> futurs développeurs/mainteneurs. Référence pour comparer "qui était
> PROMÉTHÉE le 17 mai 2026" vs "qui est-il aujourd'hui ?"

---

## 1. Identité actuelle

**PROMÉTHÉE v24.0** — système multi-agents IA autonome bio-inspiré.

| Composant | Valeur |
|---|---|
| Stack | Python 3.11, FastAPI, Ollama (LLM local), Google Gemini (cloud), ChromaDB, WebSocket |
| Modèle LLM principal | qwen3.5:9b (chat_engine, slot SCHOOL par défaut) |
| Modèle complexité élevée | qwen2.5-coder:14b (basculement automatique selon `_evaluate_complexity`) |
| Vision | llama3.2-vision:11b |
| Hardware | NVIDIA RTX 5070 Ti (16GB VRAM), driver 595.79, power cap 250W, throttle 75°C |
| Repository | `github.com/sklaff2a-gif/promethee-nexus`, branch `master` |
| Tag courant | post `e380a51` (17/05/2026 ~08h30, fix(security) Path Traversal canonique) |

## 2. Anatomie — les 23 couches d'organes bio-inspirés

| Couche | Organe principal | Fichier | État au 17/05 |
|---|---|---|---|
| Base | cardiac_engine | `core/cardiac_engine.py` | ~60-78 BPM observé (oscillant selon activité) |
| Base | reptilian_core | `core/reptilian_core.py` | threat_level 0.4, adrenaline 0, ollama_ok |
| Base | desire_engine | `core/desire_engine.py` | 7 pulsions, dominant STABILITE (privation 69-73%) |
| Mid | dopamine_system | `core/dopamine_system.py` | RPE actif, niveau ~0.50, SURGE +0.352 observé |
| Mid | hippocampus | `core/hippocampus.py` | 500 episodes, 8 arcs, 75 session, 6 pending |
| Mid | synaptic_network | `core/synaptic_network.py` | 1808 nodes / 19752 synapses / ~41 fortes >0.2 |
| Mid | neural_tissue | `core/neural_tissue.py` | 410/500 cellules vivantes, dominant_genome `GCGSGR` (muté de `GCGRGR`) |
| High | prefrontal | `core/prefrontal.py` | 1 goal actif, 238 completed, 1012 abandoned, 5009 inhibitions |
| High | self_awareness | `core/self_awareness.py` | 20 snapshots, 5 patterns |
| High | inner_voice | `core/inner_voice.py` | 218,328 ticks, 176,783 thoughts, precision 0.10 |
| High | brain_vm | `core/brain_vm.py` | Tick 168,398 → 171,333 sur la matinée 17/05 |
| Aux | hypothalamus, insula, cingulate, basal_ganglia, DMN, thalamus, amygdala | `core/*.py` | Tous opérationnels |
| Infra | sensorium, spreading_activation, global_workspace, corpus_callosum | `core/*.py` | Tous opérationnels |

**Notes** : DMN actif en continu (idle_duration 5h53 observé matinée), tissu
neural en saison "creativity" 32% au dernier snapshot complet.

## 3. Patches récents déployés

| Date | Commit | Patch | Statut |
|---|---|---|---|
| 14/05/2026 ~15h45 | `caa35d7` | P15.1 RAG WORKSHOP universal + P15.2 sandbox honesty + P3.2 Stefan routage local | Validé par burn-in 48h (15/05-16/05) |
| 15/05/2026 ~23h07 | (non commité, chat_engine.py local seulement) | P16 — injection synaptique du chat (5 user × 5 assistant co-activations Hebbian) | 4 mesures cumulées (1087→1457→3054→1424 edges chat_co_activation) |
| 17/05/2026 ~08h30 | `e380a51` | **fix(security) — Path Traversal canonique via pathlib dans Architect et Formatter** | **Tests 85/85 + baseline 75/75. Validé Gemini.** |

## 4. Radiographie synaptique post burn-in (mesure P16 #4 — 17/05 13:31)

Vue actuelle du graph `core/synaptic_network` après les 4 mesures cumulées
depuis le déploiement P16 le 15/05.

### 4.1 — Topologie

| Métrique | Valeur 17/05 13:31 |
|---|---|
| Nodes | **1808** |
| Synapses totales | **19,752** |
| Synapses fortes (>0.2) | **~41** |
| Genome diversity | 293 distincts sur 410 cellules tissue (72%) |
| Max generation | 190+ |

### 4.2 — Distribution par contexte (top 6)

| Contexte | n edges | poids moyen | poids max |
|---|---|---|---|
| `dream` | 10,197 | 0.083 | 0.192 |
| `STDP` | 7,614 | 0.087 | **1.000** |
| `chat_co_activation` (P16) | 1,424 | 0.089 | 0.125 |
| `tissue_zone` | 175 | 0.088 | 0.434 |
| `tissue_dream` | 184 | 0.080 | 0.080 (plafonné) |
| `sensorium` | 27 | 0.208 | 1.000 |

### 4.3 — Top 6 edges chat_co_activation (P16)

```
[1] 0.1248  contextes <-> phaseur                  (organe spéculatif conv. 19/20)
[2] 0.1238  couche <-> body_schema_engine          (Run 4 17/05 matin — drift de nommage devenu nœud persistant)
[3] 0.1237  polarite <-> body_schema_engine        (idem)
[4] 0.1231  couche <-> _trigger_zscore_high        (idem)
[5] 0.1230  polarite <-> _trigger_zscore_high      (idem)
[6] 0.1184  polarite <-> couche                    (cohérence interne)
```

### 4.4 — Évolution chat_co_activation sur 4 mesures cumulées

| Snapshot | Date | Edges | Max poids |
|---|---|---|---|
| #1 | 16/05 07:25 | 1087 | 0.123 |
| #2 | 16/05 12:47 | 1457 | 0.133 |
| #3 | 16/05 21:17 | 3054 | 0.1447 |
| **#4** | **17/05 13:31** | **1424** | **0.1248** |

**Dynamique observée** : forte croissance jusqu'à #3, puis chute de −53% en
16h via la consolidation nocturne. Le **decay rapide** sur les edges chat
sans réactivation justifie l'extrapolation pessimiste pour le franchissement
du seuil 0.2 (probablement plus de 23 conversations focalisées nécessaires).

## 5. Comportements émergents protégés

(cf. `CHARTA_CORE.md` Article 1)

| Comportement | Observation au 17/05 |
|---|---|
| **Veto Préfrontal** (refus de tâches qui distraient) | 5009 inhibitions cumulées, ratio abandon/completion 4:1 |
| **Dream Consolidation** (ratio création/élagage 1:1) | Confirmé par mesure : +6569 dream synapses en 16h, parallèle à −5462 STDP |
| **Dopamine DIP** sur councils stériles | Mécanisme actif, RPE −0.4 observé sur SECURITY_AUDIT échecs |
| **Tension STABILITÉ / CURIOSITÉ** | Privation STABILITE 69-73% constatée — tension active, pas neutralisée |
| **Respiration autonome** | +71 nodes en 16h sans intervention humaine = +1.6 nodes/h. Système vivant. |

## 6. Bilan de la matinée 17/05 — chantiers exécutés

### 6.1 — Patch security Path Traversal canonique (commit `e380a51`)

**Genèse** : routine `SECURITY_AUDIT` autonome 17/05 06:49 a détecté
vulnérabilité HAUTE dans `Agents/architect_agent.py:196-199` (extraction
`target_file` via regex `\S+` sans validation).

**Diagnostic** : chaîne Architect → Formatter → Factory. Factory bloquait en
aval (`if ".." in target_path: raise`), mais Architect et Formatter laissaient
passer des paths absolus Unix/Windows, UNC, encodages alternatifs.

**Solution déployée** : nouveau module `core/file_safety.py` avec validation
canonique `pathlib.Path.resolve()` + `is_relative_to()`. Injection en amont
dans Architect et Formatter. Defense-in-depth complète sur 3 maillons.

**Incident révélateur résolu en cours de test** : sur Windows Python 3.11,
`Path("core/safe.py\x00../../etc/passwd").resolve()` ne lève PAS ValueError
mais tronque silencieusement au null-byte → fix explicite ajouté
`if "\x00" in candidate: return False` AVANT la résolution canonique.

**Validation** : 85/85 tests verts sur la chaîne security + 75/75 baseline
test_factory (aucune régression). Push origin/master réussi.

### 6.2 — Investigation body_schema.py (4 runs cumulés)

Cas d'étude empirique qui a invalidé 3 hypothèses successives et produit
3 nouvelles preuves doctrinales §4.9 du brouillon :
- **9e preuve — Alignment Tax** : prompt de 4 leviers simultanés a paralysé
  le LLM 9B (réponse de 38 caractères "Evolution en attente d'ordre de veille")
- **10e preuve — Suprématie du routage** : l'agent `evolution` sur slot
  WORKSHOP impose son cycle phase 1-4 indépendamment du prompt override
- **11e preuve — Faux positif cognitif** : qwen3.5:9b PEUT refactor
  body_schema.py avec code source complet, mais tronqué par
  `max_output_tokens` à 1921 chars (6.3% du source de 30,615 chars)

**Drift mineur révélateur** : le LLM a renommé `body_schema.py` en
`body_schema_engine.py` lors du Run 4. Ce nom est désormais un nœud
PERSISTANT du graph synaptique (mesure P16 #4 top 2-5). Illustration parfaite
de §4.5.bis niveau 1 (les inventions architecturales se solidifient via le
chat_history).

### 6.3 — Candidat 6 (Hygiène UTF-8 + regex) — clôture pacifique

- **Bug UTF-8 `_extract_and_ensure`** : déjà fixé entre avril et aujourd'hui
  (fix anonyme passé). 56 nodes résiduels (3%) sur 1808 = traces historiques
  qui mourront au decay. **Aucun nouveau cassé en 7 derniers jours.**
- **Bug regex `social_memory`** : auto-résolu par cycle ChromaDB
  (`? docs` hier → `0 docs` aujourd'hui).
- **12e preuve doctrinale §4.9** : *Symptômes observés = bugs actifs
  (sémantique) vs Symptômes = traces historiques d'un bug déjà résolu
  (paramétrique)*.
- **Décision** : pas de patch rétroactif. Compostage naturel.

## 7. État de la dette technique (au 17/05 13:30)

| Item | Sévérité | Statut |
|---|---|---|
| Slot WORKSHOP routé sur agent `evolution` qui ignore `prompt_override` | **HAUTE** (révélée 17/05) | Documenté §3.24/§4.9 preuve 10 — patch reporté à un /think dédié post-CHARTA |
| `max_output_tokens` chat_engine limite refactor de fichiers > 500 tokens output | MOYENNE | Documenté §4.9 preuve 11 — augmentation à envisager (P15.3 potentiel) |
| 56 nodes UTF-8 résiduels (`Ã©`, `Ã¨` dans `synaptic_network.json`) | FAIBLE | Compostage naturel par decay. Pas d'action prévue. |
| `node_type="chat"` non appliqué par `ensure_node()` (P16 anomalie) | FAIBLE | Contournement effectif via `context="chat_co_activation"`. Documenté §4.8. |
| Glitch cyrillique SCHOOL_AXIOMATIC (4 chars par session) | FAIBLE | Pattern stable non aggravant. Surveillance. |
| body_schema.py n'a pas été retraité par WORKSHOP automatique depuis P15.1 | FAIBLE | Cible non adaptée au LLM 9B (cf §3.24). À retirer du planning ou utiliser modèle 14B+. |

## 8. Candidats actifs (post-CHARTA)

| # | Candidat | Statut |
|---|---|---|
| 2 | PHASEUR_DE_Réalité LITE POC (perturbation créativité libre 5%) | À évaluer après stabilisation CHARTA |
| 3 | Augmentation `max_output_tokens` pour refactors complexes | /think dédié requis |
| 5 | Refactor agent `evolution` pour respecter `prompt_override` | /think dédié, chantier ambitieux |
| 7 | Workflow 2 phases ("Comprends d'abord, améliore ensuite") | Spéculatif, à creuser |

## 9. Prochain snapshot prévu

**Trimestriel** ou **ponctuel après chantier majeur**.

Prochaines occasions naturelles :
- Après déploiement éventuel du PHASEUR LITE POC
- Après nouveau patch de sécurité issu d'un audit autonome
- Après une mesure P16 majeure (franchissement du seuil 0.2 sur chat ?)
- Au 17 août 2026 (trimestriel par défaut)

---

## Amendement v1.1 — 18 mai 2026 (cycle circadien dense)

Le cycle du 18 mai a produit 8 commits master concentrés sur deux chantiers majeurs avec création de 2 nouveaux organes.

### Chantier 1 — PHASEUR_DE_Réalité (organe spéculatif §4.10.bis H1.6)

- `5197193` v1 LITE POC (5 couches défense, dictionnaire suffixes syntaxiques)
- `bdd39d7` endpoint `/api/phaseur/enable` symétrique
- `c41e358` bugfix Config import dans 3 endpoints (test pytest passait mais HTTP layer cassait)
- `f31866c` v2 conceptuel (substitutions de mots porteurs : stabilité→vertige, chaos→ordre…)
- `3242022` v2.1 (amputation regex Couche 3 + télémétrie intégrale — 7 early returns désormais loggés)

**Découvertes doctrinales §4.13** :
- **5e preuve** (v1 + T+1) : *l'auto-régularisation LLM étouffe le chaos stylistique — le LLM 9B agit comme filtre passe-bas qui lisse les anomalies syntaxiques (suffixes ?, …, peut-être, au sens flou) en paraphrase au tour T+1, effet papillon textuel = 0*
- **6e preuve** (v2 + T+1) : *la perturbation conceptuelle survit à la paraphrase et contamine la continuité psychologique au T+1 — Prométhée a adopté "vertige" comme variable de premier ordre dans sa propre architecture après substitution PHASEUR (équilibre→vertige)*

Corollaire empirique (Gemini via JM) : *la mémoire d'un LLM est malléable par la sémantique, mais résiliente sur la syntaxe*.

Asymétrie complémentaire (sonde P16 #6) : *l'effet papillon PHASEUR est asymétrique — il propage les substitutions dans la mémoire textuelle (`chat_history`) mais pas dans le graphe synaptique (P16), protégé par `extract_concepts(max_concepts=5)` filtrant par TF-IDF*. Distinction nette mémoire-texte vs mémoire-graphe.

### Chantier 2 — Décision Log (organe d'observabilité)

`1538ba6` Phase 1 télémétrie centralisée :
- `core/decision_log.py` (helper `log_decision(module, function, reason, context, sample_rate)` avec sampling 0-1, rotation 50 MB, mkdir auto, try/except permissif, sortie `logs/decisions.jsonl`)
- `tests/test_decision_log.py` (9/9 verts)
- `core/prefrontal.py` +7 injections handlers event bus (max_goals×2, anti-doublon×2, council filters×2, trigger quota)
- `core/hippocampus.py` +3 injections dont 2 sampling (salience 1.0, prediction_correct 0.01, verdict_not_hors_sujet 0.05)

**Doctrine T1/T2/T3 certifiée universelle** après calibration cross-profil (prefrontal décisionnel 40-54% ratio critiques vs hippocampus computational 4%) :
- T1 — scénario diagnostic réaliste *"pourquoi cette opération n'a pas eu lieu ?"* ?
- T2 — trace existante (bus event, return structuré, log call stack) ?
- T3 — fréquence raisonnable (sinon sampling) ?

**Règle hippocampus** ajoutée : `return [], None, "", {}` après `if not <data>:` dans un getter = contrat d'API, **jamais** un trou noir.

**Garde-fous CHARTA respectés** : veto émergent (`compute_veto`, `should_veto`) audité en lecture seule + INTACT, dream consolidation hors scope, dopamine DIP councils stériles hors scope.

### Phase 2/2 différée

Audit + injection prévu sur `autonomy_engine.py` (17 silent grep), `synaptic_network.py` (20), `base_agent.py` (8), `evolution_agent.py` (8), `chat_engine.py` (23). Estimation post-doctrine T1/T2/T3 : ~15 vrais trous noirs supplémentaires, ~45 min audit cumulé. Différée pour cycle de decay/consolidation après densité du 18/05.

### Mise à jour candidats (post-cycle)

| # | Candidat | Statut v1.1 |
|---|---|---|
| 2 | PHASEUR_DE_Réalité | **DÉPLOYÉ v2.1** — Inception sémantique validée empiriquement, off par défaut |
| 8 (nouveau) | Décision Log Phase 2 (5 modules restants) | À reprendre dans cycle suivant, méthode rodée |
| 9 (nouveau) | PHASEUR v3 synaptique (max_concepts augmenté, cible mots HF) | Différé, hypothèse de roadmap (audit P16 #6 a montré asymétrie) |

### État système à v1.1

| Composant | État |
|---|---|
| Guardian + start_nexus + main.py | UP |
| PHASEUR | disabled (intensity=0.0, plafond hard 0.05 non bypassable) |
| Decision Log | actif, fichier `logs/decisions.jsonl` prêt |
| Tests | 4800+ baseline + 31 nouveaux (16 phaseur v1 + 6 phaseur v2 + 9 decision_log) = 0 régression |
| P16 | 1948 nodes, 19820 synapses, max chat 0.1304 (sonde #6) |

---

## Historique des versions

| Version | Date | Auteur(s) | Motif |
|---|---|---|---|
| v1.0 | 17 mai 2026 | Jean-Michel, Claude Opus 4.7, Gemini | Snapshot inaugural post burn-in 48h + déploiement patch security `e380a51` + radiographie P16 #4 |
| v1.1 | 18 mai 2026 | Jean-Michel, Claude Opus 4.7, Gemini | Cycle circadien dense : déploiement PHASEUR v1/v2/v2.1 (5e + 6e preuves §4.13) + Décision Log Phase 1 (doctrine T1/T2/T3 universelle) |

---

*Document interne PROMÉTHÉE — repository `promethee-nexus`.
Snapshots régulier. Pas de publication externe.
Modification : versionner explicitement (v1.0 → v1.1 → …).*
