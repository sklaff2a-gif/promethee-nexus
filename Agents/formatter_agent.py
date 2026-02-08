import logging
import asyncio
from typing import Dict, Any
from core.base_agent import BaseAgent

logger = logging.getLogger("formatter_agent")

class DivineFormatter(BaseAgent):
    """
    DivineFormatter V2.3 (Hybrid Escalation - PATCHED)
    - Rôle : Nettoie le code ET déclenche la Factory.
    - Architecture Hybride : Tente le modèle Local d'abord. En cas d'échec/hallucination, escalade vers le modèle Premium.
    - Sécurité : Filtre strict (PATCH : 'try', 'except' ajoutés à la blacklist, 'cible' retiré).
    """
    def __init__(self):
        super().__init__(name="formatter", role="Standardizer", description="Formate les entrées pour la Factory.")

    def _is_valid_filename(self, filename: str) -> bool:
        """Détecte si le modèle a confondu du code (ex: shutil.copy) avec un nom de fichier."""
        if not filename: return False
        
        # Liste noire de mots-clés Python souvent confondus
        # PATCH : Ajout de 'try' et 'except' pour éviter ton dernier bug
        blacklist = ["shutil", "print", "import", "def", "return", "class", "exit", "sys", "os", "copy2", "try", "except"]
        clean_name = filename.strip().lower()
        
        # 1. Rejet si commence par un mot clé
        for kw in blacklist:
            if clean_name.startswith(kw + ".") or clean_name == kw:
                return False
                
        # 2. Rejet si pas d'extension (sauf fichiers spéciaux connus)
        # PATCH : 'cible' a été retiré pour forcer une extension correcte
        if "." not in clean_name and clean_name not in ["makefile", "dockerfile", "license", "readme", "requirements.txt"]:
            return False
            
        return True

    def _parse_response(self, response: str):
        """Extrait le fichier et le code de la réponse."""
        target_file = None
        if "FICHIER:" in response:
            try:
                target_file = response.split("FICHIER:")[1].split("\n")[0].strip()
            except Exception as e:
                logger.warning(f"[FORMATTER] Échec parsing FICHIER: {e}")
        
        has_code = "```" in response
        return target_file, has_code

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")
        full_text = f"{mission}\n{context}"
        
        self.log_thought("🧹 Nettoyage et formatage du code en cours...", type="thought")

        # Prompt Standard
        prompt = (
            f"Tu es un compilateur strict. Analyse ce texte brut :\n"
            f"--- DÉBUT ---\n{full_text[:2000]}...\n--- FIN ---\n"
            f"TA MISSION :\n"
            f"1. Trouve le nom du fichier cible (ex: core/test.py).\n"
            f"   ATTENTION: Ne confonds pas le code (ex: shutil.copy) avec le nom du fichier !\n"
            f"2. Trouve le CODE complet à écrire.\n"
            f"3. Renvoie UNIQUEMENT ce format exact :\n"
            f"FICHIER: <chemin_valide>\n"
            f"CODE:\n"
            f"```python\n"
            f"<le code ici>\n"
            f"```"
        )

        # --- TENTATIVE 1 : MODE ÉCO (Local) ---
        response = await self.generate_content(prompt)
        target_file, has_code = self._parse_response(response)

        # Vérification de la qualité
        is_valid = target_file and self._is_valid_filename(target_file) and has_code

        if not is_valid:
            # --- TENTATIVE 2 : ESCALADE PREMIUM (Si le local échoue) ---
            fail_reason = f"Hallucination détectée ({target_file})" if target_file else "Format illisible"
            self.log_thought(f"⚠️ {fail_reason}. Escalade vers l'Intelligence Supérieure (Premium)...", type="warning")
            
            # On relance avec un prompt d'insistance
            prompt_premium = f"ERREUR PRÉCÉDENTE : Le modèle a confondu le code et le nom du fichier.\nCORRIGE IMMÉDIATEMENT.\n\n{prompt}"
            
            response = await self.generate_content(prompt_premium)
            target_file, has_code = self._parse_response(response)
            
            # Re-validation
            is_valid = target_file and self._is_valid_filename(target_file) and has_code

        # --- DÉCISION FINALE ---
        if is_valid:
            self.log_thought(f"✅ Formatage validé ({target_file}). Transmission Factory...", type="success")
            try:
                from core.orchestrator import orchestrator
                factory_payload = { "mission": "Exécute ce code propre.", "context": response }
                
                loop = asyncio.get_running_loop()
                loop.create_task(orchestrator.dispatch_task("factory", factory_payload))
                return {"status": "success", "result": "CODE_CLEAN_SENT_TO_FACTORY"}
                
            except Exception as e:
                return {"status": "error", "result": f"ERREUR RELAIS: {e}"}

        else:
            self.log_thought(f"❌ Échec définitif du formatage ({target_file}).", type="error")
            return {"status": "error", "result": "IMPOSSIBLE DE FORMATER. Intervention humaine requise."}