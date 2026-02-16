import os
import hashlib
import json
import logging
from datetime import datetime

logger = logging.getLogger("DropzoneIndexer")


class DropzoneIndexer:
    """Catalogue les fichiers de la dropzone avec dédup et classification."""

    EXTENSIONS = {
        "code": [".py", ".js", ".ts", ".html", ".css", ".jsx", ".tsx"],
        "config": [".json", ".yaml", ".yml", ".toml", ".ini"],
        "docs": [".md", ".txt", ".rst"],
        "data": [".csv", ".xml", ".sql"],
    }

    PROJECT_MARKERS = [
        "requirements.txt",
        "package.json",
        "setup.py",
        "pyproject.toml",
        "__init__.py",
        "Makefile",
    ]

    def catalog(self, root_path="USER_DROPZONE") -> dict:
        """Scanne un répertoire et retourne un manifest structuré."""
        root_path = os.path.abspath(root_path)
        processed_dir = os.path.join(root_path, "processed")

        if not os.path.exists(root_path):
            return {
                "scan_date": datetime.now().isoformat(),
                "projects": [],
                "duplicates": [],
                "summary": {"total_projects": 0, "total_files": 0, "unique_files": 0, "duplicates": 0},
            }

        projects = self.detect_projects(root_path)
        all_hashes = {}  # sha256 -> list of rel_paths
        total_files = 0

        for project in projects:
            project_root = project["root"]
            files = []
            stats = {"code": 0, "config": 0, "docs": 0, "data": 0, "other": 0, "total_size": 0}

            for dirpath, dirnames, filenames in os.walk(project_root):
                abs_dir = os.path.abspath(dirpath)
                # Ignorer processed/ et dossiers cachés
                if abs_dir.startswith(processed_dir) or os.path.basename(dirpath).startswith("."):
                    dirnames.clear()
                    continue
                # Élaguer les sous-dossiers cachés
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]

                for fname in filenames:
                    filepath = os.path.join(dirpath, fname)
                    try:
                        size = os.path.getsize(filepath)
                    except OSError:
                        continue

                    rel_path = os.path.relpath(filepath, root_path).replace("\\", "/")
                    file_type = self._classify_file(fname)
                    sha = self._hash_file(filepath)

                    files.append({
                        "rel_path": rel_path,
                        "type": file_type,
                        "size": size,
                        "sha256": sha,
                    })
                    stats[file_type] = stats.get(file_type, 0) + 1
                    stats["total_size"] += size
                    total_files += 1

                    # Tracking pour dédup
                    all_hashes.setdefault(sha, []).append(rel_path)

            project["files"] = files
            project["stats"] = stats

        # Détecter les doublons (SHA256 identiques entre fichiers)
        duplicates = [
            {"sha256": sha, "files": paths}
            for sha, paths in all_hashes.items()
            if len(paths) > 1
        ]
        dup_count = sum(len(d["files"]) - 1 for d in duplicates)
        unique_files = total_files - dup_count

        manifest = {
            "scan_date": datetime.now().isoformat(),
            "projects": projects,
            "duplicates": duplicates,
            "summary": {
                "total_projects": len(projects),
                "total_files": total_files,
                "unique_files": unique_files,
                "duplicates": dup_count,
            },
        }

        # Sauvegarder le manifest
        self._save_manifest(root_path, manifest)

        return manifest

    def detect_projects(self, root_path) -> list:
        """Identifie les sous-projets par leurs marqueurs (requirements.txt, package.json, etc.)."""
        root_path = os.path.abspath(root_path)
        processed_dir = os.path.join(root_path, "processed")
        projects = []
        seen_roots = set()

        for dirpath, dirnames, filenames in os.walk(root_path):
            abs_dir = os.path.abspath(dirpath)
            # Ignorer processed/ et dossiers cachés
            if abs_dir.startswith(processed_dir) or os.path.basename(dirpath).startswith("."):
                dirnames.clear()
                continue
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

            markers_found = [m for m in self.PROJECT_MARKERS if m in filenames]
            if markers_found and abs_dir not in seen_roots:
                # Éviter d'enregistrer un sous-projet si un parent est déjà projet
                is_subproject = any(abs_dir.startswith(sr + os.sep) for sr in seen_roots)
                if not is_subproject:
                    seen_roots.add(abs_dir)
                    projects.append({
                        "name": os.path.basename(abs_dir),
                        "root": abs_dir,
                        "markers": markers_found,
                    })

        # Si aucun projet détecté mais des fichiers existent, traiter la racine comme un seul "projet"
        if not projects:
            has_files = any(
                True for dp, _, fnames in os.walk(root_path)
                if fnames and not os.path.abspath(dp).startswith(processed_dir)
            )
            if has_files:
                projects.append({
                    "name": os.path.basename(root_path),
                    "root": root_path,
                    "markers": [],
                })

        return projects

    def _classify_file(self, filepath) -> str:
        """Retourne la catégorie du fichier basée sur son extension."""
        ext = os.path.splitext(filepath)[1].lower()
        for category, extensions in self.EXTENSIONS.items():
            if ext in extensions:
                return category
        return "other"

    def _hash_file(self, filepath) -> str:
        """SHA256 du contenu pour dédup."""
        h = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
        except OSError:
            return "error"
        return h.hexdigest()

    def quick_count(self, root_path="USER_DROPZONE") -> int:
        """Comptage rapide des fichiers en attente (sans hash)."""
        root_path = os.path.abspath(root_path)
        processed_dir = os.path.join(root_path, "processed")
        count = 0

        if not os.path.exists(root_path):
            return 0

        for dirpath, dirnames, filenames in os.walk(root_path):
            abs_dir = os.path.abspath(dirpath)
            if abs_dir.startswith(processed_dir) or os.path.basename(dirpath).startswith("."):
                dirnames.clear()
                continue
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            count += len(filenames)

        return count

    def _save_manifest(self, root_path, manifest):
        """Sauvegarde le manifest dans processed/."""
        processed_dir = os.path.join(root_path, "processed")
        os.makedirs(processed_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(processed_dir, f"manifest_{timestamp}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
            logger.info(f"📋 Manifest sauvé : {path}")
        except Exception as e:
            logger.warning(f"Impossible de sauver le manifest : {e}")
