"""V31 — Indexation batch de la collection ChromaDB `source_code`.

One-shot : scanne core/, Agents/, math_core/, tools/ et indexe tous
les .py via SourceCodeIndexer (chunking AST par fonction/classe).

Apres ce script, la collection `source_code` contient les chunks de
TOUT le code projet, queryable semantiquement par le RAG V31
(_build_v31_dependency_context).

Usage : python tools/index_source_code.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.capabilities.source_code_indexer import indexer


def main() -> int:
    print("=== V31 INDEXATION SOURCE_CODE — Batch one-shot ===")
    print(f"Project root : {ROOT}")
    t0 = time.time()
    stats = indexer.index_at_boot(str(ROOT))
    duration = time.time() - t0
    print()
    print(f"=== STATISTIQUES ===")
    print(f"  files_scanned : {stats.get('files_scanned')}")
    print(f"  files_indexed : {stats.get('files_indexed')}")
    print(f"  chunks_added  : {stats.get('chunks_added')}")
    print(f"  duree         : {duration:.1f}s")
    print()
    if stats.get("chunks_added", 0) > 0:
        print(f"OK — collection source_code peuplee. Le RAG V31 peut maintenant")
        print(f"     ramener du code reel des dependances cross-file.")
        return 0
    print("ERREUR — aucun chunk indexe. Verifier vector_store + ChromaDB.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
