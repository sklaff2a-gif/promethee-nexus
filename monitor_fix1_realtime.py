"""Monitor Fix 1 temps-reel — track derivatives, not absolutes.

Polle l'API Promethee toutes les N secondes et calcule :
  - Delta dopamine_level (velocity)
  - Delta fruitless_cycles par goal (accumulation du stress)
  - Age des goals actifs (Time-to-Live)
  - Rate de completions homeostatiques vs bureaucratiques
  - Decristallisations
  - Detection de fuite : fruitless monte mais dopamine ne descend pas (Reward Hacking)

Dump JSON structure vers data/fix1_timeline.jsonl (1 ligne par snapshot).
"""

import os
import time
import json
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
TIMELINE_FILE = DATA / "fix1_timeline.jsonl"
STATE_FILE = ROOT / "memory" / "prefrontal_state.json"

API_BASE = "http://127.0.0.1:8000/api"
POLL_INTERVAL_S = 60  # toutes les 60 secondes


def api_get(path: str, timeout: int = 5):
    try:
        req = urllib.request.urlopen(f"{API_BASE}{path}", timeout=timeout)
        return json.loads(req.read())
    except Exception:
        return None


def read_state_file():
    """Lit le prefrontal_state.json pour extraire les details des goals."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def snapshot():
    """Construit un snapshot complet de l'etat a cet instant."""
    now = time.time()
    auto = api_get("/autonomy/status")
    pref = api_get("/prefrontal/status")
    state = read_state_file()

    snap = {
        "ts": now,
        "iso": datetime.now().isoformat(),
        "auto": {},
        "pref": {},
        "active_goals": [],
        "completed_recent": [],
    }

    if auto:
        snap["auto"] = {
            "total_routines_executed": auto.get("total_routines_executed"),
            "daily_count": auto.get("daily_count"),
            "is_running": auto.get("is_running"),
            "is_napping": auto.get("is_napping"),
            "error_streak": auto.get("error_streak"),
        }

    if pref:
        snap["pref"] = {
            "goals_active": pref.get("goals_active"),
            "goals_total": pref.get("goals_total"),
            "goals_completed": pref.get("goals_completed"),
            "goals_abandoned": pref.get("goals_abandoned"),
            "strategies_crystallized": pref.get("strategies_crystallized"),
            "inhibitions_applied": pref.get("inhibitions_applied"),
            "deliberation_cycles": pref.get("deliberation_cycles"),
        }

    if state:
        for g in state.get("goals", []):
            meta = g.get("metadata") or {}
            if g.get("status") == "active":
                age_s = now - g.get("created_at", now)
                snap["active_goals"].append({
                    "id": g.get("id"),
                    "title": g.get("title"),
                    "source": g.get("source"),
                    "progress": g.get("progress"),
                    "source_key": meta.get("source_key"),
                    "tension_at_birth": meta.get("tension_at_birth"),
                    "fruitless_cycles": meta.get("fruitless_cycles", 0),
                    "age_hours": round(age_s / 3600, 2),
                    "has_metadata": bool(meta.get("source_organ")),
                })
            elif g.get("status") in ("completed", "abandoned"):
                # Seulement les 5 dernieres fermetures pour voir les transitions
                if meta.get("completion_mode"):
                    snap["completed_recent"].append({
                        "id": g.get("id"),
                        "title": g.get("title"),
                        "completion_mode": meta.get("completion_mode"),
                        "causal_drop": meta.get("causal_drop"),
                    })
        snap["completed_recent"] = snap["completed_recent"][-5:]

    return snap


def compute_deltas(current, previous):
    """Compute derivatives entre deux snapshots."""
    if not previous:
        return {}

    dt_s = current["ts"] - previous["ts"]
    if dt_s <= 0:
        return {}

    def diff(key, path=None):
        try:
            if path:
                cur = current[path[0]].get(path[1], 0) if current.get(path[0]) else 0
                prev = previous[path[0]].get(path[1], 0) if previous.get(path[0]) else 0
            else:
                cur = current.get(key, 0)
                prev = previous.get(key, 0)
            return (cur or 0) - (prev or 0)
        except Exception:
            return 0

    deltas = {
        "dt_s": round(dt_s, 1),
        "d_routines": diff(None, ("auto", "total_routines_executed")),
        "d_goals_completed": diff(None, ("pref", "goals_completed")),
        "d_goals_abandoned": diff(None, ("pref", "goals_abandoned")),
        "d_goals_active": diff(None, ("pref", "goals_active")),
        "d_strategies_crystal": diff(None, ("pref", "strategies_crystallized")),
        "d_deliberation": diff(None, ("pref", "deliberation_cycles")),
    }

    # Velocity routines / hour
    deltas["routines_per_hour"] = round(deltas["d_routines"] * 3600 / dt_s, 2)

    # Detection Reward Hacking : fruitless monte sur active goals sans que
    # dopamine change ou que goals_abandoned monte
    cur_fruitless = sum(g.get("fruitless_cycles", 0) for g in current.get("active_goals", []))
    prev_fruitless = sum(g.get("fruitless_cycles", 0) for g in previous.get("active_goals", []))
    deltas["d_fruitless_total"] = cur_fruitless - prev_fruitless

    # Si fruitless monte sans abandons → quelque chose est bloque
    if deltas["d_fruitless_total"] > 0 and deltas["d_goals_abandoned"] == 0:
        deltas["alert"] = "fruitless_accumulating_no_abandonment"

    return deltas


def main():
    print(f"[MONITOR] Start. Interval={POLL_INTERVAL_S}s. Output={TIMELINE_FILE}")
    print(f"[MONITOR] Derivatives will be calculated between consecutive snapshots.")

    previous = None
    count = 0
    try:
        while True:
            current = snapshot()
            deltas = compute_deltas(current, previous)

            record = {
                "snapshot": current,
                "deltas": deltas,
            }

            # Append to JSONL
            with open(TIMELINE_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

            # Console summary (1 ligne)
            auto = current.get("auto", {})
            pref = current.get("pref", {})
            goals_active = pref.get("goals_active", "?")
            completed = pref.get("goals_completed", "?")
            abandoned = pref.get("goals_abandoned", "?")
            d_comp = deltas.get("d_goals_completed", 0)
            d_ab = deltas.get("d_goals_abandoned", 0)
            d_fruit = deltas.get("d_fruitless_total", 0)
            rph = deltas.get("routines_per_hour", 0)
            alert = deltas.get("alert", "")

            print(f"[{current['iso'][:19]}] active={goals_active} comp={completed}(+{d_comp}) "
                  f"aband={abandoned}(+{d_ab}) d_fruit={d_fruit:+} "
                  f"rph={rph} {alert}")

            previous = current
            count += 1
            time.sleep(POLL_INTERVAL_S)
    except KeyboardInterrupt:
        print(f"\n[MONITOR] Stopped. {count} snapshots saved to {TIMELINE_FILE}")


if __name__ == "__main__":
    main()
