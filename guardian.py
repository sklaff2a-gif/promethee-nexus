import subprocess
import time
import sys
import os

# CONFIGURATION
NEXUS_SCRIPT = "start_nexus.py"
RESTORE_SCRIPT = "emergency_restore.py"
MAX_RETRIES = 5  # Sécurité anti-boucle infinie
CRASH_WINDOW = 30 # Si ça plante en moins de 30s, c'est un crash de démarrage (mauvais code)

def log(message, type="INFO"):
    prefix = {"INFO": "🛡️ [GUARDIAN]", "WARN": "⚠️ [GUARDIAN]", "ERROR": "🚑 [GUARDIAN]"}
    print(f"{prefix.get(type, 'INFO')} {message}")

def run_restore():
    """Exécute le script de restauration des backups."""
    log("DÉTECTION D'UNE CORRUPTION CRITIQUE. Lancement du protocole de restauration...", "ERROR")
    try:
        # On lance le script de restore créé précédemment
        subprocess.run([sys.executable, RESTORE_SCRIPT], check=True)
        log("Système restauré. Tentative de redémarrage...", "INFO")
    except Exception as e:
        log(f"ÉCHEC DE LA RESTAURATION : {e}", "ERROR")

def main():
    retries = 0
    
    log("Démarrage du système de surveillance...")
    log(f"Cible : {NEXUS_SCRIPT}")

    while True:
        start_time = time.time()
        
        try:
            # 1. Lancement de Nexus
            log("Lancement de Prométhée...", "INFO")
            process = subprocess.Popen([sys.executable, NEXUS_SCRIPT])
            
            # 2. Surveillance
            try:
                process.wait() # On attend qu'il finisse (ou plante)
            except KeyboardInterrupt:
                # Si VOUS faites Ctrl+C, on arrête tout proprement
                log("Arrêt manuel demandé.", "WARN")
                process.terminate()
                break

            # 3. Analyse de la mort
            return_code = process.returncode
            uptime = time.time() - start_time

            if return_code == 0:
                log("Prométhée s'est arrêté normalement.", "INFO")
                break # Arrêt volontaire
            else:
                # CRASH DÉTECTÉ
                log(f"CRASH DÉTECTÉ (Code: {return_code}) après {uptime:.1f} secondes.", "ERROR")
                
                # Si le crash est immédiat (< 30s), c'est sûrement une erreur de syntaxe due à une mutation
                if uptime < CRASH_WINDOW:
                    log("Crash au démarrage -> Mutation défectueuse probable.", "WARN")
                    run_restore() # On restaure les backups
                    retries += 1
                else:
                    log("Crash tardif -> Redémarrage simple.", "WARN")
                    # Fonctionnement stable (>30s) → reset partiel du compteur de crashs
                    if retries > 0:
                        retries = max(0, retries - 1)
                    # Mais si ça boucle quand même, on restore au bout de 3 essais
                    if retries > 2:
                        run_restore()
                
                # Sécurité Anti-Boucle
                if retries > MAX_RETRIES:
                    log("TROP DE CRASHES CONSÉCUTIFS. ABANDON.", "ERROR")
                    log("Veuillez vérifier les logs manuellement.", "ERROR")
                    break
                
                log(f"Redémarrage dans 3 secondes... (Essai {retries}/{MAX_RETRIES})", "INFO")
                time.sleep(3)

        except Exception as e:
            log(f"Erreur fatale du Guardian : {e}", "ERROR")
            break

if __name__ == "__main__":
    main()