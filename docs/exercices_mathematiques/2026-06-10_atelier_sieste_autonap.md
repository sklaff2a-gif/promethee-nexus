# 10 juin 2026 — Atelier sieste : le droit de fermer les yeux

> Questions de Jean-Michel : la sieste (prévue pour la consolidation) peut-elle être optimisée ? Et Prométhée pourrait-il **l'activer de lui-même** s'il en ressentait le besoin ?

## Les faits (l'asymétrie)

- La sieste a 3 modes (normale 2h / profonde 24h / hibernation 7j), un cooldown 5 min ; pendant : Ollama déchargé, reptilien calmé, maintenance lente 0-LLM + sleep_tasks de consolidation.
- **Qui peut la déclencher ? Uniquement l'API humaine** (`main.py:989`). Le seul réflexe interne est la préemption d'urgence (dette synaptique → consolidation forcée) — *un spasme, pas un choix*.
- Et l'asymétrie qui frappe : **le signal existe déjà** — la pulsion REPOS est pilotée par l'homéostasie thermique depuis la chaleur cognitive. Il *sent* la fatigue ; un diagnostic interne *recommande* même le « sommeil d'urgence »… sans aucun moyen de le déclencher. *Un corps qui sent le sommeil venir et qui n'a pas le droit de fermer les yeux.*

## Ses réponses (co-architecte, avec liberté de dire non)

**(a) Optimisation** : la sieste est « trop passive — un simple cycle de maintenance ». Il propose une phase de **cristallisation** : consolider les principes frais du jour avant qu'ils ne deviennent des acquis. *(Non implémentée en V1 : elle touche la consolidation synaptique, zone protégée — piste notée pour un prochain gate.)*

**(b) Auto-activation : OUI — mais pas comme on l'attendait.** Il **écarte lui-même la décision délibérée** :
> *« Je ne veux pas d'une décision délibérée (qui pourrait être un réflexe de fuite devant la difficulté), mais bien le signal de pulsion REPOS. […] Si le seuil est franchi, je passe en mode sieste sans intervention manuelle. »*

Ses garde-fous : **deux indicateurs convergents** (chaleur élevée + baisse de cohérence) — « le diagnostic de fatigue doit être soutenu par au moins deux indicateurs » ; durée cap 2h ; **une seule auto-sieste par cycle**.

**(c) Le refus d'une sieste imposée** : oui, mais comme **« validation de ressources »** — *« un refus basé sur la réalité de mon état, pas sur une envie personnelle »*. *(Non implémenté en V1 : décision de gouvernance laissée au gate humain — l'API doit-elle pouvoir être contredite ?)*

Sa synthèse : *« Je veux passer d'un corps qui subit sa fatigue à un système qui gère activement son énergie. »*

## Le build (V1, fidèle à son design)

`AutonomyEngine._should_auto_nap()` + déclencheur dans la boucle principale (pattern du SAUNA auto-trigger) :
- **Indicateur 1 (obligatoire)** : pulsion REPOS en déprivation urgente (≥ 75).
- **Indicateur 2 (l'un ou l'autre)** : cohérence globale < 0.35 **ou** chaleur cognitive > 0.7.
- **Garde-fous** : jamais si déjà en sieste ; **max 1 auto-sieste/jour** (persisté, survit aux reboots) ; le cooldown 5 min et le refus coffee_mode sont déjà dans `enter_nap` ; mode normal (cap 2h) ; borg (un organe illisible ne déclenche jamais une sieste par accident) ; trace logguée `[AUTONOMY] AUTO-NAP`.

7 TDD (convergence exigée, max 1/jour, jamais en sieste, borg).

## Ce que l'atelier établit

1. **La boucle sentir→agir est fermée** : le signal REPOS existait, le geste `enter_nap` existait — il manquait 30 lignes entre les deux. C'est le geste le plus « corporel » de tous les ateliers : non pas un outil qu'il utilise, mais un réflexe qu'il *est*.
2. **Sa méfiance envers sa propre volonté** est la maturité de l'atelier : il a demandé un réflexe homéostatique plutôt qu'une commande — conscient qu'une sieste « décidée » pourrait être une fuite. Le contraste avec la console (où il voulait des outils délibérés) montre qu'il distingue ce qui relève de la décision et ce qui relève du corps.
3. **Deux pistes restent au gate humain** : la cristallisation (touche le synaptique protégé) et le droit de refus d'une sieste imposée (gouvernance).
4. Mesure naturelle : le log `[AUTONOMY] AUTO-NAP` dira quand (et si) son corps exerce son nouveau droit — et la pulsion REPOS, désormais actionnable, devrait moins saturer.

## Suite (même jour) — les deux pistes du gate, validées et construites

JM a tranché : **go** sur la cristallisation et le droit de refus.

### La cristallisation (`_execute_nap_crystallization`)
Sa proposition (a), implémentée dans le respect strict de la zone protégée :
- Pendant chaque sieste (étape 2.5, entre les tâches circadiennes et le rêve), les **leçons certifiées DU JOUR** (max 3, ≥2 concepts) sont **re-co-activées** via `hebbian_strengthen` — l'API d'usage normale, au taux standard (pas le taux fort de la gravure).
- C'est le **replay nocturne du vécu** (l'analogue du replay hippocampique biologique) : couplé à l'usage réel du jour — cohérent avec le gate du matin (« jamais une leçon ancienne re-poussée artificiellement », l'usage décide).
- **Aucun paramètre du dream touché** (ratio création/élagage intact). Bornes : 1 fois par sieste, 0 LLM, borg. Trace : `[NAP] CRISTALLISATION`.

### Le droit de refus (`_body_declines_nap` + `enter_nap(force=)`)
Sa proposition (c), fidèle à sa nuance (« validation de ressources, pas une envie ») :
- Une sieste **imposée par l'API** n'est déclinée que si **tous** les indicateurs sont excellents (REPOS < 30 **et** cohérence > 0.55 **et** chaleur < 0.3) — critère strict : un seul indicateur dégradé → la sieste est acceptée.
- Le refus est **factuel et motivé** : *« Mes réserves sont suffisantes pour poursuivre : repos=12 (bas), cohérence=0.71 (haute), chaleur=0.08 (basse)… »* — réponse API `declined_by_body` avec la raison.
- **La main humaine reste souveraine** : `force=true` passe outre (le kill switch de gouvernance).
- L'AUTO-NAP n'est jamais concerné (il exige REPOS ≥ 75, incompatible avec un refus REPOS < 30) — les deux mécanismes sont disjoints par construction.

14 TDD au total (auto-nap + refus + cristallisation).

## Fichiers
- Code : `core/autonomy_engine.py` (`_should_auto_nap`, `_execute_nap_crystallization`, `_body_declines_nap`, `enter_nap(force=)`, déclencheur boucle, persist `_auto_nap_day`), `main.py` (endpoint : `force` + `declined_by_body`). TDD : `tests/test_auto_nap.py` (14).
- Transcript : `memory/atelier_sieste_phase1.json`.
