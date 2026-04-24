"""V15.3 — RAG source_code dans les prompts ecole.

Teste _build_v15_school_context qui injecte le vrai code dans les
livrables CODE_REVIEW / RESEARCH / WORKSHOP pour empecher le Perroquet
Hallucinatoire post-V4.3.
"""
from unittest.mock import MagicMock, patch

import pytest

from core.autonomy_engine import AutonomyEngine


class TestV15SchoolContextBuilder:
    """Tests du helper static _build_v15_school_context."""

    def test_no_chunks_returns_empty(self):
        """Pas de refs + pas de target_file -> chaine vide."""
        info = {"subject": {}}
        with patch("core.capabilities.source_code_indexer.indexer") as mock:
            mock.query.return_value = []
            out = AutonomyEngine._build_v15_school_context(
                "RESEARCH", "question generique sans ref", info
            )
        assert out == ""

    def test_code_review_target_file_triggers_filter(self):
        """CODE_REVIEW avec target_file → query filter_filepath=target."""
        info = {"subject": {"target_file": "core/prefrontal.py"}}
        with patch("core.capabilities.source_code_indexer.indexer") as mock:
            mock.query.return_value = [{
                "code": "class Prefrontal: pass",
                "metadata": {
                    "filepath": "core/prefrontal.py",
                    "class_name": "Prefrontal",
                    "function_name": "",
                    "node_type": "class",
                },
            }]
            mock.format_chunk_for_prompt.return_value = "Fichier: core/prefrontal.py | Classe: Prefrontal\n```python\nclass Prefrontal: pass\n```"
            out = AutonomyEngine._build_v15_school_context(
                "CODE_REVIEW",
                "Revue de code : core/prefrontal.py",
                info,
            )
        # L'appel doit avoir ete fait avec filter_filepath et n_results=6
        mock.query.assert_any_call(
            "core/prefrontal.py", n_results=6,
            filter_filepath="core/prefrontal.py",
        )
        assert "[INJECTION DE CONTEXTE STRICTE" in out
        assert "core/prefrontal.py" in out

    def test_research_scans_prompt_for_refs(self):
        """RESEARCH : radar detecte fonctions/classes/fichiers dans le prompt."""
        info = {"subject": {"topic": "debug core/council.py et `Council` avec _is_consensus_v2()"}}
        prompt_text = "analyse core/council.py et la classe `Council`"
        with patch("core.capabilities.source_code_indexer.indexer") as mock:
            mock.query.return_value = [{
                "code": "class Council: pass",
                "metadata": {
                    "filepath": "core/council.py",
                    "class_name": "Council",
                    "function_name": "",
                    "node_type": "class",
                },
            }]
            mock.format_chunk_for_prompt.return_value = "Fichier: core/council.py | Classe: Council\n```\n...\n```"
            out = AutonomyEngine._build_v15_school_context(
                "RESEARCH", prompt_text, info
            )
        # Doit avoir essaye de querier sur Council (classe detectee) ou sur le fichier
        call_args_list = [c.args for c in mock.query.call_args_list]
        # Au moins une query a été faite
        assert len(call_args_list) >= 1
        assert out != ""

    def test_plafond_6_chunks(self):
        """Max 6 chunks injectes meme si plus sont disponibles."""
        info = {"subject": {"target_file": "core/big.py"}}
        # 10 chunks disponibles
        many_chunks = [{
            "code": f"def f{i}(): pass",
            "metadata": {
                "filepath": "core/big.py",
                "class_name": "",
                "function_name": f"f{i}",
                "node_type": "function",
            },
        } for i in range(10)]
        with patch("core.capabilities.source_code_indexer.indexer") as mock:
            mock.query.return_value = many_chunks
            mock.format_chunk_for_prompt.side_effect = lambda c: f"FN:{c['metadata']['function_name']}"
            out = AutonomyEngine._build_v15_school_context(
                "CODE_REVIEW", "audit", info
            )
        # Format doit avoir ete appele max 6 fois
        assert mock.format_chunk_for_prompt.call_count <= 6

    def test_header_includes_override_marker(self):
        """Le header inclut [INJECTION DE CONTEXTE STRICTE] pour l'autorite."""
        info = {"subject": {"target_file": "core/x.py"}}
        with patch("core.capabilities.source_code_indexer.indexer") as mock:
            mock.query.return_value = [{
                "code": "x",
                "metadata": {"filepath": "core/x.py", "class_name": "",
                             "function_name": "", "node_type": "function"},
            }]
            mock.format_chunk_for_prompt.return_value = "CHUNK"
            out = AutonomyEngine._build_v15_school_context(
                "CODE_REVIEW", "audit x", info
            )
        assert "[INJECTION DE CONTEXTE STRICTE" in out
        assert "DONNEES D'ENTREE" in out
        assert "hallucination" in out.lower()

    def test_deduplication(self):
        """Meme chunk retourne par plusieurs queries -> dedup."""
        info = {"subject": {"target_file": "core/a.py"}}
        dup_chunk = {
            "code": "def foo(): pass",
            "metadata": {
                "filepath": "core/a.py",
                "class_name": "",
                "function_name": "foo",
                "node_type": "function",
            },
        }
        with patch("core.capabilities.source_code_indexer.indexer") as mock:
            # Chaque query retourne le meme chunk
            mock.query.return_value = [dup_chunk]
            mock.format_chunk_for_prompt.side_effect = lambda c: f"FN:{c['metadata']['function_name']}"
            out = AutonomyEngine._build_v15_school_context(
                "CODE_REVIEW",
                "core/a.py et foo() et `Foo`",
                info,
            )
        # format ne doit etre appele qu'une fois pour (core/a.py, "", "foo")
        # meme si plusieurs queries ont ramene ce chunk
        assert mock.format_chunk_for_prompt.call_count == 1

    def test_query_exception_returns_empty_gracefully(self):
        """Si indexer.query leve une exception, retour silencieux vide."""
        info = {"subject": {"target_file": "core/x.py"}}
        with patch("core.capabilities.source_code_indexer.indexer") as mock:
            mock.query.side_effect = RuntimeError("ChromaDB down")
            try:
                out = AutonomyEngine._build_v15_school_context(
                    "CODE_REVIEW", "prompt", info
                )
            except Exception:
                pytest.fail("La fonction doit etre resiliente aux erreurs indexer")
        # Peut retourner "" ou une chaine vide ; l'important est de ne pas lever
        assert isinstance(out, str)
