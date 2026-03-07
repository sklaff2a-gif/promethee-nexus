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

### M01 — ~~Boucle MAITRISE-REFACTOR obsessionnelle~~ **FIXED** (pré-2026-03-07)
- **Fix** : Compteurs `_drive_force_counts` (fenêtre 5 cycles, max 3 forçages) + `_drive_force_total` (plafond 10/session).

### M02 — ~~Payload dispatch sans clé `intent`~~ **FIXED** (pré-2026-03-07)
- **Fichier** : `core/autonomy_engine.py`
- **Fix** : `"intent": intent` est déjà présent dans tous les payloads dispatch.

### M03 — ~~Formatter : troncation perd le code~~ **FIXED** (2026-03-07, via C01)
- **Fix** : Résolu par `Config.get_max_content_chars("formatter")` — troncation dynamique (14384 chars au lieu de 6000).

### M04 — ~~Coder : prompt sans limite de taille → guardrail perdu~~ **FIXED** (2026-03-07, via C01)
- **Fix** : `Config.get_max_content_chars("coder", prompt_overhead=3000)` tronque AVANT le guardrail (29768 chars).

### M05 — ~~`_contains_python_code()` dupliqué sans sync~~ **FIXED** (pré-2026-03-07)
- **Fix** : Extrait dans `core/code_utils.contains_python_code()`. Architect et Orchestrator délèguent.

### M06 — ~~Double chemin dispatch Architect~~ **FIXED** (2026-03-07)
- **Fix** : Flag `routed_internally: True` dans les réponses Architect. Bridge vérifie le flag en priorité (plus de string matching fragile).

### M07 — ~~Factory : `_resolve_smart_path` dépend du cwd~~ **FIXED** (pré-2026-03-07)
- **Fix** : Utilise déjà `os.path.join(self.project_root, potential_path)` pour les vérifications.

### M08 — ~~Factory : sandboxing `startswith()` contournable~~ **FIXED** (2026-03-07)
- **Fix** : `sandbox_prefix` avec trailing `os.sep` — empêche match sur dossier homonyme (ex: `project_EVIL/`).

### M09 — ~~`OLLAMA_DOWN` vs `OLLAMA_UNRESPONSIVE`~~ **FIXED** (pré-2026-03-07)
- **Fix** : `OLLAMA_UNRESPONSIVE` est déjà utilisé partout (code_smith, evolution_catalog, base_agent).

### M10 — ~~InfraAgent : import Config sans fallback~~ **FIXED** (pré-2026-03-07)
- **Fix** : `try/except` avec `Config = None` fallback déjà en place.

### M11 — ~~Specs `failed` verrouillées sans déverrouillage~~ **FIXED** (pré-2026-03-07)
- **Fix** : Auto-unlock après 7 jours dans `_select_spec()` — reset attempts/failure_reasons, status → available.

### M12 — ~~FOCUS_BONUS_PRIMARY = 6.0 domine le scoring~~ **FIXED** (pré-2026-03-07)
- **Fix** : `FOCUS_BONUS_PRIMARY = 4.0` (réduit de 6.0).

---

## MOYENS (9) — Fonctionnels mais dégradés

### Mo01 — ~~error_streak de 4 ne décrémente jamais~~ **FIXED** (pré-2026-03-07)
- **Fix** : Decay `-1` par cycle si `error_streak >= 3` (seuil abaissé de 5 à 3).

### Mo02 — ~~Soliloque : `_get_strategic_mode()` référence un attribut inexistant~~ **FIXED** (pré-2026-03-07)
- **Fix** : Utilise déjà `awareness.compute_strategic_mode()` (soliloque.py:523).

### Mo03 — ~~Soliloque : coût sous-évalué~~ **FIXED** (pré-2026-03-07)
- **Fix** : `SOLILOQUE_INTERNE: cost=5` (augmenté de 2 à 5pt).

### Mo04 — ~~Roadmap : `neural_tissue` et `vision_agent` mal catégorisés~~ **FIXED** (pré-2026-03-07)
- **Fix** : Les deux sont maintenant `"status": "implemented"`. `workspace`/`global_workspace` = dépendance incrémentale intentionnelle (Phase 4 → Phase 7).

### Mo05 — ~~`get_learning_insights()` jamais appelé~~ **FIXED** (pré-2026-03-07)
- **Fix** : Appelé dans evolution_agent Phase 1 (stuck_specs) et Phase 3 (recurring_alien → alien_warning dans le prompt).

### Mo06 — ~~Strategist : prompt sans limite de taille~~ **FIXED** (2026-03-07)
- **Fix** : `Config.get_max_content_chars("strategist")` — troncation dynamique (22576 chars).

### Mo07 — ~~Default cost inconsistant dans `_should_veto` SHED~~ **FIXED** (pré-2026-03-07)
- **Fix** : Tous les `RESOURCE_COSTS.get(intent, 2)` utilisent déjà le default 2.

### Mo08 — `_execute_forced_routine()` ne gère pas le fallback DROPZONE_SCAN vide
- **Fichier** : `core/autonomy_engine.py:1293-1315`
- **Description** : Quand le loop_breaker force DROPZONE_SCAN et la dropzone est vide, la routine forced ne fait pas le fallback YouTube/veille — résultat inutile.
- **Fix proposé** : Extraire la logique veille YouTube en méthode partagée.

### Mo09 — ~~Doublon workspace/global_workspace~~ **FIXED** (pré-2026-03-07)
- **Fix** : Pas un doublon — `workspace` (Phase 4) est l'infra technique, `global_workspace` (Phase 7) est la couche conscience. Dépendance incrémentale correcte.

---

## MINEURS (18) — Fonctionnels mais à nettoyer

### m01 — 17 `except Exception: pass` dans evolution_agent.py
- **Fichier** : `Agents/evolution_agent.py` (17 occurrences)
- **Description** : Principalement autour de ExperienceRegistry et bus publish. Le debug est difficile.
- **Fix** : Remplacer par `except Exception as e: logger.debug(...)` au minimum.

### m02 — ~~Researcher, Writer, Infra : pas de guardrail~~ **FIXED** (2026-03-07)
- **Fix** : Researcher et Writer avaient déjà `AUTONOMY_GUARDRAIL`. Ajouté pour Infra (injection dans context avant `super().process_task()`).

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

### m09 — ~~Formatter : troncation dans le path standard~~ **FIXED** (2026-03-07, via C01)
- **Fix** : Résolu par `Config.get_max_content_chars("formatter")` — troncation dynamique harmonisée.

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

### m16 — ~~Repetition penalty + cooldown se cumulent (-8.0)~~ **FIXED** (pré-2026-03-07)
- **Fix** : Cap combiné `min(recency_penalty, 6.0)` — pénalité max plafonnée à -6.0.

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

### Sprint 1 — Critiques + Quick wins ✅ TERMINÉ (2026-03-07)
- [x] C01 : Troncation dynamique `get_max_content_chars()` (architect/formatter/coder)
- [x] C02 : Guard `in` au lieu de `startswith()`
- [x] C03 : Await dispatch Formatter→Factory
- [x] M02 : `intent` déjà dans payload (pré-corrigé)
- [x] M03 : Résolu par C01
- [x] M04 : Résolu par C01
- [x] M09 : `OLLAMA_UNRESPONSIVE` déjà unifié (pré-corrigé)
- [x] Mo02 : `compute_strategic_mode()` déjà utilisé (pré-corrigé)
- [x] Mo07 : Default cost déjà à 2 (pré-corrigé)
- [x] m09 : Résolu par C01

### Sprint 2 — Boucles et scoring ✅ TERMINÉ (2026-03-07, tous pré-corrigés)
- [x] M01 : Anti-boucle MAITRISE (compteurs forçage + cooldown + plafond session)
- [x] M12 : `FOCUS_BONUS_PRIMARY = 4.0` (réduit de 6.0)
- [x] Mo01 : Decay error_streak ≥3 (seuil abaissé de 5 à 3)
- [x] Mo03 : Coût soliloque 2pt → 5pt
- [x] m16 : Cap cumul repetition+cooldown à -6.0

### Sprint 3 — Troncations et guardrails ✅ TERMINÉ (2026-03-07)
- [x] M03 : Résolu par C01 (Sprint 1)
- [x] M04 : Résolu par C01 (Sprint 1)
- [x] Mo06 : `Config.get_max_content_chars("strategist")` — troncation dynamique
- [x] M05 : Déjà extrait dans `core/code_utils.py` (pré-corrigé)
- [x] m02 : Guardrails OK (researcher/writer pré-corrigés, infra ajouté)

### Sprint 4 — Factory et sécurité ✅ TERMINÉ (2026-03-07)
- [x] M07 : Déjà résolu (project_root dans _resolve_smart_path, pré-corrigé)
- [x] M08 : sandbox_prefix avec trailing os.sep (path traversal fix)
- [x] M10 : Déjà résolu (try/except fallback, pré-corrigé)
- [x] M06 : Flag `routed_internally` + vérification dans le Bridge

### Sprint 5 — Catalogue et apprentissage ✅ TERMINÉ (2026-03-07, tous pré-corrigés)
- [x] M11 : Auto-unlock 7 jours déjà en place
- [x] Mo05 : `get_learning_insights()` déjà branché (stuck_specs + recurring_alien)
- [x] Mo04 : Statuts roadmap déjà à jour (implemented)
- [x] Mo09 : workspace/global_workspace = dépendance incrémentale correcte

### Sprint 6 — Événements et connexions (estimé : 1 session)
- [ ] m10-m14 : Connecter les événements orphelins utiles
- [ ] m12 : SANDBOX_TEST_PASS/FAIL → dopamine
- [ ] m15 : Déplacer cardiac init plus tôt

### Sprint 7 — Nettoyage cosmétique (estimé : 30 min)
- [ ] c01-c08 : Imports inutiles, versions, constantes
- [ ] m17-m18 : Soliloque singleton + persistance atomique
- [ ] m01 : Remplacer `except: pass` par `except: logger.debug()`
