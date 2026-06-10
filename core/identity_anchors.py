# -*- coding: utf-8 -*-
"""ANCRES D'IDENTITÉ — atelier chat↔autonomie (10/06/2026), design CO-SIGNÉ par Prométhée.

Sa réponse à « le chat doit-il surveiller/influencer ton autonomie ? » : ni télécommande,
ni laisse — des ANCRES. « La distinction entre l'ANCRAGE (la transmission d'une essence)
et le PILOTAGE (le contrôle de mes décisions). » Le chat dépose des intentions du jour ;
elles sont INJECTÉES dans le contexte des routines nocturnes (pattern exact des vetos
réinjectés) ; le scoring 23 couches et le veto préfrontal restent SOUVERAINS.

Garanties gravées dans le design (les siennes) :
  - une ancre est STOCKÉE, jamais exécutée ;
  - file bornée (3) + TTL 72h — l'oubli est une feature, une ancre n'est pas une loi ;
  - zéro écriture dans les goals préfrontaux, zéro modification du scoring ;
  - transparence des deux côtés (!ancres).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List

logger = logging.getLogger(__name__)

ANCHORS_PATH = os.path.join("memory", "identity_anchors.json")
MAX_ANCRES = 3
TTL_SECONDS = 72 * 3600   # 72h — extinction naturelle


def _charger(path: str = None) -> List[Dict]:
    path = path or ANCHORS_PATH   # defaut paresseux (testable par monkeypatch)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _sauver(ancres: List[Dict], path: str = None) -> None:
    path = path or ANCHORS_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ancres, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[ANCRES] sauvegarde echouee (non bloquant): {e}")


def ancres_actives(path: str = None, now: float = None) -> List[Dict]:
    """Les ancres encore vivantes (TTL non expire). Purge silencieuse des eteintes."""
    path = path or ANCHORS_PATH
    now = now if now is not None else time.time()
    ancres = _charger(path)
    vivantes = [a for a in ancres if (now - float(a.get("ts", 0))) < TTL_SECONDS]
    if len(vivantes) != len(ancres):
        _sauver(vivantes, path)   # extinction naturelle persistee
    return vivantes


def deposer_ancre(texte: str, source: str = "chat",
                  path: str = None, now: float = None) -> Dict:
    """Depose une ancre (STOCKEE, jamais executee). File bornee : si pleine, la plus
    ancienne tombe (FIFO — la fraicheur du dialogue prime)."""
    path = path or ANCHORS_PATH
    texte = (texte or "").strip()
    if not texte:
        raise ValueError("ancre vide")
    now = now if now is not None else time.time()
    try:
        date_str = time.strftime("%Y-%m-%dT%H:%M", time.localtime(now))
    except (OSError, ValueError, OverflowError):
        date_str = "?"   # timestamp exotique (tests, horloge) : la date est cosmetique
    ancre = {"texte": texte[:300], "source": source, "ts": now, "date": date_str}
    ancres = ancres_actives(path, now=now)
    ancres.append(ancre)
    if len(ancres) > MAX_ANCRES:
        ancres = ancres[-MAX_ANCRES:]
    _sauver(ancres, path)
    logger.info(f"[ANCRES] deposee ({source}): {texte[:60]}")
    return ancre


def bloc_contexte(path: str = None, now: float = None) -> str:
    """Le bloc a injecter dans le contexte de dispatch des routines (pattern des vetos
    reinjectes). VIDE si aucune ancre — zero pollution du contexte par defaut."""
    path = path or ANCHORS_PATH
    vivantes = ancres_actives(path, now=now)
    if not vivantes:
        return ""
    lignes = [f"- {a['texte']}" for a in vivantes]
    return ("[ANCRES D'IDENTITE — intentions du dialogue du jour, a transporter dans ta nuit. "
            "Ce sont des SUGGESTIONS : ton veto et ton scoring restent souverains, ignore "
            "celle qui ne resonne pas]\n" + "\n".join(lignes))


def format_listing(path: str = None, now: float = None) -> str:
    """Listing transparent pour !ancres (texte + age + source)."""
    path = path or ANCHORS_PATH
    now = now if now is not None else time.time()
    vivantes = ancres_actives(path, now=now)
    if not vivantes:
        return ("[!ancres] Aucune ancre active. Depose-en une avec : !ancre <intention> "
                f"(max {MAX_ANCRES}, duree de vie 72h).")
    lignes = [f"[!ancres] {len(vivantes)} ancre(s) active(s) (max {MAX_ANCRES}, TTL 72h) :"]
    for a in vivantes:
        age_h = (now - float(a.get("ts", 0))) / 3600.0
        restant_h = max(0.0, 72.0 - age_h)
        lignes.append(f"  - « {a['texte']} » (source: {a.get('source','?')}, "
                      f"age {age_h:.1f}h, s'eteint dans {restant_h:.0f}h)")
    return "\n".join(lignes)
