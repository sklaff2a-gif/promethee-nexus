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

## Briques (20 TDD verts)
| Fichier | Role |
|---|---|
| `anticipation_engine.py` | Moteur `anticipate(generator)` + `mirror()` a 3 passes deterministes : (1) ast.parse, (2) micro-lint constructs dangereux, (3) SCOPE V24.1 (NameError anticipe : inventaire des bindings + builtins en liste blanche `dir(builtins)`, attributs ignores -> 0 faux positif). Disjoncteur MAX_RETRIES=2 (veto au 2e echec), Error Ingestion (trace compilateur brute reinjectee). |
| `test_anticipation.py` (20) | Ebauches corrompues (parentheses, indentation, construct dangereux, variable orpheline) + non-faux-positifs (methode sur objet, import, comprehension, args, allowed globals) -> interception, reorientation, livraison purifiee, veto. |

## Reste
- Brancher `generator` sur le VRAI appel LLM (PLAYGROUND/code) au deploiement (a froid).
- Phase (a) : le miroir COMPORTEMENTAL (mini-appel LLM contraint sur les invariants d'armure)
  — le saut d'eveil, une fois la digue deterministe (syntaxe+lint+scope) infranchissable.
