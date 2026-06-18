# -*- coding: utf-8 -*-
"""Irrigation neurochimique du rappel mémoriel — RAG spatialisé, Incision A (18/06).

Topologie cognitive appliquée : la mémoire cesse d'être une matrice plate où seule
la similarité cosinus décide du réveil. On couple l'index sémantique à une carte
d'organes DISCRÈTE (zones anatomiques) et on module le score de rappel par l'état
métabolique courant — le « facteur d'irrigation » (perfusion du sang computationnel).

    Score_final = Similarité_cosinus × Perfusion(Zone, État_chimique)

DOCTRINE (scellée 18/06) :
  - Zone = ORIGINE (quel organe a émis la trace). Enum discret, PAS de (x,y,z) ici
    — les coordonnées 3D sont reléguées à la viz frontend, hors de l'arithmétique.
  - coping_affinity = FONCTION (structure de secours). Orthogonal à la zone : sous
    stress on sur-irrigue le COPING (« comment je m'en suis sorti »), JAMAIS la
    douleur brute (anti-rumination). Flag EXPLICITE à terme ; en Phase 1 on amorce
    un PROXY temporaire : tier_status==PREMIUM = bouée de sauvetage sémantique.
  - Disjoncteur métabolique : plancher de perfusion 0.05 — la boussole cosinus
    n'est JAMAIS totalement aveuglée par l'émotion instantanée.
  - Lissage EMA anti-thrashing : la perfusion ne se réoriente pas en 30 ms quand
    d(Threat)/dt est bruité (un débit sanguin a de l'inertie).

KILL-SWITCH (double commutateur, défaut SÛR) :
  - IRRIGATION_SHADOW=1 (défaut) : calcule + logge le re-ranking, retour INCHANGÉ.
  - IRRIGATION_ACTIVE=0 (défaut) : n'applique JAMAIS le re-ranking au rappel servi.
  Phase 1 = SHADOW pur. On observe les deltas dans memory/irrigation_shadow.jsonl
  avant d'oser toucher au rappel (leçon du 10/06 : un re-routage active des bugs
  latents en aval).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional

logger = logging.getLogger("Irrigation")

# --- Commutateurs d'urgence -------------------------------------------------
IRRIGATION_SHADOW = os.getenv("IRRIGATION_SHADOW", "1") != "0"   # observe + logge
IRRIGATION_ACTIVE = os.getenv("IRRIGATION_ACTIVE", "0") == "1"   # applique (Phase 2, OFF)

IRRIGATION_LOG_PATH = os.path.join("memory", "irrigation_shadow.jsonl")

# --- Zones anatomiques (enum discret) ---------------------------------------


class Zone:
    TRONC = "TRONC"                      # reptilien / respiration / cardiaque (survie)
    LIMBIQUE = "LIMBIQUE"                # amygdale / veto / friction / nocicepteurs
    TEMPORAL_MEDIAN = "TEMPORAL_MEDIAN"  # hippocampe / épisodique / graines
    CORTEX = "CORTEX"                    # agents délibératifs (DÉFAUT rétro-compatible)


ZONES: List[str] = [Zone.TRONC, Zone.LIMBIQUE, Zone.TEMPORAL_MEDIAN, Zone.CORTEX]

# Inférence de zone read-side depuis le metadata DÉJÀ présent (source/agent) —
# zéro écriture, rétro-compatible avec les 2622 docs existants (défaut CORTEX).
_SOURCE_ZONE_HINTS = [
    (("reptil", "respiration", "cardiac", "survie", "survival", "brainstem", "tronc"), Zone.TRONC),
    (("amygdal", "veto", "friction", "nocicept", "limbic", "limbique", "alarme"), Zone.LIMBIQUE),
    (("hippocamp", "episod", "épisod", "recall", "seed", "graine", "temporal", "souvenir"), Zone.TEMPORAL_MEDIAN),
]

# --- Calibration Phase 1 (à ajuster après lecture du shadow) -----------------
PERFUSION_FLOOR = 0.05      # plancher disjoncteur : aucune zone jamais totalement éteinte
EMA_NEW_WEIGHT = 0.70       # poids du nouveau (0.70) vs mémoire (0.30) — anti-thrashing
THREAT_RISE_FULL = 0.05     # d(Threat)/dt (/s) qui sature l'effet de crise (~+1.5 sur 30s)
DOPAMINE_FULL = 0.30        # écart dopamine-baseline qui sature l'ouverture exploratoire
COPING_BOOST = 0.5          # gain max appliqué aux traces coping sous menace montante


def infer_zone(meta: Optional[Dict]) -> str:
    """Zone d'origine d'une trace. Priorité : zone explicite > indices source > CORTEX."""
    meta = meta or {}
    z = meta.get("zone")
    if z in ZONES:
        return z
    src = str(meta.get("source", "")).lower()
    for keys, zone in _SOURCE_ZONE_HINTS:
        if any(k in src for k in keys):
            return zone
    return Zone.CORTEX


def is_coping(meta: Optional[Dict]) -> bool:
    """Affinité coping (structure de secours). Flag explicite si présent, sinon PROXY
    Phase 1 : une leçon tier_status==PREMIUM est par définition un invariant de survie."""
    meta = meta or {}
    v = meta.get("coping_affinity", None)
    if v is not None and v != "":
        return str(v).lower() in ("true", "1", "yes")
    return str(meta.get("tier_status", "")).upper() == "PREMIUM"


# --- Lecture de l'état neurochimique (synchrone, défensive) ------------------
_prev_threat: Optional[float] = None
_prev_threat_ts: Optional[float] = None
_prev_perfusion: Optional[Dict[str, float]] = None


def reset() -> None:
    """Réinitialise les buffers volatils (dérivée de menace + EMA). Pour les tests."""
    global _prev_threat, _prev_threat_ts, _prev_perfusion
    _prev_threat = None
    _prev_threat_ts = None
    _prev_perfusion = None


def read_neuro_state() -> Dict[str, float]:
    """Lit threat_level, dopamine relatif à la baseline, et d(Threat)/dt (2 points).

    La dérivée réutilise le patron du watchdog council (différence à 2 points). Le
    buffer est volatil par DESIGN (signal vivant, aucune persistance requise)."""
    global _prev_threat, _prev_threat_ts
    threat = 0.0
    try:
        from core.reptilian_core import reptilian
        threat = float(getattr(reptilian, "threat_level", 0.0))
    except Exception:
        threat = 0.0

    now = time.time()
    d_threat_dt = 0.0
    if _prev_threat is not None and _prev_threat_ts is not None:
        dt = now - _prev_threat_ts
        if dt > 0:
            d_threat_dt = (threat - _prev_threat) / dt
    _prev_threat = threat
    _prev_threat_ts = now

    dopamine_rel = 0.0
    try:
        from core.dopamine_system import dopamine, BASELINE_DOPAMINE
        dopamine_rel = float(getattr(dopamine, "dopamine_level", BASELINE_DOPAMINE)) - float(BASELINE_DOPAMINE)
    except Exception:
        dopamine_rel = 0.0

    return {"threat": threat, "d_threat_dt": d_threat_dt, "dopamine_rel": dopamine_rel}


def _ema_smooth(target: Dict[str, float]) -> Dict[str, float]:
    """Lissage EMA. Préserve les invariants : EMA de deux vecteurs (somme=N, ≥plancher)
    reste (somme=N, ≥plancher) car la combinaison est convexe et linéaire."""
    global _prev_perfusion
    if _prev_perfusion is None:
        _prev_perfusion = {z: 1.0 for z in ZONES}
    a = EMA_NEW_WEIGHT
    w = {z: a * target[z] + (1.0 - a) * _prev_perfusion[z] for z in ZONES}
    _prev_perfusion = w
    return w


def compute_perfusion(state: Dict[str, float], smooth: bool = True) -> Dict[str, float]:
    """Carte de perfusion par zone. Redistribution à SOMME CONSTANTE (= len(ZONES))
    du sang computationnel, avec plancher disjoncteur garanti.

    - d(Threat)/dt > 0 (crise qui s'emballe) : sang vers TRONC + LIMBIQUE (coping),
      CORTEX délibératif réduit (baisse de perfusion protectrice).
    - Dopamine > baseline (calme stable) : vannes CORTEX + associations lointaines
      (TEMPORAL_MEDIAN) ouvertes pour l'exploration.

    Invariants (préservés par l'EMA) : pour tout z, w[z] >= PERFUSION_FLOOR ;
    sum(w) == len(ZONES) (à epsilon près).
    """
    n = len(ZONES)
    raw = {z: 1.0 for z in ZONES}

    d_threat = state.get("d_threat_dt", 0.0)
    dopa = state.get("dopamine_rel", 0.0)

    if d_threat > 0:
        i = min(1.0, d_threat / THREAT_RISE_FULL)
        raw[Zone.TRONC] += 0.8 * i
        raw[Zone.LIMBIQUE] += 0.5 * i
        raw[Zone.TEMPORAL_MEDIAN] -= 0.4 * i
        raw[Zone.CORTEX] -= 0.9 * i
    if dopa > 0:
        g = min(1.0, dopa / DOPAMINE_FULL)
        raw[Zone.CORTEX] += 0.7 * g
        raw[Zone.TEMPORAL_MEDIAN] += 0.3 * g
        raw[Zone.TRONC] -= 0.5 * g
        raw[Zone.LIMBIQUE] -= 0.5 * g

    # Répartition à somme constante AVEC plancher garanti : on réserve la masse de
    # plancher (floor*n), on distribue le reste proportionnellement aux poids relatifs
    # positifs. => chaque w >= floor ET sum == n EXACTEMENT.
    reserved = PERFUSION_FLOOR * n
    free = n - reserved
    rel = {z: max(0.0, raw[z]) for z in ZONES}
    total_rel = sum(rel.values())
    if total_rel <= 0:
        w = {z: 1.0 for z in ZONES}
    else:
        w = {z: PERFUSION_FLOOR + free * (rel[z] / total_rel) for z in ZONES}

    if smooth:
        w = _ema_smooth(w)
    return w


def perfusion_for_doc(meta: Optional[Dict], perfusion_map: Dict[str, float],
                      state: Dict[str, float]) -> float:
    """Multiplicateur de perfusion d'un document : base de zone × boost coping sous
    menace montante (anti-rumination : on fait remonter les bouées, pas les plaies)."""
    base = perfusion_map.get(infer_zone(meta), 1.0)
    if state.get("d_threat_dt", 0.0) > 0 and is_coping(meta):
        i = min(1.0, state["d_threat_dt"] / THREAT_RISE_FULL)
        base *= (1.0 + COPING_BOOST * i)
    return base


def rerank(ids: List[str], distances: List[float], metas: List[Dict],
           state: Optional[Dict[str, float]] = None) -> Dict:
    """Re-classe un top-K (déjà retourné par Chroma) par Score = sim × perfusion.
    Fonction PURE — ne ré-embedde rien, réutilise les distances cosinus existantes.
    `similarité = 1 - distance` (collection cosinus)."""
    if state is None:
        state = read_neuro_state()
    perf = compute_perfusion(state)
    scored = []
    for i, did in enumerate(ids):
        dist = float(distances[i]) if i < len(distances) and distances[i] is not None else 1.0
        sim = 1.0 - dist
        meta = metas[i] if i < len(metas) else {}
        scored.append((did, sim * perfusion_for_doc(meta, perf, state)))
    order_idx = sorted(range(len(scored)), key=lambda k: scored[k][1], reverse=True)
    return {
        "order": [scored[k][0] for k in order_idx],
        "scores": [round(scored[k][1], 4) for k in order_idx],
        "perfusion": {z: round(perf[z], 3) for z in perf},
    }


def reorder_result(result: Dict, new_order_ids: List[str]) -> Dict:
    """Réordonne un résultat Chroma (listes par-requête) selon new_order_ids. PURE.
    Utilisé UNIQUEMENT par le mode IRRIGATION_ACTIVE (Phase 2, dormant en Phase 1)."""
    ids = ((result or {}).get("ids") or [[]])[0]
    if not ids:
        return result
    idx = [ids.index(i) for i in new_order_ids if i in ids]
    out = dict(result)
    for key in ("ids", "distances", "documents", "metadatas", "embeddings", "uris", "data"):
        seq = (result or {}).get(key)
        if (seq and isinstance(seq, list) and seq
                and isinstance(seq[0], list) and len(seq[0]) == len(ids)):
            out[key] = [[seq[0][i] for i in idx]]
    return out
