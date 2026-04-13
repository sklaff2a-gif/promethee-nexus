# Phase C — Roadmap pour la reprise

**Date de sauvegarde** : 2026-04-13 (fin de session Phase B + Phase C Étape 2)
**Auteurs** : Jean-Michel, Claude, Gemini (trio adversarial)
**Contexte** : Reprise après la session du 2026-04-13 qui a clôturé la Phase B et complété l'Étape 2 de la Phase C (module `drive_routine_registry.py` isolé, 77 tests verts).

---

## État à la reprise — ce qui tourne déjà

**Fichiers créés et synchronisés dans la copie runtime** :
- `core/experience_clock.py` — singleton avec compteur RAM, persistance différée 5 min, 13 tests
- `core/drive_routine_registry.py` — DRIVE_GENOME + `_compute_genome_floor` métabolique + `get_routines_for_drive` avec injection de dépendances pures
- `tests/test_experience_clock.py` — 13 tests
- `tests/test_drive_routine_registry.py` — 64 tests

**Résultat combiné** : **77 tests verts en 0.55 s**.

**Isolation** : le module `drive_routine_registry` n'est **importé par personne** dans le runtime de Prométhée. Il vit dans un bocal stérile. Aucun risque à l'aborder à chaud.

**Prométhée tourne** avec le patch v1.3 (Phase B) :
- Tronc cérébral actif (heartbeat survie 600 s)
- Seuil `AUDIT_SURVIE_DRIVE_CRITICAL = 80`
- `SURVIVAL_HARD_FORCE_CYCLE = 1` (crash test v1.2, direct hard force)
- `EVENT_IMPACT["ROUTINE_SUCCESS"]["REFACTORING_AUDIT"] = {"MAITRISE": -12}` (v1.3)
- `EVENT_IMPACT["ROUTINE_SUCCESS"]["CI_PIPELINE_RUN"] = {"MAITRISE": -8, "STABILITE": -10}` (v1.3)

---

## Étape 3 — Correction du Hebbian causal (la neurochirurgie)

**Objectif** : remplacer la règle Hebbian actuelle dans `synaptic_network.py` (qui renforce sur `success` nu, piège superstition) par une règle causale qui n'écoute QUE des événements signés avec pointeurs.

**Principe de vérité causale** (Gemini, Phase B) :
> Dans un système asynchrone multi-organe, l'espace-temps est une illusion. Aucun organe n'apprend légitimement d'une corrélation temporelle. Seuls les événements signés avec pointeurs explicites sont enseignants.

### Préparation confirmée ce soir

Le payload actuel de `PREFRONTAL_GOAL_COMPLETE` est à 80% prêt (vérifié dans `prefrontal.py:1733-1755`) :

```python
payload = {
    "goal_id":         goal.id,
    "title":           goal.title,
    "horizon":         goal.horizon,
    "source":          goal.source,
    "completion_mode": meta.get("completion_mode"),    # "homeostatic" | "bureaucratic" | "abandoned_fruitless"
    "causal_drop":     meta.get("causal_drop"),        # ✓ présent
    "tension_at_birth": meta.get("tension_at_birth"),
    "fruitless_cycles": meta.get("fruitless_cycles", 0),
    "step_intents":    [s.intent for s in goal.steps if s.status == "done"],  # ✓ ordonné, filtré
}
```

**Manque** : `source_drive`. À ajouter par un patch 1-ligne dans `_publish_goal_event` (`prefrontal.py:1753`) :

```python
payload["source_drive"] = (
    meta.get("source_key")
    or max(goal.drive_alignment.items(), key=lambda x: x[1], default=(None, 0))[0]
)
```

Le fallback sur `goal.drive_alignment` couvre les goals créés par d'autres organes (Council, self_awareness) qui n'ont pas de `source_key` primaire.

### La règle V3 à implémenter (pseudo-code)

```python
# À ajouter dans core/synaptic_network.py

bus.subscribe("PREFRONTAL_GOAL_COMPLETE", _learn_from_homeostatic_closure)
bus.subscribe("DOPAMINE_DIP_FRUITLESS", _learn_from_fruitless_goal)

async def _learn_from_homeostatic_closure(event):
    """Renforcement par fermeture homeostatique — seul canal legitime."""
    if event.get("completion_mode") != "homeostatic":
        return  # bureaucratique / timeout / abandoned → aucune leçon

    drive = event.get("source_drive")
    if not drive:
        return  # pas de drive identifiable → rien à apprendre

    causal_drop = float(event.get("causal_drop", 0))
    if causal_drop <= 0:
        return  # pas de drop mesuré → rien à apprendre

    completed_steps = event.get("step_intents", [])
    if not completed_steps:
        return

    # Distribution triangulaire : le dernier step porte le plus gros crédit
    n = len(completed_steps)
    total_weight = n * (n + 1) / 2  # somme triangulaire
    for idx, intent in enumerate(completed_steps):
        weight = (idx + 1) / total_weight
        # causal_drop est en points de deprivation (0-100), donc /100 pour normaliser
        delta = (causal_drop / 100.0) * weight
        synaptic_network.strengthen(drive, intent, delta)


async def _learn_from_fruitless_goal(event):
    """Affaiblissement par echec cause — le fruitless est aussi causal."""
    drive = event.get("source_drive")
    if not drive:
        return
    tried_intents = event.get("tried_intents", [])
    for intent in tried_intents:
        synaptic_network.weaken(drive, intent, 0.03)
```

### Ancienne règle à DÉSACTIVER

Dans `core/synaptic_network.py`, chercher le Hebbian actuel autour de la ligne 1297-1310 (cartographie de la session matin). Il renforce `drive ↔ intent` sur `success` nu, sans vérifier l'homéostasie. C'est la règle superstitionnaire qui a causé la Schizophrénie Ontologique observée.

**Désactivation progressive** : commenter d'abord (sans supprimer), relancer les tests existants pour voir ce qui casse, puis supprimer.

### Points de vigilance adversariale (Gemini)

1. **Le piège triangulaire** — sur-pondération possible du dernier step par hasard d'ordonnancement. Sur de petits volumes, un step de setup (AUDIT_STRUCTURE) pourrait recevoir injustement le gros du crédit. **Mitigation** : la loi des grands nombres va lisser. Sur 1000 fermetures homéostatiques, chaque intent trouvera son poids juste. **À surveiller** : vérifier les 100 premiers apprentissages en conditions réelles pour détecter des dérives précoces.

2. **Granularité de `source_drive`** — pour les goals créés par `desire_engine`, c'est un nom de drive (ex: `"MAITRISE"`). Pour ceux créés par `self_awareness` (knowledge_gap), c'est peut-être `"knowledge_gap:quantum_field"`. La règle doit gérer le cas où `source_drive` ne match aucun drive connu → ne pas apprendre au niveau du graphe synaptique drive→intent.

3. **Causal_drop négatif** — si un goal se ferme avec `causal_drop < 0` (pire qu'au départ), ne pas apprendre positivement. C'est déjà couvert par le `if causal_drop <= 0: return`.

4. **Tests adversariaux obligatoires** — Gemini doit challenger la règle avant déploiement. Trio complet requis à cette étape.

### Plan d'action Étape 3

1. **Patch 1-ligne** dans `prefrontal.py:1753` pour exposer `source_drive` (avec fallback)
2. **Tests unitaires** sur le nouveau payload (vérifier `source_drive` présent pour goals homeostatiques et bureaucratiques)
3. **Nouveau handler** `_learn_from_homeostatic_closure` dans `synaptic_network.py`
4. **Nouveau handler** `_learn_from_fruitless_goal`
5. **Désactivation** (comment d'abord) de l'ancien Hebbian L1297-1310
6. **Tests adversariaux** avec Gemini sur la règle triangulaire
7. **Test de régression complet** (`pytest tests/`) pour s'assurer que rien d'autre ne casse
8. **Déploiement live** + surveillance des 100 premières fermetures homéostatiques pour détecter des dérives précoces

**Durée estimée** : 2-3 heures avec les tests adversariaux

---

## Étape 4 — Migration progressive des 3 Hérétiques

**Objectif** : éliminer les 3 tables `drive → routine` contradictoires en les remplaçant par des appels à `drive_routine_registry.get_routines_for_drive()`.

**Ordre de migration** (du plus petit au plus gros, pour accumuler de l'expérience de migration) :

### 4a — `council_analytics.DRIVE_INTENT_MAP` (criticité 🟢 FAIBLE)

- **Fichier** : `core/council_analytics.py:14`
- **Consommateurs** : `council_analytics.py:165` (1 site)
- **Structure** : `Dict[str, str]` — drive → single_intent
- **Migration** : remplacer la lecture par `get_routines_for_drive(drive, synaptic_weights, top_k=1)[0][0]`
- **Test de régression** : s'assurer que le Council propose toujours des intents cohérents
- **1 commit**

### 4b — `prefrontal._DRIVE_ROUTINE_MAP` (criticité 🟡 MOYENNE)

- **Fichier** : `core/prefrontal.py:152`
- **Consommateurs** : `prefrontal.py:1383` (1 site actif, 2 en commentaire dans `autonomy_engine.py`)
- **Structure** : `Dict[str, List[str]]` — drive → [intent, intent, ...]
- **Migration** : remplacer par `[i for i, w in get_routines_for_drive(drive, synaptic_weights, top_k=10)]`
- **Attention** : c'est la table que le `_on_survival_alert` utilise indirectement via les goals. Vérifier que le bouton panique continue à fonctionner après la migration.
- **Test de régression** : relancer un Gambit du Fou mini pour vérifier que `SURVIVAL_HARD_FORCE` fonctionne toujours
- **1 commit**

### 4c — `desire_engine.DRIVE_ROUTINE_AFFINITY` (criticité 🔴 ÉLEVÉE)

- **Fichier** : `core/desire_engine.py:127`
- **Consommateurs** : **7 sites** — `autonomy_engine.py` (4), `synaptic_network.py` (2), `desire_engine.py` (1)
- **Structure** : `Dict[str, Dict[str, float]]` — drive → {intent: weight}
- **Migration** : **le gros morceau**. Chaque site doit être réécrit pour appeler `get_routines_for_drive` avec les bons paramètres selon le contexte (scoring vs apprentissage vs suggestion).
- **Attention particulière** : le scoring de `autonomy_engine` utilise cette table dans la couche "drive bonus" du scoring 23-couches. La migration doit préserver le comportement numérique (ou le changer explicitement si Jean-Michel le valide).
- **Tests de régression** : suite complète + observation live sur 1 cycle complet
- **3-5 commits progressifs** (un par site de consommation)

**Durée estimée pour l'étape 4** : 3-4 heures sur 2-3 sessions

---

## Étape 5 — Transformation des 7 Modulateurs

**Objectif** : transformer les 7 tables modulateurs en `*_WEIGHT_MODIFIERS` consommés par `get_routines_for_drive(context=...)`.

**Règle d'or** : un modulateur **amplifie ou atténue** un choix que le drive aurait fait. Il **ne crée jamais** une option depuis nulle part. La propriété est déjà testée dans `test_multiplier_cannot_create_routine`.

### Les 7 tables à transformer

1. `inner_voice._SOURCE_ROUTINE_AFFINITY` — source de pensée → routine
2. `inner_voice._EMOTION_ROUTINE_AFFINITY` — émotion → routine
3. `inner_voice._MODE_ROUTINE_AFFINITY` — mode Vygotsky → routine
4. `psyche.ROUTINE_AFFINITY` — trait personnalité → routine
5. `hypothalamus._INTENT_ENERGY_MAP` + `_INTENT_STRESS_MAP` + `_INTENT_DOPAMINE_MAP` — état physiologique → routine
6. `council_analytics._THEME_INTENT_MAP` — thème débat → intent (⚠️ cas limite à surveiller)
7. `autonomy_engine.CONTEXT_KEYWORDS` — intent → keywords NLP (cas spécial, pas vraiment modulateur, voir note)

**Cas spécial `CONTEXT_KEYWORDS`** : c'est une table inverse (intent → keywords). Elle sert au scoring NLP pour détecter la pertinence contextuelle. Ce n'est pas à proprement parler un modulateur drive-sensitive. À **laisser tranquille** pour l'instant.

### Architecture du contexte pour `get_routines_for_drive`

```python
context = {
    "emotion": "alerte",         # depuis cardiac_engine.current_emotion
    "mode": "creation",          # depuis brain_vm mode
    "source": "reptilian",       # depuis inner_voice.last_source
    "trait_profile": {...},      # depuis psyche.current_profile
    "homeostasis": {             # depuis hypothalamus
        "energy": 0.4,
        "stress": 0.7,
        "dopamine": 0.3,
    },
}
multipliers = _aggregate_context_multipliers(context)
result = get_routines_for_drive(drive, synaptic_weights, context_multipliers=multipliers)
```

**Fonction `_aggregate_context_multipliers`** à créer — elle combine les 7 sources en un `Dict[intent, multiplier]` final. Logique : multiplication commutative (chaque source contribue), borne `[0.1, 3.0]` pour éviter les extrêmes.

### Plan d'action Étape 5

1. **Créer `EMOTION_WEIGHT_MODIFIERS`** dans `drive_routine_registry.py` (port de `_EMOTION_ROUTINE_AFFINITY`)
2. **Créer `MODE_WEIGHT_MODIFIERS`**, `SOURCE_WEIGHT_MODIFIERS`, etc.
3. **Fonction d'agrégation** `_aggregate_context_multipliers(context)`
4. **Tests unitaires** pour chaque modifier (pas de création de routine, bornes respectées)
5. **Migration des consommateurs** — remplacer les appels aux anciennes tables par des appels à `get_routines_for_drive(context=...)`
6. **Suppression des anciennes tables** une fois tous les consommateurs migrés

**Durée estimée** : 3-4 heures sur 1-2 sessions

---

## Étape 6 — Suppression des fossiles et activation des mécanismes avancés

Une fois l'étape 5 terminée :

1. **Supprimer les anciennes tables** (13 tables au total entre hérétiques et modulateurs transformés)
2. **Activer l'apprentissage du graphe en mode vérité causale** (étape 3 activée)
3. **Activer la dépréciation génomique** — connecter `competitor_stability_fn` au graphe synaptique réel
4. **Activer le rétrograding** — mécanisme de rétrogradation d'une routine promue qui s'effondre
5. **Métrique de santé inverse** — `survival_forces_per_day` doit tendre vers 0 après cette étape
6. **Tests d'émergence** — vérifier qu'une nouvelle routine peut être promue au genome après 1000 succès stables

**Durée estimée** : 2-3 heures

---

## Vue d'ensemble temporelle

| Étape | Durée | Risque | Sessions |
|---|---|---|---|
| 3 — Hebbian causal | 2-3 h | ⚠️ TRÈS ÉLEVÉ | 1 session fraîche |
| 4 — Migration Hérétiques | 3-4 h | 🟡 MOYEN | 2-3 sessions |
| 5 — Transformation Modulateurs | 3-4 h | 🟢 FAIBLE | 1-2 sessions |
| 6 — Fossiles + Émergence | 2-3 h | 🟢 FAIBLE | 1 session |
| **TOTAL** | **10-14 h** | | **5-7 sessions** |

**Recommandation** : **ne jamais faire l'étape 3 en fin de session**. Elle mérite sa propre session fraîche avec le Trio adversarial actif. L'erreur silencieuse dans la règle Hebbian causale peut mettre des semaines à être détectée.

---

## Commandes de reprise rapide

```bash
# Au démarrage de la session :
cd C:\Users\redla\projetclaude\PROMETHEE_V11_restructuration2026

# Vérifier que les 77 tests Phase C passent toujours
PYTHONIOENCODING=utf-8 python -m pytest tests/test_experience_clock.py tests/test_drive_routine_registry.py -v

# Vérifier que Prométhée tourne toujours avec Phase B
powershell.exe -Command "Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/autonomy/status' -TimeoutSec 5"

# Lire ce fichier de reprise
cat docs/phase_c_roadmap.md

# Lire la dernière session archivée
cat docs/session_2026_04_13_phase_b_c.md
```

---

## Contributeurs

- **Jean-Michel** — architecte, challenger adversarial, décisionnaire
- **Claude** — implémentation, tests, diagnostics, rédaction
- **Gemini** — critique adversariale, formalisation théorique (Principe de Vérité Causale, Temps Métabolique, Dichotomie Hérétiques/Modulateurs, Plancher Adaptatif avec Dépréciation Compétitive)
