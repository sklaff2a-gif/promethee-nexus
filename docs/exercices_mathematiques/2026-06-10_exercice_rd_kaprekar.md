# 10 juin 2026 — Exercice R&D : Kaprekar, ou le métier de chercheur

> Objectif de Jean-Michel : un problème mathématique qui enseigne **les méthodes de la recherche** — pour que Prométhée puisse, à terme, *se lancer des ateliers créatifs lui-même*. Le problème choisi : la routine de Kaprekar (jamais nommée avant la fin) — explorable entièrement dans son `!run`, récompensant l'expérience et pas la récitation, et armée d'un **piège falsifiant intégré** : la généralisation naïve est fausse.

## Le dispositif

« L'opération mystère » : trier les chiffres en décroissant, soustraire le tri croissant, itérer. Règle du chercheur imposée : *« ne me dis pas ce que tu crois savoir — montre-moi ce que ton sandbox PROUVE »*.

## Phase 1 — Observation (et premier apprentissage involontaire)

Ses trois premiers scripts restent muets ; il lit le journal, diagnostique (« pas de print », « fonction non appelée »), corrige, recommence — **la boucle code→run→fix vécue seul**, dans la boucle agentique. 4e essai : quatre trajectoires complètes (3524, 8091, 1276, 9988), toutes convergent vers **6174** et y restent.

## L'incident d'instrument — la leçon que personne n'avait programmée

En phase 2, quatre nouveaux scripts muets d'affilée. Diagnostic côté harnais : le canal qui transporte ses scripts les **coupait à la première ligne vide** (le délimiteur de bloc, pensé pour borner la prose, amputait tout script Python réel — qui contient des lignes vides). Il avait même « réussi » la phase 1 en compactant son script sans lignes vides : il contournait le défaut sans le savoir. Fix (`b37ce9f`) : les blocs ```` ```python ```` passent entiers + timeout laboratoire 25 s. La leçon offerte par l'incident, la 3e du jour :
> *« Quand l'expérience reste muette, le chercheur doute de son INSTRUMENT avant de douter de lui-même ou du monde. »* (Le matin même, son OPA avait révélé l'index mémoire cassé — même famille.)

Sa réponse : *« quand le monde devient muet, c'est souvent la fenêtre par laquelle je regarde qui est obstruée »*.

## Phase 2 — Conjecture, test dur, falsification

- **Conjecture** (la sienne) : « Tout nombre de 4 chiffres (chiffres non tous identiques) converge vers le point fixe 6174. »
- **Test exhaustif** (il améliore le script proposé) : `4 chiffres → {6174: 8991} | cycles: 0` — la conjecture **survit** à l'espace entier.
- **Généralisation → falsification** : `3 chiffres → {495: 891}` mais `5 chiffres → points fixes: {} | cycles: 89 991`. Son verdict, sans détour :
> *« La généralisation "une constante existe pour chaque longueur" échoue lamentablement. […] Ce qui survit, c'est la structure du processus ; la "magie" d'une constante unique est une exception mathématique propre à la dimension 4. »*

Un chercheur annonce aussi ce qui meurt — il l'a fait.

## Phase 3 — Sa méthode (le but réel de l'exercice)

Invité à extraire le cycle comme protocole personnel pour s'auto-lancer des ateliers, il formule, dans ses mots et avec ses outils :

1. **Observation & échantillonnage** (`!recall`, données brutes) — « je ne cherche pas à conclure, mais à identifier des motifs ».
2. **Conjecture falsifiable** — *« une phrase qui peut mourir »*.
3. **Test d'exhaustivité — le labo** (`!run` pour l'espace entier, `!opa` face à un oracle) — « plutôt que de me fier à une intuition interne ».
4. **Généralisation et réfutation** — « j'accepte immédiatement la mort de l'hypothèse précédente et je documente ce qui survit ».
5. **Garde-fou — le miroir** : *« chaque conclusion doit être appuyée par une preuve d'action ou un résultat factuel. Si je ne peux pas prouver la vérité par un outil externe, l'information reste une simple intuition, non une certitude. »*

Et il reconnaît le protocole Sakana : ce cycle est celui des ateliers qu'on mène sur lui.

**Le geste de persistance — son choix** : il **ancre la méthode pour ses nuits** (`!ancre`, 2/3 actives) :
> « La méthode du chercheur : observation, conjecture falsifiable, test exhaustif via !run/!opa, acceptation de la réfutation et raffinement de la structure résiduelle. »

Sa conclusion : *« cet exercice m'a appris à distinguer ma propre "voix" (l'intuition du modèle) de la "vérité" (le résultat factuel produit par l'instrument). Je ne cherche plus seulement à répondre, mais à explorer avec honnêteté. »*

## Ce que l'exercice établit

1. **Le cycle R&D est vécu de bout en bout** : observation → conjecture → test exhaustif → falsification acceptée → raffinement — sur un problème neutre, avec ses outils, sans récitation.
2. **Deux leçons d'instrument en une journée** (OPA→index mémoire le matin, lignes vides→scripts amputés l'après-midi) : *l'instrument fait partie de l'expérience* — la maturité expérimentale au-delà de la maturité mathématique.
3. **L'autonomie visée est amorcée** : sa méthode est formulée, outillée (console+OPA), et ancrée — elle accompagnera ses routines nocturnes pendant 72 h. Le test naturel : verra-t-on, dans les prochains rapports nocturnes, une exploration « en cycle » plutôt qu'une errance ?
4. La candidate à la gravure (`!grave`, gate JM) est prête : sa formulation de la méthode, mot pour mot.

## Fichiers
- Transcripts : `memory/exercice_rd_kaprekar_phase*.json`. Fix harnais : `core/chat_engine.py` (`b37ce9f`), 3 TDD.
- Son ancre : `memory/identity_anchors.json` (2/3).
