import subprocess
import sys
import time
import os

def main():
    print("╔════════════════════════════════════════════════════╗")
    print("║    🔥 PROMÉTHÉE NEXUS V11 - SYSTÈME GLOBAL 🔥      ║")
    print("╚════════════════════════════════════════════════════╝")

    # Lancement du Cerveau (Boucle de Redémarrage)
    print("\n🧠 ACTIVATION DU NOYAU CENTRAL...")
    print("   (Ctrl+C pour arrêter tout le système)\n")
    
    RESTART_CODE = 65  # Code secret pour demander un redémarrage
    
    while True:
        try:
            # On lance main.py et on attend sa fin
            # Note: on retire check=True pour gérer nous-mêmes le code de retour
            result = subprocess.run([sys.executable, "main.py"])
            
            # ANALYSE DE LA FIN DU PROCESSUS
            if result.returncode == RESTART_CODE:
                print("\n🔄 [NEXUS] Mise à jour système détectée. Redémarrage immédiat...\n")
                time.sleep(1) # Petite pause respiratoire
                continue # On relance la boucle (et donc main.py)
            
            # Si code 0 (arrêt normal) ou erreur crash, on sort
            break
            
        except KeyboardInterrupt:
            print("\n🛑 Arrêt d'urgence demandé...")
            break
            
    print("💀 Arrêt du système.")

if __name__ == "__main__":
    main()