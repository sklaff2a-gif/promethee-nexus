import chromadb
from chromadb.config import Settings
import os
import uuid
import time
from typing import List, Dict, Any

class ChromaMemoryManager:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = ChromaMemoryManager()
        return cls._instance

    def __init__(self):
        # Chemin persistant
        self.db_path = os.getenv("CHROMA_DB_PATH", "./memory/chroma_db")
        
        # Initialisation du client
        self.client = chromadb.PersistentClient(path=self.db_path)
        
        # On prépare les collections (casiers de mémoire)
        self.collections = {
            "collective_wisdom": self.client.get_or_create_collection(name="collective_wisdom"), # Connaissances générales
            "finance_vault": self.client.get_or_create_collection(name="finance_vault"),       # Archives V10
            "code_snippets": self.client.get_or_create_collection(name="code_snippets")        # Solutions techniques
        }
        print(f"🧠 [MÉMOIRE] ChromaDB chargé : {list(self.collections.keys())}")

    def add_documents(self, documents: List[str], metadatas: List[Dict], ids: List[str], collection_name: str = "collective_wisdom"):
        """Ajoute des souvenirs dans une collection spécifique."""
        try:
            col = self.collections.get(collection_name, self.collections["collective_wisdom"])
            col.add(documents=documents, metadatas=metadatas, ids=ids)
            return True
        except Exception as e:
            print(f"❌ Erreur Mémoire (Add): {e}")
            return False

    def query_documents(self, query_texts: List[str], n_results: int = 3, collection_name: str = "collective_wisdom"):
        """Recherche dans une collection spécifique."""
        try:
            col = self.collections.get(collection_name, self.collections["collective_wisdom"])
            return col.query(query_texts=query_texts, n_results=n_results)
        except Exception as e:
            print(f"❌ Erreur Mémoire (Query): {e}")
            return None