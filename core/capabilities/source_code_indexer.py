"""V15 — Organe d'Introspection RAG.

Indexe le code source de Prométhée (core/, Agents/, tools/) dans la
collection ChromaDB 'source_code' pour tuer le Perroquet Architectural.

Contexte : observation 23/04 matin (Test Y ex.84 Arrow) — le LLM 9B,
prive d'acces en lecture a ses propres fichiers, invente des mecanismes
plausibles ("consensus probabilistique", "ponderation par fiabilite")
au lieu d'admettre son ignorance. C'est la confabulation de Korsakoff
algorithmique.

V15 lui donne des yeux : au lieu de fantasmer son architecture, il la
consulte via RAG semantique sur ses propres .py.

Design (2026-04-23, apres audit Gemini) :
- Chunking AST : 1 chunk = 1 fonction OU 1 methode de classe OU 1 classe
  compacte. Les classes volumineuses sont eclatees par methodes.
- Metadonnees hierarchiques strictes : filepath + class_name + function_name
  pour que le LLM sache TOUJOURS ou vient un chunk quand il le lit.
- Re-indexation incrementielle via mtime cache. Drop des anciens chunks
  du meme fichier AVANT ajout des nouveaux pour eviter les doublons.
- Singleton (comme les autres capabilities : dropzone_indexer, github_mirror).

0 LLM, 0 GPU, indexation boot ~2-5s pour 100 fichiers.
"""
from __future__ import annotations

import ast
import hashlib
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Dossiers indexes (lecture seule, chemin relatif au project_root)
INDEXED_DIRS = ("core", "Agents", "tools")

# Exclusions
EXCLUDED_NAME_PATTERNS = ("__pycache__", "unsloth_compiled_cache")
EXCLUDED_FILE_PATTERNS = (
    "test_",          # tests pytest
    "_test.py",       # suffixe test
    "conftest.py",
)

# Seuil max caractères par chunk (evite les classes monstres)
MAX_CHUNK_CHARS = 4000

# Nom de la collection ChromaDB
COLLECTION_NAME = "source_code"


class SourceCodeIndexer:
    """Indexeur singleton du code source dans ChromaDB."""

    _instance: Optional["SourceCodeIndexer"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        # filepath absolu -> mtime deja indexe (re-indexation incrementielle)
        self._mtime_cache: Dict[str, float] = {}
        self._project_root: Optional[str] = None
        self._stats = {
            "files_indexed_total": 0,
            "chunks_added_total": 0,
            "last_scan_at": None,
        }

    @classmethod
    def reset_singleton(cls) -> None:
        """Pour les tests uniquement."""
        cls._instance = None

    # ──────────────────────────────────────────────────────────
    # Accès ChromaDB
    # ──────────────────────────────────────────────────────────

    def _get_manager(self):
        try:
            from core.vector_store import ChromaMemoryManager
            return ChromaMemoryManager.get_instance()
        except Exception as e:
            logger.warning(f"V15 : ChromaDB indisponible : {e}")
            return None

    # ──────────────────────────────────────────────────────────
    # Filtres fichiers
    # ──────────────────────────────────────────────────────────

    def _should_index(self, filename: str, full_path: str) -> bool:
        """Filtre les fichiers a indexer."""
        if not filename.endswith(".py"):
            return False
        if filename == "__init__.py" and os.path.getsize(full_path) < 100:
            return False  # init quasi-vide, rien a indexer
        for pat in EXCLUDED_FILE_PATTERNS:
            if pat in filename:
                return False
        for pat in EXCLUDED_NAME_PATTERNS:
            if pat in full_path:
                return False
        return True

    # ──────────────────────────────────────────────────────────
    # Chunking AST
    # ──────────────────────────────────────────────────────────

    def _chunk_id(self, rel_filepath: str, chunk_key: str) -> str:
        """ID stable et court pour ChromaDB."""
        raw = f"{rel_filepath}::{chunk_key}"
        return "v15_" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]

    def _get_segment(self, lines: List[str], start: int, end: int) -> str:
        """Extrait les lignes [start, end] du source (1-indexed, inclusif)."""
        if start < 1:
            start = 1
        if end > len(lines):
            end = len(lines)
        return "\n".join(lines[start - 1 : end])

    def _truncate_if_needed(self, code: str) -> str:
        if len(code) <= MAX_CHUNK_CHARS:
            return code
        return code[:MAX_CHUNK_CHARS] + "\n# ... [chunk tronque — fichier complet dans core/] ..."

    def _chunk_by_ast(self, rel_filepath: str, source: str) -> List[Dict[str, Any]]:
        """Decoupe un fichier Python en chunks AST.

        Chaque chunk :
          - 1 fonction top-level
          - 1 classe (si < MAX_CHUNK_CHARS), sinon eclatee par methode
          - 1 methode de classe (attachee a son class_name)

        Retourne une liste de dicts {chunk_key, code, metadata}.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            logger.debug(f"V15 : SyntaxError dans {rel_filepath}, skip : {e}")
            return []
        except Exception as e:
            logger.debug(f"V15 : parse error {rel_filepath} : {e}")
            return []

        lines = source.splitlines()
        chunks: List[Dict[str, Any]] = []

        # Docstring de module (si présent) devient un chunk "module header"
        module_doc = ast.get_docstring(tree) or ""
        if module_doc.strip():
            chunks.append({
                "chunk_key": "module:header",
                "code": f'"""{module_doc}"""',
                "metadata": {
                    "filepath": rel_filepath,
                    "class_name": "",
                    "function_name": "",
                    "node_type": "module_doc",
                    "n_lines": module_doc.count("\n") + 1,
                },
            })

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end_line = node.end_lineno or node.lineno
                code = self._truncate_if_needed(
                    self._get_segment(lines, node.lineno, end_line)
                )
                chunks.append({
                    "chunk_key": f"func:{node.name}",
                    "code": code,
                    "metadata": {
                        "filepath": rel_filepath,
                        "class_name": "",
                        "function_name": node.name,
                        "node_type": "function",
                        "n_lines": end_line - node.lineno + 1,
                    },
                })

            elif isinstance(node, ast.ClassDef):
                end_line = node.end_lineno or node.lineno
                full_class_code = self._get_segment(lines, node.lineno, end_line)

                if len(full_class_code) <= MAX_CHUNK_CHARS:
                    # Classe compacte : 1 chunk global
                    chunks.append({
                        "chunk_key": f"class:{node.name}",
                        "code": full_class_code,
                        "metadata": {
                            "filepath": rel_filepath,
                            "class_name": node.name,
                            "function_name": "",
                            "node_type": "class",
                            "n_lines": end_line - node.lineno + 1,
                        },
                    })
                else:
                    # Classe trop grande : eclatee par methode + 1 chunk
                    # "class header" (signature + docstring + attributs)
                    first_method_line = None
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            first_method_line = sub.lineno
                            break
                    header_end = (first_method_line - 1) if first_method_line else end_line
                    header_code = self._get_segment(lines, node.lineno, header_end)
                    if header_code.strip():
                        chunks.append({
                            "chunk_key": f"class:{node.name}::header",
                            "code": self._truncate_if_needed(header_code),
                            "metadata": {
                                "filepath": rel_filepath,
                                "class_name": node.name,
                                "function_name": "",
                                "node_type": "class_header",
                                "n_lines": header_end - node.lineno + 1,
                            },
                        })
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            sub_end = sub.end_lineno or sub.lineno
                            sub_code = self._truncate_if_needed(
                                self._get_segment(lines, sub.lineno, sub_end)
                            )
                            chunks.append({
                                "chunk_key": f"class:{node.name}::method:{sub.name}",
                                "code": sub_code,
                                "metadata": {
                                    "filepath": rel_filepath,
                                    "class_name": node.name,
                                    "function_name": sub.name,
                                    "node_type": "method",
                                    "n_lines": sub_end - sub.lineno + 1,
                                },
                            })

        return chunks

    # ──────────────────────────────────────────────────────────
    # Indexation
    # ──────────────────────────────────────────────────────────

    def _delete_old_chunks_for_file(self, rel_filepath: str) -> bool:
        """Purge les anciens chunks ChromaDB d'un fichier avant re-indexation.

        CRITIQUE : sans ce drop, ChromaDB accumule des doublons a chaque
        re-indexation. La filtre where={'filepath': ...} ChromaDB purge
        atomiquement tous les chunks du fichier en 1 appel.
        """
        mgr = self._get_manager()
        if not mgr:
            return False
        try:
            col = mgr._get_collection(COLLECTION_NAME)
            col.delete(where={"filepath": rel_filepath})
            return True
        except Exception as e:
            logger.debug(f"V15 : delete anciens {rel_filepath} : {e}")
            return False

    def _relative_path(self, abs_filepath: str) -> str:
        """Convertit chemin absolu → relatif au project_root, format unix."""
        if self._project_root and abs_filepath.startswith(self._project_root):
            rel = abs_filepath[len(self._project_root):].lstrip(os.sep)
        else:
            rel = os.path.basename(abs_filepath)
        return rel.replace(os.sep, "/")

    def _index_file(self, abs_filepath: str) -> int:
        """Indexe un fichier si son mtime a change. Returns nb chunks ajoutes."""
        try:
            mtime = os.path.getmtime(abs_filepath)
        except OSError:
            return 0

        # Skip si déjà indexé avec même mtime
        if self._mtime_cache.get(abs_filepath) == mtime:
            return 0

        try:
            with open(abs_filepath, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
        except Exception as e:
            logger.debug(f"V15 : lecture {abs_filepath} : {e}")
            return 0

        rel_path = self._relative_path(abs_filepath)
        chunks = self._chunk_by_ast(rel_path, source)
        if not chunks:
            # Marquer quand même comme indexé pour éviter retraitement
            self._mtime_cache[abs_filepath] = mtime
            return 0

        # Drop anciens avant ajout → évite les doublons ChromaDB
        self._delete_old_chunks_for_file(rel_path)

        # Ajouter les nouveaux chunks
        mgr = self._get_manager()
        if not mgr:
            return 0

        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        ids: List[str] = []
        for c in chunks:
            documents.append(c["code"])
            metadatas.append(c["metadata"])
            ids.append(self._chunk_id(rel_path, c["chunk_key"]))

        ok = mgr.add_documents(
            documents, metadatas, ids, collection_name=COLLECTION_NAME
        )
        if ok:
            self._mtime_cache[abs_filepath] = mtime
            return len(chunks)
        return 0

    def index_at_boot(self, project_root: Optional[str] = None) -> Dict[str, int]:
        """Scanne les dossiers cibles et indexe tous les .py.

        Returns:
            Dict {files_scanned, files_indexed, chunks_added, duration_s}.
        """
        import time
        t0 = time.time()

        if project_root is None:
            # core/capabilities/source_code_indexer.py → PROMETHEE_V11_*/
            project_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
        self._project_root = project_root

        stats = {
            "files_scanned": 0,
            "files_indexed": 0,
            "chunks_added": 0,
            "duration_s": 0.0,
        }

        for dirname in INDEXED_DIRS:
            target_dir = os.path.join(project_root, dirname)
            if not os.path.isdir(target_dir):
                continue
            for root, dirs, files in os.walk(target_dir):
                # Filtrer les sous-dossiers exclus
                dirs[:] = [
                    d for d in dirs
                    if not any(p in d for p in EXCLUDED_NAME_PATTERNS)
                    and not d.startswith(".")
                ]
                for fname in files:
                    fp = os.path.join(root, fname)
                    if not self._should_index(fname, fp):
                        continue
                    stats["files_scanned"] += 1
                    n = self._index_file(fp)
                    if n > 0:
                        stats["files_indexed"] += 1
                        stats["chunks_added"] += n

        stats["duration_s"] = round(time.time() - t0, 2)
        self._stats["files_indexed_total"] += stats["files_indexed"]
        self._stats["chunks_added_total"] += stats["chunks_added"]
        self._stats["last_scan_at"] = time.time()

        logger.info(
            f"V15 : SourceCodeIndexer — {stats['files_indexed']}/"
            f"{stats['files_scanned']} fichiers, {stats['chunks_added']} chunks, "
            f"{stats['duration_s']}s."
        )
        return stats

    # ──────────────────────────────────────────────────────────
    # Query
    # ──────────────────────────────────────────────────────────

    def query(
        self,
        query_text: str,
        n_results: int = 3,
        filter_filepath: Optional[str] = None,
        filter_function: Optional[str] = None,
        filter_class: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Recherche sémantique dans la collection source_code.

        Args:
            query_text: question sémantique (nom de fonction, description...)
            n_results: nombre de chunks max a retourner
            filter_filepath / filter_function / filter_class: filtres exacts
                optionnels sur les metadatas (recherche ciblée).

        Returns:
            Liste de dicts {code, metadata, distance} tries par pertinence.
        """
        mgr = self._get_manager()
        if not mgr:
            return []
        try:
            col = mgr._get_collection(COLLECTION_NAME)

            # Construire le filtre where ChromaDB
            where: Dict[str, Any] = {}
            if filter_filepath:
                where["filepath"] = filter_filepath
            if filter_function:
                where["function_name"] = filter_function
            if filter_class:
                where["class_name"] = filter_class

            kwargs = {"query_texts": [query_text], "n_results": n_results}
            if where:
                kwargs["where"] = where
            results = col.query(**kwargs)

            docs = (results.get("documents") or [[]])[0]
            metas = (results.get("metadatas") or [[]])[0]
            dists = (results.get("distances") or [[0.0] * len(docs)])[0]

            out = []
            for doc, meta, dist in zip(docs, metas, dists):
                out.append({
                    "code": doc,
                    "metadata": meta,
                    "distance": float(dist) if dist is not None else 0.0,
                })
            return out
        except Exception as e:
            logger.warning(f"V15 : query '{query_text[:30]}' echoue : {e}")
            return []

    def format_chunk_for_prompt(self, chunk: Dict[str, Any]) -> str:
        """Formate un chunk pour injection dans un prompt LLM.

        Format hiérarchique strict pour que le LLM sache TOUJOURS ou vient
        le code qu'il lit :

            Fichier: core/debate_engine.py | Classe: Council | Fonction: _evaluate
            ```python
            def _evaluate(self, ...): ...
            ```
        """
        meta = chunk.get("metadata") or {}
        header_parts = [f"Fichier: {meta.get('filepath', '?')}"]
        if meta.get("class_name"):
            header_parts.append(f"Classe: {meta['class_name']}")
        if meta.get("function_name"):
            header_parts.append(f"Fonction: {meta['function_name']}")
        header = " | ".join(header_parts)
        return f"{header}\n```python\n{chunk['code']}\n```"

    # ──────────────────────────────────────────────────────────
    # Stats (pour diagnostic)
    # ──────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Statistiques d'indexation cumulatives."""
        mgr = self._get_manager()
        collection_count = 0
        if mgr:
            try:
                collection_count = mgr.count_documents(collection_name=COLLECTION_NAME)
            except Exception:
                pass
        return {
            "files_cached_mtime": len(self._mtime_cache),
            "collection_document_count": collection_count,
            **self._stats,
        }


# Singleton global
indexer = SourceCodeIndexer()
