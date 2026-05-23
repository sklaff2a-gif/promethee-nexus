import subprocess
import time
import sys
import os
from collections import deque

# CONFIGURATION
NEXUS_SCRIPT = "start_nexus.py"
RESTORE_SCRIPT = "emergency_restore.py"
MAX_RETRIES = 5  # Sécurité anti-boucle infinie
CRASH_WINDOW = 30 # Si ça plante en moins de 30s, c'est un crash de démarrage (mauvais code)

# --- Détection de GEL via heartbeat-fichier (debat 4/4 du 23/05) ---
# main.py écrit memory/heartbeat.txt toutes les 30s. Si l'âge dépasse
# HEARTBEAT_MAX_AGE, c'est un gel (process vivant mais boucle morte) -> kill+restart.
HEARTBEAT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "heartbeat.txt")
HEARTBEAT_MAX_AGE = 300          # 5 min : seuil de détection du gel
HEARTBEAT_CHECK_INTERVAL = 10    # poll toutes les 10s pendant surveillance
GEL_RESTART_WINDOW = 1800        # 30 min : fenêtre du circuit breaker
GEL_RESTART_MAX = 3              # max 3 gel-restarts par fenêtre, sinon escalation

def log(message, type="INFO"):
    prefix = {"INFO": "🛡️ [GUARDIAN]", "WARN": "⚠️ [GUARDIAN]", "ERROR": "🚑 [GUARDIAN]"}
    print(f"{prefix.get(type, 'INFO')} {message}")


def _send_telegram_alert(text: str) -> bool:
    """Envoie une alerte Telegram directement via l'API Bot, sans dépendance lourde.
    Lit TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID depuis .env (parsing manuel).
    Guardian tourne dans un process séparé : il ne peut PAS utiliser le bus
    event-driven d'outreach (qui vit dans la RAM de main.py). D'où cette voie
    directe, autonome, fonctionnelle même si tout le reste est gelé.
    Retourne True si l'envoi a réussi (HTTP 200), False sinon.
    """
    import urllib.request
    import json as _json
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    token = chat_id = None
    try:
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("TELEGRAM_CHAT_ID="):
                        chat_id = line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        return False
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = _json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False

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
    # Sliding window des timestamps de restart pour cause "gel" (validation
    # simulateur 23/05). Une fenetre fixe avec reset etait moins rigoureuse :
    # elle permettait 6 gels en 50min sans declencher le breaker.
    gel_restart_timestamps: deque = deque()

    log("Démarrage du système de surveillance...")
    log(f"Cible : {NEXUS_SCRIPT}")

    while True:
        start_time = time.time()
        gel_detected = False

        try:
            # 1. Lancement de Nexus
            log("Lancement de Prométhée...", "INFO")
            process = subprocess.Popen([sys.executable, NEXUS_SCRIPT])

            # 2. Surveillance : crash (process.poll) OU gel (heartbeat trop vieux).
            try:
                while True:
                    rc = process.poll()
                    if rc is not None:
                        break  # process terminé (crash ou exit normal)
                    if os.path.exists(HEARTBEAT_PATH):
                        age = time.time() - os.path.getmtime(HEARTBEAT_PATH)
                        if age > HEARTBEAT_MAX_AGE:
                            log(f"GEL DETECTE : heartbeat vieux de {age:.0f}s (> {HEARTBEAT_MAX_AGE}s) -> kill+restart", "WARN")
                            process.terminate()
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                process.kill()
                            gel_detected = True
                            break
                    time.sleep(HEARTBEAT_CHECK_INTERVAL)
            except KeyboardInterrupt:
                # Si VOUS faites Ctrl+C, on arrête tout proprement
                log("Arrêt manuel demandé.", "WARN")
                process.terminate()
                break

            # 2.5 — gel détecté : circuit breaker AVEC FENETRE GLISSANTE
            if gel_detected:
                now = time.time()
                # Nettoie les timestamps sortis de la fenetre glissante
                while gel_restart_timestamps and now - gel_restart_timestamps[0] > GEL_RESTART_WINDOW:
                    gel_restart_timestamps.popleft()
                gel_restart_timestamps.append(now)
                count = len(gel_restart_timestamps)
                if count > GEL_RESTART_MAX:
                    log(f"CIRCUIT BREAKER : {count} gels dans la fenetre glissante de {GEL_RESTART_WINDOW//60}min — ESCALATION HUMAINE", "ERROR")
                    log("Le systeme se gele de maniere recurrente. Verification manuelle requise.", "ERROR")
                    alert_msg = (
                        f"🚨 GUARDIAN CIRCUIT BREAKER\n"
                        f"{count} gels dans les {GEL_RESTART_WINDOW//60} dernières min sur Prométhée.\n"
                        f"Redémarrage automatique suspendu. Intervention manuelle requise."
                    )
                    if _send_telegram_alert(alert_msg):
                        log("Alerte Telegram envoyée à Jean-Michel.", "INFO")
                    else:
                        log("Alerte Telegram non envoyée (token/chat_id manquants ou reseau KO).", "WARN")
                    break
                log(f"Redemarrage apres gel #{count}/{GEL_RESTART_MAX} dans 3s...", "INFO")
                time.sleep(3)
                continue

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