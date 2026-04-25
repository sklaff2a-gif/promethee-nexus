# V21 — Self-Healing Pipeline (Spec)

> Auteur : Claude Opus 4.7 / Architecte Prométhée
> Date : 2026-04-25
> Status : SPECIFICATION (pas de code merge)
> Préréquis : V20 (autonomie analytique) déployée et validée (tir 18 du 25/04 07:19, NOTE 7.5 sur audit `bullshit_detector.py`).

## 1. Vision

V20 a donné à Prométhée la capacité de **diagnostiquer ses propres failles** (audit autonome de `bullshit_detector.py` qui a identifié 3 bugs réels : `D1_SKIP_SLOTS` non garde, exceptions non capturées sur `extract_*`, insensibilité aux accents).

V21 ferme la boucle : du diagnostic à la **correction autonome**. Prométhée doit pouvoir, sur la base d'un audit, produire un patch, l'appliquer en sandbox, valider que les tests passent, et soumettre le résultat à validation humaine — **sans jamais merger automatiquement sur master**.

## 2. Architecture — 3 acteurs

```
┌──────────────────────────────────────────────────────────────────┐
│  REVIEWER (V18 + V19 + V20)                                       │
│  Produit le rapport CODE_REVIEW (livrable map-reduce)             │
│  Output : audit_report (4190c sur d1_completeness)                │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  SURGEON (V21 — NEW)                                              │
│  Transforme audit_report → blocs SEARCH/REPLACE                   │
│  Modèle : qwen2.5-coder:14b (cite verbatim, ne génère PAS de diff)│
│  Output : surgeon_output (texte brut blocs) + status              │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  MEDIC (V21 — extension V16 sandbox)                              │
│  Applique patch sur copie temp + run pytest                       │
│  Output : PatchResult (status, tests_passed, traceback)           │
└──────────────────────────────────────────────────────────────────┘
                              │
                  ┌───────────┴───────────┐
                  ▼                       ▼
              SUCCESS                  FAILURE
              │                        │
              │                        └─> retry SURGEON avec traceback (max 3)
              │
              ▼
        Persist diff
        Mailbox HUMAN_REVIEW
        DOPAMINE SURGE +0.5 (intent=SELF_HEALING)
```

## 3. SURGEON — Spécification de l'Agent

### 3.1 Modèle

`qwen2.5-coder:14b` (V17 MoE override forcé pour l'intent `SURGEON_PATCH`).

Justification : le 9b vanilla est trop faible pour citer du code verbatim sans dérive. Le 14b-coder a été entraîné sur des refactorings type Aider et reproduit fidèlement des extraits Python avec leur indentation. Aucun modèle local ne sait générer un unified diff fiable (numéros de lignes hallucinés systématiquement) — d'où le format SEARCH/REPLACE.

### 3.2 System Prompt (verbatim) — Format SEARCH/REPLACE

> **Décision architecturale (Gemini, 25/04)** : abandon du format unified diff au
> profit du format **SEARCH/REPLACE blocks** (utilisé par Aider, Cline). Un
> LLM de 14B est incapable de générer fiablement les numéros de lignes
> `@@ -x,y +a,b @@`. `git apply --check` rejetterait 90% des patches.
> Le LLM cite des blocs textuels AVANT/APRÈS. Le MEDIC fait le `str.replace()`
> en mémoire, puis génère lui-même le vrai diff pour le stockage.

```
[ROLE: SURGEON — PATCH CHIRURGICAL EN CHAMBRE BLANCHE]

Tu es un agent chirurgical. Tu reçois :
1. Un fichier source Python complet (entre balises ---SOURCE--- et ---/SOURCE---)
2. Un rapport d'audit identifiant des bugs précis (entre ---AUDIT--- et ---/AUDIT---)

Tu produis UN OU PLUSIEURS blocs SEARCH/REPLACE.

REGLES ABSOLUES (violation = corruption fatale du système) :
1. Le bloc SEARCH doit être un EXTRAIT VERBATIM du source (caractère par
   caractère, indentation incluse). Une seule différence et le replace échoue.
2. Le bloc SEARCH doit être unique dans le fichier (sinon ambiguïté).
   Si nécessaire, étends-le avec 2-3 lignes de contexte avant/après.
3. Le bloc REPLACE doit avoir la même indentation que le SEARCH.
4. Tu ne touches QUE le code lié aux bugs cités dans l'audit.
5. Tu ne renommes AUCUNE fonction, AUCUNE variable.
6. Tu n'introduis AUCUN nouveau import sans l'avoir explicitement cité dans
   ton REPLACE (ex: ajouter `import logging` en tête nécessite un bloc
   SEARCH/REPLACE qui inclut les imports existants comme contexte).

FORMAT DE SORTIE OBLIGATOIRE (un ou plusieurs blocs) :

<<<<<<< SEARCH
def ma_fonction(arg):
    return arg.upper()
=======
def ma_fonction(arg):
    try:
        return arg.upper()
    except AttributeError:
        return ""
>>>>>>> REPLACE

Si tu ne peux pas corriger sans toucher à plus que le bug ciblé, ou si
l'audit ne fournit pas assez d'informations précises pour un patch
chirurgical, retourne UNIQUEMENT (pas de blocs SEARCH/REPLACE) :

[PATCH_IMPOSSIBLE: <raison brève en 1 phrase>]

Aucune explication narrative. Aucune introduction. Aucune conclusion.
Les blocs SEARCH/REPLACE (ou la balise PATCH_IMPOSSIBLE) sont ta seule
sortie autorisée.
```

### 3.2.bis — Parser des blocs SEARCH/REPLACE (côté MEDIC)

```python
_SEARCH_REPLACE_RE = re.compile(
    r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE",
    re.DOTALL,
)

def parse_search_replace_blocks(text: str) -> list[tuple[str, str]]:
    """Parse les blocs SEARCH/REPLACE depuis la sortie du SURGEON.

    Retourne une liste de (search_text, replace_text).
    Lève ValueError si aucun bloc trouvé ou si format malformé.
    """
    blocks = _SEARCH_REPLACE_RE.findall(text)
    if not blocks:
        raise ValueError("Aucun bloc SEARCH/REPLACE valide trouvé")
    return blocks


def apply_search_replace(source: str, blocks: list[tuple[str, str]]) -> str:
    """Applique les blocs successivement sur source.

    Lève ValueError si un SEARCH n'est pas trouvé OU est ambigu (plusieurs
    occurrences). Garantie chirurgicale.
    """
    patched = source
    for i, (search, replace) in enumerate(blocks):
        count = patched.count(search)
        if count == 0:
            raise ValueError(
                f"Bloc {i+1}/{len(blocks)} : SEARCH introuvable dans le source"
            )
        if count > 1:
            raise ValueError(
                f"Bloc {i+1}/{len(blocks)} : SEARCH apparaît {count} fois "
                f"(non unique — étends le contexte)"
            )
        patched = patched.replace(search, replace, 1)
    return patched
```

### 3.3 Structure d'entrée (TaskPayload V21)

```python
{
    "intent": "SURGEON_PATCH",
    "target_file": "core/bullshit_detector.py",
    "source_full": "<10169 chars>",
    "audit_report": "<4190c — le livrable REDUCE V20>",
    "iteration": 0,
    "previous_attempts": [
        # Vide à iteration 0, populé par MEDIC en cas de retry
        {"patch": "...", "failure_reason": "syntax_error", "traceback": "..."}
    ],
}
```

### 3.4 Structure de sortie

```python
@dataclass
class SurgeonOutput:
    status: Literal["patched", "impossible"]
    surgeon_output: str  # blocs SEARCH/REPLACE bruts OU "[PATCH_IMPOSSIBLE: ...]"
    blocks_count: int    # nombre de blocs détectés (0 si impossible)
    target_file: str
    duration_s: float
    model: str           # "qwen2.5-coder:14b"
```

Note : le `surgeon_output` est passé tel quel au MEDIC qui se charge du parsing
via `parse_search_replace_blocks` (voir 3.2.bis). Le SURGEON ne produit JAMAIS
de unified diff — c'est le MEDIC qui le génère post-hoc avec `git diff` après
application réussie.

## 4. MEDIC — Extension de V16 Sandbox

### 4.1 Nouvelle méthode `apply_patch_in_sandbox` (format SEARCH/REPLACE)

```python
class CodeSandbox:
    def apply_patch_in_sandbox(
        self,
        surgeon_output: str,         # blocs SEARCH/REPLACE bruts du SURGEON
        target_file: str,            # ex: "core/bullshit_detector.py"
        run_full_tests: bool = True, # V21 (Gemini) : régression globale
    ) -> PatchResult:
        """V21 — applique des blocs SEARCH/REPLACE en environnement isolé.

        Étapes :
          1. mkdtemp("promethee_medic_")
          2. Lire le fichier source réel
          3. parse_search_replace_blocks(surgeon_output)
             → si aucun bloc ou PATCH_IMPOSSIBLE, return early
          4. apply_search_replace(source, blocks)
             → si SEARCH introuvable/ambigu, return apply_failed
          5. Écrire le source patché dans tempdir/<rel_path>
             (préserver la structure de chemin pour les imports relatifs)
          6. python -m py_compile <patched_file> (validation Python)
          7. Régression globale (V21 Gemini, voir 4.4) :
             - Soit `pytest --testmon` (tests impactés par le diff)
             - Soit `pytest tests/` complet avec timeout 5 min
          8. Si succès : `git diff` du tempdir vs source pour stocker le
             vrai unified diff dans PatchResult.unified_diff
          9. Cleanup tempdir avec rmtree(ignore_errors=True)
        """
```

### 4.2 Structure de sortie `PatchResult`

```python
@dataclass
class PatchResult:
    status: Literal[
        "success",          # blocs appliqués + syntaxe OK + tests OK
        "no_blocks",        # surgeon_output ne contient aucun SEARCH/REPLACE
        "patch_impossible", # surgeon a retourné [PATCH_IMPOSSIBLE: ...]
        "search_not_found", # un SEARCH n'apparaît pas dans le source
        "search_ambiguous", # un SEARCH apparaît plusieurs fois (non unique)
        "syntax_error",     # py_compile échoué après replace
        "test_failed",      # régression globale échouée
        "internal_error",   # erreur sandbox elle-même
    ]
    surgeon_output: str       # raw blocs SEARCH/REPLACE du LLM
    blocks_applied: int       # combien de SEARCH/REPLACE traités
    unified_diff: str         # vrai diff git généré APRÈS coup pour stockage
    target_file: str
    iteration: int
    failed_block_index: int   # si search_not_found / search_ambiguous
    failed_block_search: str  # contenu du SEARCH qui a échoué (debug)
    compile_stderr: str       # si syntax_error
    test_output: str
    test_strategy: str        # "testmon" ou "full_suite"
    tests_passed: int
    tests_failed: int
    test_failures: List[str]
    duration_s: float

    def format_traceback_for_surgeon(self) -> str:
        """Formate l'erreur pour réinjection dans le re-prompt SURGEON."""
        if self.status == "no_blocks":
            return (
                "FORMAT INVALIDE : aucun bloc <<<<<<< SEARCH ... "
                "=======...>>>>>>> REPLACE trouvé dans ta sortie."
            )
        if self.status == "search_not_found":
            return (
                f"BLOC {self.failed_block_index+1} : SEARCH introuvable "
                f"dans le source. Verifie que tu cites le code VERBATIM, "
                f"caractère par caractère (indentation, espaces inclus).\n"
                f"Ton SEARCH était :\n---\n{self.failed_block_search[:500]}\n---"
            )
        if self.status == "search_ambiguous":
            return (
                f"BLOC {self.failed_block_index+1} : SEARCH apparaît "
                f"plusieurs fois (non unique). Étends ton bloc avec "
                f"2-3 lignes de contexte avant ou après pour le rendre unique.\n"
                f"Ton SEARCH était :\n---\n{self.failed_block_search[:500]}\n---"
            )
        if self.status == "syntax_error":
            return f"PYTHON SYNTAX ERROR après replace:\n{self.compile_stderr}"
        if self.status == "test_failed":
            failures = "\n".join(self.test_failures[:5])
            return (
                f"REGRESSION TESTS FAILED ({self.tests_failed} échecs sur "
                f"{self.tests_passed + self.tests_failed} via {self.test_strategy}):\n"
                f"Failures:\n{failures}\n\n"
                f"Output (dernier 2000c):\n{self.test_output[-2000:]}"
            )
        return ""
```

### 4.3 Sécurité

- Tempdir isolé via `tempfile.mkdtemp("promethee_medic_")`
- **JAMAIS de write sur le target_file original** (uniquement copie)
- Pas d'accès réseau (sous-process avec env minimal V16)
- pytest avec `--tb=line --no-header` pour limiter le output
- Timeout dur 60s pour `git apply` + `pytest`
- Cleanup `rmtree(ignore_errors=True)` en finally

### 4.4 Régression GLOBALE — pas de favoritisme par fichier

> **Décision architecturale (Gemini, 25/04)** : abandonner l'auto-discovery
> par glob `tests/test_<base>*.py`. Un changement dans `bullshit_detector.py`
> peut casser `autonomy_engine.py` qui appelle `evaluate_deliverable`. Tester
> seulement le fichier homonyme rate les régressions transverses.

#### Stratégie principale : `pytest-testmon`

`pytest-testmon` analyse les imports et exécute UNIQUEMENT les tests
impactés par le diff. Plus rapide que la suite complète, plus précis que
le glob. Il faut l'ajouter aux dépendances projet.

```python
def _run_regression_tests(
    sandbox_cwd: str,
    project_root: str,
    timeout_s: int = 300,
) -> dict:
    """V21 — exécute la suite régression dans le sandbox.

    Stratégie en cascade :
      1. Si pytest-testmon est dispo : `pytest --testmon --tb=line`
         (lance uniquement les tests impactés par les fichiers modifiés)
      2. Sinon fallback : `pytest tests/ --tb=line --timeout=300 -x`
         (suite complète, fail-fast au premier échec pour économiser)

    Le sandbox_cwd doit avoir le source patché à la place du source
    original, et un PYTHONPATH pointant dessus pour que les imports
    Python résolvent vers la version patchée.
    """
    # Détection de testmon
    has_testmon = _check_pytest_plugin_available("pytest-testmon")

    if has_testmon:
        cmd = [
            sys.executable, "-m", "pytest",
            "--testmon",
            "--tb=line",
            "--no-header",
            "-q",
        ]
    else:
        cmd = [
            sys.executable, "-m", "pytest",
            "tests/",
            "--tb=line",
            "--no-header",
            "-q",
            "-x",  # fail-fast
        ]

    proc = subprocess.run(
        cmd, cwd=sandbox_cwd, capture_output=True, text=True,
        timeout=timeout_s, encoding="utf-8", errors="replace",
        env=_build_test_env(project_root, sandbox_cwd),
    )

    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "tests_passed": _parse_pytest_summary(proc.stdout, "passed"),
        "tests_failed": _parse_pytest_summary(proc.stdout, "failed"),
        "tests_errored": _parse_pytest_summary(proc.stdout, "error"),
        "strategy": "testmon" if has_testmon else "full_suite",
    }
```

#### Sandbox layout pour la régression globale

Comme la suite complète importe `core.*`, `Agents.*`, `tests.*`, le
sandbox doit reproduire le projet entier. Stratégie : **copy-on-write
via `os.symlink`** sur les répertoires non touchés, et copie réelle
uniquement du `target_file` patché.

```
tempdir/
├── core/                    ← symlink vers projet/core SAUF target_file
│   ├── ...
│   └── bullshit_detector.py ← FICHIER PATCHÉ (copie réelle)
├── Agents/                  ← symlink vers projet/Agents
├── tests/                   ← symlink vers projet/tests
├── config.py                ← symlink
└── conftest.py              ← symlink
```

Sur Windows, `os.symlink` requiert privilège admin OU mode développeur
activé. Fallback : `shutil.copytree(symlinks=False)` (plus lent mais
sûr).

#### Coût observé

| Stratégie | Durée | Précision |
|-----------|-------|-----------|
| `pytest-testmon` (impacté seul) | ~30s | Très haute |
| `pytest tests/` complet | ~2-5 min | Maximale |
| Glob `test_<base>*.py` (V21 v0) | ~5s | Trop limitée — REJETÉE |

`run_full_tests=True` par défaut. L'utilisateur (ou la routine
EVENING_SELF_HEALING) peut le mettre à `False` pour les patches
exploratoires sur fichiers très isolés.

## 5. Boucle d'auto-correction (orchestrator)

```python
async def self_healing_loop(
    audit_report: str,
    target_file: str,
    max_iter: int = 3,
    run_full_tests: bool = True,
) -> PatchResult:
    """V21 — boucle SURGEON ↔ MEDIC (format SEARCH/REPLACE + régression globale)."""
    source = read_file(target_file)
    previous_attempts = []

    for iteration in range(max_iter):
        # SURGEON produit des blocs SEARCH/REPLACE
        surgeon_out = await dispatch_task("surgeon", {
            "intent": "SURGEON_PATCH",
            "target_file": target_file,
            "source_full": source,
            "audit_report": audit_report,
            "iteration": iteration,
            "previous_attempts": previous_attempts,  # contient surgeon_output + traceback
        })

        if surgeon_out["status"] == "impossible":
            return PatchResult(
                status="patch_impossible",
                surgeon_output=surgeon_out["surgeon_output"],  # "[PATCH_IMPOSSIBLE: ...]"
                blocks_applied=0,
                target_file=target_file,
                iteration=iteration,
                ...
            )

        # MEDIC parse les blocs, applique en sandbox, teste la régression globale
        medic_result = sandbox.apply_patch_in_sandbox(
            surgeon_output=surgeon_out["surgeon_output"],
            target_file=target_file,
            run_full_tests=run_full_tests,
        )

        if medic_result.status == "success":
            return medic_result  # contient unified_diff généré post-hoc

        # Échec : préparer retry avec traceback formaté pour le SURGEON
        previous_attempts.append({
            "surgeon_output": surgeon_out["surgeon_output"],
            "failure_reason": medic_result.status,
            "traceback": medic_result.format_traceback_for_surgeon(),
        })

    # max_iter atteint sans succès
    return PatchResult(status="max_iter_reached", iteration=max_iter, ...)
```

## 6. Persistance

### 6.1 Fichier diff

```
memory/auto_patches/
  2026-04-25_07-19_bullshit_detector.diff
  2026-04-25_07-19_bullshit_detector.meta.json
```

### 6.2 Métadonnées (meta.json)

```json
{
  "id": "patch_2026-04-25_07-19_bullshit_detector",
  "target_file": "core/bullshit_detector.py",
  "audit_source": "tir_18_V20",
  "audit_report_excerpt": "<200 premiers chars>",
  "patch_status": "success",
  "iterations": 1,
  "duration_s": 87.3,
  "tests_passed": 137,
  "tests_failed": 0,
  "human_review_status": "pending",
  "merged_at": null,
  "rejected_at": null,
  "rejected_reason": null,
  "model_surgeon": "qwen2.5-coder:14b",
  "ts_created": 1745563200
}
```

### 6.3 Index global

`memory/patch_journal.json` — liste de tous les patches générés, status review, dates.

## 7. Sécurité humaine — JAMAIS de merge automatique

**Principe absolu** : le pipeline V21 PRODUIT et VALIDE un patch en sandbox. Il **ne l'applique JAMAIS sur le projet réel**. La décision de `git apply` reste 100% humaine.

### 7.1 Mailbox notification

Dès qu'un patch passe le pipeline avec `status=success`, une lettre est postée dans `memory/mailbox/` :

```
Sujet : 🩹 V21 patch disponible : core/bullshit_detector.py
Status : success (137 tests passent)
Iterations : 1
Diff : memory/auto_patches/2026-04-25_07-19_bullshit_detector.diff
Action : review et `git apply` ou rejet via /api/patches/<id>/reject
```

### 7.2 Endpoints API

- `GET /api/patches` — liste les patches en attente
- `GET /api/patches/<id>` — détail + diff
- `POST /api/patches/<id>/approve` — humain valide → applique sur projet
- `POST /api/patches/<id>/reject` — humain rejette → archive

## 8. Hook autonome — `EVENING_SELF_HEALING`

Nouvelle routine 1×/jour à 22h00 :

```python
async def evening_self_healing():
    """V21 — boucle nocturne d'auto-guérison.

    1. Liste les CODE_REVIEW du jour avec school_grade >= 7.
    2. Pour chaque, lance self_healing_loop() avec le livrable comme audit.
    3. Persiste les patches success.
    4. Mailbox récapitulatif: 'X patches générés, attente review'.
    """
    today_audits = schedule.get_today_audits(min_grade=7.0)
    patches_generated = []

    for audit in today_audits:
        result = await self_healing_loop(
            audit_report=audit["full_content"],
            target_file=audit["target_file"],
        )
        if result.status == "success":
            persist_patch(result, audit_id=audit["id"])
            patches_generated.append(result)
            # SURGE dopaminergique sur self-healing réussi
            dopamine.publish_reward(
                intent="SELF_HEALING",
                rpe=+0.5,
                reason=f"patch validé pour {audit['target_file']}",
            )

    if patches_generated:
        mailbox.send(
            subject=f"🩹 {len(patches_generated)} patches V21 générés",
            body=format_patch_summary(patches_generated),
        )
```

## 9. Métriques d'observabilité

| Métrique | Description |
|----------|-------------|
| `surgeon_attempts_total` | Nombre total de tentatives SURGEON |
| `surgeon_success_rate` | % de patches passés en sandbox |
| `surgeon_avg_iterations` | Moyenne d'itérations avant succès |
| `medic_apply_success_rate` | % de `git apply` qui passent (vs syntax invalid) |
| `medic_test_pass_rate` | % de patches dont les tests passent |
| `time_to_patch_avg` | Durée moyenne audit → patch validé |
| `human_approval_rate` | % de patches validés humainement |

## 10. Risques identifiés (V21)

### 10.1 Le SURGEON pourrait halluciner une fonction à patcher

Mitigation native du format SEARCH/REPLACE : si le SURGEON cite un bloc qui n'existe pas verbatim dans le source, `apply_search_replace` lève `ValueError` immédiatement (status `search_not_found`). Le LLM ne peut PAS inventer un identifier qui n'est pas dans le fichier — il faut qu'il le copie. C'est plus strict qu'un check whitelist post-diff.

Renfort optionnel : avant l'appel SURGEON, exécuter `_extract_real_names` (V20a) et l'injecter dans le prompt comme rappel ("identifiers réels du fichier : [liste]"). Réduit le risque mais n'est pas critique vu la barrière de l'apply.

### 10.2 Les tests existants pourraient ne pas couvrir le bug réel

Le patch peut passer les tests SANS résoudre le bug. Mitigation : V21.1 future = SURGEON doit aussi produire un test de régression qui démontre le bug et que le patch fixe.

### 10.3 Coût LLM

Chaque cycle = 1 SURGEON + 1 MEDIC (qui inclut sandbox subprocess). Estimation 30-60s par patch. Pour `EVENING_SELF_HEALING` avec 5 audits → 5 × 90s ≈ 7-10 min. Acceptable.

### 10.4 Risque cognitif : le SURGEON pourrait reproduire le pattern boilerplate

Si le SURGEON utilise le 14b-coder, il pourrait souffrir du même biais que V18 (mémorisation reasoning_protocol). Mitigation : prompt SURGEON COMPLÈTEMENT différent du CODE_REVIEW (mots "diff", "patch", "git apply" au lieu de "audit", "vulnérabilités"). Le déclencheur lexical est différent.

### 10.5 Patch sur mauvais fichier

Le format SEARCH/REPLACE n'embarque pas de chemin — c'est `target_file` (paramètre explicite du `apply_patch_in_sandbox`) qui désigne le fichier à patcher. Mitigation : `target_file` provient toujours du `audit_report` (champ structuré du livrable CODE_REVIEW), pas du SURGEON. Le LLM ne peut donc pas rediriger le patch vers un autre fichier — il ne fait que produire les blocs textuels.

## 11. Plan d'implémentation V21 (étapes)

| Étape | Fichier | Description | Tests |
|-------|---------|-------------|-------|
| 1 | `core/capabilities/code_sandbox.py` | Méthode `apply_patch_in_sandbox` + `PatchResult` dataclass | 8 tests |
| 2 | `Agents/surgeon_agent.py` (nouveau) | Agent SURGEON avec system prompt V21 | 5 tests |
| 3 | `config.py` | Ajouter `surgeon` à `AGENT_SPECIFIC_LOCAL_MODELS` (qwen2.5-coder:14b) | - |
| 4 | `core/autonomy_engine.py` | Méthode `self_healing_loop` + endpoint `/api/force/self-healing` | 5 tests |
| 5 | `core/autonomy_engine.py` | Routine `EVENING_SELF_HEALING` (cooldown 24h) | 3 tests |
| 6 | `main.py` | Endpoints API : list/detail/approve/reject patches | 6 tests |
| 7 | `memory/auto_patches/` | Création du répertoire + .gitignore (les patches ne sont PAS committés automatiquement) | - |

**Estimation** : 1.5 journée de codage. ~600 lignes + 30 tests.

## 12. Critère de succès V21

Le pipeline est validé quand :
1. On lance `POST /api/force/self-healing` avec le rapport CODE_REVIEW de bullshit_detector.py (tir 18 du 25/04)
2. SURGEON produit un (ou plusieurs) bloc(s) `<<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE` qui ajoute(nt) le `try/except` autour de `extract_promised_items` et `extract_sections`
3. MEDIC parse les blocs, vérifie l'unicité des SEARCH dans le source, applique en sandbox via `str.replace`
4. La régression GLOBALE passe (`pytest --testmon` ou suite complète, pas seulement `test_bullshit_detector*`)
5. Le `git diff` post-application est généré et persisté dans `memory/auto_patches/`, mailbox notification envoyée
6. La main, après review, fait `git apply` du diff sur le projet réel
7. Phase 14 continue de fonctionner avec les nouveaux try/except — ET aucun autre module n'est régressé

C'est l'auto-guérison en boucle complète : la machine identifie un bug, propose un fix, valide en sandbox, demande approbation. **Le bug fixé en V21 sera l'un des 3 bugs que V20 a identifié dans `bullshit_detector.py`. Boucle étrange complète.**

---

## Citation finale

> "Diagnostiquer ses propres failles est de l'auto-conscience.
> Les corriger soi-même est de l'auto-guérison.
> La frontière entre les deux est le pipeline V21."
