import ast
import logging
import os
import re
from datetime import datetime
from typing import Dict, Any
from core.base_agent import BaseAgent

logger = logging.getLogger("factory_agent")

ALLOWED_EXTENSIONS = {".py", ".txt", ".md", ".json", ".js", ".html", ".css", ".yaml", ".yml", ".toml", ".cfg"}
MAX_FILE_SIZE = 100 * 1024  # 100 KB
# Seuil anti-troncature : rejeter si nouveau < X% de l'existant (pour fichiers > 500B)
TRUNCATION_MIN_RATIO = 0.60
TRUNCATION_MIN_FILE_SIZE = 500  # Ne pas vérifier pour les petits fichiers

# Fichiers système critiques : le Factory ne doit JAMAIS les écraser via une chaîne automatique.
# Seul un ordre utilisateur direct (Niveau 0 : "factory: écris ...") peut contourner cette protection.
_PROTECTED_FILES = {
    "main.py", "config.py", "guardian.py", "start_nexus.py", "emergency_restore.py",
    "core/orchestrator.py", "core/base_agent.py", "core/router.py",
    "core/summoner.py", "core/event_bus/bus.py", "core/autonomy_engine.py",
    "core/council.py", "core/vector_store.py", "core/grimoire_writer.py",
    "core/ci_pipeline.py",
}

# Mots français/anglais courants que la regex de détection capture par erreur
# quand elle parse du texte LLM libre (ex: "fichier de données" → capture "de")
_FILENAME_STOPLIST = {
    # Articles / déterminants français
    "le", "la", "les", "un", "une", "des", "de", "du", "au", "aux",
    "ce", "ces", "mon", "ton", "son", "notre", "votre", "leur",
    # Conjonctions / prépositions
    "et", "ou", "ni", "mais", "donc", "car", "que", "qui", "quoi",
    "dans", "pour", "par", "sur", "sous", "avec", "sans", "entre",
    # Verbes / adverbes courants
    "en", "est", "sont", "fait", "sera", "avoir",
    "pas", "plus", "moins", "bien", "mal",
    "tout", "tous", "rien", "autre", "aussi",
    # Noms courants captés comme faux positifs (cf. _FACTORY_HISTORY.txt)
    "cible", "contient", "suivant", "nouveau", "nouvelle", "fichier",
    "texte", "analyse", "route", "sortie", "entree", "resultat",
    # Mots-clés Python (pas des noms de fichiers)
    "try", "except", "import", "from", "class", "def", "return",
    "if", "else", "elif", "for", "while", "with", "as", "is", "not",
    "and", "or", "in", "true", "false", "none", "pass", "break",
    "continue", "raise", "yield", "lambda", "global", "nonlocal",
}

class DivineFactory(BaseAgent):
    """
    DivineFactory V26.0 (Smart Path Resolver + Sandboxing)
    - Détection Code : Regex durcie V23.2 préservée.
    - Compatibilité : Support format "FICHIER:" du Formatter.
    - Amélioration A : Publie un événement 'ARTIFACT_CREATED' après écriture.
    - Amélioration B : Intelligence Géographique (évite les doublons à la racine).
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
        Détection intelligente : Markdown OU Code Python Syntaxiquement Valide.
        """
        # 1. Markdown (Priorité absolue - Le plus propre)
        code_blocks = re.findall(r'```(?:python)?(.*?)```', text, re.DOTALL)
        if code_blocks:
            return code_blocks[-1].strip()

        # 2. Mots-clés Python (Regex Durcie V23.2)
        pattern = r'(^|\n)(import |from |class |def |@|print\(|[ \t]*[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*[\"\'\[\{])'
        
        match = re.search(pattern, text)
        if match:
            start_index = match.start()
            return text[start_index:].lstrip()
            
        return None

    def _is_valid_target_path(self, filename: str) -> bool:
        """
        Valide qu'un nom extrait est un vrai chemin de fichier,
        pas un mot français/anglais capturé par la regex dans du texte LLM.
        Règles :
          1. Pas dans la stoplist
          2. Doit avoir une extension (un point dans le basename) OU un séparateur de chemin
        L'extension autorisée est vérifiée plus tard dans process_task (sandboxing).
        """
        if not filename:
            return False
        if filename.lower() in _FILENAME_STOPLIST:
            return False
        basename = os.path.basename(filename.replace("\\", "/"))
        has_extension = "." in basename
        has_path_sep = "/" in filename or "\\" in filename
        if not has_extension and not has_path_sep:
            return False
        return True

    def _resolve_smart_path(self, filename: str) -> str:
        """
        Intelligence Géographique : Si le chemin est relatif simple (ex: 'toto.py'),
        on cherche si le fichier existe déjà dans des dossiers clés (Agents/, core/).
        Cela évite de créer des doublons à la racine si l'original est ailleurs.
        """
        # Si le chemin contient déjà un dossier explicite (ex: 'Agents/toto.py'), on respecte l'ordre.
        if "/" in filename or "\\" in filename:
            return filename
            
        # Liste des dossiers surveillés pour la redirection
        watched_dirs = ["Agents", "core"]
        
        for folder in watched_dirs:
            potential_path = os.path.join(folder, filename)
            if os.path.exists(potential_path):
                self.log_thought(f"📍 Redirection intelligente : {filename} -> {potential_path}", type="info")
                return potential_path
                
        # Si non trouvé ailleurs, on garde le chemin d'origine (racine)
        return filename

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")
        
        self.log_thought(f"Analyse Factory V25.0 : {mission[:50]}...", type="thought")

        # 1. DÉTECTION DU CHEMIN
        path_match = re.search(r'(?:crée|écris|fichier|path|write|update|dans|cible)\s+[:\s]*([a-zA-Z0-9_/\.\\-]+)', mission + "\n" + context, re.IGNORECASE)
        target_path = None
        
        if path_match:
            candidate = path_match.group(1).strip().strip("'").strip('"')
            if self._is_valid_target_path(candidate):
                target_path = candidate
            else:
                logger.info(f"[FACTORY] Path rejeté (faux positif regex) : '{candidate}'")
        elif "FICHIER:" in context:
            try:
                candidate = context.split("FICHIER:")[1].split()[0].strip()
                if self._is_valid_target_path(candidate):
                    target_path = candidate
                else:
                    logger.info(f"[FACTORY] Path rejeté (faux positif FICHIER:) : '{candidate}'")
            except Exception as e:
                logger.warning(f"[FACTORY] Échec parsing chemin FICHIER: {e}")

        # APPLICATION INTELLIGENCE GÉOGRAPHIQUE
        if target_path:
            target_path = self._resolve_smart_path(target_path)
        
        # 2. DÉTECTION DU CODE
        full_text = context + "\n" + mission
        code_content = self._extract_code_force(full_text)

        # 3. EXÉCUTION (Gate de Sécurité + Sandboxing)
        if target_path and code_content:
            if len(code_content) < 5:
                return {"status": "error", "result": "Code détecté trop court (Faux positif probable)."}

            # Sandboxing : fichiers protégés (seul un ordre direct Niveau 0 peut les modifier)
            normalized = target_path.replace("\\", "/")
            if normalized in _PROTECTED_FILES:
                logger.warning(f"[FACTORY] 🛡️ PROTÉGÉ : {target_path} — écriture refusée (fichier système critique)")
                return {"status": "error", "result": f"Fichier protégé : {target_path}. Écriture refusée."}

            # Sandboxing : vérification extension
            ext = os.path.splitext(target_path)[1].lower()
            if ext and ext not in ALLOWED_EXTENSIONS:
                return {"status": "error", "result": f"Extension interdite : {ext}. Autorisées : {ALLOWED_EXTENSIONS}"}

            # Sandboxing : limite de taille
            if len(code_content.encode("utf-8")) > MAX_FILE_SIZE:
                return {"status": "error", "result": f"Fichier trop volumineux (>{MAX_FILE_SIZE // 1024}KB)."}

            # Validation syntaxique pré-écriture (fichiers .py uniquement)
            if ext == ".py":
                try:
                    ast.parse(code_content)
                except SyntaxError as e:
                    logger.warning(f"[FACTORY] Code Python invalide pour {target_path} (ligne {e.lineno}): {e.msg}")
                    return {"status": "error", "result": f"Code Python invalide (SyntaxError ligne {e.lineno}): {e.msg}"}

            try:
                if ".." in target_path: raise Exception("Path Traversal Interdit")
                full_path = os.path.abspath(target_path)

                # Sandboxing : écriture dans le périmètre projet uniquement
                if not full_path.startswith(self.project_root):
                    return {"status": "error", "result": "Écriture hors-projet interdite."}

                os.makedirs(os.path.dirname(full_path), exist_ok=True)

                # Anti-troncature : rejeter si le nouveau fichier est trop court par rapport à l'existant
                if os.path.exists(full_path):
                    existing_size = os.path.getsize(full_path)
                    new_size = len(code_content.encode("utf-8"))
                    if existing_size > TRUNCATION_MIN_FILE_SIZE and new_size < existing_size * TRUNCATION_MIN_RATIO:
                        ratio_pct = int(new_size / existing_size * 100)
                        logger.warning(
                            f"[FACTORY] 🛡️ ANTI-TRONCATURE: {target_path} rejeté "
                            f"({new_size}B vs {existing_size}B existant = {ratio_pct}% < {int(TRUNCATION_MIN_RATIO*100)}%)"
                        )
                        return {
                            "status": "error",
                            "result": (
                                f"Anti-troncature : le nouveau code ({new_size}B) fait {ratio_pct}% "
                                f"de l'existant ({existing_size}B). Écriture refusée. "
                                f"Le code généré est probablement incomplet."
                            )
                        }

                # Backup automatique avant écriture
                self._backup(full_path)
                
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(code_content)
                
                self._log_to_manifest("WRITE", target_path)
                msg = f"✅ Fichier écrit (Smart Path + Backup) : {target_path}"
                self.remember(f"FILE_CREATED: {target_path}", metadata={"type": "code_creation"})
                
                # Feedback UI
                from core.event_bus.bus import bus
                await bus.publish("AGENT_RESPONSE", {"agent": "factory", "content": msg, "timestamp": str(datetime.now())})

                # --- AMÉLIORATION A : TRIGGER QUALITÉ ---
                await bus.publish("ARTIFACT_CREATED", {"filepath": full_path, "filename": target_path})
                
                return {"status": "success", "result": msg}
            
            except Exception as e:
                return {"status": "error", "result": f"Erreur disque : {e}"}

        self.log_thought("⚠️ Factory : Pas de code Python valide détecté.", type="warning")
        return {"status": "warning", "result": "Indique le chemin ET du code valide."}