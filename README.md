# PROMETHEE NEXUS

Systeme multi-agents IA autonome avec orchestration intelligente, memoire vectorielle (RAG) et routage adaptatif Cloud/Local.

Promethee coordonne 10 agents specialises qui collaborent pour generer du code, analyser des architectures, effectuer de la veille technologique, produire du contenu et s'auto-ameliorer -- le tout avec des mecanismes de securite (kill switch, watchdog, restauration d'urgence).

## Stack technique

| Couche | Technologies |
|--------|-------------|
| Backend | Python 3, FastAPI, Uvicorn, WebSockets |
| IA Cloud | Google Gemini (2.5-flash, 2.5-pro, deep-research, gemma-3-27b) |
| IA Locale | Ollama (deepseek-r1:8b, qwen3:8b, gemma3:12b, gpt-oss:20b) |
| Memoire | ChromaDB (RAG vectoriel persistant) |
| Recherche Web | SerpAPI (Google), DuckDuckGo (fallback) |
| Frontend | HTML, Tailwind CSS, Chart.js, WebSocket temps reel |

## Prerequis

- Python 3.10+
- [Ollama](https://ollama.com/) installe et lance localement (pour le traitement local)
- Un fichier `.env` a la racine du projet (voir section Configuration)

## Installation

```bash
cd PROMETHEE_V11_restructuration2026
pip install -r requirements.txt
```

Telecharger les modeles Ollama utilises :

```bash
ollama pull gemma3:12b
ollama pull deepseek-r1:8b
ollama pull qwen3:8b
ollama pull gpt-oss:20b
```

## Configuration

Creer un fichier `.env` a la racine de `PROMETHEE_V11_restructuration2026/` :

```env
GOOGLE_API_KEY=votre_cle_gemini
SERPAPI_API_KEY=votre_cle_serpapi
OLLAMA_URL=http://localhost:11434/api/generate
CHROMA_DB_PATH=./memory/chroma_db
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO
```

`GOOGLE_API_KEY` est optionnel : sans elle, le systeme fonctionne en mode 100% local via Ollama.

## Lancement

### Mode Production (avec watchdog)

```bash
python guardian.py
```

Le Guardian surveille le processus principal. En cas de crash au demarrage (< 30s), il restaure automatiquement les fichiers `.bak` et relance le systeme. Securite anti-boucle apres 5 echecs consecutifs.

### Mode Developpement

```bash
python start_nexus.py
```

Lance le serveur avec une boucle de redemarrage automatique. Si un fichier systeme est modifie par un agent, le serveur se relance via le code de sortie 65.

### Serveur seul

```bash
python main.py
```

Lance FastAPI directement sur `http://127.0.0.1:8000`. L'interface de monitoring est accessible a la racine (`/`).

### Restauration d'urgence

```bash
python emergency_restore.py
```

Restaure tous les fichiers `.bak` dans `Agents/`, `core/` et la racine.

## Architecture

```
                    ┌──────────────┐
                    │  guardian.py  │  Watchdog & crash recovery
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │start_nexus.py│  Boucle de redemarrage (exit 65)
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │   main.py    │  FastAPI + WebSocket + Quality Control
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────▼─────┐ ┌───▼────┐ ┌────▼──────┐
        │  Router    │ │  Bus   │ │ Autonomy  │
        │ (3 niv.)   │ │Pub/Sub │ │  Engine   │
        └─────┬─────┘ └────────┘ └───────────┘
              │
        ┌─────▼──────┐
        │Orchestrator │  Dispatch, kill switch, chaines de reaction
        └─────┬──────┘
              │
     ┌────────┼────────────┬───────────┐
     │        │            │           │
  ┌──▼──┐ ┌──▼───┐ ┌──────▼──┐ ┌─────▼────┐
  │Coder│ │Archi.│ │Factory  │ │ 7 autres │  10 agents au total
  └─────┘ └──────┘ └─────────┘ └──────────┘
              │
        ┌─────▼──────┐
        │  ChromaDB   │  Memoire vectorielle persistante (RAG)
        └────────────┘
```

### Routage des missions

Le `RouterAgent` classe chaque mission utilisateur en 3 niveaux :

1. **Blind Trust** -- Syntaxe `agent_name: commande` pour adresser directement un agent (y compris les agents ephemeres du Grimoire)
2. **Reflexe** -- Detection par mots-cles (`code`, `cpu`, `scan`, `secu`, etc.)
3. **Semantique** -- Appel LLM local pour trancher les cas ambigus, fallback sur `strategist`

### Strategie LLM : Local-First & Cloud-Escalation

Chaque requete passe par un evaluateur de complexite (Ollama `gemma3:12b`) :

- **Tache simple** → traitement local via Ollama (modele specifique par agent)
- **Tache complexe** → escalade vers Google Gemini avec cascade de fallback entre modeles
- **Echec Cloud** → repli automatique sur le local

La matrice de routage par agent est definie dans `config.py` (`AGENT_MODEL_ROUTING`).

### Chaines de reaction automatiques

- **Quality Control** : Quand un fichier `.py` est cree, le Coder l'audite, le Strategist le memorise si valide, et un Smart Restart se declenche si c'est un fichier systeme
- **Feedback Loop** : Apres chaque mission, le Strategist audite la reponse, l'Architect valide les propositions d'amelioration
- **Bridge Architect → Factory** : Le code valide par l'Architect est automatiquement transmis a la Factory pour execution
- **Autonomie** : Apres 5 minutes d'inactivite, le moteur d'autonomie lance des routines (evolution, audit, veille) avec protection anti-tempete (cooldown 30s)

## Les 10 agents

| Agent | Role | Specialite |
|-------|------|-----------|
| **strategist** | COO | Optimisation processus, arbitrage, memorisation collective |
| **coder** | Developpeur | Generation de code avec memoire RAG |
| **architect** | Validateur | Evaluation des risques, approbation avant deploiement |
| **factory** | Executeur | Creation/modification de fichiers sur disque |
| **formatter** | Standardiseur | Formatage du code avant passage en factory |
| **researcher** | Analyste | Recherche web (SerpAPI/DDG), ingestion de fichiers |
| **writer** | Redacteur | Generation de contenu, optimisation SEO |
| **security** | Cyber-defense | Detection de menaces, analyse de vulnerabilites |
| **infra** | DevOps/SRE | Monitoring hardware (CPU, RAM, GPU), sante systeme |
| **evolution** | R&D | Protocole d'auto-amelioration (Darwin) |

Tous les agents heritent de `BaseAgent` (`core/base_agent.py`) qui fournit : routage LLM, memoire RAG (remember/recall), publication sur l'Event Bus, chargement dynamique de capabilities.

## API

### Endpoints HTTP

| Methode | Route | Description |
|---------|-------|-------------|
| `GET` | `/` | Interface de monitoring (dashboard cyberpunk) |
| `POST` | `/api/mission` | Envoyer une mission (`{"mission": "..."}`) |
| `POST` | `/api/override` | Activer/desactiver le kill switch (`{"active": true}`) |

### WebSocket

- `ws://127.0.0.1:8000/ws` -- Flux temps reel des evenements : `AGENT_RESPONSE`, `THOUGHT_STREAM`, `ARTIFACT_CREATED`, `USER_COMMAND`, `SYSTEM_OVERRIDE`

## Structure du projet

```
PROMETHEE_V11_restructuration2026/
├── main.py                  # Point d'entree FastAPI
├── start_nexus.py           # Lanceur avec boucle de redemarrage
├── guardian.py              # Watchdog crash-recovery
├── emergency_restore.py     # Restauration des .bak
├── config.py                # Matrice de routage multi-modeles
├── requirements.txt         # Dependances Python
├── .env                     # Cles API (non versionne)
├── Agents/                  # Les 10 agents specialises
├── core/
│   ├── orchestrator.py      # Dispatch et chaines de reaction
│   ├── base_agent.py        # Classe mere (RAG, LLM, Bus)
│   ├── router.py            # Classification d'intention 3 niveaux
│   ├── autonomy_engine.py   # Routines autonomes (Anti-Storm)
│   ├── summoner.py          # Chargement dynamique (Grimoire)
│   ├── event_bus/           # Bus pub/sub en memoire
│   ├── capabilities/        # Modules reutilisables (web_surfer, etc.)
│   ├── grimoire/            # Agents ephemeres chargeables a la demande
│   └── memory/              # Wrapper ChromaDB
├── memory/chroma_db/        # Stockage vectoriel persistant
├── static/                  # Frontend (HTML, JS, CSS)
├── USER_DROPZONE/           # Zone d'ingestion de fichiers
└── _FACTORY_HISTORY.txt     # Journal des operations Factory
```

## Securite

- **Kill Switch** : Arret immediat de tous les agents via `/api/override`
- **Guardian Watchdog** : Detection de crash, restauration automatique, anti-boucle (5 retries max)
- **Architect Validator** : Revue de code avec niveaux de risque (CRITICAL, MEDIUM, LOW) avant execution
- **Backups automatiques** : La Factory cree un `.bak` avant chaque modification
- **Cooldown Anti-Storm** : Le moteur d'autonomie integre un verrou et un cooldown de 30s pour empecher les boucles d'evenements

## Roadmap UI

- Speech Synthesis (TTS) via `pyttsx3` / Web Speech API
- Computer Vision via OpenCV + WebSocket binaire
- Visualisation 3D du Knowledge Graph en WebGL
