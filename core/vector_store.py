import chromadb
import logging
import os
import shutil
import uuid
import time
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

        # Chemin persistant isolé par projet
        self.db_path = os.path.join(".", "memory", project_id, "chroma_db")
        os.makedirs(self.db_path, exist_ok=True)

        # Initialisation du client (fallback EphemeralClient si PersistentClient échoue)
        try:
            self.client = chromadb.PersistentClient(path=self.db_path)
        except Exception as e:
            logger.warning(f"PersistentClient échoué ({e}), fallback EphemeralClient (mémoire non persistante)")
            self.client = chromadb.EphemeralClient()

        # On prépare les collections de base (casiers de mémoire)
        self.collections = {
            "collective_wisdom": self.client.get_or_create_collection(name="collective_wisdom"),
            "finance_vault": self.client.get_or_create_collection(name="finance_vault"),
            "code_snippets": self.client.get_or_create_collection(name="code_snippets")
        }
        print(f"🧠 [MÉMOIRE] ChromaDB chargé (projet={project_id}) : {list(self.collections.keys())}")

    def _get_collection(self, collection_name: str):
        """Retourne la collection, en la créant à la demande si inconnue."""
        if collection_name not in self.collections:
            self.collections[collection_name] = self.client.get_or_create_collection(name=collection_name)
        return self.collections[collection_name]

    def add_documents(self, documents: List[str], metadatas: List[Dict], ids: List[str], collection_name: str = "collective_wisdom"):
        """Ajoute des souvenirs dans une collection spécifique."""
        try:
            col = self._get_collection(collection_name)
            col.add(documents=documents, metadatas=metadatas, ids=ids)
            return True
        except Exception as e:
            print(f"❌ Erreur Mémoire (Add): {e}")
            return False

    def query_documents(self, query_texts: List[str], n_results: int = 3, collection_name: str = "collective_wisdom"):
        """Recherche dans une collection spécifique."""
        try:
            col = self._get_collection(collection_name)
            return col.query(query_texts=query_texts, n_results=n_results)
        except Exception as e:
            print(f"❌ Erreur Mémoire (Query): {e}")
            return None

    def query_with_metadata(self, query_texts: List[str], n_results: int = 3, collection_name: str = "collective_wisdom"):
        """Comme query_documents mais inclut distances et metadatas."""
        try:
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
