import os
import json
import logging
import time
from serpapi import GoogleSearch
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

try:
    from config import Config
except ImportError:
    class Config: SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")

# ─── Quota SERP API ──────────────────────────────────────────────────────────
# 250 appels/mois → ~8 appels/jour pour tenir 30 jours
SERP_DAILY_QUOTA = int(os.getenv("SERP_DAILY_QUOTA", "8"))
SERP_QUOTA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "memory", "serp_quota.json"
)


def _load_quota() -> dict:
    """Charge le compteur journalier SERP."""
    try:
        if os.path.exists(SERP_QUOTA_FILE):
            with open(SERP_QUOTA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Reset si jour différent
            if data.get("date") != time.strftime("%Y-%m-%d"):
                return {"date": time.strftime("%Y-%m-%d"), "count": 0, "total": data.get("total", 0)}
            return data
    except Exception:
        pass
    return {"date": time.strftime("%Y-%m-%d"), "count": 0, "total": 0}


def _save_quota(data: dict):
    """Sauvegarde atomique du compteur."""
    try:
        os.makedirs(os.path.dirname(SERP_QUOTA_FILE), exist_ok=True)
        tmp = SERP_QUOTA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SERP_QUOTA_FILE)
    except Exception:
        pass


class WebSurfer:
    """
    Capacité V2: Recherche Hybride (SerpApi Google -> Fallback DuckDuckGo)
    Quota journalier SERP_DAILY_QUOTA (défaut 8). Au-delà → DuckDuckGo auto.
    """
    def __init__(self):
        self.logger = logging.getLogger("WebSurfer")
        self.api_key = getattr(Config, "SERPAPI_API_KEY", None) or os.getenv("SERPAPI_API_KEY")

    def _can_use_serp(self) -> bool:
        """Vérifie si le quota journalier SERP permet un appel."""
        if not self.api_key:
            return False
        quota = _load_quota()
        return quota["count"] < SERP_DAILY_QUOTA

    def _record_serp_call(self):
        """Incrémente le compteur journalier SERP."""
        quota = _load_quota()
        quota["count"] += 1
        quota["total"] = quota.get("total", 0) + 1
        _save_quota(quota)
        remaining = SERP_DAILY_QUOTA - quota["count"]
        self.logger.info(f"SERP: appel {quota['count']}/{SERP_DAILY_QUOTA} aujourd'hui ({remaining} restants)")

    def get_quota_status(self) -> dict:
        """Retourne l'état du quota SERP."""
        quota = _load_quota()
        return {
            "date": quota["date"],
            "used_today": quota["count"],
            "daily_limit": SERP_DAILY_QUOTA,
            "remaining_today": max(0, SERP_DAILY_QUOTA - quota["count"]),
            "total_all_time": quota.get("total", 0),
        }

    def search(self, query: str, max_results=5):
        """Tente Google via SerpApi (si quota OK), sinon bascule sur DuckDuckGo."""

        # 1. TENTATIVE GOOGLE (SerpApi) — seulement si quota non épuisé
        if self._can_use_serp():
            try:
                params = {
                    "q": query,
                    "api_key": self.api_key,
                    "num": max_results,
                    "engine": "google"
                }
                search = GoogleSearch(params)
                results = search.get_dict()

                if "organic_results" in results:
                    self._record_serp_call()
                    summary = ""
                    for r in results["organic_results"][:max_results]:
                        title = r.get("title", "No Title")
                        link = r.get("link", "#")
                        snippet = r.get("snippet", "No content")
                        summary += f"- [GOOGLE] {title}\n  LIEN: {link}\n  INFO: {snippet}\n\n"
                    return summary

            except Exception as e:
                print(f"⚠️ Erreur SerpApi: {e} -> Bascule sur DuckDuckGo.")
        else:
            quota = _load_quota()
            self.logger.info(f"SERP: quota atteint ({quota['count']}/{SERP_DAILY_QUOTA}), fallback DDG")

        # 2. FALLBACK DUCKDUCKGO (Gratuit, illimité)
        try:
            ddg_results = DDGS().text(query, max_results=max_results)
            if not ddg_results:
                return "Aucun résultat trouvé (ni Google, ni DDG)."

            summary = ""
            for r in ddg_results:
                summary += f"- [DDG] {r['title']}\n  LIEN: {r['href']}\n  INFO: {r['body']}\n\n"
            return summary

        except Exception as e:
            return f"❌ ÉCHEC TOTAL RECHERCHE WEB: {e}"
