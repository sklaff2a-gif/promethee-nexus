# Journal des Councils

Ce fichier est maintenu automatiquement par le moteur d'autonomie et curé manuellement.
- **Conserver** les sujets intéressants jusqu'à implémentation
- **Supprimer** les sujets inappropriés ou hors périmètre
- **Archiver** (supprimer) les sujets implémentés

---

## [2026-02-17 01:25] Event Bus — Dead-letter queue

**Participants** : architect, coder, infra | **Tours** : 3 | **Consensus** : oui

**Proposition** :
- Dead-letter queue pour les événements échoués (stockage + retraitement automatique)

**Fichiers cibles** : `core/event_bus/bus.py`
**Verdict** : Utile pour le debug. Le bus actuel fonctionne mais les événements échoués sont silencieusement perdus.

---

## [2026-02-17 03:30] Regex anti-patterns dans les réponses agents

**Participants** : security, architect, strategist | **Tours** : 4 | **Consensus** : oui

~~**Propositions** : Regex anti-patterns dangereux (eval, exec, Base64, cmd /c)~~ IMPLÉMENTÉ (2026-02-17)

---

## [2026-02-17 12:12] Stabilité des poids adaptatifs

**Participants** : evolution, strategist, coder | **Tours** : 3 | **Consensus** : oui

~~Clamping des poids adaptatifs [-10, +5]~~ IMPLÉMENTÉ (2026-02-17)

**Propositions restantes** :
- Logging des deltas de poids dans evolution_agent (traçabilité)
- Persistance des poids stables (`stable_weights.json`) pour rollback

**Fichiers cibles** : `core/autonomy_engine.py`, `Agents/evolution_agent.py`

---

## [2026-02-17 18:27] Arguments scorés dans les débats Council

**Participants** : strategist, coder, writer | **Tours** : 3 | **Consensus** : oui

~~**Proposition** : Scoring des arguments (fichiers cités, actions, code, longueur)~~ IMPLÉMENTÉ (2026-02-17)

---
