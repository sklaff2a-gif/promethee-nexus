import os
import shutil
import glob

def defibrillate():
    print("🚑 DÉMARRAGE DU DÉFIBRILLATEUR SYSTÈME...")
    print("   Recherche de fichiers endommagés et restauration des backups (.bak)...")
    
    # On cherche tous les fichiers .bak dans le projet (récursivement si possible, ou juste Agents/Core)
    # Ici on cible les dossiers critiques
    critical_dirs = ["Agents", "core", "."]
    
    restored_count = 0
    
    for folder in critical_dirs:
        if not os.path.exists(folder): continue
        
        # Liste des backups dans le dossier
        backups = glob.glob(os.path.join(folder, "*.bak"))
        
        for bak_file in backups:
            # Le fichier original correspondant (sans .bak)
            original_file = bak_file[:-4]
            
            print(f"   ⚡ Analyse de {original_file}...")
            
            # Logique de restauration :
            # On restaure si l'utilisateur le demande, ou on peut le faire systématiquement en cas d'urgence
            # Ici, on va restaurer automatiquement.
            
            try:
                shutil.copy2(bak_file, original_file)
                print(f"      ✅ RESTAURÉ : {original_file} (depuis le backup)")
                restored_count += 1
            except Exception as e:
                print(f"      ❌ Échec restauration : {e}")

    if restored_count > 0:
        print(f"\n✨ SUCCÈS : {restored_count} fichiers vitaux restaurés.")
        print("   Le système devrait pouvoir redémarrer.")
    else:
        print("\n⚠️ Aucun backup trouvé ou nécessaire.")

if __name__ == "__main__":
    defibrillate()