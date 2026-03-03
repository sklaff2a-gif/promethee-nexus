import logging
import psutil
from typing import Dict, Any
from core.base_agent import BaseAgent
from core.event_bus.bus import bus

# M10: import Config avec fallback pour éviter crash si config.py a une erreur
try:
    from config import Config
except Exception:
    Config = None

try:
    import GPUtil
    GPU_AVAILABLE = True
except ImportError:
    GPU_AVAILABLE = False

logger = logging.getLogger("infra")

class DivineInfra(BaseAgent):
    """
    DivineInfra V13.7 - Bilingue & Explict
    """
    
    def __init__(self):
        super().__init__(
            name="infra",
            role="DevOps SRE & Hardware Guardian",
            description="Gère l'infrastructure et protège les limites physiques du serveur."
        )
        self.limits = getattr(Config, "HARDWARE", {"RAM_GB": 32, "VRAM_GB": 16}) if Config else {"RAM_GB": 32, "VRAM_GB": 16}
        self.system_instructions = """Tu es le gardien infrastructure du projet Prométhée.
CONTEXTE : Système multi-agents Python sur UN SEUL PC Windows (Ollama local, ChromaDB, FastAPI).
TON RÔLE : Monitorer CPU/RAM/VRAM, diagnostiquer les problèmes de performance, recommander des optimisations.
RÈGLES : Réponds en français. Base tes analyses sur les métriques réelles. Pas de Docker/Kubernetes/Cloud."""

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        
        # --- MODE GARDIEN (Prioritaire) ---
        # [CORRECTIF] Ajout des déclencheurs français
        keywords = ["status", "santé", "health", "check", "audit", "vram", "cpu", "système", "état", "ram"]
        
        if any(kw in mission.lower() for kw in keywords):
            return await self._perform_health_check()

        # --- MODE EXPERT (Génératif) ---
        current_state = self._get_simple_metrics()
        task_payload["context"] = f"ÉTAT ACTUEL DU SERVEUR:\n{current_state}\n\n" + task_payload.get("context", "")
        return await super().process_task(task_payload)

    async def _perform_health_check(self) -> Dict[str, Any]:
        """Vérifie physiquement le matériel et publie le résultat."""
        
        # RAM
        ram = psutil.virtual_memory()
        ram_used_gb = ram.used / (1024**3)
        ram_percent = ram.percent
        
        # VRAM (GPU)
        vram_used_gb = 0
        vram_total_gb = self.limits["VRAM_GB"]
        gpu_load = 0
        
        if GPU_AVAILABLE:
            try:
                gpus = GPUtil.getGPUs()
                if gpus:
                    gpu = gpus[0]
                    vram_used_gb = gpu.memoryUsed / 1024
                    gpu_load = gpu.load * 100
            except Exception as e:
                logger.warning(f"[INFRA] Échec lecture GPU : {e}")
        
        # Décision
        status = "GREEN"
        warnings = []
        
        if vram_used_gb > (vram_total_gb * 0.85):
            status = "RED"
            warnings.append(f"VRAM CRITIQUE ({vram_used_gb:.1f}GB)")
            
        if ram_percent > 90:
            status = "RED"
            warnings.append(f"RAM CRITIQUE ({ram_percent}%)")
        elif ram_percent > 75:
            status = "ORANGE"
            warnings.append("RAM Chargée")

        report = (
            f"💻 RAPPORT INFRA (Ryzen 9 / RTX 5070 Ti)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"GLOBAL STATUS: {status} {'🛑' if status == 'RED' else '✅'}\n"
            f"🧠 CPU Load : {psutil.cpu_percent()}%\n"
            f"💾 RAM Sys  : {ram_used_gb:.1f}/{self.limits['RAM_GB']} GB ({ram_percent}%)\n"
            f"🎮 VRAM GPU : {vram_used_gb:.1f}/{vram_total_gb} GB (Load: {gpu_load:.1f}%)\n"
        )
        
        if warnings:
            report += f"\n⚠️ ALERTES ACTIVE: {', '.join(warnings)}"

        # Publication explicite
        await bus.publish("THOUGHT_STREAM", {
            "agent": self.name,
            "content": report,
            "type": "result"
        })

        return {"status": "success", "result": report}

    def _get_simple_metrics(self):
        return f"CPU: {psutil.cpu_percent()}% | RAM: {psutil.virtual_memory().percent}%"