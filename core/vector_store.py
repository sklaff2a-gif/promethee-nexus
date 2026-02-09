import chromadb
from chromadb.config import Settings
import os
import shutil
import uuid
import time
from typing import List, Dict, Any

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

        # Initialisation du client
        self.client = chromadb.PersistentClient(path=self.db_path)

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
