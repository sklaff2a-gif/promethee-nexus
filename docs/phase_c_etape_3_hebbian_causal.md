# Phase C — Étape 3 : Correction du Hebbian causal

**Date** : 2026-04-14
**Auteurs** : Jean-Michel, Claude, Gemini (trio adversarial requis avant implémentation)
**Statut** : DRAFT — à challenger par Gemini avant toute ligne de code production
**Cible** : `core/synaptic_network.py`
**Risque** : 🔴 CRITIQUE — une erreur silencieuse dans la distribution du crédit corrompra le learning pendant des jours/semaines sans être détectée

---

## 1. Objectif

Remplacer la règle Hebbian actuelle de `synaptic_network.py` (qui renforce
les liens `drive ↔ intent` sur la base de `status == "success"` nu) par une
règle **causale** qui n'écoute que des événements signés avec pointeurs
explicites, conformément au **principe de vérité causale** formalisé par
Gemini en Phase B (2026-04-13).

> **Principe de vérité causale** (rappel) : Dans un système asynchrone
> multi-organe, l'espace-temps est une illusion. Aucun organe n'apprend
> légitimement d'une corrélation temporelle. Seuls les événements signés
> avec pointeurs explicites sont enseignants.

## 2. Problème observé (diagnostic Phase B)

La règle actuelle de `synaptic_network.py` (lignes ~1297-1310) renforce
le lien `drive ↔ intent` chaque fois qu'une routine se termine avec
`status == "success"`. Conséquences :

1. **Superstition par corrélation temporelle** — un `DOPAMINE_SURGE`
   concomitant venant d'une autre source (compliment humain, salary_payday,
   eureka_moment) peut déclencher un renforcement parasite.

2. **Pseudo-victoires bureaucratiques** — une routine exécutée "avec succès"
   au sens technique (pas de crash) mais qui n'a **pas fait baisser la
   tension upstream** est quand même renforcée. C'est la Loi de Goodhart
   appliquée au graphe synaptique lui-même.

3. **Confusion des drives** — le lien renforcé utilise le "drive candidat"
   actif au moment de l'exécution, pas le drive qui a effectivement généré
   le goal (les deux peuvent diverger en cas de forçage, d'inhibition
   latérale, ou de multi-goals concurrents).

## 3. Solution architecturale — règle V3 causale

### 3.1 Principe

Le graphe synaptique apprend **exclusivement** depuis deux événements
causalement signés, tous deux émis par le préfrontal et le dopamine_system
uniquement sur des fermetures homéostatiques réelles (Fix 1 Phase C validé) :

1. **`PREFRONTAL_GOAL_COMPLETE`** avec `completion_mode == "homeostatic"`
   → renforcement positif
2. **`DOPAMINE_DIP_FRUITLESS`** (alias `PREFRONTAL_GOAL_ABANDONED` avec
   `completion_mode == "abandoned_fruitless"`)
   → extinction du conditionnement

Aucune fenêtre temporelle. Aucune écoute de `DOPAMINE_SURGE` global. Aucun
signal `routine_complete` nu.

### 3.2 Spécification fonctionnelle du renforcement positif

```
Entrée : event PREFRONTAL_GOAL_COMPLETE avec payload signé :
  - completion_mode : "homeostatic" | "bureaucratic" | "abandoned_fruitless"
  - source_drive    : str                    (nouveau : à exposer par patch)
  - causal_drop     : float ∈ ℝ              (déjà dans le payload)
  - step_intents    : List[str]              (déjà dans le payload, ordonné)
  - goal_id         : str                    (pour traçabilité)

Filtres de sécurité (early return, ordre strict) :
  [F1] completion_mode ≠ "homeostatic"  → return     (fermeture non causale)
  [F2] causal_drop ≤ 0                  → return     (cf. cas limite #1)
  [F3] step_intents vide                → return     (cf. cas limite #5)
  [F4] source_drive absent/inconnu      → return     (cf. cas limite #3)

Calcul du renforcement :
  n               = len(step_intents)
  total_weight    = n * (n + 1) / 2                  # somme triangulaire
  normalized_drop = min(1.0, causal_drop / 100.0)    # cap à 100 pts de tension

  POUR idx, intent ∈ enumerate(step_intents) :
      triangular_weight = (idx + 1) / total_weight
      delta             = normalized_drop × triangular_weight × LEARNING_RATE
      synaptic_network.strengthen(source_drive, intent, delta)

Constantes :
  LEARNING_RATE    = 0.10     # cap sur le gain maximal par event (drop=100, idx=n-1)
  CAUSAL_DROP_CAP  = 100.0    # tension max de référence (cf. desire_engine)
```

### 3.3 Spécification fonctionnelle de l'extinction

```
Entrée : event PREFRONTAL_GOAL_ABANDONED avec :
  - completion_mode : "abandoned_fruitless"
  - source_drive    : str
  - step_intents    : List[str]              (intents qui ont tourné sans résoudre)
  - goal_id         : str

Filtres de sécurité :
  [F1'] completion_mode ≠ "abandoned_fruitless" → return
  [F2'] step_intents vide                        → return
  [F3'] source_drive absent/inconnu              → return

Calcul de l'extinction (choix de design ouvert — voir §7 Q1) :
  Option EGA (Egalitaire) : chaque intent reçoit la même pénalité
      POUR intent ∈ step_intents :
          synaptic_network.weaken(source_drive, intent, EXTINCTION_DELTA)

  Option TRI (Triangulaire inverse) : le premier step est le plus pénalisé
      (pénalise l'initiation de la fausse séquence, encourage l'exploration d'autres
       premières actions)
      n = len(step_intents)
      total_weight = n * (n + 1) / 2
      POUR idx, intent ∈ enumerate(step_intents) :
          inverse_weight = (n - idx) / total_weight
          delta          = EXTINCTION_DELTA × inverse_weight
          synaptic_network.weaken(source_drive, intent, delta)

Plancher (cf. cas limite #4) :
  Le poids synaptique ne descend jamais en dessous de EXTINCTION_FLOOR.
  Si après extinction poids < EXTINCTION_FLOOR → poids = EXTINCTION_FLOOR

Constantes :
  EXTINCTION_DELTA = 0.03     # réduction par event (pour option EGA)
  EXTINCTION_FLOOR = 0.0      # plancher absolu (cf. §7 Q2)
```

## 4. Validation mathématique de la distribution triangulaire

### 4.1 Formule générale

Pour une séquence de `n` steps, le poids triangulaire du `k`-ième step
(indexé à partir de 0) est :

```
weight(k, n) = (k + 1) / (n × (n + 1) / 2)
             = 2 × (k + 1) / (n × (n + 1))
```

### 4.2 Propriétés invariantes

**Invariant A — Conservation du total** :
```
Σ(k=0..n-1) weight(k, n) = 1.0
```

**Invariant B — Monotonie croissante** :
```
weight(k, n) < weight(k+1, n)  ∀ k ∈ [0, n-2]
```

**Invariant C — Dernier step dominant** :
```
weight(n-1, n) = n / (n × (n+1) / 2) = 2 / (n+1)
```

### 4.3 Tables de validation

| n | total_weight | weights individuels | dernier = 2/(n+1) | somme |
|---|---|---|---|---|
| 1 | 1 | [1.000] | 1.000 | 1.000 |
| 2 | 3 | [0.333, 0.667] | 0.667 | 1.000 |
| 3 | 6 | [0.167, 0.333, 0.500] | 0.500 | 1.000 |
| 4 | 10 | [0.100, 0.200, 0.300, 0.400] | 0.400 | 1.000 |
| 5 | 15 | [0.067, 0.133, 0.200, 0.267, 0.333] | 0.333 | 1.000 |
| 10 | 55 | premier=0.018, dernier=0.182 | 0.182 | 1.000 |

**Observation** : pour `n` grand, le dernier step tend vers `2/n` (0 quand
`n → ∞`), donc la pondération triangulaire devient **moins discriminante**
sur les longues séquences. C'est un comportement souhaitable : sur une
séquence de 10 steps, il serait injuste de donner 91% du crédit au dernier
alors qu'il s'est appuyé sur 9 étapes de préparation.

**Contraste avec pondération uniforme** : `1/n` pour tous. Cas `n=1` : 1.0
identique. Cas `n=2` : [0.5, 0.5] — ne privilégie pas le dernier.

---

## 5. Cas limites exhaustifs (challenge Gemini)

### Cas limite #1 — `causal_drop ≤ 0` sur fermeture homéostatique

**Question** : un goal avec `completion_mode == "homeostatic"` mais
`causal_drop ≤ 0` est-il théoriquement possible ?

**Analyse** :
- Fix 1.5 vérifie `is_resolved = (causal_drop >= resolution_threshold)` avant
  de poser `completion_mode = "homeostatic"`
- `resolution_threshold` est typiquement `max(8.0, tension_at_birth * 0.40)`
- Donc **en théorie**, `completion_mode == "homeostatic"` implique
  `causal_drop > 0`
- **MAIS** : bruit dans la mesure (re-appel de `measure_tension` à la
  fermeture peut donner une valeur légèrement différente du snapshot),
  race condition, bug latent dans un organe TensionSource futur

**Décision V3** : filtre défensif `[F2] if causal_drop <= 0: return`.
Skip silencieux (pas de log warning) parce que c'est un edge case théorique.

**Alternative rejetée** : apprendre avec `max(0, causal_drop)` comme fallback.
Rejetée parce que si `causal_drop ≤ 0` est un bug, on préfère ne pas
apprendre du tout plutôt que d'apprendre un delta nul qui pollue les stats.

### Cas limite #2 — Goal à un seul step (n=1)

**Analyse** :
- `total_weight = 1 × 2 / 2 = 1`
- `weight(0, 1) = (0+1)/1 = 1.0`
- Le seul step reçoit **100% du crédit** → comportement correct

**Test unitaire requis** :
```python
def test_triangular_single_step_gets_full_credit():
    weights = [triangular_weight(0, 1)]
    assert weights == [1.0]
    assert sum(weights) == 1.0
```

### Cas limite #3 — Drive inconnu (goal non-drive)

**Question** : que faire si `source_drive` n'appartient pas aux 7 drives
connus (CURIOSITE, MAITRISE, STABILITE, CONNEXION, CROISSANCE, CREATION,
COMPREHENSION) ?

**Exemples concrets** :
- Goal créé par `self_awareness` pour un knowledge_gap → `source_key =
  "Python asyncio"` (topic, pas drive)
- Goal créé par `council` pour un consensus → `source_key =
  "consensus_hash:abc123"` (hash, pas drive)
- Goal créé par un organe futur non prévu

**Options** :

| Option | Comportement | Pour | Contre |
|---|---|---|---|
| **A — Skip** | `return` silencieux si `source_drive ∉ KNOWN_DRIVES` | Simple, safe, pas de pollution du graphe drive↔intent | Perd de l'info d'apprentissage sur les goals non-drive |
| **B — Fallback drive_alignment** | `source_drive = max(goal.drive_alignment, key=val)` | Récupère un drive "proche" | Retombe dans le piège temporel (drive_alignment est statique, pas causal) |
| **C — Dimension séparée** | Stocker dans une table parallèle `topic↔intent` ou `theme↔intent` | Préserve l'info, architecture propre | Complexifie le graphe, nécessite une 2e règle d'apprentissage |

**Décision V3** : **Option A (skip)**.

**Raisons** :
1. V3 minimal : on évite d'introduire une 2e règle d'apprentissage dans
   un refactor déjà risqué
2. Les goals non-drive représentent actuellement ~15% des goals créés
   (estimation à valider via stats préfrontal)
3. Option C est propre architecturalement et reste possible en V3.1 sans
   casser la V3

**Dette V3.1** : documenter dans `MEMORY.md` qu'il faut une dimension
d'apprentissage séparée pour les goals knowledge_gap / council / futurs.

### Cas limite #4 — Plancher de l'extinction

**Question** : quand `weaken(drive, intent, delta)` réduit un poids, jusqu'où
peut-il descendre ?

**Constats** :
- Les poids synaptiques actuels sont probablement dans `[0, ∞)` ou
  `[0, weight_cap]` (à vérifier dans le code existant)
- Un poids négatif n'a **aucun sens biologique** (pas de synapse "inversée")
- Un poids à 0 = connexion éliminée (synaptic pruning biologiquement
  plausible)

**Deux options** :

| Option | Comportement | Pour | Contre |
|---|---|---|---|
| **FLOOR_ZERO** | `max(0.0, weight - delta)` | Biologique, cohérent avec synaptic pruning, le registry filtre les 0 | Lien "mort" nécessite un vrai événement positif pour reapparaître |
| **FLOOR_TRACE** | `max(0.01, weight - delta)` | Garde une trace minimale pour récupération rapide | Pollue le graphe avec des liens fantômes |

**Décision V3** : **FLOOR_ZERO (EXTINCTION_FLOOR = 0.0)**.

**Raisons** :
1. Le **genome** du registre (Phase C Étape 2) garantit déjà une récupération
   via le plancher génomique minimal. Si un lien canonique tombe à 0, il
   reste visible dans `get_routines_for_drive` grâce au `DRIVE_GENOME`
2. Le `FLOOR_OF_THE_FLOOR = 0.05` du genome joue déjà le rôle de trace
3. Biologiquement plus propre
4. Économise de la mémoire pour les liens effectivement morts

**MAIS attention** : cette décision interagit avec la Phase C Étape 4
(migration des consommateurs). Le registry doit bien traiter les liens à 0
sans crash. À vérifier dans `get_routines_for_drive`.

### Cas limite #5 — `step_intents` vide

**Contexte** : un goal est fermé homéostatiquement **sans qu'aucun step n'ait
été marqué `done`**. Ça peut arriver si :
- Fix 1.6 ferme un goal orphelin (le drive a redescendu tout seul)
- Le goal est fermé par une baisse de tension exogène (effet latéral d'une
  autre routine)

**Analyse** : dans ces cas, il n'y a **aucun intent à créditer**. Appliquer
la règle triangulaire sur une liste vide crasherait (`total_weight = 0`).

**Décision V3** : filtre `[F3] if not step_intents: return`. Skip silencieux.

### Cas limite #6 — Duplicats dans `step_intents`

**Question** : si une routine est exécutée plusieurs fois pour le même goal
(retry), elle apparaît plusieurs fois dans `step_intents`. Qui reçoit le
crédit ?

**Exemple** : `step_intents = ["VEILLE_SILENCIEUSE", "EXPANSION_CODE",
"VEILLE_SILENCIEUSE", "EXPANSION_CODE"]` (retry de 2 steps).

**Options** :
- **Appliquer la règle tel quel** : chaque occurrence reçoit son poids
  triangulaire. VEILLE_SILENCIEUSE reçoit `weight(0) + weight(2) =
  1/10 + 3/10 = 4/10` au total. Plus juste causalement (les 2 essais ont
  contribué)
- **Dédupliquer** : ne garder que la dernière occurrence, appliquer
  triangulaire sur la séquence unique. Plus simple mais perd l'info de
  retry

**Décision V3** : **appliquer tel quel** (sans déduplication). Chaque
occurrence a contribué à la séquence qui a causé le drop. Le graphe
apprendra naturellement les intents "multi-essayés" avec un poids cumulé
plus élevé.

### Cas limite #7 — Causal drop très grand ou très petit

**Scénario 1** : `tension_at_birth = 95`, `current_tension = 10`, donc
`causal_drop = 85`. Normalisé : `85/100 = 0.85`. Multiplié par `LEARNING_RATE`
(0.10) → delta max = **0.085** pour le dernier step. OK, raisonnable.

**Scénario 2** : `causal_drop = 5` (petit drop). Normalisé : `0.05`. Delta max
= **0.005**. OK, raisonnable.

**Scénario 3 exotique** : `causal_drop = 150` (bug, drop > tension_max).
Cap à 100 via `min(1.0, causal_drop / 100)`. Delta max = **0.1**. Safe.

**Décision V3** : cap défensif `min(1.0, causal_drop / CAUSAL_DROP_CAP)`
avec `CAUSAL_DROP_CAP = 100.0`. Ne dépasse jamais 0.1 de renforcement par
event.

### Cas limite #8 — Race condition multi-events

**Scénario** : deux goals se ferment simultanément sur le même drive. Les
deux events `PREFRONTAL_GOAL_COMPLETE` arrivent en parallèle dans le bus
async.

**Analyse** :
- `synaptic_network.strengthen` modifie l'état interne du graphe
- Python asyncio est single-threaded (GIL + boucle async)
- Les deux handlers s'exécutent séquentiellement via `await`
- **Pas de race condition** tant que `strengthen` ne cède pas le contrôle
  (pas de `await` interne)

**Décision V3** : pas de lock nécessaire si `strengthen` est pur synchrone.
À **vérifier dans le code existant** de `synaptic_network.strengthen`.

**Test unitaire** : simuler 2 events concurrents via `asyncio.gather` et
vérifier que le graphe est dans un état cohérent après.

---

## 6. Invariants à tester (test gate avant production)

### 6.1 Invariants sur la distribution triangulaire

```python
def test_triangular_conserves_total():
    """La somme des poids triangulaires doit être exactement 1.0."""
    for n in [1, 2, 3, 5, 10, 50]:
        weights = [triangular_weight(k, n) for k in range(n)]
        assert abs(sum(weights) - 1.0) < 1e-9

def test_triangular_monotone_increasing():
    """Les poids sont strictement croissants."""
    for n in [2, 3, 5, 10]:
        weights = [triangular_weight(k, n) for k in range(n)]
        for i in range(n - 1):
            assert weights[i] < weights[i+1]

def test_triangular_last_step_dominant():
    """Le dernier step est égal à 2/(n+1)."""
    for n in [1, 2, 3, 5, 10]:
        w_last = triangular_weight(n - 1, n)
        assert abs(w_last - 2.0 / (n + 1)) < 1e-9
```

### 6.2 Invariants sur le handler

```python
def test_no_learning_on_bureaucratic():
    """Une fermeture bureaucratique ne doit RIEN apprendre."""
    graph_before = snapshot_graph()
    await _learn_from_homeostatic_closure({
        "completion_mode": "bureaucratic",
        "source_drive": "MAITRISE",
        "causal_drop": 50,
        "step_intents": ["REFACTORING_AUDIT"],
    })
    assert snapshot_graph() == graph_before

def test_no_learning_on_abandoned():
    """Une fermeture abandoned_fruitless ne renforce pas (c'est pour extinction)."""
    # Même test avec completion_mode = "abandoned_fruitless"

def test_no_learning_on_unknown_drive():
    """Un source_drive inconnu → skip silencieux."""
    graph_before = snapshot_graph()
    await _learn_from_homeostatic_closure({
        "completion_mode": "homeostatic",
        "source_drive": "knowledge_gap:quantum",
        "causal_drop": 50,
        "step_intents": ["VEILLE_SILENCIEUSE"],
    })
    assert snapshot_graph() == graph_before

def test_no_learning_on_zero_drop():
    """causal_drop ≤ 0 → skip."""
    # ...

def test_no_learning_on_empty_steps():
    """step_intents vide → skip, pas de crash."""
    # ...

def test_learning_single_step_full_credit():
    """n=1 : le step reçoit delta = normalized_drop × LEARNING_RATE."""
    # ...

def test_learning_triangular_distribution():
    """n=3 : delta distribué 1/6, 2/6, 3/6."""
    # ...

def test_extinction_respects_floor():
    """Un lien à 0.02 après weaken(0.03) doit rester à 0.0, pas négatif."""
    # ...

def test_learning_rate_cap():
    """Le delta max pour un event ne dépasse pas LEARNING_RATE."""
    # ...
```

### 6.3 Invariants système (tests d'intégration)

```python
def test_no_learning_on_dopamine_surge_without_goal_complete():
    """Un DOPAMINE_SURGE isolé (non lié à un goal_complete) ne doit
    PAS déclencher d'apprentissage — vérification du principe de
    vérité causale.
    """
    graph_before = snapshot_graph()
    await bus.publish("DOPAMINE_SURGE", {"source": "compliment_chat"})
    await asyncio.sleep(0.1)
    assert snapshot_graph() == graph_before

def test_ancien_hebbian_inactif():
    """L'ancien renforcement sur 'success' nu ne doit plus s'appliquer."""
    graph_before = snapshot_graph()
    await bus.publish("AUTONOMY_ROUTINE_COMPLETE", {
        "intent": "EXPANSION_CODE",
        "status": "success",
    })
    await asyncio.sleep(0.1)
    # L'ancien code aurait renforcé drive↔EXPANSION_CODE
    # La V3 ne doit rien faire sans un GOAL_COMPLETE accompagnant
    assert snapshot_graph() == graph_before
```

---

## 7. Questions ouvertes pour Gemini

### Q1 — Distribution de l'extinction : EGA vs TRI inverse

**Contexte** : pour l'extinction (`_learn_from_fruitless_goal`), deux options
de distribution sont possibles (cf. §3.3).

- **EGA (Égalitaire)** : chaque intent reçoit `-0.03`. Simple, équitable.
- **TRI inverse** : le premier step est le plus pénalisé. Intuition : c'est
  l'initiation de la fausse séquence qui est l'erreur. Encourage l'exploration
  d'autres premières actions.

**Ma préférence** : **EGA** pour la symétrie avec le renforcement triangulaire
(qui favorise le dernier). Mais je peux être challengé : si on favorise le
dernier en récompense, pourquoi ne pas favoriser le premier en punition ?

**Demande à Gemini** : arbitrage adversarial. Quel est le plus biologiquement
plausible ? Quel est le plus susceptible de produire une émergence saine ?

### Q2 — Plancher d'extinction : 0.0 vs 0.01

**Contexte** : cf. cas limite #4.

**Ma préférence** : **0.0**, parce que le genome du registre couvre déjà la
récupération. Mais si Gemini préfère 0.01 pour une raison de traçabilité ou
de récupération plus rapide, je suis ouvert.

### Q3 — Fallback drive_alignment (cas limite #3)

**Contexte** : j'ai choisi l'Option A (skip) pour V3 minimal. Mais l'Option B
(fallback via `drive_alignment`) est tentante parce qu'elle permet un
apprentissage immédiat sur les goals knowledge_gap.

**Demande à Gemini** : est-ce que le fallback `drive_alignment` viole le
principe de vérité causale (parce que `drive_alignment` est une prédiction
statique au moment de la création du goal, pas une mesure a posteriori) ?

### Q4 — Learning rate cap

**Contexte** : `LEARNING_RATE = 0.10`. Le delta max pour un event (causal_drop
maxi × dernier step) est de `0.10`.

**Demande à Gemini** : 0.10 est-il trop ou trop peu ? Comparer avec :
- L'ancien Hebbian qui renforçait de `+0.05` par event (d'après ma mémoire
  de Phase B)
- Le RPE dopamine qui peut aller jusqu'à `+0.7` mais sur la value function,
  pas sur le graphe synaptique

### Q5 — Fréquence d'observation attendue

**Contexte** : combien de `PREFRONTAL_GOAL_COMPLETE` avec `mode=homeostatic`
Prométhée génère-t-il par jour actuellement ?

**Observation empirique** : sur la nuit du 13→14 avril, on a vu ~11 DIP
fruitless mais seulement **2 completions homéostatiques** (MAITRISE et
STABILITE forcées).

→ Ratio estimé : ~1 apprentissage positif pour 5 extinctions. Le graphe va
**surtout apprendre par extinction** en phase V3. Est-ce un problème ?

**Demande à Gemini** : ce déséquilibre positif/négatif est-il sain ? Devrait-on
ajuster `LEARNING_RATE` pour compenser (ex: +0.15 pour positif, -0.02 pour
négatif) ?

### Q6 — Indexation par drive ou par source_organ ?

**Contexte** : dans la règle V3, j'indexe par `source_drive` (ex: "MAITRISE").
Mais Fix 1.5 utilise `source_organ` + `source_key` (ex: "desire_engine",
"MAITRISE").

**Question** : est-ce qu'on pourrait étendre le graphe pour apprendre aussi
`source_organ → intent` (ex: "desire_engine → REFACTORING_AUDIT") en plus de
`source_key → intent` ? Ça permettrait d'apprendre des patterns par organe,
pas seulement par drive.

**Ma préférence** : **non en V3**. Un niveau d'indexation à la fois. On reste
sur `source_drive → intent` (qui devient `source_key → intent` avec le filtre
F4 qui exclut les non-drives).

---

## 8. Points d'ancrage dans le code

### 8.1 À patcher

**`core/prefrontal.py:1753`** — ajouter 1 ligne dans `_publish_goal_event`
pour exposer `source_drive` :

```python
# Avant L1755 (loop.create_task(bus.publish(...)))
payload["source_drive"] = (
    meta.get("source_key")
    or max(
        (goal.drive_alignment or {}).items(),
        key=lambda x: x[1],
        default=(None, 0)
    )[0]
)
```

**`core/synaptic_network.py`** — localiser et **désactiver** l'ancien Hebbian.
Cartographie précise à faire en étape 3.B (avant le code).

### 8.2 À créer

**`core/synaptic_network.py`** (dans la même classe) :
- `_learn_from_homeostatic_closure(event)` — handler du renforcement
- `_learn_from_fruitless_goal(event)` — handler de l'extinction
- `triangular_weight(idx, n)` — helper pur, testable unitairement
- Subscribe des deux handlers dans `__init__`

**`tests/test_synaptic_hebbian_causal.py`** — nouveau fichier de tests avec
les invariants du §6, mock du bus pour isolation totale.

---

## 9. Risques et mitigations

| Risque | Probabilité | Impact | Mitigation |
|---|---|---|---|
| Erreur dans la distribution triangulaire | Faible | 🔴 Apprentissage silencieusement biaisé | Tests unitaires exhaustifs §6.1, challenge Gemini sur les invariants |
| L'ancien Hebbian reste partiellement actif (code mort non supprimé) | Moyenne | 🟡 Double apprentissage parasite | Test §6.3 `test_ancien_hebbian_inactif`, commenter (pas supprimer) avant validation |
| Race condition sur `strengthen` | Très faible | 🟡 État incohérent temporaire | Vérifier que `strengthen` est pur synchrone, test §6.2 de concurrence |
| Drive inconnu → perte d'info knowledge_gap | Certaine | 🟢 Pas d'apprentissage sur ~15% des goals | Documenté comme dette V3.1 |
| LEARNING_RATE trop fort → instabilité | Moyenne | 🟡 Le graphe oscille, ne converge pas | Cap à 0.1, monitoring des poids après 24h, rollback possible |
| LEARNING_RATE trop faible → pas de learning visible | Moyenne | 🟢 Phase C Étape 3 semble inefficace | Observer 7 jours, ajuster si les poids ne bougent pas |
| Extinction trop agressive → pruning massif des liens | Faible | 🟡 Perte de routines canoniques | `EXTINCTION_DELTA = 0.03` faible, floor génomique protège les canoniques |

---

## 10. Protocole de déploiement (après validation Gemini)

1. **Rédiger le design doc** (ce fichier) ← **FAIT**
2. **Challenge Gemini** via `/challenge` sur le design — attendre feu vert
3. **Cartographie de l'ancien Hebbian** dans `synaptic_network.py`
4. **Patch `prefrontal.py:1753`** (1 ligne)
5. **Test unitaire du patch** (vérifier `source_drive` dans le payload)
6. **Écrire les handlers V3** (`_learn_from_homeostatic_closure` +
   `_learn_from_fruitless_goal`) avec tests unitaires isolés
7. **Tests d'invariants §6.1** (distribution triangulaire pure)
8. **Tests de handler §6.2** (avec mock du bus)
9. **Tests système §6.3** (régression ancien Hebbian)
10. **Commenter** l'ancien Hebbian (pas supprimer)
11. **Subscribe** les nouveaux handlers dans `__init__`
12. **Tests pytest globaux** — zéro régression
13. **Sync + reboot** (petite fenêtre d'observation post-déploiement)
14. **Monitoring 24h** des stats synaptic (nombre de renforcements, poids
    moyen, détection d'anomalies)
15. **Si OK → suppression** du code mort à J+7
16. **Si KO → rollback** via revert commit

---

## 11. Métriques de succès (observabilité post-déploiement)

**À ajouter dans `synaptic_network.stats`** :

```python
stats = {
    "hebbian_causal_reinforcements": 0,     # compteur events positifs
    "hebbian_causal_extinctions": 0,        # compteur events négatifs
    "hebbian_causal_skipped_unknown_drive": 0,  # F4 filter
    "hebbian_causal_skipped_zero_drop": 0,      # F2 filter
    "hebbian_causal_skipped_empty_steps": 0,    # F3 filter
    "hebbian_causal_skipped_non_homeostatic": 0, # F1 filter
    "hebbian_causal_total_delta_applied": 0.0,  # somme des deltas (sanity)
}
```

**KPI à observer après 24h** :

- `reinforcements` > 5 et `extinctions` > 10 → système apprend activement
- `reinforcements / extinctions` ∈ [0.1, 0.5] → ratio sain (plus
  d'extinction au départ, normal)
- `total_delta_applied / reinforcements` ≈ 0.05-0.08 → delta moyen cohérent
- Aucun `skipped_*` ne doit représenter > 50% des events reçus

**Alerte critique** : si après 24h `reinforcements == 0` OU `extinctions == 0`,
c'est que la règle ne s'active pas → rollback immédiat.

---

## 12. Ce que le design NE fait PAS (hors scope V3)

- **Pas d'apprentissage sur les goals non-drive** (knowledge_gap, council).
  Dette V3.1 documentée.
- **Pas de décroissance temporelle** des poids (les liens ne se dégradent
  pas avec le temps). À réfléchir pour V3.2.
- **Pas de normalisation globale** du graphe (les poids peuvent diverger
  arbitrairement). Le registre Phase C Étape 2 gère la normalisation à la
  lecture via `min(1.0, weight * something)`.
- **Pas de récupération des liens à 0** via mécanisme endogène. Un lien
  mort reste mort jusqu'à ce qu'un vrai événement positif arrive.
- **Pas de migration des données** de l'ancien Hebbian vers la nouvelle.
  Le graphe démarre "à chaud" avec les poids actuels et le learning V3
  les modifie progressivement.

---

## Appendice A — Glossaire rapide

- **Causal drop** : `expected_if_no_action - current_tension`. Mesure isolant
  la contribution causale de l'action (vs decay naturel).
- **Completion mode** : étiquette posée par Fix 1.5 à la fermeture d'un goal.
  Peut être `"homeostatic"`, `"bureaucratic"`, `"abandoned_fruitless"`, ou
  `"abandoned"` (cost/timeout).
- **Distribution triangulaire** : poids `(idx+1) / (n*(n+1)/2)` — croissante,
  somme à 1, favorise le dernier step.
- **Extinction** : affaiblissement du lien synaptique suite à un fruitless.
  Mécanisme d'apprentissage par conséquences négatives.
- **Source drive** : nom du drive originel qui a créé le goal (via
  TensionSource protocol).
- **Step intents** : liste ordonnée des intents des steps `done` d'un goal.

## Appendice B — Références

- `docs/FINDINGS.md` — Clôture Phase B (principes vérité causale, temps
  métabolique, dichotomie Hérétiques/Modulateurs)
- `docs/phase_c_roadmap.md` — Plan d'action Étapes 3→6
- `docs/session_2026_04_13_phase_b_c.md` — Chronologie de la session fondatrice
- `core/drive_routine_registry.py` — Phase C Étape 2 (le genome sera alimenté
  par cette règle à travers le graphe synaptique)
- `core/tension_protocol.py` — Fix 1 Phase C (TensionSource + causal_drop)
