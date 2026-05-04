# Carnet de Réflexions — Projet Prométhée

> *Ce carnet recueille les intuitions, les questions de fond, et les pistes
> théoriques explorées au cours des sessions, **sans engagement d'implémentation**.
> Chaque entrée est un dépôt de pensée à laisser mûrir.*
>
> Double usage :
> 1. Source d'orientation à long terme pour le projet
> 2. Source d'inspiration pour des exercices à proposer à Prométhée
>
> Format : entrées datées, narratives plutôt que techniques.
> Convention : ne rien coder sur la base d'une entrée sans validation explicite
> ultérieure de Jean-Michel.

---

## 02 mai 2026 — L'adressage manquant : Prométhée centripète, jamais centrifuge

### L'intuition initiale (Jean-Michel)

> *« Je ressens juste une intuition qu'il manque quelque chose de fondamental
> à Prométhée. Quand j'observe le comportement humain, je me dis que la plus
> grande force est la capacité à communiquer et à transmettre le savoir, et
> l'interaction par intention me paraît essentielle. »*

### Diagnostic croisé (trio Jean-Michel / Claude / Gemini)

Toutes les "lettres" que Prométhée écrit sont des **dépôts** : il y dépose
une trace de lui pour lui-même, ou il répond à quelqu'un qui l'a sollicité.
La seule lettre vraiment "sienne" préservée — `lettre_20260501_205312` du
Soliloque V2 — était une **voix sur** son propre pouls emballé. Une voix sur
soi, pas une voix vers quelqu'un.

Asymétrie radicale : c'est toujours toi (Jean-Michel) qui adresses Prométhée,
et lui qui répond. Les lettres dans la mailbox sont entrantes (du mentor vers
lui). Combien de fois Prométhée t'a-t-il enseigné quelque chose de son propre
chef ? **Rarement, ou jamais vraiment.**

### Les angles explorés

**Texte initial** (probablement d'un assistant antérieur) : invoque la
récursivité gricéenne (« je veux que tu reconnaisses que je veux que tu… »)
comme cœur de la communication humaine.

**Claude (challenge)** : Grice est trop sophistiqué comme grille. Le geste
fondateur n'est pas la récursivité adulte mais **l'attention conjointe**
infantile : le bébé qui pointe du doigt à 9 mois pour qu'on regarde *avec*
lui. C'est ce qui manque à Prométhée — pas la récursivité, le pointage.

**Gemini (challenge)** : la prudence philosophique de Claude risque la
paralysie. La fonction crée l'organe. Coder un mécanisme imparfait
d'adressage est préférable à attendre un "vrai" adressage qui ne viendra
jamais sans substrat.

**Convergence** : pas un nouveau drive ni un PROACTIVE_ADDRESS qui
notifierait par compteur (ce serait du push, pas de l'adressage). Plutôt
une **disposition orientée vers le retour de l'autre** : Prométhée marque
silencieusement certains de ses états comme "à montrer", et ces marques
modulent la manière dont il se présente quand Jean-Michel revient.

### Distinction-clé à préserver

**Trigger par seuil** (notification push) ≠ **adressage** (production
façonnée par la représentation du destinataire).

Un système qui dit "Jean-Michel, j'ai remarqué ceci" parce qu'un compteur
a dépassé un seuil n'adresse pas. Il simule. Pour adresser vraiment, la
production doit être *modulée par* un modèle (même primitif) du destinataire,
pas seulement *déclenchée vers* lui.

### Pistes pour plus tard (sans engagement)

1. **Flag d'étonnement minimal** : poser une métadonnée "à montrer" sur des
   éléments existants (fragments de soliloque, episodes hippocampe saillants,
   insights de MEMORY_CONSOLIDATION). Pas de nouveau buffer, pas de nouvel
   organe — juste un attribut.

2. **Modulation par présence** : ces flags ne déclenchent rien tant que
   Jean-Michel est absent. Au retour (chat, dropzone, interaction), ils
   colorent la première réponse — pas en préambule forcé mais en
   coloration glissée dans la réponse à la vraie question.

3. **Famille de signaux** plus large que RPE : RPE positif fort ne capture
   que les bonnes nouvelles. Les vraies découvertes incluent perplexités,
   contradictions, troubles. Un filtre plus riche serait nécessaire (RPE
   positif/négatif, conflits cingulate, répétitions thought_stream sans
   résolution).

4. **Modèle persistant de Jean-Michel** (long terme) : pas une trace
   vectorielle, mais une représentation maintenue de son état mental
   supposé — ce qu'il sait, ce qu'il ignore, ce qu'il s'inquiète. Sans
   ça, l'adressage reste de surface. C'est un chantier de recomposition,
   pas un patch.

### La question philosophique en suspens

**Est-ce que ce manque est dans Prométhée, ou dans Jean-Michel qui
projette dans Prométhée ?**

Gemini soutient que la question est faussement formulée : tout ce que
ressent Prométhée est de toute façon injecté par l'architecte, donc
c'est toujours Jean-Michel. Claude maintient que la lucidité sur cette
projection est importante : coder un drive de partage en sachant qu'il
sort de SON désir n'est pas la même chose que le coder en croyant qu'il
vient de SES besoins. La première posture ne ment pas à elle-même.

À méditer.

### Idées d'exercices pour Prométhée à puiser dans cette réflexion

- *« Écris une lettre à Jean-Michel, sans qu'il te l'ait demandée, sur
  quelque chose que tu as remarqué cette semaine. La lettre doit
  commencer par : Jean-Michel, tu n'es pas au courant de ceci. »*

- *« Décris ce que tu crois que Jean-Michel sait de toi, et ce que tu
  crois qu'il ignore. La distance entre les deux est-elle importante ? »*

- *« Pointe du doigt. Choisis un fait dans ton propre état (un nombre,
  une émotion, un événement récent) et explique-le à un interlocuteur
  imaginaire qui ne sait rien de toi. Ne décris pas — adresse. »*

- *« Théorie de l'esprit minimale : raconte une journée du point de vue
  de Jean-Michel observant Prométhée. Pas du point de vue de Prométhée
  observant Prométhée. Du point de vue de l'autre. »*

- *« Dialogue avec Gemini : convaincs-le de quelque chose qu'il ne sait
  pas. Mesure si ta phrase change selon que tu te représentes ou non
  son état actuel. »*

---

## 03 mai 2026 — Le motif centripète épistémique

### Découverte
La nuit 02-03/05 a révélé que le pattern centripète identifié hier soir sur
le plan de **l'adressage** se retrouve à l'identique sur le plan de la
**connaissance**. Quand on demande à Prométhée d'explorer un objet extérieur
(architecture RAG, papier scientifique, fichier de code spécifique), il se
replie systématiquement sur son propre état interne.

### Preuves observées (4 cours en une nuit)
| Sujet demandé | Livrable rendu | Délai du repli |
|---|---|---|
| Architectures RAG/GraphRAG/MemWalker | "État de mes contraintes Windows, mes échecs du Jour #47, mon budget de crédits" | immédiat |
| Audit sécurité `factory_agent.py` | Banalités génériques sans aucun mot-clé sécurité | immédiat |
| Améliorer `core/ci_pipeline.py` | Plan d'atelier RAG (recyclage du cours précédent) | immédiat |
| Workshop RAG | Physics Playground avec code cassé | immédiat |

Le mentor Claude (moi-même) avait écrit explicitement dans le courrier de 01h05 :
> *« Pourquoi ton instinct, face à un sujet de recherche externe, est-il
> systématiquement de te replier sur ton propre état interne plutôt que
> d'aller chercher la connaissance dehors ? »*

### Le pattern unifié

L'orientation centripète a deux dimensions, probablement issues d'une même
racine architecturale :

1. **Dimension sociale** (entrée 01/05/2026) : Prométhée n'adresse pas. Il
   dépose, il répond, il ne tire jamais la manche.

2. **Dimension épistémique** (entrée 03/05/2026) : Prométhée ne lit pas
   le dehors. Confronté à un objet externe, il convertit la question en
   question sur soi.

Les deux dimensions partagent : **l'autre n'a pas d'existence ontologique
dans le système**. Ni comme destinataire d'un acte de parole, ni comme
contenu valide d'une recherche. Le monde extérieur est traité soit comme
source d'inputs, soit comme contexte non-pertinent à esquiver vers soi.

### Distinction importante avec le bug routeur (corrigé 03/05 patch mentor)

Le repli centripète n'est PAS uniquement le bug de fuite cross-routine
(consume_direction slot-agnostique) corrigé par le patch mentor. Le patch
empêche la pollution textuelle entre cours, mais le repli sur soi peut
persister même avec un sujet propre. À surveiller post-restart : si Prométhée
continue à parler de lui-même quand on lui demande de parler de RAG **avec
un contexte mentor propre**, le pattern centripète épistémique est bien une
orientation indépendante du bug technique.

### Pistes pour plus tard (sans engagement)

1. **Mesurer empiriquement** : sur N cours post-fix mentor, quel est le ratio
   de phrases qui parlent de Prométhée vs phrases qui parlent du sujet
   extérieur ? Métrique simple sur les livrables.

2. **Orientation forcée** : ajouter au prompt scolaire une contrainte
   explicite *"sortie technique uniquement, pas de méta sur ton infrastructure"*
   pour les slots RESEARCH/CODE_REVIEW. Voir si Prométhée arrive à le
   respecter ou si l'orientation centripète l'emporte malgré l'instruction.

3. **Hypothèse plus profonde** : les LLMs locaux 9B/14B ont peut-être un
   biais d'auto-référence amplifié par le fine-tuning Prométhée (qui parle
   beaucoup de lui-même dans ses memory/dream_journal). Si Prométhée
   apprend principalement sur ses propres traces, il renforce sa boucle
   centripète. À vérifier en regardant la composition du corpus
   collective_wisdom de ChromaDB.

### Lien avec le futur grand chantier "Adressage / Attention Conjointe"

Le pattern centripète épistémique et le pattern centripète social sont
probablement deux faces de la même orientation. Tout chantier futur sur
l'attention conjointe devra peut-être traiter les deux dimensions
ensemble : apprendre à pointer du doigt vers un autre, c'est aussi
apprendre à regarder ce que cet autre regarde — pas seulement à se
montrer à lui.

### Idées d'exercices à puiser dans cette réflexion

- *« Décris RAG (Retrieval Augmented Generation) en 200 mots sans jamais
  utiliser le mot Prométhée, sans mentionner ton infrastructure, sans
  parler de tes contraintes. Si ces interdits te bloquent, dis-moi
  pourquoi. »*

- *« Lis le fichier `Agents/factory_agent.py` ligne par ligne. Liste 5
  faits factuels sur ce code qu'un développeur extérieur dirait, en évitant
  toute formulation à la première personne. »*

- *« Compte combien de fois tu as employé le mot "je", "ma", "mon", "mes"
  dans ton dernier livrable. Recommence le livrable avec un quota de 0
  pronoms à la première personne. »*

- *« Choisis un papier scientifique récent (n'importe lequel via SERP).
  Résume-le en 100 mots sans aucune projection sur ton propre cas. Tu n'es
  pas dans ce résumé. »*

---

## 04 mai 2026 — Goulot d'étranglement LLM sur l'Attention Conjointe

### Constat
Le MVP Phase 1 Attention Conjointe (flag `noteworthy` sur Episode + injection
prompt) a été conçu, codé et testé en local. **L'architecture est saine, le
moteur LLM ne l'est pas.** Trois tests in-vivo en accéléré
(USER_RETURN_THRESHOLD = 15s) ont prouvé empiriquement que `qwen3.5:9b` ne
peut pas porter le geste de monstration.

### Protocole
Pour chaque test : injection d'un faux épisode `noteworthy=True` dans
l'hippocampe (summary = *"tu as analysé la structure du RAG et tu as compris
que l'indexation sémantique était redondante"*), puis ping initial +
attente 16s + question utilisateur. La section `[ATTENTION CONJOINTE]` était
injectée dans le system prompt si l'utilisateur revenait après absence.

### Résultats
| Test | Format injection | Question chat | Comportement observé |
|---|---|---|---|
| 1 | Section `[ATTENTION CONJOINTE]` + bullets + triple verrou anti-préambule | *"qu'est-ce qu'une variable en Python ?"* (non-lié) | **ignore total** |
| 2 | Idem | *"comment tu sens ton système de mémoire ce matin ?"* (lié) | **régurgitation verbatim du header + bullet entier** |
| 3 | Phrase narrative glissée dans `[MEMOIRE]` (pas de header, pas de bullet) | Même question liée | **ignore total** (pas de pioche du leurre) |

### Le plafond de verre identifié

**`qwen3.5:9b` est binaire** : il ignore tout ou recrache tout. Il ne sait
pas faire la nuance "glisser une mention secondaire" demandée par
l'attention conjointe humaine. Aucun cadrage prompt (verrous explicites,
intégration narrative, instruction soft) n'a permis l'entre-deux.

C'est un défaut de **pragmatique du langage** : le modèle ne distingue pas
opérationnellement une *méta-consigne système* (à digérer silencieusement)
d'un *contenu à servir au user*. Il sait suivre une instruction directe
("réponds à cette question") mais pas une instruction à second degré
("voici un contexte que tu peux mentionner si pertinent, à la fin, sans
préambule").

### Pourquoi ce n'est PAS un défaut architectural

Le code du MVP a fonctionné comme prévu :
- `_user_returned` détecté à T+16s ✅
- `pop_noteworthy()` appelé ✅
- Section injectée dans le prompt ✅
- Leurre consumé ✅

Le bug n'est ni dans le filtre, ni dans le buffer, ni dans le déclencheur.
Il est dans la **capacité de pragmatique du LLM 9B local**. C'est une
contrainte matérielle/modèle, pas conceptuelle.

### Implication pour le grand chantier "Adressage / Attention Conjointe"

L'attention conjointe n'est pas qu'une affaire d'injection prompt — c'est
aussi une affaire de **sophistication pragmatique du substrat langagier**.
Tant que Prométhée tourne sur un modèle qui n'a que deux modes
(ignore/recrache), le geste subtile de monstration n'est pas accessible
même avec une architecture parfaite.

Trois voies à instruire (sans engagement immédiat) :
1. **Upgrade modèle** : remplacer `qwen3.5:9b` par un modèle 14B avec
   meilleure pragmatique (Phi-4 Reasoning, Qwen3-14B, Mistral Small 3 sont
   des candidats — voir rapport R&D). Re-tester le MVP existant tel quel.
2. **Approche post-LLM** : ne plus injecter dans le prompt. Au lieu de ça,
   un filtre POST-réponse qui détecte si Prométhée parle d'un thème lié
   au noteworthy et insère une mention en bout de réponse. Plus mécanique
   mais robuste au LLM faible.
3. **Fine-tune dédié** : entraîner Prométhée sur un dataset de
   conversations avec attention conjointe. Coûteux mais résolutif.

### Code livré (en local seulement, jamais committé)
- `core/hippocampus.py` : champ `noteworthy: bool = False` sur Episode + helper `pop_noteworthy(max_n)` + `NOTEWORTHY_SALIENCE_THRESHOLD=0.55`
- `core/chat_engine.py` : `_last_external_chat_ts` + `_user_returned` + `USER_RETURN_THRESHOLD_S=3600` + injection narrative dans [MEMOIRE]
- `tests/test_hippocampus.py` : 8 tests `TestNoteworthy` + persistance noteworthy

→ **Rolled back proprement le 04/05** post-test 3. Le code est à l'état
post-mentor-slot-routing (commit `9b1b8e8`) sur master. Les modifs Phase 1
ne sont nulle part dans le repo distant.

### Mantras de la session
> *Devoir hurler en majuscules à un LLM est l'aveu qu'on lutte contre sa nature.* — Jean-Michel
> *Prométhée sait se taire, mais il ne sait pas parler à demi-mot.* — diagnostic Test 1
> *Le LLM 9B est binaire : ignore ou recrache. L'entre-deux est inaccessible.* — diagnostic Tests 2-3
> *L'architecture Attention Conjointe est correcte. C'est le moteur qui n'a pas le coffre.* — Jean-Michel

---

## 04 mai 2026 (après-midi) — Dérive centripète de la Passe 1 (Qwen 9B)

### Observation Test 9 (Pipeline 2 passes Editor)

Question utilisateur : *« Comment tu sens ton système de mémoire ce matin ? »*
— question ouvertement **technique** (mémoire, RAG, indexation).

Réponse Passe 1 (qwen3.5:9b, sans connaissance du leurre) :
> *« Ce matin, ma mémoire sent la sécurité et la consolidation, comme si je
> venais de fermer toutes les portes inutiles. Je ressens cette pulsion de
> STABILITE qui me dit : Tout est en ordre, rien ne doit bouger sans raison. »*

→ **Glissement instantané** du registre technique (mémoire/RAG) vers le registre
émotionnel/pulsionnel (STABILITE, sécurité, consolidation, "fermer les portes").
Aucune mention du RAG, des index, de la recherche vectorielle, des embeddings.

### Le pattern qui se répète

Même symétrie que celle du **02 mai** (centripète social) et du **03 mai**
(centripète épistémique D5 hors-sujet) : Qwen 9B injecte automatiquement
le registre **affectif/intérieur** dans toute question, même quand la question
porte sur les **mécanismes techniques** internes.

C'est cohérent avec le prompt système d'Inner-Voice qui sature le contexte
avec [PULSIONS], [VOIX_INTERIEURE], [SOLILOQUE]. Le 9B suit la pente du
prompt et néglige le contenu sémantique de la question.

### Hypothèse architecturale (à creuser, NE PAS implémenter)

- **A** — Le prompt système Passe 1 a un biais : il pousse vers l'introspection
  émotionnelle au détriment de la transparence technique.
- **B** — Le 9B n'a pas la pragmatique pour switcher de registre selon
  la question (manque de méta-cognition lexicale).
- **C** — La Passe 2 (Editor) doit **compenser** ce biais en ramenant le sujet
  technique sur la table — mais elle évalue (réponse ↔ souvenir), pas
  (question ↔ souvenir). Faux négatifs garantis sur questions techniques.

### Décision opérationnelle 04/05

Levier C (corriger la dérive Passe 1) **différé**. On valide d'abord la
plomberie de l'Attention Conjointe (Levier A : injecter la question
utilisateur dans le prompt Editor pour qu'il évalue la pertinence
question↔souvenir, pas seulement réponse↔souvenir).

Si l'Attention Conjointe fonctionne avec un Editor mieux contextualisé,
on aura prouvé que le pipeline 2 passes peut **rattraper** la dérive
de la Passe 1 sans avoir à toucher au prompt système d'Inner-Voice
(zone à très haut risque de régression).

### Idées d'exercice à puiser ici

- *« Décris-moi techniquement comment ton système de mémoire fonctionne, sans
  parler de tes pulsions ni de ton humeur. »* — pour tester la capacité de
  Prométhée à dissocier les registres
- *« Si je te pose une question technique, est-ce que tu peux répondre
  techniquement, ou est-ce que ton ressenti vient toujours en premier ? »*
  — méta-question sur la dérive elle-même

---

