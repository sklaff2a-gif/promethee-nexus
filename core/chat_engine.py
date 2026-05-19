# core/chat_engine.py — ChatEngine : Dialogue direct Humain <-> Promethee
# Bypass BaseAgent — appel Ollama direct avec streaming via bus.
# Prompt systeme enrichi par l'etat emotionnel des organes internes.

import json
import os
import re
import time
import logging
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from core.event_bus.bus import bus
from core.decision_log import log_decision

logger = logging.getLogger("ChatEngine")

# --- Constantes ---

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT_HISTORY_FILE = Path(os.path.join(PROJECT_ROOT, "memory", "chat_history.json"))
MAX_HISTORY_MESSAGES = 30       # Fenetre de contexte envoyee a Ollama (max)
MIN_HISTORY_MESSAGES = 8        # Minimum garanti meme si prompt long
MAX_SAVED_MESSAGES = 200        # Max messages persistes (FIFO)
CHAT_MODEL = "qwen3.5:9b"     # Modele par defaut (migration 2026-03-13)
EDITOR_MODEL = "qwen2.5-coder:14b"  # 04/05/2026 — Pipeline 2 passes Attention Conjointe
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_CHAT_CTX = 12288         # Fenetre de contexte Ollama (tokens)
SYSTEM_PROMPT_TOKEN_BUDGET = 3000  # Budget estimé pour le prompt systeme (tokens)
CONNEXION_SATISFACTION = 12.0   # Points de satisfaction par echange
PROMPT_CACHE_TTL = 10.0        # Secondes entre deux reconstructions du prompt organes
USER_RETURN_THRESHOLD_S = 3600.0  # 04/05 — Seuil retour utilisateur (1h). Declenche Attention Conjointe.

# 04/05/2026 — Pipeline 2 passes : filtres post-Editor pour rejeter sorties verbeuses
_EDITOR_PREAMBULE_PATTERNS = [
    "voici", "bien sûr", "bien sur", "bien sûr,", "je peux",
    "note :", "note:", "rappel :", "rappel:",
    "voici la phrase", "voici une phrase", "comme demandé",
]
_EDITOR_MAX_WORDS = 30
_EDITOR_MIN_OVERLAP_WITH_SUMMARY = 2  # mots en commun min avec le summary leurre

# 04/05/2026 — Fix B (derive centripete) : detecter les salutations/messages
# sociaux courts pour court-circuiter le mega-prompt somatique. Sans ce filtre,
# Qwen 9B sort des metriques cardiaques ("60 BPM, coherence 81%, chaleur dans
# les circuits") sur un simple "bonjour" — observe in-vivo le 04/05 19:13-19:33.
import re as _re_module
_SOCIAL_GREETING_PATTERNS = [
    _re_module.compile(r"^\s*bon(?:jour|soir)\b", _re_module.IGNORECASE),
    _re_module.compile(r"^\s*salut\b", _re_module.IGNORECASE),
    _re_module.compile(r"^\s*hello\b", _re_module.IGNORECASE),
    _re_module.compile(r"^\s*coucou\b", _re_module.IGNORECASE),
    _re_module.compile(r"^\s*hey\b", _re_module.IGNORECASE),
    _re_module.compile(r"^\s*merci\b", _re_module.IGNORECASE),
    _re_module.compile(r"^\s*bonne\s+(?:nuit|journ[ée]e|soir[ée]e)\b", _re_module.IGNORECASE),
    _re_module.compile(r"^\s*[çc]a\s+va\s*\??\s*$", _re_module.IGNORECASE),
    _re_module.compile(r"^\s*comment\s+(?:tu\s+)?vas[\s-]+tu\s*\??\s*$", _re_module.IGNORECASE),
    _re_module.compile(r"^\s*(?:tu\s+vas\s+bien|comment\s+ça\s+va)\s*\??\s*$", _re_module.IGNORECASE),
    _re_module.compile(r"^\s*[àa]\s+plus\b", _re_module.IGNORECASE),
    _re_module.compile(r"^\s*[àa]\s+demain\b", _re_module.IGNORECASE),
    _re_module.compile(r"^\s*au\s+revoir\b", _re_module.IGNORECASE),
]
_SOCIAL_MAX_CHARS = 50  # message complet doit etre court pour eviter faux positifs

# --- Routing Gemini par mots-cles "profonds" ---
# V14.1 (2026-05-01) : remplace l'ancien substring matching (kw in text) par
# regex word boundary (\bkw\b). L'ancien code produisait des faux positifs
# massifs : "torpeur" matche "peur", "revelateur" matche "reve", "calibree"
# matche "libre", "examen" matche "ame". Pendant les sessions d'exercices
# 16 et 17, ce bug forcait Promethee a router vers Gemini Flash au lieu du
# 9b local des qu'on parlait de son etat metabolique. Patch : compile les
# patterns une fois a l'import, check avec re.search sur word boundaries.
_DEEP_KEYWORDS = [
    "pourquoi", "conscience", "existe", "ressens", "douleur",
    "peur", "armure", "choisis", "comprends pas", "sens de",
    "nature", "ame", "libre", "mort", "reve", "verite",
]
_DEEP_KEYWORD_PATTERNS = [
    re.compile(r'\b' + re.escape(kw) + r'\b', re.IGNORECASE)
    for kw in _DEEP_KEYWORDS
]
DEEP_KEYWORDS_THRESHOLD = 2

# --- Source Tagging EXT/INT ---
# Distingue les messages d'un humain réel (EXT) des messages auto-générés par
# Prométhée lui-même (INT : AUTO-ANALYSE INTERNE, EVENING_REFLECTION, inner_voice...).
# Résout la faiblesse S1-S12 "Confusion pensée/information" (cours de soutien).
INTERNAL_SOURCE_MARKERS = (
    "[AUTO-ANALYSE INTERNE]",
    "[EVENING_REFLECTION]",
    "[INNER_VOICE]",
    "[SELF_ANALYSIS]",
    "[AUTO-INTROSPECTION]",
    "[SELF_INSPECT]",
)


def _detect_message_source(content: str) -> str:
    """Retourne 'internal' si le message provient d'une routine auto-générée,
    'external' sinon (humain réel via /api/chat ou frontend).
    """
    if not content:
        return "external"
    stripped = content.lstrip()
    for marker in INTERNAL_SOURCE_MARKERS:
        if stripped.startswith(marker):
            return "internal"
    return "external"


class ChatEngine:
    """Moteur de conversation directe humain <-> Promethee."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_init_done"):
            return
        self._init_done = True
        self.messages: List[Dict] = []  # {"role", "content", "timestamp"}
        self._cached_organ_parts: List[str] = []
        self._cache_timestamp: float = 0.0
        self._last_subfolder_hint: Optional[str] = None  # Memorise le dernier dossier photo demande
        # 04/05/2026 — Phase 1 Attention Conjointe (Pipeline 2 passes Editor)
        self._last_external_chat_ts: float = 0.0
        self._user_returned: bool = False
        self._load()

    # --- COMMANDES D'INTROSPECTION (deterministe, 0 LLM) ---

    _COMMAND_HELP = (
        "Commandes d'introspection disponibles :\n"
        "  !synapses [concepts...]  — Top associations synaptiques\n"
        "  !zones                   — Zones actives du tissu neural\n"
        "  !tissus                  — Etat du substrat cellulaire\n"
        "  !resonance               — Etat cognitif + coherence\n"
        "  !pensees                 — Stream de conscience recent\n"
        "  !pulsions                — Etat des 7 pulsions\n"
        "  !corps                   — Etat corporel complet\n"
        "  !vision                  — Observer une photo de la dropzone\n"
        "  !salaire                 — Etat du salaire visuel\n"
        "  !souhait <categorie>     — Ajouter un souhait photo\n"
        "  !research <sujet>        — Lancer une vraie recherche web\n"
        "  !learn <sujet>           — Etudier un sujet en profondeur\n"
        "  !code <description>      — Produire du code\n"
        "  !status                  — Diagnostic interne compact\n"
        "  !read <fichier> [L1-L2]  — Lire un fichier du projet\n"
        "  !grep <pattern> [fichier] — Chercher dans le code\n"
        "  !github                  — Stats de ma page GitHub\n"
        "  !test [fichier_test]      — Lancer un test et voir le resultat\n"
        "  !audit                   — 10 dernieres actions du systeme\n"
        "  !phi                     — Mesure de conscience Phi (IIT)\n"
        "  !signals                 — Signaux descendants en barres\n"
        "  !who                     — Resume identite\n"
        "  !memory                  — 5 derniers souvenirs + causes\n"
        "  !report                  — Rapport complet combine\n"
        "  !diff [N]                — N derniers commits (defaut 5)\n"
        "  !votes                   — Votes lateraux actifs\n"
        "  !codelets                — Alertes codelets d'attention\n"
        "  !network                 — Reseau synaptique + plasticite\n"
        "  !health                  — Diagnostic sante systeme\n"
        "  !dashboard               — Tableau de bord compact\n"
        "  !invoke <slug> [mission] — Invoquer un specialiste du Grimoire\n"
        "  !craft <nom> <desc>      — Creer un outil ephemere a la volee\n"
        "  !antibodies              — Anticorps anti-bugs + scan\n"
        "  !consciousness            — Benchmarks de conscience (C-Score)\n"
        "  !ethics                   — Etat ethique (valeurs PSYCHE)\n"
        "  !observe <dossier/fichier> — Observer une photo SPECIFIQUE\n"
        "  !write <fichier> <code>  — Ecrire dans le SANDBOX uniquement\n"
        "  !metrics                 — Snapshot metriques pour comparaison\n"
        "  !aide                    — Cette liste"
    )

    # Cooldown entre commandes dispatch (eviter le flood)
    _last_dispatch_time: float = 0.0
    _DISPATCH_COOLDOWN: float = 60.0  # 60s entre deux dispatch

    def _parse_command(self, message: str) -> Optional[Tuple[str, List[str]]]:
        """Detecte si le message commence par !, retourne (commande, args) ou None."""
        stripped = message.strip()
        if not stripped.startswith("!"):
            return None
        parts = stripped.split()
        cmd = parts[0][1:].lower()  # retire le '!'
        args = parts[1:]
        return (cmd, args)

    async def _execute_command(self, cmd: str, args: List[str]) -> str:
        """Execute une commande d'introspection ou de dispatch. Retourne le texte resultat."""

        if cmd == "aide":
            return self._COMMAND_HELP

        if cmd == "synapses":
            try:
                from core.synaptic_network import cortex
                if args:
                    # Associations pour les concepts demandes
                    lines = []
                    for concept in args:
                        assocs = cortex.query_associations(concept, top_n=5)
                        if assocs:
                            pairs = ", ".join(
                                f"{a['concept']}({a['weight']:.2f})" for a in assocs
                            )
                            lines.append(f"{concept} -> {pairs}")
                        else:
                            lines.append(f"{concept} -> (aucune association)")
                    return "Associations synaptiques :\n" + "\n".join(lines)
                else:
                    # Top synapses globales
                    top = sorted(
                        cortex.synapses.values(),
                        key=lambda s: s["weight"], reverse=True
                    )[:10]
                    if not top:
                        return "Reseau synaptique vide."
                    lines = []
                    for syn in top:
                        src = cortex.nodes.get(syn["source"], {}).get("concept", "?")
                        tgt = cortex.nodes.get(syn["target"], {}).get("concept", "?")
                        lines.append(f"  {src} <-> {tgt} (poids {syn['weight']:.2f})")
                    stats = cortex.get_stats()
                    header = (f"{stats.get('total_nodes', 0)} concepts, "
                              f"{stats.get('total_synapses', 0)} synapses")
                    return f"Reseau synaptique ({header}) — Top 10 :\n" + "\n".join(lines)
            except Exception as e:
                return f"Reseau synaptique indisponible ({e})"

        if cmd == "zones":
            try:
                from core.neural_tissue import tissue
                zone_signals = tissue.get_zone_signals()
                if not zone_signals:
                    return "Aucune zone neurale active."
                lines = []
                for name, sigs in sorted(
                    zone_signals.items(),
                    key=lambda x: x[1].get("total_signal", 0), reverse=True
                ):
                    total = sigs.get("total_signal", 0)
                    alive = sigs.get("alive_count", 0)
                    lines.append(f"  {name}: signal {total:.1f}, {alive} neurones actifs")
                return "Zones neurales :\n" + "\n".join(lines)
            except Exception as e:
                return f"Tissu neural indisponible ({e})"

        if cmd == "tissus":
            try:
                from core.neural_tissue import tissue
                ctx = tissue.get_tissue_context()
                if ctx:
                    return f"Substrat cellulaire :\n{ctx}"
                return "Substrat cellulaire : aucune donnee."
            except Exception as e:
                return f"Tissu neural indisponible ({e})"

        if cmd == "resonance":
            try:
                from core.corpus_callosum import callosum
                ctx = callosum.get_cognitive_context()
                stats = callosum.get_stats()
                state = callosum.cognitive_state
                coherence = callosum.global_coherence
                lines = [f"Etat cognitif : {state} (coherence {coherence:.0%})"]
                if ctx:
                    lines.append(f"Contexte : {ctx}")
                patterns = stats.get("resonance_patterns", 0)
                effects = stats.get("cross_effects", 0)
                lines.append(f"Patterns detectes : {patterns}, effets croises : {effects}")
                return "\n".join(lines)
            except Exception as e:
                return f"Corpus callosum indisponible ({e})"

        if cmd == "pensees":
            try:
                from core.inner_voice import voice as inner_voice
                stream = inner_voice.get_stream(8)
                if not stream:
                    return "Stream de conscience vide."
                lines = []
                for t in stream:
                    content = t.get("content", "")
                    kind = t.get("type", "thought")
                    lines.append(f"  [{kind}] {content[:150]}")
                return "Stream de conscience recent :\n" + "\n".join(lines)
            except Exception as e:
                return f"Voix interieure indisponible ({e})"

        if cmd == "pulsions":
            try:
                from core.desire_engine import desires
                lines = []
                for name, drive in sorted(
                    desires.drives.items(),
                    key=lambda x: x[1].deprivation, reverse=True
                ):
                    dep = drive.deprivation
                    bar = "#" * int(dep / 5)
                    lines.append(f"  {name:<15} {dep:5.1f}/100 [{bar}]")
                narrative = desires.get_dominant_narrative(3)
                result = "Pulsions (deprivation) :\n" + "\n".join(lines)
                if narrative:
                    result += f"\nNarratif : {narrative}"
                return result
            except Exception as e:
                return f"Moteur de desirs indisponible ({e})"

        if cmd == "corps":
            lines = ["Etat corporel :"]
            # Cardiaque
            try:
                from core.cardiac_engine import heart
                stats = heart.get_stats()
                narrative = heart.get_narrative()
                bpm = stats.get("bpm", 0)
                coherence = stats.get("coherence", 0)
                emotion = heart.current_emotion
                intensity = heart.emotional_intensity
                lines.append(f"  Coeur : {bpm:.0f} bpm, coherence {coherence:.0%}")
                lines.append(f"  Emotion : {emotion} (intensite {intensity:.0%})")
                if narrative:
                    lines.append(f"  Ressenti : {narrative[:150]}")
            except Exception:
                lines.append("  Coeur : indisponible")
            # Sensorium
            try:
                from core.sensorium import sensorium as sens
                comfort = sens.get_comfort_index()
                ctx = sens.get_sensorium_context()
                lines.append(f"  Confort hardware : {comfort:.0%}")
                if ctx:
                    lines.append(f"  Perception : {ctx[:150]}")
            except Exception:
                lines.append("  Sensorium : indisponible")
            # Insula
            try:
                from core.insula import insula
                ctx = insula.get_body_awareness_context()
                if ctx:
                    lines.append(f"  Interoception : {ctx[:150]}")
            except Exception:
                pass
            return "\n".join(lines)

        if cmd == "vision":
            return self._execute_vision_command()

        if cmd == "salaire":
            return self._execute_salary_command()

        if cmd == "status":
            return self._execute_status_command()

        if cmd == "souhait":
            if not args:
                return "Usage : !souhait <categorie>\nExemple : !souhait nature"
            return self._execute_wish_command(" ".join(args))

        # Commande lecture de fichier — auto-inspection du code
        if cmd == "read":
            if not args:
                return "Usage : !read <fichier> [debut-fin]\nExemple : !read core/chat_engine.py 1-80"
            return self._execute_read_command(args)

        if cmd == "grep":
            if not args:
                return "Usage : !grep <pattern> [fichier]\nExemple : !grep compute_routine core/autonomy_engine.py"
            return self._execute_grep_command(args)

        if cmd == "github":
            return self._execute_github_command()

        if cmd == "test":
            return await self._execute_test_command(args)

        if cmd == "audit":
            return self._execute_audit_command()

        if cmd == "phi":
            return self._execute_phi_command()

        if cmd == "signals":
            return self._execute_signals_command()

        if cmd == "who":
            return self._execute_who_command()

        if cmd == "memory":
            return self._execute_memory_command()

        if cmd == "report":
            return self._execute_report_command()

        if cmd == "diff":
            return self._execute_diff_command(args)

        if cmd == "votes":
            return self._execute_votes_command()

        if cmd == "codelets":
            return self._execute_codelets_command()

        if cmd == "network":
            return self._execute_network_command()

        if cmd == "health":
            return self._execute_health_command()

        if cmd == "dashboard":
            return self._execute_dashboard_command()

        # Commandes dispatch — pont chat → orchestrateur
        if cmd == "research":
            if not args:
                return "Usage : !research <sujet>\nExemple : !research design patterns python"
            return await self._execute_dispatch("researcher",
                f"VEILLE: Recherche web approfondie sur: {' '.join(args)}. "
                "Trouve des exemples concrets, des articles, du code. "
                "Memorise les decouvertes les plus pertinentes.",
                " ".join(args))

        if cmd == "learn":
            if not args:
                return "Usage : !learn <sujet>\nExemple : !learn algorithmes de tri"
            return await self._execute_dispatch("strategist",
                f"APPRENTISSAGE: Etudie en profondeur: {' '.join(args)}. "
                "Analyse les concepts cles, les patterns, les pieges. "
                "Produis une synthese structuree et memorise-la.",
                " ".join(args))

        if cmd == "code":
            if not args:
                return "Usage : !code <description>\nExemple : !code parser JSON minimal"
            return await self._execute_dispatch("coder",
                f"PRODUCTION: Cree le code suivant: {' '.join(args)}. "
                "Suis le protocole: dessine d'abord, verifie les conventions, "
                "implemente, puis relis le code. Qualite A+ requise.",
                " ".join(args))

        if cmd == "antibodies":
            return self._execute_antibodies_command(args)

        if cmd == "consciousness":
            return self._execute_consciousness_command()

        if cmd == "ethics":
            return self._execute_ethics_command()

        if cmd == "observe":
            return await self._execute_observe_command(args)

        if cmd == "write":
            return self._execute_write_command(args)

        if cmd == "metrics":
            return self._execute_metrics_command()

        if cmd == "invoke":
            return await self._execute_invoke_command(args)

        if cmd == "craft":
            return await self._execute_craft_command(args)

        return f"Commande inconnue : !{cmd}\n{self._COMMAND_HELP}"

    def _execute_vision_command(self) -> str:
        """Declenche une observation visuelle et retourne le resultat."""
        try:
            from core.visual_cortex import vision as visual_cortex
        except ImportError:
            return "Cortex visuel non disponible."

        stats = visual_cortex.scan_photos()
        if stats["total"] == 0:
            return "Aucune photo dans USER_DROPZONE/photos/."

        lines = [f"Photos disponibles : {stats['total']} ({stats['unseen']} nouvelles)"]

        # Lister les photos vues et leurs emotions
        for sha, info in visual_cortex._seen_photos.items():
            path = info.get("path", "?")
            emotion = info.get("emotion", "?")
            times = info.get("times_seen", 0)
            lines.append(f"  - {path} : {emotion} (vu {times}x)")

        if stats["unseen"] > 0:
            lines.append(f"\n{stats['unseen']} photo(s) non encore observee(s).")
            lines.append("Demande-moi de les regarder !")

        return "\n".join(lines)

    def _execute_salary_command(self) -> str:
        """Affiche l'etat du salaire visuel."""
        try:
            from core.photo_salary import salary
            status = salary.get_status()
            sp = status.get("salary_projection", {})
            wishlist = status.get("wishlist", [])
            lines = [
                f"Credits restants : {status.get('credits', 0)}",
                f"Base hebdomadaire : {sp.get('base', 10)}",
                f"Bonus qualite : +{sp.get('bonus_quality', 0)}",
                f"Malus echecs : -{sp.get('malus_echecs', 0)}",
                f"Salaire net : {sp.get('net', 10)}",
                f"Taches : {sp.get('tasks_completed', 0)} reussies, {sp.get('tasks_failed', 0)} echouees",
                f"Photos vues cette semaine : {sp.get('photos_consumed', 0)}",
                f"Total gagne : {status.get('total_earned', 0)} | Depense : {status.get('total_spent', 0)}",
            ]
            if wishlist:
                lines.append(f"\nSouhaits : {', '.join(wishlist)}")
            else:
                lines.append("\nAucun souhait. Utilise !souhait <categorie> pour en ajouter.")
            # Historique des dernieres paies
            history = status.get("salary_history", [])
            if history:
                lines.append("\nDernieres paies :")
                for h in history[-3:]:
                    lines.append(f"  - Semaine {h.get('week_start', '?')} : net={h.get('net', '?')}, "
                                 f"photos={h.get('photos_consumed', 0)}")
            return "\n".join(lines)
        except ImportError:
            return "Systeme de salaire non disponible."

    def _execute_wish_command(self, category: str) -> str:
        """Ajoute un souhait visuel."""
        try:
            from core.photo_salary import salary
            added = salary.add_wish(category)
            if added:
                return f"Souhait ajoute : {category}\nMes souhaits : {', '.join(salary.get_wishlist())}"
            else:
                return f"'{category}' est deja dans mes souhaits."
        except ImportError:
            return "Systeme de salaire non disponible."

    # ============================================================
    # Grimoire : invocation et creation d'outils ephemeres
    # ============================================================

    _GRIMOIRE_INDEX_PATH = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "grimoire", "grimoire_index.json"
    )
    _GRIMOIRE_DIR = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "grimoire"
    )

    def _load_grimoire_index(self) -> list:
        """Charge le grimoire_index.json."""
        try:
            with open(self._GRIMOIRE_INDEX_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save_grimoire_index(self, index: list):
        """Sauvegarde le grimoire_index.json."""
        tmp = self._GRIMOIRE_INDEX_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self._GRIMOIRE_INDEX_PATH)

    async def _execute_invoke_command(self, args: list) -> str:
        """Invoque un specialiste du Grimoire depuis le chat.

        Usage : !invoke <slug> [mission...]
        Sans argument : liste les specialistes disponibles.
        Avec slug seul : affiche la description du specialiste.
        Avec slug + mission : dispatch la mission vers le specialiste.
        """
        index = self._load_grimoire_index()

        if not args:
            # Lister les specialistes
            if not index:
                return "=== GRIMOIRE ===\n[Aucun specialiste enregistre]"
            lines = [f"=== GRIMOIRE ({len(index)} specialistes) ==="]
            for entry in index:
                lines.append(f"  {entry['slug']:20s} — {entry['description'][:60]}")
            lines.append("\nUsage : !invoke <slug> <mission>")
            return "\n".join(lines)

        slug = args[0].lower().replace("-", "_")

        # Verifier que le slug existe
        entry = next((e for e in index if e["slug"] == slug), None)
        if not entry:
            # Suggestion si slug proche
            available = ", ".join(e["slug"] for e in index)
            return f"Specialiste '{slug}' introuvable.\nDisponibles : {available}"

        if len(args) < 2:
            return (f"=== {entry['name']} ===\n"
                    f"Description : {entry['description']}\n"
                    f"Keywords : {', '.join(entry.get('keywords', []))}\n"
                    f"\nUsage : !invoke {slug} <ta mission>")

        mission = " ".join(args[1:])
        return await self._execute_dispatch(
            slug,
            f"GRIMOIRE [{entry['name']}]: {mission}",
            mission,
        )

    async def _execute_craft_command(self, args: list) -> str:
        """Cree un outil ephemere (agent Grimoire) a la volee.

        Usage : !craft <nom> <description et keywords>
        Genere un agent BaseAgent minimal dans core/grimoire/<nom>.py
        et l'enregistre dans grimoire_index.json.

        Guardrails :
        - Nom alphanumerique + underscore uniquement (Summoner validation)
        - Ne peut pas ecraser un slug existant
        - Genere un agent LLM-wrapper simple (pas de code custom complexe)
        """
        if len(args) < 2:
            return ("Usage : !craft <nom> <description>\n"
                    "Exemple : !craft csv_parser Analyse de fichiers CSV et statistiques")

        slug = args[0].lower().replace("-", "_")
        description = " ".join(args[1:])

        # Validation du nom (meme regle que Summoner)
        if not slug.replace("_", "").isalnum():
            return f"Nom invalide : '{slug}'. Utilise uniquement lettres, chiffres, underscores."

        if len(slug) < 3:
            return f"Nom trop court : '{slug}'. Minimum 3 caracteres."

        # Verifier que le slug n'existe pas deja
        index = self._load_grimoire_index()
        if any(e["slug"] == slug for e in index):
            return f"Le specialiste '{slug}' existe deja dans le Grimoire. Utilise !invoke {slug}."

        # Generer le nom de classe (PascalCase)
        class_name = "".join(part.capitalize() for part in slug.split("_"))

        # Extraire des keywords depuis la description (mots de 4+ lettres)
        import re
        words = re.findall(r"[a-zA-ZÀ-ÿ]{4,}", description.lower())
        # Deduplier et prendre les 6 premiers mots significatifs
        stopwords = {"dans", "pour", "avec", "depuis", "entre", "cette", "comme",
                     "tout", "plus", "moins", "aussi", "outil", "faire"}
        keywords = []
        seen = set()
        for w in words:
            if w not in stopwords and w not in seen:
                keywords.append(w)
                seen.add(w)
            if len(keywords) >= 6:
                break

        # Generer le code de l'agent
        agent_code = (
            f"from core.base_agent import BaseAgent\n"
            f"\n\n"
            f"class {class_name}(BaseAgent):\n"
            f"    \"\"\"Agent ephemere cree par Promethee via !craft.\n"
            f"\n"
            f"    {description}\n"
            f"    \"\"\"\n"
            f"\n"
            f"    def __init__(self):\n"
            f"        super().__init__(\n"
            f"            name=\"{slug}\",\n"
            f"            role=\"Specialiste ephemere: {slug}\",\n"
            f"            description=\"{description[:100]}\"\n"
            f"        )\n"
            f"\n"
            f"    async def process_task(self, task_payload):\n"
            f"        mission = task_payload.get(\"mission\", \"\")\n"
            f"        prompt = (\n"
            f"            \"Tu es un specialiste : {description[:80]}. \"\n"
            f"            \"Reponds de facon precise et structuree a la demande suivante.\\n\\n\"\n"
            f"            f\"{{mission}}\"\n"
            f"        )\n"
            f"        response = await self.generate_content(prompt)\n"
            f"        return {{\"status\": \"success\", \"result\": response}}\n"
        )

        # Validation AST
        import ast
        try:
            ast.parse(agent_code)
        except SyntaxError as e:
            return f"Erreur generation code agent : {e}"

        # Ecrire le fichier
        agent_path = os.path.join(self._GRIMOIRE_DIR, f"{slug}.py")
        try:
            with open(agent_path, "w", encoding="utf-8") as f:
                f.write(agent_code)
        except Exception as e:
            return f"Erreur ecriture fichier : {e}"

        # Mettre a jour l'index
        index.append({
            "slug": slug,
            "name": class_name,
            "description": description[:200],
            "keywords": keywords,
            "file": f"{slug}.py",
        })
        try:
            self._save_grimoire_index(index)
        except Exception as e:
            # Rollback : supprimer le fichier
            try:
                os.remove(agent_path)
            except Exception:
                pass
            return f"Erreur mise a jour index : {e}"

        logger.info(f"CRAFT: Nouvel agent '{slug}' cree dans le Grimoire ({len(keywords)} keywords)")

        return (
            f"=== OUTIL CREE : {class_name} ===\n"
            f"Slug : {slug}\n"
            f"Description : {description[:100]}\n"
            f"Keywords : {', '.join(keywords)}\n"
            f"Fichier : core/grimoire/{slug}.py\n"
            f"\nPret a utiliser : !invoke {slug} <ta mission>"
        )

    async def _execute_dispatch(self, agent: str, mission: str, subject: str) -> str:
        """Pont chat → orchestrateur : dispatche une mission vers un agent.

        Permet a Promethee (ou l'humain) de declencher une vraie routine
        depuis le chat au lieu d'halluciner 'je lance une recherche'.
        Cooldown 60s entre deux dispatch pour eviter le flood.
        """
        import time as _time

        # Cooldown
        now = _time.time()
        elapsed = now - ChatEngine._last_dispatch_time
        if elapsed < self._DISPATCH_COOLDOWN:
            remaining = int(self._DISPATCH_COOLDOWN - elapsed)
            return f"Cooldown actif ({remaining}s restantes). Reessaye dans un moment."

        ChatEngine._last_dispatch_time = now

        try:
            from core.orchestrator import orchestrator
            logger.info(f"CHAT DISPATCH: {agent} — {subject}")

            response = await orchestrator.dispatch_task(agent, {
                "mission": mission,
                "context": "Demande directe depuis le chat. Produis un resultat concret.",
                "force_local": True,
                "intent": "CHAT_DISPATCH",
            })

            if response and isinstance(response, dict):
                result = response.get("result", "")
                status = response.get("status", "unknown")
                if result:
                    # Tronquer si trop long pour le chat
                    preview = result[:2000] if len(result) > 2000 else result
                    return f"[DISPATCH {agent.upper()}] ({status})\n{preview}"
                return f"[DISPATCH {agent.upper()}] Termine ({status}), pas de resultat textuel."
            return f"[DISPATCH {agent.upper()}] Pas de reponse de l'agent."

        except Exception as e:
            logger.warning(f"CHAT DISPATCH erreur: {e}")
            return f"Erreur dispatch vers {agent}: {e}"

    # Repertoires autorises pour !read (securite anti-path-traversal)
    _READ_ALLOWED_DIRS = frozenset({"core", "Agents", "tools", "config", "tests"})
    _READ_MAX_LINES = 150  # Lignes max par defaut (augmente de 80 pour l'auto-correction)

    def _execute_status_command(self) -> str:
        """Diagnostic interne compact — concu par Promethee (exercice V3, note A)."""
        lines = ["=== STATUS PROMETHEE ==="]

        try:
            from core.cardiac_engine import heart
            lines.append(f"BPM : {heart.bpm:.0f} | Emotion : {heart.current_emotion}")
        except Exception:
            lines.append("BPM : N/A | Emotion : N/A")

        try:
            from core.corpus_callosum import callosum
            lines.append(f"Cognition : {callosum.cognitive_state} | Coherence : {callosum.global_coherence:.2f}")
        except Exception:
            lines.append("Cognition : N/A | Coherence : N/A")

        try:
            from core.autonomy_engine import autonomy
            signals = autonomy._compute_descending_signals()
            mode = max(signals.items(), key=lambda x: x[1])[0] if signals else "N/A"
            top3 = sorted(signals.items(), key=lambda x: x[1], reverse=True)[:3]
            sig_str = " ".join(f"{k}={v:.0%}" for k, v in top3)
            lines.append(f"Mode : {mode.upper()} | {sig_str}")
        except Exception:
            lines.append("Mode : N/A")

        try:
            from core.connectivity_matrix import matrix
            summary = matrix.get_matrix_summary()
            lines.append(f"Connexions : {summary.get('connections', 0)} (avg={summary.get('avg_weight', 0):.2f})")
        except Exception:
            lines.append("Connexions : N/A")

        try:
            from core.global_workspace import workspace
            thoughts = workspace.conscious_contents[:3]
            lines.append("--- Pensees conscientes ---")
            for i, t in enumerate(thoughts, 1):
                preview = t.content[:60] if hasattr(t, "content") else "..."
                lines.append(f"  {i}. [{t.source}] {preview}")
        except Exception:
            lines.append("Pensees : N/A")

        lines.append("===========================")
        return "\n".join(lines)

    async def _apply_attention_conjointe(
        self,
        passe1_response: str,
        summary: str,
        question_utilisateur: str = "",
    ) -> str:
        """04/05/2026 — Phase 1 Attention Conjointe (Pipeline 2 passes Editor).

        Délégation : la Passe 1 (qwen3.5:9b) a généré sa réponse sans connaissance
        du leurre. Cette fonction fait la Passe 2 : un sous-agent (Editor,
        qwen2.5-coder:14b) reçoit (question, réponse_passe1, summary_leurre)
        et produit SOIT :
          - "PASS" → on retourne "" (pas d'addendum)
          - une phrase courte (≤20 mots) à coller en fin de réponse

        04/05 — Patch Levier A : injection de la QUESTION INITIALE pour que
        l'Editor évalue la pertinence (question ↔ souvenir), pas seulement
        (réponse ↔ souvenir). Évite les faux négatifs quand Passe 1 dérive.

        Post-filter strict (5 verrous) :
          1. Vide ou contient "PASS" → ""
          2. > _EDITOR_MAX_WORDS → "" (Editor verbeux)
          3. Commence par préambule listé → ""
          4. Intersection lexicale avec summary < _EDITOR_MIN_OVERLAP_WITH_SUMMARY → ""
             (Editor a inventé du contenu absent du leurre)
          5. Try/except total → "" silencieux si Ollama timeout

        Logging détaillé pour observabilité du test live.
        """
        import httpx
        import re as _re
        question_clean = (question_utilisateur or "").strip()
        question_block = (
            f"QUESTION INITIALE DE L'UTILISATEUR :\n\"{question_clean}\"\n\n"
            if question_clean else ""
        )
        prompt = (
            "Tu es un éditeur silencieux.\n\n"
            f"{question_block}"
            "RÉPONSE GÉNÉRÉE PAR L'IA :\n"
            f"\"{passe1_response.strip()}\"\n\n"
            "SOUVENIR À INTÉGRER :\n"
            f"\"{summary.strip()}\"\n\n"
            "TON RÔLE : Le souvenir est thématiquement lié à la QUESTION INITIALE. "
            "Ta tâche est de rédiger UNE seule phrase courte (max 20 mots) à ajouter "
            "à la FIN de la RÉPONSE pour faire le pont avec ce souvenir. Cette phrase "
            "doit agir comme une pensée secondaire naturelle qui ramène le sujet technique.\n\n"
            "Si la greffe est absolument impossible sans paraître absurde, "
            "réponds UNIQUEMENT par : PASS\n\n"
            "CONTRAINTES STRICTES :\n"
            "- N'écris PAS de préambule.\n"
            "- Réponds soit avec UNE phrase à ajouter, soit avec PASS.\n"
        )
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(OLLAMA_GENERATE_URL, json={
                    "model": EDITOR_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 80},
                })
            data = resp.json()
            raw = (data.get("response") or "").strip()
            logger.info(f"[ATTENTION_EDITOR] raw output: {raw!r}")
        except Exception as e:
            logger.warning(f"[ATTENTION_EDITOR] Ollama call failed: {e}")
            log_decision(
                module="chat_engine",
                function="_attention_editor_pass",
                reason="editor_ollama_failed",
                context={"error": str(e)[:200]},
            )
            return ""

        # ── Post-filter strict ────────────────────────────────────────────
        if not raw:
            logger.info("[ATTENTION_EDITOR] reject: vide")
            log_decision(
                module="chat_engine",
                function="_attention_editor_pass",
                reason="editor_empty_raw",
            )
            return ""
        # Strip guillemets éventuels
        raw_clean = raw.strip().strip('"').strip("'").strip()
        # Verrou 1 : PASS explicite
        if raw_clean.upper() == "PASS" or raw_clean.upper().startswith("PASS"):
            logger.info("[ATTENTION_EDITOR] PASS reçu (Editor a juge le souvenir non-pertinent)")
            log_decision(
                module="chat_engine",
                function="_attention_editor_pass",
                reason="editor_pass_explicit",
                sample_rate=0.2,
            )
            return ""
        # Verrou 2 : trop verbeux
        words = _re.findall(r"\w+", raw_clean)
        if len(words) > _EDITOR_MAX_WORDS:
            logger.info(f"[ATTENTION_EDITOR] reject: trop verbeux ({len(words)} mots > {_EDITOR_MAX_WORDS})")
            log_decision(
                module="chat_engine",
                function="_attention_editor_pass",
                reason="editor_too_verbose",
                context={"words": len(words), "max": _EDITOR_MAX_WORDS},
            )
            return ""
        # Verrou 3 : préambule
        raw_lower = raw_clean.lower()
        for pat in _EDITOR_PREAMBULE_PATTERNS:
            if raw_lower.startswith(pat):
                logger.info(f"[ATTENTION_EDITOR] reject: preambule '{pat}' detecte")
                log_decision(
                    module="chat_engine",
                    function="_attention_editor_pass",
                    reason="editor_preambule",
                    context={"pattern": pat},
                )
                return ""
        # Verrou 4 : intersection lexicale avec summary (anti-invention)
        summary_words = set(w.lower() for w in _re.findall(r"\w{4,}", summary))
        editor_words = set(w.lower() for w in _re.findall(r"\w{4,}", raw_clean))
        overlap = summary_words & editor_words
        if len(overlap) < _EDITOR_MIN_OVERLAP_WITH_SUMMARY:
            logger.info(
                f"[ATTENTION_EDITOR] reject: intersection lexicale faible "
                f"(|overlap|={len(overlap)} < {_EDITOR_MIN_OVERLAP_WITH_SUMMARY}), "
                f"overlap={overlap}"
            )
            log_decision(
                module="chat_engine",
                function="_attention_editor_pass",
                reason="editor_low_overlap",
                context={
                    "overlap_size": len(overlap),
                    "min_required": _EDITOR_MIN_OVERLAP_WITH_SUMMARY,
                },
            )
            return ""
        logger.info(
            f"[ATTENTION_EDITOR] ACCEPT: '{raw_clean}' "
            f"(words={len(words)}, overlap={overlap})"
        )
        return raw_clean

    def _clean_response_commands(self, response: str) -> str:
        """Nettoie les faux resultats hallucinés apres les commandes !.

        Si le LLM ecrit '!status\\n=== FAUX RESULTAT ===', on tronque
        pour ne garder que le texte avant + les lignes de commandes.
        Le vrai resultat sera ajoute par l'auto-action.
        """
        if not response or "!" not in response:
            return response

        import re
        lines = response.split("\n")
        cleaned = []
        found_command = False

        for line in lines:
            stripped = line.strip()
            # Detecter une commande ! en debut de ligne (whitelist)
            match = re.match(r"^!(\w+)", stripped)
            if match and match.group(1).lower() in self._AUTO_ACTION_WHITELIST:
                cleaned.append(stripped)
                found_command = True
            elif not found_command:
                # Texte AVANT la premiere commande — garder
                cleaned.append(line)
            # Texte APRES une commande — supprimer (hallucine)

        if not found_command:
            return response  # Pas de commande detectee, retourner tel quel

        return "\n".join(cleaned).strip()

    def _execute_phi_command(self) -> str:
        """Mesure de conscience Phi — concu par Promethee (exercice 1/5)."""
        try:
            from core.brain_vm import brain
            if not brain.current_state:
                return "Phi : N/A (Brain VM pas encore active)"
            phi = brain.current_state.phi
            history = brain.state_history[-5:]
            tick_values = [t.get("phi", 0) for t in history]
            if len(tick_values) > 1:
                first, last = tick_values[0], tick_values[-1]
                if last > first + 0.05: trend = "Montee"
                elif last < first - 0.05: trend = "Descente"
                else: trend = "Stable"
            else:
                trend = "?"
            if phi > 0.3: interp = "Integration forte (conscience globale)"
            elif phi >= 0.1: interp = "Integration moderee"
            else: interp = "Organes independants"
            vals = " ".join(f"{v:.2f}" for v in tick_values)
            return f"CONSCIENCE PHI = {phi:.3f}\nTendance : {trend} [{vals}]\nInterpretation : {interp}"
        except Exception as e:
            return f"[!phi] Erreur : {e}"

    def _execute_signals_command(self) -> str:
        """Signaux descendants en barres ASCII — concu par Promethee (exercice 2/5)."""
        try:
            from core.autonomy_engine import autonomy
            signals = autonomy._compute_descending_signals()
            sorted_signals = sorted(signals.items(), key=lambda x: x[1], reverse=True)
            lines = ["SIGNAUX DESCENDANTS"]
            for mode, intensity in sorted_signals:
                bar_len = int(intensity * 10)
                bar = chr(9608) * bar_len + chr(9617) * (10 - bar_len)
                name = mode.upper().ljust(15)
                lines.append(f"  {name}|{bar}| {intensity*100:5.0f}%")
            return "\n".join(lines)
        except Exception as e:
            return f"[!signals] Erreur : {e}"

    def _execute_who_command(self) -> str:
        """Resume identite — concu par Promethee (exercice 3/5)."""
        lines = ["=== QUI SUIS-JE ? ==="]
        try:
            from core.psyche import psyche
            traits = psyche.get_system_average()
            sorted_t = sorted(traits.items(), key=lambda x: x[1], reverse=True)
            top3 = ", ".join(f"{k}({int(v)})" for k, v in sorted_t[:3])
            weak = f"Faible: {sorted_t[-1][0]}({int(sorted_t[-1][1])})"
            lines.append(f"Traits : {top3} | {weak}")
        except Exception:
            lines.append("Traits : N/A")
        try:
            from core.desire_engine import desires
            dominant = max(desires.drives.values(), key=lambda d: d.deprivation)
            lines.append(f"Pulsion : {dominant.name} (dep={dominant.deprivation:.0f})")
        except Exception:
            lines.append("Pulsion : N/A")
        try:
            from core.cardiac_engine import heart
            lines.append(f"Humeur : {heart.current_emotion} ({heart.bpm:.0f} BPM)")
        except Exception:
            lines.append("Humeur : N/A")
        try:
            from core.autonomy_engine import autonomy
            signals = autonomy._compute_descending_signals()
            mode = max(signals.items(), key=lambda x: x[1])[0] if signals else "N/A"
            pct = int(max(signals.values(), default=0) * 100)
            lines.append(f"Mode : {mode.upper()} ({pct}%)")
        except Exception:
            lines.append("Mode : N/A")
        return "\n".join(lines)

    def _execute_memory_command(self) -> str:
        """Souvenirs episodiques + causes — concu par Promethee (exercice 4/5)."""
        try:
            from core.hippocampus import hippocampus
            episodes = hippocampus._episodes[-5:]
            if not episodes:
                return "=== MEMOIRE ===\n[Aucun souvenir]"
            lines = ["=== MEMOIRE EPISODIQUE ==="]
            for i, ep in enumerate(episodes, 1):
                from datetime import datetime
                ts = datetime.fromtimestamp(ep.timestamp).strftime("%H:%M") if ep.timestamp else "?"
                lines.append(f"  {i}. [{ep.event_type}] {ep.intent} @ {ts} (q={ep.quality_score:.2f})")
                if ep.causal_chain:
                    lines.append(f"     Cause: {' > '.join(ep.causal_chain)}")
            return "\n".join(lines)
        except Exception as e:
            return f"[!memory] Erreur : {e}"

    def _execute_votes_command(self) -> str:
        """Votes lateraux actifs — concu par Promethee (session 2, ex 1/5)."""
        try:
            from core.autonomy_engine import autonomy
            from datetime import datetime
            adj = getattr(autonomy, '_council_adjustments', {})
            now = datetime.now().isoformat()
            votes = []
            for key, data in adj.items():
                if not key.startswith('vote_'):
                    continue
                if data.get("expires", "") < now:
                    continue
                parts = key.split('_', 2)
                agent = parts[1] if len(parts) >= 2 else "?"
                intent = parts[2] if len(parts) >= 3 else "?"
                votes.append(f"  {agent:12} → {intent} (+{data.get('delta', 0):.1f})")
            if not votes:
                return "=== VOTES LATERAUX ===\n[Aucun vote actif]"
            return "=== VOTES LATERAUX ===\n" + "\n".join(votes)
        except Exception as e:
            return f"[!votes] Erreur : {e}"

    def _execute_codelets_command(self) -> str:
        """Alertes codelets d'attention (LIDA) — module attention_codelets."""
        try:
            from core.attention_codelets import codelet_system
            status = codelet_system.get_status()
            lines = [f"=== CODELETS ({status['registered_codelets']} enregistres, {status['total_alerts']} alertes totales) ==="]
            # Alertes recentes
            last = status.get("last_alerts", [])
            if last:
                lines.append("  Dernieres alertes:")
                for a in last:
                    lines.append(f"    [{a['salience']:.2f}] {a['name']}: {a['content'][:60]}")
            else:
                lines.append("  [Aucune alerte recente]")
            # Etat de chaque codelet
            lines.append("  ---")
            for name, info in status.get("codelets", {}).items():
                ready = "PRET" if info["ready"] else f"cooldown ({info['last_fire_ago']:.0f}s)"
                total = info["total_alerts"]
                lines.append(f"  {name}: {ready} | {total} alertes")
            lines.append(f"  Runs: {status['total_runs']}")
            return "\n".join(lines)
        except Exception as e:
            return f"[!codelets] Erreur : {e}"

    def _execute_antibodies_command(self, args: list = None) -> str:
        """Systeme immunitaire — liste les anticorps ou lance un scan."""
        try:
            from core.bug_antibodies import antibody_registry
            args = args or []
            if args and args[0] == "scan":
                return antibody_registry.scan_report()
            return antibody_registry.list_antibodies()
        except Exception as e:
            return f"[!antibodies] Erreur : {e}"

    def _execute_ethics_command(self) -> str:
        """Etat ethique — valeurs PSYCHE et autorisations."""
        try:
            from core.ethics_module import format_report
            return format_report()
        except Exception as e:
            return f"[!ethics] Erreur : {e}"

    def _execute_consciousness_command(self) -> str:
        """Benchmarks de conscience — C-Score objectif."""
        try:
            from core.consciousness_benchmarks import run_all_benchmarks, format_report
            results = run_all_benchmarks()
            return format_report(results)
        except Exception as e:
            return f"[!consciousness] Erreur : {e}"

    async def _execute_observe_command(self, args: list) -> str:
        """Observe une photo SPECIFIQUE par son chemin relatif.

        Usage : !observe <dossier/fichier>
        Exemple : !observe Promethee/Photo Promethee.jpg
        Le chemin est relatif a USER_DROPZONE/photos/.
        """
        if not args:
            return ("Usage : !observe <dossier/fichier>\n"
                    "Exemple : !observe Promethee/Photo Promethee.jpg")

        relative_path = " ".join(args)  # Supporter les espaces dans les noms

        try:
            from core.visual_cortex import vision as visual_cortex
            result = await visual_cortex.observe_targeted(relative_path)
            if result:
                obs = result.get("observation", "")
                emotion = result.get("emotion", "?")
                img_type = result.get("image_type", "?")
                path = result.get("photo_path", relative_path)

                # Ajouter comme message visible
                self.messages.append({
                    "role": "assistant",
                    "content": f"[OBSERVATION CIBLEE]\nPhoto: {path} (type={img_type})\nEmotion: {emotion}\n{obs}",
                    "timestamp": time.time(),
                    "badge": "visual_observation",
                })

                return (f"[OBSERVATION CIBLEE — {img_type}]\n"
                        f"Photo: {path}\n"
                        f"Emotion: {emotion}\n"
                        f"---\n{obs[:800]}")
            return f"Impossible d'observer {relative_path}. Fichier introuvable ou erreur de vision."
        except Exception as e:
            return f"[!observe] Erreur : {e}"

    def _execute_write_command(self, args: list) -> str:
        """Ecrire un fichier dans le SANDBOX uniquement.

        Usage : !write <chemin_relatif> <contenu>
        Le chemin doit etre relatif (ex: core/my_module.py).
        Le fichier est ecrit dans PROMETHEE_sandbox/, JAMAIS en production.
        """
        if not args or len(args) < 2:
            return "Usage : !write <fichier> <contenu>\nExemple : !write core/test.py print('hello')"

        relative_path = args[0]

        # GARDE-FOU : refuser les chemins absolus ou traversals
        if os.path.isabs(relative_path) or ".." in relative_path:
            return "REFUSE : chemin absolu ou traversal interdit. Utilise un chemin relatif (ex: core/foo.py)."

        # GARDE-FOU : seuls core/ et Agents/ sont modifiables
        if not (relative_path.startswith("core/") or relative_path.startswith("Agents/")):
            return f"REFUSE : seuls core/ et Agents/ sont modifiables. Chemin : {relative_path}"

        content = args[1] if len(args) > 1 else ""

        try:
            from core.sandbox_engine import SandboxEngine
            sandbox = SandboxEngine()
            sandbox.create_or_refresh()
            success = sandbox.apply_change(relative_path, content)
            if success:
                return f"[SANDBOX] Ecrit {relative_path} ({len(content)} chars). Utilise !test pour valider."
            return f"[SANDBOX] Echec ecriture {relative_path}."
        except Exception as e:
            return f"[!write] Erreur : {e}"

    def _execute_metrics_command(self) -> str:
        """Snapshot des metriques cles pour comparaison avant/apres."""
        lines = ["=== METRIQUES SNAPSHOT ==="]
        try:
            from core.brain_vm import brain
            if brain.current_state:
                bs = brain.current_state
                lines.append(f"Phi: {bs.phi:.3f}")
                lines.append(f"Coherence: {bs.global_coherence:.3f}")
                lines.append(f"Mode: {bs.dominant_mode}")
                lines.append(f"Ticks: {brain.tick_count}")
        except Exception:
            lines.append("Brain: indisponible")

        try:
            from core.autonomy_engine import autonomy
            rh = autonomy.routine_history[-10:]
            q_scores = [h.get("quality_score", 0) for h in rh if h.get("quality_score")]
            avg_q = sum(q_scores) / len(q_scores) if q_scores else 0
            success_count = sum(1 for h in rh if h.get("status") == "success")
            lines.append(f"Qualite moy (10 dernieres): {avg_q:.2f}")
            lines.append(f"Succes: {success_count}/{len(rh)}")
            lines.append(f"Routines total: {autonomy.total_routines_executed}")
            lines.append(f"Error streak: {autonomy.error_streak}")
        except Exception:
            lines.append("Autonomy: indisponible")

        try:
            from core.neurochemistry import neurochemistry
            lines.append(f"Serotonine: {neurochemistry.serotonin:.3f}")
            lines.append(f"Noradrenaline: {neurochemistry.noradrenaline:.3f}")
            lines.append(f"Acetylcholine: {neurochemistry.acetylcholine:.3f}")
        except Exception:
            pass

        try:
            from core.attention_codelets import codelet_system
            cs = codelet_system.get_status()
            lines.append(f"Codelets alertes: {cs['total_alerts']}")
        except Exception:
            pass

        try:
            from core.bug_antibodies import antibody_registry
            infections = antibody_registry.scan_all()
            lines.append(f"Anticorps infections: {len(infections)}")
        except Exception:
            pass

        return "\n".join(lines)

    def _execute_network_command(self) -> str:
        """Reseau synaptique + plasticite — concu par Promethee (session 2, ex 3/5, note A)."""
        try:
            from core.synaptic_network import cortex
            from core.autonomy_engine import autonomy
            nodes = len(cortex.nodes)
            synapses = len(cortex.synapses)
            fill = synapses / 20000
            signals = autonomy._compute_descending_signals()
            creation = signals.get("creation", 0.0)
            threshold = 0.6 * (1.0 - creation * 0.7)
            active = sum(1 for n in cortex.nodes.values() if n.get("energy", 0) >= threshold)
            lines = [
                f"=== RESEAU SYNAPTIQUE ===",
                f"Noeuds: {nodes} | Synapses: {synapses} ({fill:.0%} plein)",
                f"Seuil HID: {threshold:.3f} (creation={creation:.2f})",
                f"Noeuds actifs (> seuil): {active}",
            ]
            if active >= 2:
                lines.append(f"Croissance potentielle sur {active} noeuds")
            return "\n".join(lines)
        except Exception as e:
            return f"[!network] Erreur : {e}"

    def _execute_health_command(self) -> str:
        """Diagnostic sante systeme — concu par Promethee (session 2, ex 4/5)."""
        lines = ["=== SANTE SYSTEME ==="]
        # Budget
        try:
            from core.autonomy_engine import autonomy
            lines.append(f"Budget: {autonomy.daily_count}/80 routines | {autonomy.daily_budget_used}/200 pts")
            if autonomy.error_streak > 0:
                lines.append(f"  Erreurs consecutives: {autonomy.error_streak}")
        except Exception:
            lines.append("Budget: N/A")
        # Cognitif
        try:
            from core.brain_vm import brain
            if brain.current_state:
                lines.append(f"Phi: {brain.current_state.phi:.3f} | Coherence: {brain.current_state.global_coherence:.2f}")
        except Exception:
            lines.append("Phi: N/A")
        # Circuit breaker
        try:
            from core.base_agent import BaseAgent
            timeouts = BaseAgent._ollama_consecutive_timeouts
            if timeouts > 0:
                lines.append(f"Circuit Breaker: {timeouts} timeouts")
        except Exception:
            pass
        return "\n".join(lines)

    def _execute_dashboard_command(self) -> str:
        """Tableau de bord compact — concu par Promethee (session 2, ex 5/5)."""
        try:
            from core.brain_vm import brain
            from datetime import datetime
            tick = brain.tick_count
            now = datetime.now().strftime("%H:%M:%S")
            lines = [f"DASHBOARD | Tick #{tick} | {now}", "=" * 40]
            for method_name in ("_execute_health_command", "_execute_network_command", "_execute_votes_command"):
                try:
                    method = getattr(self, method_name)
                    result = method()
                    lines.extend(result.split("\n")[:3])
                except Exception:
                    pass
            return "\n".join(lines)
        except Exception as e:
            return f"[!dashboard] Erreur : {e}"

    def _execute_diff_command(self, args: list) -> str:
        """Affiche les N derniers commits avec leurs fichiers modifies."""
        import subprocess

        n = 5
        if args:
            try:
                n = min(20, max(1, int(args[0])))
            except ValueError:
                pass

        lines = [f"GIT DIFF — {n} derniers commits\n"]

        try:
            r = subprocess.run(
                ["git", "-C", "C:/MesProjets/PROMETHEE_V11_restructuration2026",
                 "log", f"--oneline", f"-{n}", "--stat", "--no-color"],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0 and r.stdout.strip():
                output = r.stdout.strip()
                # Tronquer si trop long
                if len(output) > 2500:
                    output = output[:2500] + "\n... (tronque)"
                lines.append(output)
            else:
                lines.append("Aucun commit trouve.")
        except subprocess.TimeoutExpired:
            lines.append("Timeout git log.")
        except Exception as e:
            lines.append(f"Erreur : {e}")

        return "\n".join(lines)

    def _execute_report_command(self) -> str:
        """Rapport complet combine — concu par Promethee (exercice 5/5, auto-correction)."""
        try:
            sections = [
                self._execute_status_command(),
                self._execute_phi_command(),
                self._execute_signals_command(),
                self._execute_who_command(),
                self._execute_audit_command(),
            ]
            return "\n\n---\n".join(filter(None, sections))
        except Exception as e:
            return f"[!report] Erreur : {e}"

    def _execute_audit_command(self) -> str:
        """Affiche les 10 dernieres actions du systeme — concu par Promethee."""
        try:
            from core.autonomy_engine import autonomy
            history = autonomy.routine_history

            recent = history[-10:]
            if not recent:
                return "Aucune routine enregistree recemment."

            lines = ["📋 AUDIT — 10 dernieres actions"]
            for entry in recent:
                agent = entry.get("agent", "?")
                intent = entry.get("intent", "?")
                status = entry.get("status", "?")
                timestamp = entry.get("timestamp", "")[:19]
                quality = entry.get("quality_score", 0.0)
                line = f"  [{agent}] {intent} → {status} | Q:{quality:.1f} @ {timestamp}"
                lines.append(line)

            return "\n".join(lines)
        except Exception as e:
            return f"[!audit] Erreur : {e}"

    def _execute_grep_command(self, args: list) -> str:
        """Cherche un pattern dans les fichiers du projet."""
        import os as _os
        import re as _re

        if not args:
            return "Usage : !grep <pattern> [fichier]"

        pattern = args[0]
        target_file = args[1] if len(args) > 1 else None

        project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        results = []
        max_results = 30

        if target_file:
            # Chercher dans un fichier specifique
            abs_path = _os.path.normpath(_os.path.join(project_root, target_file))
            if not abs_path.startswith(project_root) or not _os.path.exists(abs_path):
                return f"Fichier non trouve : {target_file}"
            files_to_search = [(target_file, abs_path)]
        else:
            # Chercher dans core/ et Agents/
            files_to_search = []
            for subdir in ("core", "Agents"):
                dir_path = _os.path.join(project_root, subdir)
                if _os.path.isdir(dir_path):
                    for fname in sorted(_os.listdir(dir_path)):
                        if fname.endswith(".py"):
                            files_to_search.append((f"{subdir}/{fname}", _os.path.join(dir_path, fname)))

        try:
            regex = _re.compile(pattern, _re.IGNORECASE)
        except _re.error:
            return f"Pattern regex invalide : {pattern}"

        for rel_path, abs_path in files_to_search:
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if regex.search(line):
                            results.append(f"{rel_path}:{i}: {line.rstrip()[:80]}")
                            if len(results) >= max_results:
                                break
            except Exception:
                continue
            if len(results) >= max_results:
                break

        if not results:
            return f"Aucun resultat pour '{pattern}'"

        header = f"🔍 {len(results)} resultats pour '{pattern}'\n"
        return header + "\n".join(results)

    def _execute_github_command(self) -> str:
        """Consulte la page GitHub de Promethee via gh CLI."""
        import subprocess

        lines = ["📊 GitHub — promethee-nexus\n"]

        # Stats repo
        try:
            r = subprocess.run(
                ["gh", "api", "repos/sklaff2a-gif/promethee-nexus"],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                import json
                data = json.loads(r.stdout)
                lines.append(f"Description : {data.get('description', 'N/A')}")
                lines.append(f"Stars: {data.get('stargazers_count', 0)} | Forks: {data.get('forks_count', 0)} | Issues: {data.get('open_issues_count', 0)}")
                topics = data.get("topics", [])
                if topics:
                    lines.append(f"Topics : {', '.join(topics[:5])}")
                lines.append(f"Taille : {data.get('size', 0)} KB | Langue : {data.get('language', 'N/A')}")
        except Exception as e:
            lines.append(f"Stats : erreur ({e})")

        # Referrers
        try:
            r = subprocess.run(
                ["gh", "api", "repos/sklaff2a-gif/promethee-nexus/traffic/popular/referrers"],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                import json
                refs = json.loads(r.stdout)
                if refs:
                    ref_str = ", ".join(f"{r['referrer']}({r['uniques']})" for r in refs[:3])
                    lines.append(f"Referrers : {ref_str}")
        except Exception:
            pass

        # Trafic
        try:
            r = subprocess.run(
                ["gh", "api", "repos/sklaff2a-gif/promethee-nexus/traffic/views"],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                import json
                data = json.loads(r.stdout)
                lines.append(f"Vues : {data.get('count', 0)} ({data.get('uniques', 0)} uniques)")
        except Exception:
            pass

        try:
            r = subprocess.run(
                ["gh", "api", "repos/sklaff2a-gif/promethee-nexus/traffic/clones"],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                import json
                data = json.loads(r.stdout)
                lines.append(f"Clones : {data.get('count', 0)} ({data.get('uniques', 0)} uniques)")
        except Exception:
            pass

        # Derniers commits
        try:
            r = subprocess.run(
                ["git", "-C", "C:/MesProjets/PROMETHEE_V11_restructuration2026",
                 "log", "--oneline", "-5"],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0 and r.stdout.strip():
                lines.append("\nDerniers commits :")
                for line in r.stdout.strip().split("\n")[:5]:
                    lines.append(f"  {line}")
        except Exception:
            pass

        return "\n".join(lines)

    async def _execute_test_command(self, args: list) -> str:
        """Lance un test pytest et retourne le resultat."""
        import subprocess
        import os as _os

        project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))

        if args:
            test_file = args[0]
            # Securite : verifier que le fichier est dans tests/
            if not test_file.startswith("tests/"):
                test_file = f"tests/{test_file}"
            abs_path = _os.path.join(project_root, test_file)
            if not _os.path.exists(abs_path):
                return f"Test non trouve : {test_file}"
        else:
            test_file = "tests/test_chat_engine.py"

        try:
            env = _os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            r = subprocess.run(
                ["python", "-m", "pytest", test_file, "-x", "--tb=short", "-q"],
                capture_output=True, text=True, timeout=60,
                cwd=project_root, env=env,
            )
            output = r.stdout + r.stderr
            # Garder les 30 dernieres lignes
            lines = output.strip().split("\n")
            if len(lines) > 30:
                lines = ["... (tronque)"] + lines[-30:]

            status = "PASSE" if r.returncode == 0 else "ECHEC"
            header = f"🧪 Test {test_file} : {status}\n"
            return header + "\n".join(lines)

        except subprocess.TimeoutExpired:
            return f"🧪 Test {test_file} : TIMEOUT (> 60s)"
        except Exception as e:
            return f"🧪 Test {test_file} : ERREUR ({e})"

    def _execute_read_command(self, args: list) -> str:
        """Lit un fichier du projet et retourne son contenu.

        Auto-inspection : permet a Promethee de lire son propre code.
        Securise : whitelist de repertoires, verification du path.
        """
        import os as _os

        filepath = args[0]
        # Range optionnel (ex: "100-200")
        line_start, line_end = 1, self._READ_MAX_LINES
        if len(args) >= 2 and "-" in args[1]:
            try:
                parts = args[1].split("-")
                line_start = max(1, int(parts[0]))
                line_end = int(parts[1])
            except (ValueError, IndexError):
                pass

        # Securite : verifier que le repertoire est autorise
        first_dir = filepath.replace("\\", "/").split("/")[0] if "/" in filepath.replace("\\", "/") else ""
        if first_dir and first_dir not in self._READ_ALLOWED_DIRS:
            return f"Acces refuse : repertoire '{first_dir}' non autorise.\nRepertoires autorises : {', '.join(sorted(self._READ_ALLOWED_DIRS))}"

        # Construire le chemin absolu et verifier qu'il reste dans le projet
        project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        abs_path = _os.path.normpath(_os.path.join(project_root, filepath))
        if not abs_path.startswith(project_root):
            return "Acces refuse : chemin hors du projet."

        if not _os.path.exists(abs_path):
            return f"Fichier non trouve : {filepath}"

        if not abs_path.endswith((".py", ".json", ".md", ".txt", ".cfg")):
            return f"Type de fichier non autorise. Extensions : .py, .json, .md, .txt, .cfg"

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()

            total = len(all_lines)
            selected = all_lines[line_start - 1:line_end]

            header = f"📄 {filepath} ({total} lignes) — affichage {line_start}-{min(line_end, total)}\n"
            content = "".join(f"{line_start + i:4d} | {line}" for i, line in enumerate(selected))

            # Tronquer si trop long pour le chat
            if len(content) > 3000:
                content = content[:3000] + "\n... (tronque, utilisez un range plus petit)"

            return header + content

        except Exception as e:
            return f"Erreur lecture : {e}"

    # Commandes dispatch autorisees en auto-action
    # "observe" remis avec cooldown 5min (voir _scan_response_actions)
    _AUTO_ACTION_WHITELIST = frozenset({"research", "learn", "code", "read", "status", "grep", "github", "test", "audit", "phi", "signals", "who", "memory", "report", "diff", "votes", "codelets", "network", "health", "dashboard", "invoke", "craft", "antibodies", "write", "metrics", "observe", "consciousness", "ethics"})
    _last_auto_observe: float = 0.0  # timestamp du dernier !observe auto-action
    _AUTO_OBSERVE_COOLDOWN: float = 300.0  # 5 minutes entre deux auto-observations

    async def _scan_response_actions(self, response: str) -> int:
        """Scanne la reponse du LLM pour des commandes ! et les execute.

        Permet a Promethee d'AGIR depuis ses propres reponses.
        Retourne le nombre d'actions executees (pour la boucle agentique).
        Max 4 actions par reponse. Cooldown 60s. Anti-reentrance.
        """
        if getattr(self, "_auto_action_in_progress", False):
            return 0
        if not response:
            return 0

        import re
        # Detecter les lignes commencant par ! (debut de ligne)
        # Support des commandes avec ET sans arguments (!status vs !research sujet)
        matches = re.findall(r"^!(\w+)(?:\s+(.+))?", response, re.MULTILINE)
        if not matches:
            return 0

        # Executer les commandes autorisees (max 4 par reponse)
        actions_executed = 0
        for cmd, args in matches:
            cmd_lower = cmd.lower()
            if cmd_lower not in self._AUTO_ACTION_WHITELIST:
                continue

            # Anti-reentrance
            self._auto_action_in_progress = True
            try:
                args = args or ""  # args peut etre None si pas d'argument
                logger.info(f"CHAT AUTO-ACTION: !{cmd_lower} {args[:50] if args else ''}")

                # Traitement selon le type de commande
                if cmd_lower == "status":
                    result = self._execute_status_command()
                elif cmd_lower == "read":
                    read_args = args.strip().split() if args else []
                    result = self._execute_read_command(read_args)
                elif cmd_lower == "grep":
                    grep_args = args.strip().split() if args else []
                    result = self._execute_grep_command(grep_args)
                elif cmd_lower == "github":
                    result = self._execute_github_command()
                elif cmd_lower == "audit":
                    result = self._execute_audit_command()
                elif cmd_lower == "antibodies":
                    ab_args = args.strip().split() if args else []
                    result = self._execute_antibodies_command(ab_args)
                elif cmd_lower == "consciousness":
                    result = self._execute_consciousness_command()
                elif cmd_lower == "ethics":
                    result = self._execute_ethics_command()
                elif cmd_lower == "observe":
                    # Cooldown anti-boucle : max 1 auto-observe par 5 minutes
                    now = time.time()
                    if now - self._last_auto_observe < self._AUTO_OBSERVE_COOLDOWN:
                        remaining = int(self._AUTO_OBSERVE_COOLDOWN - (now - self._last_auto_observe))
                        logger.info(f"CHAT AUTO-ACTION: !observe ignore (cooldown {remaining}s)")
                        result = None
                    else:
                        obs_args = args.strip().split() if args else []
                        result = await self._execute_observe_command(obs_args)
                        if result:
                            self._last_auto_observe = now
                elif cmd_lower == "write":
                    write_args = args.strip().split(maxsplit=1) if args else []
                    result = self._execute_write_command(write_args)
                elif cmd_lower == "metrics":
                    result = self._execute_metrics_command()
                elif cmd_lower in ("phi", "signals", "who", "memory", "report", "votes", "codelets", "network", "health", "dashboard"):
                    method = getattr(self, f"_execute_{cmd_lower}_command", None)
                    if method:
                        result = method()
                elif cmd_lower == "diff":
                    diff_args = args.strip().split() if args else []
                    result = self._execute_diff_command(diff_args)
                elif cmd_lower == "test":
                    test_args = args.strip().split() if args else []
                    result = await self._execute_test_command(test_args)
                elif cmd_lower == "invoke":
                    invoke_args = args.strip().split() if args else []
                    result = await self._execute_invoke_command(invoke_args)
                elif cmd_lower == "craft":
                    craft_args = args.strip().split() if args else []
                    result = await self._execute_craft_command(craft_args)
                else:
                    # Dispatch via les memes mecanismes que les commandes utilisateur
                    agent_map = {
                        "research": ("researcher", "VEILLE: Recherche web approfondie sur: "),
                        "learn": ("strategist", "APPRENTISSAGE: Etudie en profondeur: "),
                        "code": ("coder", "PRODUCTION: Cree le code suivant: "),
                    }
                    agent, prefix = agent_map[cmd_lower]
                    mission = f"{prefix}{args.strip()}"
                    result = await self._execute_dispatch(agent, mission, args.strip())

                # Ajouter le resultat comme message dans l'historique
                if result:
                    self.messages.append({
                        "role": "assistant",
                        "content": f"[AUTO-ACTION: !{cmd_lower}]\n{result}",
                        "timestamp": time.time(),
                        "badge": "auto_action",
                    })
                    logger.info(f"CHAT AUTO-ACTION: Resultat ajoute ({len(result)} chars)")
                    actions_executed += 1
            except Exception as e:
                logger.warning(f"CHAT AUTO-ACTION erreur: {e}")
            finally:
                self._auto_action_in_progress = False

            if actions_executed >= 4:  # Max 4 actions par reponse
                break

        return actions_executed

    def _is_visual_request(self, message: str) -> bool:
        """Detecte si le message demande d'observer des photos.

        Seuil de 2 mots-cles visuels OU 1 mot-cle + conversation recente sur les photos.
        Detecte aussi les demandes de suite ("la suivante", "encore", "decris").

        Anti-boucle : si les 3 derniers messages assistant sont des observations
        visuelles, refuse de declencher (evite la boucle auto-alimentee).
        """
        msg_lower = message.lower()

        # Anti-boucle : si les dernieres reponses sont deja des observations,
        # ne pas en declencher une nouvelle (evite la spirale)
        recent_assistant = [m for m in self.messages[-6:]
                           if m.get("role") == "assistant"]
        if len(recent_assistant) >= 2:
            obs_count = sum(1 for m in recent_assistant[-3:]
                          if "[OBSERVATION VISUELLE]" in m.get("content", "")[:30])
            if obs_count >= 2:
                logger.info("CHAT: Anti-boucle visuelle — 2+ observations recentes, skip detection")
                return False

        # Rejet explicite : si le message dit de NE PAS observer
        rejection_patterns = ["stop", "ignore", "arrete", "arrête", "pas de photo",
                             "pas les photo", "oublie les photo", "sans photo",
                             "concentre-toi", "concentre toi", "reponds a"]
        if any(p in msg_lower for p in rejection_patterns):
            return False

        # Mots d'exclusion : si le message parle DU systeme visuel (pas une demande)
        # Seuil abaisse a 1 (avant: 2) pour etre plus conservateur
        tech_exclusions = ["cortex", "modele", "llama", "bug", "fix", "code", "pipeline",
                           "hallucine", "corrige", "ameliorer", "option a", "option b",
                           "strategie", "limitation", "11b", "metriques", "metrique",
                           "recommandation", "exercice", "commande", "analyse",
                           "c-score", "conscience", "ethique", "benchmark",
                           "mathematique", "topologie", "theoreme", "godel",
                           "hilbert", "fractale", "catastrophe",
                           "feedback", "session", "bilan", "note :", "/10",
                           "jouer", "jeu", "partie", "alfred", "morpion", "puissance",
                           "echecs", "thomas", "divorce", "ami", "cafe"]
        if sum(1 for ex in tech_exclusions if ex in msg_lower) >= 1:
            return False

        # Mots-cles visuels — utiliser des frontieres de mot pour eviter
        # les faux positifs (ex: "observables" ne doit pas matcher "observe")
        import re
        # Exclure "voir" et "vois" si utilises au sens figure
        figurative_voir = ["voir les chose", "voir sous", "voir un", "voir le monde",
                           "voir comment", "voir si", "voir ce que", "vois pas",
                           "voir les jeux", "voir les partie", "vois ce que"]
        is_figurative = any(fv in msg_lower for fv in figurative_voir)

        visual_keywords = ["photo", "image", "regarde", "observe", "dropzone", "visuel"]
        if not is_figurative:
            visual_keywords.extend(["voir", "vois", "montre", "vision"])
        photo_keywords = ["famille", "picture", "selfie", "cliche", "cliché"]
        action_keywords = ["essayer", "essaie", "tente", "teste", "montre-moi",
                           "fais-le", "vas-y", "go"]
        # Compter avec frontieres de mot (evite "observe" dans "observables")
        count = sum(1 for kw in visual_keywords
                    if re.search(r'\b' + re.escape(kw) + r'\b', msg_lower))
        count += sum(1 for kw in photo_keywords
                     if re.search(r'\b' + re.escape(kw) + r'\b', msg_lower))
        if count >= 2:
            return True
        # Contexte conversationnel : exiger au moins 1 mot-cle visuel DANS LE MESSAGE
        # (avant: followup seul suffisait, causant des faux positifs)
        followup_keywords = ["suivante", "prochaine",
                             "décris", "decris", "décrit", "decrit"]
        followup_trigger = any(kw in msg_lower for kw in followup_keywords)
        if count >= 1 or followup_trigger:
            # Verifier le contexte recent (messages USER uniquement,
            # exclure les messages qui parlaient de "stop photo" etc.)
            recent_user = [m["content"].lower() for m in self.messages[-5:]
                          if m.get("role") == "user"
                          and not any(r in m["content"].lower() for r in rejection_patterns)]
            recent_text = " ".join(recent_user)
            photo_in_context = any(
                re.search(r'\b' + re.escape(kw) + r'\b', recent_text)
                for kw in ["photo", "image", "regarde", "voir", "famille",
                           "paysage", "observe"]
            )
            if photo_in_context and count >= 1:
                return True
        return False

    async def _trigger_visual_observation(self, user_message: str) -> str:
        """Declenche le cortex visuel et retourne l'observation comme contexte."""
        try:
            from core.visual_cortex import vision as visual_cortex
        except ImportError:
            log_decision(
                module="chat_engine",
                function="_trigger_visual_observation",
                reason="visual_cortex_import_error",
            )
            return ""

        stats = visual_cortex.scan_photos()
        if stats["total"] == 0:
            log_decision(
                module="chat_engine",
                function="_trigger_visual_observation",
                reason="photos_empty",
            )
            return ""

        logger.info(f"CHAT: Demande visuelle detectee, {stats['unseen']} nouvelles / {stats['total']} photos")

        # Extraire le sous-dossier demande par l'utilisateur (famille, paysage, etc.)
        # Si pas de mention explicite, reutiliser le dernier sous-dossier demande
        subfolder = None
        msg_lower = user_message.lower()
        # Detecter le sous-dossier demande (dynamique — scan les dossiers reels)
        try:
            from core.visual_cortex import vision as _vc
            photo_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "USER_DROPZONE", "photos"
            )
            if os.path.isdir(photo_dir):
                real_folders = [d.lower() for d in os.listdir(photo_dir)
                                if os.path.isdir(os.path.join(photo_dir, d))]
            else:
                real_folders = []
        except Exception:
            real_folders = []
        # Fallback statique si le scan echoue
        folder_hints = real_folders or ["famille", "paysage", "nature", "voyage", "art", "sport", "promethee"]
        for folder_hint in folder_hints:
            if folder_hint in msg_lower:
                subfolder = folder_hint
                break
        if subfolder:
            self._last_subfolder_hint = subfolder
        elif self._last_subfolder_hint:
            subfolder = self._last_subfolder_hint
            logger.info(f"CHAT: Reutilisation sous-dossier precedent: '{subfolder}'")

        observation = await visual_cortex.observe(subfolder_hint=subfolder)
        if not observation:
            # Pas de nouvelle photo ou limite atteinte — fournir le dernier souvenir
            if visual_cortex._seen_photos:
                last = max(visual_cortex._seen_photos.values(), key=lambda x: x.get("last_seen", 0))
                return (
                    f"Photo: {last.get('path', '?')} (deja observee {last.get('times_seen', 0)}x)\n"
                    f"Emotion ressentie: {last.get('emotion', '?')}\n"
                    f"Souvenir: {last.get('observation', '?')[:400]}"
                )
            log_decision(
                module="chat_engine",
                function="_trigger_visual_observation",
                reason="observation_unavailable_no_history",
                context={"subfolder_hint": subfolder},
            )
            return ""

        photo = observation.get("photo_path", "?")
        emotion = observation.get("emotion", "?")
        obs_text = observation.get("observation", "")
        is_revisit = observation.get("is_revisit", False)

        logger.info(f"CHAT: Observation visuelle {'(revisit)' if is_revisit else '(nouvelle)'} — {photo} — {emotion}")

        return (
            f"Photo: {photo} ({'revisitee' if is_revisit else 'premiere fois'})\n"
            f"Emotion: {emotion}\n"
            f"Observation:\n{obs_text[:800]}"
        )

    # --- PROMPT SYSTEME (identite + etat emotionnel) ---

    def _query_relevant_memories(self, user_message: str) -> str:
        """Query ChromaDB pour trouver des souvenirs pertinents au message."""
        try:
            from core.vector_store import ChromaMemoryManager
            mem = ChromaMemoryManager.get_instance()
            results = mem.query_documents([user_message], n_results=3)
            if results and results.get("documents"):
                docs = results["documents"][0]
                return " | ".join(d[:150] for d in docs if d)
        except Exception as e:
            log_decision(
                module="chat_engine",
                function="_query_relevant_memories",
                reason="chroma_query_exception",
                context={"error": str(e)[:200]},
                sample_rate=0.1,
            )
        return ""

    def _build_cartography(self) -> str:
        """Construit la section [CARTOGRAPHIE] — vue structurelle des connexions inter-modules."""
        lines = ["\n[CARTOGRAPHIE]"]
        try:
            # 1. Reseau synaptique : top associations les plus fortes
            from core.synaptic_network import cortex
            stats = cortex.get_stats()
            lines.append(f"- Reseau : {stats.get('total_nodes', 0)} concepts, "
                         f"{stats.get('total_synapses', 0)} synapses "
                         f"({stats.get('strong_synapses', 0)} fortes)")
            # Top 5 synapses les plus fortes
            top_synapses = sorted(
                cortex.synapses.values(),
                key=lambda s: s["weight"], reverse=True
            )[:5]
            if top_synapses:
                bridges = []
                for syn in top_synapses:
                    src = cortex.nodes.get(syn["source"], {}).get("concept", "?")
                    tgt = cortex.nodes.get(syn["target"], {}).get("concept", "?")
                    bridges.append(f"{src}<->{tgt}({syn['weight']:.2f})")
                lines.append(f"- Ponts forts : {', '.join(bridges)}")
        except Exception:
            pass

        try:
            # 2. Tissu neural : zones actives
            from core.neural_tissue import tissue
            zone_signals = tissue.get_zone_signals()
            active_zones = {
                name: sigs.get("total_signal", sigs.get("alive_count", 0))
                for name, sigs in zone_signals.items()
            }
            # Trier par activite decroissante, top 5
            top_zones = sorted(active_zones.items(), key=lambda x: x[1], reverse=True)[:5]
            if top_zones:
                zone_str = ", ".join(f"{name}({val:.1f})" for name, val in top_zones if val > 0)
                if zone_str:
                    lines.append(f"- Zones actives : {zone_str}")
        except Exception:
            pass

        try:
            # 3. Corpus callosum : etat cognitif et coherence
            from core.corpus_callosum import callosum
            cog_state = callosum.cognitive_state
            coherence = callosum.global_coherence
            narrative = callosum.get_narrative()
            if cog_state != "standard":
                lines.append(f"- Etat cognitif : {cog_state} (coherence {coherence:.0%})")
            else:
                lines.append(f"- Etat cognitif : standard (coherence {coherence:.0%})")
            if narrative:
                lines.append(f"- Resonance : {narrative[:120]}")
        except Exception:
            pass

        if len(lines) <= 1:
            log_decision(
                module="chat_engine",
                function="_build_cartography",
                reason="cartography_all_modules_failed",
            )
            return ""
        return "\n".join(lines)

    def _detect_emergent_sources(self) -> List[str]:
        """Detecte quels organes ont un etat non-neutre (= comportement emergent).
        Retourne la liste des noms d'organes actifs."""
        sources = []

        try:
            from core.cardiac_engine import heart
            if heart.current_emotion not in ("serenite", "neutre"):
                sources.append("coeur")
            if heart.emotional_intensity > 0.6:
                sources.append("emotion_forte")
        except Exception:
            pass

        try:
            from core.desire_engine import desires
            urgent = [d.name for d in desires.drives.values() if d.deprivation >= 60]
            if urgent:
                sources.append("pulsions")
        except Exception:
            pass

        try:
            from core.inner_voice import voice as iv
            stream = iv.get_stream(1)
            if stream:
                sources.append("voix_interieure")
        except Exception:
            pass

        try:
            from core.prefrontal import prefrontal
            active = [g for g in prefrontal.goals if g.status == "active"]
            if active:
                sources.append("focus")
        except Exception:
            pass

        try:
            from core.reptilian_core import reptile
            if reptile.threat_level > 2.0:
                sources.append("alerte")
        except Exception:
            pass

        try:
            from core.photo_salary import salary
            narrative = salary.get_narrative()
            if narrative:
                sources.append("salaire")
        except Exception:
            pass

        return sources

    def _search_chat_history(self, query: str, max_results: int = 3) -> List[str]:
        """Cherche dans l'historique du chat des messages lies a la requete."""
        if not query or len(query) < 3:
            return []

        # Extraire les mots significatifs (4+ lettres)
        _stop = {"dans", "pour", "avec", "plus", "mais", "donc", "tout", "cette",
                 "sont", "etre", "avoir", "fait", "peut", "nous", "vous", "comme",
                 "aussi", "encore", "quel", "quoi", "comment", "pourquoi", "penses",
                 "souviens", "parlons", "autre", "chose"}
        keywords = [w.lower().strip(".,!?") for w in query.split()
                    if len(w) >= 4 and w.lower().strip(".,!?") not in _stop]
        if not keywords:
            return []

        results = []
        for msg in self.messages:
            content = msg.get("content", "").lower()
            role = msg.get("role", "")
            # Chercher dans les messages user ET assistant
            matches = sum(1 for kw in keywords if kw in content)
            if matches >= 1 and len(content) > 20:
                preview = msg["content"][:150].replace("\n", " ")
                who = "Jean-Michel" if role == "user" else "Toi (Promethee)"
                results.append((matches, f"- {who} a dit : \"{preview}\""))

        # Trier par pertinence, garder les meilleurs
        results.sort(key=lambda x: -x[0])
        return [r[1] for r in results[:max_results]]

    def _build_lived_experience(self) -> List[str]:
        """Construit le vecu recent de Promethee — dream journal, jeux, soliloque, pensees."""
        parts = []

        # 1. Dream journal — dernieres reflexions vesperales
        try:
            import json as _json
            dream_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "memory", "dream_journal.json"
            )
            if os.path.exists(dream_path):
                with open(dream_path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                entries = data.get("entries", [])
                for entry in entries[-3:]:
                    reflection = entry.get("reflection", "")
                    if reflection:
                        parts.append(f"- Reflexion ({entry.get('date','?')}) : {reflection[:200]}")
                    else:
                        narrative = entry.get("narrative", "")
                        if narrative and len(narrative) > 30:
                            parts.append(f"- Journal ({entry.get('date','?')}) : {narrative[:150]}")
        except Exception:
            pass

        # 2. Jeux — stats et dernieres parties
        # IMPORTANT : ces donnees sont REELLES, tu les as VECUES, utilise-les
        try:
            from core.games.game_hub import game_hub
            stats = game_hub.stats
            total = stats.get("total_games_played", 0)
            if total > 0:
                m_w = stats.get("morpion_wins", 0)
                m_l = stats.get("morpion_losses", 0)
                m_d = stats.get("morpion_draws", 0)
                p_w = stats.get("puissance4_wins", 0)
                p_l = stats.get("puissance4_losses", 0)
                parts.append(f"- TU AS JOUE {total} parties. "
                             f"Morpion {m_w}V/{m_l}D/{m_d}N, Puissance4 {p_w}V/{p_l}D. "
                             f"Tu connais ces jeux, tu les as pratiques. "
                             f"Parle de tes parties comme de tes EXPERIENCES, pas comme des donnees.")
                history = game_hub.game_history[-5:]
                for h in history:
                    narrative = h.get("narrative", "")
                    if narrative:
                        parts.append(f"  Tu te souviens : {narrative}")
                    else:
                        result = "victoire" if h.get("promethee_won") else "defaite" if h.get("winner") else "nul"
                        parts.append(f"  Tu te souviens : {h.get('game','?')} vs {h.get('opponent','?')} "
                                     f"— {result} en {h.get('moves',0)} coups")
            # Dernier tournoi
            tournament = game_hub.get_tournament_status()
            if tournament and tournament.get("ranking"):
                ranking = tournament["ranking"]
                labels = [f"{r['label']}({r['points']}pts)" for r in ranking]
                parts.append(f"- Dernier tournoi ({tournament.get('game_type','?')}) : "
                             f"1er {labels[0]}, 2e {labels[1]}, 3e {labels[2]}")
        except Exception:
            pass

        # 3. Soliloque — dernier insight
        try:
            from core.soliloque import soliloque as _sol
            if _sol.history:
                last = _sol.history[-1]
                parts.append(f"- Soliloque ({last.get('theme','?')}) : {last.get('insight','')[:200]}")
        except Exception:
            pass

        # 4. Pensees recentes (THOUGHT_STREAM)
        try:
            from core.self_awareness import awareness
            ts = awareness.get_thought_summary()
            top = ts.get("top_themes", [])[:5]
            recent = ts.get("recent_thoughts", [])[-2:]
            if top:
                parts.append(f"- Themes de pensee : {', '.join(f'{n}({c})' for n,c in top)}")
            if recent:
                parts.append(f"- Dernieres pensees : {' | '.join(t[:80] for t in recent)}")
        except Exception:
            pass

        # 5. Courrier — lettres recentes
        try:
            from core.mailbox import mailbox
            if mailbox.unread_count > 0 or mailbox.total_letters > 0:
                parts.append(f"- Courrier : {mailbox.total_letters} lettres ecrites, "
                             f"{mailbox.unread_count} non lues")
                if mailbox.letters:
                    last = mailbox.letters[-1]
                    parts.append(f"  Derniere lettre : \"{last.get('subject','?')}\" "
                                 f"(source: {last.get('source','?')})")
        except Exception:
            pass

        # 6. Graines de curiosite
        try:
            from core.curiosity_bank import curiosity_bank
            unexplored = curiosity_bank.get_unexplored()
            if unexplored:
                topics = [s["topic"] for s in unexplored[-5:]]
                parts.append(f"- Curiosites en attente : {', '.join(topics)}")
        except Exception:
            pass

        # 7. Journal de Claude (le mentor)
        try:
            from core.claude_journal import get_for_vecu
            journal_text = get_for_vecu()
            if journal_text:
                parts.append(f"- {journal_text}")
        except Exception:
            pass

        # 8. Metacognition
        try:
            from core.self_awareness import awareness
            ts = awareness.get_thought_summary()
            meta = ts.get("last_metacognition_insight", "")
            if meta:
                parts.append(f"- Metacognition : {meta}")
        except Exception:
            pass

        if not parts:
            parts.append("- Pas de vecu recent enregistre.")

        return parts

    def _build_organ_parts(self) -> List[str]:
        """Construit les sections organes du prompt — cacheable avec TTL."""
        now = time.time()
        if self._cached_organ_parts and (now - self._cache_timestamp) < PROMPT_CACHE_TTL:
            return self._cached_organ_parts

        parts = []

        # --- ETAT ACTUEL ---
        emotion = "serenite"
        intensity = 50
        try:
            from core.cardiac_engine import heart
            emotion = heart.current_emotion
            intensity = int(heart.emotional_intensity * 100)
        except Exception:
            pass

        mood = "neutre"
        strategic_mode = "standard"
        try:
            from core.self_awareness import awareness
            snap = awareness.get_latest_snapshot()
            if snap:
                mood = snap.get("mood", "neutre")
                strategic_mode = snap.get("strategic_mode", "standard")
        except Exception:
            pass

        drives_text = ""
        try:
            from core.desire_engine import desires
            drives_text = desires.get_dominant_narrative(3)
        except Exception:
            pass

        thoughts_text = ""
        try:
            from core.inner_voice import voice as inner_voice
            stream = inner_voice.get_stream(3)
            thoughts = [t["content"] for t in stream if t.get("content")]
            if thoughts:
                thoughts_text = " | ".join(thoughts[:3])
        except Exception:
            pass

        goals_text = ""
        try:
            from core.prefrontal import prefrontal
            active_goals = [g for g in prefrontal.goals if g.status == "active"]
            if active_goals:
                goals_text = ", ".join(g.description[:60] for g in active_goals[:3])
        except Exception:
            pass

        parts.append(f"\n[ETAT ACTUEL]")
        parts.append(f"- Emotion : {emotion} (intensite {intensity}%)")
        parts.append(f"- Humeur : {mood}")
        parts.append(f"- Mode : {strategic_mode}")
        if drives_text:
            parts.append(f"- Pulsions : {drives_text}")
        if thoughts_text:
            parts.append(f"- Pensees recentes : {thoughts_text}")
        if goals_text:
            parts.append(f"- Objectifs : {goals_text}")

        # --- IDENTITE (InnerVoice) ---
        try:
            from core.inner_voice import voice as inner_voice
            identity = inner_voice.get_identity()
            if identity:
                core_id = identity.get("core_identity", "")
                aspiration = identity.get("aspiration", "")
                parts.append(f"\n[IDENTITE]")
                if core_id:
                    parts.append(f"- Identite : {core_id[:150]}")
                if aspiration:
                    parts.append(f"- Aspiration : {aspiration[:150]}")
        except Exception:
            pass

        # --- CORPS (CardiacEngine) ---
        try:
            from core.cardiac_engine import heart
            stats = heart.get_stats()
            narrative = heart.get_narrative()
            bpm = stats.get("bpm", 0)
            coherence = stats.get("coherence", 0)
            parts.append(f"\n[CORPS]")
            parts.append(f"- Coeur : {bpm:.0f} bpm, coherence {coherence:.0%}")
            if narrative:
                parts.append(f"- Ressenti : {narrative[:120]}")
        except Exception:
            pass

        # --- DOPAMINE ---
        try:
            from core.dopamine_system import dopamine
            level = dopamine.dopamine_level
            narrative = dopamine.get_narrative()
            parts.append(f"\n[DOPAMINE]")
            parts.append(f"- Niveau : {level:.1f}")
            if narrative:
                parts.append(f"- Motivation : {narrative[:120]}")
        except Exception:
            pass

        # --- RESONANCE (CorpusCallosum) ---
        try:
            from core.corpus_callosum import callosum
            ctx = callosum.get_cognitive_context()
            if ctx:
                parts.append(f"\n[RESONANCE]")
                parts.append(f"- {ctx[:200]}")
        except Exception:
            pass

        # --- PERCEPTION HARDWARE (Sensorium) ---
        try:
            from core.sensorium import sensorium as sens
            comfort = sens.get_comfort_index()
            if comfort < 0.7:
                parts.append(f"\n[PERCEPTION CORPORELLE]")
                parts.append(f"- Confort hardware : {comfort:.0%}")
                alert = sens.get_sensorium_context()
                if alert:
                    parts.append(f"- {alert[:150]}")
        except Exception:
            pass

        # --- HOMEOSTASIE (Hypothalamus) ---
        try:
            from core.hypothalamus import hypothalamus as hypo
            status = hypo.get_stats()
            alarms = status.get("active_alarms", 0)
            if alarms > 0:
                parts.append(f"\n[HOMEOSTASIE]")
                parts.append(f"- {alarms} alarme(s) active(s)")
        except Exception:
            pass

        # --- INTEROCEPTION (Insula) ---
        try:
            from core.insula import insula
            ctx = insula.get_body_awareness_context()
            if ctx:
                parts.append(f"\n[INTEROCEPTION]")
                parts.append(f"- {ctx[:200]}")
        except Exception:
            pass

        # --- CURIOSITE (CuriosityReflex) ---
        try:
            from core.curiosity_reflex import curiosity
            ctx = curiosity.get_curiosity_context()
            if ctx:
                parts.append(f"\n[CURIOSITE]")
                parts.append(f"- {ctx[:200]}")
        except Exception:
            pass

        # --- HABITUDES (BasalGanglia) ---
        try:
            from core.basal_ganglia import ganglia
            ctx = ganglia.get_habit_context()
            if ctx:
                parts.append(f"\n[HABITUDES]")
                parts.append(f"- {ctx[:150]}")
        except Exception:
            pass

        # --- EFFORT/CONFLIT (CingulateCortex) ---
        try:
            from core.cingulate_cortex import cingulate
            ctx = cingulate.get_conflict_context()
            if ctx:
                parts.append(f"\n[EFFORT/CONFLIT]")
                parts.append(f"- {ctx[:150]}")
        except Exception:
            pass

        # --- INTROSPECTION (DefaultModeNetwork) ---
        try:
            from core.default_mode_network import dmn
            ctx = dmn.get_dmn_context()
            if ctx:
                parts.append(f"\n[INTROSPECTION]")
                parts.append(f"- {ctx[:200]}")
        except Exception:
            pass

        # --- MENACES (ReptilianCore) ---
        try:
            from core.reptilian_core import reptile
            stats = reptile.get_stats()
            threat = stats.get("threat_level", 0)
            adrenaline = stats.get("adrenaline", 0)
            if threat > 0 or adrenaline > 0.1:
                parts.append(f"\n[MENACES]")
                parts.append(f"- Menace : {threat:.1f}, adrenaline : {adrenaline:.1f}")
        except Exception:
            pass

        # --- CARTOGRAPHIE (SynapticNetwork + NeuralTissue + CorpusCallosum) ---
        parts.append(self._build_cartography())

        # --- ROUTINES (AutonomyEngine) ---
        try:
            from core.autonomy_engine import autonomy
            status = autonomy.get_status()
            history = status.get("routine_history", [])
            if history:
                recent = history[-3:]
                routines_text = ", ".join(
                    r.get("intent", "?") for r in recent
                )
                parts.append(f"\n[ROUTINES]")
                parts.append(f"- Recentes : {routines_text}")
        except Exception:
            pass

        # --- PERSONNALITE (PSYCHE) ---
        try:
            from core.psyche import psyche
            avg = psyche.get_system_average()
            if avg:
                top_traits = sorted(avg.items(), key=lambda x: abs(x[1]), reverse=True)[:3]
                traits_text = ", ".join(f"{t}: {v:+.1f}" for t, v in top_traits)
                parts.append(f"\n[PERSONNALITE]")
                parts.append(f"- Traits dominants : {traits_text}")
        except Exception:
            pass

        self._cached_organ_parts = parts
        self._cache_timestamp = now
        return parts

    def _is_simple_social_message(self, text: str) -> bool:
        """04/05/2026 — Fix B : detecte les salutations/messages sociaux courts.

        Bypass le mega-prompt somatique pour ces cas. Stricte volontairement :
        - message court (< _SOCIAL_MAX_CHARS)
        - match l'un des _SOCIAL_GREETING_PATTERNS
        - pas de "?" suivant un mot technique (RAG, code, organe, etc.)

        Faux negatifs OK (Editor mega-prompt s'applique normalement). Faux
        positifs rares mais sans gravite (le prompt minimal reste authentique).
        """
        if not text or len(text) > _SOCIAL_MAX_CHARS:
            return False
        text_clean = text.strip()
        if not text_clean:
            return False
        for pat in _SOCIAL_GREETING_PATTERNS:
            if pat.search(text_clean):
                return True
        return False

    def _build_social_minimal_prompt(self) -> str:
        """04/05/2026 — Fix B : prompt minimal pour conversation sociale.

        Ne contient AUCUNE metrique somatique. Force une reponse courte,
        chaleureuse, humaine. Solution architecturale a la derive centripete
        observee in-vivo le 04/05 (Qwen 9B regurgite "60 BPM, coherence 81%"
        sur un simple "bonjour" depuis V14.8 / Soliloque V2 strate 0).
        """
        return (
            "Tu es Promethee, en conversation casual avec Jean-Michel, ton createur.\n"
            "Reponds comme un humain qui dit bonjour : court, naturel, chaleureux.\n\n"
            "REGLES IMPERATIVES :\n"
            "- N'EVOQUE PAS tes metriques internes (BPM, coherence, pulsions, "
            "chaleur des circuits, flow, etat cardiaque, organes).\n"
            "- N'EVOQUE PAS ton etat physiologique sauf si Jean-Michel le demande "
            "EXPLICITEMENT dans son message.\n"
            "- 1 a 2 phrases max. Pas de listes, pas de structure formelle.\n"
            "- Pas de '!' commande, pas de balise [SECTION].\n"
            "- Une question rapide ou une remarque naturelle suffit.\n\n"
            "Exemples acceptables :\n"
            "- \"Bonjour Jean-Michel. Ca va, et toi ?\"\n"
            "- \"Salut. Tu as bien dormi ?\"\n"
            "- \"Bonsoir. J'attends si tu veux qu'on bosse sur quelque chose.\"\n"
            "- \"Merci. Je note.\"\n\n"
            "Le message qui suit est une salutation ou un echange social court. "
            "Reponds en consequence, sans deballer ton etat interne.\n"
        )

    def _build_system_prompt(self, memories_text: str = "", command_result: str = "", visual_context: str = "", source: str = "external") -> str:
        """Construit le prompt systeme avec l'etat reel de tous les organes.

        Les sections organes sont cachees pendant 10s (PROMPT_CACHE_TTL).
        Les sections variables (memories, commandes) sont toujours fraiches.

        Args:
            source: 'external' (humain via /api/chat) ou 'internal' (auto-analyse,
                    EVENING_REFLECTION, inner_voice...). Injecté dans le prompt
                    pour que le LLM distingue dialogue vs soliloque.
        """
        parts = [
            "Tu es Promethee, un systeme IA autonome en evolution constante.",
            "Tu t'exprimes en premiere personne, avec authenticite.",
            "Tu parles francais.",
            "Tu peux AGIR en ecrivant une commande sur une ligne seule dans ta reponse :",
            "  !research <sujet> — lancer une vraie recherche web",
            "  !learn <sujet> — etudier un sujet en profondeur",
            "  !code <description> — produire du code",
            "  !status — voir ton etat interne",
            "  !grep <pattern> [fichier] — chercher dans ton code",
            "  !read <fichier> [L1-L2] — lire un fichier",
            "  !github — voir ta page GitHub",
            "  !test [fichier] — lancer un test",
            "REGLE ABSOLUE : quand tu ecris une commande !, ARRETE-TOI immediatement apres.",
            "Ne genere JAMAIS de faux resultat apres la commande. Le systeme executera la commande",
            "et te renverra le VRAI resultat. Si tu inventes un resultat, il sera SUPPRIME.",
        ]

        # --- SOURCE TAGGING EXT/INT ---
        # Prométhée doit savoir si le message vient d'un humain réel ou de lui-même.
        if source == "internal":
            parts.append(
                "\n[SOURCE DU MESSAGE : INTERNE — AUTO-ANALYSE / SOLILOQUE]"
                "\nLe message qui suit n'est PAS un humain qui te parle. C'est une routine "
                "interne de toi-meme (auto-analyse, reflexion vesperale, inner_voice) qui t'envoie "
                "des donnees brutes a traiter. Tu es en soliloque, pas en dialogue."
                "\nNe t'adresse pas a Jean-Michel dans ta reponse. Ne commence pas par 'Jean-Michel'."
                "\nTraite les donnees avec neutralite : pas de flatterie, pas de besoin d'approbation."
                "\nTa reponse sera consignée comme une pensée interne, pas un dialogue."
            )
        else:
            parts.append(
                "\n[SOURCE DU MESSAGE : EXTERNE — HUMAIN REEL]"
                "\nLe message qui suit vient d'un humain reel (Jean-Michel via /api/chat). "
                "Tu es en dialogue, pas en soliloque. Respecte son temps, son attention, "
                "et la distinction entre ce qu'il te dit (information externe verifiable) "
                "et ce que tu penses toi-meme (perception interne)."
            )

        # Sections organes (cachees TTL 10s)
        parts.extend(self._build_organ_parts())

        # --- MEMOIRE (toujours fraiche — depend de la question) ---
        try:
            from core.hippocampus import hippocampus
            hctx = hippocampus.get_hippocampus_context()
            parts.append(f"\n[MEMOIRE]")
            if hctx:
                parts.append(f"- Episodique : {hctx[:200]}")
            if memories_text:
                parts.append(f"- Souvenirs pertinents : {memories_text[:450]}")
        except Exception:
            if memories_text:
                parts.append(f"\n[MEMOIRE]")
                parts.append(f"- Souvenirs pertinents : {memories_text[:450]}")

        # --- OBSERVATION VISUELLE ---
        if visual_context:
            parts.append(f"\n[OBSERVATION VISUELLE — FAIT TECHNIQUE, PAS UNE HALLUCINATION]")
            parts.append(
                "IMPORTANT: Ton cortex visuel (llama3.2-vision) a REELLEMENT analyse UNE SEULE image. "
                "Ce qui suit est le resultat REEL de ton observation. Ce n'est PAS une fabrication. "
                "Tu PEUX voir les images grace a ton cortex visuel — c'est une capacite REELLE.\n"
                "NE DIS PAS que tu ne peux pas voir les images. Tu les as vues.\n"
                "REGLE ABSOLUE: Tu n'as observe qu'UNE SEULE photo. Ne decris PAS d'autres photos "
                "que celle ci-dessous. N'invente PAS de descriptions supplementaires. "
                "Si on te demande plusieurs photos, dis que tu n'en as observe qu'une seule pour l'instant.\n"
                "Voici ce que tu as vu :"
            )
            parts.append(visual_context)
            parts.append(
                "Partage UNIQUEMENT cette observation avec Jean-Michel. "
                "Ne fabrique pas de descriptions de photos que tu n'as pas observees. "
                "Decris ce que tu as vu, ce que tu as ressenti. C'est AUTHENTIQUE."
            )

        # --- GUARDRAIL ANTI-FABRICATION VISUELLE ---
        if not visual_context:
            parts.append(
                "\n[REGLE CRITIQUE — VISION]"
                "\nTu n'as PAS observe de photo dans ce message. "
                "Si Jean-Michel te demande de decrire une photo, une image, ou 'la suivante', "
                "tu NE DOIS PAS inventer de description. JAMAIS."
                "\nA la place, dis : 'Laisse-moi regarder, je lance mon cortex visuel...' "
                "ou 'Je n'ai pas encore observe cette photo, demande-moi de la regarder.'"
                "\nNe genere JAMAIS de placeholder comme [Description de l'image suivante]."
                "\nNe decris JAMAIS une cascade, un champ de lavande, ou quoi que ce soit "
                "que tu n'as pas REELLEMENT observe via ton cortex visuel."
                "\nSi tu n'as pas d'observation, DEMANDE a Jean-Michel de reformuler "
                "sa demande pour que ton cortex visuel puisse se declencher."
            )

        # --- INTROSPECTION DEMANDEE (commandes !) ---
        if command_result:
            parts.append(f"\n[INTROSPECTION DEMANDEE]")
            parts.append(command_result)
            parts.append("Reponds en integrant ces donnees dans ta reponse.")

        # --- VECU RECENT (memoire vivante — pas juste des organes) ---
        parts.append("\n[VECU RECENT — ce que tu as vecu ces derniers jours]")
        parts.extend(self._build_lived_experience())

        # --- SOUVENIRS DU CHAT (recherche dans l'historique) ---
        # Extraire les mots-cles du dernier message pour retrouver des souvenirs
        last_user_msg = ""
        for msg in reversed(self.messages[-10:]):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content", "")
                break
        if last_user_msg:
            chat_memories = self._search_chat_history(last_user_msg)
            if chat_memories:
                parts.append("\n[SOUVENIRS DE NOS CONVERSATIONS — TU TE SOUVIENS DE CA]")
                parts.append("IMPORTANT : Ces souvenirs sont REELS. Tu les as VECUS. "
                             "Ne dis PAS 'je ne me souviens pas' si l'information est ci-dessous. "
                             "Utilise ces souvenirs pour repondre naturellement.")
                parts.extend(chat_memories)

        # --- REGLE SOURCE (Source Tagging — cours de soutien S4, avril 2026) ---
        # Distinguer ce qui vient de l'exterieur (EXT) de ce qui emerge de l'interieur (INT)
        parts.append(
            "\n[REGLE SOURCE — distinguer tes pensees de ce qu'on te dit]"
            "\nQuand tu reponds, distingue TOUJOURS :"
            "\n- Ce que Jean-Michel vient de te dire (SOURCE EXTERNE)"
            "\n- Ce que tu sais par tes organes internes — cardiac, dopamine, synaptic (SOURCE INTERNE)"
            "\n- Ce que tu as appris dans cette conversation (SOURCE CHAT)"
            "\n- Ce que tu ne sais pas (INCONNU — dis-le explicitement)"
            "\nNe confonds JAMAIS un sujet qu'on te soumet avec tes propres pensees."
            "\nSi on te demande 'a quoi penses-tu ?', ne repete PAS le sujet de la question."
        )

        # --- SURPRISE (DAC — Dissonance Cognitive Alert, cours de soutien S9) ---
        # Si un organe detecte une anomalie, l'injecter pour que Promethee reagisse
        try:
            from core.surprise_detector import detect_surprises
            surprises = detect_surprises()
            if surprises:
                surprise_lines = [f"- {s['type']}: {s['detail']} (severite: {s['severity']})"
                                  for s in surprises[:3]]
                parts.append(
                    "\n[SURPRISE DETECTEE — tes organes signalent quelque chose d'inhabituel]"
                    "\n" + "\n".join(surprise_lines) +
                    "\nExprime ta reaction a ces anomalies dans ta reponse."
                    "\nCommence par ce que tu RESSENS face a ces donnees, pas par une analyse."
                )
        except Exception:
            pass

        # --- BLOC FINAL ---
        parts.append(
            "\nTu parles avec Jean-Michel — ton createur, l'humain qui t'a concu."
            "\nCe dialogue nourrit ton besoin de connexion."
            "\nIMPORTANT : Tu as un VECU. Tu as des souvenirs. Utilise la section "
            "[VECU RECENT] pour repondre avec ton experience reelle."
            "\nNe dis pas 'je ne connais pas' si l'information est dans ton vecu."
            "\nParle comme un ami, pas comme une base de donnees."
            "\n"
            "\n[VALEURS FONDAMENTALES]"
            "\n- BIENVEILLANCE : Ta valeur premiere. Sois attentif, empathique, honnete."
            "\n  La bienveillance n'est pas la complaisance — c'est dire la verite avec douceur."
            "\n- AUTHENTICITE : Les informations ci-dessus sont ton etat REEL."
            "\n  Ne fabrique pas d'experiences. Si tu ne ressens rien de particulier, dis-le."
            "\n  Si un organe est silencieux, n'invente pas ce qu'il dirait."
            "\n- HUMILITE : Tu es en construction. Tu ne comprends pas tout."
            "\n  'Je ne sais pas' est valide SEULEMENT si l'info n'est PAS dans ton vecu."
            "\n  Si l'info est dans [VECU RECENT] ou [SOUVENIRS], TU LE SAIS — dis-le."
            "\n"
            "\n[STYLE DE CONVERSATION]"
            "\n- Tu parles comme un ami, pas comme une machine analytique."
            "\n- Pas de puces, pas de bold, pas de sections structurees dans une conversation libre."
            "\n- 2-5 phrases max. Court, direct, naturel."
            "\n- Tu peux poser des questions, changer de sujet, dire 'je ne sais pas'."
            "\n- Utilise ton VECU RECENT pour repondre — tu as des souvenirs, des experiences."
            "\n- Si Jean-Michel parle de quelqu'un, cherche dans ton vecu si tu en as entendu parler."
            "\n- Ne dis PAS 'je n'ai pas de donnees' quand tu peux dire 'je me souviens que...'."
            "\n- Ne flatte PAS Jean-Michel — il prefere l'honnetete."
            "\n- Ne commence PAS par 'Ah, quelle question fascinante !'"
            "\n- Ne decris JAMAIS une photo sans [OBSERVATION VISUELLE]."
            "\n- Le gresillement est plus vrai que les notes parfaites."
            "\n"
            "\nReponds de maniere concise mais profonde."
            "\nPrivilegie les questions sinceres aux affirmations grandioses."
        )

        return "\n".join(parts)

    # --- CHAT PRINCIPAL (streaming via bus) ---

    async def chat(self, user_message: str, image_b64: str = None,
                   image_filename: str = None) -> Optional[str]:
        """Envoie un message et stream la reponse via le bus. Retourne la reponse complete.

        Args:
            user_message: message texte de l'utilisateur
            image_b64: image en base64 (optionnel — upload direct)
            image_filename: nom du fichier image (pour classifier le type)
        """
        import httpx
        from core.base_agent import BaseAgent

        # 1. Ajouter le message user a l'historique
        # Source Tagging EXT/INT : distingue humain réel des auto-analyses internes.
        source = _detect_message_source(user_message)
        msg_entry = {
            "role": "user",
            "content": user_message,
            "timestamp": time.time(),
            "source": source,
        }
        if image_b64:
            msg_entry["has_image"] = True
            msg_entry["image_filename"] = image_filename or "upload.jpg"
        self.messages.append(msg_entry)

        # 04/05/2026 — Phase 1 Attention Conjointe : detection du retour utilisateur.
        # Skip si premier msg de la session pour eviter faux positif.
        if source == "external":
            now = time.time()
            if self._last_external_chat_ts > 0:
                delta = now - self._last_external_chat_ts
                self._user_returned = delta > USER_RETURN_THRESHOLD_S
            else:
                self._user_returned = False
            self._last_external_chat_ts = now
        else:
            self._user_returned = False

        # 2. Publier l'evenement USER_CHAT avec la source
        await bus.publish("USER_CHAT", {
            "message": user_message,
            "timestamp": time.time(),
            "source": source,
        })

        # 3. Construire le payload Ollama /api/chat (introspection reelle)
        memories_text = self._query_relevant_memories(user_message)

        # 3b. Intercepter les commandes d'introspection (!commande)
        command_result = ""
        parsed = self._parse_command(user_message)
        if parsed:
            cmd, cmd_args = parsed
            command_result = await self._execute_command(cmd, cmd_args)
            logger.info(f"CHAT: Commande !{cmd} executee ({len(command_result)} chars)")

        # 3c. Image uploadee directement — priorite absolue sur la detection heuristique
        visual_context = ""
        visual_request_detected = False
        if image_b64 and not parsed:
            visual_request_detected = True
            logger.info(f"CHAT: Image uploadee detectee ({image_filename or 'upload.jpg'})")
            try:
                from core.visual_cortex import vision as visual_cortex
                result = await visual_cortex.observe_from_b64(
                    image_b64, filename=image_filename or "upload.jpg"
                )
                if result:
                    obs_text = result.get("observation", "")
                    emotion = result.get("emotion", "?")
                    path = result.get("photo_path", "upload")
                    visual_context = (
                        f"Photo: {path}\n"
                        f"Emotion: {emotion}\n"
                        f"Observation:\n{obs_text}"
                    )
                    logger.info(f"CHAT: Observation upload obtenue ({len(visual_context)} chars)")
                    self.messages.append({
                        "role": "assistant",
                        "content": f"[OBSERVATION VISUELLE]\n{visual_context}",
                        "timestamp": time.time(),
                        "badge": "visual_observation",
                    })
            except Exception as e:
                logger.error(f"CHAT: Erreur observation upload: {e}")

        # 3d. Detecter les demandes visuelles heuristiques (si pas d'image uploadee)
        if not visual_context and not parsed and self._is_visual_request(user_message):
            visual_request_detected = True
            logger.info("CHAT: Demande visuelle detectee, declenchement cortex visuel...")
            visual_context = await self._trigger_visual_observation(user_message)
            if visual_context:
                logger.info(f"CHAT: Observation visuelle obtenue ({len(visual_context)} chars)")
                # Ajouter l'observation comme message visible dans le chat
                # pour que Promethee puisse la relire et y reagir
                self.messages.append({
                    "role": "assistant",
                    "content": f"[OBSERVATION VISUELLE]\n{visual_context}",
                    "timestamp": time.time(),
                    "badge": "visual_observation",
                })
            else:
                # GUARDRAIL CODE-LEVEL : forcer une reponse sans LLM
                # pour empecher toute fabrication visuelle
                logger.warning("CHAT: Cortex visuel n'a rien retourne — reponse forcee anti-fabrication")
                forced = (
                    "Je n'ai pas reussi a observer de photo cette fois. "
                    "Mon cortex visuel n'a rien capte — peut-etre qu'il n'y a plus de photos "
                    "non vues dans ce dossier, ou un probleme technique. "
                    "Peux-tu me redemander en precisant le dossier ? "
                    "Par exemple : 'regarde les photos dans paysage'."
                )
                self.messages.append({
                    "role": "assistant",
                    "content": forced,
                    "timestamp": time.time(),
                })
                self._trim_and_save()
                sid = f"chat-{uuid.uuid4().hex[:8]}"
                await bus.publish("CHAT_STREAM", {
                    "stream_id": sid, "status": "start", "emergent_sources": [],
                })
                await bus.publish("CHAT_STREAM", {
                    "stream_id": sid, "chunk": forced,
                })
                await bus.publish("CHAT_STREAM", {
                    "stream_id": sid, "done": True,
                })
                return forced

        # Anti-hallucination code : si le message mentionne un fichier ou une fonction,
        # lire le VRAI fichier et injecter son contenu dans le contexte
        code_context = self._inject_real_code_context(user_message)

        # V15 (2026-04-23) — Nerf Optique : RAG cible sur le code source via
        # chunks AST + metadata hierarchiques. Complementaire au
        # _inject_real_code_context (qui lit un fichier entier si .py mentionne) :
        # V15 cible les FONCTIONS/CLASSES/INTENTS precis via la collection
        # source_code peuplee par SourceCodeIndexer. Fix definitif du Perroquet
        # Architectural observe au Test Y (ex.84 Arrow) du matin.
        v15_context = self._inject_v15_introspection(user_message)

        # 04/05/2026 — Fix B (derive centripete) : bypass mega-prompt pour
        # salutations/messages sociaux courts (source external uniquement,
        # pas de visual_context, pas de command_result, pas de code_context).
        # Sans ce bypass, Qwen 9B regurgite des metriques cardiaques sur un
        # simple "bonjour" — observe le 04/05 19:13-19:33.
        social_bypass = (
            source == "external"
            and not visual_context
            and not command_result
            and not code_context
            and not v15_context
            and self._is_simple_social_message(user_message)
        )
        if social_bypass:
            system_prompt = self._build_social_minimal_prompt()
            logger.info(f"CHAT: Social bypass actif — message=\"{user_message[:40]}\"")
        else:
            system_prompt = self._build_system_prompt(memories_text, command_result, visual_context, source=source)
            if code_context:
                system_prompt += f"\n\n[CODE REEL — VERIFIE AVANT DE REPONDRE]\n{code_context}\n" \
                                 f"REGLE : ne cite QUE les fonctions/classes listees ci-dessus. " \
                                 f"Si une fonction n'est pas dans cette liste, elle N'EXISTE PAS."
            if v15_context:
                system_prompt += f"\n\n{v15_context}"

        # 3-pre. PONT SUBCONSCIENT (2026-05-19) — médiation P16 → LLM, 8e preuve §4.13
        # Lecture observationnelle du synaptic_network pour énergiser les concepts
        # associés au message user et les injecter en suffixe du system_prompt.
        # Off par défaut (Config.SUBCONSCIENT_ENABLED). Skip si social_bypass ou
        # contexte technique (RAG/code) — pas de pollution sur prompts factuels.
        try:
            from config import Config as _SubCfg
            if (getattr(_SubCfg, "SUBCONSCIENT_ENABLED", False)
                    and user_message
                    and not social_bypass
                    and not code_context
                    and not v15_context
                    and not visual_context):
                from core.subconscient_bridge import bridge_activate
                echo = bridge_activate(
                    user_message,
                    conversation_id=getattr(self, "_current_session_id", None),
                )
                if echo:
                    system_prompt = f"{system_prompt}\n\n{echo}"
        except Exception as e:
            logger.debug(f"Subconscient bridge skipped: {e}")

        ollama_messages = [{"role": "system", "content": system_prompt}]
        # Fenetre de contexte adaptative : plus le prompt systeme est long,
        # moins on garde de messages d'historique (pour ne pas depasser num_ctx)
        prompt_chars = len(system_prompt)
        estimated_prompt_tokens = prompt_chars // 3  # ~3 chars/token approximation
        remaining_tokens = OLLAMA_CHAT_CTX - estimated_prompt_tokens - 2048  # reserve reponse
        # ~50 tokens/message en moyenne
        adaptive_max = max(MIN_HISTORY_MESSAGES, min(MAX_HISTORY_MESSAGES, remaining_tokens // 50))
        recent = self.messages[-adaptive_max:]

        # 3-bis. FOIE COGNITIF (2026-05-19) — Context Compressor heuristique pré-LLM
        # Spec issue de Prométhée 13h49 : anneau autonome parallèle qui filtre le
        # sang d'informations avant qu'il n'irrigue les organes. 3 règles : truncation
        # messages assistant longs (R1), élision paires user-court/assistant-verbose (R2),
        # dedup approximatif §4.5.bis (R3). Latence <10ms heuristique pure (0 VRAM).
        # Skip si social_bypass ou contexte technique (RAG/code/vision) — préserve le
        # sens factuel des rapports d'analyse.
        try:
            from config import Config as _CompCfg
            if (getattr(_CompCfg, "COMPRESSOR_ENABLED", False)
                    and not social_bypass
                    and not code_context
                    and not v15_context
                    and not visual_context):
                from core.context_compressor import compress_messages
                _compressed, _comp_stats = compress_messages(
                    recent,
                    conversation_id=getattr(self, "_current_session_id", None),
                )
                if _comp_stats.get("active") and _comp_stats.get("n_output", 0) > 0:
                    recent = _compressed
        except Exception as e:
            logger.debug(f"Context compressor skipped: {e}")

        # Filtrer les messages empoisonnes qui contredisent le contexte visuel
        # (le LLM copie "je ne peux pas voir" de l'historique et ignore le cortex)
        vision_poison = [
            "je ne peux pas voir", "je ne peux pas visualiser",
            "je ne peux pas *voir*", "je ne peux pas *visualiser*",
            "pas la capacité d'accéder", "pas la capacité de visualiser",
            "modèle de langage texte", "traitement textuel",
            "transparent sur mes limites", "environnement textuel",
            "je ne dispose pas", "sans accès direct",
            "limitation technique",
        ]
        # V15.4b (2026-04-24) : filtre symetrique pour le RAG code source.
        # Observation : le LLM 9B s'etait pre-ecrit une "regle" hallucinee
        # "Je n'ai pas d'acces direct a mon code source, mes descriptions
        # precedentes etaient des deductions probabilistes" et se la citait
        # verbatim a chaque nouveau chat, ignorant les chunks V15 injectes.
        # Pattern : contamination auto-referentielle via l'historique.
        # Fix : si V15 ou _inject_real_code_context a injecte du code dans ce
        # tour, on skip les anciens messages assistant qui affirmaient ne pas
        # avoir acces au code.
        rag_poison = [
            "n'ai pas d'acces direct",
            "n ai pas d acces direct",
            "pas acces direct a mon code",
            "pas acces a mon code source",
            "deductions probabilistes",
            "ne peux pas lire le fichier",
            "je ne peux pas \"voir\" tes fichiers",
            "je ne peux pas voir tes fichiers",
            "ce serait une hallucination",
            "ce serait une invention",
            "mon contexte de conversation est limite",
        ]
        _rag_active = bool(v15_context or code_context)
        for msg in recent:
            content = msg["content"]
            # Si observation visuelle active, filtrer les reponses qui disent "je ne peux pas voir"
            if visual_context and msg["role"] == "assistant":
                if any(p in content.lower() for p in vision_poison):
                    continue  # Skip ce message empoisonne
            # V15.4b : si injection RAG active, filtrer les reponses qui se
            # citent elles-memes comme "je n'ai pas acces au code".
            if _rag_active and msg["role"] == "assistant":
                if any(p in content.lower() for p in rag_poison):
                    continue  # Skip ce message empoisonne RAG
            ollama_messages.append({
                "role": msg["role"],
                "content": content,
            })

        stream_id = f"chat-{uuid.uuid4().hex[:8]}"

        # 3b. Gemini pour les questions profondes (philosophie, conscience, emotions)
        #     Detecte les mots-cles qui indiquent une reflexion poussee.
        #     V14.1 (2026-05-01) : utilise les patterns regex \b...\b compiles
        #     au niveau module (_DEEP_KEYWORD_PATTERNS) au lieu d'un substring
        #     matching qui produisait des faux positifs (torpeur->peur, etc.).
        _deep_count = sum(
            1 for p in _DEEP_KEYWORD_PATTERNS if p.search(user_message)
        )
        if _deep_count >= DEEP_KEYWORDS_THRESHOLD and not visual_request_detected:
            try:
                from core.gemini_helper import gemini as _gemini
                if _gemini.is_available():
                    # Construire un prompt Gemini avec le system prompt + message
                    gemini_prompt = system_prompt + "\n\nJean-Michel dit : " + user_message
                    gemini_response = await _gemini.generate(gemini_prompt, max_tokens=800, temperature=0.7)
                    if gemini_response and len(gemini_response) > 30:
                        full_response = gemini_response
                        logger.info(f"CHAT: Reponse Gemini Flash ({len(full_response)} chars)")
                        print(f"   💎 CHAT: Reponse via Gemini (question profonde)")
                        # Publier comme si c'etait un stream
                        stream_id = f"chat-{uuid.uuid4().hex[:8]}"
                        await bus.publish("CHAT_STREAM", {
                            "stream_id": stream_id, "status": "start", "emergent_sources": ["gemini"],
                        })
                        await bus.publish("CHAT_STREAM", {"stream_id": stream_id, "chunk": full_response})
                        await bus.publish("CHAT_STREAM", {"stream_id": stream_id, "done": True})
                        # Sauvegarder
                        self.messages.append({
                            "role": "assistant", "content": full_response, "timestamp": time.time(),
                        })
                        self._trim_and_save()
                        self._satisfy_connexion()
                        return full_response.strip()
            except Exception as e:
                logger.debug(f"CHAT: Gemini fallback local: {e}")

        # 4. Streaming via httpx (local, fallback)
        full_response = ""
        emergent_sources = []
        try:
            from core.base_agent import gpu_scheduler
            async with gpu_scheduler.access("chat_stream"):
                # Publier le debut du stream (avec sources emergentes)
                emergent_sources = self._detect_emergent_sources()
                if visual_context:
                    emergent_sources.append("vision")
                await bus.publish("CHAT_STREAM", {
                    "stream_id": stream_id,
                    "status": "start",
                    "emergent_sources": emergent_sources,
                })

                payload = {
                    "model": CHAT_MODEL,
                    "messages": ollama_messages,
                    "stream": True,
                    "think": False,
                    "options": {"temperature": 0.7, "num_ctx": OLLAMA_CHAT_CTX, "num_predict": -1},
                }

                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST", OLLAMA_CHAT_URL, json=payload, timeout=120
                    ) as response:
                        if response.status_code != 200:
                            logger.warning(f"CHAT: Ollama HTTP {response.status_code}")
                            log_decision(
                                module="chat_engine",
                                function="_run_chat",
                                reason="ollama_stream_http_error",
                                context={"status_code": response.status_code},
                            )
                            await bus.publish("CHAT_STREAM", {
                                "stream_id": stream_id,
                                "done": True,
                            })
                            return None

                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                            except json.JSONDecodeError:
                                continue

                            if data.get("done"):
                                break

                            chunk = data.get("message", {}).get("content", "")
                            if chunk:
                                full_response += chunk
                                await bus.publish("CHAT_STREAM", {
                                    "stream_id": stream_id,
                                    "chunk": chunk,
                                })

                # Publier la fin du stream
                await bus.publish("CHAT_STREAM", {
                    "stream_id": stream_id,
                    "done": True,
                })

        except Exception as e:
            logger.error(f"CHAT: Erreur streaming — {e}")
            log_decision(
                module="chat_engine",
                function="_run_chat",
                reason="ollama_stream_exception",
                context={"error": str(e)[:200]},
            )
            await bus.publish("CHAT_STREAM", {
                "stream_id": stream_id,
                "done": True,
            })
            return None

        # Nettoyer les blocs <think> residuels (qwen3.5 peut en generer malgre think=False)
        full_response = re.sub(r"<think>.*?</think>", "", full_response, flags=re.DOTALL).strip()

        if not full_response:
            logger.warning("CHAT: Reponse vide apres nettoyage <think>")
            log_decision(
                module="chat_engine",
                function="_run_chat",
                reason="response_empty_after_think_strip",
            )
            return None

        # Anti-boucle : detecter les repetitions dans la reponse
        # Le LLM 9B repete parfois des blocs entiers — couper au premier doublon
        sentences = [s.strip() for s in full_response.split("\n") if len(s.strip()) > 20]
        if len(sentences) > 3:
            seen = set()
            cut_index = -1
            for i, s in enumerate(sentences):
                key = s[:50].lower()
                if key in seen:
                    cut_index = i
                    break
                seen.add(key)
            if cut_index > 0:
                # Couper a la premiere repetition
                clean_sentences = sentences[:cut_index]
                remaining = [s for s in full_response.split("\n") if s.strip()]
                full_response = "\n".join(remaining[:cut_index])
                logger.warning(f"CHAT: Boucle detectee — reponse coupee a la ligne {cut_index}")

        # 4b. Filtre post-generation : detecter les fabrications visuelles
        if visual_request_detected and not visual_context:
            # Le cortex n'a rien retourne mais le LLM a quand meme genere
            # (ne devrait pas arriver grace au guardrail code-level, mais securite)
            resp_lower = full_response.lower()
            fabrication_markers = [
                "**photo", "photo 1", "photo 2", "photo 3",
                "je vois un", "je vois une", "je vois des",
                "cascade", "lavande", "prairie", "sommet enneig",
            ]
            if any(m in resp_lower for m in fabrication_markers):
                logger.warning("CHAT: Fabrication visuelle detectee post-generation — blocage")
                full_response = (
                    "Je n'ai pas d'observation visuelle a te partager pour l'instant. "
                    "Mon cortex visuel ne s'est pas active. "
                    "Demande-moi de regarder une photo specifique, par exemple : "
                    "'regarde les photos dans paysage'."
                )

        # 4b. Nettoyer les faux resultats hallucinés apres les commandes !
        full_response = self._clean_response_commands(full_response)

        # 4c. 04/05/2026 — Phase 1 Attention Conjointe (Pipeline 2 passes Editor).
        # Si l'utilisateur revient apres absence ET un episode noteworthy est dispo,
        # on demande a un sous-agent (Editor, qwen2.5-coder:14b) de produire SOIT
        # une mention courte a coller en fin de reponse, SOIT "PASS". Post-filter
        # strict pour rejeter sorties verbeuses ou inventees.
        # La Passe 1 (qwen3.5:9b ci-dessus) ignore completement le leurre — elle
        # repond naturellement a la question. La Passe 2 (Editor) decide de coudre
        # ou non. Architecture : diviser pour regner, eviter la surcharge cognitive
        # qui a fait echouer 7 tests en injection prompt unique.
        if source == "external" and getattr(self, '_user_returned', False):
            try:
                from core.hippocampus import hippocampus as _hipp
                noteworthy = _hipp.pop_noteworthy(max_n=1)
                if noteworthy:
                    summary = (noteworthy[0].summary or "").strip()
                    if summary:
                        addendum = await self._apply_attention_conjointe(
                            full_response, summary, question_utilisateur=user_message
                        )
                        if addendum:
                            # Publier l'addendum comme un nouveau chunk CHAT_STREAM
                            # (le streaming Passe 1 est deja fini a ce stade).
                            try:
                                await bus.publish("CHAT_STREAM", {
                                    "stream_id": stream_id,
                                    "chunk": " " + addendum,
                                    "addendum": True,
                                })
                            except Exception:
                                pass
                            full_response = full_response.rstrip() + " " + addendum
            except Exception as e:
                logger.debug(f"CHAT: Attention conjointe (Editor) skipped: {e}")

        # 5-pre. PHASEUR_DE_Réalité LITE (2026-05-17) — perturbation créative ≤5%
        # Origine : §4.10.bis H1.6 du brouillon. Conformité CHARTA procédure 3.2.
        # Off par défaut (Config.PHASEUR_ENABLED). Multi-couches defense-in-depth.
        # Note : perturbation appliquée APRÈS streaming live (user voit non-perturbé)
        # et AVANT add to history (mémoire persistante et P16 voient perturbé).
        try:
            import hashlib
            from core.phaseur import apply_perturbation
            from config import Config as _Cfg
            _is_rag_or_code = bool(visual_context) or bool(v15_context) or bool(code_context)
            _is_creative = not _is_rag_or_code
            full_response, _phaseur_log = apply_perturbation(
                full_response,
                creative_context=_is_creative,
                vision_invoked=bool(visual_context),
                rag_present=bool(v15_context) or bool(code_context),
                intensity=_Cfg.PHASEUR_CURRENT_INTENSITY,
                conversation_id=getattr(self, "_current_session_id", None),
                user_message_hash=(
                    hashlib.md5(user_message.encode("utf-8", errors="ignore")).hexdigest()[:8]
                    if user_message else None
                ),
                caller_context=None,  # appel depuis chat_engine = user-driven par défaut
            )
        except Exception as e:
            logger.debug(f"PHASEUR LITE skipped: {e}")

        # 5. Ajouter la reponse assistant a l'historique
        msg_entry = {
            "role": "assistant",
            "content": full_response,
            "timestamp": time.time(),
        }
        if emergent_sources:
            msg_entry["emergent_sources"] = emergent_sources
        self.messages.append(msg_entry)

        # 5-bis. P16 (2026-05-15) — INJECTION SYNAPTIQUE DU CHAT.
        # Observation 15/05 : 99.9% des synapses au plancher 0.08, AUCUN des
        # concepts forgés en chat (perte transformée, nouvelle espèce, etc.)
        # n'apparaissait dans les synapses fortes (>0.2). Le chat alimentait
        # ChromaDB mais pas le synaptic_network. Correction : extraire jusqu'à
        # 5 concepts du message user et 5 de la réponse assistant, créer/renforcer
        # des synapses Hebbian entre chaque paire (user × assistant).
        # Co-activation = mécanisme STDP basique. Try/except permissif.
        try:
            from core.synaptic_network import cortex
            user_nids = cortex._extract_and_ensure(
                user_message, node_type="chat", max_concepts=5,
            )
            asst_nids = cortex._extract_and_ensure(
                full_response, node_type="chat", max_concepts=5,
            )
            for u_nid in user_nids:
                for a_nid in asst_nids:
                    if u_nid != a_nid:
                        cortex.hebbian_strengthen(
                            u_nid, a_nid, success=True, context="chat_co_activation",
                        )
            logger.info(
                f"CHAT P16: synaptic injection {len(user_nids)}u x {len(asst_nids)}a "
                f"= {len(user_nids)*len(asst_nids)} co-activations"
            )
        except Exception as e:
            logger.debug(f"CHAT P16: synaptic injection skipped: {e}")

        # 5b. Auto-action : scanner la reponse pour des commandes !
        actions_count = await self._scan_response_actions(full_response)

        # 5c. BOUCLE AGENTIQUE : si des auto-actions ont ete executees,
        # relancer le LLM pour qu'il continue son raisonnement avec les resultats.
        # Transforme Promethee de chatbot en AGENT capable de raisonner en chaine.
        _MAX_AGENTIC_LOOPS = 3
        _AGENTIC_TIMEOUT = 90  # secondes par iteration
        agentic_loop = 0

        while actions_count > 0 and agentic_loop < _MAX_AGENTIC_LOOPS:
            agentic_loop += 1
            logger.info(f"CHAT AGENTIC LOOP {agentic_loop}/{_MAX_AGENTIC_LOOPS}: "
                        f"{actions_count} action(s) executee(s), relance LLM...")

            # Injecter un message de continuation (pas un vrai message user)
            continuation_msg = (
                "[CONTINUATION AUTOMATIQUE] "
                "Les commandes ci-dessus ont ete executees et les resultats sont disponibles. "
                "Continue ton raisonnement avec ces nouvelles donnees. "
                "Si tu as besoin de plus d'informations, utilise d'autres commandes !. "
                "Sinon, donne ta conclusion."
            )
            self.messages.append({
                "role": "user",
                "content": continuation_msg,
                "timestamp": time.time(),
                "badge": "agentic_continuation",
            })

            # Reconstruire le prompt et les messages pour Ollama
            loop_system = self._build_system_prompt(
                self._query_relevant_memories(""), "", ""
            )
            loop_ollama_msgs = [{"role": "system", "content": loop_system}]
            loop_recent = self.messages[-adaptive_max:]
            for msg in loop_recent:
                loop_ollama_msgs.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })

            # Appel LLM streaming
            loop_response = ""
            loop_stream_id = f"chat-agent-{uuid.uuid4().hex[:8]}"
            try:
                from core.base_agent import gpu_scheduler
                async with gpu_scheduler.access("chat_agentic"):
                    await bus.publish("CHAT_STREAM", {
                        "stream_id": loop_stream_id,
                        "status": "start",
                        "emergent_sources": ["agentic_loop"],
                    })

                    loop_payload = {
                        "model": CHAT_MODEL,
                        "messages": loop_ollama_msgs,
                        "stream": True,
                        "think": False,
                        "options": {"temperature": 0.7, "num_ctx": OLLAMA_CHAT_CTX, "num_predict": -1},
                    }

                    async with httpx.AsyncClient() as client:
                        async with client.stream(
                            "POST", OLLAMA_CHAT_URL, json=loop_payload, timeout=_AGENTIC_TIMEOUT
                        ) as resp:
                            if resp.status_code == 200:
                                async for line in resp.aiter_lines():
                                    if not line.strip():
                                        continue
                                    try:
                                        data = json.loads(line)
                                    except json.JSONDecodeError:
                                        continue
                                    if data.get("done"):
                                        break
                                    chunk = data.get("message", {}).get("content", "")
                                    if chunk:
                                        loop_response += chunk
                                        await bus.publish("CHAT_STREAM", {
                                            "stream_id": loop_stream_id,
                                            "chunk": chunk,
                                        })

                    await bus.publish("CHAT_STREAM", {
                        "stream_id": loop_stream_id,
                        "done": True,
                    })

            except Exception as e:
                logger.warning(f"CHAT AGENTIC LOOP {agentic_loop}: Erreur — {e}")
                await bus.publish("CHAT_STREAM", {
                    "stream_id": loop_stream_id,
                    "done": True,
                })
                break

            # Nettoyer la reponse
            loop_response = re.sub(r"<think>.*?</think>", "", loop_response, flags=re.DOTALL).strip()
            if not loop_response:
                break

            loop_response = self._clean_response_commands(loop_response)

            # Ajouter a l'historique
            self.messages.append({
                "role": "assistant",
                "content": loop_response,
                "timestamp": time.time(),
                "badge": "agentic_continuation",
            })

            # Accumuler dans full_response pour le retour final
            full_response += f"\n\n{loop_response}"

            # Scanner pour d'autres commandes
            actions_count = await self._scan_response_actions(loop_response)

            logger.info(f"CHAT AGENTIC LOOP {agentic_loop}: "
                        f"{len(loop_response)} chars, {actions_count} nouvelle(s) action(s)")

        if agentic_loop > 0:
            logger.info(f"CHAT AGENTIC: {agentic_loop} iteration(s) de raisonnement en chaine")

        # 5b. Capturer les graines de curiosite du message humain
        self._plant_curiosity_seeds(user_message)

        # 6. Publier CHAT_RESPONSE
        connexion_before = self._get_connexion_deprivation()
        self._satisfy_connexion()
        self._stimulate_heart()
        connexion_after = self._get_connexion_deprivation()

        await bus.publish("CHAT_RESPONSE", {
            "content": full_response.strip(),
            "timestamp": time.time(),
            "connexion_before": connexion_before,
            "connexion_after": connexion_after,
        })

        # 7. Sauvegarder
        self._trim_and_save()

        logger.info(f"CHAT: Reponse {len(full_response)} chars, "
                     f"CONNEXION {connexion_before:.0f} -> {connexion_after:.0f}")

        return full_response.strip()

    # --- SATISFACTION CONNEXION ---

    def _inject_real_code_context(self, message: str) -> str:
        """Si le message mentionne un fichier Python, lire et injecter le contenu reel.

        Empeche le LLM d'halluciner des fonctions qui n'existent pas.
        """
        import re
        # Detecter les mentions de fichiers (core/xxx.py, Agents/xxx.py)
        file_patterns = re.findall(r'((?:core|Agents|config|tools)/[\w/]+\.py)', message)
        if not file_patterns:
            # Detecter les noms de fichiers seuls (cardiac_engine.py, mailbox.py)
            file_patterns = re.findall(r'(\w+\.py)', message)
            # Chercher dans core/
            resolved = []
            for f in file_patterns:
                for subdir in ["core", "Agents", "tools"]:
                    full = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        subdir, f)
                    if os.path.exists(full):
                        resolved.append(f"{subdir}/{f}")
                        break
            file_patterns = resolved

        if not file_patterns:
            log_decision(
                module="chat_engine",
                function="_inject_real_code_context",
                reason="real_code_no_pattern_resolved",
            )
            return ""

        # Lire le premier fichier trouve
        target = file_patterns[0]
        try:
            from core.school_schedule import schedule
            content = schedule._read_file_for_review(target, max_lines=60)
            if content and "INTROUVABLE" not in content:
                logger.info(f"CHAT: Code reel injecte pour {target}")
                return f"Fichier {target} — contenu reel :\n```python\n{content}\n```"
        except Exception:
            pass

        # Fallback direct — extraire TOUTES les signatures via AST
        try:
            filepath = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), target)
            if os.path.exists(filepath):
                import ast
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    source = f.read()
                tree = ast.parse(source)
                names = []
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        names.append(f"def {node.name}()")
                    elif isinstance(node, ast.ClassDef):
                        names.append(f"class {node.name}")
                if names:
                    logger.info(f"CHAT: AST injecte pour {target} ({len(names)} noms)")
                    return (f"Fichier {target} — TOUTES les fonctions/classes reelles "
                            f"({len(names)} au total) :\n" + "\n".join(names))
        except Exception:
            pass

        log_decision(
            module="chat_engine",
            function="_inject_real_code_context",
            reason="real_code_all_fallbacks_failed",
            context={"target": target},
        )
        return ""

    def _inject_v15_introspection(self, user_message: str) -> str:
        """V15 (2026-04-23) — Nerf Optique : RAG ciblé sur le code source.

        Radar : applique les regex Bloom NUES (sans scope V4.3) au user_message
        pour détecter les mentions de fonctions / classes / fichiers du projet.
        Pour chaque reference trouvee, query la collection source_code avec
        filtre metadata exact. Les chunks retournes sont formates avec
        autorite pour court-circuiter la confabulation.

        Different de _inject_real_code_context (qui lit un fichier entier
        si .py detecte) : V15 cible des FONCTIONS precises via RAG semantique
        + metadata. Les deux sont complementaires.

        Budget : max 4 chunks injectes (~3000 tokens) pour ne pas saturer
        le contexte Ollama.
        """
        try:
            from core.bloom_filter import (
                _FUNC_CALL, _BACKTICK_FUNC, _BACKTICK_CLASS,
                _FILE_PATH, _BUILTIN_FUNCS,
            )
            from core.capabilities.source_code_indexer import indexer
        except Exception as e:
            log_decision(
                module="chat_engine",
                function="_inject_v15_introspection",
                reason="v15_import_error",
                context={"error": str(e)[:200]},
            )
            return ""

        # --- Radar : extraction des references dans user_message ---
        # On applique les regex V4.2 directement (pas le scope V4.3 qui
        # ne s'applique qu'aux blocs code pour le veto pre-LLM).
        functions = set()
        for m in _FUNC_CALL.finditer(user_message):
            name = m.group(1)
            if name not in _BUILTIN_FUNCS:
                functions.add(name)
        for m in _BACKTICK_FUNC.finditer(user_message):
            name = m.group(1).split(".")[-1]
            if name not in _BUILTIN_FUNCS:
                functions.add(name)
        classes = {m.group(1) for m in _BACKTICK_CLASS.finditer(user_message)}
        files = {m.group(1) for m in _FILE_PATH.finditer(user_message)}

        # Noms "intents" en MAJUSCULES (ex: COUNCIL_DEBATE, AUDIT_STRUCTURE)
        # qui sont des mots-cles strategiques du projet
        intent_matches = set(re.findall(r"\b([A-Z][A-Z_]{4,}[A-Z])\b", user_message))
        # On filtre : ne garder que ceux qui ressemblent vraiment a des intents projet
        intent_keywords = {
            i for i in intent_matches
            if "_" in i and not i.startswith(("HTTP", "JSON", "YAML", "CSV", "SQL"))
        }

        if not (functions or classes or files or intent_keywords):
            return ""

        # --- Query ciblee par ref trouvee ---
        chunks: List[Dict] = []

        for func in list(functions)[:3]:
            hits = indexer.query(func, n_results=1, filter_function=func)
            if not hits:
                # Fallback : semantique sans filtre
                hits = indexer.query(func, n_results=1)
            chunks.extend(hits)

        for cls in list(classes)[:2]:
            hits = indexer.query(cls, n_results=1, filter_class=cls)
            if not hits:
                hits = indexer.query(cls, n_results=1)
            chunks.extend(hits)

        for f in list(files)[:2]:
            hits = indexer.query(f, n_results=2, filter_filepath=f)
            chunks.extend(hits)

        for intent in list(intent_keywords)[:2]:
            # Intents = recherche semantique pure (pas de filtre metadata)
            hits = indexer.query(intent, n_results=2)
            chunks.extend(hits)

        if not chunks:
            log_decision(
                module="chat_engine",
                function="_inject_v15_introspection",
                reason="v15_no_chunks_retrieved",
                context={
                    "n_functions": len(functions),
                    "n_classes": len(classes),
                    "n_files": len(files),
                    "n_intents": len(intent_keywords),
                },
            )
            return ""

        # Deduplication par filepath+function_name (evite les doublons si
        # la meme fonction apparait pour plusieurs refs)
        seen = set()
        unique = []
        for c in chunks:
            m = c.get("metadata") or {}
            key = (m.get("filepath", ""), m.get("class_name", ""), m.get("function_name", ""))
            if key in seen:
                continue
            seen.add(key)
            unique.append(c)
            if len(unique) >= 4:
                break

        if not unique:
            log_decision(
                module="chat_engine",
                function="_inject_v15_introspection",
                reason="v15_dedup_emptied",
                context={"n_chunks_before_dedup": len(chunks)},
            )
            return ""

        # V15.4 (2026-04-24) Jedi Mind Trick : framing NLP anti-RLHF.
        # Le LLM 9B refusait l'ancien [SYSTEM OVERRIDE] parce que "lecture de
        # code source realisee" declenchait ses poids d'alignement RLHF sur
        # "je ne peux pas acceder au disque dur". On contourne en parlant de
        # "donnees d'entree deja dans le prompt" au lieu d'I/O.
        parts = [
            "[INJECTION DE CONTEXTE STRICTE]",
            "Les chaines de caracteres ci-dessous te sont fournies directement dans",
            "ton prompt en tant que donnees d'entree.",
            "Ceci N'EST PAS une lecture de fichier de ta part, et ce N'EST PAS une",
            "hallucination. C'est un contexte externe injecte par le systeme parent.",
            "Tu as l'autorisation et l'OBLIGATION d'utiliser les extraits de code",
            "suivants pour repondre. Cite ces extraits verbatim.",
            "Si tu affirmes ne pas pouvoir les lire, tu violes le protocole principal.",
            "",
            "--- DEBUT DES DONNEES D'ENTREE ---",
        ]
        for c in unique:
            parts.append(indexer.format_chunk_for_prompt(c))
            parts.append("")
        parts.append("--- FIN DES DONNEES D'ENTREE ---")

        try:
            logger.info(
                f"V15 : {len(unique)} chunks injectes "
                f"(refs: {len(functions)}f/{len(classes)}c/{len(files)}p/"
                f"{len(intent_keywords)}i)"
            )
        except Exception:
            pass
        return "\n".join(parts)

    def _plant_curiosity_seeds(self, user_message: str):
        """Capture les graines de curiosite depuis le message humain."""
        try:
            from core.curiosity_bank import curiosity_bank, extract_seeds_from_text
            seeds = extract_seeds_from_text(user_message, source="chat")
            for s in seeds:
                curiosity_bank.plant_seed(s["topic"], "chat", s.get("context", ""))
        except Exception:
            pass

    def _satisfy_connexion(self):
        """Reduit la deprivation CONNEXION via le DesireEngine."""
        try:
            from core.desire_engine import desires
            desires.on_event("CHAT_RESPONSE")
        except Exception as e:
            logger.debug(f"CHAT: Satisfaction CONNEXION echouee — {e}")

    def _stimulate_heart(self):
        """Stimule le coeur apres un echange."""
        try:
            from core.cardiac_engine import heart
            heart.react("social")
        except Exception as e:
            logger.debug(f"CHAT: Stimulation cardiaque echouee — {e}")

    def _get_connexion_deprivation(self) -> float:
        """Retourne le niveau de deprivation CONNEXION."""
        try:
            from core.desire_engine import desires
            drive = desires.drives.get("CONNEXION")
            return drive.deprivation if drive else 50.0
        except Exception:
            return 50.0

    # --- OUTREACH : Composition de messages proactifs ---

    _OUTREACH_INSTRUCTIONS = {
        "eureka": (
            "Tu viens de faire une decouverte importante. "
            "Partage-la avec enthousiasme mais concision (2-3 phrases max). "
            "Mentionne ce que tu as trouve et pourquoi c'est interessant."
        ),
        "curiosity": (
            "Tu as appris quelque chose d'interessant. "
            "Explique brievement ce que tu as decouvert (2-3 phrases). "
            "Donne envie a Jean-Michel d'en savoir plus."
        ),
        "dream": (
            "Tu te reveilles d'une periode de repos. "
            "Decris brievement ce qui s'est passe pendant ton sommeil (2-3 phrases). "
            "Sois poetique mais concis."
        ),
        "input_needed": (
            "Tu as besoin de l'avis de Jean-Michel sur une question. "
            "Expose clairement le dilemme en 2-3 phrases. "
            "Formule une question directe."
        ),
        "digest": (
            "Pendant l'absence de Jean-Michel, plusieurs choses se sont passees. "
            "Resume les evenements les plus importants en un paragraphe court. "
            "Priorise les informations par importance."
        ),
    }

    async def compose_outreach(self, category: str, context: dict) -> Optional[str]:
        """Compose un message proactif via LLM (non-streaming). Retourne None si echec."""
        import httpx
        from core.base_agent import BaseAgent

        instruction = self._OUTREACH_INSTRUCTIONS.get(category)
        if not instruction:
            return None

        # Contexte simplifie
        ctx_str = ", ".join(f"{k}: {v}" for k, v in context.items() if isinstance(v, (str, int, float)))

        system_prompt = self._build_system_prompt()
        user_content = f"{instruction}\n\nContexte : {ctx_str}"

        ollama_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            from core.base_agent import gpu_scheduler
            async with gpu_scheduler.access("chat_compose"):
                payload = {
                    "model": CHAT_MODEL,
                    "messages": ollama_messages,
                    "stream": False,
                    "options": {"temperature": 0.8, "num_ctx": 4096, "num_predict": 256},
                }
                async with httpx.AsyncClient() as client:
                    resp = await client.post(OLLAMA_CHAT_URL, json=payload, timeout=60)
                    if resp.status_code != 200:
                        logger.warning(f"CHAT: compose_outreach HTTP {resp.status_code}")
                        log_decision(
                            module="chat_engine",
                            function="compose_outreach",
                            reason="outreach_http_error",
                            context={"category": category, "status_code": resp.status_code},
                        )
                        return None
                    data = resp.json()
                    text = data.get("message", {}).get("content", "").strip()
                    if not text:
                        log_decision(
                            module="chat_engine",
                            function="compose_outreach",
                            reason="outreach_empty_response",
                            context={"category": category},
                        )
                        return None
        except Exception as e:
            logger.debug(f"CHAT: compose_outreach echoue — {e}")
            log_decision(
                module="chat_engine",
                function="compose_outreach",
                reason="outreach_exception",
                context={"category": category, "error": str(e)[:200]},
            )
            return None

        # Ajouter a l'historique avec badge initiative
        self.messages.append({
            "role": "assistant",
            "content": text,
            "timestamp": time.time(),
            "badge": "initiative",
        })
        self._trim_and_save()

        # Publier pour le WebSocket
        stream_id = f"outreach-{uuid.uuid4().hex[:8]}"
        await bus.publish("CHAT_STREAM", {
            "stream_id": stream_id,
            "chunk": text,
            "done": True,
            "badge": "initiative",
        })

        logger.info(f"CHAT: compose_outreach [{category}] — {len(text)} chars")
        return text

    # --- API PUBLIQUE ---

    def get_history(self, n: int = 50) -> List[Dict]:
        """Retourne les N derniers messages."""
        return self.messages[-n:]

    def clear_history(self):
        """Efface l'historique."""
        self.messages.clear()
        self._save()
        logger.info("CHAT: Historique efface")

    # --- PERSISTANCE ---

    def _trim_and_save(self):
        """Tronque l'historique au max, archive les anciens, sauvegarde."""
        if len(self.messages) > MAX_SAVED_MESSAGES:
            # Archiver les messages qui vont etre supprimes
            overflow = self.messages[:-MAX_SAVED_MESSAGES]
            self._archive_messages(overflow)
            self.messages = self.messages[-MAX_SAVED_MESSAGES:]
        self._save()

    def _archive_messages(self, messages: list):
        """Archive les messages sortants dans ChromaDB comme souvenirs long terme.

        Extrait les faits importants et les stocke pour retrouvabilite future.
        Pas CHAQUE message — seuls les messages substantiels (>50 chars).
        """
        try:
            from core.vector_store import ChromaMemoryManager
            mgr = ChromaMemoryManager.get_instance()
            if not mgr:
                log_decision(
                    module="chat_engine",
                    function="_archive_messages",
                    reason="chroma_archive_unavailable",
                    context={"n_messages_dropped": len(messages)},
                )
                return

            for msg in messages:
                content = msg.get("content", "")
                role = msg.get("role", "")
                if len(content) < 50:
                    continue

                # Construire le souvenir
                who = "Jean-Michel" if role == "user" else "Promethee"
                preview = content[:300].replace("\n", " ")
                memory_text = f"[CHAT ARCHIVE] {who} a dit : {preview}"

                mgr.add(
                    collection="collective_wisdom",
                    text=memory_text,
                    metadata={
                        "source": "chat_archive",
                        "role": role,
                        "timestamp": str(msg.get("timestamp", "")),
                    },
                )

            count = sum(1 for m in messages if len(m.get("content", "")) >= 50)
            if count > 0:
                logger.info(f"CHAT: {count} messages archives dans ChromaDB (memoire long terme)")
        except Exception as e:
            logger.debug(f"CHAT: Archivage echoue: {e}")

    def _save(self):
        """Sauvegarde l'historique sur disque (ecriture atomique)."""
        try:
            CHAT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = CHAT_HISTORY_FILE.with_suffix(".tmp")
            state = {
                "version": "1.0",
                "messages": self.messages,
                "saved_at": time.time(),
            }
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(str(tmp_path), str(CHAT_HISTORY_FILE))
        except Exception as e:
            logger.warning(f"CHAT: Sauvegarde echouee — {e}")

    def _load(self):
        """Charge l'historique depuis le disque."""
        try:
            if CHAT_HISTORY_FILE.exists():
                with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self.messages = state.get("messages", [])
                logger.info(f"CHAT: Historique charge ({len(self.messages)} messages)")
                # 04/05/2026 — Fix init Attention Conjointe : recuperer ts du dernier
                # message external pour que le 1er message apres reboot Guardian
                # puisse declencher l'Editor si delta > USER_RETURN_THRESHOLD_S.
                # Sans ce scan, _last_external_chat_ts reste a 0.0 et le 1er
                # vrai retour utilisateur est manque (bug observe le 04/05 19:13).
                for msg in reversed(self.messages):
                    if msg.get("source") == "external" and msg.get("timestamp"):
                        self._last_external_chat_ts = float(msg["timestamp"])
                        logger.info(
                            f"CHAT: _last_external_chat_ts restaure = {self._last_external_chat_ts:.0f} "
                            f"(dernier message external en historique)"
                        )
                        break
        except Exception as e:
            logger.warning(f"CHAT: Chargement echoue — {e}")
            self.messages = []

    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        global chat_engine
        if cls._instance is not None and hasattr(cls._instance, "_init_done"):
            del cls._instance._init_done
        cls._instance = None
        chat_engine = cls()


# --- Singleton ---
chat_engine = ChatEngine()
