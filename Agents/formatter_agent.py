import ast
import logging
import asyncio
import re
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

    def _extract_from_context(self, text: str):
        """Extraction déterministe du fichier cible et du code depuis le texte brut.
        Fallback quand le LLM ne produit pas le format FICHIER:/CODE attendu."""
        target_file = None
        code_content = None

        # 1. Marqueur FICHIER: déjà présent dans le texte d'entrée
        if "FICHIER:" in text:
            try:
                candidate = text.split("FICHIER:")[1].split("\n")[0].strip()
                if self._is_valid_filename(candidate):
                    target_file = candidate
            except Exception:
                pass

        # 2. Chercher des chemins de fichiers Python explicites
        if not target_file:
            path_patterns = re.findall(
                r'(?:core|Agents|tests|capabilities)[/\\][\w/\\]+\.py',
                text
            )
            for candidate in path_patterns:
                clean = candidate.replace("\\", "/")
                if self._is_valid_filename(clean):
                    target_file = clean
                    break

        # 3. Chercher un fichier .py isolé (ex: "agent_code_generator.py")
        if not target_file:
            simple_files = re.findall(r'(?<![/\\.\w])(\w[\w-]+\.py)(?!\w)', text)
            for candidate in simple_files:
                if self._is_valid_filename(candidate) and len(candidate) > 4:
                    target_file = candidate
                    break

        # 4. Extraire les blocs de code ```python ... ```
        code_blocks = re.findall(r'```(?:python)?\s*\n(.*?)```', text, re.DOTALL)
        if code_blocks:
            code_content = max(code_blocks, key=len)

        return target_file, code_content

    def _rebuild_formatted_response(self, target_file: str, code: str) -> str:
        """Reconstruit une réponse formatée FICHIER:/CODE pour la Factory."""
        return f"FICHIER: {target_file}\nCODE:\n```python\n{code}\n```"

    def _has_formattable_code(self, text: str) -> bool:
        """Vérifie si le texte contient du code Python exploitable."""
        # 1. Bloc de code markdown
        if "```" in text:
            return True
        # 2. Au moins 3 lignes qui ressemblent à du Python
        code_patterns = re.compile(
            r"^(import |from \w+ import |def \w+|class \w+|    (?:def |if |for |return |self\.))",
            re.MULTILINE,
        )
        matches = code_patterns.findall(text)
        return len(matches) >= 3

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")
        full_text = f"{mission}\n{context}"

        # Guard : pas de code exploitable → skip immédiat
        if not self._has_formattable_code(full_text):
            self.log_thought("ℹ️ Aucun code exploitable détecté — formatage ignoré.", type="info")
            return {"status": "success", "result": "NO_CODE_TO_FORMAT"}

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

        # --- TENTATIVE 3 : EXTRACTION DÉTERMINISTE (si LLM a échoué) ---
        if not is_valid:
            self.log_thought("🔧 Fallback : extraction déterministe depuis le contexte...", type="info")
            det_file, det_code = self._extract_from_context(full_text)

            if det_file and det_code:
                target_file = det_file
                response = self._rebuild_formatted_response(det_file, det_code)
                is_valid = True
                self.log_thought(f"✅ Extraction déterministe réussie : {det_file}", type="success")
            elif det_code and not det_file:
                self.log_thought("⚠️ Code trouvé mais aucun fichier cible identifiable.", type="warning")
            else:
                self.log_thought("⚠️ Aucun bloc de code exploitable dans le contexte.", type="warning")

        # --- VALIDATION SYNTAXE (avant dispatch Factory) ---
        if is_valid and target_file and target_file.endswith(".py"):
            # Extraire le code du bloc markdown dans la réponse
            code_blocks = re.findall(r'```(?:python)?\s*\n(.*?)```', response, re.DOTALL)
            code_to_check = max(code_blocks, key=len) if code_blocks else ""
            if code_to_check:
                try:
                    ast.parse(code_to_check)
                except SyntaxError as e:
                    self.log_thought(
                        f"⚠️ LLM a cassé la syntaxe ({e.msg} ligne {e.lineno}). Fallback code original...",
                        type="warning",
                    )
                    # Fallback : extraire le code original depuis le contexte d'entrée
                    det_file, det_code = self._extract_from_context(full_text)
                    if det_code:
                        try:
                            ast.parse(det_code)
                            response = self._rebuild_formatted_response(
                                target_file, det_code
                            )
                            self.log_thought("✅ Fallback code original valide.", type="info")
                        except SyntaxError:
                            self.log_thought("❌ Code original aussi invalide.", type="error")
                            return {
                                "status": "error",
                                "result": "SYNTAXE_INVALIDE — LLM et original cassés.",
                            }
                    else:
                        return {
                            "status": "error",
                            "result": "SYNTAXE_INVALIDE — pas de code original en fallback.",
                        }

        # --- DÉCISION FINALE ---
        if is_valid:
            self.log_thought(f"✅ Formatage validé ({target_file}). Transmission Factory...", type="success")
            try:
                from core.orchestrator import orchestrator
                factory_payload = { "mission": "Exécute ce code propre.", "context": response }

                # Propager evolution_spec_id si présent (pipeline Evolution)
                evo_spec_id = task_payload.get("evolution_spec_id")
                if evo_spec_id:
                    factory_payload["evolution_spec_id"] = evo_spec_id

                loop = asyncio.get_running_loop()
                loop.create_task(orchestrator.dispatch_task("factory", factory_payload))
                return {"status": "success", "result": "CODE_CLEAN_SENT_TO_FACTORY"}

            except Exception as e:
                return {"status": "error", "result": f"ERREUR RELAIS: {e}"}

        else:
            self.log_thought(f"❌ Échec définitif du formatage ({target_file}).", type="error")
            return {"status": "error", "result": "IMPOSSIBLE DE FORMATER. Intervention humaine requise."}