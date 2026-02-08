import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging

# Configuration du logger
logger = logging.getLogger("performance_utils")

class AsyncTaskManager:
    """
    Gestionnaire de tâches asynchrones optimisé pour Prométhée Nexus.
    Permet de décharger les calculs lourds (CPU-bound) du thread principal asyncio.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        # Pattern Singleton pour éviter de multiplier les ThreadPools
        if not cls._instance:
            cls._instance = super(AsyncTaskManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, max_workers=5):
        # On s'assure de n'initialiser qu'une seule fois
        if not hasattr(self, "executor"):
            self.executor = ThreadPoolExecutor(max_workers=max_workers)
            self.running = True
            logger.info(f"✅ AsyncTaskManager initialisé avec {max_workers} workers.")

    async def execute_cpu_bound(self, func, *args):
        """
        Exécute une fonction bloquante dans un thread séparé.
        Conforme aux bonnes pratiques : utilise run_in_executor.
        """
        if not self.running:
            logger.warning("Tentative d'exécution sur un manager arrêté.")
            return None
            
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self.executor, func, *args)
        except Exception as e:
            logger.error(f"Erreur d'exécution asynchrone : {e}")
            return None

    def shutdown(self):
        """
        Nettoyage propre des ressources pour éviter les threads orphelins.
        """
        self.running = False
        self.executor.shutdown(wait=False)
        logger.info("🔌 AsyncTaskManager arrêté.")