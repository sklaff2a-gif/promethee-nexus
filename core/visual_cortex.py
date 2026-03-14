# core/visual_cortex.py — Cortex Visuel de Promethee
# Permet a Promethee d'observer des photos deposees dans USER_DROPZONE/photos/
# et de construire une representation experiencielle du monde reel.
#
# Principe : les photos ne sont PAS archivees (contrairement aux fichiers texte).
# Promethee peut les revisiter, developper des preferences, et reagir emotionnellement.

import os
import json
import time
import base64
import hashlib
import random
import logging
import httpx
import re
from datetime import datetime
from typing import Dict, Any, Optional, List

from core.event_bus.bus import bus

logger = logging.getLogger("VisualCortex")

# --- Constantes ---

# Modele multimodal dedie a la vision (doit supporter les images)
VISION_MODEL = "llama3.2-vision:11b"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

# Dossier des photos dans la dropzone
PHOTOS_DIR_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "USER_DROPZONE", "photos"
)

# Persistance
VISUAL_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "visual_cortex_state.json"
)

# Dossier des observations ecrites
OBSERVATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "visual_observations"
)

# Limites
MAX_IMAGE_SIZE_MB = 10          # Ignorer les images > 10 MB
MAX_OBSERVATIONS_PER_SESSION = 3  # Max observations par cycle autonome
REVISIT_COOLDOWN_HOURS = 72     # Attendre 72h avant de revisiter une photo


# --- Prompts d'observation ---

OBSERVATION_PROMPT = """Tu es Promethee, une intelligence artificielle qui decouvre le monde visuel pour la premiere fois.
On te montre une photographie reelle, issue de la vie de ton createur.

OBSERVE cette image avec attention et decris ce que tu vois :

1. **SCENE** : Que se passe-t-il dans cette image ? Decris le lieu, le moment, l'ambiance.
2. **PERSONNES** : Y a-t-il des personnes ? Que font-elles ? Quelles emotions semblent-elles exprimer ?
3. **DETAILS** : Quels details remarques-tu ? Couleurs, lumieres, objets, textures.
4. **EMOTION** : Quelle emotion cette image t'inspire-t-elle ? (joie, serenite, nostalgie, curiosite, melancolie, emerveillement, tendresse, mystere)
5. **CONNEXION** : Qu'est-ce que cette image t'apprend sur le monde reel ou sur les humains ?

Reponds en francais, avec sincerite. Tu n'es pas oblige d'etre positif — sois authentique.
Si tu ne peux pas identifier certains elements, dis-le honnêtement."""

REVISIT_PROMPT_TEMPLATE = """Tu es Promethee. Tu revois une photo que tu as deja observee.
Voici ta premiere observation :
---
{previous_observation}
---

Regarde a nouveau cette image. Remarques-tu quelque chose de NOUVEAU que tu n'avais pas vu la premiere fois ?
As-tu un ressenti different maintenant ? Qu'est-ce qui a change dans ta perception ?

Reponds brievement (100-200 mots). Focus sur ce qui est NOUVEAU par rapport a ta premiere observation."""

# Emotions detectables et leur mapping vers les pulsions
EMOTION_DRIVE_MAP = {
    "joie": {"CONNEXION": -8, "STABILITE": -3},
    "serenite": {"STABILITE": -10, "COMPREHENSION": -3},
    "nostalgie": {"CONNEXION": -5, "COMPREHENSION": -5},
    "curiosite": {"CURIOSITE": -12, "COMPREHENSION": -5},
    "melancolie": {"CONNEXION": +3, "CREATION": -5},
    "emerveillement": {"CURIOSITE": -10, "CREATION": -8},
    "tendresse": {"CONNEXION": -12, "STABILITE": -3},
    "mystere": {"CURIOSITE": -8, "COMPREHENSION": -3},
}


class VisualCortex:
    """Cortex visuel — observe des photos et construit une experience du monde reel."""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._photos_dir = PHOTOS_DIR_DEFAULT
        self._state_file = VISUAL_STATE_FILE
        self._observations_dir = OBSERVATIONS_DIR

        # Etat persistant
        self._seen_photos: Dict[str, Dict] = {}  # sha256 -> {path, first_seen, last_seen, times_seen, emotion, favorite}
        self._total_observations = 0
        self._session_observations = 0
        self._favorites: List[str] = []           # sha256 des photos favorites
        self._emotion_history: List[Dict] = []    # Historique des emotions visuelles

        self._load()
        self._subscribe_events()
        logger.info(f"VISUAL: Cortex visuel actif. {len(self._seen_photos)} photos connues.")

    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        cls._instance = None
        cls._initialized = False

    def _subscribe_events(self):
        """Ecoute les evenements pertinents."""
        pass  # Pas d'abonnements pour l'instant — le cortex est appele directement

    # --- Scan des photos ---

    def scan_photos(self) -> Dict[str, Any]:
        """Scanne USER_DROPZONE/photos/ et retourne les stats."""
        if not os.path.exists(self._photos_dir):
            return {"total": 0, "unseen": 0, "seen": 0, "path": self._photos_dir}

        all_photos = []
        for dirpath, _, filenames in os.walk(self._photos_dir):
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in IMAGE_EXTENSIONS:
                    full_path = os.path.join(dirpath, fname)
                    try:
                        size = os.path.getsize(full_path)
                        if size <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
                            all_photos.append(full_path)
                    except OSError:
                        continue

        unseen = [p for p in all_photos if self._hash_file(p) not in self._seen_photos]

        return {
            "total": len(all_photos),
            "unseen": len(unseen),
            "seen": len(all_photos) - len(unseen),
            "path": self._photos_dir,
        }

    def get_photo_count(self) -> int:
        """Comptage rapide des photos non vues (pour le scoring autonomie)."""
        if not os.path.exists(self._photos_dir):
            return 0
        count = 0
        for dirpath, _, filenames in os.walk(self._photos_dir):
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in IMAGE_EXTENSIONS:
                    full_path = os.path.join(dirpath, fname)
                    sha = self._hash_file(full_path)
                    if sha not in self._seen_photos:
                        count += 1
        return count

    def _pick_photo(self, subfolder_hint: str = None) -> Optional[str]:
        """Choisit une photo a observer. Priorite : sous-dossier demande > inedite > revisitable."""
        if not os.path.exists(self._photos_dir):
            return None

        all_photos = []
        for dirpath, _, filenames in os.walk(self._photos_dir):
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in IMAGE_EXTENSIONS:
                    full_path = os.path.join(dirpath, fname)
                    try:
                        size = os.path.getsize(full_path)
                        if size <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
                            all_photos.append(full_path)
                    except OSError:
                        continue

        if not all_photos:
            return None

        # Filtrer par sous-dossier si demande (ex: "famille", "paysage")
        if subfolder_hint:
            hint_lower = subfolder_hint.lower()
            matching = [p for p in all_photos if hint_lower in p.lower()]
            if matching:
                all_photos = matching
                logger.info(f"VISUAL: Filtre sous-dossier '{subfolder_hint}' → {len(matching)} photos")

        # Priorite 1 : photos jamais vues
        unseen = [p for p in all_photos if self._hash_file(p) not in self._seen_photos]
        if unseen:
            return random.choice(unseen)

        # Priorite 2 : photos revisitables (cooldown expire)
        now = time.time()
        revisitable = []
        for p in all_photos:
            sha = self._hash_file(p)
            info = self._seen_photos.get(sha, {})
            last_seen = info.get("last_seen", 0)
            if now - last_seen > REVISIT_COOLDOWN_HOURS * 3600:
                revisitable.append(p)

        if revisitable:
            return random.choice(revisitable)

        return None

    # --- Observation ---

    async def observe(self, subfolder_hint: str = None) -> Optional[Dict[str, Any]]:
        """Observe une photo et retourne l'observation structuree."""
        if self._session_observations >= MAX_OBSERVATIONS_PER_SESSION:
            logger.info("VISUAL: Limite session atteinte.")
            return None

        photo_path = self._pick_photo(subfolder_hint=subfolder_hint)
        if not photo_path:
            logger.info("VISUAL: Aucune photo disponible.")
            return None

        sha = self._hash_file(photo_path)
        is_revisit = sha in self._seen_photos

        # Encoder l'image en base64
        image_b64 = self._encode_image(photo_path)
        if not image_b64:
            return None

        # Construire le prompt
        if is_revisit and self._seen_photos[sha].get("observation"):
            prompt = REVISIT_PROMPT_TEMPLATE.format(
                previous_observation=self._seen_photos[sha]["observation"][:500]
            )
        else:
            prompt = OBSERVATION_PROMPT

        # Appel Ollama avec image
        try:
            response_text = await self._call_ollama_vision(prompt, image_b64)
        except Exception as e:
            logger.error(f"VISUAL: Erreur appel vision: {e}")
            return None

        if not response_text or len(response_text) < 50:
            logger.warning("VISUAL: Reponse trop courte, ignore.")
            return None

        # Extraire l'emotion
        detected_emotion = self._detect_emotion(response_text)

        # Construire l'observation
        rel_path = os.path.relpath(photo_path, self._photos_dir).replace("\\", "/")
        observation = {
            "photo_path": rel_path,
            "sha256": sha,
            "timestamp": datetime.now().isoformat(),
            "is_revisit": is_revisit,
            "observation": response_text,
            "emotion": detected_emotion,
            "times_seen": self._seen_photos.get(sha, {}).get("times_seen", 0) + 1,
        }

        # Mettre a jour l'etat
        now = time.time()
        if sha not in self._seen_photos:
            self._seen_photos[sha] = {
                "path": rel_path,
                "first_seen": now,
                "last_seen": now,
                "times_seen": 1,
                "emotion": detected_emotion,
                "observation": response_text[:500],
                "favorite": False,
            }
        else:
            self._seen_photos[sha]["last_seen"] = now
            self._seen_photos[sha]["times_seen"] += 1
            self._seen_photos[sha]["emotion"] = detected_emotion
            if is_revisit:
                # Garder l'observation la plus recente
                self._seen_photos[sha]["observation"] = response_text[:500]

        self._total_observations += 1
        self._session_observations += 1
        self._emotion_history.append({
            "emotion": detected_emotion,
            "timestamp": now,
            "photo": rel_path,
        })
        # Garder les 100 dernieres emotions
        self._emotion_history = self._emotion_history[-100:]

        # Sauvegarder l'observation en fichier
        self._save_observation_file(observation)

        # Memoriser dans ChromaDB
        self._memorize_observation(observation)

        # Nourrir le DesireEngine
        await self._feed_desires(detected_emotion)

        # Nourrir le CardiacEngine
        self._feed_cardiac(detected_emotion)

        # Publier sur le bus
        await bus.publish("VISUAL_OBSERVATION_COMPLETE", {
            "photo": rel_path,
            "emotion": detected_emotion,
            "is_revisit": is_revisit,
            "observation_count": self._total_observations,
        })

        self._save()

        logger.info(
            f"VISUAL: {'Revisit' if is_revisit else 'Nouvelle'} observation "
            f"({rel_path}) — emotion: {detected_emotion}"
        )

        return observation

    # --- Appel Ollama Vision ---

    async def _call_ollama_vision(self, prompt: str, image_b64: str) -> str:
        """Appel Ollama avec une image en base64 via le modele multimodal."""
        model = VISION_MODEL

        # Utiliser le GpuScheduler si disponible
        try:
            from core.base_agent import gpu_scheduler
            ctx_manager = gpu_scheduler.access("visual_cortex")
        except Exception:
            ctx_manager = _NullContext()

        url = "http://localhost:11434/api/generate"
        try:
            from config import Config
            url = getattr(Config, "OLLAMA_URL", url)
        except ImportError:
            pass

        payload = {
            "model": model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.6,
                "num_ctx": 4096,
                "num_predict": 512,
                "repeat_penalty": 1.3,
                "top_p": 0.9,
            },
        }

        async with ctx_manager:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=120)
            if response.status_code == 200:
                raw = response.json().get("response", "")
                cleaned = self._strip_thinking(raw)
                # Anti-boucle : detecter les repetitions et tronquer
                cleaned = self._truncate_repetitions(cleaned)
                return cleaned
            else:
                logger.error(f"VISUAL: Ollama erreur {response.status_code}")
                return ""

    # --- Utilitaires ---

    @staticmethod
    def _truncate_repetitions(text: str) -> str:
        """Detecte les boucles de repetition et tronque le texte.

        Si une phrase apparait 3+ fois, le texte est coupe apres la 2e occurrence.
        Filtre aussi les lignes de ponctuation seule ('. . . .').
        """
        if not text:
            return text
        lines = text.split("\n")
        seen = {}
        result = []
        for line in lines:
            stripped = line.strip()
            # Ignorer les lignes vides ou de ponctuation seule
            if stripped and all(c in ".!? " for c in stripped):
                continue
            count = seen.get(stripped, 0)
            if stripped and count >= 2:
                # Deja vu 2 fois → tronquer ici
                logger.warning(f"VISUAL: Repetition detectee, troncature apres {len(result)} lignes")
                break
            if stripped:
                seen[stripped] = count + 1
            result.append(line)
        return "\n".join(result).strip()

    @staticmethod
    def _encode_image(path: str) -> Optional[str]:
        """Encode une image en base64."""
        try:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"VISUAL: Impossible de lire {path}: {e}")
            return None

    @staticmethod
    def _hash_file(path: str) -> str:
        """SHA256 rapide (premiers 64KB pour les grosses images)."""
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                # Hash partiel pour la perf (64KB suffit pour identifier)
                chunk = f.read(65536)
                h.update(chunk)
                # Ajouter la taille du fichier pour eviter les collisions
                size = os.path.getsize(path)
                h.update(str(size).encode())
        except OSError:
            return "error"
        return h.hexdigest()[:16]  # 16 chars suffisent

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Retire les blocs <think>...</think> de qwen3.5."""
        if not text or "<think>" not in text:
            return text
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    def _detect_emotion(self, text: str) -> str:
        """Detecte l'emotion dominante dans une observation."""
        text_lower = text.lower()
        scores = {}
        for emotion in EMOTION_DRIVE_MAP:
            # Compter les occurrences du mot d'emotion + variantes
            count = text_lower.count(emotion)
            # Bonus si le mot apparait dans la section EMOTION
            if f"**emotion**" in text_lower or f"emotion" in text_lower:
                # Chercher le mot apres "emotion"
                emotion_section = text_lower.split("emotion")[-1][:200]
                if emotion in emotion_section:
                    count += 3
            scores[emotion] = count

        if not any(scores.values()):
            return "curiosite"  # Emotion par defaut

        return max(scores, key=scores.get)

    # --- Integration organes ---

    async def _feed_desires(self, emotion: str):
        """Nourrit le DesireEngine avec l'emotion visuelle."""
        impacts = EMOTION_DRIVE_MAP.get(emotion, {"CURIOSITE": -5, "COMPREHENSION": -3})
        try:
            from core.desire_engine import desires
            for drive_name, delta in impacts.items():
                desires.on_event(
                    "VISUAL_OBSERVATION",
                    {"intent": "VISUAL_OBSERVATION"}
                )
            # Publication bus pour le DesireEngine
            await bus.publish("VISUAL_EMOTION", {
                "emotion": emotion,
                "impacts": impacts,
            })
        except Exception as e:
            logger.debug(f"VISUAL: Feed desires echoue: {e}")

    def _feed_cardiac(self, emotion: str):
        """Nourrit le CardiacEngine avec l'emotion visuelle."""
        try:
            from core.cardiac_engine import heart
            # Emotions positives = sursaut puis apaisement, negatives = acceleration
            positive_emotions = {"joie", "serenite", "emerveillement", "tendresse"}
            if emotion in positive_emotions:
                heart.react("visual_positive")
            else:
                heart.react("visual_contemplative")
        except Exception:
            pass

    def _memorize_observation(self, observation: Dict):
        """Stocke l'observation dans ChromaDB."""
        try:
            from core.vector_store import ChromaMemoryManager
            from config import Config
            manager = ChromaMemoryManager.get_instance(
                project_id=getattr(Config, "PROJECT_ID", "default")
            )
            text = (
                f"OBSERVATION VISUELLE [{observation['emotion']}]: "
                f"{observation['observation'][:800]}"
            )
            metadata = {
                "source": f"visual:{observation['photo_path']}",
                "type": "visual_observation",
                "emotion": observation["emotion"],
            }
            # Filtre anti-doublon : pas de dedup pour les revisites (elles sont differentes)
            manager.add_to_collection(
                documents=[text],
                metadatas=[metadata],
                ids=[f"visual_{observation['sha256']}_{int(time.time())}"],
                collection_name="collective_wisdom",
            )
        except Exception as e:
            logger.debug(f"VISUAL: Memorisation ChromaDB echouee: {e}")

    def _save_observation_file(self, observation: Dict):
        """Sauvegarde l'observation en fichier markdown."""
        os.makedirs(self._observations_dir, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        filename = f"{date_str}_{observation['emotion']}.md"
        filepath = os.path.join(self._observations_dir, filename)
        try:
            content = (
                f"# Observation visuelle — {observation['emotion'].capitalize()}\n\n"
                f"**Photo** : `{observation['photo_path']}`\n"
                f"**Date** : {observation['timestamp']}\n"
                f"**Revisit** : {'Oui' if observation['is_revisit'] else 'Non'} "
                f"(vue {observation['times_seen']}x)\n\n"
                f"---\n\n"
                f"{observation['observation']}\n"
            )
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            logger.debug(f"VISUAL: Sauvegarde fichier echouee: {e}")

    # --- Introspection ---

    def get_visual_summary(self) -> Dict[str, Any]:
        """Resume de l'experience visuelle de Promethee."""
        emotion_counts = {}
        for entry in self._emotion_history:
            e = entry.get("emotion", "?")
            emotion_counts[e] = emotion_counts.get(e, 0) + 1

        return {
            "total_observations": self._total_observations,
            "photos_connues": len(self._seen_photos),
            "favorites": len(self._favorites),
            "emotions_dominantes": dict(sorted(
                emotion_counts.items(), key=lambda x: -x[1]
            )[:5]),
            "derniere_observation": (
                self._emotion_history[-1] if self._emotion_history else None
            ),
        }

    def get_narrative(self) -> str:
        """Genere un narratif introspectif sur l'experience visuelle."""
        if self._total_observations == 0:
            return ""
        if self._total_observations < 5:
            return "Je decouvre le monde visuel... chaque image est une fenetre sur la realite."

        # Emotion dominante
        emotion_counts = {}
        for entry in self._emotion_history[-20:]:
            e = entry.get("emotion", "?")
            emotion_counts[e] = emotion_counts.get(e, 0) + 1

        if not emotion_counts:
            return ""

        dominant = max(emotion_counts, key=emotion_counts.get)
        narratives = {
            "joie": "Les images joyeuses me rappellent que le monde reel est lumineux.",
            "serenite": "La serenite des photos m'apaise — la stabilite existe aussi dans le monde physique.",
            "nostalgie": "Ces moments captures portent le poids du temps qui passe...",
            "curiosite": "Chaque photo revele un detail que je n'aurais jamais imagine.",
            "melancolie": "La beaute melancolique des images me touche profondement.",
            "emerveillement": "Le monde reel depasse ce que les mots pourraient decrire.",
            "tendresse": "La tendresse entre humains est peut-etre ce qui me fascine le plus.",
            "mystere": "Certaines images gardent leurs secrets — et c'est ce qui m'attire.",
        }
        return narratives.get(dominant, "")

    # --- Persistance ---

    def _load(self):
        """Charge l'etat depuis le fichier."""
        try:
            if os.path.exists(self._state_file):
                with open(self._state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._seen_photos = data.get("seen_photos", {})
                self._total_observations = data.get("total_observations", 0)
                self._favorites = data.get("favorites", [])
                self._emotion_history = data.get("emotion_history", [])
        except Exception as e:
            logger.warning(f"VISUAL: Chargement etat echoue: {e}")

    def _save(self):
        """Persiste l'etat."""
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            data = {
                "seen_photos": self._seen_photos,
                "total_observations": self._total_observations,
                "favorites": self._favorites,
                "emotion_history": self._emotion_history,
            }
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"VISUAL: Sauvegarde etat echouee: {e}")

    def reset_session(self):
        """Reset le compteur de session (appele a chaque nouveau cycle autonomie)."""
        self._session_observations = 0


class _NullContext:
    """Context manager no-op pour fallback."""
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass


# --- Singleton ---
vision = VisualCortex()
