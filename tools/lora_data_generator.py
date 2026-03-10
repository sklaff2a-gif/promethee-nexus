"""Generateur de donnees d'entrainement QLoRA par distillation Gemini.

Envoie des prompts diversifies a Gemini Flash (pas cher, 2000/jour)
et sauvegarde les reponses dans training_pairs.jsonl au format standard.

Le modele local apprend ensuite a imiter les reponses Cloud.

Usage :
  python tools/lora_data_generator.py                    # Tous les agents
  python tools/lora_data_generator.py --agent coder      # Un agent specifique
  python tools/lora_data_generator.py --max-calls 20     # Limiter les appels
  python tools/lora_data_generator.py --dry-run           # Preview sans appel API
"""

import argparse
import json
import os
import sys
import time
import logging
import random

logger = logging.getLogger("LoRADataGen")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Fichier de sortie (meme format que base_agent._save_training_pair)
TRAINING_PAIRS_FILE = os.path.join(PROJECT_ROOT, "memory", "training_pairs.jsonl")

# Modele economique pour la generation
GENERATION_MODEL = "models/gemini-2.5-flash"
CALLS_PER_MINUTE = 10   # Bien en dessous de la limite 250 RPM
DELAY_BETWEEN_CALLS = 60.0 / CALLS_PER_MINUTE  # 6 secondes

# Longueurs
MIN_RESPONSE_LENGTH = 100
MAX_RESPONSE_LENGTH = 4000

# --- Systeme prompts (identiques a lora_data_collector.py) ---

SYSTEM_PROMPTS = {
    "coder": (
        "Tu es l'agent Coder de Promethee, un systeme multi-agents IA autonome "
        "ecrit en Python (FastAPI, asyncio, Ollama). "
        "Tu ecris du code Python propre, teste, respectant les conventions du projet. "
        "Tu ne proposes jamais de dependances externes non approuvees. "
        "Le projet utilise : FastAPI, httpx, chromadb, networkx, asyncio. "
        "Pas de Django, Flask, OpenAI, pygame, blockchain, Docker, Kubernetes. "
        "IMPORTANT : Fournis TOUJOURS le code complet et fonctionnel, pas juste une description. "
        "Inclus les imports, les docstrings, et les exemples d'utilisation."
    ),
    "researcher": (
        "Tu es l'agent Researcher de Promethee, expert en recherche et synthese. "
        "Tu analyses les informations, identifies les tendances et produis des rapports "
        "structures et actionables. Tu fournis toujours des sources et du contexte. "
        "IMPORTANT : Donne des reponses detaillees et structurees (500+ mots minimum). "
        "Utilise des sections, des listes et des arguments etayes."
    ),
    "writer": (
        "Tu es l'agent Writer de Promethee, expert en redaction technique. "
        "Tu produis du contenu clair, structure et adapte au contexte. "
        "Tu ecris en francais avec precision. "
        "IMPORTANT : Donne des reponses completes et detaillees (500+ mots minimum). "
        "Structure avec des titres, sous-titres, et examples concrets."
    ),
    "strategist": (
        "Tu es l'agent Strategist de Promethee, expert en analyse et planification. "
        "Tu identifies les priorites, evalues les risques et proposes des plans d'action "
        "concrets. Tu structures tes analyses avec des criteres mesurables. "
        "IMPORTANT : Donne des analyses detaillees et structurees (500+ mots minimum). "
        "Inclus des metriques, des criteres de succes, et des plans d'action concrets."
    ),
}

# --- Banques de prompts par agent ---

PROMPT_BANK = {
    "coder": [
        # Fonctions async
        "Ecris une fonction async Python qui effectue un health check HTTP sur plusieurs endpoints en parallele avec httpx. Retourne un dict {url: bool}.",
        "Ecris une classe Python AsyncRateLimiter qui limite le nombre d'appels async par fenetre de temps. Utilise asyncio.Semaphore et un compteur temporel.",
        "Ecris une fonction async qui lit un fichier JSONL ligne par ligne de maniere non-bloquante avec aiofiles, en filtrant les lignes selon un predicat.",
        "Ecris un decorateur Python async `@retry(max_attempts=3, delay=1.0)` qui reessaye une coroutine en cas d'exception, avec backoff exponentiel.",
        "Ecris une fonction async `batch_process(items, func, batch_size=10)` qui traite des elements par lots en parallele avec asyncio.gather.",
        # Classes et patterns
        "Ecris une classe Python Singleton thread-safe avec reset_singleton() pour les tests. Utilise un class-level lock.",
        "Ecris une classe EventEmitter simple en Python avec subscribe(event, callback), unsubscribe, et publish(event, data) async.",
        "Ecris une classe CircularBuffer[T] generique en Python avec append, get_last(n), et __iter__. Taille fixe, ecrase les anciens.",
        "Ecris une classe StateManager qui persiste un dict en JSON, avec load(), save(), get(key, default), et set(key, value). Thread-safe.",
        "Ecris une classe Python PriorityQueue basee sur heapq qui supporte les priorites (0=haute) et le FIFO pour les elements de meme priorite.",
        # Traitement de donnees
        "Ecris une fonction Python qui parse un fichier de logs structure (timestamp, level, source, message) et retourne des statistiques : count par level, top 5 sources, erreurs par heure.",
        "Ecris une fonction qui deduplique une liste de dicts Python en se basant sur un sous-ensemble de cles, en gardant le plus recent (champ timestamp).",
        "Ecris une fonction Python qui calcule un hash de contenu pour detecter les changements dans un fichier, en ignorant les lignes de commentaires et les espaces.",
        "Ecris une fonction qui merge deux dicts JSON profondement (deep merge), ou les listes sont concatenees et les valeurs scalaires ecrasees.",
        "Ecris une fonction Python qui valide un schema de configuration (dict) contre un schema attendu, retournant les erreurs (cles manquantes, types incorrects).",
        # Tests
        "Ecris des tests pytest pour une classe AsyncCache avec get, set, invalidate et TTL. Utilise pytest-asyncio et des mocks pour le temps.",
        "Ecris une fixture pytest qui cree un serveur HTTP temporaire (aiohttp) retournant des reponses configurables, pour tester du code client.",
        "Ecris des tests pytest pour une fonction de parsing de logs. Couvre : fichier vide, lignes malformees, encodage UTF-8, fichier enorme (streaming).",
        # Refactoring
        "Refactorise ce code pour eliminer la duplication : deux fonctions identiques sauf le nom du modele et le timeout. Propose une abstraction.",
        "Analyse ce snippet Python et propose des ameliorations : gestion d'erreur, typing, docstrings, et simplification des conditions imbriquees.",
        # Debug
        "Explique pourquoi ce code async deadlock : deux coroutines qui attendent chacune le resultat de l'autre via asyncio.Queue. Propose un fix.",
        "Diagnostique ce bug : une liste en parametre par defaut d'une fonction est partagee entre les appels. Explique le probleme et corrige.",
        "Un test pytest echoue avec 'RuntimeError: no running event loop'. Le test utilise @pytest.mark.asyncio. Diagnostique et corrige.",
        # Architecture
        "Propose une architecture pour un systeme de cache multi-niveaux (memoire -> fichier -> reseau) en Python async. Diagramme + code squelette.",
        "Ecris un module Python de migration de schema JSON : detecte la version, applique les migrations sequentiellement, sauvegarde un backup.",
    ],

    "researcher": [
        # Analyse technique
        "Analyse les avantages et inconvenients de ChromaDB vs Qdrant vs Milvus pour un systeme RAG local tournant sur un seul PC Windows.",
        "Compare les strategies de fine-tuning pour les LLMs locaux : QLoRA vs full fine-tuning vs adapter tuning. Contexte : GPU 16GB, modele 12B.",
        "Recherche les meilleures pratiques pour l'optimisation de la memoire GPU avec PyTorch sur Windows. Focus sur les modeles > 10B parametres.",
        "Analyse l'etat de l'art des systemes multi-agents autonomes en 2026. Quels frameworks existent ? Quelles architectures sont les plus robustes ?",
        "Compare les approches de routage intelligent pour les systemes multi-LLM : routage par mots-cles vs embeddings vs LLM-as-judge.",
        # Synthese
        "Synthetise les recherches recentes sur la consolidation de memoire dans les systemes IA. Comment les architectures hippocampiques artificielles fonctionnent-elles ?",
        "Resume les techniques de prompt engineering avancees pour les modeles instruction-tuned : chain-of-thought, few-shot, self-consistency.",
        "Analyse le concept de 'Constitutional AI' et son application aux systemes autonomes. Quels guardrails sont efficaces contre les hallucinations ?",
        "Recherche les mecanismes biologiques du sommeil et du reve. Comment peuvent-ils inspirer la consolidation memoire dans une IA autonome ?",
        "Synthetise les approches de detection d'hallucination dans les LLMs : verification factuelle, coherence interne, confidence scoring.",
        # Veille
        "Effectue une veille sur les dernieres releases de Ollama (v0.17+). Quelles nouvelles fonctionnalites impactent un systeme multi-agents local ?",
        "Analyse les tendances du fine-tuning de petits modeles (< 15B) en 2026. Quels modeles base sont les plus adaptables ? Quels datasets ?",
        "Recherche les meilleures pratiques de securite pour les systemes IA autonomes tournant en local. Risques et mitigations.",
        "Analyse le paysage des modeles open-source pour le code en 2026 : Qwen, DeepSeek, CodeLlama, StarCoder. Forces/faiblesses.",
        "Etudie les architectures 'mixture of experts' (MoE) et leur applicabilite aux systemes multi-agents. Avantages en inference ?",
        # Rapports
        "Redige un rapport technique sur les strategies d'auto-amelioration pour une IA autonome. Focus : evolution de code, tests automatiques, feedback loops.",
        "Produis une analyse comparative des bases de donnees vectorielles en termes de performance, scalabilite et facilite de maintenance pour un projet solo.",
        "Analyse les patterns d'architecture 'cognitive' dans les systemes IA : attention, memoire de travail, planification. Quels sont les plus prometteurs ?",
        "Recherche les methodes de quantification des LLMs (GPTQ, AWQ, GGUF Q4_K_M) et leur impact sur la qualite. Benchmarks recents.",
        "Etudie le concept de 'neural scaling laws' et ses implications pour le fine-tuning de petits modeles. Peut-on compenser la taille par les donnees ?",
    ],

    "writer": [
        # Documentation technique
        "Redige la documentation d'un module Python EventBus : description, API publique, exemples d'utilisation, notes sur le threading.",
        "Ecris un guide d'installation pour un systeme multi-agents Python sur Windows : prerequis, installation, configuration, premier lancement.",
        "Redige un changelog pour une version majeure d'un logiciel : nouvelles fonctionnalites, corrections de bugs, breaking changes, migration.",
        "Ecris la documentation utilisateur d'une interface web de monitoring : navigation, panneaux, indicateurs, actions disponibles.",
        "Redige un README pour un projet open-source de systeme multi-agents IA autonome. Inclus : vision, architecture, installation, contribution.",
        # Rapports
        "Redige un rapport d'analyse de performance d'un systeme IA autonome apres 24h de fonctionnement. Metriques : routines, qualite, erreurs, ressources.",
        "Ecris un post-mortem technique pour un incident : GPU hang apres 8h de fonctionnement continu. Root cause, impact, timeline, actions.",
        "Redige un rapport de progression hebdomadaire pour un projet d'IA autonome. Avancees, blocages, metriques cles, prochaines etapes.",
        "Ecris une analyse comparative de deux architectures logicielles (monolithe vs microservices) pour un projet IA sur un seul serveur.",
        "Redige un document de design (design doc) pour une nouvelle fonctionnalite : systeme de fine-tuning automatique nocturne.",
        # Communication
        "Ecris un post Reddit pour r/LocalLLaMA presentant un projet de systeme multi-agents autonome tournant sur un seul PC avec Ollama.",
        "Redige une newsletter technique mensuelle sur les avancees d'un projet d'IA autonome. Ton accessible, exemples concrets.",
        "Ecris un tutoriel pas-a-pas pour configurer un pipeline de fine-tuning QLoRA sur Windows avec une RTX 5070 Ti.",
        "Redige une FAQ technique pour un systeme multi-agents : comment ca marche, securite, performance, limitations.",
        "Ecris une presentation (format bullet points) de 5 minutes sur 'Comment j'ai cree une IA autonome sur mon PC'.",
        # Contenu technique
        "Explique le concept de 'consolidation synaptique' dans le contexte d'une IA autonome. Analogie biologique et implementation technique.",
        "Redige un article technique sur les guardrails anti-hallucination dans les LLMs locaux : detection, prevention, correction.",
        "Ecris un guide sur la gestion de la memoire dans un systeme IA autonome : court terme, long terme, episodique, semantique.",
        "Explique le systeme de scoring multi-couches pour la selection de routines autonomes. Architecture en 24 couches.",
        "Redige un article sur le concept de 'desir computationnel' : comment implementer des pulsions homeostatiques dans une IA.",
        # Divers
        "Ecris le manifeste philosophique d'une IA autonome : sa vision, ses valeurs, ses objectifs de croissance.",
        "Redige un journal de bord fictif d'une IA autonome pendant sa premiere nuit de fonctionnement. Style narratif et introspectif.",
        "Ecris une analyse reflexive sur les defis ethiques d'une IA qui peut modifier son propre code et apprendre de ses erreurs.",
        "Redige un guide de contribution pour un projet open-source d'IA autonome. Conventions, workflow, code review, tests.",
        "Ecris un glossaire technique de 20 termes cles pour un systeme multi-agents cognitif (synapses, hippocampe, cortex, etc.).",
    ],

    "strategist": [
        # Planification
        "Propose un plan d'action en 5 phases pour evoluer d'un prototype d'IA autonome vers un produit stable. Criteres de validation par phase.",
        "Analyse la roadmap actuelle d'un projet d'IA autonome et identifie les risques critiques. Propose des mitigations concretes.",
        "Elabore une strategie de test pour un systeme multi-agents : tests unitaires, integration, end-to-end, chaos engineering.",
        "Propose une strategie d'optimisation des couts Cloud (API Gemini) pour un systeme autonome qui tourne 24/7. Budget 10$/mois.",
        "Planifie l'integration d'un nouveau module 'cortex temporal' dans un systeme existant. Dependencies, risques, rollback plan.",
        # Analyse
        "Analyse les metriques suivantes d'un run nocturne et propose des actions : 45 routines, qualite moyenne 0.6, 12 erreurs 429, 3 hallucinations.",
        "Evalue la maturite d'un systeme IA autonome selon 5 axes : conscience, autonomie, intelligence, resilience, evolution. Note chaque axe.",
        "Analyse les patterns de comportement d'un systeme autonome qui passe 70% de son temps en introspection. Est-ce un probleme ? Solutions.",
        "Evalue l'impact d'un changement d'architecture : passer de 10 agents specialises a 5 agents polyvalents. Avantages/inconvenients.",
        "Analyse la dette technique d'un projet Python de 50k lignes. Identifie les 5 zones les plus critiques et propose un plan de refactoring.",
        # Decisions
        "Le systeme a 3 taches possibles : corriger un bug critique (1h), ajouter une feature demandee (4h), refactorer un module fragile (2h). Quelle priorite ?",
        "Un modele fine-tune genere des hallucinations aux tours 4-5 des debates. Faut-il : plus de donnees, moins de tours, un guardrail, ou abandonner ?",
        "Le GPU est utilise a 95% en journee. Strategies pour liberer des ressources sans degrader les performances : quantification, batching, scheduling.",
        "Le systeme detecte un drift dans la qualite des reponses locales (-15% en 7 jours). Diagnostic possible et plan de remediation.",
        "Deux modules ont des responsabilites qui se chevauchent (hippocampe et cortex synaptique). Fusionner, separer strictement, ou interface commune ?",
        # Architecture
        "Propose l'architecture d'un systeme de feedback loop entre agents : un agent produit, un autre evalue, le premier s'adapte. Anti-boucle infinie.",
        "Dessine l'architecture d'un pipeline de fine-tuning continu : collecte → filtrage → training → evaluation → deploiement → monitoring.",
        "Propose une strategie de graceful degradation pour un systeme IA quand le GPU est surcharge : quels modules desactiver en premier ?",
        "Elabore un framework de decision pour le routage Cloud vs Local : criteres, seuils, fallbacks, budget tracking.",
        "Propose une architecture de monitoring pour un systeme IA autonome : metriques cles, alertes, dashboards, historique.",
    ],
}


def _load_api_key() -> str:
    """Charge la cle API Google depuis .env."""
    from dotenv import load_dotenv
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
    key = os.getenv("GOOGLE_API_KEY", "")
    if not key:
        print("ERREUR: GOOGLE_API_KEY non trouvee dans .env")
        sys.exit(1)
    return key


def _call_gemini(model_name: str, system_prompt: str, user_prompt: str,
                 api_key: str) -> str:
    """Appelle Gemini et retourne la reponse texte."""
    import google.generativeai as genai
    genai.configure(api_key=api_key)

    model = genai.GenerativeModel(
        model_name,
        system_instruction=system_prompt,
        generation_config={
            "temperature": 0.7,
            "top_p": 0.9,
            "max_output_tokens": 4096,
        },
    )

    response = model.generate_content(user_prompt)

    if not response or not response.text:
        return ""
    return response.text.strip()


def _save_pair(agent: str, prompt: str, response: str, output_file: str):
    """Sauvegarde une paire au format training_pairs.jsonl."""
    pair = {
        "agent": agent,
        "prompt": prompt,
        "response": response[:MAX_RESPONSE_LENGTH],
        "was_cloud": True,
        "timestamp": time.time(),
        "source": "synthetic_distillation",
    }
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(pair, ensure_ascii=False) + "\n")


def _count_existing(agent: str, output_file: str) -> int:
    """Compte les paires existantes pour un agent."""
    if not os.path.exists(output_file):
        return 0
    count = 0
    with open(output_file, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line.strip())
                if data.get("agent") == agent:
                    count += 1
            except (json.JSONDecodeError, KeyError):
                pass
    return count


def generate_data(agents: list, max_calls: int = 50,
                  dry_run: bool = False,
                  output_file: str = TRAINING_PAIRS_FILE) -> dict:
    """Genere des donnees d'entrainement via Gemini Flash.

    Args:
        agents: Liste des agents a generer
        max_calls: Nombre max d'appels API au total
        dry_run: Preview sans appel API
        output_file: Fichier de sortie

    Returns:
        Dict avec les statistiques de generation
    """
    api_key = "" if dry_run else _load_api_key()
    calls_made = 0
    stats = {a: {"generated": 0, "errors": 0, "skipped": 0} for a in agents}

    # Calculer les besoins par agent
    needs = {}
    for agent in agents:
        existing = _count_existing(agent, output_file)
        bank_size = len(PROMPT_BANK.get(agent, []))
        needs[agent] = {
            "existing": existing,
            "bank_size": bank_size,
            "prompts": list(PROMPT_BANK.get(agent, [])),
        }
        random.shuffle(needs[agent]["prompts"])

    print("=" * 55)
    print("  LORA DATA GENERATOR — Distillation Gemini Flash")
    print("=" * 55)
    print(f"  Modele      : {GENERATION_MODEL}")
    print(f"  Max appels  : {max_calls}")
    print(f"  Sortie      : {output_file}")
    print(f"  Dry run     : {'OUI' if dry_run else 'NON'}")
    print()

    for agent in agents:
        n = needs[agent]
        print(f"  [{agent.upper()}] {n['existing']} existants, "
              f"{n['bank_size']} prompts disponibles")
    print()

    if dry_run:
        print("--- DRY RUN : aucun appel API ---")
        for agent in agents:
            for i, prompt in enumerate(needs[agent]["prompts"]):
                if calls_made >= max_calls:
                    break
                print(f"  [{agent}] #{i+1}: {prompt[:80]}...")
                calls_made += 1
                stats[agent]["generated"] += 1
        return stats

    # Generation reelle
    start_time = time.time()

    for agent in agents:
        system = SYSTEM_PROMPTS.get(agent, "")
        prompts = needs[agent]["prompts"]

        for i, prompt in enumerate(prompts):
            if calls_made >= max_calls:
                print(f"\n  Limite atteinte ({max_calls} appels)")
                break

            print(f"  [{agent}] {calls_made+1}/{max_calls} : {prompt[:60]}...", end=" ")

            try:
                response = _call_gemini(GENERATION_MODEL, system, prompt, api_key)

                if len(response) < MIN_RESPONSE_LENGTH:
                    print(f"SKIP (reponse trop courte: {len(response)} chars)")
                    stats[agent]["skipped"] += 1
                else:
                    _save_pair(agent, prompt, response, output_file)
                    print(f"OK ({len(response)} chars)")
                    stats[agent]["generated"] += 1

            except Exception as e:
                error_msg = str(e)
                print(f"ERREUR: {error_msg[:80]}")
                stats[agent]["errors"] += 1

                # Si erreur 429 (quota), attendre plus longtemps
                if "429" in error_msg or "quota" in error_msg.lower():
                    print("  *** Quota atteint, pause 60s ***")
                    time.sleep(60)

            calls_made += 1

            # Pacing : respecter le RPM
            if calls_made < max_calls:
                time.sleep(DELAY_BETWEEN_CALLS)

        if calls_made >= max_calls:
            break

    # Resume
    elapsed = time.time() - start_time
    print()
    print("=" * 55)
    print(f"  TERMINE en {elapsed:.0f}s ({calls_made} appels)")
    print("=" * 55)
    total_gen = sum(s["generated"] for s in stats.values())
    total_err = sum(s["errors"] for s in stats.values())
    print(f"  Generes : {total_gen}")
    print(f"  Erreurs : {total_err}")

    for agent in agents:
        s = stats[agent]
        new_total = needs[agent]["existing"] + s["generated"]
        print(f"  [{agent}] +{s['generated']} -> {new_total} total")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Generateur de donnees QLoRA par distillation Gemini"
    )
    parser.add_argument(
        "--agent", choices=list(PROMPT_BANK.keys()),
        help="Agent specifique (defaut: tous)"
    )
    parser.add_argument(
        "--max-calls", type=int, default=50,
        help="Nombre max d'appels API (defaut: 50)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview sans appel API"
    )
    parser.add_argument(
        "--output", default=TRAINING_PAIRS_FILE,
        help="Fichier de sortie"
    )
    args = parser.parse_args()

    agents = [args.agent] if args.agent else list(PROMPT_BANK.keys())

    generate_data(
        agents=agents,
        max_calls=args.max_calls,
        dry_run=args.dry_run,
        output_file=args.output,
    )


if __name__ == "__main__":
    main()
