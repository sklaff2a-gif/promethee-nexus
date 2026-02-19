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
