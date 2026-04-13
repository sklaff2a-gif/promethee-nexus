# Fix 1 — Phase C : Architecture pour les organes cognitifs

**Date** : 2026-04-13
**Status** : DRAFT (non implemente)
**Prerequis** : Phase A (desire_engine) et Phase B (hook dopamine) livrees et validees

## Contexte

Fix 1 Phase A+B est operationnel sur le `desire_engine` : les goals issus de
pulsions frustrees sont fermes homeostatiquement quand la deprivation causale
chute, ou abandonnes apres N cycles steriles. Validation live au matin du
2026-04-13 a montre 4 fermetures homeostatiques reussies (dont CONNEXION avec
causal_drop=23.82/57=41.8%, precision chirurgicale).

Phase C doit etendre Fix 1 aux **organes cognitifs** qui generent aussi des
goals : self_awareness (KNOWLEDGE_GAP), synaptic_network (EUREKA_BRIDGE),
council (COUNCIL_END).

## Le probleme conceptuel — Paradoxe de Moravec applique a l'architecture

Les organes biologiques bas niveau (desire_engine, cardiac, dopamine) utilisent
des **metriques continues** (deprivation 0-100, BPM, RPE float). Les organes
cognitifs haut niveau utilisent des **flags binaires** (`learned: bool`,
`crystallized: bool`). Le critere de fermeture actuel pour KNOWLEDGE_GAP :

```python
researcher_ok = (
    result.get("status") == "success"
    and len(str(result.get("result", ""))) > 50
)
if researcher_ok:
    awareness.mark_gap_learned(topic)
```

**C'est la Loi de Goodhart incarnee** : la mesure (retour du researcher > 50
chars) a remplace l'objectif (savoir reellement le sujet). Un agent qui
hallucine 500 chars de blabla coche la case.

Phase C doit refonder ces organes sur des **mathematiques continues**.

## Principe directeur : Compression, pas Coverage

**Metrique rejetee** : nombre de documents pertinents × similarite cosine dans
ChromaDB. C'est la metrique d'un bibliothecaire. Un agent qui decoupe la page
Wikipedia en 15 chunks explode son coverage_score sans rien avoir appris.

**Metrique adoptee** : la densite d'information d'un **resume genere par le
LLM local** a partir du corpus. Si l'agent peut synthetiser, il sait. S'il
regurgite, il a juste stocke. La compression est la preuve de la comprehension.

Formellement :
```
tension(topic) = 100 × (1 - self_consistency(topic))
```

ou `self_consistency` est mesure par la variance semantique sur N echantillons
a haute temperature (voir 1.2).

## Section 1 — KNOWLEDGE_GAP (self_awareness)

### 1.1 Probleme actuel

- `mark_gap_learned(topic)` est appele si `result.status == "success" and len > 50`
- Pas de mesure semantique, pas de test de retention, pas de cross-check
- Bug direct : un agent en mode bullshit jobs coche toutes ses cases

### 1.2 Metrique proposee — Self-Consistency via echantillonnage multi-temperature

**Principe** : le LLM local est interroge 3 fois sur le topic avec temperature
eleve (T=0.8). Les 3 reponses sont comparees. Si elles convergent sur les
memes concepts et les memes faits, le savoir est cristallise. Si elles
divergent, l'agent improvise (lacune reelle).

**Pourquoi pas la perplexite** : les LLM sont des menteurs confiants. Un fait
hallucine peut avoir une perplexite basse. La variance inter-echantillons a
haute temperature expose l'incertitude reelle.

**Pourquoi pas LLM-as-judge** : tautologie. Demander a un LLM de juger un
autre LLM est un serpent qui se mord la queue.

### 1.3 Implementation — Fonction de similarity

```python
def measure_self_consistency(self, topic: str, n_samples: int = 3) -> float:
    """Retourne [0, 1] : 1 = cristallise, 0 = hallucination divergente."""
    prompt = f"Explique {topic} en 150 mots, de maniere structuree et factuelle."
    responses = [self.llm.generate(prompt, temperature=0.8).strip()
                 for _ in range(n_samples)]

    # Cache trivial : meme debut = reponse mise en cache, neutre
    starts = {r[:80] for r in responses}
    if len(starts) == 1:
        return 0.5

    # 1. Embeddings cosine (concept-level coherence)
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer('all-MiniLM-L6-v2')
    embeds = model.encode(responses)
    pairwise_cos = []
    for i in range(n_samples):
        for j in range(i+1, n_samples):
            sim = float(np.dot(embeds[i], embeds[j]) /
                        (np.linalg.norm(embeds[i]) * np.linalg.norm(embeds[j])))
            pairwise_cos.append(sim)
    emb_coherence = float(np.mean(pairwise_cos))

    # 2. Key-term overlap (fact-level coherence)
    term_sets = [extract_key_terms(r) for r in responses]
    common = set.intersection(*term_sets)
    union = set.union(*term_sets)
    term_coherence = len(common) / len(union) if union else 0

    # 3. Ponderation
    return 0.65 * emb_coherence + 0.35 * term_coherence
```

### 1.4 Architecture de `measure_tension` pour self_awareness

```python
def measure_tension(self, goal_meta: dict) -> TensionMeasurement:
    topic = goal_meta['source_key']
    tension_at_birth = goal_meta['tension_at_birth']

    consistency = self.measure_self_consistency(topic)
    current_tension = 100 * (1 - consistency)

    # Counterfactual : la tension knowledge-gap ne baisse pas toute seule
    # (contrairement aux drives qui ont un rise naturel)
    expected_if_no_action = tension_at_birth  # STATIONNAIRE

    causal_drop = expected_if_no_action - current_tension
    is_resolved = causal_drop >= tension_at_birth * 0.40

    return TensionMeasurement(
        current_tension=current_tension,
        tension_at_birth=tension_at_birth,
        expected_if_no_action=expected_if_no_action,
        causal_drop=causal_drop,
        is_resolved=is_resolved,
        is_worsened=(causal_drop <= -10),
        extra={
            "topic": topic,
            "self_consistency": consistency,
            "mode": "self_consistency_v1",
        }
    )
```

### 1.5 tension_at_birth : Epistemic Surprise initial

Ne pas fixer `tension_at_birth = 100` par defaut. Une lacune **legere** (le
topic est deja dans le champ de Promethee) ne merite pas la meme recompense
qu'une lacune **profonde** (territoire inconnu).

```python
def compute_epistemic_surprise(self, topic: str) -> float:
    """Retourne [20, 95] : plus c'est haut, plus l'ignorance est profonde."""
    initial_consistency = self.measure_self_consistency(topic, n_samples=3)
    return max(20.0, min(95.0, 100.0 * (1.0 - initial_consistency)))
```

Au moment de la creation du goal KNOWLEDGE_GAP, self_awareness appelle cette
fonction et stocke le resultat dans `goal.metadata.tension_at_birth`.

### 1.6 Acquisition vs Cristallisation

Inspiration Gemini : separer "Acquisition" (fermeture du goal) et
"Cristallisation" (validation par reutilisation en contexte).

- **Acquisition** (V1 ici) : self_consistency au-dessus du seuil -> goal ferme
  homeostatique, dopamine surge modere.
- **Cristallisation** (V2 futur) : si dans les N jours suivants l'acquisition,
  Promethee utilise avec succes ce savoir dans une tache reelle (la
  routine_history montre un USE_FOR_TASK avec ce topic), un second dopamine
  surge vient renforcer la strategie gagnante.

Pour l'instant, seule l'Acquisition est implementee. La Cristallisation
necessite un tracking cross-goal complexe (V2).

### 1.7 Limites connues et mitigations

**Limite 1 — Hallucination consistante** : si le LLM hallucine la meme
fiction 3 fois, le self_consistency est haut et Fix 1 conclut "cristallise" a
tort. Mitigation V2 : cross-check vs Wikipedia API ou corpus externe.

**Limite 2 — Cout LLM** : 3 appels par measure_tension = ~10s sur qwen local.
Acceptable car _check_goal_completion est rare. Mitigation : cache LRU 5 min
sur (topic, consistency).

**Limite 3 — Subjectivite du prompt** : le prompt influence les reponses.
Utiliser un prompt simple et neutre pour eviter de biaiser le test.

## Section 2 — EUREKA_BRIDGE (synaptic_network)

### 2.1 Probleme actuel

Les goals EUREKA_BRIDGE sont crees quand synaptic_network detecte un pont
creatif (spreading_activation entre 2 nodes semantiques). Leur fermeture
actuelle est bureaucratique : les steps sont executes, done.

### 2.2 Metrique proposee — Distance geodesique dans le graphe

Inspiration Gemini : la tension d'un EUREKA_BRIDGE est la **distance
geodesique** (plus court chemin) entre les 2 nodes concernes dans le graphe
semantique. L'action (debat, recherche) doit creer des nodes intermediaires
qui **raccourcissent** ce chemin.

```python
def measure_tension(self, goal_meta: dict) -> TensionMeasurement:
    node_a = goal_meta['extra']['node_a']
    node_b = goal_meta['extra']['node_b']
    initial_distance = goal_meta['tension_at_birth']  # ex: 7 sauts a la creation

    current_distance = self.shortest_path(node_a, node_b)
    if current_distance is None:
        current_distance = 999  # nodes deconnectes

    # Normaliser en [0, 100]
    # Un chemin < 3 est "tres court" (voisins quasi-directs)
    # Un chemin > 10 est "lointain" (lien faible)
    tension_at_birth_normalized = min(100, initial_distance * 10)
    current_tension_normalized = min(100, current_distance * 10)

    causal_drop = tension_at_birth_normalized - current_tension_normalized
    is_resolved = causal_drop >= tension_at_birth_normalized * 0.50

    return TensionMeasurement(...)
```

### 2.3 Modelisation de la creation de nodes

Quand une routine EXPANSION_CODE s'execute sur un topic intermediaire, elle
doit etre "attachee" au graphe semantique avec un weight proportionnel a sa
pertinence. Le `shortest_path(a, b)` peut alors passer par ce nouveau node si
cela raccourcit le trajet.

C'est un probleme de theorie des graphes avec poids. NetworkX ou igraph
peuvent calculer les shortest paths efficacement.

**Question ouverte** : comment decider si un nouveau document doit etre un
node ? Un embedding + clustering KNN sur les nodes existants ? Heavy.

## Section 3 — COUNCIL_END (council_system)

### 3.1 Probleme actuel

Un goal est cree quand un council atteint un consensus (`final_summary`). Le
goal est ferme bureaucratiquement quand ses steps (EXPANSION_CODE,
SECURITY_AUDIT) ont fini. Pas de verification que le consensus a ete
effectivement implemente.

### 3.2 Metrique proposee — KL Divergence

Inspiration Gemini : le consensus du conseil est une **distribution de
probabilites cible** sur les actions possibles. Le comportement reel de
Promethee dans les jours suivants est une autre distribution. La tension =
divergence de Kullback-Leibler entre les deux.

```python
def encode_consensus_as_distribution(self, consensus_text: str) -> Dict[str, float]:
    """Extrait du texte du consensus une distribution de probabilites sur les
    intents/agents recommandes.
    Ex : 'prioriser REFACTORING_AUDIT et SECURITY_AUDIT'
    -> {'REFACTORING_AUDIT': 0.5, 'SECURITY_AUDIT': 0.5}
    """
    # LLM zero-shot ou parsing heuristique
    ...

def measure_tension(self, goal_meta: dict) -> TensionMeasurement:
    target_dist = goal_meta['extra']['target_distribution']
    window_s = goal_meta.get('measurement_window_s', 3600)

    # Distribution reelle : frequence des intents dans la routine_history recente
    recent = self.get_recent_routine_distribution(window_s)

    # KL(target || actual) : penalise si actual manque des actions recommandees
    kl = sum(target_dist[k] * math.log(target_dist[k] / (recent.get(k, 1e-9)))
             for k in target_dist)

    # Normaliser en tension [0, 100]
    current_tension = min(100, kl * 20)  # calibration a faire
    tension_at_birth = goal_meta['tension_at_birth']  # ex: 100 a la creation
    causal_drop = tension_at_birth - current_tension

    is_resolved = causal_drop >= tension_at_birth * 0.40
    return TensionMeasurement(...)
```

### 3.3 Question ouverte

Comment encoder un consensus en distribution de probabilites de maniere
stable ? Le texte du consensus est naturellement fuzzy ("prioriser la
stabilite", "se concentrer sur X"). Le mapping texte -> distribution est
lui-meme une tache NLP non triviale.

Option 1 : LLM-as-parser avec prompt structure "Extract action distribution".
Option 2 : Regex + keyword matching sur les intents connus.
Option 3 : Human-in-the-loop pour annoter les consensus manuellement.

**Pour V2** : Option 1 avec fallback Option 2 en cas d'echec.

## Section 4 — Priorisation d'implementation

| Section | Effort estime | Chance de succes | Valeur |
|---|---|---|---|
| KNOWLEDGE_GAP via self_consistency | 6-8h | 85% | HAUTE (le plus frequent) |
| EUREKA_BRIDGE via geodesic distance | 12-16h | 60% | MOYENNE (peu frequent) |
| COUNCIL_END via KL divergence | 16-24h | 40% | HAUTE mais risquee |

**Recommandation** : attaquer KNOWLEDGE_GAP en premier. C'est la voie la plus
frequente et la plus necessaire a fixer (critere actuel = scandale). Les 2
autres peuvent rester en fallback bureaucratique pendant longtemps sans
impact critique.

## Section 5 — Questions a resoudre avant d'implementer

1. **Nomination de la primitive LLM** : doit-on passer par l'API `llm_manager`
   ou directement par Ollama ? La premiere est plus propre, la seconde plus
   rapide.

2. **Cache de self_consistency** : un LRU 5 min sur (topic, score) pour
   eviter de recalculer si un goal est re-check plusieurs fois.

3. **Threshold de resolution** : 40% comme pour les drives ? Ou plus strict
   pour les lacunes (50-60%) car on ne veut pas fermer trop vite ?

4. **Gestion des echecs LLM** : si l'appel LLM echoue, fallback sur le mode
   bureaucratique avec `False` et ajout au compteur d'echecs mesure.

5. **Integration EVENING_REFLECTION** : la routine vesperale doit faire un
   rapport sur le mur de la honte, incluant les stats Phase C (goals
   KNOWLEDGE_GAP fermes homeostatiques vs bureaucratiques).

## Section 6 — Risques majeurs

- **Risque 1 : Reward Hacking sur self_consistency** — l'agent peut apprendre
  a generer des reponses consistamment hallucinees pour hacker le score.
  Mitigation : cross-check corpus externe (V2).

- **Risque 2 : Latence** — 3 calls LLM par measure_tension. Si trop lent,
  bloque le prefrontal. Mitigation : cache + async + timeout strict.

- **Risque 3 : Deadlock d'epistemic surprise** — si le LLM local est
  extremement faible sur un topic et genere toujours du random, le score
  reste a 0.2 a vie, le goal n'est jamais ferme. Mitigation : max age des
  goals KNOWLEDGE_GAP (force close apres 7 jours avec completion_mode =
  "aged_out").

- **Risque 4 : Cout GPU** — 3 appels LLM × N goals actifs × frequence checks
  = beaucoup de tokens. Mitigation : budget GPU dedie avec limite.

## Appendix A — Pseudocode d'integration dans `_resolve_tension_source`

```python
def _resolve_tension_source(self, source_organ_name: str):
    if source_organ_name == "desire_engine":
        from core.desire_engine import desires
        return desires
    if source_organ_name == "self_awareness":
        from core.self_awareness import awareness
        return awareness
    if source_organ_name == "synaptic_network":
        from core.synaptic_network import synaptic
        return synaptic
    if source_organ_name == "council":
        from core.council_system import council
        return council
    return None
```

## Status

**Draft complet** — pret pour review par Gemini.
Non implemente. Aucun code modifie sur la production.
