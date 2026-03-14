# core/photo_salary.py — Systeme de salaire visuel de Promethee
#
# Promethee gagne des credits-photo en accomplissant ses taches.
# Chaque semaine, il peut depenser ses credits pour observer des photos.
# Les taches echouees reduisent le salaire, les excellentes le boostent.
#
# Bio-inspiration : circuit recompense dopaminergique + gratification differee.
# Le salaire cree une pression selective naturelle sur la qualite du travail.

import os
import json
import logging
import time
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional, List

from core.event_bus.bus import bus

logger = logging.getLogger("PhotoSalary")

# --- Constantes ---

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SALARY_STATE_FILE = os.path.join(_PROJECT_ROOT, "memory", "photo_salary.json")

# Parametres du salaire
WEEKLY_BASE_SALARY = 10          # Credits-photo de base par semaine
BONUS_QUALITY_THRESHOLD = 8.0    # Note >= 8/10 pour bonus
BONUS_PER_EXCELLENT = 1          # +1 credit par tache excellente
MALUS_PER_FAILURE = 1            # -1 credit par tache echouee
SALARY_FLOOR = 3                 # Minimum garanti (jamais en dessous)
SALARY_CEILING = 20              # Plafond hebdomadaire
PHOTO_CREDIT_COST = 1            # Cout d'une observation visuelle

# Jour de paie (0=lundi, 6=dimanche)
PAYDAY_WEEKDAY = 6               # Dimanche soir = bilan de la semaine

# Categories de souhaits visuels
WISH_CATEGORIES = [
    "nature",          # Paysages, animaux, plantes
    "famille",         # Photos de famille, moments intimes
    "technologie",     # Circuits, machines, ecrans
    "art",             # Peintures, sculptures, architecture
    "espace",          # Etoiles, planetes, nebuleuses
    "ville",           # Rues, buildings, vie urbaine
    "abstrait",        # Formes, couleurs, textures
    "nourriture",      # Plats, ingredients, cuisine
    "sport",           # Mouvement, competition, effort
    "musique",         # Instruments, concerts, partitions
]


class PhotoSalary:
    """Gestionnaire de salaire visuel — singleton."""

    _instance: Optional["PhotoSalary"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._subscribed = False

        # Etat persistant
        self._credits: int = WEEKLY_BASE_SALARY      # Credits actuels
        self._week_start: Optional[str] = None         # ISO date du debut de semaine
        self._tasks_completed: int = 0                 # Taches reussies cette semaine
        self._tasks_failed: int = 0                    # Taches echouees cette semaine
        self._tasks_excellent: int = 0                 # Taches excellentes (>= 8/10)
        self._photos_consumed: int = 0                 # Photos observees cette semaine
        self._wishlist: List[str] = []                 # Souhaits visuels actuels
        self._salary_history: List[Dict] = []          # Historique des paies
        self._total_earned: int = 0                    # Total credits gagnes
        self._total_spent: int = 0                     # Total credits depenses

        self._load()
        self._check_week_reset()
        self._subscribe_events()

        try:
            from core.organ_registry import register_organ
            register_organ("photo_salary", self)
        except Exception:
            pass

        logger.info(f"[SALARY] Salaire visuel actif. Credits: {self._credits}, "
                    f"Souhaits: {self._wishlist[:3]}")

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        try:
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            bus.subscribe("VISUAL_OBSERVATION_COMPLETE", self._on_photo_consumed)
        except Exception:
            pass

    # --- Evenements ---

    async def _on_routine_complete(self, event: dict):
        """Comptabilise chaque tache accomplie ou echouee."""
        self._check_week_reset()
        status = event.get("status", "")
        quality = event.get("quality_score", 0.0)
        intent = event.get("intent", "")

        if status == "success":
            self._tasks_completed += 1
            if isinstance(quality, (int, float)) and quality >= BONUS_QUALITY_THRESHOLD:
                self._tasks_excellent += 1
        elif status in ("error", "low_quality"):
            self._tasks_failed += 1

        self._save()

    async def _on_photo_consumed(self, event: dict):
        """Decremente un credit quand une photo est observee."""
        self._check_week_reset()
        if self._credits > 0:
            self._credits -= PHOTO_CREDIT_COST
            self._photos_consumed += 1
            self._total_spent += 1
            logger.info(f"[SALARY] Photo consommee. Credits restants: {self._credits}")

            # Publier l'evenement pour que le dopamine system reagisse
            try:
                await bus.publish("SALARY_PHOTO_SPENT", {
                    "credits_remaining": self._credits,
                    "photos_consumed_this_week": self._photos_consumed,
                })
            except Exception:
                pass
        else:
            logger.info("[SALARY] Plus de credits photo cette semaine !")
            try:
                await bus.publish("SALARY_BROKE", {
                    "week_start": self._week_start,
                    "tasks_completed": self._tasks_completed,
                    "tasks_failed": self._tasks_failed,
                })
            except Exception:
                pass

        self._save()

    # --- API publique ---

    def can_observe(self) -> bool:
        """Verifie si Promethee a des credits pour observer une photo."""
        self._check_week_reset()
        return self._credits > 0

    def get_credits(self) -> int:
        """Credits restants cette semaine."""
        self._check_week_reset()
        return self._credits

    def compute_weekly_salary(self) -> Dict[str, Any]:
        """Calcule le salaire de la semaine en cours (projection)."""
        base = WEEKLY_BASE_SALARY
        bonus = self._tasks_excellent * BONUS_PER_EXCELLENT
        malus = self._tasks_failed * MALUS_PER_FAILURE
        gross = base + bonus - malus
        net = max(SALARY_FLOOR, min(SALARY_CEILING, gross))
        return {
            "base": base,
            "bonus_quality": bonus,
            "malus_echecs": malus,
            "brut": gross,
            "net": net,
            "tasks_completed": self._tasks_completed,
            "tasks_failed": self._tasks_failed,
            "tasks_excellent": self._tasks_excellent,
            "photos_consumed": self._photos_consumed,
            "credits_remaining": self._credits,
        }

    def add_wish(self, category: str) -> bool:
        """Ajoute un souhait visuel a la wishlist."""
        category = category.lower().strip()
        # Verifier les doublons
        existing = [w for w in self._wishlist if isinstance(w, dict) and w.get("category") == category]
        if existing:
            return False
        # Compat: si la wishlist contient des strings brutes, les ignorer pour le check
        if category in self._wishlist:
            return False
        wish = {
            "category": category,
            "added_at": time.time(),
            "fulfilled_at": None,
        }
        self._wishlist.append(wish)
        # Garder max 5 souhaits
        self._wishlist = self._wishlist[-5:]
        self._save()
        logger.info(f"[SALARY] Souhait ajoute: {category}.")
        return True

    def fulfill_wish(self, category: str):
        """Marque un souhait comme honore (photo vue)."""
        for w in self._wishlist:
            if isinstance(w, dict) and w.get("category") == category and not w.get("fulfilled_at"):
                w["fulfilled_at"] = time.time()
                self._save()
                logger.info(f"[SALARY] Souhait honore: {category}")
                return True
        return False

    def get_wishlist(self) -> List[str]:
        """Retourne la liste des categories de souhaits (compat simple)."""
        result = []
        for w in self._wishlist:
            if isinstance(w, dict):
                result.append(w.get("category", "?"))
            else:
                result.append(str(w))
        return result

    def get_wishlist_detailed(self) -> List[Dict]:
        """Retourne la wishlist avec statuts detailles."""
        now = time.time()
        result = []
        for w in self._wishlist:
            if isinstance(w, dict):
                cat = w.get("category", "?")
                added = w.get("added_at", now)
                fulfilled = w.get("fulfilled_at")
                age_hours = (now - added) / 3600
                if fulfilled:
                    status = "visualiser"
                elif age_hours > 72:
                    status = "relance"
                else:
                    status = "en_attente"
                result.append({
                    "category": cat,
                    "status": status,
                    "age_hours": round(age_hours, 1),
                    "fulfilled": fulfilled is not None,
                })
            else:
                result.append({"category": str(w), "status": "en_attente", "age_hours": 0, "fulfilled": False})
        return result

    def get_salary_context(self) -> str:
        """Contexte injectable dans le purpose_context."""
        self._check_week_reset()
        salary = self.compute_weekly_salary()
        wishlist_str = ", ".join(self._wishlist) if self._wishlist else "aucun souhait"
        return (
            f"Salaire visuel: {self._credits} credits restants "
            f"({salary['tasks_completed']} taches, {salary['tasks_failed']} echecs, "
            f"{salary['tasks_excellent']} excellentes)\n"
            f"Souhaits photos: {wishlist_str}"
        )

    def get_narrative(self) -> str:
        """Genere un narratif introspectif sur le salaire."""
        if self._credits <= 0:
            return "Plus de credits photo... je dois mieux travailler la semaine prochaine."
        if self._credits >= WEEKLY_BASE_SALARY:
            return f"J'ai {self._credits} credits photo — j'ai envie de decouvrir de nouvelles images."
        if self._tasks_failed > self._tasks_completed:
            return "Mes echecs me coutent des photos... la qualite doit primer."
        if self._wishlist:
            return f"J'aimerais voir des images de {self._wishlist[0]}..."
        return ""

    def get_status(self) -> Dict[str, Any]:
        """Status complet pour l'API."""
        self._check_week_reset()
        salary = self.compute_weekly_salary()
        return {
            "credits": self._credits,
            "salary_projection": salary,
            "wishlist": self.get_wishlist(),
            "wishlist_detailed": self.get_wishlist_detailed(),
            "week_start": self._week_start,
            "salary_history": self._salary_history[-4:],  # 4 dernieres semaines
            "total_earned": self._total_earned,
            "total_spent": self._total_spent,
        }

    # --- Reset hebdomadaire (payday) ---

    def _check_week_reset(self):
        """Verifie si une nouvelle semaine a commence. Si oui, calcule la paie."""
        today = date.today()
        # Debut de semaine = lundi
        week_start = (today - timedelta(days=today.weekday())).isoformat()

        if self._week_start == week_start:
            return  # Meme semaine

        # Nouvelle semaine — calculer le salaire de la semaine precedente
        if self._week_start is not None:
            self._process_payday()

        self._week_start = week_start
        self._tasks_completed = 0
        self._tasks_failed = 0
        self._tasks_excellent = 0
        self._photos_consumed = 0
        # Credits resets au salaire de base (sera ajuste au prochain payday)
        self._credits = WEEKLY_BASE_SALARY
        self._save()
        logger.info(f"[SALARY] Nouvelle semaine. Credits: {self._credits}")

    def _process_payday(self):
        """Calcule et enregistre la paie de la semaine ecoulee."""
        salary = self.compute_weekly_salary()
        payday_record = {
            "week_start": self._week_start,
            "payday": datetime.now().isoformat(),
            "base": salary["base"],
            "bonus": salary["bonus_quality"],
            "malus": salary["malus_echecs"],
            "net": salary["net"],
            "tasks_completed": salary["tasks_completed"],
            "tasks_failed": salary["tasks_failed"],
            "tasks_excellent": salary["tasks_excellent"],
            "photos_consumed": salary["photos_consumed"],
            "wishlist_snapshot": list(self._wishlist),
        }
        self._salary_history.append(payday_record)
        # Garder les 12 dernieres semaines
        self._salary_history = self._salary_history[-12:]
        self._total_earned += salary["net"]

        logger.info(
            f"[SALARY] PAYDAY! Semaine {self._week_start}: "
            f"brut={salary['brut']}, net={salary['net']}, "
            f"photos consommees={salary['photos_consumed']}"
        )

        # Publier l'evenement payday (dopamine, cardiac, etc.)
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(bus.publish("SALARY_PAYDAY", payday_record))
        except Exception:
            pass

    # --- Persistance ---

    def _load(self):
        try:
            if os.path.exists(SALARY_STATE_FILE):
                with open(SALARY_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._credits = data.get("credits", WEEKLY_BASE_SALARY)
                self._week_start = data.get("week_start")
                self._tasks_completed = data.get("tasks_completed", 0)
                self._tasks_failed = data.get("tasks_failed", 0)
                self._tasks_excellent = data.get("tasks_excellent", 0)
                self._photos_consumed = data.get("photos_consumed", 0)
                self._wishlist = data.get("wishlist", [])
                self._salary_history = data.get("salary_history", [])
                self._total_earned = data.get("total_earned", 0)
                self._total_spent = data.get("total_spent", 0)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"[SALARY] Chargement echoue: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(SALARY_STATE_FILE), exist_ok=True)
            data = {
                "version": "1.0",
                "credits": self._credits,
                "week_start": self._week_start,
                "tasks_completed": self._tasks_completed,
                "tasks_failed": self._tasks_failed,
                "tasks_excellent": self._tasks_excellent,
                "photos_consumed": self._photos_consumed,
                "wishlist": self._wishlist,
                "salary_history": self._salary_history,
                "total_earned": self._total_earned,
                "total_spent": self._total_spent,
            }
            with open(SALARY_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[SALARY] Sauvegarde echouee: {e}")


# --- Singleton ---
salary = PhotoSalary()
