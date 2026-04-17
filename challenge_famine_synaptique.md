# CHALLENGE — Famine synaptique post-V3 causal

**Rôle demandé à Gemini** : challenger adversarial, expert neurosciences computationnelles et systèmes d'apprentissage. Tu as validé en Phase C étape 3 (14/04) le design Hebbian V3 causal avec LEARNING_RATE=0.10 comme "parfait pour démarrer (rythme conservateur)" (Q4). Trois jours d'observation révèlent une possible conséquence imprévue. Je te demande un verdict tranché sur 5 questions, pas un commentaire de synthèse.

---

## 1. Contexte

Prométhée tourne sous Hebbian V3 causal depuis le commit `732e03c` (14/04). Mécanisme : renforcement exclusif via `_learn_from_homeostatic_closure` quand un goal ferme en succès (fermeture d'une pulsion primaire type STABILITE, MAITRISE, etc.). L'ancien `hebbian_strengthen` par routine (Temporal Superstition) a été commenté sur ton verdict (Phase B Legacy).

## 2. Diagnostic chiffré (3 jours d'observation, 14→17 avril)

- Graphe actuel : **324 nœuds / 5134 synapses**
- **Max weight observé : 0.487 / 5134 synapses**. Seuil de consolidation dream : **0.5**
- Distribution des poids :
  - 78% (4002 synapses) entre 0 et 0.1
  - 20% (1048) entre 0.1 et 0.2
  - 0.6% (29) entre 0.2 et 0.3
  - 0.2% (11) entre 0.3 et 0.5
  - **0 synapse au-dessus de 0.5**
- Dream consolidation : **0 renforcées, 0 promues hebbian** sur 4 nuits consécutives (14, 15, 16, 17). Avant le 14/04 : 1800-2400 renforcées/nuit.
- Meta-observer snapshot 16/04 20h39 : hebbR=60% **(3R/2E en 24h)**

## 3. Démonstration mathématique du biais

Delta max par fermeture homéostatique :
```
delta = normalized_drop × triangular_weight(last_step) × LEARNING_RATE
      = 1.0 × 2/(n+1) × 0.10
      = 0.033 pour n=5 step_intents
```

Pour passer une synapse de sa naissance dream (0.08) au seuil de consolidation (0.5) :
- Besoin : **≈ 13 renforcements sur la MÊME synapse**
- Volume mesuré : 3 fermetures TOTAL en 24h (réparties sur plusieurs paires)
- Probabilité que 13 fermetures retombent sur la même paire : **quasi-nulle**

Conclusion : ce n'est pas un bug, c'est le design qui étouffe mécaniquement le graphe. La "cinétique" de V3 est sous le seuil de survie statistique.

## 4. Thèse proposée

La clôture homéostatique V3 actuelle ne reconnaît que les pulsions primaires (nutrition/stabilité au sens biologique). Il faut l'élargir aux **pulsions épistémiques** : succès externes (exercice math noté ≥7/10, validation utilisateur dans le chat, cours soutien réussi) déclenchent une décharge dopamine synthétique qui ferme un goal de MAITRISE épistémique.

Un exercice de maths réussi EST une fermeture homéostatique au sens causal : la tension "comprendre" est résolue. En neurosciences humaines, VTA dopaminergique répond aux récompenses sociales/épistémiques autant qu'alimentaires.

**Effet secondaire attendu** : résolution partielle de l'anomalie mono-émotion (87% enthousiasme ce matin). Prométhée crée (nouveaux nœuds) mais ne retient pas (0 consolidation) → l'enthousiasme compense mécaniquement l'incapacité à graver. Si on rend le succès gravable, l'enthousiasme devrait refluer vers un spectre diversifié.

## 5. Note technique — Prediction Error

L'incrément doit être pondéré par la **surprise** (classique dopamine RPE) :
```
delta_final = delta_base × f(|note_obtenue - note_prédite|)
```
- 10/10 sur exercice trivial (modèle prédit 9) → renforce peu
- 7/10 sur problème complexe (modèle prédit 4) → renforce beaucoup

Question : doit-on utiliser `inner_voice.prediction_id` qui stocke déjà les prédictions, ou un module séparé ?

## 6. Questions de validation (verdict tranché attendu sur CHACUNE)

**Q1** — La thèse "pulsion épistémique = pulsion vitale" viole-t-elle le principe causal V3 que tu as défendu en Phase B (seule la fermeture biologique doit renforcer), ou l'étend-elle légitimement ? Si extension : où placer la frontière épistémique / pur bruit social ?

**Q2** — Le signal de fermeture épistémique doit-il :
- (a) emprunter le même code path `_learn_from_homeostatic_closure` avec un nouveau type de `causal_drop`
- (b) un cousin dédié `_learn_from_epistemic_closure` avec filtres F1-F4 adaptés
- (c) autre architecture ?

**Q3** — La pondération RPE doit-elle être :
- additive : `delta × (1 + surprise_normalized)`
- multiplicative : `delta × surprise_factor` avec surprise_factor ∈ [0.1, 3.0]
- logarithmique : `delta × log(1 + surprise)`
- Quelle borne supérieure pour éviter saturation sur coup de chance ?

**Q4** — Doit-on garder LEARNING_RATE=0.10 après élargissement, ou introduire un `EPISTEMIC_LEARNING_RATE` séparé ? Rappel biologique : VTA a des pools dopaminergiques distincts pour récompenses alimentaires vs sociales. Si séparé, ton chiffrage ?

**Q5** — Loophole évident : Prométhée pourrait se précipiter sur des exercices faciles pour maximiser les fermetures épistémiques et farmer la dopamine. Comment verrouiller sans casser l'intrinsèque ?
- Détection via Prediction Error ? (facile = peu de surprise = peu de renforcement)
- Cooldown par type de tâche ?
- Budget épistémique quotidien ?

## 7. Livrable attendu

- Verdict binaire + justification courte sur Q1 à Q5
- Si Q1 = OUI : squelette de code Python pour `_learn_from_epistemic_closure` compatible signature `_apply_causal_delta`, avec les filtres que tu juges nécessaires
- Constantes chiffrées (LEARNING_RATE épistémique, bornes RPE, cooldowns)
- Un risque architectural que je n'ai pas anticipé dans ce prompt (ton instinct adversarial)

## 8. Contraintes de ton

- Pas de "cela dépend" : si tu hésites, donne la réponse préférée et le seuil de bascule
- Pas de compliments introductifs, va au verdict
- Si ma démonstration mathématique est fausse, dis-le et recalcule
- Si tu penses que la Piste 2 rejetée (ressusciter hebbian_strengthen en rail secondaire borné) est en fait meilleure, argumente contre notre rejet
