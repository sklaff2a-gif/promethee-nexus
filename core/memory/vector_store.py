# Core/memory.py
import chromadb
import os

# --- Placeholder for Core/config.py import ---
# This block ensures the code runs even if Core/config.py isn't fully set up yet.
# In a real application, you would ensure Core/config.py is properly defined.
try:
    from Core import config
except ImportError:
    # Fallback/Placeholder config if Core/config.py is not accessible
    class ConfigPlaceholder:
        CHROMA_PERSIST_PATH = os.getenv("CHROMA_PERSIST_PATH", "./data/chromadb")
    config = ConfigPlaceholder()
# --- End Placeholder ---

class ChromaMemoryManager:
    """
    ChromaMemoryManager provides a singleton interface for managing vector memory
    using ChromaDB. It allows adding, querying, and deleting documents from
    vector collections.
    """
    _instance = None
    _client = None
    _default_collection = None
    _initialized = False  # Flag to ensure __init__ runs only once for the singleton

    def __new__(cls, *args, **kwargs):
        """
        Ensures only one instance of ChromaMemoryManager is created (singleton pattern).
        """
        if cls._instance is None:
            cls._instance = super(ChromaMemoryManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, persist_directory: str = None, default_collection_name: str = "default_memory"):
        """
        Initializes the ChromaDB client and a default collection.
        This method is designed to run its core logic only once.

        Args:
            persist_directory (str, optional): Directory to persist ChromaDB data.
                                               Defaults to `config.CHROMA_PERSIST_PATH`.
            default_collection_name (str, optional): The name for the default collection.
                                                     Defaults to "default_memory".
        """
        if not self._initialized:
            self.persist_directory = persist_directory if persist_directory else config.CHROMA_PERSIST_PATH
            
            os.makedirs(self.persist_directory, exist_ok=True)
            self._client = chromadb.PersistentClient(path=self.persist_directory)

            self.default_collection_name = default_collection_name
            self._default_collection = self._client.get_or_create_collection(name=self.default_collection_name)
            
            print(f"ChromaDB Memory Manager initialized with persist_directory: {self.persist_directory} and default collection: '{self.default_collection_name}'")
            self._initialized = True

    @classmethod
    def get_instance(cls, persist_directory: str = None, default_collection_name: str = "default_memory"):
        """
        Retrieves the singleton instance of ChromaMemoryManager. If the instance
        has not been created yet, it will be initialized with the provided
        parameters (or defaults). Subsequent calls will return the same instance.
        """
        if cls._instance is None:
            cls._instance = cls(persist_directory=persist_directory, default_collection_name=default_collection_name)
        return cls._instance

    def get_client(self):
        """
        Returns the underlying ChromaDB client instance.
        """
        return self._client

    def get_collection(self, name: str, embedding_function=None):
        """
        Retrieves an existing collection or creates a new one.

        Args:
            name (str): The name of the collection.
            embedding_function: An optional embedding function for new collections.
                                If not provided, ChromaDB's default will be used.

        Returns:
            chromadb.Collection: The requested ChromaDB collection.
        """
        if embedding_function:
            return self._client.get_or_create_collection(name=name, embedding_function=embedding_function)
        return self._client.get_or_create_collection(name=name)

    def add_documents(self, documents: list[str], metadatas: list[dict], ids: list[str], collection_name: str = None):
        """
        Adds a batch of documents to a specified or the default collection.

        Args:
            documents (list[str]): A list of text documents to add.
            metadatas (list[dict]): A list of metadata dictionaries, one for each document.
            ids (list[str]): A list of unique IDs for the documents.
            collection_name (str, optional): The name of the collection to add to.
                                             If None, documents are added to the default collection.

        Raises:
            ValueError: If `documents`, `metadatas`, and `ids` do not have matching lengths.
        """
        if not (len(documents) == len(metadatas) == len(ids)):
            raise ValueError("Documents, metadatas, and ids must have the same length.")

        collection = self._default_collection
        if collection_name:
            collection = self.get_collection(collection_name)
        
        collection.add(documents=documents, metadatas=metadatas, ids=ids)
        print(f"Added {len(documents)} documents to collection '{collection.name}'")

    def query_documents(self, query_texts: list[str], n_results: int = 5, where: dict = None,
                        where_document: dict = None, collection_name: str = None):
        """
        Queries documents from a specified or the default collection based on similarity.

        Args:
            query_texts (list[str]): A list of query strings.
            n_results (int, optional): The number of similar results to return per query. Defaults to 5.
            where (dict, optional): A dictionary to filter results by metadata.
            where_document (dict, optional): A dictionary to filter results by document content.
            collection_name (str, optional): The name of the collection to query.
                                             If None, the default collection is queried.

        Returns:
            dict: The query results, including distances, metadatas, documents, and IDs.
        """
        collection = self._default_collection
        if collection_name:
            collection = self.get_collection(collection_name)

        results = collection.query(
            query_texts=query_texts,
            n_results=n_results,
            where=where,
            where_document=where_document
        )
        return results

    def delete_documents(self, ids: list[str] = None, where: dict = None, collection_name: str = None):
        """
        Deletes documents from a specified or the default collection.

        Args:
            ids (list[str], optional): A list of document IDs to delete.
            where (dict, optional): A dictionary to filter documents for deletion by metadata.
            collection_name (str, optional): The name of the collection to delete from.
                                             If None, documents are deleted from the default collection.

        Raises:
            ValueError: If neither `ids` nor `where` filter is provided.
        """
        collection = self._default_collection
        if collection_name:
            collection = self.get_collection(collection_name)
        
        if not ids and not where:
            raise ValueError("Either 'ids' or 'where' filter must be provided to delete documents.")

        collection.delete(ids=ids, where=where)
        print(f"Deleted documents from collection '{collection.name}'")

    def list_collections(self) -> list[chromadb.Collection]:
        """
        Lists all available collections in the ChromaDB instance.

        Returns:
            list[chromadb.Collection]: A list of ChromaDB collection objects.
        """
        return self._client.list_collections()

    def delete_collection(self, name: str):
        """
        Deletes a specific collection by name from the ChromaDB instance.

        Args:
            name (str): The name of the collection to delete.
        """
        try:
            self._client.delete_collection(name=name)
            print(f"Collection '{name}' deleted.")
            if self._default_collection and self._default_collection.name == name:
                self._default_collection = None  # Reset default if it was deleted
        except Exception as e:
            print(f"Error deleting collection '{name}': {e}")

    def reset(self):
        """
        Resets the entire ChromaDB client, deleting all collections and data.
        Use with extreme caution, as this operation is irreversible and will erase all stored data.
        After reset, the default collection will be recreated.
        """
        print("WARNING: Resetting ChromaDB client. All data will be irrevocably erased.")
        self._client.reset()
        # Recreate the default collection after a full reset
        self._default_collection = self._client.get_or_create_collection(name=self.default_collection_name)
        print(f"ChromaDB client reset. Default collection '{self.default_collection_name}' recreated.")

