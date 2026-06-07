# Sandbox Anticipation V1 — cortex prefrontal frugal (Chantier B)

Prototypes ISOLES. Apres la memoire (hippocampe), on attaque l'executif (prefrontal) :
faire que Promethee ANTICIPE les consequences de ses choix AVANT de livrer, sans
exploser le budget de jetons (anti Tree-of-Thoughts).

## Principe : predictive coding de l'action
L'agent ebauche -> un MIROIR DETERMINISTE evalue AVANT toute livraison. En regime
nominal (code valide) le cout additionnel est NUL (un `ast.parse` gratuit) ; l'energie
semantique n'est mobilisee qu'en cas de SURPRISE (exception interceptee). Le brouillon
defectueux meurt en stase dans le tampon prefrontal, l'interface n'est jamais polluee.

## Amorcage : la fracture OPERATIONNELLE (mesurable)
On commence par les echecs OPERATIONNELS (le code parse ou non) car il existe un
ORACLE DUR (`ast.parse`) -> on peut MESURER que l'anticipation a raison. Le
COMPORTEMENTAL (anticiper une derive type Logos/honnetete) viendra APRES : pas d'oracle
deterministe, miroir = mini-appel LLM contraint, et le 9B y est faillible (son talon).

## Briques (32 TDD verts)
| Fichier | Role |
|---|---|
| `anticipation_engine.py` | Moteur `anticipate(generator, mirror_fn=)` + `mirror()` DETERMINISTE a 3 passes 0-jeton : (1) ast.parse, (2) micro-lint constructs dangereux, (3) SCOPE V24.1 (NameError anticipe : bindings + builtins `dir(builtins)`, attributs ignores -> 0 faux positif). Disjoncteur MAX_RETRIES=2, Error Ingestion (trace brute reinjectee). `mirror_fn` injectable -> meme boucle pour le code ET le texte. |
| `test_anticipation.py` (20) | Ebauches corrompues + non-faux-positifs (methode, import, comprehension, args) -> interception, reorientation, livraison purifiee, veto. |
| `behavioral_mirror.py` (+test, 12) | Phase (a) — miroir COMPORTEMENTAL (V25.0_META) : mini-juge LLM contraint (JSON strict, temp ~0, 1 appel) sur 3 passes — orniere/pathos (seuil), Logos OPERE (pas slogan mort), honnetete. Doctrine INVERSE : doute/JSON casse/timeout -> LAISSE PASSER. Reorientation = DIAGNOSTIC DE POSTURE (categorie + direction, JAMAIS le lexeme -> anti-correction cosmetique, cf V16.7). Mesurabilite via jeu de reference annote. |

## Reste
- Brancher `generator` (les deux miroirs) sur le VRAI appel LLM du slot au deploiement (a froid).
- Mesurer le miroir comportemental contre le VRAI 9B (le proxy heuristique valide la plomberie, pas le taux reel).
- Calibrer le seuil de pathos + enrichir le prompt du juge sur des cas reels.
