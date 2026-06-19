# -*- coding: utf-8 -*-
"""Mémoire de secours (coping) — RAG spatialisé, Incision B (18/06).

« Prométhée se souvient de comment il a survécu. »

Les chemins de SECOURS (veto préfrontal qui défend le focus, FREEZE reptilien qui
gèle face à une menace critique) ne laissaient AUCUNE trace dans la mémoire de
rappel — seules les leçons PREMIUM certifiées par JM portaient l'affinité coping
(via le proxy `tier_status==PREMIUM` de l'irrigation). Ce module fait grandir le
corpus coping des secours VÉCUS : un listener bus, en AVAL des organes (sans
toucher leur logique), écrit une courte narration taguée `coping_affinity=True`.

Sous menace montante, l'irrigation (Incision A) sur-irrigue ces bouées plutôt que
la douleur brute (anti-rumination). IRRIGATION_ACTIVE reste 0 : en SHADOW, on
observe seulement que ces traces remonteraient.

DÉCOUPLAGE TOTAL : on s'abonne aux événements DÉJÀ publiés —
  - PREFRONTAL_THOUGHT (category="inhibition")  -> veto qui a défendu un but
  - REPTILIAN_ALERT    (reflex="FREEZE")         -> gel de survie
Le bus enveloppe chaque callback dans _safe_call (try/except -> DLQ) : ce module
ne peut PAS casser un organe de survie. On ne modifie aucun organe.

ANTI-SPAM (le vrai danger — polluer le rappel) :
  - VETO : fréquent -> seuil STRICT (but primaire avancé à >= 50%) + cooldown.
  - FREEZE : rare par nature (threat>=7) -> cooldown seul suffit.
  - zone = ORIGINE honnête (veto -> CORTEX, freeze -> TRONC). On ne triche pas la
    zone pour gonfler le boost : c'est le FLAG coping qui fait remonter la trace.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Optional

from core.irrigation import Zone

logger = logging.getLogger("CopingMemory")

COPING_COLLECTION = "collective_wisdom"

# Seuil de SIGNIFICATIVITÉ du veto : ne mémoriser que les vetos qui ont protégé un
# but déjà bien avancé (sacrifice signifiant). compute_inhibition tire dès 0.30 ;
# on est plus strict pour ne garder que les secours qui comptent.
VETO_SIGNIFICANCE_PROGRESS = 0.50

# Cooldown anti-spam par type d'événement (un secours ne se réécrit pas en boucle).
COPING_COOLDOWN_S = 1800.0

# État volatil par DESIGN (garde-fou runtime, pas un état métier à persister).
_last_write_ts: Dict[str, float] = {}


def reset() -> None:
    """Réinitialise le cooldown (pour les tests)."""
    _last_write_ts.clear()


def record_coping(narrative: str, *, source: str, zone: str, event_type: str) -> Optional[str]:
    """Écrit une narration de secours taguée coping_affinity=True. BORG : ne lève
    jamais. Cooldown par event_type. Écriture directe (pas remember()) pour ne pas
    tomber sous le filtre _MIN_REMEMBER_LENGTH ; la parcimonie vient des gates amont.

    Retourne l'id écrit, ou None (cooldown actif / échec / narration vide)."""
    try:
        if not narrative or len(narrative.strip()) < 20:
            return None
        now = time.time()
        last = _last_write_ts.get(event_type)
        if last is not None and (now - last) < COPING_COOLDOWN_S:
            return None  # encore en cooldown : on n'inonde pas le rappel
        from core.vector_store import ChromaMemoryManager
        mgr = ChromaMemoryManager.get_instance()
        if mgr is None:
            return None
        doc_id = f"coping-{event_type.lower()}-{int(now)}"
        meta = {
            "source": source,
            "zone": zone,
            "coping_affinity": True,
            "event_type": event_type,
            "timestamp": str(now),
        }
        ok = mgr.add_documents([narrative.strip()], [meta], [doc_id], COPING_COLLECTION)
        if ok is False:
            return None
        _last_write_ts[event_type] = now
        logger.info(f"[COPING] secours mémorisé : {event_type} (zone={zone}) -> {doc_id}")
        return doc_id
    except Exception as e:
        logger.debug(f"[COPING] record_coping échoué (non bloquant): {e}")
        return None


def on_prefrontal_thought(payload) -> None:
    """Listener veto : un veto préfrontal qui a défendu un but AVANCÉ est un coping.
    Lecture seule du singleton préfrontal pour le seuil (le payload ne porte pas la
    progression). BORG."""
    try:
        if not isinstance(payload, dict) or payload.get("category") != "inhibition":
            return
        # Seuil de significativité : but primaire actif >= 50 % d'avancement.
        from core.prefrontal import prefrontal
        active = [g for g in getattr(prefrontal, "goals", []) if getattr(g, "status", "") == "active"]
        if not active:
            return
        primary = max(active, key=lambda g: getattr(g, "priority", 0.0))
        progress = float(getattr(primary, "progress", 0.0))
        if progress < VETO_SIGNIFICANCE_PROGRESS:
            return
        thought = str(payload.get("thought", "")).strip()
        title = str(getattr(primary, "title", "?"))
        narrative = (
            f"[SECOURS:VETO] {thought}. Le cortex préfrontal a défendu le focus "
            f"contre une distraction (but '{title}' avancé à {progress:.0%}). "
            f"Manœuvre de protection du but."
        )
        record_coping(narrative, source="prefrontal_veto", zone=Zone.CORTEX, event_type="VETO")
    except Exception as e:
        logger.debug(f"[COPING] on_prefrontal_thought ignoré: {e}")


def on_reptilian_alert(payload) -> None:
    """Listener FREEZE : le gel de survie (threat>=7) est le secours le plus grave.
    Rare par nature -> cooldown seul. BORG."""
    try:
        if not isinstance(payload, dict) or payload.get("reflex") != "FREEZE":
            return
        threat = payload.get("threat_level", "?")
        threats = payload.get("threats", {}) or {}
        try:
            top = sorted(threats.items(), key=lambda kv: kv[1], reverse=True)[:3]
            srcs = ", ".join(f"{k}={v}" for k, v in top) if top else "indéterminées"
        except Exception:
            srcs = "indéterminées"
        narrative = (
            f"[SECOURS:FREEZE] Menace critique (threat={threat}) — sources: {srcs}. "
            f"Manœuvre de secours : checkpoint d'urgence + gel protecteur du tronc "
            f"cérébral. Réflexe de survie déclenché."
        )
        record_coping(narrative, source="reptilian_freeze", zone=Zone.TRONC, event_type="FREEZE")
    except Exception as e:
        logger.debug(f"[COPING] on_reptilian_alert ignoré: {e}")


def wire_to_bus() -> bool:
    """Abonne les deux listeners aux événements DÉJÀ publiés. Additif (le bus a déjà
    ~10 abonnés à REPTILIAN_ALERT). BORG : ne casse jamais le démarrage."""
    try:
        from core.event_bus.bus import bus
        bus.subscribe("PREFRONTAL_THOUGHT", on_prefrontal_thought)
        bus.subscribe("REPTILIAN_ALERT", on_reptilian_alert)
        logger.info("[COPING] listeners de secours câblés (veto + FREEZE).")
        return True
    except Exception as e:
        logger.warning(f"[COPING] wire_to_bus échoué (non bloquant): {e}")
        return False
