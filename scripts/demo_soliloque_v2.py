"""Démo live du Soliloque V2 sur l'état actuel de Prométhée.

Lit les *_state.json des organes (cardiac, desire, etc.), construit le
Body Schema, appelle Ollama (qwen3.5:9b) en réel, trace toutes les étapes :
state agrégé, dominants, prompt, réponses LLM brutes, retry éventuel,
insight final.

Ne pollue pas le runtime live : redirige BASELINE_FILE/STATE_FILE/LOG_DIR
vers un dossier temporaire.

Usage : python scripts/demo_soliloque_v2.py
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

# Path du projet
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Setup TMP avant tout import
TMP = Path(tempfile.mkdtemp(prefix="soliloque_v2_demo_"))
print(f"[TMP] {TMP}\n")

from core import baseline_tracker as bt_module
from core import soliloque_v2 as sv2_module
from core.baseline_tracker import BaselineTracker
from core.soliloque_v2 import (
    SoliloqueV2Engine,
    build_system_prompt,
    parse_llm_response,
    validate_ancrages,
    validate_insight,
)
from core.body_schema import (
    SYMPTOMES,
    gather_state,
    select_dominants,
    state_to_body_schema,
)

bt_module.BASELINE_FILE = TMP / "baselines.json"
sv2_module.STATE_FILE = TMP / "soliloque_v2_state.json"
sv2_module.LOG_DIR = TMP / "logs_v2"
BaselineTracker.reset_singleton()
SoliloqueV2Engine.reset_singleton()


def section(title):
    print(f"\n{'═' * 70}\n  {title}\n{'═' * 70}")


async def main():
    section("1. STATE AGRÉGÉ depuis les organes natifs")
    state = gather_state()
    for organ, data in state.items():
        if organ == "now_ts":
            continue
        if isinstance(data, dict) and data:
            print(f"\n  [{organ}]")
            for k, v in data.items():
                if isinstance(v, dict):
                    sub = ", ".join(f"{kk}={vv}" for kk, vv in v.items() if kk != "_recent_satisfied_age_s")
                    print(f"    {k}: {{{sub}}}")
                else:
                    print(f"    {k}: {v}")

    section("2. SYMPTÔMES — tous les actifs (catalogue 32)")
    symptomes = state_to_body_schema(state)
    if not symptomes:
        print("\n  Aucun symptôme actif. Tout est dans la norme nominale.")
    else:
        print(f"\n  {len(symptomes)} symptôme(s) déclenché(s) sur {len(SYMPTOMES)} évalués :\n")
        for s in sorted(symptomes, key=lambda s: s.saillance, reverse=True):
            sign = "+" if s.zscore >= 0 else ""
            print(
                f"    {s.id:30} couche={s.couche.value} "
                f"saill={s.saillance:5.2f} "
                f"z={sign}{s.zscore:5.2f} dz/dt={s.dzdt:+5.2f} "
                f"polarite={s.polarite.value}"
            )

    section("3. SÉLECTION : Top 3 dominants (seuil 1.5)")
    dominants = select_dominants(symptomes, k=3, seuil=1.5)
    if not dominants:
        print("\n  ⊘ SILENCE MÉTABOLIQUE — aucun symptôme >= seuil 1.5.")
        print("  Le Soliloque V2 ne se déclencherait pas. Sortie.")
        return
    for i, d in enumerate(dominants, 1):
        print(f"\n  [{i}] id={d.id} (saill={d.saillance:.2f})")
        print(f"      phenom : « {d.phenomenologie} »")

    section("4. PROMPT 4 STRATES envoyé au LLM")
    prompt = build_system_prompt(dominants)
    print()
    print(prompt)

    section("5. APPEL LLM (qwen3.5:9b, format=json) — TRACE")

    engine = SoliloqueV2Engine()
    real_call = engine._call_llm
    call_log = []

    async def traced_call(messages):
        n = len(call_log) + 1
        print(f"\n  ▸ Appel LLM #{n} (msg={len(messages)})")
        for m in messages:
            preview = m['content'][:160].replace("\n", " ")
            print(f"      [{m['role']:9}] {preview}{'...' if len(m['content']) > 160 else ''}")
        try:
            raw = await real_call(messages)
        except Exception as e:
            print(f"      ✗ exception: {e}")
            call_log.append((messages, None))
            return None
        print(f"\n  ◂ Réponse #{n} :")
        print(f"      {raw}")
        call_log.append((messages, raw))

        # Validation immédiate (pour la trace, pas pour la décision)
        parsed = parse_llm_response(raw) if raw else None
        if parsed is None:
            print(f"      → ✗ JSON invalide")
        else:
            valid_ids = [d.id for d in dominants]
            err_a = validate_ancrages(parsed.get("ancrages_utilises"), valid_ids)
            if err_a:
                print(f"      → ✗ rejet ancrages : {err_a[0]} ({err_a[1]})")
            else:
                err_i = validate_insight(parsed.get("insight", ""), valid_ids)
                if err_i:
                    print(f"      → ✗ rejet insight : {err_i[0]} ({err_i[1]})")
                else:
                    print(f"      → ✓ accepté")
        return raw

    engine._call_llm = traced_call

    result = await engine.engage()

    section("6. RÉSULTAT FINAL")
    print(f"\n  status   : {result['status']}")
    print(f"  durée    : {result.get('duration_s', '-')}s")
    print(f"  attempts : {result.get('attempts', '-')}")
    if result.get("rejection_log"):
        print(f"  rejets   : {len(result['rejection_log'])}")
        for r in result["rejection_log"]:
            print(f"    - essai {r['attempt']} : {r['reason']} ({r['detail'][:80]})")

    if result["status"] == "success":
        print(f"\n  ancrages_utilises : {result['ancrages_utilises']}")
        print(f"\n  ◆ INSIGHT FINAL ◆\n")
        print(f"    « {result['insight']} »")

    print(f"\n[FICHIERS générés dans {TMP}]")
    for f in TMP.rglob("*"):
        if f.is_file():
            print(f"  {f.relative_to(TMP)}  ({f.stat().st_size} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
