# Journal des Councils

Ce fichier est maintenu automatiquement par le moteur d'autonomie et curé manuellement.
- **Conserver** les sujets intéressants jusqu'à implémentation
- **Supprimer** les sujets inappropriés ou hors périmètre
- **Archiver** (supprimer) les sujets implémentés

---

## [2026-02-17 01:25] Event Bus — Dead-letter queue ✅ IMPLÉMENTÉ (2026-02-19)

**Participants** : architect, coder, infra | **Tours** : 3 | **Consensus** : oui

**Proposition** :
- Dead-letter queue pour les événements échoués (stockage + retraitement automatique)

**Fichiers cibles** : `core/event_bus/bus.py`
**Verdict** : Implémenté — DeadLetter dataclass, DLQ in-memory (max 100, FIFO), inspection + retry.

---

## [2026-02-19 15:04] Le Researcher a trouvé des techniques d'optimisation des ressources pour les age

**Participants** : infra, strategist, architect | **Tours** : 3 | **Consensus** : oui

**Propositions clés** :
  - **Fichier modifié** : `core/memory/vector_store.py` (fonction `load_model` ligne ~45)
  - Ajouter une logique pour convertir les embeddings en `np.float32` avant de les stocker dans ChromaDB.
  - **Raison** : ChromaDB attend des `float32`, et la conversion naïve évite de modifier ChromaDB (hors-périmètre).
  - **Fichier modifié** : `core/memory/vector_store.py` (fonction `load_model` ligne ~45)
  - Ajouter une vérification du format du fichier (`.gguf`) et charger le modèle quantifié.

**Fichiers cibles** : `core/memory/vector_store.py`
**Verdict** : (à curé manuellement)

---

## [2026-02-19 23:20] Le Researcher a identifié des stratégies de scalabilité autonome. Comment Promét

**Participants** : evolution, strategist, coder | **Tours** : 4 | **Consensus** : oui

**Propositions clés** :
  **Problème potentiel
  **Fichier concerné** : `core/autonomy_engine.py`
  - Ajout d’un dictionnaire `PROMPT_SIMPLIFICATION_THRESHOLDS` (configurable via un simple fichier `.json` ou directement 
  - La fonction `adjust_prompt(errors: int, metrics: dict)` est modifiée pour :
  - `core/autonomy_engine.py`

**Fichiers cibles** : `Agents/base_agent.py`, `core/autonomy_engine.py`, `core/evolution_feedback.py`
**Verdict** : (à curé manuellement)
