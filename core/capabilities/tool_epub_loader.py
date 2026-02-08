import zipfile
import re

class EpubLoader:
    def get_chunks(self, path, chunk_size=10000):
        """
        Découpe un EPUB en morceaux de 'chunk_size' caractères.
        Retourne un Générateur (pour ne pas saturer la RAM).
        """
        try:
            with zipfile.ZipFile(path) as zip_ref:
                # On filtre les fichiers HTML
                html_files = [f for f in zip_ref.namelist() if f.endswith(('.html', '.xhtml'))]
                # Tri pour essayer de respecter l'ordre des chapitres
                html_files.sort()
                
                current_chunk = ""
                
                for file in html_files:
                    try:
                        with zip_ref.open(file) as f:
                            content = f.read().decode('utf-8', errors='ignore')
                            # Nettoyage
                            clean = re.sub('<[^<]+?>', ' ', content)
                            clean = re.sub('\s+', ' ', clean).strip()
                            
                            current_chunk += clean + "\n\n"
                            
                            # Si le morceau dépasse la taille, on le livre et on vide le tampon
                            if len(current_chunk) >= chunk_size:
                                yield current_chunk
                                current_chunk = ""
                    except:
                        continue
                
                # S'il reste un morceau à la fin
                if current_chunk:
                    yield current_chunk
                    
        except Exception as e:
            yield f"ERREUR FATALE EPUB: {str(e)}"

    # Alias
    def read_epub(self, path):
        # Version simple qui ne renvoie que le premier chunk (Preview)
        gen = self.get_chunks(path, chunk_size=5000)
        return next(gen, "Livre vide.")