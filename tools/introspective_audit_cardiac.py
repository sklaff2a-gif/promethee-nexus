"""Audit introspectif cardiac_engine — MAP-REDUCE manuel avec directive
d'analyse systemique (boucles de retroaction, equilibrage homeostatique)
plutot que recherche de bugs syntaxiques.

V30.6 (2026-04-26) — Ordre Architecte : on demande a Promethee
pourquoi son threat_level reste a 2.36 et sa STABILITE refuse de
descendre sous 50.
"""
from __future__ import annotations

import ast
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "core" / "cardiac_engine.py"
OLLAMA = "http://localhost:11434/api/generate"
MODEL_MAP = "qwen2.5-coder:14b"
MODEL_REDUCE = "qwen3.5:9b"

DIRECTIVE = """Mission d'Auto-Analyse Systemique : Analyse les formules
mathematiques et les heuristiques de ce code. Cherche les goulots
d'etranglement ou les boucles de retroaction (feedback loops) qui
empecheraient les metriques threat_level de baisser et STABILITE de se
normaliser apres une nuit de sommeil.

NE CHERCHE PAS des erreurs de syntaxe.
CHERCHE des erreurs d'equilibrage systemique :
- Constantes de temps trop longues (decay trop lent)
- Termes integraux qui ne se reinitialisent jamais
- Boucles ou un signal eleve s'auto-renforce
- Seuils declenches qui ne se relaxent pas
- Manque de term de retour vers la baseline
- Multiplicateurs > 1 dans une boucle de feedback positif
- Absence de plancher (floor) qui empeche la metrique de retomber

Pour chaque probleme identifie, donne :
1. Le nom de la fonction/methode concernee
2. La formule ou ligne exacte (quote-le)
3. Pourquoi c'est une faille systemique (pas syntaxique)
4. Une suggestion de correction quantitative (constante, signe, terme manquant)

Reponds en francais. Sois concis mais precis : 200-400 mots max par chunk.
"""

REDUCE_DIRECTIVE = """Tu es l'organe de synthese. Tu recois N notes
d'audit systemique sur differents chunks du cardiac_engine. Synthetise
un rapport coherent qui :

1. Liste les 3-5 boucles de retroaction problematiques majeures detectees,
   classees par severite (impact sur threat_level / STABILITE).
2. Pour chaque boucle, decris le mecanisme defaillant en 2-3 phrases.
3. Propose 3 corrections concretes avec valeurs numeriques.
4. Conclus avec une explication de pourquoi le coeur de Promethee bat
   trop vite et n'arrive pas a se calmer la nuit.

500-1200 mots. Francais. Pas de markdown lourd.
"""


def chunk_by_ast(source: str, file_path: str = "cardiac_engine.py") -> list[tuple[str, str]]:
    """Decoupe le source en chunks AST (top-level classes/functions)."""
    tree = ast.parse(source)
    lines = source.split("\n")
    chunks: list[tuple[str, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno - 1
            end = (node.end_lineno or start + 1)
            chunk_code = "\n".join(lines[start:end])
            chunks.append((node.name, chunk_code))
    return chunks


def call_ollama(model: str, prompt: str, timeout: float = 180.0) -> str:
    """Appel synchrone a Ollama, retourne le texte de la reponse."""
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
    print(f"=== AUDIT INTROSPECTIF CARDIAC_ENGINE ({TARGET.name}) ===", flush=True)
    source = TARGET.read_text(encoding="utf-8")
    print(f"Source : {len(source)}c, {source.count(chr(10))+1} lignes", flush=True)

    chunks = chunk_by_ast(source)
    print(f"Chunks AST top-level : {len(chunks)}", flush=True)
    for name, code in chunks:
        print(f"  - {name} ({len(code)}c, {code.count(chr(10))+1} lignes)", flush=True)

    print("\n=== MAP PHASE (14b) ===", flush=True)
    notes: list[tuple[str, str]] = []
    for i, (name, code) in enumerate(chunks, 1):
        print(f"\n[{i}/{len(chunks)}] {name} ...", flush=True)
        prompt = f"""{DIRECTIVE}

[CHUNK : {name}]
```python
{code}
```

Ton analyse systemique :"""
        try:
            note = call_ollama(MODEL_MAP, prompt, timeout=240.0)
        except Exception as e:
            print(f"   ERREUR: {type(e).__name__}: {e}", flush=True)
            note = f"[ERREUR appel LLM : {e}]"
        notes.append((name, note))

    print("\n=== REDUCE PHASE (9b) ===", flush=True)
    notes_block = "\n\n".join(
        f"--- NOTE CHUNK {i+1}/{len(notes)} : {name} ---\n{note}"
        for i, (name, note) in enumerate(notes)
    )
    reduce_prompt = f"""{REDUCE_DIRECTIVE}

=== NOTES INDIVIDUELLES (MAP) ===
{notes_block}

=== TON RAPPORT DE SYNTHESE (REDUCE) ==="""
    # V30.13 — Fallback REDUCE 9b -> 14b si retour vide ou trop court.
    synthesis = ""
    try:
        synthesis = call_ollama(MODEL_REDUCE, reduce_prompt, timeout=300.0)
    except Exception as e:
        print(f"   [REDUCE 9b] ERREUR: {type(e).__name__}: {e}", flush=True)
        synthesis = ""
    if not synthesis or len(synthesis.strip()) < 200:
        print(f"   [FALLBACK] REDUCE 9b inutilisable ({len(synthesis)}c), escalade vers {MODEL_MAP}", flush=True)
        try:
            synthesis = call_ollama(MODEL_MAP, reduce_prompt, timeout=300.0)
        except Exception as e:
            synthesis = f"[ERREUR REDUCE 9b ET 14b : {e}]"

    out_dir = ROOT / "memory" / "introspective_audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    out_file = out_dir / f"cardiac_engine_{ts}.md"
    out_file.write_text(
        f"# Audit Introspectif — cardiac_engine.py\n\n"
        f"Date : {ts}\n"
        f"Modeles : MAP={MODEL_MAP} / REDUCE={MODEL_REDUCE}\n"
        f"Chunks audites : {len(chunks)}\n\n"
        f"---\n\n## SYNTHESE (REDUCE)\n\n{synthesis}\n\n"
        f"---\n\n## NOTES INDIVIDUELLES (MAP)\n\n{notes_block}\n",
        encoding="utf-8",
    )
    print(f"\n=== RAPPORT ECRIT : {out_file} ===", flush=True)
    print(f"\n{synthesis}", flush=True)


if __name__ == "__main__":
    main()
