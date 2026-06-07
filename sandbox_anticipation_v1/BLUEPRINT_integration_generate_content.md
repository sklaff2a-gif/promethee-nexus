# BLUEPRINT V25.1 — Greffe préfrontale dans `generate_content` (CONCEPTION, non déployé)

> Mandat lecture seule. Squelette algorithmique de l'insertion dans
> `base_agent.py:886` (`generate_content`, 886-1177). NE TOUCHE PAS le live.

## Cartographie de la zone terminale (réelle)
- `full_prompt` = prompt + RAG, assemblé **une fois** avant l'appel LLM → **point d'injection de la friction**.
- 2 chemins d'appel LLM, tous deux finissant en `final = sanitize(strip_cot(...))` puis `return final` :
  - Cloud cascade (l.1066-1165) : `client.generate_content(full_prompt)`
  - Local (l.1171-1177) : `await self._call_ollama_stream(full_prompt, local_model)`
- ⚠️ `self.remember(...)` (l.1145, cloud) + `_save_training_pair` + `_record_for_compiler` se font **par appel** → à DÉPLACER sur le livrable final validé (invariant 3).

## Les 3 extractions nécessaires (refactoring, à froid)
1. `_invoke_llm(self, full_prompt) -> str` : toute la logique appel-LLM (cloud cascade + fallback local), **sans** remember/training/compiler. Re-appelable.
2. `_route_prefrontal_mirror(self, prompt) -> (mirror_fn, mode)` : routage par `[SCHOOL_SLOT]` (CODE_REVIEW/WORKSHOP/REFACTORING/FEATURE = code ; sinon introspectif).
3. `_consolidate(self, prompt, content, was_cloud)` : remember + _save_training_pair + _record_for_compiler, appelé **une seule fois** sur le livrable retenu.

## Squelette de la greffe (remplace la zone de return final)

```python
MAX_PREFRONTAL_RETRIES = 2
import inspect

async def generate_content(self, prompt: str) -> str:
    # ... [INCHANGE] Neural Compiler, Bloom Filter, RAG -> construit `full_prompt` ...

    # ===== GREFFE PREFRONTALE V25.1 =====
    mirror_fn, mode = self._route_prefrontal_mirror(prompt)   # invariant 1 : routage par slot
    friction = None            # invariant 3 : friction EPHEMERE, variable locale pure
    last, was_cloud = None, False

    for attempt in range(1, MAX_PREFRONTAL_RETRIES + 1):       # invariant 2 : etranglement budgetaire
        # isolation : la friction enrichit le PROMPT D'APPEL, jamais self.messages / l'historique
        prompt_try = full_prompt if friction is None else f"{full_prompt}\n\n{friction}"
        last, was_cloud = await self._invoke_llm(prompt_try)

        verdict = mirror_fn(last)                  # determinisme = sync ; comportemental = coroutine
        if inspect.isawaitable(verdict):           # le miroir LLM (intro) est async (appel 9B)
            verdict = await verdict
        ok, rejection = verdict
        if ok:
            self._consolidate(prompt, last, was_cloud)   # remember/training : 1 fois, sur le VALIDE
            return last
        friction = rejection                       # re-injecte EPHEMEREMENT au tour suivant

    # ===== budget epuise -> INVARIANT 4 (fail-safe), ASYMETRIQUE =====
    if mode == "code":
        # arc reflexe : un code non compilable ne se livre JAMAIS
        self.log_thought("[PREFRONTAL_VETO] code non valide apres reorientation — canal coupe", type="warning")
        return "[PREFRONTAL_VETO: CODE_NON_COMPILABLE]"   # marqueur amont, pas de freeze
    # introspectif : mode degrade -> livrer la derniere ebauche + balise visible
    degraded = f"[METABOLISME_ALERT: POSTURE_NON_CONSOLIDEE]\n{last or ''}"
    self._consolidate(prompt, degraded, was_cloud)
    return degraded
```

## Récursivité PROTÉGÉE
Pas de récursion réelle (pas de `generate_content` qui s'auto-appelle — trop risqué sur 293 lignes).
C'est une **boucle bornée** `for attempt in range(1, MAX+1)` qui ré-invoque seulement `_invoke_llm`
(l'appel LLM), **pas** tout `generate_content`. Le RAG/pré-filtres ne sont PAS rejoués (cout maitrise).
Borne dure -> jamais de boucle infinie meme si le 9B s'obstine dans son orniere.

## Isolation des contextes (anti-contamination)
- `friction` est une **variable locale** ; elle n'est ajoutée qu'au `prompt_try` passé à `_invoke_llm`.
- Elle n'entre JAMAIS dans `self.messages`, l'historique de session, ni un `remember()`.
- `_consolidate` (remember/training/compiler) n'est appelé que sur le **livrable retenu** (succes ou degrade final), jamais sur une ebauche rejetee -> la memoire ne se pollue pas de brouillons.

## Les 4 invariants — traçabilité
| Invariant | Où |
|---|---|
| 1. Routage par slot | `_route_prefrontal_mirror` (balises `[SCHOOL_SLOT]` natives) |
| 2. Étranglement budgétaire | `for attempt in range(1, MAX_PREFRONTAL_RETRIES+1)` |
| 3. Isolation des contextes | `friction` locale + `_consolidate` déplacé sur le livrable |
| 4. Fail-safe asymétrique | code = `[PREFRONTAL_VETO]` ; intro = `[METABOLISME_ALERT]` + dernière ébauche |

## Risques & validation avant déploiement (à froid)
- **Async** : le miroir comportemental = appel 9B -> `behavioral_mirror` async (judge Ollama). Géré via `inspect.isawaitable`.
- **Refactoring `_invoke_llm`** : extraction délicate (cascade cloud + cooldown/RPM entremelés). À faire en isolant SANS changer le comportement, valide par les 4800 tests.
- **Coût** : +1 appel 9B (le juge) par génération introspective + jusqu'à 2 regen. Acceptable hors slots chauds ; à mesurer.
- **Déploiement** : diff prudent + `.bak` + restart clinique + health=GO, comme la greffe shadow.
