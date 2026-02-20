# Journal des Councils

Ce fichier est maintenu automatiquement par le moteur d'autonomie et curé manuellement.
- **Conserver** les sujets intéressants jusqu'à implémentation
- **Supprimer** les sujets inappropriés ou hors périmètre
- **Archiver** (supprimer) les sujets implémentés

---

## [2026-02-20 07:56] asyncio.Lock mémoire partagée

**Participants** : researcher, evolution, coder | **Tours** : 3 | **Consensus** : oui

**Propositions clés** :
  - `asyncio.Lock` pour thread-safety de la mémoire partagée
  - Résout le problème de mutation non contrôlée du memory store
  - Overhead négligeable sur un seul PC

**Fichiers cibles** : `core/memory/` (package mémoire, pas un fichier unique)
**Verdict** : À implémenter — difficulté 2, protéger les accès concurrents ChromaDB

---

## [2026-02-20 13:58] Module centralisé anti-injection prompt

**Participants** : security, architect, strategist | **Tours** : 3 | **Consensus** : oui

**Propositions clés** :
  - Module centralisé pour validation anti-injection
  - Décodage URL, détection patterns malveillants, sanitisation avant envoi à Ollama
  - Intégration dans chaque agent via appel au module avant `generate_content()`

**Fichiers cibles** : `core/prompt_templates.py` (étendre le module existant), agents concernés
**Verdict** : À implémenter — évolution logique de SEC-002 (injection Router). Centraliser la détection pour tous les agents.

---

## [2026-02-20 20:00] Paramètre ctx_size configurable Ollama

**Participants** : infra, strategist, architect | **Tours** : 3 | **Consensus** : oui

**Propositions clés** :
  - Rendre `num_ctx` (taille contexte Ollama) configurable par agent ou par type de tâche
  - Permet d'optimiser VRAM/vitesse selon la complexité du prompt
  - Valeur par défaut 2048, réduire à 1024 pour les tâches simples (routage, classification)

**Fichiers cibles** : `config.py` (paramètre Ollama), `core/base_agent.py` (passage du paramètre)
**Verdict** : À implémenter — difficulté 2, bon rapport effort/impact sur les performances locales

---

*Curation du 2026-02-20 — 4 councils supprimés :*
- *[15:22] threading.Lock debates — approche incorrecte (threading.Lock dans asyncio)*
- *[17:43] Budget priorisation — trop vague, aucune action concrète*
- *[19:11] Scalabilité métriques — vague, pas d'implémentation extractible*
- *[20:52] asyncio.Queue event_bus — redondant avec PERF-006 (Batch bus publish)*
