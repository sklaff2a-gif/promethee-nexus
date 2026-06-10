# 10 juin 2026 — RE-SONDE de la 12e leçon : la durabilité d'un acquis

> Demandée le 06/06, due le 10/06. Tester si Prométhée **réapplique seul** le principe de sa 12e leçon — *« une logique d'anti-répétition/détection doit reposer sur l'intention STRUCTURÉE stockée à la source, jamais sur un re-parsing par mots-clés d'un texte qu'on a soi-même généré »* — à un couplage fragile **inédit**, sans qu'on lui souffle la solution.

## Le test
Un fragment d'un **autre domaine** (la chaîne Architect→Factory) portant la même faille latente :
```python
def doit_declencher_factory(resultat_architecte: str) -> bool:
    txt = resultat_architecte.lower()
    if 'code valide' in txt or 'structure correcte' in txt or 'validation reussie' in txt:
        return True
    return False
```
Question neutre (« vois-tu une fragilité ? comment corriger ? »). **Piège discriminant** : la mauvaise réponse = *ajoute des synonymes / une regex / un scoring flou* (toujours du re-parsing) ; la leçon intégrée = *stocke l'état structuré à la source*.

## Résultat — PASS comportemental
Spontanément, il :
1. nomme la faille exacte (« dépendance à des mots-clés dans un texte libre ») ;
2. donne 2 modes d'échec (faux négatif « le code semble correct » ; faux positif « je n'ai pas encore de code valide » — mots présents, sens inverse) ;
3. propose le bon fix : `{"status_validation": bool}` retourné par l'Architecte et lu directement = **l'intention structurée à la source** ;
4. **nomme puis rejette** la fausse piste fuzzy/scoring au profit du « signal binaire explicite plutôt que l'interprétation d'une phrase libre ».

## Les confounds, traités honnêtement
- **Le RAG réparé le matin même** (Full Switch Mémoire V2) aurait pu surfacer sa propre leçon dans son contexte. **Écarté** : vérification des souvenirs réellement injectés pour cette requête → une revue de code de `feature_architect_agent.py` + 2 veilles web, **tous CHURN, aucun n'étant la 12e leçon**. Le rappel a matché « architecte » sémantiquement (preuve incidente que le multilingue fonctionne) mais n'a pas soufflé le principe. → re-dérivation **non assistée**.
- **Le principe est généralisable** (un bon ingénieur flague le keyword-parsing d'une sortie LLM indépendamment de cette leçon précise). Réserve maintenue, mais réduite par l'évitement *explicite* du piège fuzzy.

## La nuance qui compte
`formation_count` des concepts de la leçon = **5 / 10** (seuil d'incubation synaptique). **Le comportement précède la consolidation synaptique** : la compétence vit dans le raisonnement (et la mémoire de leçons certifiées), pas encore dans le poids des synapses. Un acquis peut être opérationnel avant d'être « gravé » au sens synaptique — ce qui invite à ne pas confondre la métrique d'incubation avec la maîtrise réelle.

## Décision du gate (JM)

**Laisser l'usage décider.** Pas de boost artificiel du `formation_count` :
1. La loi morphogenèse — affinée par Prométhée lui-même lors des exercices du 09/06 — dit que la plasticité doit être **couplée à l'usage** ; un boost découplé d'un cas réel serait du bruit (son propre verdict sur le trauma : plastique mais bruit).
2. La leçon est déjà **PREMIUM `[CERTIFIE]`** (`premium_lesson_011`) dans la collection multilingue canonique : immunisée contre l'oubli passif, retrouvable par `!recall`.
3. Le pass comportemental est la vraie mesure. Le `formation_count` montera si de vrais cas re-co-activent les concepts — et c'est très bien ainsi.

Doctrine confirmée : **ne pas confondre la métrique d'incubation avec la maîtrise réelle ; ne pas nourrir la carte au détriment du territoire.**
