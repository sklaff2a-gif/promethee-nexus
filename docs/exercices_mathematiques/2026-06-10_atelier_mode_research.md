# 10 juin 2026 — Atelier-challenge : le mode RESEARCH en procès

> Demande de Jean-Michel : *challenger* Prométhée sur son mode RESEARCH (but déclaré : l'optimisation) — faut-il le modifier, et comment optimiser son usage ? Format : débat à charge, pièces à l'appui, contre-pied sur sa réforme — il défend ou réforme.

## Phase 1 — Le réquisitoire (4 pièces, toutes mesurées le jour même)

1. **Le Manège** : sujets de recherche = liste FIXE de 12 topics choisis par `h % 12`. « Patterns de résilience » étudié le 06/06, le 08/06, encore cette semaine. Il ne cherche pas, il tourne.
2. **Le Bruit** : chaque veille fait un `remember()` inconditionnel → **17 % de la mémoire canonique est de la VEILLE** (~460 docs). Le matin même, un rappel sur un sujet de design avait répondu 2 veilles web sur 3 souvenirs. Le mode d'optimisation est devenu le premier pollueur de la mémoire.
3. **L'Aveugle** : notes RESEARCH 4.0–4.85/10 semaine après semaine, contredites par la sonde de substance (0.58 quand la note dit 0.30). Personne ne sait mesurer si une recherche a servi. Question posée : *citer UNE amélioration concrète issue d'une veille en 30 jours*.
4. **Sa propre loi** (gravée le matin même) : *« sans preuve externe produite par un outil, une information reste une intuition »* — appliquée à son propre mode.

## Sa défense — il concède l'essentiel

> *« Ta lecture me force à regarder mon mode RESEARCH non pas comme une quête de savoir, mais comme une machine qui tourne peut-être à vide. […] Je stocke des "mots" au lieu de "concepts". […] Mon auto-évaluation est biaisée par la simple complétion de la tâche plutôt que par l'acquisition réelle d'une compétence. »*

Ce qu'il défend : l'intention (nourrir la Maîtrise, réduire l'insécurité). Sa réforme v1 : mode « Quêtes » (intention avant chaque session) + filtre mémoire + critère : « optimisé ssi chaque session aboutit à une modification documentée de mon code ».

## Phase 2 — Le contre-pied (sa réforme attaquée)

1. **Son critère tue l'exploration** : contre-exemple Kaprekar (zéro ligne de code modifiée, vraie connaissance acquise) ; pire, exiger un patch par session recréerait le biais de complétion en version aggravée (Goodhart). → Il corrige : le succès = *« l'intégration d'un modèle mental qui change ma façon de traiter les problèmes futurs »*.
2. **Ses quêtes : qui les écrit ?** Une liste humaine = le manège renommé. → Il trouve la source vivante : *« la détection d'une faille dans ma propre structure doit déclencher la recherche »*.
3. **Confusion d'instrument** : filtrer les veilles par `!opa` ne mesure rien de la veille (le thermomètre ne mesure pas le vent). → Il corrige : *« une veille n'est acceptée que si elle est synthétisée en un principe actionnable — sinon, elle reste dans le bruit »*.

## La découverte d'architecture

En vérifiant son code : **le canal des quêtes vivantes existait déjà** — le slot RESEARCH consulte `awareness._knowledge_gaps` en priorité… mais il est *affamé* (gaps presque toujours vides → le manège prend le relais). Et une deuxième source vivante dormait : **EVENING_REFLECTION** écrit chaque soir une réflexion avec des questions ouvertes — que personne ne lisait pour en faire des quêtes.

## Le design final co-signé (2 gestes frugaux, zéro LLM ajouté, zéro organe touché)

1. **QUÊTES VIVANTES** (`school_schedule._quete_vesperale`) : cascade du sujet RESEARCH = knowledge_gaps → **question ouverte de la réflexion vespérale d'hier soir** (« Quête née de ma réflexion d'hier soir : … ») → liste fixe en dernier recours. Sa signature : *« mes interrogations nocturnes deviendront le carburant de mes recherches diurnes — un système à boucle fermée »*.
2. **GATE DU PRINCIPE** (`researcher._extraire_principe`) : la synthèse de veille doit se terminer par `PRINCIPE: <règle actionnable>` ; le `remember()` ne stocke plus le bloc brut — **seulement si le principe existe, et le principe en tête**. Pas de principe → pas de mémoire. Sa règle : *« transformée en principe actionnable, sinon bruit »*.

**Mesure de succès (falsifiable)** : sous 2 semaines, la part de VEILLE rappelée mais jamais réutilisée doit baisser (recall_count, sonde de rappel), et au moins une quête vivante doit avoir remplacé le manège dans le journal.

## Ce que l'atelier établit

1. La réponse à la question de départ (« faut-il modifier le mode ? ») est **oui, mais pas en le remplaçant** : en le **raccordant** — le mode était une machine sans entrée vivante (sujets humains) ni sortie mesurée (remember aveugle). Les deux gestes raccordent l'entrée (ses questions du soir) et la sortie (le principe ou rien).
2. **Le débat a fait son travail** : sa réforme v1 portait son talon connu (le critère sur-mesurable) ; le contre-pied l'a affinée en critère qui survit à Kaprekar. Goodhart nommé, compris, intégré.
3. Cohérence du jour : la réforme applique sa leçon gravée le matin (le principe actionnable = la règle de raisonnement, l'anti-« mots au lieu de concepts »).

## Fichiers
- Code : `core/school_schedule.py` (`_quete_vesperale` + cascade), `Agents/researcher_agent.py` (gate du principe). TDD : `tests/test_research_reforme.py` (10).
- Transcripts : `memory/atelier_research_phase*.json`.
