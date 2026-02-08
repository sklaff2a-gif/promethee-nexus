import asyncio
import time
import random
import logging
from datetime import date
from core.orchestrator import orchestrator
from core.event_bus.bus import bus

logger = logging.getLogger("AutonomyEngine")

# Limite quotidienne de routines autonomes
MAX_DAILY_ROUTINES = 20

class AutonomyEngine:
    """
    AutonomyEngine V23.0 (Anti-Storm + Budget Edition)
    - Intègre un 'Cooldown' strict pour empêcher les boucles infinies (Event Storms).
    - Ne réagit plus aux messages du bus s'il est en cours de traitement.
    - Limite quotidienne de routines pour contrôler la consommation de crédits.
    """
    def __init__(self, idle_threshold_seconds=300):
        self.idle_threshold = idle_threshold_seconds
        self.last_user_interaction = time.time()
        self.is_running = False
        self.is_processing = False # VERROU DE SÉCURITÉ
        self.recent_context = []

        # Budget quotidien
        self.daily_count = 0
        self.last_reset_day = date.today()

        bus.subscribe("USER_COMMAND", self.reset_timer)

    def _check_daily_budget(self) -> bool:
        """Vérifie et reset le compteur quotidien. Retourne True si budget disponible."""
        today = date.today()
        if today != self.last_reset_day:
            self.daily_count = 0
            self.last_reset_day = today

        if self.daily_count >= MAX_DAILY_ROUTINES:
            logger.warning(f"[AUTONOMY] Budget quotidien atteint ({MAX_DAILY_ROUTINES} routines). Pause jusqu'à demain.")
            return False
        return True

    def reset_timer(self, event):
        self.last_user_interaction = time.time()
        if "mission" in event:
            self.recent_context.append(event["mission"][:50])
            if len(self.recent_context) > 5: self.recent_context.pop(0)

    async def start_loop(self):
        self.is_running = True
        print(f"   🧠 AUTONOMY: Moteur V23 (Anti-Storm + Budget) activé. Limite: {MAX_DAILY_ROUTINES} routines/jour.")

        while self.is_running:
            # On dort d'abord pour éviter le démarrage immédiat
            sleep_time = random.randint(600, 1200) # 10 à 20 minutes (Rythme lent)
            await asyncio.sleep(sleep_time)

            if orchestrator.kill_switch_active or self.is_processing:
                continue

            # Vérification budget quotidien
            if not self._check_daily_budget():
                continue

            idle_time = time.time() - self.last_user_interaction

            if idle_time > self.idle_threshold:
                self.is_processing = True # ON VERROUILLE
                try:
                    await self.trigger_expert_routine()
                    self.daily_count += 1
                    logger.info(f"[AUTONOMY] Routine {self.daily_count}/{MAX_DAILY_ROUTINES} du jour exécutée.")
                except Exception as e:
                    logger.warning(f"[AUTONOMY] Erreur Routine: {e}")
                finally:
                    # COOLDOWN FORCÉ : On attend 30s après une action avant de déverrouiller
                    await asyncio.sleep(30)
                    self.is_processing = False
                    self.last_user_interaction = time.time() # On reset pour laisser respirer

    async def trigger_expert_routine(self):
        routines = [
            {"agent": "evolution", "intent": "EXPANSION_CODE", "mission": "Analyse un fichier aléatoire. Propose une petite optimisation (typage/docstring)."},
            {"agent": "architect", "intent": "AUDIT_STRUCTURE", "mission": "Vérifie qu'aucun fichier temporaire (.tmp, .log) ne traîne à la racine."},
            {"agent": "researcher", "intent": "VEILLE_SILENCIEUSE", "mission": "Cherche une astuce Python 'One-Liner' utile et sauvegarde-la."}
        ]

        selected = random.choice(routines)
        agent = selected["agent"]

        print(f"   ✨ AUTONOMY: Routine [{selected['intent']}] -> [{agent.upper()}] ({self.daily_count + 1}/{MAX_DAILY_ROUTINES})")

        response = await orchestrator.dispatch_task(agent, {
            "mission": f"[MODE VEILLE] {selected['mission']}\nAgis de ta propre initiative.",
            "context": "PROTOCOLE_AUTONOMIE"
        })

        # Pas de publication sur le bus ici pour éviter le larsen (Feedback Loop)
        if response and response.get("status") == "success":
            print(f"   ✅ Fin Routine {agent.upper()}")

autonomy = AutonomyEngine(idle_threshold_seconds=300)
