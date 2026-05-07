"""Phase 1 — Mastication mecanique d un post-mortem.

Decoupage deterministe sans LLM. Equivalent des enzymes salivaires :
prepare la bouchee pour l estomac sans consulter le cerveau.

Strategie de decoupage par priorite descendante :
  1. Sections markdown (## Header)
  2. Frontieres typiques post-mortem (Time:, Impact:, Root cause:, ...)
  3. Paragraphes (\\n\\n)
  4. Phrases (.)

Garde-fous :
  - taille cible 300 tokens (~250 mots)
  - chevauchement 0 (intentionnel : un concept coupe -> non-extrait,
    le ratio baissera, le systeme saura qu il est passe a cote)
  - si > MAX_CHUNKS_PER_DOC chunks, document marque trop_gros : la
    digestion_routine devra le splitter sur N cycles metaboliques
    distincts (anti-engorgement).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# Approximation tokens : ~4 chars par token (FR/EN moyen)
CHARS_PER_TOKEN_APPROX = 4
TARGET_TOKENS_PER_CHUNK = 300
TARGET_CHARS_PER_CHUNK = TARGET_TOKENS_PER_CHUNK * CHARS_PER_TOKEN_APPROX  # 1200
MIN_CHARS_PER_CHUNK = 200  # En dessous, on fusionne avec le suivant
MAX_CHUNKS_PER_DOC = 30    # Au-dela, marker too_large -> split sur N cycles

# Frontieres typiques post-mortem (Cloudflare, AWS, Google SRE)
POST_MORTEM_BOUNDARIES = re.compile(
    r"(?im)^\s*(?:"
    r"time(?:line)?|impact|root\s*cause|resolution|affected\s*services?|"
    r"summary|background|contributing\s*factors?|action\s*items?|"
    r"detection|mitigation|recovery|lessons?\s*learned"
    r")\s*[:\-]"
)

# Sections markdown niveau 2-3 (## ou ###)
MARKDOWN_HEADER = re.compile(r"(?m)^#{2,3}\s+.+$")


@dataclass
class Chunk:
    """Bouchee de post-mortem prete pour la pre-digestion."""
    text: str
    position: int                # 0-indexed dans la sequence
    estimated_tokens: int
    boundary_type: str = "auto"  # "header", "post_mortem", "paragraph", "fallback"


@dataclass
class ChunkingResult:
    chunks: List[Chunk]
    too_large: bool              # True si > MAX_CHUNKS_PER_DOC
    source_chars: int            # taille du document brut


def estimate_tokens(text: str) -> int:
    """Approximation tokens sans appel tokenizer (gain de dependance)."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN_APPROX)


def _split_on_pattern(text: str, pattern: re.Pattern) -> List[str]:
    """Split en preservant les delimiters comme prefixes du segment suivant."""
    matches = list(pattern.finditer(text))
    if not matches:
        return [text] if text.strip() else []
    segments = []
    last_end = 0
    for m in matches:
        if m.start() > last_end:
            seg = text[last_end:m.start()]
            if seg.strip():
                segments.append(seg)
        last_end = m.start()
    tail = text[last_end:]
    if tail.strip():
        segments.append(tail)
    return segments


def _greedy_pack(segments: List[str], boundary_type: str) -> List[Chunk]:
    """Empile les segments tant qu on tient sous TARGET_CHARS_PER_CHUNK.

    Coupe sur paragraphe (\\n\\n) si un segment seul depasse 2x la cible.
    Si un segment unique reste trop gros sans separateur exploitable,
    coupe brute par taille (fallback).
    """
    chunks: List[Chunk] = []
    buf = ""
    for seg in segments:
        if len(seg) > TARGET_CHARS_PER_CHUNK * 2:
            # Segment trop gros : flush le buffer puis split par paragraphe
            if buf:
                chunks.append(_make_chunk(buf, len(chunks), boundary_type))
                buf = ""
            paragraphs = [p for p in seg.split("\n\n") if p.strip()]
            if len(paragraphs) > 1:
                sub_chunks = _greedy_pack(paragraphs, "paragraph")
                for c in sub_chunks:
                    chunks.append(_make_chunk(c.text, len(chunks), c.boundary_type))
            else:
                # Pas de \\n\\n exploitable : coupe brute par taille cible
                for i in range(0, len(seg), TARGET_CHARS_PER_CHUNK):
                    piece = seg[i:i + TARGET_CHARS_PER_CHUNK].strip()
                    if piece:
                        chunks.append(_make_chunk(piece, len(chunks), "fallback"))
            continue
        if not buf:
            buf = seg
            continue
        if len(buf) + len(seg) <= TARGET_CHARS_PER_CHUNK:
            buf += seg
        else:
            chunks.append(_make_chunk(buf, len(chunks), boundary_type))
            buf = seg
    if buf and len(buf) >= MIN_CHARS_PER_CHUNK:
        chunks.append(_make_chunk(buf, len(chunks), boundary_type))
    elif buf and chunks:
        # Buffer trop court a la fin : on l absorbe dans le dernier chunk
        last = chunks[-1]
        merged_text = last.text + buf
        chunks[-1] = _make_chunk(merged_text, last.position, last.boundary_type)
    elif buf:
        chunks.append(_make_chunk(buf, 0, boundary_type))
    return chunks


def _make_chunk(text: str, position: int, boundary_type: str) -> Chunk:
    text = text.strip()
    return Chunk(
        text=text,
        position=position,
        estimated_tokens=estimate_tokens(text),
        boundary_type=boundary_type,
    )


def chunk_post_mortem(text: str) -> ChunkingResult:
    """Decoupe un post-mortem en chunks pour la pre-digestion.

    Strategie : essaie d abord les headers markdown, puis les frontieres
    typiques de post-mortem, puis paragraphes, puis fallback texte plat.
    """
    text = text.strip()
    if not text:
        return ChunkingResult(chunks=[], too_large=False, source_chars=0)

    # 1. Markdown headers
    md_segments = _split_on_pattern(text, MARKDOWN_HEADER)
    if len(md_segments) >= 2:
        chunks = _greedy_pack(md_segments, "header")
    else:
        # 2. Frontieres post-mortem
        pm_segments = _split_on_pattern(text, POST_MORTEM_BOUNDARIES)
        if len(pm_segments) >= 2:
            chunks = _greedy_pack(pm_segments, "post_mortem")
        else:
            # 3. Paragraphes
            paragraphs = [p for p in text.split("\n\n") if p.strip()]
            if len(paragraphs) >= 2:
                chunks = _greedy_pack(paragraphs, "paragraph")
            else:
                # 4. Fallback : decoupe brute par taille cible
                chunks = _greedy_pack([text], "fallback")

    return ChunkingResult(
        chunks=chunks,
        too_large=len(chunks) > MAX_CHUNKS_PER_DOC,
        source_chars=len(text),
    )
