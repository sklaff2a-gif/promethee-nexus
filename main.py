# main.py
from fastapi import FastAPI, WebSocket, Request, BackgroundTasks, Depends, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from contextlib import asynccontextmanager
import os
import asyncio
import json
import importlib
import uvicorn
import tracemalloc
import secrets
import sys
from core.orchestrator import orchestrator
from core.event_bus.bus import bus
from core.autonomy_engine import autonomy
from core.router import RouterAgent

# --- AUTHENTIFICATION API ---
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")
security = HTTPBearer()

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Vérifie le token Bearer sur les endpoints API."""
    if not API_SECRET_KEY:
        return  # Pas de clé configurée = mode ouvert (dev local)
    if not secrets.compare_digest(credentials.credentials, API_SECRET_KEY):
        raise HTTPException(status_code=401, detail="Token invalide")

def verify_ws_token(token: str) -> bool:
    """Vérifie le token pour les connexions WebSocket."""
    if not API_SECRET_KEY:
        return True
    return secrets.compare_digest(token, API_SECRET_KEY)

# Configuration nettoyée (Agents financiers retirés)
AGENTS_CONFIG = [
    ("strategist", "DivineStrategist", "strategist_agent"),
    ("coder", "DivineCoder", "coder_agent"),
    ("architect", "DivineArchitect", "architect_agent"),
    ("factory", "DivineFactory", "factory_agent"), 
    ("evolution", "DivineEvolution", "evolution_agent"), 
    ("infra", "DivineInfra", "infra_agent"),
    ("security", "DivineSecurity", "security_agent"),
    ("writer", "DivineWriter", "writer_agent"),
    ("researcher", "DivineResearcher", "researcher_agent"),
    ("formatter", "DivineFormatter", "formatter_agent"), # <--- AJOUT VITAL : L'Agent Formatter
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    tracemalloc.start()
    print(f"🤖 PROMÉTHÉE V12.4 (Smart Restart): Chargement des modules...")
    for slug, class_name, file_name in AGENTS_CONFIG:
        try:
            module = importlib.import_module(f"Agents.{file_name}")
            AgentClass = getattr(module, class_name)
            await orchestrator.register_agent(slug, AgentClass())
            print(f"   [OK] {slug.upper()}")
        except Exception as e:
            print(f"   [ERR] {slug}: {e}")
    
    print("   🧠 Autonomie & Gouvernance : ACTIVES.")
    bus.subscribe("AGENT_TASK_DISPATCH", nervous_system_listener)
    
    # --- AMÉLIORATION A + B : FEEDBACK LOOP + MÉMOIRE ---
    # On écoute la création de fichiers pour lancer le pipeline de contrôle et mémorisation
    bus.subscribe("ARTIFACT_CREATED", quality_control_listener)

    asyncio.create_task(autonomy.start_loop())
    yield
    print("🔌 Arrêt.")
    tracemalloc.stop()

async def nervous_system_listener(event: dict):
    data = event.get("data", {})
    target = data.get("target")
    payload = data.get("payload")
    if target and payload:
        print(f"⚡ [NERVOUS SYSTEM] Réflexe déclenché vers -> {target.upper()}")
        asyncio.create_task(orchestrator.dispatch_task(target, payload))

async def quality_control_listener(event: dict):
    """
    AMÉLIORATION A + B : Point d'entrée du contrôle qualité.
    Déclenche le pipeline asynchrone pour ne pas bloquer le bus.
    """
    data = event.get("data", {})
    filepath = data.get("filepath")
    filename = data.get("filename")
    
    if filepath and filename and filename.endswith(".py"):
        print(f"🕵️ [QUALITY CONTROL] Vérification demandée pour : {filename}")
        # Lancement du pipeline en tâche de fond (Fire & Forget)
        asyncio.create_task(run_qc_pipeline(filename, filepath))

async def run_qc_pipeline(filename: str, filepath: str):
    """
    Pipeline séquentiel :
    1. Coder analyse le fichier.
    2. Si SUCCÈS -> Stratège mémorise le code (Auto-RAG).
    3. Si FICHIER SYSTÈME -> Redémarrage intelligent (Smart Restart).
    """
    qc_mission = f"AUDIT_QUALITE: Le fichier '{filename}' vient d'être généré. Analyse-le. S'il est valide et fonctionnel, réponds par 'SUCCÈS' suivi d'une brève analyse. Sinon, propose un correctif."
    
    # 1. Appel Coder (On attend la réponse ici)
    response = await orchestrator.dispatch_task("coder", {
        "mission": qc_mission,
        "context": f"CHEMIN_COMPLET: {filepath}"
    })
    
    # 2. Logique de Mémorisation (Auto-RAG)
    if response and response.get("status") == "success":
        result_text = str(response.get("result", "")).upper()
        
        # Si le Coder valide explicitement
        if "SUCCÈS" in result_text or "VALIDE" in result_text or "FONCTIONNE" in result_text:
            print(f"🧠 [MÉMOIRE] Capitalisation du succès : {filename}")
            
            # On demande au Stratège d'indexer ce savoir
            await orchestrator.dispatch_task("strategist", {
                "mission": f"MÉMORISATION: Le script '{filename}' est validé et fonctionnel. Ajoute-le à la mémoire collective (collective_wisdom) pour qu'il serve d'exemple futur.",
                "context": f"FICHIER: {filename}\nCHEMIN: {filepath}\nANALYSE CODER: {result_text[:500]}"
            })

    # 3. DÉTECTION DE MISE À JOUR SYSTÈME (Smart Restart)
    # Si le fichier modifié touche au cerveau (Agents, core, ou main), on redémarre APRES le travail.
    # On vérifie si le chemin contient des dossiers critiques
    is_system_file = any(k in filepath for k in ["Agents", "core", "main.py", "config.py"])
    
    if is_system_file:
        print(f"🔄 [SYSTÈME] Modification structurelle détectée ({filename}). Redémarrage requis.")
        print("⏳ Attente de finalisation des tâches (3s)...")
        await asyncio.sleep(3) # On laisse le temps au Bus de finir ses messages et à la mémoire de s'écrire
        sys.exit(65) # Ce code signalera à start_nexus.py de relancer la boucle

app = FastAPI(lifespan=lifespan)

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    if os.path.exists("static/index.html"):
        with open("static/index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>UI Loading...</h1>")

@app.post("/api/override", dependencies=[Depends(verify_token)])
async def api_override(request: Request):
    data = await request.json()
    active = data.get("active", False)
    await orchestrator.set_kill_switch(active)
    await bus.publish("SYSTEM_OVERRIDE", {"active": active})
    return {"status": "ok", "kill_switch": active}

async def strategic_feedback_loop(agent_name: str, mission: str, result: str):
    if agent_name in ["strategist", "architect", "factory"]: return
    await bus.publish("THOUGHT_STREAM", {
        "agent": "GOUVERNANCE", 
        "content": f"Analyse de la performance de {agent_name}...", 
        "type": "info"
    })
    audit_mission = f"AUDIT_STRATEGIQUE: L'agent {agent_name} a fini une tâche. Analyse le résultat ci-joint. S'il est incomplet ou erroné, propose un correctif."
    audit_context = f"MISSION: {mission}\n\nRÉSULTAT:\n{result[:3000]}"
    
    strat_res = await orchestrator.dispatch_task("strategist", {
        "mission": audit_mission,
        "context": audit_context
    })
    
    if not strat_res or strat_res.get("status") != "success": return
    strategy_proposal = strat_res.get("result", "")

    if "R.A.S" in strategy_proposal or "OPTIMAL" in strategy_proposal: return

    validation_mission = f"VALIDATION_STRATEGIE: Le Stratège propose cette amélioration pour {agent_name}. Si valide, transforme en 'ORDRE_USINE'."
    await orchestrator.dispatch_task("architect", {
        "mission": validation_mission,
        "context": strategy_proposal
    })

@app.post("/api/mission", dependencies=[Depends(verify_token)])
async def mission(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    msn = data.get("mission", "")
    await bus.publish("USER_COMMAND", {"mission": msn})
    
    # [V13.3] Utilisation du Router dédié
    target = await RouterAgent.classify_intent(msn)

    response = await orchestrator.dispatch_task(target, {"mission": msn})
    
    if response and response.get("status") == "success":
        result_text = str(response.get("result", ""))
        background_tasks.add_task(strategic_feedback_loop, target, msn, result_text)

    return {"status": "dispatched", "target": target}

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str = Query(default="")):
    if not verify_ws_token(token):
        await websocket.close(code=1008, reason="Token invalide")
        return
    await websocket.accept()
    async def sender(event):
        try:
            await websocket.send_text(json.dumps({"type": event.get("type"), "payload": event.get("data")}))
        except Exception:
            pass  # WebSocket déconnecté, normal en fin de session
    bus.subscribe("*", sender)
    try:
        while True: await websocket.receive_text()
    except Exception:
        pass  # Client déconnecté, fin normale du WebSocket
    finally:
        bus.unsubscribe("*", sender)

if __name__ == "__main__":
    print("🔥 Démarrage via Lanceur Direct...")
    # RELOAD=FALSE OBLIGATOIRE : On gère le restart nous-mêmes via sys.exit(65)
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)