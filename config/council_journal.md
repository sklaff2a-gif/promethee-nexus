# Journal des Councils

Ce fichier est maintenu automatiquement par le moteur d'autonomie et curé manuellement.
- **Conserver** les sujets intéressants jusqu'à implémentation
- **Supprimer** les sujets inappropriés ou hors périmètre
- **Archiver** (supprimer) les sujets implémentés

---

## [2026-02-20 07:56] asyncio.Lock dans core/memory.py — Thread-safety mémoire

**Participants** : researcher, evolution, coder | **Tours** : 3 | **Consensus** : oui

**Propositions clés** :
  - `asyncio.Lock` dans `core/memory.py` pour thread-safety de la mémoire partagée
  - Résout le problème de mutation non contrôlée du memory store
  - Overhead négligeable sur un seul PC

**Fichiers cibles** : `core/memory.py`
**Verdict** : (à implémenter)

---

## [2026-02-20 13:58] Module centralisé core/prompt_validation.py — Anti-injection

**Participants** : security, architect, strategist | **Tours** : 3 | **Consensus** : oui

**Propositions clés** :
  - Module centralisé `core/prompt_validation.py` pour validation anti-injection
  - Décodage URL, détection patterns malveillants, sanitisation avant envoi à Ollama
  - Intégration dans chaque agent via appel au module avant `generate_content()`

**Fichiers cibles** : `core/prompt_validation.py`, agents concernés
**Verdict** : (à implémenter)

---

## [2026-02-20 15:22] Le Researcher a trouvé des méthodes pour améliorer les débats entre agents IA. C

**Participants** : strategist, coder, writer | **Tours** : 3 | **Consensus** : oui

**Propositions clés** :
  **Fichier concerné :** `core/orchestrator.py`
  - Déclarer un verrou global `active_debates_lock = threading.Lock()` (standard Python, donc autorisé).
  - Encadrer l’incrémentation et la décrémentation de `active_debates` avec ce verrou :
  - Le problème de *race condition* sur `active_debates` a été résolu avec un `threading.Lock()` et une logique d’attente 
  - La protection du compteur empêche toute surcharge CPU liée à un dépassement de parallélisme.

**Fichiers cibles** : `core/council.py`, `core/orchestrator.py`, `core/performance_utils.py`
**Verdict** : (à curé manuellement)
