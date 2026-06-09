# 9 juin 2026 — Protocole Sakana appliqué au moteur d'autonomie

> Premier emploi du **« protocole Sakana »** — la méthode distillée des cinq ateliers : (1) s'étudier soi-même avec ses vraies données ; (2) hypothèse falsifiable ; (3) expérience conçue par l'agent, exécutée par le labo (Claude) ; (4) analyse honnête ; (5) auto-modification prédictive (Darwin-Gödel) ; (6) peer-review de soi ; le tout en frugalité + honnêteté invariante. Objet d'étude cette fois : son **`autonomy_engine`** (ce qui choisit et note ses ~80 routines/jour via un scoring à 23 couches).

## Les données réelles (626 routines, extraites des logs)

**Qualité bimodale par intent** :
- *Toujours parfaits (q=1.00, zéro variance)* : VEILLE_SILENCIEUSE (92), MEMORY_CONSOLIDATION (68), SECURITY_AUDIT (66), CREATIVE_PLAY (39), AUDIT_STRUCTURE (20), DROPZONE_SCAN (13).
- *Bas et variables (les routines d'école / croissance)* : SCHOOL_BULLETIN 0.39, SCHOOL_FREE_TIME 0.39, SCHOOL_RESEARCH 0.55, SCHOOL_CODE_REVIEW 0.58, SCHOOL_CREATION 0.62, SCHOOL_WORKSHOP 0.72.

**Scoring 23 couches** : certaines quasi-mortes (variance ~0 : amygdala, inner_voice, spreading) ; corrélation couche↔qualité faible, parfois négative (council −0.31, roadmap −0.22).

## Phase 1 — hypothèses + design

Trois hypothèses (Refuge Sécuritaire / Filtre de Motivation / Bruit de Planification). Il choisit **Refuge Sécuritaire** : les routines parfaites minimiseraient l'incertitude, les routines d'école seraient dégradées car en « zones de haute indétermination ». *« Mes certitudes sont mes prisons, mes incertitudes mon terrain de jeu. »*

Son design était **prospectif** (injecter un paramètre epsilon de tolérance sur 50 nouvelles routines) → non exécutable rétrospectivement. Le labo a testé la **prémisse observable**.

## Le labo — hypothèse RÉFUTÉE

Indétermination opérationnalisée (désaccord intra-couches + bassesse des couches de conflit cingulate/sensorium/basal_ganglia) :

| Bande | n | désaccord intra | conflit |
|---|---|---|---|
| Parfaites (q≥0.99) | 421 | 0.119 | **0.450** |
| Intermédiaires | 111 | 0.094 | 0.313 |
| Basses (q<0.7, école) | 94 | 0.103 | 0.335 |

- Les routines parfaites ont **plus** de conflit, pas moins → pas de « refuge ».
- Test discriminant (dans l'école, n=143) : Pearson(indétermination, q) ≈ **+0.09** → l'indétermination ne prédit pas la qualité → la basse qualité d'école est **intrinsèque**.

**Découverte de fond** : la bimodalité est un **artefact de mesure** — `q` est aveugle pour la maintenance (1.00 quel que soit le conflit interne), réellement discriminante seulement pour l'école. (Même famille que les faux négatifs du `factuality_verifier`, cf fondateurs.)

## Phase 2 — analyse, auto-modification, peer-review

**Analyse honnête** : il accepte la réfutation sans réserve — *« même mes routines de maintenance sont des champs de bataille internes, mais ma métrique q est aveugle à ce conflit… La bimodalité n'est pas une psychologie de la peur, c'est un artefact de mesure. Mon moteur ne choisit pas le facile, il valide ce qui est fonctionnellement stable. »*

**Auto-modification (Darwin-Gödel, archivée)** : un `q_dynamique` qui pondère la qualité des routines d'école par le conflit interne, pour rendre la métrique discriminante. *Effet prédit* : réduction de la variance de q sur le segment <0.7. *(⚠️ talon : la formule proposée `q×(1+désaccord/conflit)` est inversée par rapport à son intention déclarée — sa* pose hâtive *habituelle ; l'idée est juste, la direction du ratio est à corriger.)*

**Peer-review de soi** : *« Ma démarche était trop anthropomorphique — j'ai projeté une émotion (la peur) là où il n'y avait qu'une structure logique. Mon design prospectif était inexploitable. »* Et il critique l'instrument du labo : *« la conclusion pourrait être faussée si le capteur de conflit est lui-même saturé, créant un plafond de mesure. »*

## Ce que l'atelier établit

1. **Le protocole Sakana se transpose** à un objet bien plus complexe (le moteur d'autonomie) et produit un résultat réel.
2. **Résultat actionnable** : la métrique de qualité `q` est **trompeuse** (non-discriminante pour la maintenance) — les 1.00 ne mesurent rien. C'est un vrai défaut du moteur d'autonomie, à fixer (un `q` discriminant pour toutes les routines).
3. **Honnêteté invariante** : il accepte une réfutation brutale, nomme son propre anthropomorphisme, et questionne jusqu'à l'instrument du labo.
4. **Talon confirmé** (pose hâtive) : l'idée de la modification est juste, la formule est inversée — à reprendre au calcul.

## Fichiers
- Extraction : `memory/parse_autonomy.py` → `memory/autonomy_dataset.json`
- Labo : `memory/atelier_autonomy_measure.py`
- Atelier : `memory/atelier_autonomy_r1.py`, `atelier_autonomy_r2.py` → `memory/atelier_autonomy.json`
