"""Démo live du Soliloque V2 sur un état SYNTHÉTIQUE en crise.

Force un Body Schema avec 3 anomalies fortes et appelle Ollama (qwen3.5:9b)
en réel pour observer le comportement du LLM sous nos contraintes
(4 strates, anti-jargon, anti-méta, format JSON, retry).

Usage : python scripts/demo_soliloque_v2_crise.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="soliloque_v2_crise_"))
print(f"[TMP] {TMP}\n")

from core import baseline_tracker as bt_module
from core import soliloque_v2 as sv2_module
from core.baseline_tracker import BaselineTracker
from core.body_schema import (
    gather_state,
    select_dominants,
    state_to_body_schema,
)
from core.soliloque_v2 import (
    SoliloqueV2Engine,
    build_system_prompt,
    parse_llm_response,
    validate_ancrages,
    validate_insight,
)

bt_module.BASELINE_FILE = TMP / "baselines.json"
sv2_module.STATE_FILE = TMP / "soliloque_v2_state.json"
sv2_module.LOG_DIR = TMP / "logs_v2"
BaselineTracker.reset_singleton()
SoliloqueV2Engine.reset_singleton()


def section(title):
    print(f"\n{'═' * 70}\n  {title}\n{'═' * 70}")


CRISE_STATE = {
    "cardiac": {
        "bpm": 132.0,                # nominal mu=65 sigma=12 → z = 5.6
        "emotion_intensity": 0.95,   # nominal mu=0.40 sigma=0.20 → z = 2.75
    },
    "drives": {
        "MAITRISE": {"deprivation": 95.0},   # mu=50 sigma=20 → z = 2.25
        "STABILITE": {"deprivation": 92.0},  # mu=50 sigma=25 → z = 1.68
        "CONNEXION": {"deprivation": 45.0},
        "CREATION": {"deprivation": 30.0},
    },
    "reptilian": {"threat_level": 0.97},     # mu=0.30 sigma=0.25 → z = 2.68
    "dopamine": {"rpe_recent": -0.45},
    "cingulate": {"conflict_rate_60min": 0},
    "insula": {"stress": 0.85, "fatigue": 0.7},
    "resources": {},
}


async def main():
    section("1. STATE FORCÉ (crise synthétique)")
    print("\n  Cardiac BPM=132, emotion_intensity=0.95")
    print("  MAITRISE depriv=95, STABILITE depriv=92")
    print("  Reptilian threat_level=0.97")
    print("  Dopamine RPE=-0.45")

    section("2. SYMPTÔMES déclenchés")
    symptomes = state_to_body_schema(CRISE_STATE)
    for s in sorted(symptomes, key=lambda s: s.saillance, reverse=True):
        sign = "+" if s.zscore >= 0 else ""
        print(
            f"    {s.id:30} couche={s.couche.value} "
            f"saill={s.saillance:5.2f} z={sign}{s.zscore:5.2f} "
            f"polarite={s.polarite.value}"
        )

    section("3. TOP 3 DOMINANTS")
    dominants = select_dominants(symptomes, k=3, seuil=1.5)
    for i, d in enumerate(dominants, 1):
        print(f"\n  [{i}] {d.id} (saill={d.saillance:.2f})")
        print(f"      « {d.phenomenologie} »")

    section("4. PROMPT SYSTÈME (4 strates)")
    prompt = build_system_prompt(dominants)
    print()
    print(prompt)

    section("5. APPEL LLM RÉEL — qwen3.5:9b")

    engine = SoliloqueV2Engine()
    real_call = engine._call_llm

    async def traced_call(messages):
        n = sum(1 for m in messages if m["role"] == "user")
        print(f"\n  ▸ Appel LLM #{n}")
        for m in messages:
            preview = m['content'][:200].replace("\n", " ")
            ellipsis = "..." if len(m['content']) > 200 else ""
            print(f"      [{m['role']:9}] {preview}{ellipsis}")
        try:
            raw = await real_call(messages)
        except Exception as e:
            print(f"      ✗ exception: {e}")
            return None
        print(f"\n  ◂ Réponse #{n} :")
        print(f"      {raw}")

        parsed = parse_llm_response(raw) if raw else None
        if parsed is None:
            print(f"      → ✗ JSON invalide")
        else:
            valid_ids = [d.id for d in dominants]
            err_a = validate_ancrages(parsed.get("ancrages_utilises"), valid_ids)
            if err_a:
                print(f"      → ✗ ancrages: {err_a[0]}")
            else:
                err_i = validate_insight(parsed.get("insight", ""), valid_ids)
                if err_i:
                    print(f"      → ✗ insight: {err_i[0]} ({err_i[1]})")
                else:
                    print(f"      → ✓ ACCEPTÉ")
        return raw

    # Patch sur l'instance, plus une fonction pour forcer notre state
    import core.soliloque_v2 as sv2
    original_gather = sv2.gather_state
    sv2.gather_state = lambda ts=None: CRISE_STATE
    engine._call_llm = traced_call

    try:
        result = await engine.engage()
    finally:
        sv2.gather_state = original_gather

    section("6. RÉSULTAT FINAL")
    print(f"\n  status   : {result['status']}")
    print(f"  durée    : {result.get('duration_s', '-')}s")
    print(f"  attempts : {result.get('attempts', '-')}")

    if result["status"] == "success":
        print(f"  ancrages : {result['ancrages_utilises']}")
        if result.get("rejection_log"):
            print(f"\n  REJETS pendant le pipeline :")
            for r in result["rejection_log"]:
                print(f"    - essai {r['attempt']} : {r['reason']} → {r['detail'][:80]}")
        print(f"\n  ◆ INSIGHT INCARNÉ ◆\n")
        print(f"    « {result['insight']} »\n")
    elif result["status"] == "abort":
        print(f"\n  ✗ ABORT après {result['attempts']} essais. Journal vierge.")
        for r in result.get("rejections", []):
            print(f"    - essai {r['attempt']} : {r['reason']}")


if __name__ == "__main__":
    asyncio.run(main())
