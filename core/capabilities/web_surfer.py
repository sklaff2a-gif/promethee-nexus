import os
import logging
from serpapi import GoogleSearch
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

try:
    from config import Config
except ImportError:
    class Config: SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")

class WebSurfer:
    """
    Capacité V2: Recherche Hybride (SerpApi Google -> Fallback DuckDuckGo)
    """
    def __init__(self):
        self.logger = logging.getLogger("WebSurfer")
        self.api_key = getattr(Config, "SERPAPI_API_KEY", None) or os.getenv("SERPAPI_API_KEY")

    def search(self, query: str, max_results=5):
        """Tente Google via SerpApi, sinon bascule sur DuckDuckGo."""
        
        # 1. TENTATIVE GOOGLE (SerpApi)
        if self.api_key:
            try:
                # self.logger.info(f"🔎 Recherche SerpApi (Google): {query}")
                params = {
                    "q": query,
                    "api_key": self.api_key,
                    "num": max_results,
                    "engine": "google"
                }
                search = GoogleSearch(params)
                results = search.get_dict()
                
                if "organic_results" in results:
                    summary = ""
                    for r in results["organic_results"][:max_results]:
                        title = r.get("title", "No Title")
                        link = r.get("link", "#")
                        snippet = r.get("snippet", "No content")
                        summary += f"- [GOOGLE] {title}\n  LIEN: {link}\n  INFO: {snippet}\n\n"
                    return summary
                    
            except Exception as e:
                print(f"⚠️ Erreur SerpApi: {e} -> Bascule sur DuckDuckGo.")
        
        # 2. FALLBACK DUCKDUCKGO (Gratuit)
        try:
            # self.logger.info(f"🦆 Recherche DuckDuckGo: {query}")
            ddg_results = DDGS().text(query, max_results=max_results)
            if not ddg_results:
                return "Aucun résultat trouvé (ni Google, ni DDG)."
            
            summary = ""
            for r in ddg_results:
                summary += f"- [DDG] {r['title']}\n  LIEN: {r['href']}\n  INFO: {r['body']}\n\n"
            return summary
            
        except Exception as e:
            return f"❌ ÉCHEC TOTAL RECHERCHE WEB: {e}"