"""Pipeline de collecte et nettoyage de données pour QLoRA fine-tuning.

Extrait des paires (instruction, réponse) depuis toutes les sources de données
de Prométhée et produit des datasets JSONL par agent.

Sources :
  1. training_pairs.jsonl — Hook temps réel dans base_agent.py (la plus riche)
  2. ChromaDB — collective_wisdom + code_snippets (texte réel + métadonnées)
  3. chat_history.json — Conversations utilisateur (paires naturelles)
  4. hippocampus_state.json — Épisodes avec contexte affectif

Sortie : datasets/coder.jsonl, datasets/writer.jsonl, etc. (format ChatML)

Usage :
  python tools/lora_data_collector.py                    # Extraire tout
  python tools/lora_data_collector.py --source training  # Source spécifique
  python tools/lora_data_collector.py --agent coder      # Agent spécifique
  python tools/lora_data_collector.py --stats             # Statistiques seulement
  python tools/lora_data_collector.py --min-quality 0.5   # Filtre qualité
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_DIR = os.path.join(PROJECT_ROOT, "memory")
DATASETS_DIR = os.path.join(PROJECT_ROOT, "datasets")

# Fichiers sources
TRAINING_PAIRS_FILE = os.path.join(MEMORY_DIR, "training_pairs.jsonl")
CHAT_HISTORY_FILE = os.path.join(MEMORY_DIR, "chat_history.json")
HIPPOCAMPUS_FILE = os.path.join(MEMORY_DIR, "hippocampus_state.json")
AUTONOMY_STATE_FILE = os.path.join(MEMORY_DIR, "autonomy_state.json")

# Agents connus pour QLoRA
KNOWN_AGENTS = {
    "coder", "writer", "strategist", "researcher",
    "architect", "formatter", "security", "infra",
    "evolution", "factory",
}

# Agents regroupés pour LoRA (plusieurs agents → même LoRA)
LORA_GROUPS = {
    "coder": ["coder", "evolution", "factory"],         # Code Python
    "writer": ["writer", "formatter"],                   # Contenu/rédaction
    "strategist": ["strategist", "architect"],           # Analyse/planification
    "researcher": ["researcher", "security", "infra"],   # Synthèse/veille
}

# Système prompts par LoRA
SYSTEM_PROMPTS = {
    "coder": (
        "Tu es l'agent Coder de Prométhée, expert en génération de code Python. "
        "Tu écris du code propre, testé, respectant les conventions du projet. "
        "Tu ne proposes jamais de dépendances externes non approuvées."
    ),
    "writer": (
        "Tu es l'agent Writer de Prométhée, expert en rédaction et communication. "
        "Tu produis du contenu clair, structuré et adapté au contexte."
    ),
    "strategist": (
        "Tu es l'agent Strategist de Prométhée, expert en analyse et planification. "
        "Tu identifies les priorités, évalues les risques et proposes des plans d'action."
    ),
    "researcher": (
        "Tu es l'agent Researcher de Prométhée, expert en recherche et synthèse. "
        "Tu analyses les informations, identifies les tendances et produis des rapports."
    ),
}

# Filtres qualité
MIN_RESPONSE_LENGTH = 50
MAX_RESPONSE_LENGTH = 4000
MIN_PROMPT_LENGTH = 10

# Mots-clés de réponses à exclure (erreurs, hallucinations)
EXCLUDE_PATTERNS = [
    r"^Erreur",
    r"^⚠️",
    r"^Je ne peux pas",
    r"^I cannot",
    r"^Sorry",
    r"^\[Erreur\]",
    r"Traceback \(most recent call",
]
_EXCLUDE_RE = re.compile("|".join(EXCLUDE_PATTERNS), re.IGNORECASE)


def _is_valid_pair(prompt: str, response: str) -> bool:
    """Vérifie qu'une paire est valide pour l'entraînement."""
    if not prompt or not response:
        return False
    if len(prompt) < MIN_PROMPT_LENGTH:
        return False
    if len(response) < MIN_RESPONSE_LENGTH:
        return False
    if _EXCLUDE_RE.search(response[:100]):
        return False
    return True


def _format_bio_state(bio_state: Dict) -> str:
    """Formate l'etat biologique en texte court pour le system prompt."""
    if not bio_state:
        return ""
    parts = []
    if "dopamine" in bio_state:
        level = bio_state["dopamine"]
        label = "haute" if level > 0.65 else "basse" if level < 0.35 else "normale"
        parts.append(f"Dopamine: {label}")
    if "desire" in bio_state:
        parts.append(f"Pulsion: {bio_state['desire']}")
    if "psyche" in bio_state:
        traits = ", ".join(f"{k}={int(v)}" for k, v in bio_state["psyche"].items())
        parts.append(f"Traits: {traits}")
    if "threat" in bio_state:
        parts.append(f"Menace: {bio_state['threat']:.1f}")
    if not parts:
        return ""
    return " [ETAT INTERNE: " + ", ".join(parts) + "]"


def _to_chatml(system: str, instruction: str, response: str,
               metadata: Optional[Dict] = None) -> Dict:
    """Convertit en format ChatML pour QLoRA."""
    # Enrichir le system prompt avec l'etat biologique si disponible
    bio_state = (metadata or {}).get("bio_state", {})
    enriched_system = system + _format_bio_state(bio_state)

    entry = {
        "messages": [
            {"role": "system", "content": enriched_system},
            {"role": "user", "content": instruction[:MAX_RESPONSE_LENGTH]},
            {"role": "assistant", "content": response[:MAX_RESPONSE_LENGTH]},
        ],
    }
    if metadata:
        entry["metadata"] = metadata
    return entry


def _resolve_lora_group(agent_name: str) -> str:
    """Résout le nom LoRA pour un agent donné."""
    for lora_name, agents in LORA_GROUPS.items():
        if agent_name in agents:
            return lora_name
    return "strategist"  # Fallback


# ============================================================
# Source 1 : training_pairs.jsonl (Hook temps réel)
# ============================================================

def extract_training_pairs(min_quality: float = -1.0) -> List[Dict]:
    """Extrait les paires depuis le fichier JSONL temps réel."""
    results = []
    if not os.path.exists(TRAINING_PAIRS_FILE):
        return results

    with open(TRAINING_PAIRS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                pair = json.loads(line)
            except json.JSONDecodeError:
                continue

            prompt = pair.get("prompt", "")
            response = pair.get("response", "")
            agent = pair.get("agent", "unknown")

            if not _is_valid_pair(prompt, response):
                continue

            # Nettoyer le prompt (retirer le préfixe système)
            clean_prompt = _strip_system_prefix(prompt)
            if not clean_prompt:
                continue

            lora = _resolve_lora_group(agent)
            results.append(_to_chatml(
                SYSTEM_PROMPTS.get(lora, ""),
                clean_prompt,
                response,
                metadata={
                    "source": "training_pairs",
                    "agent": agent,
                    "lora": lora,
                    "was_cloud": pair.get("was_cloud", False),
                    "timestamp": pair.get("timestamp", 0),
                    "bio_state": pair.get("bio_state", {}),
                },
            ))

    return results


def _strip_system_prefix(prompt: str) -> str:
    """Retire le préfixe système injecté par generate_content()."""
    # Retirer [SYSTEM: ...], [CONTRAINTE: ...], [SOUVENIRS]:..., [INTUITIONS]:...
    cleaned = re.sub(
        r"\[SYSTEM:.*?\]\n?", "", prompt, flags=re.DOTALL
    )
    cleaned = re.sub(
        r"\[CONTRAINTE:.*?\]\n?", "", cleaned, flags=re.DOTALL
    )
    cleaned = re.sub(
        r"\[SOUVENIRS\]:.*?(?=\n[^\s]|\Z)", "", cleaned, flags=re.DOTALL
    )
    cleaned = re.sub(
        r"\[INTUITIONS SYNAPTIQUES\]:.*?(?=\n[^\s]|\Z)", "", cleaned, flags=re.DOTALL
    )
    return cleaned.strip()


# ============================================================
# Source 2 : ChromaDB (collective_wisdom + code_snippets)
# ============================================================

def extract_chromadb() -> List[Dict]:
    """Extrait les paires depuis ChromaDB."""
    results = []
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from core.vector_store import ChromaMemoryManager
        manager = ChromaMemoryManager.get_instance()

        for collection_name in ["collective_wisdom", "code_snippets"]:
            try:
                col = manager._get_collection(collection_name)
                if col is None:
                    continue
                data = col.get(include=["documents", "metadatas"])
                docs = data.get("documents", [])
                metas = data.get("metadatas", [])

                for doc, meta in zip(docs, metas):
                    if not doc or len(doc) < MIN_RESPONSE_LENGTH:
                        continue

                    # ChromaDB stocke souvent "Q: ...\nA: ..."
                    prompt, response = _split_qa(doc)
                    if not _is_valid_pair(prompt, response):
                        # Si pas de format Q/A, traiter comme réponse seule
                        if len(doc) >= MIN_RESPONSE_LENGTH:
                            agent = (meta or {}).get("agent", "unknown")
                            lora = _resolve_lora_group(agent)
                            results.append(_to_chatml(
                                SYSTEM_PROMPTS.get(lora, ""),
                                f"[Contexte ChromaDB: {collection_name}]",
                                doc[:MAX_RESPONSE_LENGTH],
                                metadata={
                                    "source": f"chromadb_{collection_name}",
                                    "agent": agent,
                                    "lora": lora,
                                },
                            ))
                        continue

                    agent = (meta or {}).get("agent", "unknown")
                    lora = _resolve_lora_group(agent)
                    results.append(_to_chatml(
                        SYSTEM_PROMPTS.get(lora, ""),
                        _strip_system_prefix(prompt),
                        response,
                        metadata={
                            "source": f"chromadb_{collection_name}",
                            "agent": agent,
                            "lora": lora,
                        },
                    ))

            except Exception as e:
                print(f"  [WARN] Collection {collection_name}: {e}")

    except Exception as e:
        print(f"  [WARN] ChromaDB indisponible (normal si runtime actif): {e}")

    return results


def _split_qa(text: str) -> Tuple[str, str]:
    """Sépare un texte 'Q: ...\nA: ...' en (question, réponse)."""
    # Format attendu : "Q: ...\nA: ..."
    match = re.match(r"Q:\s*(.*?)\nA:\s*(.*)", text, re.DOTALL)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", text


# ============================================================
# Source 3 : Chat history
# ============================================================

def extract_chat_history() -> List[Dict]:
    """Extrait les paires (user, assistant) depuis l'historique de chat."""
    results = []
    if not os.path.exists(CHAT_HISTORY_FILE):
        return results

    try:
        with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return results

    messages = data.get("messages", [])

    for i in range(len(messages) - 1):
        if messages[i].get("role") == "user" and messages[i + 1].get("role") == "assistant":
            user_msg = messages[i].get("content", "")
            assistant_msg = messages[i + 1].get("content", "")

            if not _is_valid_pair(user_msg, assistant_msg):
                continue

            # Les réponses chat sont souvent du strategist
            results.append(_to_chatml(
                SYSTEM_PROMPTS["strategist"],
                user_msg,
                assistant_msg[:MAX_RESPONSE_LENGTH],
                metadata={
                    "source": "chat_history",
                    "agent": "strategist",
                    "lora": "strategist",
                    "timestamp": messages[i].get("timestamp", 0),
                },
            ))

    return results


# ============================================================
# Source 4 : Hippocampus (contexte affectif enrichi)
# ============================================================

def extract_hippocampus() -> List[Dict]:
    """Extrait les arcs narratifs de l'hippocampe comme exemples enrichis."""
    results = []
    if not os.path.exists(HIPPOCAMPUS_FILE):
        return results

    try:
        with open(HIPPOCAMPUS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return results

    # Arcs narratifs : instruction = résumé des épisodes, réponse = narrative
    arcs = data.get("arcs", [])
    for arc in arcs:
        narrative = arc.get("narrative", "")
        title = arc.get("title", "")
        arc_type = arc.get("arc_type", "")
        emotional_arc = arc.get("emotional_arc", "")

        if not narrative or len(narrative) < MIN_RESPONSE_LENGTH:
            continue

        instruction = f"Analyse l'arc narratif '{title}' (type: {arc_type})"
        if emotional_arc:
            instruction += f". Trajectoire émotionnelle: {emotional_arc}"

        results.append(_to_chatml(
            SYSTEM_PROMPTS["strategist"],
            instruction,
            narrative[:MAX_RESPONSE_LENGTH],
            metadata={
                "source": "hippocampus",
                "agent": "strategist",
                "lora": "strategist",
                "arc_type": arc_type,
            },
        ))

    return results


# ============================================================
# Pipeline principal
# ============================================================

def collect_all(sources: Optional[List[str]] = None,
                agent_filter: Optional[str] = None,
                min_quality: float = -1.0) -> Dict[str, List[Dict]]:
    """Collecte et organise les données par LoRA group.

    Returns:
        Dict[lora_name, List[chatml_entry]]
    """
    all_entries = []

    extractors = {
        "training": ("training_pairs.jsonl", extract_training_pairs),
        "chromadb": ("ChromaDB", extract_chromadb),
        "chat": ("chat_history.json", extract_chat_history),
        "hippocampus": ("hippocampus_state.json", extract_hippocampus),
    }

    for source_key, (label, extractor) in extractors.items():
        if sources and source_key not in sources:
            continue
        print(f"  Extraction: {label}...")
        try:
            if source_key == "training":
                entries = extractor(min_quality)
            else:
                entries = extractor()
            print(f"    → {len(entries)} paires extraites")
            all_entries.extend(entries)
        except Exception as e:
            print(f"    → Erreur: {e}")

    # Organiser par LoRA group
    by_lora = defaultdict(list)
    for entry in all_entries:
        lora = entry.get("metadata", {}).get("lora", "strategist")
        if agent_filter and lora != agent_filter:
            continue
        by_lora[lora].append(entry)

    return dict(by_lora)


def save_datasets(by_lora: Dict[str, List[Dict]], output_dir: str = DATASETS_DIR):
    """Sauvegarde les datasets JSONL par LoRA group."""
    os.makedirs(output_dir, exist_ok=True)

    for lora_name, entries in by_lora.items():
        filepath = os.path.join(output_dir, f"{lora_name}.jsonl")
        with open(filepath, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"  {lora_name}.jsonl : {len(entries)} exemples ({os.path.getsize(filepath) / 1024:.1f} KB)")


def print_stats(by_lora: Dict[str, List[Dict]]):
    """Affiche les statistiques des données collectées."""
    total = sum(len(v) for v in by_lora.values())
    print(f"\n{'='*50}")
    print(f"STATISTIQUES DONNÉES QLORÀ")
    print(f"{'='*50}")
    print(f"Total : {total} exemples")
    print()

    for lora_name in sorted(by_lora.keys()):
        entries = by_lora[lora_name]
        print(f"  {lora_name.upper()} ({len(entries)} exemples)")

        # Par source
        sources = Counter(e.get("metadata", {}).get("source", "?") for e in entries)
        for src, count in sources.most_common():
            print(f"    {src}: {count}")

        # Par agent
        agents = Counter(e.get("metadata", {}).get("agent", "?") for e in entries)
        for agent, count in agents.most_common():
            print(f"    agent={agent}: {count}")

        # Longueur moyenne réponse
        if entries:
            avg_len = sum(
                len(e["messages"][2]["content"]) for e in entries
            ) / len(entries)
            print(f"    réponse moyenne: {avg_len:.0f} chars")

        # Cloud vs local
        cloud = sum(1 for e in entries if e.get("metadata", {}).get("was_cloud"))
        if cloud:
            print(f"    cloud: {cloud} ({cloud*100/len(entries):.0f}%)")
        print()


def main():
    parser = argparse.ArgumentParser(description="Collecteur de données QLoRA pour Prométhée")
    parser.add_argument("--source", choices=["training", "chromadb", "chat", "hippocampus"],
                       action="append", help="Source spécifique (répétable, défaut: toutes)")
    parser.add_argument("--agent", choices=list(LORA_GROUPS.keys()),
                       help="Filtre par LoRA group")
    parser.add_argument("--stats", action="store_true",
                       help="Afficher les statistiques seulement")
    parser.add_argument("--min-quality", type=float, default=-1.0,
                       help="Qualité minimum (0.0-1.0, défaut: pas de filtre)")
    parser.add_argument("--output", default=DATASETS_DIR,
                       help=f"Dossier de sortie (défaut: {DATASETS_DIR})")
    parser.add_argument("--memory-dir",
                       help="Dossier memory/ alternatif (ex: runtime sur C:\\MesProjets\\...)")
    args = parser.parse_args()

    # Override memory dir si spécifié
    if args.memory_dir:
        global TRAINING_PAIRS_FILE, CHAT_HISTORY_FILE, HIPPOCAMPUS_FILE
        mem = args.memory_dir
        TRAINING_PAIRS_FILE = os.path.join(mem, "training_pairs.jsonl")
        CHAT_HISTORY_FILE = os.path.join(mem, "chat_history.json")
        HIPPOCAMPUS_FILE = os.path.join(mem, "hippocampus_state.json")

    print("=" * 50)
    print("COLLECTEUR DONNÉES QLORÀ — Prométhée")
    print("=" * 50)

    sources = args.source if args.source else None
    by_lora = collect_all(sources, args.agent, args.min_quality)

    print_stats(by_lora)

    if not args.stats:
        save_datasets(by_lora, args.output)
        print(f"\nDatasets sauvegardés dans {args.output}/")


if __name__ == "__main__":
    main()
