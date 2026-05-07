"""Tests des composants deterministes IMMERSION_DOMAIN.

Couvre Phase 1 (chunker) et Phase 3+3.5 (compression). Les Phases 2
(extractor LLM) et 4 (digestion_routine) sont des squelettes — leurs
tests viendront avec leur implementation.
"""
import os

os.environ.setdefault("PROMETHEE_TEST_MODE", "1")

from unittest.mock import MagicMock

import pytest

from core.immersion.chunker import (
    Chunk,
    ChunkingResult,
    chunk_post_mortem,
    estimate_tokens,
    TARGET_CHARS_PER_CHUNK,
    MAX_CHUNKS_PER_DOC,
)
from core.immersion.compression import (
    CompressionResult,
    compute_compression,
    assimilate_unknowns,
    FLOATING_CONCEPT_INITIAL_ENERGY,
    FLOATING_CONCEPT_NODE_TYPE,
)
from core.immersion.extractor import parse_extraction_response


# ============================================================
# Phase 1 — Chunker
# ============================================================


class TestChunkerBasic:
    def test_empty_text_returns_no_chunks(self):
        result = chunk_post_mortem("")
        assert result.chunks == []
        assert result.too_large is False
        assert result.source_chars == 0

    def test_whitespace_only_returns_no_chunks(self):
        result = chunk_post_mortem("   \n\n  \t  ")
        assert result.chunks == []

    def test_short_text_one_chunk(self):
        text = "Court paragraphe. " * 5
        result = chunk_post_mortem(text)
        assert len(result.chunks) == 1
        assert result.chunks[0].position == 0
        assert result.chunks[0].text.strip() != ""

    def test_estimate_tokens_proportional_to_length(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("a" * 8) == 2     # 8/4
        assert estimate_tokens("a" * 1200) == 300


class TestChunkerMarkdownBoundaries:
    def test_markdown_headers_detected_as_boundary_type(self):
        # Petit doc en 2 sections : tient en 1 chunk mais le boundary_type
        # doit etre "header" (la priorite de decoupage a ete utilisee).
        text = "## Section 1\nContenu A.\n## Section 2\nContenu B."
        result = chunk_post_mortem(text)
        assert len(result.chunks) >= 1
        assert result.chunks[0].boundary_type == "header"

    def test_markdown_headers_split_when_oversized(self):
        # Sections assez grosses pour forcer le split (chaque > target)
        big_a = "Contenu A. " * 200  # ~2200 chars
        big_b = "Contenu B. " * 200
        text = f"## Section 1\n{big_a}\n## Section 2\n{big_b}"
        result = chunk_post_mortem(text)
        assert len(result.chunks) >= 2

    def test_single_header_falls_back_to_paragraphs(self):
        # Un seul ## -> pas de split markdown utile -> fallback paragraphes
        text = "## Solo\n\nPara 1.\n\nPara 2.\n\nPara 3."
        result = chunk_post_mortem(text)
        assert len(result.chunks) >= 1


class TestChunkerPostMortemBoundaries:
    def test_post_mortem_keywords_detected_as_boundary_type(self):
        text = (
            "Time: 2026-01-15 10:00 UTC\nbrief event.\n"
            "Impact: tier-1 customers\nshort impact.\n"
            "Root Cause: BGP misconfig\nshort rca.\n"
            "Resolution: rollback\nshort res."
        )
        result = chunk_post_mortem(text)
        assert len(result.chunks) >= 1
        # Le decoupage post_mortem a ete utilise (meme si tout tient en 1 chunk)
        types = {c.boundary_type for c in result.chunks}
        assert "post_mortem" in types or "header" in types

    def test_post_mortem_keywords_split_when_oversized(self):
        text = (
            "Time: 2026-01-15 10:00 UTC\n" + ("event detail. " * 200) +
            "\nImpact: tier-1 customers\n" + ("impact detail. " * 200) +
            "\nRoot Cause: BGP misconfig\n" + ("rca detail. " * 200) +
            "\nResolution: rollback\n" + ("resolution detail. " * 200)
        )
        result = chunk_post_mortem(text)
        assert len(result.chunks) >= 2


class TestChunkerSizingAndPacking:
    def test_chunks_respect_target_size(self):
        # 60 paragraphes de ~50 chars = 3000 chars total
        text = "\n\n".join([f"Paragraphe {i} avec quelques mots." for i in range(60)])
        result = chunk_post_mortem(text)
        for c in result.chunks:
            # Tolerance large pour le greedy pack (peut aller jusqu a 2x cible)
            assert len(c.text) <= TARGET_CHARS_PER_CHUNK * 2 + 100

    def test_too_large_flag_when_above_max_chunks(self):
        # Generer assez de paragraphes courts pour exceder MAX_CHUNKS
        # (avec target 1200 chars et chaque para ~30 chars, il faut
        # forcer des paragraphes plus gros)
        long_para = "x" * 1200
        text = "\n\n".join([f"## H{i}\n{long_para}" for i in range(MAX_CHUNKS_PER_DOC + 5)])
        result = chunk_post_mortem(text)
        assert result.too_large is True
        assert len(result.chunks) > MAX_CHUNKS_PER_DOC

    def test_small_document_not_marked_too_large(self):
        text = "## Header\n\nSmall content.\n\n## Other\n\nMore content."
        result = chunk_post_mortem(text)
        assert result.too_large is False

    def test_chunk_positions_sequential(self):
        text = "\n\n".join([f"Paragraphe {i} " * 50 for i in range(10)])
        result = chunk_post_mortem(text)
        positions = [c.position for c in result.chunks]
        assert positions == list(range(len(result.chunks)))


# ============================================================
# Phase 3 — Compression (ratio + edge cases)
# ============================================================


class TestCompressionBasic:
    def test_all_recognized_ratio_is_one(self):
        result = compute_compression(["TCP", "BGP", "MTU"], ["TCP", "BGP", "MTU"])
        assert result.total_extracted == 3
        assert result.ratio == 1.0
        assert result.unknown == []
        assert result.is_meaningful is True
        assert result.verdict == "full_consolidation"

    def test_none_recognized_ratio_is_zero(self):
        result = compute_compression(["BGP", "Anycast", "MTU"], ["TCP"])
        assert result.total_extracted == 3
        assert result.ratio == 0.0
        assert set(result.unknown) == {"BGP", "Anycast", "MTU"}
        assert result.is_meaningful is True
        assert result.verdict == "pain"

    def test_partial_ratio_micro_consolidation(self):
        # 2 sur 5 = 0.4 -> micro
        result = compute_compression(
            ["TCP", "UDP", "BGP", "Anycast", "MTU"],
            ["TCP", "UDP"],
        )
        assert result.ratio == pytest.approx(0.4)
        assert result.verdict == "micro_consolidation"

    def test_full_consolidation_threshold(self):
        # 4 sur 5 = 0.8 -> full
        result = compute_compression(
            ["TCP", "UDP", "BGP", "Anycast", "MTU"],
            ["TCP", "UDP", "BGP", "Anycast"],
        )
        assert result.ratio == pytest.approx(0.8)
        assert result.verdict == "full_consolidation"


class TestCompressionEdgeCases:
    def test_empty_extracted_returns_not_meaningful(self):
        """Tous les chunks ont retourne RIEN -> document ignore sans penalite."""
        result = compute_compression([], ["TCP", "BGP"])
        assert result.total_extracted == 0
        assert result.ratio is None
        assert result.is_meaningful is False
        assert result.verdict == "ignored"

    def test_empty_extracted_with_empty_known(self):
        """Cas extreme : pas d input, pas de connu. is_meaningful=False."""
        result = compute_compression([], [])
        assert result.is_meaningful is False
        assert result.verdict == "ignored"

    def test_whitespace_concepts_filtered(self):
        result = compute_compression(["TCP", "  ", "", "BGP"], ["TCP"])
        assert result.total_extracted == 2  # TCP, BGP

    def test_dedup_preserves_first_occurrence(self):
        result = compute_compression(["TCP", "BGP", "TCP", "BGP"], [])
        assert result.total_extracted == 2

    def test_normalization_case_insensitive(self):
        result = compute_compression(["tcp", "BGP"], ["TCP", "bgp"])
        assert result.ratio == 1.0
        assert len(result.recognized) == 2

    def test_normalization_underscore_equals_space(self):
        result = compute_compression(["root cause"], ["root_cause"])
        assert result.ratio == 1.0


# ============================================================
# Phase 3.5 — Assimilation des inconnus (Friston aplatissement)
# ============================================================


class TestAssimilation:
    def test_assimilate_creates_floating_concepts(self):
        synaptic = MagicMock()
        synaptic.nodes = {}  # pretendre vide
        synaptic.ensure_node.return_value = "node_id_xyz"

        n_added = assimilate_unknowns(["BGP", "Anycast", "MTU"], synaptic)

        assert synaptic.ensure_node.call_count == 3
        # Verifie que chaque appel utilise le bon type et l initial_energy
        for call_args in synaptic.ensure_node.call_args_list:
            args = call_args[0]
            assert args[1] == FLOATING_CONCEPT_NODE_TYPE
            assert args[2] == FLOATING_CONCEPT_INITIAL_ENERGY
            assert "immersion" in args[3]
            assert "floating" in args[3]

    def test_empty_unknowns_no_call(self):
        synaptic = MagicMock()
        n_added = assimilate_unknowns([], synaptic)
        assert n_added == 0
        synaptic.ensure_node.assert_not_called()

    def test_blank_concepts_skipped(self):
        synaptic = MagicMock()
        synaptic.nodes = {}
        synaptic.ensure_node.return_value = "id"
        assimilate_unknowns(["", "  ", "BGP"], synaptic)
        # Seul "BGP" doit etre injecte
        assert synaptic.ensure_node.call_count == 1


class TestFristonLoop:
    """Verifie le scenario complet : douleur -> assimilation -> reconnaissance."""

    def test_unknown_concepts_flow_through_pipeline(self):
        # Document 1 : Prometheus ne connait que TCP, decouvre 9 nouveaux
        comp_day1 = compute_compression(
            ["TCP", "BGP", "Anycast", "MTU", "RST", "SYN",
             "ASN", "OSPF", "ICMP", "TTL"],
            ["TCP"],
        )
        assert comp_day1.ratio == pytest.approx(0.1)
        assert len(comp_day1.unknown) == 9
        assert comp_day1.verdict == "pain"  # < 0.2 : douleur epistemique

        # Document 2 le lendemain : meme concepts -> sans Phase 3.5,
        # le ratio resterait 0.1 a vie. Avec Phase 3.5 simulee
        # (les inconnus ont ete injectes en floating_concepts) :
        known_after_day1 = ["TCP", "BGP", "Anycast", "MTU", "RST",
                            "SYN", "ASN", "OSPF", "ICMP", "TTL"]
        comp_day2 = compute_compression(
            ["TCP", "BGP", "Anycast", "MTU", "RST", "SYN",
             "ASN", "OSPF", "ICMP", "TTL"],
            known_after_day1,
        )
        assert comp_day2.ratio == 1.0
        assert comp_day2.verdict == "full_consolidation"
        # La courbe de surprise s aplatit : 0.1 -> 1.0


# ============================================================
# Phase 2 — Extractor (parser deterministe seulement)
# ============================================================


class TestExtractorParser:
    def test_parse_rien_response(self):
        result = parse_extraction_response("RIEN")
        assert result.is_nothing is True
        assert result.concepts == []

    def test_parse_rien_with_punctuation(self):
        result = parse_extraction_response("rien.")
        assert result.is_nothing is True

    def test_parse_concept_list(self):
        raw = "TCP\nBGP\nAnycast"
        result = parse_extraction_response(raw)
        assert result.is_nothing is False
        assert "TCP" in result.concepts
        assert "BGP" in result.concepts
        assert "Anycast" in result.concepts

    def test_parse_with_bullets(self):
        raw = "- TCP\n* BGP\n1. Anycast"
        result = parse_extraction_response(raw)
        assert "TCP" in result.concepts
        assert "BGP" in result.concepts
        assert "Anycast" in result.concepts

    def test_parse_rejects_long_lines(self):
        raw = "TCP\n" + "x" * 100 + "\nBGP"
        result = parse_extraction_response(raw)
        assert "TCP" in result.concepts
        assert "BGP" in result.concepts
        assert len(result.concepts) == 2  # Phrase de 100 chars rejetee

    def test_parse_rejects_stop_words(self):
        raw = "the\nTCP\nand"
        result = parse_extraction_response(raw)
        assert result.concepts == ["TCP"]

    def test_parse_strips_terminal_punctuation(self):
        raw = "TCP.\nBGP,\nAnycast;"
        result = parse_extraction_response(raw)
        assert "TCP" in result.concepts
        assert "BGP" in result.concepts
        assert "Anycast" in result.concepts

    def test_parse_empty_response(self):
        result = parse_extraction_response("")
        assert result.is_nothing is True
        assert result.concepts == []
