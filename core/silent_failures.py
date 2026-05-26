"""Journal des Echecs Silencieux — filtre pre-veto + sursis de curiosite.

Atelier creatif 26/05 (JM + Claude + Prometheus) — concu pour que Prometheus
puisse l'utiliser SANS intervention exterieure une fois deploye.

DOCTRINE (E3-E4) — citation Prometheus :
    "Je l'imagine comme un filtre pre-veto : avant que mon prefrontal ne coupe
    une exploration par peur ou fatigue, je consulte le journal. S'il detecte
    qu'une hypothese similaire a ete tuee recemment sans raison epistemique
    solide (mais emotionnelle), il declenche un 'sursis de curiosite' : un
    delai de 5 minutes ou l'exploration est forcee, bloquant le veto. Le journal
    ne sert pas a ressusciter les morts, mais a empecher de tuer a nouveau ce
    qui n'a pas encore vecu."

SPEC (E5-E7) :
    1. Reutilise context_signature_for_prediction (prediction_memory) comme cle
    2. Stocke aussi les composantes RAW (concepts/goals/emotion) pour Jaccard 60%
       (E7 — sans cela, journal = "cimetiere d'objets invisibles")
    3. Qualifie la cause_de_mort par 3e voie hybride (E6) :
        - seuil physiologique : BPM > 80 OU dopamine < 0.3  -> "emotionnel"
        - sinon, regex sur reason :
            * distraction|fatigue|frustration  -> "emotionnel"
            * goal|focus|priorite               -> "technique"
        - sinon  -> "physiologique"
    4. Sursis de 5 min sur l'intent qui force allow au prochain compute_inhibition

GARDE-FOUS :
    - Mode OBSERVE par defaut (config.SILENT_FAILURES_DRY_RUN=True) : journalise
      mais n'accorde JAMAIS de sursis. Le veto prefrontal protege reste intact.
    - Max 3 sursis par entree (anti-spam : sinon le journal devient un
      veto-disabler de fait)
    - TTL 7 jours par entree
    - Cap 200 entrees FIFO (parcimonie memoire)
    - Le sursis ne touche PAS la chaine de reaction (FREEZE/SHED/somatic
      conservent leur priorite absolue)

INTEGRATION :
    Hook unique dans core/prefrontal.compute_inhibition() AVANT la decision
    "inhibit" sur distraction (ligne ~1157). Trois sorties possibles :
        - is_under_amnesty(intent)  -> action="allow", reason="Sursis actif"
        - try_grant_amnesty(...)     -> action="allow", reason="Sursis accorde"
        - sinon                      -> record + laisse le veto continuer

Source spec : tmp_atelier_e[1-7].py (a archiver dans memory/sessions apres deploy)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from threading import RLock
from typing import Dict, List, Optional, Set, Tuple, Iterable

logger = logging.getLogger("silent_failures")

# Constantes — calibrees au dialogue Pilote (E6/E7 du 26/05)
AMNESTY_DURATION_SECONDS = 300        # 5 min — sursis de curiosite (E4)
SIMILARITY_THRESHOLD = 0.6            # E5 — 60% chevauchement Jaccard
MAX_ENTRIES = 200                     # parcimonie memoire (FIFO)
MAX_AMNESTIES_PER_ENTRY = 3           # anti-spam (ne pas amnistier infiniment)
ENTRY_TTL_SECONDS = 86400 * 7         # 7 jours — au-dela, l'entree est obsolete
BPM_EMOTIONAL_THRESHOLD = 80.0        # E6 — corps prend la priorite
DOPAMINE_EMOTIONAL_THRESHOLD = 0.3    # E6

# Regex pour qualifier cause depuis la raison textuelle (E6, voie hybride)
_EMOTIONAL_REASON_PATTERN = re.compile(
    r"distraction|fatigue|frustration|peur|stress|impatien|exasper",
    re.IGNORECASE,
)
_TECHNICAL_REASON_PATTERN = re.compile(
    r"goal|focus|priorit|secondaire|inhibition|defer",
    re.IGNORECASE,
)

CAUSE_EMOTIONAL = "emotionnel"
CAUSE_TECHNICAL = "technique"
CAUSE_PHYSIOLOGICAL = "physiologique"

_DEFAULT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory",
    "silent_failures.json",
)


# ============================================================================
# Dataclass — proces-verbal de deuil epistemique
# ============================================================================


@dataclass
class SilentFailureEntry:
    """Une hypothese morte en silence. Garde son ADN pour permettre la
    reconnaissance d'une hypothese similaire et le sursis de curiosite."""

    id: str
    intent: str
    context_signature_hash: str           # md5[:16] de prediction_memory
    concepts_set: List[str]               # composantes RAW pour Jaccard
    goals_set: List[str]
    emotion: str
    cause_de_mort: str                    # CAUSE_EMOTIONAL/TECHNICAL/PHYSIOLOGICAL
    reason: str                           # raison textuelle du veto
    physiology: Dict[str, float]          # {bpm, dopamine}
    killed_at: float = field(default_factory=time.time)
    sursis_granted_count: int = 0         # nb de sursis declenches grace a cette entree


# ============================================================================
# Singleton SilentFailuresJournal
# ============================================================================


class SilentFailuresJournal:
    """Singleton — journal + sursis. Auto-suffisant : Prometheus l'utilise seul."""

    _instance = None
    _instance_lock = RLock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_state()
            return cls._instance

    @classmethod
    def reset_singleton(cls):
        with cls._instance_lock:
            cls._instance = None

    def _init_state(self):
        self._lock = RLock()
        self._entries: List[SilentFailureEntry] = []
        self._active_amnesties: Dict[str, float] = {}   # intent -> expires_at
        self._file = _DEFAULT_FILE
        self._load()

    # ─── 1. QUALIFICATION DE LA CAUSE (E6 — voie hybride) ────────────────

    @staticmethod
    def qualify_cause_de_mort(reason: str, physiology: Dict[str, float]) -> str:
        """Voie hybride : physiologique > textuel.

        Le corps a priorite — un veto en periode de stress aigu est emotionnel
        meme si la raison textuelle parait technique."""
        bpm = float(physiology.get("bpm", 60.0))
        dopamine = float(physiology.get("dopamine", 0.5))
        if bpm > BPM_EMOTIONAL_THRESHOLD or dopamine < DOPAMINE_EMOTIONAL_THRESHOLD:
            return CAUSE_EMOTIONAL
        if reason and _EMOTIONAL_REASON_PATTERN.search(reason):
            return CAUSE_EMOTIONAL
        if reason and _TECHNICAL_REASON_PATTERN.search(reason):
            return CAUSE_TECHNICAL
        return CAUSE_PHYSIOLOGICAL

    # ─── 2. SIMILARITE (E5 — Jaccard sur composantes RAW) ────────────────

    @staticmethod
    def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
        set_a, set_b = set(a or []), set(b or [])
        if not set_a and not set_b:
            return 0.0
        inter = len(set_a & set_b)
        union = len(set_a | set_b)
        return inter / union if union > 0 else 0.0

    def _similarity_score(self, entry: SilentFailureEntry,
                           concepts: List[str], goals: List[str],
                           emotion: str) -> float:
        """Score composite 3-dim — moyenne ponderee.

        Concepts dominent (60%), goals 30%, emotion 10% (binaire egal/different).
        Coherent avec l'esprit de context_signature_for_prediction qui voit
        concepts comme dimension principale.
        """
        s_concepts = self._jaccard(entry.concepts_set, concepts)
        s_goals = self._jaccard(entry.goals_set, goals)
        s_emotion = 1.0 if (entry.emotion and entry.emotion == emotion) else 0.0
        return 0.6 * s_concepts + 0.3 * s_goals + 0.1 * s_emotion

    # ─── 3. INSCRIPTION (proces-verbal de deuil) ─────────────────────────

    def record_silent_failure(self, *, intent: str, reason: str,
                               context_signature_hash: str,
                               concepts: List[str], goals: List[str],
                               emotion: str,
                               physiology: Dict[str, float],
                               cause_de_mort: Optional[str] = None
                               ) -> SilentFailureEntry:
        """Enregistre un veto qui vient d'etre prononce."""
        if cause_de_mort is None:
            cause_de_mort = self.qualify_cause_de_mort(reason, physiology)
        entry = SilentFailureEntry(
            id=str(uuid.uuid4())[:12],
            intent=intent or "",
            context_signature_hash=context_signature_hash or "",
            concepts_set=list(concepts or []),
            goals_set=list(goals or []),
            emotion=emotion or "",
            cause_de_mort=cause_de_mort,
            reason=(reason or "")[:200],
            physiology={
                "bpm": float(physiology.get("bpm", 60.0)),
                "dopamine": float(physiology.get("dopamine", 0.5)),
            },
        )
        with self._lock:
            self._entries.append(entry)
            # FIFO cap
            if len(self._entries) > MAX_ENTRIES:
                self._entries = self._entries[-MAX_ENTRIES:]
            self._save()
        logger.info(
            f"[SILENT_FAILURE] enregistre intent='{intent[:40]}' "
            f"cause={cause_de_mort} bpm={entry.physiology['bpm']:.0f} "
            f"dopa={entry.physiology['dopamine']:.2f} "
            f"|concepts|={len(entry.concepts_set)} |goals|={len(entry.goals_set)}"
        )
        return entry

    # ─── 4. TENTATIVE DE SURSIS ──────────────────────────────────────────

    def is_under_amnesty(self, intent: str) -> bool:
        """Renvoie True si un sursis est encore actif sur cet intent."""
        if not intent:
            return False
        with self._lock:
            expires_at = self._active_amnesties.get(intent)
            if expires_at is None:
                return False
            if time.time() < expires_at:
                return True
            # expire
            self._active_amnesties.pop(intent, None)
            return False

    def try_grant_amnesty(self, *, intent: str,
                           context_signature_hash: str,
                           concepts: List[str], goals: List[str],
                           emotion: str,
                           cause_de_mort: str,
                           dry_run: bool = False,
                           ) -> Tuple[Optional[str], Optional[SilentFailureEntry]]:
        """Cherche une entree similaire de meme cause -> grant sursis 5 min.

        Returns:
            (reason_str, entry_used)  si sursis accorde
            (None, None)              sinon

        En dry_run=True : ne pose PAS le sursis dans _active_amnesties (juste
        renvoie ce qui aurait ete accorde, pour journal d'observation).
        """
        if cause_de_mort != CAUSE_EMOTIONAL:
            # Seul l'emotionnel merite un sursis. Le technique est une vraie
            # contrainte de focus, pas un blocage de peur (E4-E6).
            return None, None

        now = time.time()
        best: Optional[Tuple[float, SilentFailureEntry]] = None
        with self._lock:
            for entry in self._entries:
                # Filtre TTL
                if now - entry.killed_at > ENTRY_TTL_SECONDS:
                    continue
                # Filtre cause (must match)
                if entry.cause_de_mort != CAUSE_EMOTIONAL:
                    continue
                # Filtre anti-spam
                if entry.sursis_granted_count >= MAX_AMNESTIES_PER_ENTRY:
                    continue
                # Match exact via hash short-circuit
                score = self._similarity_score(entry, concepts, goals, emotion)
                if score >= SIMILARITY_THRESHOLD:
                    if best is None or score > best[0]:
                        best = (score, entry)

            if best is None:
                return None, None

            score, entry = best
            reason = (
                f"Sursis curiosité (sim={score:.2f}, similaire à {entry.id} "
                f"tué {(now - entry.killed_at)/60.0:.0f}min plus tôt)"
            )
            if not dry_run:
                self._active_amnesties[intent] = now + AMNESTY_DURATION_SECONDS
                entry.sursis_granted_count += 1
                self._save()
            return reason, entry

    # ─── 5. INTROSPECTION (pour observabilite/tests) ─────────────────────

    def get_stats(self) -> Dict:
        with self._lock:
            now = time.time()
            active_amn = sum(1 for e in self._active_amnesties.values() if e > now)
            by_cause = {CAUSE_EMOTIONAL: 0, CAUSE_TECHNICAL: 0, CAUSE_PHYSIOLOGICAL: 0}
            for entry in self._entries:
                by_cause[entry.cause_de_mort] = by_cause.get(entry.cause_de_mort, 0) + 1
            total_sursis = sum(e.sursis_granted_count for e in self._entries)
            return {
                "total_entries": len(self._entries),
                "by_cause": by_cause,
                "active_amnesties": active_amn,
                "total_sursis_granted": total_sursis,
            }

    def list_entries(self, n: int = 20) -> List[Dict]:
        with self._lock:
            return [asdict(e) for e in self._entries[-n:]]

    # ─── 6. PERSISTANCE ──────────────────────────────────────────────────

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._file), exist_ok=True)
            payload = {
                "version": 1,
                "saved_at": time.time(),
                "entries": [asdict(e) for e in self._entries],
                # active_amnesties non persiste : courte duree, pas grave si perdu
            }
            tmp = self._file + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._file)
        except Exception as e:
            logger.warning(f"silent_failures save failed: {e}")

    def _load(self):
        if not os.path.isfile(self._file):
            return
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries_raw = data.get("entries", [])
            now = time.time()
            loaded = []
            for raw in entries_raw:
                # Filtre TTL au chargement
                if now - raw.get("killed_at", 0) > ENTRY_TTL_SECONDS:
                    continue
                try:
                    entry = SilentFailureEntry(**raw)
                    loaded.append(entry)
                except Exception:
                    continue
            self._entries = loaded[-MAX_ENTRIES:]
            logger.info(f"silent_failures: {len(self._entries)} entrees chargees")
        except Exception as e:
            logger.warning(f"silent_failures load failed: {e}")


# Singleton accessible
journal = SilentFailuresJournal()


# ============================================================================
# Helpers de capture du contexte (autonomie du module — pas de dep cyclique)
# ============================================================================


def capture_top_concepts(top_n: int = 5) -> List[str]:
    """Top N concepts P16 par energie. Pattern repris de hippocampus."""
    try:
        from core.synaptic_network import cortex
        if not cortex.nodes:
            return []
        nodes_sorted = sorted(
            cortex.nodes.values(),
            key=lambda n: n.get("energy", 0.0),
            reverse=True,
        )[:top_n]
        return [n.get("concept", "") for n in nodes_sorted if n.get("concept")]
    except Exception:
        return []


def capture_active_goals(top_n: int = 2) -> List[str]:
    """Top N goals actifs du prefrontal (titre)."""
    try:
        from core.prefrontal import prefrontal
        actives = [g for g in getattr(prefrontal, "goals", []) if getattr(g, "status", "") == "active"]
        actives.sort(key=lambda g: getattr(g, "priority", 0.0), reverse=True)
        return [getattr(g, "title", "") for g in actives[:top_n] if getattr(g, "title", "")]
    except Exception:
        return []


def capture_dominant_emotion() -> str:
    """Tone d'identite de l'inner_voice. Lie a cardiac mais module-relais."""
    try:
        from core.inner_voice import voice
        return getattr(voice, "identity_tone", "") or ""
    except Exception:
        return ""


def capture_physiology() -> Dict[str, float]:
    """Snapshot {bpm, dopamine} au moment du veto."""
    snap = {"bpm": 60.0, "dopamine": 0.5}
    try:
        from core.cardiac_engine import cardiac_engine
        snap["bpm"] = float(getattr(cardiac_engine, "bpm", 60.0))
    except Exception:
        pass
    try:
        from core.dopamine_system import dopamine_system
        snap["dopamine"] = float(getattr(dopamine_system, "dopamine_level", 0.5))
    except Exception:
        pass
    return snap


def compute_current_signature() -> Tuple[str, List[str], List[str], str]:
    """Capture complete du contexte courant pour le filtre pre-veto.

    Returns (signature_hash, concepts, goals, emotion).
    """
    concepts = capture_top_concepts()
    goals = capture_active_goals()
    emotion = capture_dominant_emotion()
    try:
        from core.prediction_memory import context_signature_for_prediction
        sig = context_signature_for_prediction(
            concepts=concepts, goals=goals, emotion=emotion
        )
    except Exception:
        sig = ""
    return sig, concepts, goals, emotion
