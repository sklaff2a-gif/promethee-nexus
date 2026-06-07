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

## Briques (11 TDD verts)
| Fichier | Role |
|---|---|
| `anticipation_engine.py` | Moteur `anticipate(generator)` + `mirror()` (ast.parse + micro-lint). Disjoncteur MAX_ANTICIPATION_RETRIES=2 (veto au 2e echec), Error Ingestion (trace compilateur brute reinjectee). |
| `test_anticipation.py` (11) | Ebauches corrompues simulees (parentheses, indentation, construct dangereux) -> interception, reorientation, livraison purifiee, veto. |

## Reste
- Etendre le micro-lint (imports interdits, profondeur, etc.).
- Brancher `generator` sur le VRAI appel LLM (PLAYGROUND/code) au deploiement (a froid).
- Phase (a) : le miroir COMPORTEMENTAL (mini-appel LLM contraint sur les invariants d'armure).
