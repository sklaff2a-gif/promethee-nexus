# PROMETHEE NEXUS

[![Tests](https://github.com/sklaff2a-gif/promethee-nexus/actions/workflows/tests.yml/badge.svg)](https://github.com/sklaff2a-gif/promethee-nexus/actions/workflows/tests.yml)

> **Version 14.0** — API v14.0 | Restructuration 2026

Systeme multi-agents IA autonome avec orchestration intelligente, memoire vectorielle (RAG), routage adaptatif Cloud/Local et agents ephemeres auto-generables.

PROMETHEE coordonne 10 agents permanents et un nombre illimite d'agents ephemeres (Eidolons) qui collaborent pour generer du code, analyser des architectures, effectuer de la veille technologique, produire du contenu et s'auto-ameliorer -- le tout avec des mecanismes de securite integres (kill switch, watchdog, restauration d'urgence, validation architecte).

---

## Fiche technique

| Caracteristique | Detail |
|-----------------|--------|
| **Nom** | PROMETHEE Nexus |
| **Version** | 14.0.0 (Multi-Model Matrix) |
| **Type** | Systeme multi-agents IA autonome |
| **Langage** | Python 3.10+ |
| **Framework** | FastAPI + Uvicorn |
| **Communication** | WebSocket temps reel + Event Bus pub/sub |
| **Agents permanents** | 10 specialises (heritent de BaseAgent) |
| **Agents ephemeres** | Illimite via Grimoire/Summoner |
| **Memoire** | ChromaDB (RAG vectoriel persistant) |
| **LLM Locaux** | Ollama (gemma3:12b, deepseek-r1:8b, qwen3:8b, gpt-oss:20b) |
| **LLM Cloud** | Google Gemini (2.5-flash, 2.5-pro, deep-research, gemma-3-27b) |
| **Strategie** | Local-First avec Cloud-Escalation conditionnelle |
| **Recherche Web** | SerpAPI (Google) + DuckDuckGo (fallback) |
| **Frontend** | HTML/Tailwind CSS/Chart.js/WebSocket |
| **Securite** | Kill switch, Guardian watchdog, Architect validator, backups .bak |
| **Autonomie** | Routines automatiques apres 5min d'inactivite (budget 20/jour) |

---

## Stack technique detaillee

| Couche | Technologies | Role |
|--------|-------------|------|
| **Serveur** | FastAPI, Uvicorn, WebSockets | API REST + flux temps reel |
| **IA Cloud** | Google Gemini (7 modeles en cascade) | Taches complexes avec fallback automatique |
| **IA Locale** | Ollama (4+ modeles par agent) | Taches simples, evaluation de complexite, routage |
| **Memoire** | ChromaDB | RAG vectoriel persistant, anti-doublon |
| **Recherche** | SerpAPI, DuckDuckGo | Veille web avec fallback |
| **Monitoring** | psutil | CPU, RAM, GPU, sante systeme |
| **Crypto** | Web3, cryptography | Integrations blockchain |
| **Frontend** | HTML, Tailwind CSS, Chart.js | Dashboard cyberpunk avec graphiques |
| **Tests** | pytest, pytest-asyncio | 84+ tests automatises (CI/CD GitHub Actions) |

### Matrice de routage Cloud (par agent)

Chaque agent dispose d'une cascade de modeles Cloud, utilises uniquement pour les taches jugees complexes :

| Agent | Modele 1 (Ideal) | Modele 2 (Rapide) | Modele 3 (Secours) |
|-------|-------------------|--------------------|--------------------|
| strategist | Gemini 2.5 Pro | Gemini 2.5 Flash | Gemini 2.0 Flash |
| architect | Gemini 2.5 Pro | Gemini 2.5 Flash | Gemini 2.0 Flash |
| coder | Gemma 3 27B | Gemini 2.5 Pro | Gemini 2.5 Flash |
| researcher | Deep Research Pro | Gemini 2.5 Pro | Gemini 2.5 Flash |
| evolution | Agentic (Computer Use) | Gemini 2.5 Pro | Gemini 2.5 Flash |
| writer | Gemini 2.5 Pro | Gemini 2.5 Flash | Gemini 2.0 Flash |
| factory/infra/security | Gemini 2.5 Flash | Gemini 2.0 Flash | - |

### Modeles locaux Ollama (par agent)

| Agent | Modele local |
|-------|-------------|
| coder | deepseek-r1:8b |
| strategist | gpt-oss:20b |
| architect | gemma3:12b |
| writer | gemma3:12b |
| factory | qwen3:8b |
| infra | qwen3:8b |
| security | deepseek-r1:8b |
| researcher | qwen3-vl:8b |
| *evaluateur de complexite* | gemma3:12b |
| *routeur semantique* | gemma3:12b |

---

## Prerequis

- Python 3.10+
- [Ollama](https://ollama.com/) installe et lance localement
- Un fichier `.env` a la racine du projet (voir section Configuration)

## Installation

```bash
cd PROMETHEE_V11_restructuration2026
pip install -r requirements.txt
```

Telecharger les modeles Ollama :

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

---

## Lancement

### Lanceur automatique (Windows)

Double-cliquer sur `start_promethee.bat` pour acceder au menu interactif :

```
[1] Production    (Guardian + crash-recovery)
[2] Developpement (auto-restart sur exit 65)
[3] Serveur seul  (FastAPI sur 127.0.0.1:8000)
```

### Mode Production (avec watchdog)

```bash
python guardian.py
```

Le Guardian surveille le processus principal. En cas de crash au demarrage (< 30s), il restaure automatiquement les fichiers `.bak` et relance le systeme. Securite anti-boucle apres 5 echecs consecutifs.

### Mode Developpement

```bash
python start_nexus.py
```

Lance le serveur avec une boucle de redemarrage automatique. Si un fichier systeme est modifie par un agent, le serveur se relance via le code de sortie 65 (Smart Restart).

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

---

## Architecture

### Vue d'ensemble

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
        │(4 niveaux) │ │Pub/Sub │ │  Engine   │
        └─────┬─────┘ └────────┘ └───────────┘
              │
        ┌─────▼──────┐
        │Orchestrator │  Dispatch, kill switch, chaines de reaction
        └─────┬──────┘
              │
     ┌────────┼────────────┬──────────────┐
     │        │            │              │
  ┌──▼──┐ ┌──▼───┐ ┌──────▼──┐  ┌────────▼───────┐
  │Coder│ │Archi.│ │Factory  │  │  7 autres      │  10 agents permanents
  └─────┘ └──────┘ └─────────┘  └────────────────┘
              │                          │
        ┌─────▼──────┐          ┌────────▼────────┐
        │  ChromaDB   │          │  Grimoire       │
        │  (memoire)  │          │  (Eidolons)     │  Agents ephemeres
        └────────────┘          └─────────────────┘
```

### Flux d'une mission

```
Utilisateur
  │
  ▼
POST /api/mission {"mission": "..."}
  │
  ▼
RouterAgent.classify_intent()
  ├─ Niveau 0 : Blind Trust ─── syntaxe "agent: commande" → routage direct
  ├─ Niveau 1 : Reflexe ─────── mots-cles (cpu, code, scan...) → agent connu
  ├─ Niveau 1.5 : Grimoire ──── consultation grimoire_index.json → agent ephemere
  └─ Niveau 2 : Semantique ──── appel LLM local (gemma3:12b) → decision IA
  │
  ▼
Orchestrator.dispatch_task(agent_cible, payload)
  ├─ Agent en memoire ? → execution directe
  └─ Agent inconnu ? → Summoner.load() → instanciation Eidolon
  │
  ▼
Agent.process_task(task_payload)
  ├─ _evaluate_complexity() via Ollama → Simple ou Complexe ?
  ├─ Simple → Ollama local (modele specifique par agent)
  └─ Complexe → Gemini Cloud (cascade de fallback entre modeles)
  │
  ▼
bus.publish("AGENT_RESPONSE") → WebSocket → Frontend
  │
  ▼
Post-traitement automatique :
  ├─ Strategic Feedback Loop (Strategist audite → Architect valide)
  ├─ Bridge Architect→Factory (code valide → ecriture fichier)
  ├─ Chain Coder/Evolution→Architect (code detecte → validation auto)
  └─ Dissipation Eidolon (si agent ephemere → nettoyage memoire)
```

### Strategie LLM : Local-First & Cloud-Escalation

```
Mission entrante
  │
  ▼
Evaluateur de Complexite (Ollama gemma3:12b)
  │
  ├─ "NON" (Simple) ──────────────────────────────► Ollama Local
  │                                                  (modele specifique)
  │
  └─ "OUI" (Complexe) ──► Budget Cloud OK ? ──┐
                           │                   │
                           │ Non               │ Oui
                           ▼                   ▼
                        Ollama Local      Gemini Cloud
                        (fallback)        (cascade de modeles)
                                               │
                                          Echec ? → Ollama Local (fallback)
```

Le budget Cloud est plafonne a **100 appels/heure** (partage entre tous les agents, reset automatique).

---

## Les 10 agents permanents

Tous heritent de `BaseAgent` (`core/base_agent.py`) qui fournit :
- Routage LLM automatique (Local/Cloud) avec evaluation de complexite
- Memoire RAG (remember/recall) via ChromaDB avec protection anti-doublon
- Publication temps reel sur l'Event Bus
- Chargement dynamique de capabilities

| Agent | Role | Specialite | Modele local | Modele Cloud principal |
|-------|------|-----------|-------------|----------------------|
| **strategist** | COO | Optimisation processus, arbitrage, memorisation collective | gpt-oss:20b | Gemini 2.5 Pro |
| **coder** | Developpeur | Generation de code avec memoire RAG | deepseek-r1:8b | Gemma 3 27B |
| **architect** | Validateur | Evaluation des risques, approbation avant deploiement | gemma3:12b | Gemini 2.5 Pro |
| **factory** | Executeur | Creation/modification de fichiers sur disque | qwen3:8b | Gemini 2.5 Flash |
| **formatter** | Standardiseur | Formatage du code avant passage en factory | gemma3:12b | Gemini 2.5 Flash |
| **researcher** | Analyste | Recherche web (SerpAPI/DDG), ingestion de fichiers | qwen3-vl:8b | Deep Research Pro |
| **writer** | Redacteur | Generation de contenu, optimisation SEO | gemma3:12b | Gemini 2.5 Pro |
| **security** | Cyber-defense | Detection de menaces, analyse de vulnerabilites | deepseek-r1:8b | Gemini 2.5 Flash |
| **infra** | DevOps/SRE | Monitoring hardware (CPU, RAM, GPU), sante systeme | qwen3:8b | Gemini 2.5 Flash |
| **evolution** | R&D | Protocole d'auto-amelioration (Darwin) | gemma3:12b | Agentic (Computer Use) |

---

## Systeme Grimoire / Eidolons (agents ephemeres)

Le Grimoire est un systeme d'agents dynamiques charges a la demande et dissipes apres execution. Il permet d'etendre les capacites de PROMETHEE sans modifier le code source.

### Comment ca fonctionne

```
Mission inconnue des agents permanents
  │
  ▼
Summoner.load(slug) ──► Charge le fichier core/grimoire/{slug}.py
  │
  ▼
Instanciation ephemere ──► Agent execute la mission
  │
  ▼
Dissipation ──► Nettoyage sys.modules + liberation memoire
```

### Agents ephemeres inclus

| Eidolon | Classe | Specialite | Mots-cles de declenchement |
|---------|--------|-----------|---------------------------|
| `math_wizard` | MathWizard | Calculs mathematiques, equations, algebre | equation, calcul, mathematique, integrale, derivee, matrice |
| `dr_debug` | DrDebug | Diagnostic de bugs Python, analyse de tracebacks | traceback, debug, erreur python, exception, stacktrace, crash |
| `translator` | Translator | Traduction multilingue de textes | traduis, traduction, translate, japonais, anglais, espagnol |

### Catalogue (`grimoire_index.json`)

Le fichier `core/grimoire/grimoire_index.json` sert de catalogue pour le routage automatique. Le Router Niveau 1.5 le consulte (avec cache) pour decouvrir les agents ephemeres par mots-cles.

### Auto-ecriture de recettes (`GrimoireWriter`)

Le module `core/grimoire_writer.py` permet de creer de nouvelles recettes programmatiquement :

```python
from core.grimoire_writer import GrimoireWriter

result = GrimoireWriter.write_recipe(
    slug="data_analyst",
    name="DataAnalyst",
    description="Analyse de donnees et statistiques",
    keywords=["statistique", "analyse", "dataset", "csv"],
    code=code_source
)
```

**Validations de securite obligatoires :**
- Le slug doit etre alphanum + underscore, commencant par une lettre
- Le code doit contenir une classe heritant de `BaseAgent`
- Taille maximale : 50 KB
- **Blacklist** : `os.system`, `subprocess`, `shutil.rmtree`, `exec(`, `eval(`, `__import__` sont interdits
- Pas de doublon (verification d'existence du fichier)
- Mise a jour automatique de `grimoire_index.json` + invalidation du cache Router

---

## Chaines de reaction automatiques

### Quality Control Pipeline

```
Factory cree un fichier .py (ARTIFACT_CREATED)
  │
  ▼
Coder audite le code automatiquement
  │
  ├─ Code valide → Strategist memorise dans la base RAG
  └─ Fichier systeme modifie → Smart Restart (exit code 65)
```

### Strategic Feedback Loop

```
Agent termine une mission (AGENT_RESPONSE)
  │
  ▼
Strategist audite la reponse
  │
  ▼
Architect valide les propositions d'amelioration
```

### Bridge Architect → Factory

```
Architect valide du code (reponse commence par "VALIDE")
  │
  ▼
Orchestrator detecte du code Python structurel (>= 2 patterns)
  │
  ▼
Factory recoit le code pour ecriture sur disque
```

### Chain Evolution/Coder → Architect

```
Coder ou Evolution produit du code Python
  │
  ▼
Orchestrator detecte >= 2 patterns structurels (import, class, def...)
  │
  ▼
Architect est declenche pour validation automatique
```

### Moteur d'autonomie

Apres 5 minutes d'inactivite utilisateur, le moteur d'autonomie lance des routines aleatoires :

| Routine | Agent | Action |
|---------|-------|--------|
| EXPANSION_CODE | evolution | Analyse et optimise un fichier aleatoire |
| AUDIT_STRUCTURE | architect | Verifie les fichiers temporaires a la racine |
| VEILLE_SILENCIEUSE | researcher | Cherche une astuce Python utile |

**Protections :**
- Verrou anti-concurrence (`is_processing`)
- Cooldown de 30 secondes entre chaque routine
- Budget quotidien de 20 routines maximum
- Respect du kill switch

---

## Evenements Bus

Le bus pub/sub en memoire (`core/event_bus/bus.py`) est le systeme nerveux du projet. Tous les agents communiquent via ces evenements :

| Evenement | Emetteur | Declencheur |
|-----------|----------|-------------|
| `USER_COMMAND` | main.py | Mission utilisateur recue |
| `AGENT_TASK_DISPATCH` | Orchestrator | Agent demarre une tache |
| `AGENT_RESPONSE` | BaseAgent | Agent termine une mission |
| `ARTIFACT_CREATED` | Factory | Fichier cree/modifie sur disque |
| `THOUGHT_STREAM` | BaseAgent | Pensee intermediaire d'un agent |
| `SYSTEM_OVERRIDE` | main.py | Kill switch active/desactive |

Un subscriber wildcard (`*`) recoit tous les evenements (utilise pour le WebSocket frontend).

---

## API

### Endpoints HTTP

| Methode | Route | Description | Body |
|---------|-------|-------------|------|
| `GET` | `/` | Dashboard de monitoring (interface cyberpunk) | - |
| `POST` | `/api/mission` | Envoyer une mission | `{"mission": "..."}` |
| `POST` | `/api/override` | Activer/desactiver le kill switch | `{"active": true}` |

### WebSocket

```
ws://127.0.0.1:8000/ws
```

Flux temps reel des evenements : `AGENT_RESPONSE`, `THOUGHT_STREAM`, `ARTIFACT_CREATED`, `USER_COMMAND`, `SYSTEM_OVERRIDE`.

---

## Structure du projet

```
PROMETHEE_V11_restructuration2026/
├── main.py                  # Point d'entree FastAPI + Quality Control
├── start_nexus.py           # Lanceur avec boucle de redemarrage (exit 65)
├── guardian.py              # Watchdog crash-recovery (5 retries max)
├── emergency_restore.py     # Restauration des .bak
├── config.py                # Matrice de routage multi-modeles (7 Cloud + 8 Local)
├── start_promethee.bat      # Lanceur Windows avec menu interactif
├── requirements.txt         # 13 dependances Python
├── .env                     # Cles API (non versionne)
│
├── Agents/                  # Les 10 agents specialises
│   ├── strategist.py
│   ├── coder.py
│   ├── architect.py
│   ├── factory.py
│   ├── formatter.py
│   ├── researcher.py
│   ├── writer.py
│   ├── security.py
│   ├── infra.py
│   └── evolution.py
│
├── core/
│   ├── base_agent.py        # Classe mere V21 (RAG, LLM, Bus, Budget Cloud)
│   ├── orchestrator.py      # Dispatch, kill switch, chaines de reaction, dissipation
│   ├── router.py            # RouterAgent V2.3 (4 niveaux : Blind Trust → Reflexe → Grimoire → LLM)
│   ├── autonomy_engine.py   # Routines autonomes V23 (Anti-Storm + Budget)
│   ├── summoner.py          # Chargement dynamique de modules (Grimoire)
│   ├── grimoire_writer.py   # Auto-ecriture de recettes avec validations de securite
│   ├── event_bus/
│   │   └── bus.py           # Bus pub/sub singleton (sync + async + wildcard)
│   ├── capabilities/        # Modules reutilisables (web_surfer, etc.)
│   ├── grimoire/            # Agents ephemeres (Eidolons)
│   │   ├── grimoire_index.json  # Catalogue des agents avec mots-cles
│   │   ├── math_wizard.py       # Mathematicien ephemere
│   │   ├── dr_debug.py          # Diagnosticien Python
│   │   └── translator.py        # Traducteur multilingue
│   └── memory/
│       └── vector_store.py  # Wrapper ChromaDB singleton
│
├── memory/chroma_db/        # Stockage vectoriel persistant
├── static/                  # Frontend (HTML, JS, CSS)
├── tests/                   # 84+ tests automatises
│   ├── conftest.py
│   ├── test_event_bus.py
│   ├── test_factory.py
│   ├── test_orchestrator.py
│   ├── test_router.py
│   └── test_grimoire.py
├── USER_DROPZONE/           # Zone d'ingestion de fichiers (Researcher)
└── _FACTORY_HISTORY.txt     # Journal des operations Factory
```

---

## Securite

| Mecanisme | Description |
|-----------|-------------|
| **Kill Switch** | Arret immediat de tous les agents via `POST /api/override` |
| **Guardian Watchdog** | Detection de crash < 30s, restauration .bak automatique, anti-boucle (5 retries) |
| **Architect Validator** | Revue de code avec niveaux de risque (CRITICAL/MEDIUM/LOW) avant execution |
| **Backups automatiques** | La Factory cree un `.bak` avant chaque modification de fichier |
| **Anti-Storm** | Verrou + cooldown 30s dans le moteur d'autonomie |
| **Budget Cloud** | 100 appels Cloud/heure maximum, compteur partage entre agents |
| **Budget Autonomie** | 20 routines autonomes/jour maximum |
| **Grimoire Sandboxing** | Blacklist de patterns dangereux, validation heritage BaseAgent, taille max 50KB |
| **Factory Sandboxing** | Extensions interdites (.exe, .bat, .sh), detection path traversal, taille max |
| **Summoner Validation** | Noms de modules alphanumeriques uniquement (anti-injection) |

---

## Tests

```bash
cd PROMETHEE_V11_restructuration2026
python -m pytest tests/ -v
```

84 tests automatises couvrant :
- **Event Bus** : singleton, subscribe/unsubscribe, publish sync/async, wildcard, gestion d'erreurs
- **Factory** : sandboxing (extensions, path traversal, taille), extraction de code
- **Orchestrator** : kill switch, dispatch, validation, chaines de reaction
- **Router** : 4 niveaux (Blind Trust, Reflexe, Grimoire, Semantique), cas limites
- **Grimoire** : Summoner (chargement, erreurs, injection), Router 1.5, dissipation, GrimoireWriter (securite)

Tous les tests fonctionnent sans Ollama, Gemini ou ChromaDB (tout est mocke).

---

## Roadmap

- [ ] Speech Synthesis (TTS) via `pyttsx3` / Web Speech API
- [ ] Computer Vision via OpenCV + WebSocket binaire
- [ ] Visualisation 3D du Knowledge Graph en WebGL
- [ ] Multi-projets : isolation des memoires vectorielles par contexte
- [ ] Agents collaboratifs : discussions inter-agents pour taches complexes
- [ ] Pipeline CI/CD interne : generation + tests + validation + deploiement automatise
