import asyncio
import chromadb
import logging
import os
import shutil
import uuid
import time
from datetime import datetime
from typing import List, Dict, Any

logger = logging.getLogger("VectorStore")

class ChromaMemoryManager:
    _instances: Dict[str, "ChromaMemoryManager"] = {}

    @classmethod
    def get_instance(cls, project_id: str = "default") -> "ChromaMemoryManager":
        if project_id not in cls._instances:
            cls._instances[project_id] = ChromaMemoryManager(project_id)
        return cls._instances[project_id]

    @classmethod
    def reset_all(cls):
        """Nettoie toutes les instances (pour les tests)."""
        cls._instances.clear()

    def __init__(self, project_id: str = "default"):
        self.project_id = project_id

        # Migration automatique : ancien format → nouveau format
        if project_id == "default":
            old_path = os.path.join(".", "memory", "chroma_db")
            new_path = os.path.join(".", "memory", "default", "chroma_db")
            if os.path.exists(old_path) and not os.path.exists(new_path):
                print(f"🔄 [MÉMOIRE] Migration détectée : déplacement de memory/chroma_db/ → memory/default/chroma_db/")
                os.makedirs(os.path.dirname(new_path), exist_ok=True)
                shutil.move(old_path, new_path)
                print(f"✅ [MÉMOIRE] Migration terminée.")

        # Chemin persistant isolé par projet (utilise Config.CHROMA_PERSIST_PATH comme base)
        try:
            from config import Config
            base_dir = os.path.dirname(getattr(Config, "CHROMA_PERSIST_PATH", os.path.join(".", "memory", "chroma_db")))
        except ImportError:
            base_dir = os.path.join(".", "memory")
        self.db_path = os.path.join(base_dir, project_id, "chroma_db")
        os.makedirs(self.db_path, exist_ok=True)

        # Initialisation du client (fallback EphemeralClient si PersistentClient échoue)
        try:
            self.client = chromadb.PersistentClient(path=self.db_path)
        except Exception as e:
            logger.warning(f"PersistentClient échoué ({e}), fallback EphemeralClient (mémoire non persistante)")
            self.client = chromadb.EphemeralClient()

        # Lock asyncio pour les opérations d'écriture composées (purge, health check)
        # Lazy-init : recréé si l'event loop change (Smart Restart exit 65)
        self._lock = None
        self._lock_loop_id = None

        # Collections de base (casiers de mémoire)
        self.collections = {
            "collective_wisdom": self.client.get_or_create_collection(name="collective_wisdom"),
            "code_snippets": self.client.get_or_create_collection(name="code_snippets")
        }
        print(f"🧠 [MÉMOIRE] ChromaDB chargé (projet={project_id}) : {list(self.collections.keys())}")

    def _get_lock(self) -> asyncio.Lock:
        """Lazy-init du lock asyncio. Recréé si l'event loop change (Smart Restart)."""
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            return asyncio.Lock()
        if self._lock is None or self._lock_loop_id != loop_id:
            self._lock = asyncio.Lock()
            self._lock_loop_id = loop_id
        return self._lock

    def _get_collection(self, collection_name: str):
        """Retourne la collection, en la créant à la demande si inconnue."""
        if collection_name not in self.collections:
            self.collections[collection_name] = self.client.get_or_create_collection(name=collection_name)
        return self.collections[collection_name]

    @staticmethod
    def _sanitize_metadata(metadatas: List[Dict]) -> List[Dict]:
        """Assure que toutes les valeurs metadata sont str/int/float/bool (exigence ChromaDB)."""
        clean = []
        for meta in metadatas:
            sanitized = {}
            for k, v in meta.items():
                if isinstance(v, (str, int, float, bool)):
                    sanitized[k] = v
                elif v is None:
                    sanitized[k] = ""
                else:
                    sanitized[k] = str(v)
            clean.append(sanitized)
        return clean

    @property
    def is_persistent(self) -> bool:
        return getattr(self.client.get_settings(), "is_persistent", False)

    def add_documents(self, documents: List[str], metadatas: List[Dict], ids: List[str], collection_name: str = "collective_wisdom"):
        """Ajoute des souvenirs dans une collection spécifique."""
        try:
            metadatas = self._sanitize_metadata(metadatas)
            col = self._get_collection(collection_name)
            col.add(documents=documents, metadatas=metadatas, ids=ids)
            return True
        except Exception as e:
            print(f"❌ Erreur Mémoire (Add): {e}")
            return False

    def query_documents(self, query_texts: List[str], n_results: int = None, collection_name: str = "collective_wisdom"):
        """Recherche dans une collection spécifique."""
        try:
            if n_results is None:
                from config import Config
                n_results = getattr(Config, "RAG_DEFAULT_N_RESULTS", 3)
            col = self._get_collection(collection_name)
            return col.query(query_texts=query_texts, n_results=n_results)
        except Exception as e:
            print(f"❌ Erreur Mémoire (Query): {e}")
            return None

    def query_with_metadata(self, query_texts: List[str], n_results: int = None, collection_name: str = "collective_wisdom"):
        """Comme query_documents mais inclut distances et metadatas."""
        try:
            if n_results is None:
                from config import Config
                n_results = getattr(Config, "RAG_DEFAULT_N_RESULTS", 3)
            col = self._get_collection(collection_name)
            return col.query(
                query_texts=query_texts,
                n_results=n_results,
                include=["documents", "metadatas", "distances"]
            )
        except Exception as e:
            print(f"❌ Erreur Mémoire (QueryMeta): {e}")
            return None

    def purge_expired(self, max_age_days: int = 90, collection_name: str = None) -> int:
        """Supprime les souvenirs plus vieux que max_age_days.

        Note: les timestamps sont stockés en str par remember(), donc on
        récupère tous les docs et on filtre côté Python.
        """
        cutoff = time.time() - max_age_days * 86400
        targets = [collection_name] if collection_name else list(self.collections.keys())
        total = 0
        for name in targets:
            try:
                col = self._get_collection(name)
                all_docs = col.get(include=["metadatas"])
                if not all_docs["ids"]:
                    continue
                expired_ids = []
                for doc_id, meta in zip(all_docs["ids"], all_docs["metadatas"]):
                    try:
                        ts = float(meta.get("timestamp", 0))
                        if ts < cutoff:
                            expired_ids.append(doc_id)
                    except (ValueError, TypeError):
                        pass
                if expired_ids:
                    col.delete(ids=expired_ids)
                    total += len(expired_ids)
            except Exception as e:
                print(f"❌ Erreur Mémoire (Purge {name}): {e}")
        return total

    def count_documents(self, collection_name: str = "collective_wisdom") -> int:
        """Retourne le nombre de documents dans une collection."""
        try:
            col = self._get_collection(collection_name)
            return col.count()
        except Exception as e:
            print(f"❌ Erreur Mémoire (Count): {e}")
            return 0

    def check_health(self) -> dict:
        """Diagnostic de santé ChromaDB. Léger, pas de LLM."""
        result = {
            "status": "healthy",
            "persistent": True,
            "collections": {},
            "probe_ok": False,
            "warnings": [],
            "timestamp": datetime.now().isoformat(),
        }

        # 1. Vérifier si on est en mode persistent
        result["persistent"] = getattr(self.client.get_settings(), "is_persistent", False)
        if not result["persistent"]:
            result["warnings"].append("Mode EphemeralClient (mémoire non persistante)")
            result["status"] = "degraded"

        # 2. Count par collection
        for name in list(self.collections.keys()):
            try:
                count = self.collections[name].count()
                result["collections"][name] = count
            except Exception as e:
                result["collections"][name] = -1
                result["warnings"].append(f"Collection {name} inaccessible: {e}")
                result["status"] = "degraded"

        # 3. Probe write/read/delete
        probe_id = "__health_probe__"
        try:
            probe_col = self.client.get_or_create_collection("health-probe")
            probe_col.add(
                documents=["health check probe"],
                metadatas=[{"type": "probe", "timestamp": str(time.time())}],
                ids=[probe_id],
            )
            read = probe_col.get(ids=[probe_id])
            if read and read["ids"] and read["ids"][0] == probe_id:
                result["probe_ok"] = True
            probe_col.delete(ids=[probe_id])
            self.client.delete_collection("health-probe")
        except Exception as e:
            result["warnings"].append(f"Probe échoué: {e}")
            result["status"] = "down" if not result["probe_ok"] else "degraded"

        # Si aucune collection accessible → down
        if result["collections"] and all(v == -1 for v in result["collections"].values()):
            result["status"] = "down"

        return result

    def purge_low_quality(self, min_length: int = 100, max_non_latin_ratio: float = 0.10,
                          collection_name: str = None) -> int:
        """Supprime les souvenirs de mauvaise qualité (trop courts, hallucinations non-latin).

        Args:
            min_length: longueur minimum du document (en chars)
            max_non_latin_ratio: ratio max de caractères non-latin (0.0-1.0)
            collection_name: collection cible (None = toutes)

        Returns:
            Nombre de documents supprimés.
        """
        targets = [collection_name] if collection_name else list(self.collections.keys())
        total = 0
        for name in targets:
            try:
                col = self._get_collection(name)
                all_docs = col.get(include=["documents"])
                if not all_docs["ids"]:
                    continue
                bad_ids = []
                for doc_id, doc in zip(all_docs["ids"], all_docs["documents"]):
                    if not doc:
                        bad_ids.append(doc_id)
                        continue
                    # Trop court
                    if len(doc.strip()) < min_length:
                        bad_ids.append(doc_id)
                        continue
                    # Ratio non-latin trop élevé
                    alpha_chars = [c for c in doc if c.isalpha()]
                    if alpha_chars:
                        non_latin = sum(1 for c in alpha_chars if ord(c) > 0x024F)
                        ratio = non_latin / len(alpha_chars)
                        if ratio > max_non_latin_ratio:
                            bad_ids.append(doc_id)
                            continue
                if bad_ids:
                    col.delete(ids=bad_ids)
                    total += len(bad_ids)
            except Exception as e:
                print(f"Erreur Memoire (Purge qualite {name}): {e}")
        return total

    async def async_purge_expired(self, max_age_days: int = 90, collection_name: str = None) -> int:
        """Version async de purge_expired(), protégée par le lock."""
        async with self._get_lock():
            return self.purge_expired(max_age_days, collection_name)

    async def async_purge_low_quality(self, min_length: int = 100, max_non_latin_ratio: float = 0.10,
                                       collection_name: str = None) -> int:
        """Version async de purge_low_quality(), protégée par le lock."""
        async with self._get_lock():
            return self.purge_low_quality(min_length, max_non_latin_ratio, collection_name)

    async def async_check_health(self) -> dict:
        """Version async de check_health(), protégée par le lock."""
        async with self._get_lock():
            return self.check_health()
