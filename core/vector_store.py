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

# Collections protegees des purges qualitatives/temporelles aveugles.
# Conçues pour stocker du contenu structurel (chunks AST de code, signatures,
# logs CI) dont la longueur typique est < 100 chars — incompatible avec les
# filtres min_length=100 et purge par age conçus pour le wisdom textuel.
# 11/05/2026 : decouverte que purge_low_quality(min_length=100) sans
# collection_name iterait sur TOUTES les collections et wipait source_code
# (2964 chunks AST < 100 chars supprimes silencieusement chaque nuit).
PROTECTED_COLLECTIONS = frozenset({
    "source_code",     # V15 RAG du code source (chunks AST)
    "code_snippets",   # snippets de code valides
    "ci_failures",     # logs CI bisect (timestamps + diff courts)
    "ci_successes",
    # V14.12 P4 (13/05) — mémoire sociale d'Alfred (résumés de café avec
    # metadata). Sujets et résumés courts (100-500 chars) qui passeraient
    # sous le seuil min_length=100 de purge_low_quality si non protégés.
    "social_memory",
})


# --- Shadow Reading (Phase 1, migration Memoire V2) — DESACTIVE PAR DEFAUT (07/06) ---
# Greffe passive : compare l'ancien retrieval (embedder ANGLAIS par defaut) a une
# collection temoin MULTILINGUE et logue les ecarts a froid. INVARIANT : query_documents
# retourne TOUJOURS l'ancien resultat ; le nouveau est observe, JAMAIS injecte.
SHADOW_READ_ENABLED = False   # Phase 1 TERMINEE (10/06) : preuve faite (97% mismatch sur 96 requetes).
                              # Shadow OFF (la double-lecture passive n'a plus de raison d'etre).
SHADOW_LOG_PATH = os.path.join("memory", "shadow_read_v2.jsonl")
SHADOW_COLLECTION_SUFFIX = "_v2_test"
SHADOW_EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# --- Phase 4 — FULL SWITCH (10/06) : le temoin MULTILINGUE devient la collection CANONIQUE ---
# Cause racine reglee : `collective_wisdom` etait embedde en ANGLAIS (MiniLM defaut) sur du
# contenu FRANCAIS -> exil linguistique (rappel casse ~95%). Apres Phase 1 (shadow, preuve),
# Phase 3 (canary FREE_TIME, stable) et le rattrapage du delta (10/06, temoin = sur-ensemble),
# on bascule TOUTES les lectures ET ecritures de `collective_wisdom` vers le temoin multilingue.
# L'ancien index anglais est GELE (backup + rollback). KILL-SWITCH : env MEM_V2_FULL_SWITCH=0.
MEM_V2_FULL_SWITCH = os.getenv("MEM_V2_FULL_SWITCH", "1") != "0"


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

    def _init_persistent_client(self, force_reset=False):
        """Initialise PersistentClient avec recovery automatique si database corrompue.

        Args:
            force_reset: si True, skip la tentative 1 et passe directement au backup+reset.
                         Utile quand heartbeat() passe mais les opérations (collections) échouent.
        """
        # Tentative 1 : ouverture normale (skip si force_reset)
        if not force_reset:
            try:
                client = chromadb.PersistentClient(path=self.db_path)
                client.heartbeat()  # Vérifier que la connexion fonctionne
                logger.info(f"[MÉMOIRE] ChromaDB PersistentClient actif ({self.db_path})")
                return client
            except Exception as e1:
                logger.warning(f"PersistentClient échoué ({e1}), tentative de recovery...")

        # Tentative 2 : backup complet + reset dossier (version incompatible ou tables corrompues)
        backup_dir = self.db_path + ".bak"
        try:
            if os.path.exists(self.db_path):
                if os.path.exists(backup_dir):
                    shutil.rmtree(backup_dir, ignore_errors=True)
                shutil.copytree(self.db_path, backup_dir)
                shutil.rmtree(self.db_path)
            os.makedirs(self.db_path, exist_ok=True)
            logger.info(f"Database incompatible sauvegardée → {backup_dir}, recréation...")
            client = chromadb.PersistentClient(path=self.db_path)
            client.heartbeat()
            print(f"🔧 [MÉMOIRE] Database recréée avec succès (backup: {backup_dir})")
            return client
        except Exception as e2:
            logger.warning(f"Recovery PersistentClient échoué ({e2})")

        # Tentative 3 : dernier recours — EphemeralClient
        logger.warning(f"[MÉMOIRE] ⚠️ Fallback EphemeralClient — MÉMOIRE NON PERSISTANTE (path tenté: {self.db_path})")
        return chromadb.EphemeralClient()

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
        self.db_path = os.path.abspath(os.path.join(base_dir, project_id, "chroma_db"))
        os.makedirs(self.db_path, exist_ok=True)
        logger.info(f"[MÉMOIRE] ChromaDB path: {self.db_path}")

        # Initialisation du client (avec recovery si database corrompue)
        self.client = self._init_persistent_client()

        # Lock asyncio pour les opérations d'écriture composées (purge, health check)
        # Lazy-init : recréé si l'event loop change (Smart Restart exit 65)
        self._lock = None
        self._lock_loop_id = None

        # Collections de base (casiers de mémoire)
        # Try/except : si le client passe heartbeat() mais échoue sur les collections
        # (ex: acquire_write table manquante après crash), on force un reset complet.
        try:
            self.collections = {
                "collective_wisdom": self.client.get_or_create_collection(name="collective_wisdom"),
                "code_snippets": self.client.get_or_create_collection(name="code_snippets")
            }
        except Exception as e:
            logger.warning(f"Collections échouées ({e}), force reset database...")
            self.client = self._init_persistent_client(force_reset=True)
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

    def _canonical_collection(self, collection_name: str):
        """Phase 4 FULL SWITCH (10/06) : SOURCE UNIQUE de verite pour resoudre une collection.

        Sous MEM_V2_FULL_SWITCH, `collective_wisdom` EST le temoin MULTILINGUE (canonique) ->
        toutes les operations (count, record_recall, purge_expired/low_quality) DOIVENT le
        cibler, sinon on ecrirait au temoin mais on purgerait/compterait l'ancien (gele).
        Filet : embedder multilingue indispo -> on retombe sur la collection normale."""
        if MEM_V2_FULL_SWITCH and collection_name == "collective_wisdom":
            col = self._get_shadow_collection(collection_name + SHADOW_COLLECTION_SUFFIX)
            if col is not None:
                return col
        return self._get_collection(collection_name)

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
        """Ajoute des souvenirs dans une collection spécifique.

        Phase 4 FULL SWITCH (10/06) : les ecritures de `collective_wisdom` vont desormais
        dans le temoin MULTILINGUE (collection canonique), pour que les nouveaux souvenirs
        soient embeddes en francais. L'ancien index anglais est GELE. Filet : si l'embedder
        multilingue est indispo, on retombe sur l'ancien (on ne perd jamais une ecriture)."""
        try:
            metadatas = self._sanitize_metadata(metadatas)
            col = self._canonical_collection(collection_name)   # full switch -> temoin multilingue
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
            # --- FULL SWITCH (Phase 4) ou CANARY (Phase 3, FREE_TIME) : servir le temoin
            # MULTILINGUE en EXCLUSIF (espaces vectoriels incomparables -> pas de fusion). On
            # passe par _get_shadow_collection (embedder francais), JAMAIS par _get_collection
            # (qui re-embedderait la requete en anglais). Filet : neuf vide/erreur -> fallback
            # ancien ci-dessous. Sous MEM_V2_FULL_SWITCH la condition est globale (toutes les
            # lectures), pas seulement FREE_TIME.
            try:
                from core.context import canary_mem_v2
                _canary = canary_mem_v2.get()
            except Exception:
                _canary = False
            if (MEM_V2_FULL_SWITCH or _canary) and collection_name == "collective_wisdom":
                shadow_col = self._get_shadow_collection(collection_name + SHADOW_COLLECTION_SUFFIX)
                if shadow_col is not None:
                    try:
                        result_new = shadow_col.query(query_texts=query_texts, n_results=n_results)
                        if result_new and result_new.get("ids") and result_new["ids"] and result_new["ids"][0]:
                            return result_new   # EXCLUSIF : 100% multilingue
                    except Exception:
                        pass   # neuf en echec -> on retombe sur l'ancien (securite)
            col = self._get_collection(collection_name)
            result_old = col.query(query_texts=query_texts, n_results=n_results)  # MAITRE par defaut
            # --- GREFFE SHADOW READING (Phase 1) — fantome passif, n'altere JAMAIS result_old ---
            if SHADOW_READ_ENABLED:
                self._shadow_observe(query_texts, n_results, collection_name, result_old)
            return result_old
        except Exception as e:
            print(f"❌ Erreur Mémoire (Query): {e}")
            return None

    def query_with_metadata(self, query_texts: List[str], n_results: int = None, collection_name: str = "collective_wisdom"):
        """Comme query_documents mais inclut distances et metadatas."""
        try:
            if n_results is None:
                from config import Config
                n_results = getattr(Config, "RAG_DEFAULT_N_RESULTS", 3)
            col = self._canonical_collection(collection_name)   # full switch -> temoin multilingue
            return col.query(
                query_texts=query_texts,
                n_results=n_results,
                include=["documents", "metadatas", "distances"]
            )
        except Exception as e:
            print(f"❌ Erreur Mémoire (QueryMeta): {e}")
            return None

    # --- Shadow Reading (Phase 1) : fantomes passifs, jamais appeles si SHADOW_READ_ENABLED=False ---
    def _shadow_observe(self, query_texts, n_results, collection_name, result_old):
        """TRY/EXCEPT BORG : interroge la collection temoin multilingue, compare a
        result_old, logue l'ecart en JSONL. AUCUNE exception ne remonte vers query_documents."""
        try:
            shadow_col = self._get_shadow_collection(collection_name + SHADOW_COLLECTION_SUFFIX)
            if shadow_col is None:
                return
            import json
            t0 = time.perf_counter()
            result_new = shadow_col.query(query_texts=query_texts, n_results=n_results)
            lat_new = (time.perf_counter() - t0) * 1000.0
            old_ids = ((result_old or {}).get("ids") or [[]])[0]
            new_ids = ((result_new or {}).get("ids") or [[]])[0]
            rec = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "collection": collection_name,
                "query": (query_texts[0] if query_texts else "")[:80],
                "old_ids": old_ids, "new_ids": new_ids,
                "overlap": len(set(old_ids) & set(new_ids)),
                "mismatch": set(old_ids) != set(new_ids),
                "lat_new_ms": round(lat_new, 2),
            }
            with open(SHADOW_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            try:   # le BORG ne laisse RIEN fuir, pas meme l'echec d'ecriture du log
                with open(SHADOW_LOG_PATH + ".err", "a", encoding="utf-8") as f:
                    f.write(f"[shadow] {e}\n")
            except Exception:
                pass

    def _get_shadow_collection(self, shadow_name):
        """Recupere/cree la collection temoin avec l'embedder MULTILINGUE. Retourne None
        si l'embedder n'est pas dispo (le shadow s'abstient proprement, sans casser)."""
        if not hasattr(self, "_shadow_collections"):
            self._shadow_collections = {}
        if shadow_name in self._shadow_collections:
            return self._shadow_collections[shadow_name]
        try:
            from chromadb.utils import embedding_functions
            ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=SHADOW_EMBED_MODEL)
            col = self.client.get_or_create_collection(name=shadow_name, embedding_function=ef)
            self._shadow_collections[shadow_name] = col
            return col
        except Exception:
            self._shadow_collections[shadow_name] = None   # cache l'echec : pas de retry par requete
            return None

    def add_premium_lesson(self, text, concepts=None, origin_ts=None, lesson_id=None):
        """V2 (08/06) — CABLE `!grave` -> tier PREMIUM. Upsert une lecon CERTIFIEE par le gate
        humain (JM) dans la collection temoin v2 (embedder MULTILINGUE deja en RAM), avec le
        passeport PREMIUM + label [CERTIFIE]. Immunisee contre l'oubli PASSIF, revisable par
        preuve contraire. Borg : ne casse JAMAIS `!grave`. Ne s'active que si SHADOW_READ_ENABLED
        (le tier v2 ne vit que tant que le canal shadow est ouvert).
        Retourne l'id ecrit, ou None (desactive / echec / lecon trop courte).
        Phase 4 : actif si le full switch OU le shadow est en cours (le temoin est canonique)."""
        if not (MEM_V2_FULL_SWITCH or SHADOW_READ_ENABLED):
            return None
        try:
            if not text or len(text.strip()) < 20:
                return None
            col = self._get_shadow_collection("collective_wisdom" + SHADOW_COLLECTION_SUFFIX)
            if col is None:
                return None
            import time as _t
            ts = origin_ts if origin_ts is not None else _t.time()
            pid = lesson_id or f"premium_lesson_ts_{int(float(ts))}"   # id STABLE (timestamp, pas index FIFO)
            meta = {
                "tier_status": "PREMIUM",
                "is_flagged": False,
                "injected_label": "[CERTIFIE]",
                "source": "lesson_certified",
                "origin_ts": str(ts),
                "concepts": ",".join(concepts or [])[:200],
            }
            col.upsert(documents=[text.strip()], metadatas=[meta], ids=[pid])
            logger.info(f"[VECTOR V2] lecon PREMIUM cablee -> {pid} (tier=PREMIUM, multilingue)")
            return pid
        except Exception as e:
            logger.warning(f"[VECTOR V2] add_premium_lesson echoue (non bloquant): {e}")
            return None

    def record_recall(self, doc_id: str, collection_name: str = "collective_wisdom"):
        """Incrémente le compteur de rappel d'un document."""
        try:
            col = self._canonical_collection(collection_name)
            result = col.get(ids=[doc_id], include=["metadatas"])
            if result and result["metadatas"] and result["metadatas"][0]:
                meta = result["metadatas"][0]
                meta["recall_count"] = int(meta.get("recall_count", 0)) + 1
                meta["last_recalled_at"] = str(time.time())
                col.update(ids=[doc_id], metadatas=[meta])
        except Exception:
            pass

    def purge_expired(self, max_age_days: int = 90, collection_name: str = None) -> int:
        """Supprime les souvenirs plus vieux que max_age_days.

        Note: les timestamps sont stockés en str par remember(), donc on
        récupère tous les docs et on filtre côté Python.
        """
        cutoff = time.time() - max_age_days * 86400
        # Defense intrinseque : si pas de collection_name explicite, exclure
        # les collections protegees (code source, snippets, CI logs).
        if collection_name is None:
            targets = [n for n in self.collections.keys() if n not in PROTECTED_COLLECTIONS]
        elif collection_name in PROTECTED_COLLECTIONS:
            # Appel explicite sur une collection protegee : autorise mais loggue
            logger.warning(f"purge_expired explicite sur collection protegee '{collection_name}' — autorise")
            targets = [collection_name]
        else:
            targets = [collection_name]
        total = 0
        for name in targets:
            try:
                col = self._canonical_collection(name)
                all_docs = col.get(include=["metadatas"])
                if not all_docs["ids"]:
                    continue
                expired_ids = []
                for doc_id, meta in zip(all_docs["ids"], all_docs["metadatas"]):
                    try:
                        ts = float(meta.get("timestamp", 0))
                        if ts < cutoff:
                            # Protéger les mémoires fréquemment rappelées
                            recall_count = int(meta.get("recall_count", 0))
                            if recall_count >= 3:
                                continue
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
            col = self._canonical_collection(collection_name)
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
        # Defense intrinseque : si pas de collection_name explicite, exclure
        # les collections protegees. Conçue pour wisdom textuel, cette purge
        # est toxique sur source_code (chunks AST < 100 chars systematiquement).
        # 11/05/2026 : autopsie confirme que cet appel sans filtre avait wipe
        # 2964 chunks de source_code chaque nuit.
        if collection_name is None:
            targets = [n for n in self.collections.keys() if n not in PROTECTED_COLLECTIONS]
        elif collection_name in PROTECTED_COLLECTIONS:
            logger.warning(f"purge_low_quality explicite sur collection protegee '{collection_name}' — autorise")
            targets = [collection_name]
        else:
            targets = [collection_name]
        total = 0
        for name in targets:
            try:
                col = self._canonical_collection(name)
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
