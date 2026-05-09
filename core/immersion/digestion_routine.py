"""Phase 4 — Tir Hebbien + integration AutonomyEngine.

Pipeline complet d ingestion d un document du flux brut :

  Phase 1 — chunker (deterministe, zero LLM)
  Phase 2 — extractor.extract_concepts_dialectical (LLM Avocat + Procureur)
  Phase 3 — compute_compression (set-intersection, zero LLM)
  Phase 3.5 — assimilate_unknowns (Friston : assimilation des inconnus)
  Phase 4 — tir Hebbien sur routine:immersion_domain -> pulsion:maitrise_epistemic

Polymorphisme enzymatique par origine spatiale :
  - data/raw_flux/post_mortems/   -> INFRASTRUCTURE_POST_MORTEM
  - USER_DROPZONE/limite_pauseai/ -> ARCHITECTURAL_THINKER

Trois portes de declenchement (Porte A/B/C — conception 07/05) :
  A. Survie  : threat_level OK, stabilite hors crise, pas de pandemie
  B. Capacite: synaptic_congestion <0.7, hippocampe pas sature, dream <24h
  C. Appetit : dopamine basse OU maitrise_epistemic en carence

Cycle de vie filesystem (eviter le Trou Noir Digestif) :
  FRESH (raw_flux/post_mortems/x.txt)
    -> [echec/preempt] WOUNDED (raw_flux/post_mortems/x.preempted.N.txt)
    -> [3e preempt] DEAD_LETTER (digested/x.poisoned.txt)
    -> [succes] DIGESTED (digested/x.{verdict}.txt)
    -> [RIEN] IGNORED (digested/x.empty.txt)
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from core.immersion.chunker import chunk_post_mortem
from core.immersion.compression import (
    CompressionResult,
    assimilate_unknowns,
    compute_compression,
)
from core.immersion.extractor import (
    ARCHITECTURAL_THINKER,
    INFRASTRUCTURE_POST_MORTEM,
    ExtractionProfile,
    ExtractionResult,
    extract_concepts_dialectical,
    pre_process_oral,
)

logger = logging.getLogger("ImmersionDigestion")


# ============================================================
# Constantes routine
# ============================================================

IMMERSION_INTENT = "IMMERSION_DOMAIN"
IMMERSION_SOURCE_CONCEPT = "routine:immersion_domain"
IMMERSION_TARGET_CONCEPT = "pulsion:maitrise_epistemic"

# Repertoires de flux (relatifs a la racine projet)
RAW_FLUX_POST_MORTEMS = "data/raw_flux/post_mortems"
RAW_FLUX_LIMITE_PAUSEAI = "USER_DROPZONE/limite_pauseai"
DIGESTED_DIR = "data/raw_flux/digested"

# Cooldowns metaboliques (a calibrer apres premiers cycles in-vivo)
IMMERSION_MIN_INTERVAL_SECONDS = 1800   # 30 min entre 2 ingestions
IMMERSION_MAX_PER_DAY = 8

# Filesystem state machine
PREEMPT_RETRY_COOLDOWN = 1800           # 30 min anti-boucle preempt
PREEMPT_DEAD_LETTER_THRESHOLD = 3       # 3e preempt -> poisoned

# Famine pour Porte C
EPISTEMIC_FAMINE_HOURS = 24.0           # carence epistemique critique


# ============================================================
# Dispatcher spatial (polymorphisme enzymatique)
# ============================================================


def select_profile(doc_path: Path) -> ExtractionProfile:
    """Selectionne l enzyme selon le canal d arrivee du document.

    Pas de fallback silencieux : si un fichier arrive dans une zone
    sans profil enregistre, on leve. Mieux vaut un crash visible
    qu une enzyme-mismatch en aval.
    """
    parts = doc_path.parts
    if "post_mortems" in parts:
        return INFRASTRUCTURE_POST_MORTEM
    if "limite_pauseai" in parts:
        return ARCHITECTURAL_THINKER
    raise ValueError(
        f"Aucun profil d extraction pour {doc_path}. "
        f"Ajoute le mapping path -> ExtractionProfile dans select_profile()."
    )


# ============================================================
# Filesystem state machine (cycle de vie des documents)
# ============================================================


_PREEMPTED_RE = re.compile(r"\.preempted\.(\d+)\.txt$", re.IGNORECASE)


def _extract_retry_count(name: str) -> int:
    """Extrait le compteur de retry du suffixe `.preempted.N.txt`."""
    m = _PREEMPTED_RE.search(name)
    return int(m.group(1)) if m else 0


def _strip_preempted_suffix(name: str) -> str:
    """Retourne le nom de base sans `.preempted.N`."""
    return _PREEMPTED_RE.sub(".txt", name)


def pick_next_document(raw_flux_dir: Path) -> Optional[Path]:
    """Selectionne le prochain document candidat a la digestion.

    Tri deterministe : (priority, name) — ordre ALPHABETIQUE STRICT
    sur le nom dans chaque categorie de priorite.

    Priorite :
      -1 : FRESH (*.txt sans .preempted) — passe en premier
      0+ : WOUNDED (*.preempted.N.txt) — passe ensuite, ordonne par
           retry_count croissant (le moins traumatise d abord) puis nom

    Pourquoi pas mtime : quand Jean-Michel depose un dossier de
    chapitres en lot (ex: 10 chapitres copies en 1 minute), tous les
    fichiers ont des mtime identiques ou quasi. Un tri par mtime ne
    discrimine pas, et l ordre final depend de l implementation
    filesystem de glob() — non garantie en Python. Le risque est
    qu un essai philosophique soit digere a l envers (ch.8 avant ch.1),
    construisant le synaptic_network sur de mauvaises fondations
    semantiques.

    Le tri alphabetique sur le nom respecte les conventions usuelles
    de chapitrage (01_intro.txt < 02_xxx.txt < ... < 10_yyy.txt avec
    zero-padding sur 2 chiffres).

    Exclut :
      - .preempted.N en cooldown actif (anti-boucle preempt)
      - .gitkeep et fichiers caches
    """
    if not raw_flux_dir.exists():
        return None
    now = time.time()
    candidates: List[tuple] = []  # (priority, name, path)
    for f in raw_flux_dir.glob("*.txt"):
        if f.name.startswith("."):
            continue
        if ".preempted." in f.name:
            if now - f.stat().st_mtime < PREEMPT_RETRY_COOLDOWN:
                continue  # encore convalescent
            retry = _extract_retry_count(f.name)
            candidates.append((retry, f.name, f))
        else:
            candidates.append((-1, f.name, f))  # FRESH prioritaire
    if not candidates:
        return None
    # Tri ALPHABETIQUE STRICT : (priority, name). Garantit que les
    # documents avec prefixes numeriques zero-padded (01_, 02_, ...)
    # sont digeres dans l ordre chronologique de l essai.
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][2]


def _archive_to_digested(
    doc_path: Path,
    digested_dir: Path,
    suffix: str,
) -> Path:
    """Move un document vers `digested/<base>.<suffix>.txt`."""
    digested_dir.mkdir(parents=True, exist_ok=True)
    base = _strip_preempted_suffix(doc_path.name).replace(".txt", "")
    target = digested_dir / f"{base}.{suffix}.txt"
    doc_path.rename(target)
    return target


def _handle_preemption(
    doc_path: Path,
    retry_count: int,
    digested_dir: Path,
) -> Path:
    """Transition d etat WOUNDED ou DEAD_LETTER. PAS d archive sauf seuil."""
    new_retry = retry_count + 1
    if new_retry >= PREEMPT_DEAD_LETTER_THRESHOLD:
        target = _archive_to_digested(doc_path, digested_dir, "poisoned")
        logger.warning(
            f"IMMERSION: doc {doc_path.name} dead-letter "
            f"apres {new_retry} preemptions"
        )
        return target
    base = _strip_preempted_suffix(doc_path.name).replace(".txt", "")
    target = doc_path.parent / f"{base}.preempted.{new_retry}.txt"
    doc_path.rename(target)
    logger.info(
        f"IMMERSION: doc {doc_path.name} preempte "
        f"(retry {new_retry}/{PREEMPT_DEAD_LETTER_THRESHOLD})"
    )
    return target


# ============================================================
# Trois portes de declenchement (Porte A / B / C)
# ============================================================


@dataclass
class TriggerVerdict:
    """Resultat de should_trigger_immersion : decision + raison + score."""
    triggered: bool
    score: float = 0.0
    blocked_at: Optional[str] = None  # "porte_a" | "porte_b" | None
    reason: str = ""


def should_trigger_immersion(autonomy_engine) -> TriggerVerdict:
    """Verifie les 3 portes en cascade. Retourne TriggerVerdict.

    Les portes A et B sont eliminatoires (kill switches reptilien et
    metabolique). La porte C produit un score positif tail.

    Args:
        autonomy_engine : instance de AutonomyEngine pour lire les
            signaux internes (cardiac, desire, dopamine, hippocampus,
            synaptic, school_schedule).

    Note d implementation : cette fonction lit defensivement chaque
    signal (try/except + getattr) pour ne pas crash le scoring si
    un organe est temporairement indisponible. En cas d erreur de
    lecture, la garde par defaut est conservative (triggered=False).
    """
    project_root = Path(__file__).resolve().parent.parent.parent

    # Porte A — Survie (any of these true => block)
    try:
        from core.reptilian_core import reptilian
        threat = float(getattr(reptilian, "threat_level", 0))
        if threat >= 5.0:
            return TriggerVerdict(False, blocked_at="porte_a",
                                   reason=f"threat_level={threat:.1f}")
    except Exception:
        pass
    try:
        from core.desire_engine import desires
        stab = desires.drives.get("STABILITE")
        if stab and stab.deprivation > 80:
            return TriggerVerdict(
                False, blocked_at="porte_a",
                reason=f"STABILITE depriv={stab.deprivation:.1f}",
            )
    except Exception:
        pass
    try:
        from core.neural_tissue import tissue
        if getattr(tissue, "pandemic_active", False):
            return TriggerVerdict(False, blocked_at="porte_a",
                                   reason="pandemic_active")
    except Exception:
        pass

    # Porte B — Capacite metabolique
    try:
        from core.synaptic_network import SynapticNetwork
        syn = SynapticNetwork()
        last_dream = getattr(syn, "last_dream_time", 0)
        if last_dream > 0 and (time.time() - last_dream) > 86400:
            return TriggerVerdict(
                False, blocked_at="porte_b",
                reason=f"dream stale ({(time.time()-last_dream)/3600:.0f}h)",
            )
    except Exception:
        pass
    try:
        from core.hippocampus import hippocampus
        pending = len(getattr(hippocampus, "pending_episodes", []))
        if pending > 50:
            return TriggerVerdict(False, blocked_at="porte_b",
                                   reason=f"hippocampus pending={pending}")
    except Exception:
        pass

    # File de chasse non vide ?
    has_food = False
    for sub in (RAW_FLUX_POST_MORTEMS, RAW_FLUX_LIMITE_PAUSEAI):
        d = project_root / sub
        if d.exists() and pick_next_document(d) is not None:
            has_food = True
            break
    if not has_food:
        return TriggerVerdict(False, blocked_at="porte_b", reason="no_food_in_dropzone")

    # Cooldown metabolique inter-cycles
    try:
        last_immersion = float(getattr(autonomy_engine, "_last_immersion_time", 0.0))
        if last_immersion > 0 and (time.time() - last_immersion) < IMMERSION_MIN_INTERVAL_SECONDS:
            return TriggerVerdict(
                False, blocked_at="porte_b",
                reason=f"cooldown ({(time.time()-last_immersion):.0f}s)",
            )
    except Exception:
        pass
    try:
        daily = int(getattr(autonomy_engine, "_immersion_daily_count", 0))
        if daily >= IMMERSION_MAX_PER_DAY:
            return TriggerVerdict(
                False, blocked_at="porte_b",
                reason=f"daily_max ({daily}/{IMMERSION_MAX_PER_DAY})",
            )
    except Exception:
        pass

    # Porte C — Appetit (scoring tail-positif)
    score = 0.0
    try:
        from core.dopamine_system import dopamine
        dopa_level = float(getattr(dopamine, "dopamine_level", 0.5))
        score += 0.40 * max(0.0, 0.5 - dopa_level) * 2.0  # normalise [0..1]
    except Exception:
        pass
    try:
        from core.synaptic_network import SynapticNetwork
        syn = SynapticNetwork()
        history = getattr(syn, "_epistemic_history", {})
        if history:
            all_scores = [s for slot_h in history.values() for s in slot_h]
            if all_scores:
                avg = sum(all_scores) / len(all_scores)
                # Plus la moyenne est basse, plus la carence est haute.
                score += 0.35 * max(0.0, (7.0 - avg) / 7.0)
        last_closure = list(getattr(syn, "_epistemic_last_closure", {}).values())
        if last_closure:
            # FIX 2026-05-09 : MIN au lieu de MAX. La famine doit refleter
            # le slot LE PLUS affame, pas le plus repu. Coherence avec le
            # fix similaire dans autonomy_engine.py (Veto Executif + couche
            # 26ter). Sans ce fix, la Porte C se fermait des qu un seul
            # slot avait recemment ferme (ex: CREATION 4h), masquant la
            # famine reelle de BULLETIN/RESEARCH/CODE_REVIEW (200-460h).
            hours_since = (time.time() - min(last_closure)) / 3600.0
            if hours_since > EPISTEMIC_FAMINE_HOURS:
                score += 0.15  # bonus famine
    except Exception:
        pass
    try:
        last_immersion = float(getattr(autonomy_engine, "_last_immersion_time", 0.0))
        if last_immersion == 0.0:
            score += 0.10  # jamais d immersion -> petit boost initial
        else:
            stale_h = (time.time() - last_immersion) / 3600.0
            score += 0.10 * min(1.0, stale_h / 6.0)
    except Exception:
        pass

    return TriggerVerdict(triggered=score > 0.15, score=round(score, 3))


# ============================================================
# Orchestration principale (Phase 1 -> 4)
# ============================================================


@dataclass
class DigestionReport:
    """Resume d une ingestion (journal + audit)."""
    document_path: str
    n_chunks: int
    n_concepts_extracted: int
    n_recognized: int
    n_unknown_assimilated: int
    ratio: Optional[float]
    verdict: str               # ignored / pain / micro_consolidation / full_consolidation
    delta_synaptic: float
    timestamp: float
    failed_at: Optional[str] = None


def collect_known_concepts(synaptic_network) -> List[str]:
    """Liste des concepts du synaptic_network (Phase 3 lookup)."""
    nodes = getattr(synaptic_network, "nodes", {}) or {}
    out: List[str] = []
    for node in nodes.values():
        if isinstance(node, dict):
            c = node.get("concept")
            if c:
                out.append(c)
    return out


async def run_immersion_cycle(
    autonomy_engine,
    preempt_event: Optional[asyncio.Event] = None,
) -> Optional[DigestionReport]:
    """Cycle complet d ingestion d un document.

    Sequence : pick -> chunk -> extract (par chunk, dialectique) ->
    compress -> assimilate unknowns -> hebbian tir -> archive.

    Toute interruption (timeout LLM, preempt event, exception) renomme
    le doc en .preempted.N.txt et retourne None. Aucun document ne
    quitte la zone de chasse sans verdict consolide.
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    digested_dir = project_root / DIGESTED_DIR

    # Picker : chercher dans les deux zones, preferer post_mortems
    doc_path: Optional[Path] = None
    for sub in (RAW_FLUX_POST_MORTEMS, RAW_FLUX_LIMITE_PAUSEAI):
        d = project_root / sub
        candidate = pick_next_document(d)
        if candidate is not None:
            doc_path = candidate
            break
    if doc_path is None:
        return None

    profile = select_profile(doc_path)
    retry_count = _extract_retry_count(doc_path.name)

    try:
        # Phase 0.5 + Phase 1
        text_raw = doc_path.read_text(encoding="utf-8")
        text_clean = pre_process_oral(text_raw)
        chunking = chunk_post_mortem(text_clean)
        if chunking.too_large:
            logger.info(
                f"IMMERSION: {doc_path.name} too_large "
                f"({len(chunking.chunks)} chunks) — defere au prochain cycle"
            )
            return None

        # Phase 2 — extraction par chunk (preemptible)
        extracted_all: List[str] = []
        preempted = False
        for chunk in chunking.chunks:
            if preempt_event is not None and preempt_event.is_set():
                preempted = True
                break
            result: ExtractionResult = await extract_concepts_dialectical(
                chunk, profile, preempt_event=preempt_event,
            )
            if result.failed_at == "preempted":
                preempted = True
                break
            extracted_all.extend(result.final_concepts)

        if preempted:
            _handle_preemption(doc_path, retry_count, digested_dir)
            return None

        # Phase 3 — compression
        try:
            from core.synaptic_network import SynapticNetwork
            syn = SynapticNetwork()
            known = collect_known_concepts(syn)
        except Exception:
            syn = None
            known = []
        compression: CompressionResult = compute_compression(extracted_all, known)

        if not compression.is_meaningful:
            _archive_to_digested(doc_path, digested_dir, "empty")
            return DigestionReport(
                document_path=str(doc_path),
                n_chunks=len(chunking.chunks),
                n_concepts_extracted=0,
                n_recognized=0,
                n_unknown_assimilated=0,
                ratio=None,
                verdict="ignored",
                delta_synaptic=0.0,
                timestamp=time.time(),
            )

        # Phase 3.5 — assimilation des inconnus AVANT le tir Hebbien
        # (pour que les inconnus existent au moment ou le tir se calcule)
        n_assimilated = 0
        if syn is not None:
            n_assimilated = assimilate_unknowns(compression.unknown, syn)

        # Phase 4 — tir Hebbien selon verdict
        delta = _apply_hebbian_immersion_tir(syn, compression)

        # Archive selon verdict
        _archive_to_digested(doc_path, digested_dir, compression.verdict)

        # Mise a jour des compteurs sur l autonomy_engine
        try:
            autonomy_engine._last_immersion_time = time.time()
            autonomy_engine._immersion_daily_count = (
                int(getattr(autonomy_engine, "_immersion_daily_count", 0)) + 1
            )
        except Exception:
            pass

        return DigestionReport(
            document_path=str(doc_path),
            n_chunks=len(chunking.chunks),
            n_concepts_extracted=compression.total_extracted,
            n_recognized=len(compression.recognized),
            n_unknown_assimilated=n_assimilated,
            ratio=compression.ratio,
            verdict=compression.verdict,
            delta_synaptic=delta,
            timestamp=time.time(),
        )

    except asyncio.CancelledError:
        # Annulation externe (shutdown autonomy)
        _handle_preemption(doc_path, retry_count, digested_dir)
        raise
    except Exception as e:
        logger.warning(f"IMMERSION: erreur cycle sur {doc_path.name}: {e}")
        _handle_preemption(doc_path, retry_count, digested_dir)
        return None


def _apply_hebbian_immersion_tir(synaptic_network, compression: CompressionResult) -> float:
    """Phase 4 — tir Hebbien sur routine:immersion_domain -> pulsion:maitrise_epistemic.

    Magnitude proportionnelle au verdict :
      - "pain"               : extinction legere (-0.030)
      - "micro_consolidation": delta proportionnel au ratio (0.05 a 0.10)
      - "full_consolidation" : delta plein (0.15)

    Reutilise _apply_causal_delta pour respecter la mecanique synaptic
    standard (soft cap, plancher, etc.).
    """
    if synaptic_network is None or compression.ratio is None:
        return 0.0
    try:
        from core.synaptic_network import _make_node_id
        src_concept = "routine:immersion_domain"
        tgt_concept = "pulsion:maitrise_epistemic"
        synaptic_network.ensure_node(src_concept, "event", 0.5, ["immersion"])
        synaptic_network.ensure_node(tgt_concept, "desire", 0.4, ["epistemic"])
        src_nid = _make_node_id(src_concept)
        tgt_nid = _make_node_id(tgt_concept)
        if compression.verdict == "pain":
            delta = -0.030
        elif compression.verdict == "micro_consolidation":
            delta = 0.05 + 0.05 * compression.ratio
        elif compression.verdict == "full_consolidation":
            delta = 0.15
        else:
            return 0.0
        synaptic_network._apply_causal_delta(
            src_nid, tgt_nid, delta,
            context=f"immersion:{compression.verdict} ratio={compression.ratio:.2f}",
        )
        return delta
    except Exception as e:
        logger.warning(f"IMMERSION: tir Hebbien echoue: {e}")
        return 0.0
