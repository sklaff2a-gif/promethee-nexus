import os
import shutil
import glob
import time
import logging

class KnowledgeIngestor:
    """
    Outil V13.6 (Deep Ingest Recursive)
    """
    def __init__(self, dropzone_path="USER_DROPZONE"):
        self.root = dropzone_path
        self.processed = os.path.join(self.root, "processed")
        
        if not os.path.exists(self.processed):
            os.makedirs(self.processed)

    def scan_new_files(self):
        """Récupère la liste des fichiers en attente (Scan Récursif)."""
        extensions = ["*.txt", "*.md", "*.py", "*.json", "*.js", "*.html", "*.css", "*.csv"]
        files = []
        
        processed_abs = os.path.abspath(self.processed)

        for ext in extensions:
            # Le pattern ** + recursive=True permet de tout scanner
            found_files = glob.glob(os.path.join(self.root, "**", ext), recursive=True)
            files.extend(found_files)
        
        # Filtrage : On ne traite pas ce qui est DÉJÀ dans 'processed'
        valid_files = []
        for f in list(set(files)):
            if processed_abs not in os.path.abspath(f):
                valid_files.append(f)
                
        return valid_files

    def read_and_archive(self, filepath):
        try:
            filename = os.path.basename(filepath)
            
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
            except UnicodeDecodeError:
                with open(filepath, "r", encoding="latin-1") as f:
                    content = f.read()
            
            # Archivage (Aplatissement)
            dest = os.path.join(self.processed, filename)
            
            if os.path.exists(dest):
                base, ext = os.path.splitext(filename)
                timestamp = int(time.time())
                dest = os.path.join(self.processed, f"{base}_{timestamp}{ext}")
            
            shutil.move(filepath, dest)
            
            # Nettoyage des dossiers vides
            parent_dir = os.path.dirname(filepath)
            if parent_dir != self.root and not os.listdir(parent_dir):
                try:
                    os.rmdir(parent_dir)
                except Exception as e:
                    logging.getLogger("KnowledgeIngestor").warning(f"Échec suppression dossier vide {parent_dir} : {e}")

            return filename, content
            
        except Exception as e:
            return None, f"Erreur lecture: {e}"