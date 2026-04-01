"""tech_watch.py — Surveillance technologique pour Promethee.

Verifie l'etat de PRs/issues GitHub et detecte les changements
critiques pour le systeme (merges, nouvelles releases, activite).

Integre dans la routine VEILLE_IA de l'autonomy_engine.
Resultats caches pour eviter le rate limiting GitHub API.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("TechWatch")

# Chemin config
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
_CONFIG_FILE = os.path.join(_CONFIG_DIR, "tech_watch.json")
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory")
_CACHE_FILE = os.path.join(_CACHE_DIR, "tech_watch_cache.json")

# Rate limiting
_MIN_CHECK_INTERVAL_S = 3600  # 1h minimum entre deux checks complets
_REQUEST_TIMEOUT = 15  # secondes


def _load_config() -> Dict[str, Any]:
    """Charge la configuration des sources a surveiller."""
    if not os.path.isfile(_CONFIG_FILE):
        logger.warning(f"TECH_WATCH: Config introuvable: {_CONFIG_FILE}")
        return {"github_watches": []}
    with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_cache() -> Dict[str, Any]:
    """Charge le cache des derniers resultats."""
    if not os.path.isfile(_CACHE_FILE):
        return {"last_check": 0, "results": {}}
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"last_check": 0, "results": {}}


def _save_cache(cache: Dict[str, Any]):
    """Sauvegarde le cache."""
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logger.warning(f"TECH_WATCH: Erreur sauvegarde cache: {e}")


def _fetch_github_issue(repo: str, issue_number: int) -> Optional[Dict[str, Any]]:
    """Recupere l'etat d'une issue/PR GitHub via l'API REST (sans auth)."""
    import httpx
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Promethee-TechWatch"}
    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "number": data.get("number"),
                    "title": data.get("title", ""),
                    "state": data.get("state", "unknown"),
                    "updated_at": data.get("updated_at", "")[:10],
                    "comments": data.get("comments", 0),
                    "labels": [l.get("name", "") for l in data.get("labels", [])],
                    "is_pr": "pull_request" in data,
                    "merged": data.get("pull_request", {}).get("merged_at") is not None if "pull_request" in data else False,
                }
            elif resp.status_code == 403:
                logger.warning("TECH_WATCH: GitHub API rate limit atteint")
                return None
            else:
                logger.warning(f"TECH_WATCH: GitHub {resp.status_code} pour {repo}#{issue_number}")
                return None
    except Exception as e:
        logger.warning(f"TECH_WATCH: Erreur fetch {repo}#{issue_number}: {e}")
        return None


def _fetch_github_repo(repo: str) -> Optional[Dict[str, Any]]:
    """Recupere les stats d'un repo GitHub."""
    import httpx
    url = f"https://api.github.com/repos/{repo}"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Promethee-TechWatch"}
    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "stars": data.get("stargazers_count", 0),
                    "forks": data.get("forks_count", 0),
                    "updated_at": data.get("updated_at", "")[:10],
                    "description": data.get("description", ""),
                }
            return None
    except Exception:
        return None


def check_all(force: bool = False) -> Dict[str, Any]:
    """Verifie toutes les sources configurees.

    Retourne un rapport structure avec les changements detectes.
    Respecte le cache pour eviter le rate limiting.

    Args:
        force: ignorer le cache et forcer la verification
    """
    config = _load_config()
    cache = _load_cache()
    now = time.time()

    # Respecter l'intervalle minimum
    interval = config.get("check_interval_minutes", 60) * 60
    if not force and (now - cache.get("last_check", 0)) < max(interval, _MIN_CHECK_INTERVAL_S):
        logger.info("TECH_WATCH: Cache encore frais, skip")
        return {
            "from_cache": True,
            "last_check": cache.get("last_check", 0),
            "watches": cache.get("results", {}),
            "alerts": [],
        }

    watches = config.get("github_watches", [])
    results = {}
    alerts: List[str] = []

    for watch in watches:
        watch_id = watch.get("id", "unknown")
        repo = watch.get("repo", "")
        label = watch.get("label", watch_id)
        issues = watch.get("issues", [])
        priority = watch.get("priority", "medium")
        action = watch.get("action_on_merge", "")
        prev = cache.get("results", {}).get(watch_id, {})

        watch_result = {
            "label": label,
            "repo": repo,
            "priority": priority,
            "issues": {},
            "repo_stats": None,
        }

        # Checker chaque issue/PR
        for issue_num in issues:
            data = _fetch_github_issue(repo, issue_num)
            if data:
                watch_result["issues"][str(issue_num)] = data
                # Detecter les changements
                prev_issue = prev.get("issues", {}).get(str(issue_num), {})
                if prev_issue:
                    # PR mergee ?
                    if data.get("merged") and not prev_issue.get("merged"):
                        alert = f"MERGE DETECTE: {label} — {repo}#{issue_num} '{data['title']}'. Action: {action}"
                        alerts.append(alert)
                        logger.info(f"TECH_WATCH: {alert}")
                    # PR fermee ?
                    elif data.get("state") == "closed" and prev_issue.get("state") != "closed":
                        alert = f"FERME: {label} — {repo}#{issue_num} '{data['title']}'"
                        alerts.append(alert)
                    # Nouvelle activite ?
                    elif data.get("comments", 0) > prev_issue.get("comments", 0):
                        diff = data["comments"] - prev_issue["comments"]
                        alerts.append(f"ACTIVITE: {label} — {repo}#{issue_num} +{diff} commentaires")

        # Checker les stats du repo (si watch_stars)
        if watch.get("watch_stars"):
            repo_data = _fetch_github_repo(repo)
            if repo_data:
                watch_result["repo_stats"] = repo_data
                prev_stars = prev.get("repo_stats", {}).get("stars", 0)
                if repo_data["stars"] > prev_stars and prev_stars > 0:
                    diff = repo_data["stars"] - prev_stars
                    alerts.append(f"STARS: {label} — +{diff} stars ({repo_data['stars']} total)")

        results[watch_id] = watch_result

    # Sauvegarder le cache
    cache["last_check"] = now
    cache["results"] = results
    _save_cache(cache)

    report = {
        "from_cache": False,
        "last_check": now,
        "watches": results,
        "alerts": alerts,
        "total_watches": len(watches),
        "total_issues_checked": sum(len(w.get("issues", {})) for w in results.values()),
    }

    if alerts:
        logger.info(f"TECH_WATCH: {len(alerts)} alerte(s) detectee(s)")
        for a in alerts:
            print(f"   🔔 TECH_WATCH: {a}")
    else:
        logger.info("TECH_WATCH: Aucune alerte — tout stable")

    return report


def format_report(report: Dict[str, Any]) -> str:
    """Formate un rapport lisible pour le chat ou les logs."""
    lines = ["=== VEILLE TECHNOLOGIQUE ==="]

    if report.get("from_cache"):
        lines.append("(Resultats en cache — prochain check dans <1h)")
    else:
        lines.append(f"Check complet: {report.get('total_issues_checked', 0)} issues verifiees")

    alerts = report.get("alerts", [])
    if alerts:
        lines.append(f"\n*** {len(alerts)} ALERTE(S) ***")
        for a in alerts:
            lines.append(f"  ! {a}")

    for wid, data in report.get("watches", {}).items():
        label = data.get("label", wid)
        prio = data.get("priority", "?")
        lines.append(f"\n--- {label} [{prio}] ---")

        for inum, idata in data.get("issues", {}).items():
            state = idata.get("state", "?")
            merged = " [MERGED]" if idata.get("merged") else ""
            title = idata.get("title", "?")[:60]
            updated = idata.get("updated_at", "?")
            comments = idata.get("comments", 0)
            lines.append(f"  #{inum}: {state}{merged} | {updated} | {comments}c | {title}")

        stats = data.get("repo_stats")
        if stats:
            lines.append(f"  Repo: {stats.get('stars', 0)} stars | updated {stats.get('updated_at', '?')}")

    return "\n".join(lines)


def get_critical_alerts() -> List[str]:
    """Retourne les alertes critiques du dernier check (depuis le cache)."""
    cache = _load_cache()
    # Recalculer les alertes depuis le cache
    # Simplification : retourner les alertes du dernier rapport
    return cache.get("last_alerts", [])
