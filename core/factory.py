import logging
import os
import re
from datetime import datetime
from typing import Dict, Any
from core.base_agent import BaseAgent

logger = logging.getLogger("factory_agent")

ALLOWED_EXTENSIONS = {".py", ".txt", ".md", ".json", ".js", ".html", ".css", ".yaml", ".yml", ".toml", ".cfg"}
MAX_FILE_SIZE = 100 * 1024  # 100 KB

class DivineFactory(BaseAgent):
    """
    DivineFactory V24.0 (Omnivore Edition + Sandboxing)
    - Capable d'extraire du code même sans balises Markdown.
    - Logique de nettoyage "Brute Force" pour récupérer les fichiers mal formatés par le Coder.
    - Sandboxing : Whitelist extensions, périmètre projet, limite taille.
    """
    def __init__(self):
        super().__init__(name="factory", role="System Executor", description="La Main de Prométhée.")
        self.manifest_path = "_FACTORY_HISTORY.txt"
        self.project_root = os.path.abspath(".")

    def _log_to_manifest(self, action, path):
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = f"[{timestamp}] {action.upper()} -> {path}\n"
            with open(self.manifest_path, "a", encoding="utf-8") as f: f.write(entry)
            print(f"\033[93m📝 [FACTORY] {action} : {path}\033[0m")
        except Exception as e:
            logger.warning(f"[FACTORY] Échec écriture manifest : {e}")

    def _backup(self, path):
        import shutil
        if os.path.exists(path):
            try:
                shutil.copy2(path, path + ".bak")
            except Exception as e:
                logger.warning(f"[FACTORY] Échec backup de {path} : {e}")

    def _extract_code_force(self, text: str) -> str:
        """
        Tentative désespérée de trouver du code dans un texte mal formaté.
        Cherche les imports, classes ou defs.
        """
        # 1. Si balises Markdown présentes, on les prend (C'est le plus propre)
        code_blocks = re.findall(r'```(?:python)?(.*?)```', text, re.DOTALL)
        if code_blocks:
            return code_blocks[-1].strip()

        # 2. Sinon, on cherche le début du code Python (import, class, def, from)
        # On prend tout le texte à partir de la première occurrence logique
        match = re.search(r'(^|\n)(import |from |class |def |@)', text)
        if match:
            start_index = match.start()
            # On nettoie un peu ce qui est avant
            return text[start_index:].strip()
            
        return None

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")
        
        self.log_thought(f"Analyse Factory V23 : {mission[:50]}...", type="thought")

        # 1. DÉTECTION DU CHEMIN (PATH)
        # On cherche un chemin de fichier (ex: core/test.py, tests/foo.py)
        path_match = re.search(r'(?:crée|fichier|path|write|update|dans|cible)\s+[:\s]*([a-zA-Z0-9_/\.\\-]+)', mission + "\n" + context, re.IGNORECASE)
        target_path = None
        
        if path_match:
            target_path = path_match.group(1).strip().strip("'").strip('"')
        
        # 2. DÉTECTION DU CODE (CONTENU)
        # On concatène mission et contexte pour chercher le code partout
        full_text = context + "\n" + mission
        code_content = self._extract_code_force(full_text)

        # 3. EXÉCUTION (Gate de Sécurité + Sandboxing)
        if target_path and code_content:
            # Sécurité anti-hallucination : Si le code fait moins de 10 caractères, c'est louche
            if len(code_content) < 10:
                return {"status": "error", "result": "Code détecté trop court (invalide)."}

            # Sandboxing : vérification extension
            ext = os.path.splitext(target_path)[1].lower()
            if ext and ext not in ALLOWED_EXTENSIONS:
                return {"status": "error", "result": f"Extension interdite : {ext}. Autorisées : {ALLOWED_EXTENSIONS}"}

            # Sandboxing : limite de taille
            if len(code_content.encode("utf-8")) > MAX_FILE_SIZE:
                return {"status": "error", "result": f"Fichier trop volumineux (>{MAX_FILE_SIZE // 1024}KB)."}

            try:
                # Nettoyage chemin
                if ".." in target_path: raise Exception("Path Traversal Interdit")
                full_path = os.path.abspath(target_path)

                # Sandboxing : écriture dans le périmètre projet uniquement
                if not full_path.startswith(self.project_root):
                    return {"status": "error", "result": "Écriture hors-projet interdite."}

                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                
                self._backup(full_path)
                
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(code_content)
                
                self._log_to_manifest("WRITE", target_path)
                msg = f"✅ Fichier écrit (Mode Omnivore) : {target_path}"
                self.remember(f"FILE_CREATED: {target_path}", metadata={"type": "code_creation"})
                
                # Feedback Bus pour l'interface
                from core.event_bus.bus import bus
                await bus.publish("AGENT_RESPONSE", {"agent": "factory", "content": msg, "timestamp": str(datetime.now())})
                
                return {"status": "success", "result": msg}
            
            except Exception as e:
                return {"status": "error", "result": f"Erreur disque : {e}"}

        # Échec de détection
        self.log_thought("⚠️ Factory aveugle : Chemin ou Code introuvable.", type="warning")
        return {"status": "warning", "result": "Indique clairement le nom du fichier (ex: 'fichier: test.py')"}