#!/usr/bin/env python3
"""
analyze_run.py — Analyse automatique des logs de run Prométhée.

Parse les 3 formats de logs (promethee.log, talk_*.txt, interface_*.txt)
et produit un rapport structuré en Markdown.

Usage:
    python analyze_run.py logs/                          # Auto-détection
    python analyze_run.py logs/promethee.log             # Un seul fichier
    python analyze_run.py "C:\\path\\to\\log run copie\\"  # Dossier de copies
    python analyze_run.py logs/ --format json            # Sortie JSON
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Regex patterns pour promethee.log
# ---------------------------------------------------------------------------
RE_TIMESTAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")

RE_AUTONOMY_ROUTINE = re.compile(
    r"✨ AUTONOMY: Routine \[(.+?)\] \(score=([0-9.e+-]+)[^)]*\) -> \[(.+?)\] \((\d+)/(\d+)"
)

RE_ROUTINE_END = re.compile(
    r"✅ Fin Routine (.+?) \(qualité: ([0-9.]+)\)"
)

RE_ROUTINE_LOW_QUALITY = re.compile(
    r"⚠️ Routine (.+?) terminée mais qualité basse \(([0-9.]+)\) \[(.+?)\]"
)

RE_GRIMOIRE_INVOKE = re.compile(
    r"📖 GRIMOIRE INVOKE: (.+?) —"
)

RE_CLOUD_429 = re.compile(
    r"🚫 Quota Gemini épuisé \(429\) — cooldown (\d+)s"
)

RE_COUNCIL_DEBATE = re.compile(
    r"🗣️ COUNCIL DEBATE: (.+?) — (.+)"
)

RE_CLOUD_CALL = re.compile(
    r"☁️ Gemini \((.+?)\)"
)

RE_LOCAL_MODEL = re.compile(
    r"🏠 Traitement Local .+?: (.+?)(?:\.{3})?$"
)

RE_MEMORY_CLEANUP = re.compile(
    r"🧹 Nettoyage mémoire : (\d+) anciennes \+ (\d+) basse qualité"
)

RE_SECURITY_AUDIT = re.compile(
    r"🔒 SECURITY AUDIT: (.+)"
)

RE_REFACTOR = re.compile(
    r"🔧 REFACTOR: (.+)"
)

RE_CONSCIENCE = re.compile(
    r"🧠 CONSCIENCE: Ajustements adaptatifs: (.+)"
)

RE_LEARNING = re.compile(
    r"📚 APPRENTISSAGE: (.+)"
)


# ---------------------------------------------------------------------------
# Regex patterns pour talk_*.txt
# ---------------------------------------------------------------------------
RE_TALK_LINE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\] (\w+) \((\w+)\) > (.+)"
)

RE_TALK_STREAM_END = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\] (\w+) \[STREAM END\]"
)


# ---------------------------------------------------------------------------
# Regex patterns pour interface_*.txt
# ---------------------------------------------------------------------------
RE_INTERFACE_LOG = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\] \[LOG:(\w+)\] \[(\w+)\] (.+)"
)

RE_INTERFACE_DIALOGUE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\] \[DIALOGUE\] \[(\w+)\] (.+)"
)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_promethee_log(filepath: str) -> list:
    """Parse promethee.log et retourne une liste d'événements structurés."""
    events = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip()
                if not line:
                    continue

                # Extraire le timestamp
                ts_match = RE_TIMESTAMP.match(line)
                timestamp = ts_match.group(1) if ts_match else None

                # Routine lancée
                m = RE_AUTONOMY_ROUTINE.search(line)
                if m:
                    events.append({
                        "type": "routine_start",
                        "timestamp": timestamp,
                        "intent": m.group(1),
                        "score": float(m.group(2)),
                        "agent": m.group(3),
                        "count": int(m.group(4)),
                        "max": int(m.group(5)),
                    })
                    continue

                # Routine terminée avec succès
                m = RE_ROUTINE_END.search(line)
                if m:
                    events.append({
                        "type": "routine_end",
                        "timestamp": timestamp,
                        "agent": m.group(1),
                        "quality": float(m.group(2)),
                    })
                    continue

                # Routine basse qualité
                m = RE_ROUTINE_LOW_QUALITY.search(line)
                if m:
                    events.append({
                        "type": "routine_low_quality",
                        "timestamp": timestamp,
                        "intent": m.group(1),
                        "quality": float(m.group(2)),
                        "failure_type": m.group(3),
                    })
                    continue

                # Grimoire invoqué
                m = RE_GRIMOIRE_INVOKE.search(line)
                if m:
                    events.append({
                        "type": "grimoire_invoke",
                        "timestamp": timestamp,
                        "slug": m.group(1),
                    })
                    continue

                # Erreur 429 Cloud
                m = RE_CLOUD_429.search(line)
                if m:
                    events.append({
                        "type": "cloud_429",
                        "timestamp": timestamp,
                        "cooldown": int(m.group(1)),
                    })
                    continue

                # Council debate
                m = RE_COUNCIL_DEBATE.search(line)
                if m:
                    events.append({
                        "type": "council_debate",
                        "timestamp": timestamp,
                        "participants": m.group(1),
                        "mission": m.group(2),
                    })
                    continue

                # Appel Cloud
                m = RE_CLOUD_CALL.search(line)
                if m:
                    events.append({
                        "type": "cloud_call",
                        "timestamp": timestamp,
                        "model": m.group(1),
                    })
                    continue

                # Modèle local
                m = RE_LOCAL_MODEL.search(line)
                if m:
                    events.append({
                        "type": "local_model",
                        "timestamp": timestamp,
                        "model": m.group(1).strip(),
                    })
                    continue

                # Nettoyage mémoire
                m = RE_MEMORY_CLEANUP.search(line)
                if m:
                    events.append({
                        "type": "memory_cleanup",
                        "timestamp": timestamp,
                        "old": int(m.group(1)),
                        "low_quality": int(m.group(2)),
                    })
                    continue

                # Security audit
                m = RE_SECURITY_AUDIT.search(line)
                if m:
                    events.append({
                        "type": "security_audit",
                        "timestamp": timestamp,
                        "target": m.group(1).strip(),
                    })
                    continue

                # Refactor
                m = RE_REFACTOR.search(line)
                if m:
                    events.append({
                        "type": "refactor",
                        "timestamp": timestamp,
                        "target": m.group(1).strip(),
                    })
                    continue

                # Conscience
                m = RE_CONSCIENCE.search(line)
                if m:
                    events.append({
                        "type": "conscience",
                        "timestamp": timestamp,
                        "adjustments": m.group(1),
                    })
                    continue

                # Apprentissage ciblé
                m = RE_LEARNING.search(line)
                if m:
                    events.append({
                        "type": "learning",
                        "timestamp": timestamp,
                        "topic": m.group(1),
                    })
                    continue

    except FileNotFoundError:
        pass
    return events


def parse_talk_log(filepath: str) -> list:
    """Parse talk_YYYY-MM-DD.txt et retourne les interactions agents."""
    events = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip()
                if not line:
                    continue

                m = RE_TALK_LINE.match(line)
                if m:
                    events.append({
                        "type": "talk",
                        "time": m.group(1),
                        "agent": m.group(2),
                        "msg_type": m.group(3),
                        "content": m.group(4),
                    })
                    continue

                m = RE_TALK_STREAM_END.match(line)
                if m:
                    events.append({
                        "type": "stream_end",
                        "time": m.group(1),
                        "agent": m.group(2),
                    })
    except FileNotFoundError:
        pass
    return events


def parse_interface_log(filepath: str) -> list:
    """Parse interface_YYYY-MM-DD.txt et retourne les événements dual-channel."""
    events = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip()
                if not line:
                    continue

                m = RE_INTERFACE_LOG.match(line)
                if m:
                    events.append({
                        "type": "interface_log",
                        "time": m.group(1),
                        "level": m.group(2),
                        "agent": m.group(3),
                        "content": m.group(4),
                    })
                    continue

                m = RE_INTERFACE_DIALOGUE.match(line)
                if m:
                    events.append({
                        "type": "interface_dialogue",
                        "time": m.group(1),
                        "agent": m.group(2),
                        "content": m.group(3),
                    })
    except FileNotFoundError:
        pass
    return events


# ---------------------------------------------------------------------------
# Analyseurs
# ---------------------------------------------------------------------------

def analyze_routines(events: list) -> dict:
    """Analyse la distribution des routines, scores et qualité."""
    starts = [e for e in events if e["type"] == "routine_start"]
    ends = [e for e in events if e["type"] == "routine_end"]
    failures = [e for e in events if e["type"] == "routine_low_quality"]

    # Distribution par intent
    intent_counter = Counter(e["intent"] for e in starts)
    intent_scores = defaultdict(list)
    for e in starts:
        intent_scores[e["intent"]].append(e["score"])

    # Qualité par agent (depuis routine_end)
    agent_quality = defaultdict(list)
    for e in ends:
        agent_quality[e["agent"]].append(e["quality"])

    # Échecs par type
    failure_types = Counter(e["failure_type"] for e in failures)
    failure_by_intent = defaultdict(int)
    for e in failures:
        failure_by_intent[e["intent"]] += 1

    # Calcul des métriques globales
    all_quality = [e["quality"] for e in ends] + [e["quality"] for e in failures]
    avg_quality = sum(all_quality) / len(all_quality) if all_quality else 0.0
    success_count = sum(1 for q in all_quality if q >= 0.3)
    success_rate = (success_count / len(all_quality) * 100) if all_quality else 0.0

    # Durée totale
    timestamps = [e["timestamp"] for e in events if e.get("timestamp")]
    first_ts = timestamps[0] if timestamps else None
    last_ts = timestamps[-1] if timestamps else None

    total_routines = len(starts)
    max_routines = starts[0]["max"] if starts else 80

    return {
        "total_routines": total_routines,
        "max_routines": max_routines,
        "avg_quality": avg_quality,
        "success_rate": success_rate,
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
        "intent_distribution": dict(intent_counter.most_common()),
        "intent_scores": {k: {"avg": sum(v) / len(v), "count": len(v)} for k, v in intent_scores.items()},
        "agent_quality": {k: {"avg": sum(v) / len(v), "count": len(v)} for k, v in agent_quality.items()},
        "failures": {
            "total": len(failures),
            "by_type": dict(failure_types),
            "by_intent": dict(failure_by_intent),
        },
        "failure_details": failures,
    }


def analyze_cloud(events: list) -> dict:
    """Analyse les appels Cloud, erreurs 429, et ratio Cloud/Local."""
    cloud_calls = [e for e in events if e["type"] == "cloud_call"]
    local_calls = [e for e in events if e["type"] == "local_model"]
    errors_429 = [e for e in events if e["type"] == "cloud_429"]

    cloud_models = Counter(e["model"] for e in cloud_calls)
    local_models = Counter(e["model"] for e in local_calls)
    total_cooldown = sum(e["cooldown"] for e in errors_429)

    total = len(cloud_calls) + len(local_calls)
    cloud_pct = (len(cloud_calls) / total * 100) if total else 0
    local_pct = (len(local_calls) / total * 100) if total else 0

    return {
        "cloud_calls": len(cloud_calls),
        "local_calls": len(local_calls),
        "errors_429": len(errors_429),
        "total_cooldown_seconds": total_cooldown,
        "cloud_models": dict(cloud_models),
        "local_models": dict(local_models),
        "cloud_pct": cloud_pct,
        "local_pct": local_pct,
    }


def analyze_grimoire(events: list) -> dict:
    """Analyse la diversité des invocations Grimoire."""
    invokes = [e for e in events if e["type"] == "grimoire_invoke"]
    slug_counter = Counter(e["slug"] for e in invokes)
    return {
        "total_invocations": len(invokes),
        "unique_slugs": len(slug_counter),
        "distribution": dict(slug_counter.most_common()),
    }


def analyze_councils(events: list) -> dict:
    """Analyse les débats Council."""
    debates = [e for e in events if e["type"] == "council_debate"]
    return {
        "total_debates": len(debates),
        "details": [
            {
                "timestamp": e["timestamp"],
                "participants": e["participants"],
                "mission": e["mission"],
            }
            for e in debates
        ],
    }


def analyze_timeline(events: list) -> dict:
    """Analyse l'activité par heure."""
    hourly = defaultdict(lambda: {"routines": 0, "events": 0})

    for e in events:
        ts = e.get("timestamp")
        if not ts:
            continue
        try:
            hour = ts[11:13]  # "HH"
            hourly[hour]["events"] += 1
            if e["type"] == "routine_start":
                hourly[hour]["routines"] += 1
        except (IndexError, TypeError):
            continue

    return dict(sorted(hourly.items()))


# ---------------------------------------------------------------------------
# Filtrage par date
# ---------------------------------------------------------------------------

def _resolve_date(date_arg: str) -> str:
    """Resout 'today'/'yesterday'/YYYY-MM-DD en YYYY-MM-DD."""
    if not date_arg:
        return None
    if date_arg == "today":
        return datetime.now().strftime("%Y-%m-%d")
    if date_arg == "yesterday":
        from datetime import timedelta
        return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    # Validation format
    datetime.strptime(date_arg, "%Y-%m-%d")  # leve ValueError si invalide
    return date_arg


# ---------------------------------------------------------------------------
# Auto-détection et chargement des fichiers
# ---------------------------------------------------------------------------

def discover_logs(path: str, date_filter: str = None) -> dict:
    """Découvre et charge les logs depuis un chemin (fichier ou dossier)."""
    path = os.path.abspath(path)
    files = {"promethee": [], "talk": [], "interface": []}

    if os.path.isfile(path):
        name = os.path.basename(path).lower()
        if "promethee" in name:
            files["promethee"].append(path)
        elif "talk" in name:
            files["talk"].append(path)
        elif "interface" in name:
            files["interface"].append(path)
        else:
            # Tenter le format promethee par défaut
            files["promethee"].append(path)
        return files

    if os.path.isdir(path):
        # Chercher récursivement dans le dossier
        for root, _, filenames in os.walk(path):
            for fname in sorted(filenames):
                fpath = os.path.join(root, fname)
                fname_lower = fname.lower()
                if fname_lower.startswith("promethee") and fname_lower.endswith((".log", ".log.2026-02-14", ".log.2026-02-15", ".log.2026-02-16")):
                    files["promethee"].append(fpath)
                elif "promethee.log" in fname_lower:
                    files["promethee"].append(fpath)
                elif fname_lower.startswith("talk") and fname_lower.endswith(".txt"):
                    if date_filter and date_filter not in fname_lower:
                        continue
                    files["talk"].append(fpath)
                elif fname_lower.startswith("interface") and fname_lower.endswith(".txt"):
                    if date_filter and date_filter not in fname_lower:
                        continue
                    files["interface"].append(fpath)

        # Attraper aussi les fichiers rotatés (promethee.log.2026-02-XX)
        for root, _, filenames in os.walk(path):
            for fname in sorted(filenames):
                fpath = os.path.join(root, fname)
                if fpath not in files["promethee"] and "promethee.log" in fname.lower():
                    files["promethee"].append(fpath)

    return files


# ---------------------------------------------------------------------------
# Rapport Markdown
# ---------------------------------------------------------------------------

def generate_report(routines_analysis: dict, cloud_analysis: dict,
                    grimoire_analysis: dict, councils_analysis: dict,
                    timeline_analysis: dict, format: str = "markdown") -> str:
    """Génère le rapport final."""

    data = {
        "routines": routines_analysis,
        "cloud": cloud_analysis,
        "grimoire": grimoire_analysis,
        "councils": councils_analysis,
        "timeline": timeline_analysis,
    }

    if format == "json":
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)

    # --- Markdown ---
    lines = []
    r = routines_analysis
    c = cloud_analysis

    # Durée
    first = r.get("first_timestamp", "?")
    last = r.get("last_timestamp", "?")
    duration = ""
    if first and last and first != "?" and last != "?":
        try:
            t1 = datetime.strptime(first, "%Y-%m-%d %H:%M:%S")
            t2 = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
            delta = t2 - t1
            hours = int(delta.total_seconds() // 3600)
            minutes = int((delta.total_seconds() % 3600) // 60)
            duration = f"{hours}h{minutes:02d}m ({first[11:16]} -> {last[11:16]})"
            run_date = first[:10]
        except ValueError:
            duration = f"{first} -> {last}"
            run_date = first[:10] if first else "inconnu"
    else:
        run_date = "inconnu"

    lines.append(f"# Rapport d'analyse — Run du {run_date}")
    lines.append("")
    lines.append("## Resume")
    lines.append(f"- **Duree** : {duration}")
    lines.append(f"- **Routines executees** : {r['total_routines']}/{r['max_routines']}")
    lines.append(f"- **Qualite moyenne** : {r['avg_quality']:.2f}")
    lines.append(f"- **Erreurs 429 Cloud** : {c['errors_429']} (cooldown total : {c['total_cooldown_seconds']}s)")
    lines.append(f"- **Taux de succes** : {r['success_rate']:.0f}% (qualite >= 0.3)")
    lines.append("")

    # Distribution des routines
    lines.append("## Distribution des routines")
    lines.append("| Routine | Count | Score moy. | Echecs |")
    lines.append("|---------|-------|-----------|--------|")
    for intent, count in sorted(r["intent_distribution"].items(), key=lambda x: -x[1]):
        score_info = r["intent_scores"].get(intent, {})
        avg_score = score_info.get("avg", 0)
        failures = r["failures"]["by_intent"].get(intent, 0)
        lines.append(f"| {intent} | {count} | {avg_score:.1f} | {failures} |")
    lines.append("")

    # Activité par agent
    lines.append("## Activite par agent")
    lines.append("| Agent | Qualite moy. | Invocations |")
    lines.append("|-------|-------------|------------|")
    for agent, info in sorted(r["agent_quality"].items(), key=lambda x: -x[1]["count"]):
        lines.append(f"| {agent} | {info['avg']:.2f} | {info['count']} |")
    lines.append("")

    # Diversité Grimoire
    g = grimoire_analysis
    if g["total_invocations"] > 0:
        lines.append("## Diversite Grimoire")
        lines.append(f"Total invocations : {g['total_invocations']} ({g['unique_slugs']} slugs uniques)")
        lines.append("")
        lines.append("| Slug | Invocations |")
        lines.append("|------|------------|")
        for slug, count in g["distribution"].items():
            lines.append(f"| {slug} | {count} |")
        lines.append("")

    # Cloud vs Local
    lines.append("## Cloud vs Local")
    lines.append(f"- Appels Cloud tentes : {c['cloud_calls']}")
    lines.append(f"- Appels Local : {c['local_calls']}")
    lines.append(f"- Erreurs 429 : {c['errors_429']} (cooldown total : {c['total_cooldown_seconds']}s)")
    lines.append(f"- Ratio Cloud/Local : {c['cloud_pct']:.0f}%/{c['local_pct']:.0f}%")
    lines.append("")
    if c["local_models"]:
        lines.append("Modeles locaux utilises :")
        for model, count in sorted(c["local_models"].items(), key=lambda x: -x[1]):
            lines.append(f"  - {model} : {count} appels")
        lines.append("")
    if c["cloud_models"]:
        lines.append("Modeles Cloud utilises :")
        for model, count in sorted(c["cloud_models"].items(), key=lambda x: -x[1]):
            lines.append(f"  - {model} : {count} appels")
        lines.append("")

    # Councils
    co = councils_analysis
    if co["total_debates"] > 0:
        lines.append("## Councils")
        lines.append(f"Total debats : {co['total_debates']}")
        lines.append("")
        lines.append("| Heure | Participants | Mission |")
        lines.append("|-------|-------------|---------|")
        for d in co["details"]:
            ts = d["timestamp"][11:16] if d.get("timestamp") else "?"
            lines.append(f"| {ts} | {d['participants']} | {d['mission'][:60]} |")
        lines.append("")

    # Échecs détaillés
    if r["failures"]["total"] > 0:
        lines.append("## Echecs detailles")
        lines.append(f"Total : {r['failures']['total']}")
        lines.append("")
        if r["failures"]["by_type"]:
            lines.append("Par type :")
            for ftype, count in r["failures"]["by_type"].items():
                lines.append(f"  - {ftype} : {count}")
            lines.append("")
        lines.append("| Heure | Routine | Qualite | Type |")
        lines.append("|-------|---------|---------|------|")
        for f in r["failure_details"]:
            ts = f.get("timestamp", "?")
            ts_short = ts[11:16] if ts and len(ts) > 16 else ts
            lines.append(f"| {ts_short} | {f.get('intent', '?')} | {f.get('quality', '?')} | {f.get('failure_type', '?')} |")
        lines.append("")

    # Timeline
    if timeline_analysis:
        lines.append("## Timeline (activite par heure)")
        lines.append("| Heure | Routines | Events |")
        lines.append("|-------|----------|--------|")
        for hour, data in timeline_analysis.items():
            lines.append(f"| {hour}:00 | {data['routines']} | {data['events']} |")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Analyse automatique des logs de run Promethee."
    )
    parser.add_argument(
        "path",
        help="Chemin vers un fichier log ou un dossier contenant les logs."
    )
    parser.add_argument(
        "--format", "-f",
        choices=["markdown", "json"],
        default="markdown",
        help="Format de sortie (defaut: markdown)"
    )
    parser.add_argument(
        "--output", "-o",
        help="Fichier de sortie (defaut: stdout)"
    )
    parser.add_argument(
        "--date", "-d",
        help="Filtrer par date (YYYY-MM-DD, 'today', 'yesterday'). Defaut: tout."
    )

    args = parser.parse_args()

    # Filtrage par date si demande
    date_filter = _resolve_date(args.date) if args.date else None

    # Découverte des fichiers
    files = discover_logs(args.path, date_filter=date_filter)

    total_files = sum(len(v) for v in files.values())
    if total_files == 0:
        print(f"Aucun fichier log trouve dans : {args.path}", file=sys.stderr)
        sys.exit(1)

    print(f"Fichiers decouverts :", file=sys.stderr)
    for cat, paths in files.items():
        if paths:
            print(f"  {cat}: {len(paths)} fichier(s)", file=sys.stderr)

    # Parsing
    all_events = []
    for fp in files["promethee"]:
        print(f"  Parsing {os.path.basename(fp)}...", file=sys.stderr)
        all_events.extend(parse_promethee_log(fp))

    talk_events = []
    for fp in files["talk"]:
        talk_events.extend(parse_talk_log(fp))

    interface_events = []
    for fp in files["interface"]:
        interface_events.extend(parse_interface_log(fp))

    # Post-filtre : ne garder que les evenements de la date cible
    if date_filter:
        all_events = [
            e for e in all_events
            if e.get("timestamp", "").startswith(date_filter)
        ]
        print(f"  Filtre date={date_filter} : {len(all_events)} evenements retenus", file=sys.stderr)

    if not all_events:
        print("Aucun evenement parse depuis les logs.", file=sys.stderr)
        sys.exit(1)

    print(f"  {len(all_events)} evenements parses depuis promethee.log", file=sys.stderr)
    if talk_events:
        print(f"  {len(talk_events)} evenements parses depuis talk logs", file=sys.stderr)
    if interface_events:
        print(f"  {len(interface_events)} evenements parses depuis interface logs", file=sys.stderr)

    # Analyse
    routines = analyze_routines(all_events)
    cloud = analyze_cloud(all_events)
    grimoire = analyze_grimoire(all_events)
    councils = analyze_councils(all_events)
    timeline = analyze_timeline(all_events)

    # Rapport
    report = generate_report(routines, cloud, grimoire, councils, timeline, args.format)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\nRapport ecrit dans : {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
