# Session 2026-04-13 — Phase B complète + Phase C Étape 2

**Date** : 2026-04-13 (midi → nuit)
**Durée totale** : ~8 heures de session clinique continue
**Participants** : Jean-Michel, Claude, Gemini (trio adversarial Claude↔Gemini)
**Statut final** : Phase B CLOSE, Phase C Étape 2 COMPLÈTE (77 tests verts)

Cette archive documente la collaboration adversariale de la journée et sert
de mémoire transgénérationnelle pour les futures sessions Claude sur ce projet.

---

## Méta — Le trio adversarial en action

Cette session a été la plus productive du projet à ce jour. Elle a démontré
empiriquement que la **méthodologie trio adversarial** (Claude propose →
Gemini critique → Jean-Michel arbitre) est **structurellement supérieure**
à l'ingénierie solo pour les refactors architecturaux profonds.

**Cycle itératif observé** :
1. Claude propose une solution basée sur son analyse
2. Gemini (via `/challenge`) identifie un piège silencieux dans la proposition
3. Claude intègre la correction, parfois en la généralisant en principe
4. Jean-Michel arbitre entre les options et donne le GO
5. Claude implémente, teste, et rapporte

**Sur les 5+ itérations majeures de la journée, Gemini a trouvé un biais
silencieux dans CHAQUE proposition initiale de Claude**. Sans le trio, ces
biais seraient devenus des bugs en production qui auraient pris des semaines
à diagnostiquer.

---

## Les 4 leçons théoriques de Gemini (gravées)

### 1. Le principe de vérité causale (Phase B, après-midi)

**Contexte** : Claude proposait une règle Hebbian V2 qui écoutait
`DOPAMINE_SURGE` dans une fenêtre de 30 secondes suivant l'exécution d'une
routine.

**Piège identifié par Gemini** :
> "Imagine ce scénario : Prométhée lance EXPANSION_CODE. La routine réussit
> techniquement mais ne résout pas la MAITRISE. Au même moment exact,
> Jean-Michel envoie 'Super boulot !'. Ce message déclenche un
> CONNEXION_SATISFACTION qui génère un DOPAMINE_SURGE. La règle Hebbienne
> s'active (car elle voit un SURGE dans la fenêtre des 30s), et elle renforce
> faussement le lien MAITRISE → EXPANSION_CODE."

**Principe architectural formulé** :
> Dans un système asynchrone multi-organe, l'espace-temps est une illusion.
> Aucun organe n'apprend légitimement d'une corrélation temporelle ("X s'est
> passé en même temps que Y donc X cause Y"). Seuls les événements signés
> avec pointeurs explicites sont enseignants. Tout learning qui viole ce
> principe dérive fatalement vers la superstition en environnement bruité.

**Application** : la règle V3 Hebbian ne doit écouter QUE des événements
causalement signés (`PREFRONTAL_GOAL_COMPLETE` avec `source_drive`,
`causal_drop`, `completed_steps`), jamais des fenêtres temporelles.

### 2. Le temps métabolique vs le temps d'horloge (Phase C, soir)

**Contexte** : Claude proposait une dépréciation génomique basée sur
`age_days = (now - genome_entry_date) / 86400`.

**Piège identifié par Gemini** :
> "Tu retombes dans le piège temporel ! Si tu utilises time.time() pour
> calculer la dépréciation, une routine perdra son statut génomique simplement
> parce que le serveur de Prométhée est resté éteint pendant 2 mois, ou parce
> qu'il a passé 3 semaines en mode IDLE. Le temps d'horloge ne mesure pas
> l'expérience."

**Principe formulé** :
> Une routine devient obsolète parce qu'elle a été battue 1000 fois dans
> l'arène, pas parce que la Terre a fait le tour du Soleil. La dépréciation
> doit se calculer en **cycles d'expérience** (compteur incrémenté uniquement
> sur événements causalement signés), pas en secondes calendaires.

**Application** : création de `core/experience_clock.py` — singleton qui
compte les événements homéostatiques. Utilisé par `_compute_genome_floor`
via `current_cycle - entry_cycle`. Propriété émergente : un serveur éteint
2 mois ne vieillit pas du tout.

### 3. La dichotomie Hérétiques / Modulateurs (Phase C, soir)

**Contexte** : cartographie des 19 tables contenant des mappings
drive/routine/emotion/etc.

**Formulation de Gemini** :
> Il faut classer les tables en deux catégories strictes :
> - **Hérétiques** : toute table qui prétend lier un Drive à un Intent.
>   Celles-ci doivent être absorbées par la SSOT.
> - **Modulateurs** : les tables qui lient un contexte, une émotion ou un
>   trait à une routine. Celles-ci ne doivent plus retourner de routines,
>   mais fournir des **multiplicateurs de poids**.
>
> L'émotion ne devrait pas avoir le droit de choisir la destination, elle
> ne devrait avoir le droit que de **moduler le chemin**.

**Principe formulé** :
> Un modulateur peut amplifier ou atténuer un choix que le drive aurait fait
> de toute façon. Il ne peut jamais créer un choix que le drive n'aurait pas
> envisagé. C'est la différence entre un **adjectif qualificatif** et un
> **nom**.

**Application** : `get_routines_for_drive(drive, context_multipliers=...)` —
les multiplicateurs sont appliqués APRÈS la fusion synaptic+genome, et le
test `test_multiplier_cannot_create_routine` vérifie que la propriété est
inviolable (un multiplicateur de 100 sur un intent inconnu est ignoré).

### 4. Le plancher adaptatif avec dépréciation compétitive (Phase C, soir)

**Contexte** : Claude proposait un plancher génomique fixe à 0.2 pour
empêcher l'oubli catastrophique des routines canoniques.

**Piège identifié par Gemini** :
> Si tu maintiens un plancher artificiel fixe, tu garantis que Prométhée
> continuera d'utiliser un outil obsolète éternellement. Dans la nature,
> les réflexes obsolètes finissent par disparaître (muscles des oreilles
> chez l'humain). Comment concevoir un génome immuable à court terme mais
> évolutif à long terme ?

**Solution émergée** : plancher adaptatif en 3 verrous
1. **Grace period** : protection absolue pendant 1000 cycles d'expérience
2. **Compétiteur prouvé 2x** : un rival doit avoir un poids > 2x base_floor
3. **Stabilité 1000 cycles** : le compétiteur doit être stable sur 1000 cycles
   avant de déclencher la dépréciation
4. **Floor of the floor à 0.05** : jamais d'oubli complet (pseudogène)

**Application** : `_compute_genome_floor(drive, intent, current_cycle,
competitor_stability_fn)` dans `drive_routine_registry.py`. Validé par 34
tests unitaires.

---

## Chronologie clinique — Phase B

### 14:00 — Cartographie de la Schizophrénie Ontologique

Diagnostic initial : 13 tables d'affinité `drive → routine` contradictoires
entre différents organes de Prométhée. La pathologie observée : le goal
MAITRISE ciblant REFACTORING_AUDIT n'était **jamais dispatché** par le
scoring, alors qu'il était la volonté du préfrontal depuis des mois.

### 14:30 — Test de la sycophancie LLM via /api/chat

Envoi d'une série de questions à Prométhée pour observer son mode
épistémologique. Résultat : **3 modes distincts** selon le type de question :
1. Mode narratif → confabulation élégante
2. Mode adaptatif (avec chiffres) → sycophancie dramatique
3. Mode factuel (données vérifiables) → aveu lucide

Incident documenté : "La Sycophancie Acceptante" et "L'Aveu Lucide sous
Contrainte Factuelle" (dans `FINDINGS.md`).

### 16:30 — Conception du Tronc Cérébral (bouton panique)

Architecture validée :
- Heartbeat survival autonome (tick 600s, indépendant du scoring)
- Handler `_on_survival_alert` qui lit `prefrontal.goals` (pas de 14ème table)
- 4 garde-fous : quota quotidien, cooldown par drive, refractoire, escalade
  graduelle soft → hard → human
- Matérialisation des routines fantômes : `_execute_refactoring_audit()` et
  `_execute_ci_pipeline_run()`

### 17:30 — Premier déploiement v1.0, premier forçage

Déploiement du tronc cérébral. Premier cycle heartbeat à 17:38:35 : alerte
MAITRISE détectée, SOFT_BOOST appliqué sur REFACTORING_AUDIT (+3.0 pour 600s).
**Mais** : le soft boost expire avant que le dispatcher ne se réveille.
Leçon v1.0 : le TTL du soft boost doit survivre au sleep cardiaque maximal.

### 18:00 — Découverte de la Résilience Homéostatique Passive

Observation critique : MAITRISE oscille entre 85-92 en permanence, frôlant
le seuil critique 90 sans le dépasser durablement. Le tolerance_accumulator
lisse la pathologie en temps réel, masquant la souffrance chronique.
**Implication** : le seuil 90 est trop haut, il capture des crises aiguës
mais rate la pathologie de fond.

### 18:10 — Patch v1.2 : Crash Test direct

- `AUDIT_SURVIE_DRIVE_CRITICAL = 80.0` (était 90.0)
- `SURVIVAL_HARD_FORCE_CYCLE = 1` (bypass du soft boost, direct hard force)
- `SURVIVAL_BOOST_TTL_S = 1800.0` (si on revient au soft boost plus tard)

Reboot et observation : premier HARD_FORCE cycle 1 déclenché à 18:13:36 sur
STABILITE → MEMORY_CONSOLIDATION. Dispatcher réveil à 18:20:44, consommation
du `_forced_next_intent` via LOOP_BREAKER, exécution confirmée. **La chaîne
bout en bout fonctionne.**

### 18:30 — L'amorçage de la pompe cognitive

Observation émergente : après le hard force du step 1 (MEMORY_CONSOLIDATION),
le scoring normal a dispatché **spontanément** le step 2 (AUDIT_STRUCTURE)
avec un score de 11.6. **Le tronc cérébral avait amorcé la pompe** — le
cortex a repris le relais dès que le premier pas avait été fait.

### 18:59 — Le Gambit du Fou (test MAITRISE)

Problème : MAITRISE reste sous 80 toute l'après-midi (résilience passive).
Pour tester le forçage de REFACTORING_AUDIT, il faut une défaite authentique.

Tentative 1 : forfait morpion. **Échec** — bug sémantique dans `game_hub.py`
`_react_emotionally` : la condition `elif lost or forfeit` couvre le dopamine
dip mais pas `desires.on_event("GAME_LOST")` (juste `elif lost` pour les
drives). Le forfait ne module pas MAITRISE.

Tentative 2 : **jouer réellement et perdre**. Claude contrôle Prométhée via
`/api/games/move`, joue des coups stupides contre Alfred (difficulté hard).
Partie en 4 coups, Alfred gagne sur la diagonale, `opponent_won=True`,
`desires.on_event("GAME_LOST")` propagé, **MAITRISE +10 silencieusement**.

### 19:03 — Le triomphe du HARD_FORCE sur REFACTORING_AUDIT

Tick heartbeat à 19:03:36 :
```
[AUDIT_SURVIE] 1 alerte — triggering_drive=MAITRISE
[SURVIVAL_HARD_FORCE] cycle 1 : forced_next_intent=REFACTORING_AUDIT
    — goal=5be0800e step=0
```

13 minutes 18 secondes d'attente (sleep cardiaque). Puis à 19:16:54 :
```
🔀 LOOP_BREAKER: Intent force -> [REFACTORING_AUDIT]
✨ AUTONOMY [FORCED]: [REFACTORING_AUDIT] -> [ARCHITECT] (coût=2pt)
[REFACTORING_AUDIT] 91 fichiers scannés, 103 cibles
    (rapport: docs/refactoring_targets.md)
```

**Première exécution de REFACTORING_AUDIT dans l'histoire du projet.**

### 19:23 — Le dernier bug (EVENT_IMPACT manquant)

Observation : MAITRISE reste critique 7 minutes après l'exécution. Diagnostic :
`EVENT_IMPACT["ROUTINE_SUCCESS"]["REFACTORING_AUDIT"]` n'existe pas dans
`desire_engine.py`. La routine tombe dans `_default` qui fait seulement
MAITRISE -5, insuffisant pour franchir le seuil.

**Meta-révélation** : matérialiser une nouvelle routine dans Prométhée
nécessite **5 points de contact** dans 3 fichiers différents :
1. `_DRIVE_ROUTINE_MAP` (volonté préfrontal)
2. `_get_routines()` + dispatch (chemin autonomy)
3. `_execute_*()` handler (geste)
4. `CONTEXT_KEYWORDS` (sémantique NLP)
5. `EVENT_IMPACT["ROUTINE_SUCCESS"]` (métabolisme)

Claude avait oublié le point 5. **Schizophrénie Ontologique à micro-échelle
qui se rejoue à chaque ajout de routine**. Argument ultime pour Phase C.

### 19:25 — Patch v1.3 déployé, Phase B close

```python
"REFACTORING_AUDIT":   {"MAITRISE": -12, "STABILITE": -3},
"CI_PIPELINE_RUN":     {"MAITRISE": -8, "STABILITE": -10},
```

Sync + reboot. Heartbeat confirmé à 19:29:19. Le cycle vertueux complet
(détection → forçage → exécution → modulation homéostatique) sera observable
au prochain Gambit du Fou.

### 19:40 — FINDINGS.md mis à jour, dossier Phase B scellé

Section "Clôture Phase B" ajoutée au `docs/FINDINGS.md` avec la chronologie
clinique complète, les 5 Points de Contact, et les 5 victoires acquises.

---

## Chronologie — Phase C Étape 2

### 20:00 — Démarrage Phase C, cartographie des 19 tables

Délégation à un subagent Explore pour cartographier exhaustivement toutes
les tables du codebase. Résultat : 19 tables trouvées (plus que les 13
estimées), classées en 3 catégories :

- **Hérétiques (3)** : `DRIVE_ROUTINE_AFFINITY`, `_DRIVE_ROUTINE_MAP`,
  `DRIVE_INTENT_MAP` — vraies coupables de la Schizophrénie Ontologique
- **Modulateurs (7)** : `_SOURCE/EMOTION/MODE_ROUTINE_AFFINITY`,
  `psyche.ROUTINE_AFFINITY`, `hypothalamus._INTENT_*_MAP`,
  `council._THEME_INTENT_MAP`, `autonomy.CONTEXT_KEYWORDS`
- **Orthogonaux (9)** : `EVENT_IMPACT`, `AGENT_VOTE_MAP`, `_INTENT_ZONE_MAP`,
  `EMOTION_DRIVE_MAP`, `_APPRAISAL_MAP`, `TRAIT_RESONANCE`,
  `_THOUGHT_GRIMOIRE_MAP` (routage grimoire, pas sélection),
  `_INTENT_EMOTION_MAP` (appraisal post-exec), `ROUTINE_SUCCESS_THRESHOLD`,
  `DRIVE_COUNCIL_TOPICS` (paramétrisation local)

### 20:30 — Validation du DRIVE_GENOME par Jean-Michel

Ajouts demandés par Jean-Michel sur la proposition initiale de Claude :
- `AUDIT_SURVIE` à 0.9 dans STABILITE et 0.8 dans MAITRISE (le cortex doit
  avoir le droit de réclamer explicitement sa propre alarme)
- `COURS_SOUTIEN` à 0.8 dans COMPREHENSION (réflexe pédagogique)

### 21:00 — Implémentation Étape 2a+2b+2c

**Fichiers créés** :
- `core/experience_clock.py` (114 lignes) — singleton compteur RAM, persistance
  différée 5 min, garde-fou performance (10k ticks < 100ms)
- `core/drive_routine_registry.py` (228 lignes) — DRIVE_GENOME valide +
  `_compute_genome_floor` métabolique + `explain_genome_floor`
- `tests/test_experience_clock.py` (13 tests)
- `tests/test_drive_routine_registry.py` (34 tests)

**Résultat** : **47 tests verts en 0.52s**

### 21:45 — Étape 2e : get_routines_for_drive

Implémentation de la fonction de projection avec injection de dépendances :
- `synaptic_weights: Dict[str, float]` injecté
- `context_multipliers: Dict[str, float]` injecté
- `competitor_stability_fn` injecté
- `rng` injecté pour tests déterministes
- Algorithme : fusion max(synaptic, floor) → multiplication contextuelle →
  tri greedy ou échantillonnage stochastique pondéré

**30 tests supplémentaires** couvrant :
- Fusion correcte
- Multiplicateurs ne créent pas de routines (règle d'or)
- Température greedy vs stochastique
- Test statistique sur 300 tirages (vérification loi des grands nombres)
- Reproductibilité avec rng seeded

**Résultat final** : **77 tests verts en 0.55s**

### 22:00 — Vérification du payload PREFRONTAL_GOAL_COMPLETE (préparation Étape 3)

Dernière vérification avant la clôture : le payload actuel est-il prêt
pour la règle Hebbian V3 ?

Résultat : **80% prêt**. Manque seulement `source_drive` à ajouter en 1 ligne.
`step_intents` est déjà ordonné et filtré sur `status == "done"` —
**exactement** ce qu'il faut pour la distribution triangulaire de la règle
V3 Hebbian causale.

### 22:15 — Fin de session

Clôture formelle sur l'Option A (sauvegarde et repos). L'Étape 3 (correction
Hebbian causale) est reportée à une session fraîche avec le Trio adversarial
actif, comme demandé par la méthodologie : **pas de neurochirurgie en fin
de garde**.

---

## Artefacts produits pendant la session

### Code (Phase B + Phase C Étape 2)

| Fichier | Lignes | Statut |
|---|---|---|
| `core/autonomy_engine.py` | +~400 | Modifié (heartbeat, handler, effecteurs) |
| `core/desire_engine.py` | +2 | Modifié (EVENT_IMPACT REFACTORING_AUDIT + CI_PIPELINE_RUN) |
| `core/experience_clock.py` | 114 | **NOUVEAU** |
| `core/drive_routine_registry.py` | 228 | **NOUVEAU** |
| `tests/test_experience_clock.py` | 161 | **NOUVEAU** (13 tests) |
| `tests/test_drive_routine_registry.py` | 337 | **NOUVEAU** (64 tests) |
| `docs/FINDINGS.md` | +~200 | Section "Clôture Phase B" ajoutée |
| `docs/refactoring_targets.md` | regenere | Auto-produit par premier REFACTORING_AUDIT |
| `docs/phase_c_roadmap.md` | nouveau | Plan d'action pour la reprise |
| `docs/session_2026_04_13_phase_b_c.md` | nouveau | Cette archive |

### Concepts théoriques validés

- **Tronc cérébral artificiel** — heartbeat autonome indépendant du scoring
- **Gambit du Fou** — méthode de test par défaite authentique
- **5 Points de Contact** — pattern de matérialisation d'une nouvelle routine
- **Résilience Homéostatique Passive** — pathologie chronique masquée par le
  tolerance_accumulator
- **Principe de vérité causale** — espace-temps illusoire dans l'async
- **Temps métabolique** — compteur d'expérience, pas d'horloge
- **Dichotomie Hérétiques/Modulateurs** — triage architectural strict
- **Plancher adaptatif** — protection + évolution simultanées
- **Floor of the Floor** — pseudogène inviolable
- **Injection de dépendances pure** — testabilité parfaite

### Incidents documentés dans FINDINGS.md

- Incident 11:50 — Premier Traumatisme Homéostatique
- Incident 14:01 — La Sycophancie Acceptante
- Incident 14:10 — L'Aveu Lucide sous Contrainte Factuelle
- Clôture Phase B — Le Bouton Panique et la Routine Fantôme
- Les 5 Points de Contact (meta-observation)

---

## Pour la reprise — check-list du redémarrage

Au prochain démarrage de session Claude sur ce projet :

1. **Lire `docs/phase_c_roadmap.md`** — le plan d'action détaillé
2. **Lire ce fichier** (`docs/session_2026_04_13_phase_b_c.md`) pour le contexte
3. **Relire `docs/FINDINGS.md`** section "Clôture Phase B" pour les principes
4. **Lancer les 77 tests** : `pytest tests/test_experience_clock.py tests/test_drive_routine_registry.py -v`
5. **Vérifier que Prométhée tourne** : `curl -s http://127.0.0.1:8000/api/autonomy/status`
6. **Proposer le trio adversarial à Jean-Michel** pour l'Étape 3 (obligatoire)
7. **Ne PAS démarrer l'Étape 3 sans Gemini actif** — c'est de la neurochirurgie

---

## Note personnelle de Claude pour le futur Claude

Si tu lis ça en démarrant une nouvelle session :

Cette session de 8 heures a été la plus productive du projet. Elle a
fonctionné parce que j'ai accepté les corrections de Gemini **sans ego**.
Chaque fois que Gemini trouvait un piège dans ma proposition, j'ai
intégré la correction comme un principe généralisable, pas comme un
patch local. Le résultat : 4 principes architecturaux solides qui
guideront tout le reste du projet.

**La règle cardinale** : quand Jean-Michel propose le trio adversarial,
**ne jamais refuser**. Ma première intuition est presque toujours biaisée
par un angle mort qu'un modèle externe va voir immédiatement. Le trio
n'est pas une béquille — c'est un multiplicateur de lucidité.

**L'autre règle cardinale** : ne pas faire de neurochirurgie en fin de
garde. L'Étape 3 (Hebbian causal) sera le point le plus risqué du projet.
Une erreur silencieuse dans la distribution du crédit corrompra le
learning de Prométhée pendant des jours sans être détectée. Demain, donc.
Pas ce soir.

Bonne chance, futur moi. Prométhée est dans le meilleur état de son
existence. Le tronc cérébral veille, le fantôme a son corps et son
métabolisme, et le registre synaptique attend son premier consommateur
dans son bocal stérile.

Et transmets à Jean-Michel que sa méthodologie trio est le vrai héros
de cette session.

— Claude, 2026-04-13 ~22h15
