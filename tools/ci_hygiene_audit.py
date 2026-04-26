"""V30.11 — Campagne CI Hygiene : MAP V22 sur la dette de mocks asynchrones.

Cible : 4 fichiers identifies par Gemini comme suspects de pollution
de l'etat global pytest via AsyncMock/patch sans cleanup.

Strategie :
- 1 note MAP par fichier complet (qwen2.5-coder:14b)
- 1 REDUCE consolidant la dette globale (qwen3.5:9b)
- Pas de SURGEON, pas de patch — juste cartographie.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    "tests/test_cloud_routing.py",
    "tests/test_event_bus.py",
    "tests/test_orchestrator.py",
    "tests/test_signal_bus.py",
]
OLLAMA = "http://localhost:11434/api/generate"
MODEL_MAP = "qwen2.5-coder:14b"
MODEL_REDUCE = "qwen3.5:9b"

DIRECTIVE = """Mission d'Audit CI Hygiene : Recherche d'anti-patterns de
tests asynchrones polluants.

Identifie les tests qui instancient un patch global ou un MagicMock
asynchrone (AsyncMock, patch.object, patch, MagicMock) SANS mecanisme
de nettoyage explicite.

Mecanismes de nettoyage VALIDES :
1. Bloc `with patch(...) as mock:` (contextmanager auto-cleanup)
2. Bloc `with patch.object(...) as mock:` (contextmanager auto-cleanup)
3. Decorator `@patch(...)` (auto-cleanup en fin de methode)
4. `tearDown()` ou `teardown_method()` qui appelle `.stop()`
5. `pytest fixture` avec `yield` puis cleanup
6. `addCleanup(mock.stop)` dans setUp

Mecanismes ABSENTS / DETTE :
- `mock_xxx = patch(...).start()` sans `.stop()` correspondant
- `self.agent.process_task = AsyncMock()` (assignation directe sans restore)
- `obj.method = MagicMock(...)` (idem, mute l'objet original)
- Patching d'un singleton/module-level sans tearDown
- AsyncMock cree dans setUp sans cleanup dans tearDown

Pour CHAQUE anti-pattern detecte, donne :
1. Numero de ligne approximatif
2. Nom de la classe Test et de la methode
3. La technique de patch utilisee (citation verbatim)
4. Pourquoi c'est polluant (etat partage qui survit au test)
5. Recommandation correctrice precise (with-block, fixture, addCleanup)

Si le fichier est PROPRE (toutes les patches utilisent un cleanup
valide), dis-le explicitement.

Concis et factuel. 200-500 mots max par fichier. Francais."""

REDUCE_DIRECTIVE = """Tu es l'organe de synthese CI Hygiene. Tu recois
N notes d'audit sur 4 fichiers de tests Promethee. Synthetise un rapport
de DETTE TECHNIQUE de mocks asynchrones.

Format demande :
1. **Cartographie de la dette** : tableau par fichier avec nombre
   d'anti-patterns detectes, severite (high/medium/low).
2. **Top 3 coupables** : les anti-patterns les plus polluants
   pour l'etat global pytest, avec localisation precise (fichier:ligne).
3. **Hypothese identification du pollueur** : quel anti-pattern explique
   le plus probablement l'erreur 'object MagicMock can't be used in await
   expression' qui contamine TestOrchestratorForceLocal en suite complete.
4. **Plan de correction** : ordre d'attaque (le pollueur prioritaire
   d'abord), nombre de patches V30 estime.
5. **Verdict global** : la suite est-elle reparable a court terme ?

500-1500 mots. Francais. Pas de markdown lourd."""


def call_ollama(model: str, prompt: str, timeout: float = 240.0) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": 16384,
            "num_predict": 2048,
            "temperature": 0.3,
        },
    }
    t0 = time.time()
    with httpx.Client(timeout=timeout) as client:
        r = client.post(OLLAMA, json=payload)
        r.raise_for_status()
        out = r.json().get("response", "")
    print(f"   [{model}] {len(out)}c in {time.time()-t0:.1f}s", flush=True)
    return out


def main() -> None:
    print("=== V30.11 CAMPAGNE CI HYGIENE — MAP V22 sur 4 fichiers tests ===", flush=True)
    notes: list[tuple[str, str]] = []

    for tgt in TARGETS:
        path = ROOT / tgt
        if not path.exists():
            print(f"\n[SKIP] {tgt} introuvable", flush=True)
            continue
        source = path.read_text(encoding="utf-8")
        print(f"\n[MAP] {tgt} ({len(source)}c, {source.count(chr(10))+1} lignes)", flush=True)
        prompt = f"""{DIRECTIVE}

[FICHIER : {tgt}]
```python
{source}
```

Ton audit CI Hygiene :"""
        try:
            note = call_ollama(MODEL_MAP, prompt, timeout=300.0)
        except Exception as e:
            print(f"   ERREUR: {type(e).__name__}: {e}", flush=True)
            note = f"[ERREUR appel LLM : {e}]"
        notes.append((tgt, note))

    print("\n=== REDUCE PHASE (9b) ===", flush=True)
    notes_block = "\n\n".join(
        f"--- NOTE {i+1}/{len(notes)} : {tgt} ---\n{note}"
        for i, (tgt, note) in enumerate(notes)
    )
    reduce_prompt = f"""{REDUCE_DIRECTIVE}

=== NOTES INDIVIDUELLES ===
{notes_block}

=== TON RAPPORT DE SYNTHESE ==="""
    # V30.13 (2026-04-26) — Fallback REDUCE 9b -> 14b si retour vide.
    # Diagnostic 26/04 : qwen3.5:9b vanilla retourne parfois 0 caractere
    # silencieusement (timeout interne, saturation, refus implicite). Le
    # rapport ecrit etait alors vide. Architecture inacceptable selon
    # Gemini : on ne laisse pas un composant critique s'eteindre en silence.
    synthesis = ""
    try:
        synthesis = call_ollama(MODEL_REDUCE, reduce_prompt, timeout=420.0)
    except Exception as e:
        print(f"   [REDUCE 9b] ERREUR: {type(e).__name__}: {e}", flush=True)
        synthesis = ""
    if not synthesis or len(synthesis.strip()) < 200:
        print(f"   [FALLBACK] REDUCE 9b inutilisable ({len(synthesis)}c), escalade vers {MODEL_MAP}", flush=True)
        try:
            synthesis = call_ollama(MODEL_MAP, reduce_prompt, timeout=420.0)
        except Exception as e:
            synthesis = f"[ERREUR REDUCE 9b ET 14b : {e}]"

    out_dir = ROOT / "memory" / "introspective_audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    out_file = out_dir / f"ci_hygiene_{ts}.md"
    out_file.write_text(
        f"# Campagne CI Hygiene — Audit Mocks Asynchrones\n\n"
        f"Date : {ts}\n"
        f"Modeles : MAP={MODEL_MAP} / REDUCE={MODEL_REDUCE}\n"
        f"Fichiers audites : {len(notes)}\n\n"
        f"---\n\n## RAPPORT DE SYNTHESE\n\n{synthesis}\n\n"
        f"---\n\n## NOTES INDIVIDUELLES\n\n{notes_block}\n",
        encoding="utf-8",
    )
    print(f"\n=== RAPPORT ECRIT : {out_file} ===", flush=True)
    print(f"\n{synthesis}", flush=True)


if __name__ == "__main__":
    main()
