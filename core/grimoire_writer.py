import os
import re
import json
import logging

logger = logging.getLogger("GrimoireWriter")

# Patterns dangereux interdits dans le code des recettes
DANGEROUS_PATTERNS = [
    "os.system",
    "subprocess",
    "shutil.rmtree",
    "exec(",
    "eval(",
    "__import__",
]

MAX_CODE_SIZE = 50 * 1024  # 50 KB


class GrimoireWriter:
    GRIMOIRE_DIR = os.path.join(os.path.dirname(__file__), "grimoire")
    INDEX_PATH = os.path.join(GRIMOIRE_DIR, "grimoire_index.json")

    @staticmethod
    def write_recipe(slug: str, name: str, description: str, keywords: list, code: str) -> dict:
        """
        Ecrit une nouvelle recette dans le Grimoire.

        Returns:
            dict avec status "success" ou "error" et un message
        """
        # 1. Validation du slug
        if not slug or not re.match(r'^[a-z][a-z0-9_]*$', slug):
            return {"status": "error", "message": f"Slug invalide : '{slug}'. Doit être alphanumérique + underscore, commençant par une lettre."}

        # 2. Validation taille
        if len(code.encode("utf-8")) > MAX_CODE_SIZE:
            return {"status": "error", "message": f"Code trop volumineux ({len(code.encode('utf-8'))} octets > {MAX_CODE_SIZE} max)."}

        # 3. Vérification héritage BaseAgent
        if not re.search(r'class\s+\w+\s*\(\s*BaseAgent\s*\)', code):
            return {"status": "error", "message": "Le code doit contenir une classe héritant de BaseAgent."}

        # 4. Vérification patterns dangereux
        for pattern in DANGEROUS_PATTERNS:
            if pattern in code:
                return {"status": "error", "message": f"Pattern dangereux détecté : '{pattern}'."}

        # 5. Ecriture du fichier .py
        file_path = os.path.join(GrimoireWriter.GRIMOIRE_DIR, f"{slug}.py")
        if os.path.exists(file_path):
            return {"status": "error", "message": f"Le fichier '{slug}.py' existe déjà dans le Grimoire."}

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            return {"status": "error", "message": f"Erreur écriture fichier : {e}"}

        # 6. Mise à jour de l'index
        try:
            index = []
            if os.path.exists(GrimoireWriter.INDEX_PATH):
                with open(GrimoireWriter.INDEX_PATH, "r", encoding="utf-8") as f:
                    index = json.load(f)

            new_entry = {
                "slug": slug,
                "name": name,
                "description": description,
                "keywords": keywords,
                "file": f"{slug}.py"
            }
            index.append(new_entry)

            with open(GrimoireWriter.INDEX_PATH, "w", encoding="utf-8") as f:
                json.dump(index, f, indent=2, ensure_ascii=False)

        except Exception as e:
            # Rollback : supprimer le fichier créé
            if os.path.exists(file_path):
                os.remove(file_path)
            return {"status": "error", "message": f"Erreur mise à jour index : {e}"}

        # 7. Invalidation du cache Router
        try:
            from core.router import RouterAgent
            RouterAgent.invalidate_grimoire_cache()
        except ImportError:
            pass

        logger.info(f"📜 [GRIMOIRE] Nouvelle recette écrite : {slug} ({name})")
        return {"status": "success", "message": f"Recette '{slug}' ajoutée au Grimoire."}
