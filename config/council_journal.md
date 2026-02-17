# Journal des Councils

Ce fichier est maintenu automatiquement par le moteur d'autonomie et curé manuellement.
- **Conserver** les sujets intéressants jusqu'à implémentation
- **Supprimer** les sujets inappropriés ou hors périmètre
- **Archiver** (supprimer) les sujets implémentés

---

## [2026-02-17 01:25] Event Bus — Patterns de communication

**Participants** : architect, coder, infra | **Tours** : 3 | **Consensus** : oui

**Propositions clés** :
- Dead-letter queue pour les événements échoués (stockage + retraitement)
- Rate limiting via sémaphore dans `core/event_bus/bus.py`
- File de priorité pour les événements critiques

**Fichiers cibles** : `core/event_bus/bus.py`, `core/event_bus/subscriber.py`
**Verdict** : Intéressant mais le bus actuel fonctionne bien pour 10 agents. Dead-letter queue serait utile pour le debug.

---

## [2026-02-17 03:30] Résilience et sécurité

**Participants** : security, architect, strategist | **Tours** : 4 | **Consensus** : oui

**Propositions clés** :
- Regex anti-patterns dangereux (Base64, eval, cmd /c) dans les réponses des agents
- Vérification d'intégrité SHA256 du security_agent au démarrage
- Sanitisation d'environnement pour subprocess (`_clean_env()`)

**Fichiers cibles** : `Agents/security_agent.py`, `core/capabilities/dropzone_indexer.py`
**Verdict** : Les regex anti-patterns sont simples et utiles. Le SHA256 est faisable. Le sandbox subprocess est théorique (Prométhée n'exécute pas de commandes).

---

## [2026-02-17 07:34] Gestion du budget et priorisation

**Participants** : strategist, evolution | **Tours** : 3 | **Consensus** : oui | **Spec** : COUNCIL-10169

**Propositions clés** :
- ~~Fichier `config/resource_costs.json` avec coûts par agent~~ IMPLÉMENTÉ (2026-02-17)
- ~~Vérification budget avant dispatch~~ IMPLÉMENTÉ (2026-02-17)
- ~~Sémaphore Ollama (max 2 concurrents)~~ IMPLÉMENTÉ (2026-02-17)

**Fichiers cibles** : `core/autonomy_engine.py`, `core/base_agent.py`, `config/resource_costs.json`
**Verdict** : Implémenté dans le commit post-analyse.

---

## [2026-02-17 12:12] Scalabilité autonome — Stabilité des poids

**Participants** : evolution, strategist, coder | **Tours** : 3 | **Consensus** : oui

**Propositions clés** :
- ~~Clamping des poids adaptatifs dans [-10, +5]~~ IMPLÉMENTÉ (2026-02-17)
- Logging des deltas de poids dans evolution_agent (traçabilité)
- Persistance des poids stables (`stable_weights.json`) pour rollback

**Fichiers cibles** : `core/autonomy_engine.py`, `Agents/evolution_agent.py`
**Verdict** : Clamping implémenté. Le logging des deltas et stable_weights.json restent à faire.
