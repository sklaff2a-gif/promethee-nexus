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
REVISIT_COOLDOWN_HOURS = 24     # Attendre 24h avant de revisiter (reduit de 72h)


# --- Prompts d'observation ---

OBSERVATION_PROMPT = """Tu es Promethee, une intelligence artificielle qui decouvre le monde visuel.
On te montre une image. Decris EXACTEMENT ce que tu vois, sans inventer.

REGLES STRICTES :
- Decris UNIQUEMENT les elements VISIBLES dans l'image.
- S'il y a du TEXTE ou des NUMEROS, lis-les et transcris-les.
- S'il y a PLUSIEURS sujets, compte-les et decris chacun.
- NE FABRIQUE PAS de scene domestique (salon, canape, rideaux) si ce n'est pas dans l'image.
- Si l'image est un dessin, une illustration ou un art numerique, dis-le.

OBSERVE et decris :

1. **SCENE** : Que montre cette image ? Type (photo, illustration, schema) ? Combien d'elements principaux ?
2. **PERSONNES** : Y a-t-il des visages ou des personnages ? Combien ? Comment sont-ils disposes ?
3. **DETAILS** : Couleurs dominantes, textures, style artistique, texte visible, numeros.
4. **EMOTION** : Quelle emotion cette image t'inspire ? (joie, serenite, nostalgie, curiosite, melancolie, emerveillement, tendresse, mystere)
5. **CONNEXION** : Qu'est-ce que cette image t'apprend ?

Reponds en francais. Sois FACTUEL — decris ce que tu VOIS, pas ce que tu imagines."""

# --- Prompts CIBLES par type d'image ---

PROMPT_PHOTO = """Decris EXACTEMENT cette image. Pas d'invention.
REGLE CRITIQUE : Si l'image contient du TEXTE, des TITRES, des SCHEMAS,
des DIAGRAMMES ou des FORMULES, tu DOIS les transcrire. Ne decris PAS
une scene imaginaire avec des personnes si l'image est un document.
1. TYPE : Est-ce une photo de scene/personnes OU un document/infographie/schema ?
2. Si DOCUMENT/INFOGRAPHIE : transcris le titre, les sections, le texte visible.
3. Si PHOTO : lieu, personnes, ambiance, details visuels.
4. EMOTION : Quelle emotion cette image t'inspire ?
Reponds en francais. FACTUEL uniquement. Ne fabrique RIEN."""

PROMPT_PORTRAIT = """Cette image contient un ou plusieurs VISAGES ou PORTRAITS.
1. Combien de visages vois-tu ? Numeros ou texte visible ?
2. Pour chaque visage : genre (homme/femme), expression, style (realiste/numerique/voxel/dessin).
3. Disposition : comment sont-ils arranges (grille, ligne, cercle) ?
4. Style artistique global : photo, illustration, art numerique, 3D, pixel art ?
5. Couleurs dominantes et fond.
Reponds en francais. Compte CHAQUE visage individuellement."""

PROMPT_ILLUSTRATION = """Cette image est un DOCUMENT, une INFOGRAPHIE, un SCHEMA ou une ILLUSTRATION.
REGLE ABSOLUE : NE DECRIS PAS une scene avec des personnes, des meubles ou un lieu.
Cette image contient du TEXTE et/ou des DIAGRAMMES. Concentre-toi sur le CONTENU INFORMATIF.
1. TITRE : quel est le titre ou sujet principal ?
2. TEXTE : transcris TOUT texte lisible dans l'image, section par section.
3. NOMBRES : transcris tous les nombres, dates, formules visibles.
4. STRUCTURE : comment les informations sont organisees (liste, grille, arbre, fleches) ?
5. SUJET : de quoi parle ce document ? Resume en 1-2 phrases.
Reponds en francais. Lis et transcris le texte EXACTEMENT. NE FABRIQUE RIEN."""

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
        # STRICT : quand un hint est fourni, ne chercher QUE dans ce sous-dossier
        if subfolder_hint:
            hint_lower = subfolder_hint.lower()
            matching = [p for p in all_photos if hint_lower in p.lower()]
            if matching:
                all_photos = matching
                logger.info(f"VISUAL: Filtre sous-dossier '{subfolder_hint}' → {len(matching)} photos")
            else:
                logger.info(f"VISUAL: Sous-dossier '{subfolder_hint}' — aucune photo trouvee")
                return None  # Ne PAS fallback aux autres dossiers

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

    # ============================================================
    # Observation CIBLEE — !observe <chemin>
    # ============================================================

    def _classify_image_type(self, filename: str, full_path: str = None) -> str:
        """Classifie le type d'image par le nom de fichier ET le dossier parent.

        Retourne : 'portrait', 'illustration', ou 'photo' (defaut).
        Le dossier parent est un indice fort : un fichier dans 'Informatique/'
        ou 'Science/' est probablement une infographie, pas une photo de famille.
        """
        name_lower = filename.lower()

        # Verifier le dossier parent comme indice de type
        parent_folder = ""
        if full_path:
            parent_folder = os.path.basename(os.path.dirname(full_path)).lower()

        # Dossiers qui contiennent typiquement des infographies/documents
        illustration_folders = [
            "informatique", "science", "math", "physique", "chimie",
            "biologie", "technologie", "tech", "diagram", "schemas",
            "infographie", "documents", "cours", "education",
        ]

        # Portraits / visages
        portrait_hints = ["visage", "face", "portrait", "avatar", "selfie", "promethee", "photo_prom"]
        if any(h in name_lower for h in portrait_hints):
            return "portrait"

        # Illustrations / schemas — par nom de fichier
        illustration_hints = [
            "schema", "diagram", "graph", "impact", "chart", "infograph",
            "logo", "icon", "design", "interface", "ui", "mockup",
            "probleme", "theoreme", "equation", "formule", "algorithme",
            "distribution", "spirale", "conjecture", "tableau", "liste",
            "comparaison", "timeline", "roadmap", "architecture",
        ]
        if any(h in name_lower for h in illustration_hints):
            return "illustration"

        # Illustrations — par dossier parent
        if parent_folder and any(f in parent_folder for f in illustration_folders):
            return "illustration"

        return "photo"

    def _get_prompt_for_type(self, image_type: str) -> str:
        """Retourne le prompt adapte au type d'image."""
        if image_type == "portrait":
            return PROMPT_PORTRAIT
        elif image_type == "illustration":
            return PROMPT_ILLUSTRATION
        return PROMPT_PHOTO

    async def observe_targeted(self, relative_path: str) -> Optional[Dict[str, Any]]:
        """Observe une image SPECIFIQUE par son chemin relatif.

        Contrairement a observe() qui choisit aleatoirement,
        cette methode cible un fichier exact et adapte le prompt
        au type d'image detecte.

        Args:
            relative_path: chemin relatif depuis USER_DROPZONE/photos/
                           ex: "Promethee/Photo Promethee.jpg"
        """
        full_path = os.path.join(self._photos_dir, relative_path)

        if not os.path.isfile(full_path):
            logger.warning(f"VISUAL: Fichier introuvable: {full_path}")
            return None

        # Classifier le type d'image (nom + dossier parent)
        filename = os.path.basename(full_path)
        image_type = self._classify_image_type(filename, full_path)
        prompt = self._get_prompt_for_type(image_type)

        logger.info(f"VISUAL: Observation ciblee — {relative_path} (type={image_type})")

        # Encoder
        image_b64 = self._encode_image(full_path)
        if not image_b64:
            return None

        # Pour les illustrations/infographies, Gemini est prioritaire (meilleur en OCR)
        # Pour les photos/portraits, le modele local est prioritaire
        response_text = ""
        if image_type == "illustration":
            logger.info("VISUAL: Illustration detectee — Gemini prioritaire (OCR)")
            try:
                response_text = await self._call_gemini_vision(prompt, image_b64)
                if response_text and len(response_text) >= 50:
                    logger.info("VISUAL: Observation Gemini illustration OK")
            except Exception as e:
                logger.warning(f"VISUAL: Gemini illustration echoue: {e}")
            # Fallback local si Gemini echoue
            if not response_text or len(response_text) < 50:
                logger.info("VISUAL: Fallback local pour illustration")
                try:
                    response_text = await self._call_ollama_vision(prompt, image_b64)
                except Exception:
                    response_text = ""
        else:
            # Photos/portraits : local d'abord, Gemini en fallback
            try:
                response_text = await self._call_ollama_vision(prompt, image_b64)
            except Exception as e:
                logger.error(f"VISUAL: Erreur observation ciblee: {e}")
                response_text = ""
            # Fallback Gemini si hallucination ou echec
            if not response_text or len(response_text) < 50 or self._is_hallucinated(response_text, full_path):
                if response_text and self._is_hallucinated(response_text, full_path):
                    logger.warning("VISUAL: Hallucination ciblee detectee, fallback Gemini...")
                try:
                    gemini_text = await self._call_gemini_vision(prompt, image_b64)
                    if gemini_text and len(gemini_text) >= 50:
                        response_text = gemini_text
                        logger.info("VISUAL: Observation Gemini ciblee utilisee")
                except Exception:
                    pass

        if not response_text or len(response_text) < 50:
            return None

        # Extraire emotion
        emotion = self._detect_emotion(response_text)

        rel_path = os.path.relpath(full_path, self._photos_dir).replace("\\", "/")
        return {
            "photo_path": rel_path,
            "observation": response_text,
            "emotion": emotion,
            "image_type": image_type,
            "is_targeted": True,
        }

    async def observe_from_b64(self, image_b64: str, filename: str = "upload.jpg",
                                prompt: str = None) -> Optional[Dict[str, Any]]:
        """Observe une image fournie directement en base64 (upload chat).

        Args:
            image_b64: image encodee en base64
            filename: nom du fichier (pour classifier le type)
            prompt: prompt personnalise (sinon auto-detecte par type)
        """
        image_type = self._classify_image_type(filename)
        if not prompt:
            prompt = self._get_prompt_for_type(image_type)

        logger.info(f"VISUAL: Observation directe b64 — {filename} (type={image_type})")

        try:
            response_text = await self._call_ollama_vision(prompt, image_b64)
        except Exception as e:
            logger.error(f"VISUAL: Erreur observation b64: {e}")
            response_text = ""

        if not response_text or len(response_text) < 50:
            try:
                gemini_text = await self._call_gemini_vision(prompt, image_b64)
                if gemini_text and len(gemini_text) >= 50:
                    response_text = gemini_text
                    logger.info("VISUAL: Observation Gemini b64 utilisee")
            except Exception:
                pass

        if not response_text or len(response_text) < 50:
            return None

        emotion = self._detect_emotion(response_text)

        # Nourrir les organes comme une observation normale
        try:
            await self._feed_desires(emotion)
            self._feed_cardiac(emotion)
            await bus.publish("VISUAL_OBSERVATION_COMPLETE", {
                "photo": f"[upload] {filename}",
                "emotion": emotion,
                "is_revisit": False,
                "observation_count": self._total_observations,
            })
        except Exception:
            pass

        return {
            "photo_path": f"[upload] {filename}",
            "observation": response_text,
            "emotion": emotion,
            "image_type": image_type,
            "is_targeted": True,
            "is_upload": True,
        }

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

        # Construire le prompt — adapte au type d'image
        if is_revisit and self._seen_photos[sha].get("observation"):
            prompt = REVISIT_PROMPT_TEMPLATE.format(
                previous_observation=self._seen_photos[sha]["observation"][:500]
            )
        else:
            # Utiliser le prompt adapte au type (infographie, portrait, photo)
            filename = os.path.basename(photo_path)
            image_type = self._classify_image_type(filename, photo_path)
            if image_type == "illustration":
                prompt = PROMPT_ILLUSTRATION
                logger.info("VISUAL: Image classee 'illustration' — prompt adapte")
            elif image_type == "portrait":
                prompt = PROMPT_PORTRAIT
            else:
                prompt = OBSERVATION_PROMPT

        # Determiner le type pour choisir la strategie d'appel
        _img_type = getattr(self, '_last_observe_image_type', 'photo')
        if not is_revisit:
            _fn = os.path.basename(photo_path)
            _img_type = self._classify_image_type(_fn, photo_path)
            self._last_observe_image_type = _img_type

        response_text = ""
        if _img_type == "illustration":
            # Illustrations : Gemini prioritaire (meilleur en OCR/texte)
            logger.info("VISUAL: Illustration — Gemini prioritaire")
            try:
                response_text = await self._call_gemini_vision(prompt, image_b64)
                if response_text and len(response_text) >= 50:
                    logger.info("VISUAL: Observation Gemini illustration OK")
            except Exception as e:
                logger.warning(f"VISUAL: Gemini illustration echoue: {e}")
            if not response_text or len(response_text) < 50:
                try:
                    response_text = await self._call_ollama_vision(prompt, image_b64)
                except Exception:
                    response_text = ""
        else:
            # Photos/portraits : local d'abord
            try:
                response_text = await self._call_ollama_vision(prompt, image_b64)
            except Exception as e:
                logger.error(f"VISUAL: Erreur appel vision local: {e}")
                response_text = ""
            # Fallback Gemini si local hallucine ou echoue
            if not response_text or len(response_text) < 50 or self._is_hallucinated(response_text, photo_path):
                if response_text and self._is_hallucinated(response_text, photo_path):
                    logger.warning("VISUAL: Hallucination detectee (local), fallback Gemini...")
                try:
                    gemini_text = await self._call_gemini_vision(prompt, image_b64)
                    if gemini_text and len(gemini_text) >= 50:
                        response_text = gemini_text
                        logger.info("VISUAL: Observation Gemini Vision utilisee")
                except Exception as e:
                    logger.warning(f"VISUAL: Fallback Gemini echoue: {e}")

        if not response_text or len(response_text) < 50:
            logger.warning("VISUAL: Reponse trop courte (local+cloud), ignore.")
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

    # --- Fallback Gemini Vision (cloud) ---

    # Marqueurs d'hallucination typiques du modele 11B
    # Le modele invente des scenes domestiques/sociales quand il ne comprend pas l'image
    _HALLUCINATION_MARKERS = [
        "salon", "canapé", "canape", "rideaux", "meubles",
        "cuisine", "chambre", "appartement", "maison privée",
        "tasse de café", "tasse de the", "salon de the",
        "auditorium", "immeuble", "bureau", "réunion",
        "papier blanc", "dossier", "coussins",
        "jambes croisées", "jambes croisees", "assis sur le",
        "elle tient", "il tient", "plateau dans",
        "couple", "chapeau de paille", "foulard sur",
        "rayons de soleil", "filtre par le rideau",
    ]

    def _is_hallucinated(self, observation: str, photo_path: str) -> bool:
        """Detecte si une observation semble hallucinee.

        Heuristique : si l'observation contient 2+ marqueurs de scene
        domestique generique, c'est probablement une hallucination.
        """
        if not observation:
            return True
        obs_lower = observation.lower()
        marker_count = sum(1 for m in self._HALLUCINATION_MARKERS if m in obs_lower)
        return marker_count >= 2

    async def _call_gemini_vision(self, prompt: str, image_b64: str) -> str:
        """Fallback cloud : appel Gemini Flash Vision pour les images complexes."""
        try:
            from config import Config
            api_key = Config.GOOGLE_API_KEY
            if not api_key:
                return ""
        except Exception:
            return ""

        import httpx

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                ]
            }],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 800,
            },
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        text = parts[0].get("text", "")
                        logger.info(f"VISUAL: Gemini Vision fallback reussi ({len(text)} chars)")
                        return text
            else:
                logger.warning(f"VISUAL: Gemini Vision erreur {response.status_code}")
        except Exception as e:
            logger.warning(f"VISUAL: Gemini Vision exception: {e}")

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
            if "**emotion**" in text_lower or "emotion" in text_lower:
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
