# Grand Livre des Bugs — Prométhée V11
> Date : 2026-03-02 | Audit complet pré-ménage
> Classement : CRITIQUE > MAJEUR > MOYEN > MINEUR > COSMÉTIQUE

---

## CRITIQUES (3) — Bombes à retardement

### C01 — ~~Architecte : troncation trop agressive~~ **FIXED** (2026-03-07)
- **Fichier** : `config.py`, `Agents/architect_agent.py`, `Agents/formatter_agent.py`, `Agents/coder_agent.py`
- **Description** : Troncation hardcodée ([:6000], [:4000]) sans lien avec le num_ctx de l'agent.
- **Fix** : `Config.get_max_content_chars(agent_name)` — calcul dynamique basé sur AGENT_NUM_CTX (2 chars/token - overhead).

### C02 — ~~Orchestrateur : guard `is_internal_pipeline` fragile~~ **FIXED** (2026-03-07)
- **Fichier** : `core/orchestrator.py:137-141`
- **Description** : `startswith()` ne détecte les markers que s'ils sont en début de contexte.
- **Fix** : Remplacé par `in` (opérateur de sous-chaîne) — robuste quelle que soit la position du marker.

### C03 — ~~Formatter→Factory : fire-and-forget sans feedback~~ **FIXED** (2026-03-07)
- **Fichier** : `Agents/formatter_agent.py` (2 endroits : bypass Evolution + chemin normal)
- **Description** : `loop.create_task()` → fire-and-forget. Factory pouvait échouer sans que personne ne le sache.
- **Fix** : `await orchestrator.dispatch_task()` — Factory échoue → Formatter retourne error → Architecte le sait.

---

## MAJEURS (12) — Bugs fonctionnels significatifs

### M01 — Boucle MAITRISE-REFACTOR obsessionnelle
- **Fichier** : `core/autonomy_engine.py:1152-1172`
- **Description** : Quand la pulsion MAITRISE a deprivation ≥90, le système force REFACTOR_RANDOM ou EXPANSION_CODE. Si la routine échoue (low_quality), la deprivation ne baisse PAS et le frustration_streak augmente, renforçant le forçage. L'anti-boucle vérifie seulement `best_intent != last_intent`, donc le système ALTERNE entre deux intents forcés sans jamais sortir.
- **Impact** : 40+ points de budget gaspillés par run (10 refactors × 4pt). Observé dans les logs du 02/03.
- **Fix proposé** : Compteur de forçage par drive. Si >2 forçages en 5 cycles → cooldown 10 cycles pour ce drive.

### M02 — Payload dispatch sans clé `intent`
- **Fichier** : `core/autonomy_engine.py:1060-1064`
- **Description** : Le dispatch standard envoie `{"mission": ..., "context": ...}` SANS la clé `intent`. Le vision_agent vérifie `payload.get("intent")` pour router ROADMAP_RESEARCH/SPEC — ne le trouvera jamais dans le payload.
- **Impact** : Les routines ROADMAP_RESEARCH et ROADMAP_SPEC tombent dans `_handle_general()` au lieu du handler approprié.
- **Fix proposé** : Ajouter `"intent": intent` au payload dispatch (one-liner).

### M03 — Formatter : troncation [:2000] perd le code
- **Fichier** : `Agents/formatter_agent.py:156`
- **Description** : Le path standard tronque le code à 2000 chars. Le LLM produit un fichier incomplet envoyé à la Factory. Le bypass Evolution contourne ce problème, mais le path standard (missions utilisateur) souffre.
- **Impact** : Code incomplet écrit par la Factory pour les missions non-Evolution.
- **Fix proposé** : Augmenter la limite ou utiliser le bypass systématiquement.

### M04 — Coder : prompt sans limite de taille → guardrail perdu
- **Fichier** : `Agents/coder_agent.py:82-92`
- **Description** : Le `context` est injecté tel quel dans le prompt sans limite. Si le context dépasse la fenêtre du LLM (8192 tokens), la fin du prompt est tronquée — y compris le `CODE_GENERATION_GUARDRAIL` placé en fin (biais de récence).
- **Impact** : Anti-hallucination inefficace quand le contexte est long.
- **Fix proposé** : Tronquer le contexte AVANT le guardrail suffix, pas après.

### M05 — `_contains_python_code()` dupliqué sans sync
- **Fichier** : `Agents/architect_agent.py:60-64` + `core/orchestrator.py:31-34`
- **Description** : La même logique est dupliquée. Le commentaire dit "synchroniser si modifié" — aucune garantie. De plus, `evolution_agent.py:1028` appelle `orchestrator._contains_python_code()` directement (couplage fort).
- **Fix proposé** : Extraire dans un module utilitaire partagé.

### M06 — Double chemin dispatch Architect (interne + Bridge orchestrateur)
- **Fichier** : `Agents/architect_agent.py` + `core/orchestrator.py:142-158`
- **Description** : L'Architecte a son propre routage vers le Formatter, ET l'orchestrateur a un Bridge Architect→Factory. Si l'Architecte retourne "VALIDE_SANS_CODE", le Bridge pourrait matcher et dispatcher en doublon.
- **Fix proposé** : Clarifier les responsabilités — le Bridge OU le routage interne, pas les deux.

### M07 — Factory : `_resolve_smart_path` dépend du cwd
- **Fichier** : `Agents/factory_agent.py:148-162`
- **Description** : `os.path.exists(potential_path)` avec chemin relatif dépend du répertoire de travail. Si le processus est lancé depuis un autre répertoire, le Smart Path échoue.
- **Fix proposé** : Utiliser `os.path.join(self.project_root, ...)` pour les chemins.

### M08 — Factory : sandboxing `startswith()` contournable
- **Fichier** : `Agents/factory_agent.py:247`
- **Description** : `os.path.abspath(target_path)` résout le chemin par rapport au cwd, pas au `project_root`. Si le cwd est un sous-répertoire, le check `startswith(project_root)` pourrait laisser passer un chemin hors du projet.
- **Fix proposé** : Résoudre avec `os.path.join(self.project_root, target_path)` puis `os.path.abspath()`.

### M09 — `OLLAMA_DOWN` vs `OLLAMA_UNRESPONSIVE` : deux events, un seul écouté
- **Fichier** : `core/code_smith.py:665` + `core/evolution_catalog.py:408` vs `core/base_agent.py:655`
- **Description** : `OLLAMA_DOWN` (code_smith, evolution_catalog) et `OLLAMA_UNRESPONSIVE` (base_agent) décrivent le même concept. Seul `OLLAMA_UNRESPONSIVE` a des souscripteurs (reptilian_core). `OLLAMA_DOWN` est orphelin.
- **Fix proposé** : Renommer `OLLAMA_DOWN` → `OLLAMA_UNRESPONSIVE` dans code_smith et evolution_catalog.

### M10 — InfraAgent : import `Config` sans fallback (crash si absent)
- **Fichier** : `Agents/infra_agent.py:6`
- **Description** : `from config import Config` au top level sans try/except. Si config.py a une erreur, l'agent ne se charge pas.
- **Fix proposé** : Import local dans les méthodes qui en ont besoin.

### M11 — Specs `failed` verrouillées sans déverrouillage automatique
- **Fichier** : `core/evolution_catalog.py:1738-1750`
- **Description** : Après 3 échecs, une spec passe en `failed` et ne revient JAMAIS à `available` automatiquement. Le catalogue se vide progressivement.
- **Fix proposé** : Déverrouillage automatique après 7 jours si le système a progressé.

### M12 — FOCUS_BONUS_PRIMARY = 6.0 domine le scoring
- **Fichier** : `core/prefrontal.py:58`
- **Description** : +6.0 pour le goal #1 est plus grand que desire (max 3.0) + voix (max 2.0) combinés. Quand un goal préfrontal est actif, il écrase toutes les autres couches → tunnel vision.
- **Fix proposé** : Réduire à 4.0 ou amortir quand le goal stagne.

---

## MOYENS (9) — Fonctionnels mais dégradés

### Mo01 — error_streak de 4 ne décrémente jamais spontanément
- **Fichier** : `core/autonomy_engine.py:2218-2219`
- **Description** : Le streak décrémente seulement si ≥5 (par cycle) OU par succès. Un streak de 4 est permanent jusqu'au prochain succès, causant des sleeps prolongés sans fin.
- **Fix proposé** : Décrémenter aussi à partir de 3 ou ajouter un timer de decay.

### Mo02 — Soliloque : `_get_strategic_mode()` référence un attribut inexistant
- **Fichier** : `core/soliloque.py:505-511`
- **Description** : `getattr(autonomy, "strategic_mode", "standard")` — l'attribut `strategic_mode` n'existe PAS dans AutonomyEngine. Retourne toujours "standard". Le thème "aspirations" du soliloque ne sera JAMAIS sélectionné.
- **Fix proposé** : Utiliser `self_awareness.compute_strategic_mode()`.

### Mo03 — Soliloque : coût sous-évalué (2pt pour 7 appels LLM)
- **Fichier** : `config/resource_costs.json:46-49`
- **Description** : `SOLILOQUE_INTERNE` coûte 2pt mais génère 4 échanges × 2 appels = ~7 appels LLM. Devrait être 4-6pt.
- **Fix proposé** : Augmenter à 5pt dans resource_costs.json.

### Mo04 — Roadmap : `neural_tissue` et `vision_agent` mal catégorisés
- **Fichier** : `config/roadmap.json`
- **Description** : `neural_tissue` et `vision_agent` sont marqués `in_progress` alors que les fichiers existent et fonctionnent. Doublon conceptuel `workspace` vs `global_workspace`.
- **Fix proposé** : Mettre à jour les statuts. Fusionner ou clarifier workspace/global_workspace.

### Mo05 — `get_learning_insights()` jamais appelé (dead code)
- **Fichier** : `core/experience_registry.py:134-168`
- **Description** : La méthode détecte des patterns d'échec récurrents mais n'est appelée nulle part en production. Seul `get_failure_summary()` est utilisé.
- **Fix proposé** : Injecter les insights dans le prompt Evolution Phase 3.

### Mo06 — Strategist : prompt sans limite de taille
- **Fichier** : `Agents/strategist_agent.py:48-63`
- **Description** : Même problème que M04 (coder) — le context est injecté sans limite, risquant de pousser les instructions importantes hors de la fenêtre du LLM.
- **Fix proposé** : Tronquer le contexte avec une limite raisonnable (3000 chars).

### Mo07 — Default cost inconsistant dans `_should_veto` SHED
- **Fichier** : `core/autonomy_engine.py:1367`
- **Description** : `RESOURCE_COSTS.get(intent, 3)` utilise un default de 3 alors que partout ailleurs c'est 2. Un intent inconnu est plus facilement bloqué par SHED.
- **Fix proposé** : Harmoniser à 2.

### Mo08 — `_execute_forced_routine()` ne gère pas le fallback DROPZONE_SCAN vide
- **Fichier** : `core/autonomy_engine.py:1293-1315`
- **Description** : Quand le loop_breaker force DROPZONE_SCAN et la dropzone est vide, la routine forced ne fait pas le fallback YouTube/veille — résultat inutile.
- **Fix proposé** : Extraire la logique veille YouTube en méthode partagée.

### Mo09 — Doublon conceptuel workspace/global_workspace dans la roadmap
- **Fichier** : `config/roadmap.json:262-371`
- **Description** : `planned.workspace` (Phase 4) et `planned.global_workspace` (Phase 7) couvrent le même concept (broadcasting, blackboard partagé).
- **Fix proposé** : Fusionner en un seul module avec des phases incrémentales.

---

## MINEURS (18) — Fonctionnels mais à nettoyer

### m01 — 17 `except Exception: pass` dans evolution_agent.py
- **Fichier** : `Agents/evolution_agent.py` (17 occurrences)
- **Description** : Principalement autour de ExperienceRegistry et bus publish. Le debug est difficile.
- **Fix** : Remplacer par `except Exception as e: logger.debug(...)` au minimum.

### m02 — Researcher, Writer, Infra : pas de guardrail prompt_templates
- **Fichiers** : 3 agents
- **Description** : Ces agents n'utilisent aucun guardrail de `prompt_templates.py`.
- **Fix** : Injecter au minimum `AUTONOMY_GUARDRAIL` pour le researcher et writer.

### m03 — InfraAgent : `system_instructions` jamais utilisé en mode expert
- **Fichier** : `Agents/infra_agent.py:44-46`
- **Description** : En mode expert, `super().process_task()` ignore `self.system_instructions`.
- **Fix** : Passer les instructions dans le prompt ou overrider la méthode.

### m04 — Security : guardrail désactivé silencieusement si exception
- **Fichier** : `Agents/security_agent.py:67-71`
- **Fix** : Logger un warning si `get_project_structure()` échoue.

### m05 — Architect : recall de jurisprudence avec `context[:50] + " ERROR"`
- **Fichier** : `Agents/architect_agent.py:123`
- **Description** : Les 50 premiers chars du code + " ERROR" ne forment pas une requête RAG pertinente.
- **Fix** : Utiliser la mission comme requête ou un résumé heuristique.

### m06 — Factory : ARTIFACT_FAILED avalé silencieusement
- **Fichier** : `Agents/factory_agent.py:273-274`
- **Description** : Si la publication `ARTIFACT_FAILED` échoue, le `pending_deploy` peut rester bloqué.
- **Fix** : Logger l'erreur.

### m07 — Evolution : `_VALID_TARGET_PREFIXES` ne couvre pas `tests/`, `config/`, `static/`
- **Fichier** : `Agents/evolution_agent.py:72`
- **Fix** : Ajouter `"tests/"` si on veut que l'évolution puisse améliorer les tests.

### m08 — Vision : `force_local=True` passé au researcher (surprenant)
- **Fichier** : `Agents/vision_agent.py:93`
- **Fix** : Documenter pourquoi ou retirer si non nécessaire.

### m09 — Formatter : troncation [:2000] dans le path standard mais pas le bypass
- **Fichier** : `Agents/formatter_agent.py:119-143 vs 154-167`
- **Fix** : Harmoniser les deux chemins.

### m10 — `SOLILOQUE_COMPLETE` publié via bus mais aucun souscripteur
- **Fichier** : `core/soliloque.py:179`
- **Fix** : Connecter hippocampus ou cardiac à cet événement.

### m11 — `TISSUE_PATTERN_EMERGED` publié mais jamais souscrit
- **Fichier** : `core/neural_tissue.py:414`
- **Fix** : Connecter self_awareness pour exploiter les patterns émergents.

### m12 — `SANDBOX_TEST_PASS/FAIL` publiés mais jamais souscrits
- **Fichier** : `Agents/evolution_agent.py:1103,1120`
- **Fix** : Connecter dopamine_system (reward sur test pass, penalty sur fail).

### m13 — `NEURAL_COMPILED` publié mais jamais souscrit
- **Fichier** : `core/neural_compiler.py:813`
- **Fix** : Connecter hippocampus ou self_awareness.

### m14 — `ROADMAP_MODULE_COMPLETED/STARTED` publiés mais jamais souscrits
- **Fichier** : `core/roadmap_engine.py:232,234`
- **Fix** : Connecter hippocampus et dopamine.

### m15 — Cardiac init en dernier (20e) — organes inertes pendant le startup
- **Fichier** : `main.py:356`
- **Fix** : Déplacer `heart.init()` plus tôt dans la séquence (après psyche et awareness).

### m16 — Repetition penalty + cooldown temporel se cumulent (-8.0)
- **Fichier** : `core/autonomy_engine.py:243-268`
- **Fix** : Réduire le cumul max ou ajouter un cap combiné.

### m17 — Soliloque : persistance non atomique
- **Fichier** : `core/soliloque.py:531-545`
- **Fix** : Utiliser le pattern tmp + `os.replace()`.

### m18 — Soliloque : pas un vrai singleton (pas de `__new__`)
- **Fichier** : `core/soliloque.py:562-570`
- **Fix** : Aligner sur le pattern `__new__` des autres singletons.

---

## COSMÉTIQUES (8) — Hygiène du code

### c01 — `import re` inutilisé dans coder_agent.py:3
### c02 — Import `bus` au top level dans infra_agent.py:5 (les autres importent localement)
### c03 — Version log "V25.0" dans factory_agent.py:168 (docstring dit V26.0)
### c04 — Version log "V26.1" dans architect_agent.py:92 (docstring dit V26.2)
### c05 — `_PROJECT_MODULES` et `_PROJECT_CONTEXT` statiques vs `get_project_structure()` dynamique
### c06 — `REFLECT_MODEL` hard-codé dans soliloque.py:24 au lieu de Config
### c07 — `AGENT_NUM_CTX` incomplet dans config.py:69-73 (seuls 2 agents + default)
### c08 — Status encodé dans les IDs roadmap.json (fragile si le status change)

---

## STATISTIQUES

| Sévérité | Nombre |
|----------|--------|
| CRITIQUE | 3 |
| MAJEUR | 12 |
| MOYEN | 9 |
| MINEUR | 18 |
| COSMÉTIQUE | 8 |
| **TOTAL** | **50** |

---

## PRIORITÉS POUR LE GRAND MÉNAGE

### Sprint 1 — Critiques + Quick wins (estimé : 1 session)
- [ ] C01 : Augmenter la limite troncation Architecte
- [ ] C02 : Propager un flag `is_pipeline` explicite
- [ ] C03 : Await le dispatch Architecte→Formatter
- [ ] M02 : Ajouter `intent` au payload dispatch (one-liner)
- [ ] M09 : Unifier `OLLAMA_DOWN` → `OLLAMA_UNRESPONSIVE`
- [ ] Mo02 : Fix `_get_strategic_mode()` dans soliloque
- [ ] Mo07 : Harmoniser default cost à 2

### Sprint 2 — Boucles et scoring (estimé : 1 session)
- [ ] M01 : Anti-boucle MAITRISE (compteur forçage + cooldown)
- [ ] M12 : Réduire FOCUS_BONUS_PRIMARY (6.0 → 4.0)
- [ ] Mo01 : Decay error_streak ≥3
- [ ] Mo03 : Coût soliloque 2pt → 5pt
- [ ] m16 : Cap cumul repetition+cooldown

### Sprint 3 — Troncations et guardrails (estimé : 1 session)
- [ ] M03 : Augmenter/supprimer troncation Formatter
- [ ] M04 : Tronquer le context AVANT le guardrail dans Coder
- [ ] Mo06 : Limite taille context Strategist
- [ ] M05 : Extraire `_contains_python_code()` en utilitaire
- [ ] m02 : Ajouter guardrails pour researcher/writer

### Sprint 4 — Factory et sécurité (estimé : 1 session)
- [ ] M07 : Fix `_resolve_smart_path` (chemins absolus)
- [ ] M08 : Fix sandboxing startswith (resolve vs project_root)
- [ ] M10 : Import Config avec fallback dans infra_agent
- [ ] M06 : Clarifier double dispatch Architect

### Sprint 5 — Catalogue et apprentissage (estimé : 1 session)
- [ ] M11 : Déverrouillage automatique specs failed (7 jours)
- [ ] Mo05 : Brancher `get_learning_insights()` dans le pipeline
- [ ] Mo04 : Mettre à jour roadmap.json

### Sprint 6 — Événements et connexions (estimé : 1 session)
- [ ] m10-m14 : Connecter les événements orphelins utiles
- [ ] m12 : SANDBOX_TEST_PASS/FAIL → dopamine
- [ ] m15 : Déplacer cardiac init plus tôt

### Sprint 7 — Nettoyage cosmétique (estimé : 30 min)
- [ ] c01-c08 : Imports inutiles, versions, constantes
- [ ] m17-m18 : Soliloque singleton + persistance atomique
- [ ] m01 : Remplacer `except: pass` par `except: logger.debug()`
