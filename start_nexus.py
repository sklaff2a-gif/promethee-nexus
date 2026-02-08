import subprocess
import sys
import time
import os
import signal

# Liste des processus satellites (Les yeux et les oreilles)
# [NETTOYAGE PHASE 2] Liste vidée car les satellites financiers ont été supprimés.
SATELLITES = []

processes = []

def start_satellite(info):
    script_path = info["script"]
    if not os.path.exists(script_path):
        print(f"⚠️ Satellite introuvable : {script_path}")
        return None
        
    print(f"   [ON] Démarrage {info['name']}...")
    # On lance en mode indépendant (creationflags pour Windows évite de tout tuer si Ctrl+C)
    return subprocess.Popen([sys.executable, script_path], cwd=os.getcwd())

def main():
    print("╔════════════════════════════════════════════════════╗")
    print("║    🔥 PROMÉTHÉE NEXUS V11 - SYSTÈME GLOBAL 🔥      ║")
    print("╚════════════════════════════════════════════════════╝")

    # 1. Lancement des Satellites
    if SATELLITES:
        print("\n📡 ACTIVATION DES CAPTEURS...")
        for sat in SATELLITES:
            p = start_satellite(sat)
            if p: processes.append(p)
    else:
        print("\n📡 CAPTEURS : Aucun satellite actif.")

    # 2. Lancement du Cerveau (Boucle de Redémarrage)
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
            
    print("💀 Terminaison des processus satellites...")
    for p in processes:
        try:
            p.terminate()
        except Exception as e:
            print(f"⚠️ Échec terminaison processus : {e}")

if __name__ == "__main__":
    main()