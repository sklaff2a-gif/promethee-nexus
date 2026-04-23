"""Tests V15 — SourceCodeIndexer (RAG source code).

Teste uniquement la logique chunking AST + format d'injection, sans
dependre de ChromaDB runtime (mockee).
"""
from unittest.mock import MagicMock, patch

import pytest

from core.capabilities.source_code_indexer import (
    MAX_CHUNK_CHARS,
    SourceCodeIndexer,
)


@pytest.fixture
def fresh_indexer():
    SourceCodeIndexer.reset_singleton()
    yield SourceCodeIndexer()
    SourceCodeIndexer.reset_singleton()


# ═══════════════════════════════════════════════════════════════════════
# Chunking AST
# ═══════════════════════════════════════════════════════════════════════


class TestChunkingAST:

    def test_function_top_level(self, fresh_indexer):
        source = '''"""Module doc."""

def hello(name):
    """Salue."""
    return f"hi {name}"
'''
        chunks = fresh_indexer._chunk_by_ast("fake.py", source)
        types = {c["metadata"]["node_type"] for c in chunks}
        assert "function" in types
        func_chunks = [c for c in chunks if c["metadata"]["node_type"] == "function"]
        assert len(func_chunks) == 1
        assert func_chunks[0]["metadata"]["function_name"] == "hello"
        assert func_chunks[0]["metadata"]["class_name"] == ""

    def test_module_docstring_indexed(self, fresh_indexer):
        source = '''"""This module does X."""

def foo():
    pass
'''
        chunks = fresh_indexer._chunk_by_ast("x.py", source)
        docs = [c for c in chunks if c["metadata"]["node_type"] == "module_doc"]
        assert len(docs) == 1
        assert "does X" in docs[0]["code"]

    def test_class_compact_single_chunk(self, fresh_indexer):
        source = '''class Small:
    """Small class."""
    def a(self): return 1
    def b(self): return 2
'''
        chunks = fresh_indexer._chunk_by_ast("s.py", source)
        class_chunks = [c for c in chunks if c["metadata"]["node_type"] == "class"]
        assert len(class_chunks) == 1
        assert class_chunks[0]["metadata"]["class_name"] == "Small"
        # Le code du chunk contient les 2 methodes
        assert "def a" in class_chunks[0]["code"]
        assert "def b" in class_chunks[0]["code"]

    def test_class_large_split_by_method(self, fresh_indexer):
        # Construire une classe > MAX_CHUNK_CHARS (chaque methode ~4000 chars)
        big_body = "    # " + "x" * MAX_CHUNK_CHARS + "\n"
        source = (
            '"""Module."""\n'
            'class Big:\n'
            '    """Big class."""\n'
            '    def method_one(self):\n' + big_body +
            '        return 1\n'
            '    def method_two(self):\n' + big_body +
            '        return 2\n'
        )
        chunks = fresh_indexer._chunk_by_ast("big.py", source)
        types = {c["metadata"]["node_type"] for c in chunks}
        # Grande classe : eclatee en header + methodes
        assert "class_header" in types
        method_chunks = [c for c in chunks if c["metadata"]["node_type"] == "method"]
        assert len(method_chunks) == 2
        names = {c["metadata"]["function_name"] for c in method_chunks}
        assert names == {"method_one", "method_two"}
        # Chaque methode porte le class_name
        assert all(c["metadata"]["class_name"] == "Big" for c in method_chunks)

    def test_syntax_error_returns_empty(self, fresh_indexer):
        chunks = fresh_indexer._chunk_by_ast("bad.py", "def broken(\n  pass")
        assert chunks == []

    def test_chunk_truncation_at_max(self, fresh_indexer):
        # Fonction enorme, ligne unique
        source = 'def huge():\n    x = "' + "A" * (MAX_CHUNK_CHARS * 2) + '"\n'
        chunks = fresh_indexer._chunk_by_ast("huge.py", source)
        assert len(chunks) == 1
        assert len(chunks[0]["code"]) <= MAX_CHUNK_CHARS + 100  # marge pour suffixe
        assert "tronque" in chunks[0]["code"].lower()


# ═══════════════════════════════════════════════════════════════════════
# Metadata hierarchiques
# ═══════════════════════════════════════════════════════════════════════


class TestMetadataHierarchy:

    def test_function_has_filepath(self, fresh_indexer):
        source = "def foo(): pass"
        chunks = fresh_indexer._chunk_by_ast("core/foo.py", source)
        assert chunks[0]["metadata"]["filepath"] == "core/foo.py"

    def test_method_has_both_class_and_function(self, fresh_indexer):
        source = "class A:\n    def m(self): pass\n"
        chunks = fresh_indexer._chunk_by_ast("a.py", source)
        # Classe compacte : 1 chunk class, pas de chunk method séparé
        # On vérifie juste que le class_name est bien rempli
        for c in chunks:
            if c["metadata"]["node_type"] == "class":
                assert c["metadata"]["class_name"] == "A"

    def test_n_lines_populated(self, fresh_indexer):
        source = "def foo():\n    x = 1\n    y = 2\n    return x + y\n"
        chunks = fresh_indexer._chunk_by_ast("x.py", source)
        func = next(c for c in chunks if c["metadata"]["node_type"] == "function")
        assert func["metadata"]["n_lines"] >= 4


# ═══════════════════════════════════════════════════════════════════════
# Format d'injection
# ═══════════════════════════════════════════════════════════════════════


class TestFormatForPrompt:

    def test_function_chunk_format(self, fresh_indexer):
        chunk = {
            "code": "def _evaluate(self): return 1",
            "metadata": {
                "filepath": "core/debate_engine.py",
                "class_name": "Council",
                "function_name": "_evaluate",
                "node_type": "method",
            },
        }
        out = fresh_indexer.format_chunk_for_prompt(chunk)
        assert "Fichier: core/debate_engine.py" in out
        assert "Classe: Council" in out
        assert "Fonction: _evaluate" in out
        assert "```python" in out
        assert "def _evaluate" in out

    def test_top_level_function_no_class(self, fresh_indexer):
        chunk = {
            "code": "def helper(): return 42",
            "metadata": {
                "filepath": "tools/h.py",
                "class_name": "",
                "function_name": "helper",
                "node_type": "function",
            },
        }
        out = fresh_indexer.format_chunk_for_prompt(chunk)
        assert "Fichier: tools/h.py" in out
        assert "Classe:" not in out  # pas affiché si vide
        assert "Fonction: helper" in out

    def test_class_header_no_function(self, fresh_indexer):
        chunk = {
            "code": 'class Foo:\n    """doc"""\n    x = 1',
            "metadata": {
                "filepath": "f.py",
                "class_name": "Foo",
                "function_name": "",
                "node_type": "class_header",
            },
        }
        out = fresh_indexer.format_chunk_for_prompt(chunk)
        assert "Classe: Foo" in out
        assert "Fonction:" not in out


# ═══════════════════════════════════════════════════════════════════════
# Chunk ID (stable, unique, reproductible)
# ═══════════════════════════════════════════════════════════════════════


class TestChunkId:

    def test_id_stable_across_calls(self, fresh_indexer):
        id1 = fresh_indexer._chunk_id("core/x.py", "func:foo")
        id2 = fresh_indexer._chunk_id("core/x.py", "func:foo")
        assert id1 == id2

    def test_id_differs_per_file(self, fresh_indexer):
        id1 = fresh_indexer._chunk_id("core/x.py", "func:foo")
        id2 = fresh_indexer._chunk_id("core/y.py", "func:foo")
        assert id1 != id2

    def test_id_differs_per_chunk(self, fresh_indexer):
        id1 = fresh_indexer._chunk_id("core/x.py", "func:foo")
        id2 = fresh_indexer._chunk_id("core/x.py", "func:bar")
        assert id1 != id2

    def test_id_has_v15_prefix(self, fresh_indexer):
        cid = fresh_indexer._chunk_id("x.py", "func:y")
        assert cid.startswith("v15_")


# ═══════════════════════════════════════════════════════════════════════
# Filtres de fichiers
# ═══════════════════════════════════════════════════════════════════════


class TestShouldIndex:

    def test_python_file_indexed(self, fresh_indexer, tmp_path):
        f = tmp_path / "module.py"
        f.write_text("def x(): pass")
        assert fresh_indexer._should_index("module.py", str(f))

    def test_non_python_skipped(self, fresh_indexer, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("hi")
        assert not fresh_indexer._should_index("doc.md", str(f))

    def test_test_file_skipped(self, fresh_indexer, tmp_path):
        f = tmp_path / "test_foo.py"
        f.write_text("def test_x(): pass")
        assert not fresh_indexer._should_index("test_foo.py", str(f))

    def test_conftest_skipped(self, fresh_indexer, tmp_path):
        f = tmp_path / "conftest.py"
        f.write_text("x = 1")
        assert not fresh_indexer._should_index("conftest.py", str(f))

    def test_small_init_skipped(self, fresh_indexer, tmp_path):
        f = tmp_path / "__init__.py"
        f.write_text("# empty")
        assert not fresh_indexer._should_index("__init__.py", str(f))

    def test_substantial_init_indexed(self, fresh_indexer, tmp_path):
        f = tmp_path / "__init__.py"
        f.write_text("# " + "x" * 200 + "\n\ndef actual_func():\n    pass\n")
        assert fresh_indexer._should_index("__init__.py", str(f))
