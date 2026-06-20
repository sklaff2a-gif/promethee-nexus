import asyncio
import os
import re
import json
import logging
import time
import uuid
from typing import Dict, Any, List, Optional

from core.event_bus.bus import bus

logger = logging.getLogger("Council")

# Marqueurs de consensus (détection en début de réponse)
# "CONSENSU" = troncation fréquente du LLM local 8B (47 occurrences observées sur 7 jours)
CONSENSUS_MARKERS = ("CONSENSUS", "CONSENSU", "CONSENUS", "APPROUVE", "APPROUVÉ", "ACCORD FINAL")

# Tour minimum avant d'autoriser le consensus (force au moins 2 tours de critique)
MIN_ROUNDS_BEFORE_CONSENSUS = 3

# Longueur minimale du contenu d'un consensus pour qu'il soit considéré substantiel
MIN_CONSENSUS_CONTENT_LENGTH = 100

# --- LE GRADIENT (V1, 2026-06-01, atelier council 31/05) ---
# Mesure la PENTE de convergence inter-agents pour distinguer un debat FERTILE
# (divergence qui decroit, "le pont s'approche") d'un debat STERILE (divergence
# figee haute, "l'echo"). Decouple la valeur d'un council du simple fait d'avoir
# tenu N tours -> tue l'opium du q=1.0 sur max_rounds. Thermometre, PAS arbitre.
_GRADIENT_MIN_ROUNDS = 3          # < 3 tours reels : trop court pour juger une pente (fusible)
_GRADIENT_SLOPE_EPS = 0.05        # pente <= -eps : convergence nette -> fertile
_GRADIENT_HIGH_DIVERGENCE = 0.6   # divergence finale >= 0.6 + pente plate -> sterile (echo)

# --- Président évaluateur (architect) ---
PRESIDENT_AGENT_NAME = "architect"
MIN_ROUNDS_BEFORE_PRESIDENT = 2

# --- WATCHDOG DU COUNCIL (chien de garde de la délibération) ---
# Doctrine : guillotine FIXE & DÉCOUPLÉE + avortement sur la DÉRIVÉE de menace.
# Le timeout absolu ne dépend PAS du reptile (il doit tenir même si le reptile
# est faussé au moment où Ollama s'emballe). L'avortement anticipé s'indexe sur
# le RISE de menace DEPUIS l'ouverture du council (front montant), JAMAIS sur le
# niveau absolu — contaminé par la menace de fond chronique ("Fantôme du 14 juin"
# ~5.0, plat -> rise=0 -> n'avorte rien). Un abort-watchdog est de l'hygiène,
# pas une défaite : status dédié qui NE déclenche PAS le DIP dopamine.
COUNCIL_MAX_WALL_S = 180.0           # plafond total absolu (bien sous les 300s du guardian)
COUNCIL_PRESIDENT_TIMEOUT_S = 45.0   # borne le président (architect), aligné sur le timeout agent
COUNCIL_THREAT_RISE_ABORT = 2.0      # +2.0 de menace DEPUIS l'ouverture = pic aigu -> abort
WATCHDOG_TIMEOUT_STATUS = "watchdog_timeout"

# --- HALTING APPRIS (Point 1 TRM "Tiny Recursive Model", shadow) ---
# q_hat de convergence : le Gradient (_compute_gradient) mesure deja la pente de
# convergence inter-agents, mais en POST-MORTEM (il plafonne la note a posteriori
# -> DIP dopamine, autonomy_engine:2189). Un debat "echo" brule donc ses N tours de
# GPU complets AVANT d'etre puni. Le q_hat de TRM est le chainon manquant : detecter
# le plateau STERILE EN COURS de debat pour pouvoir l'ecourter avant de gaspiller le GPU.
# Doctrine shadow-reader (cf IRRIGATION_SHADOW, SENTINEL_GATE_MODE) : Phase 1 = on MESURE
# et on LOGGE la decision "would_halt" SANS jamais ecourter le debat (run() bit-identique).
# Passage en 'active' GATED sur N councils de donnees shadow prouvant que le would-halt
# ne coincide JAMAIS avec un debat qui aurait fini par atteindre le consensus.
#   COUNCIL_HALT_MODE = 'shadow' (defaut, mesure) | 'off' (desactive) | 'active' (NON branche)
COUNCIL_HALT_MODE = os.getenv("COUNCIL_HALT_MODE", "shadow")
_HALT_SHADOW_LOG = "memory/council_halt_shadow.jsonl"


def _read_threat_level() -> float:
    """Lit le niveau de menace du reptilien (lecture seule, défensive).

    Sert UNIQUEMENT à calculer une dérivée (rise depuis l'ouverture du council).
    Retourne 0.0 si indisponible — fail-safe : seule la deadline fixe protège
    alors, et c'est suffisant (la guillotine ne dépend pas du reptile)."""
    try:
        from core.organ_registry import get_organ
        reptile = get_organ("reptilian")
        return float(getattr(reptile, "threat_level", 0.0)) if reptile else 0.0
    except Exception:
        return 0.0

# Contexte projet injecté dans tous les prompts Council
# Note: base_agent.generate_content() injecte aussi un header projet court — garder cohérent
_COUNCIL_PROJECT_CONTEXT = (
    "CONTEXTE PROJET PROMÉTHÉE :\n"
    "Système multi-agents IA autonome. Stack : Python 3.11, FastAPI, Ollama (LLM local), "
    "Google Gemini (Cloud), ChromaDB, WebSocket. Tourne sur UN SEUL PC Windows.\n"
    "Modules existants : orchestrator, router (3 niveaux), bus d'événements, "
    "autonomy_engine, ci_pipeline, self_awareness, psyche, vector_store, "
    "10 agents (strategist, coder, architect, factory, formatter, researcher, "
    "writer, security, infra, evolution).\n"
    "CONTRAINTE MATÉRIELLE : Le projet tourne sur UN SEUL PC Windows avec Ollama local. "
    "Pas de cluster, pas de conteneurs, pas de cloud infra.\n"
    "HORS PÉRIMÈTRE : Kubernetes, Docker, Kafka, microservices, blockchain, "
    "Chaos Engineering, load balancing, budget réel."
)


from core.prompt_templates import get_project_structure as _get_project_structure
from core.prompt_templates import council_guardrail as _council_guardrail


# --- Extraction mots-clés (utilitaire recadrage étudiant) ---
_STOPWORDS_FR = frozenset({
    "pour", "dans", "avec", "comment", "quand", "quel", "quelle",
    "quels", "quelles", "cette", "notre", "nous", "vous", "sont",
    "être", "avoir", "faire", "plus", "moins", "très", "tout",
    "tous", "etre", "entre", "aussi", "comme", "mais", "donc",
    "alors", "peut", "doit", "faut", "sans", "vers", "chez",
    "sous", "avant", "après", "apres", "depuis", "encore",
    "autre", "autres", "même", "meme", "quoi", "dont", "ceux",
})


def _extract_keywords(text: str, top_n: int = 8) -> set:
    """Extrait les mots-clés significatifs d'un texte (déterministe)."""
    words = re.findall(r'[a-zA-Zéèêàùûôîïëç]{4,}', text.lower())
    freq = {}
    for w in words:
        if w not in _STOPWORDS_FR:
            freq[w] = freq.get(w, 0) + 1
    return set(sorted(freq, key=freq.get, reverse=True)[:top_n])


# --- Scoring des arguments du Council ---
# Critères objectifs pour pondérer la force d'un argument
_FILE_PATTERN = re.compile(r'(?:core/|Agents/|config[./]|tests/|main\.py)\S+')
_ACTION_VERBS = re.compile(
    r'(?:ajouter|modifier|créer|supprimer|remplacer|implémenter|refactorer|déplacer|'
    r'renommer|corriger|injecter|extraire|vérifier|valider)',
    re.IGNORECASE
)
_CODE_BLOCK = re.compile(r'```(?:python)?\s*\n.+?\n```', re.DOTALL)


# --- Cache des fichiers réels du projet (sanitizer) ---
_REAL_FILES_CACHE = None


def _get_real_files() -> set:
    """Retourne l'ensemble des chemins relatifs des fichiers .py réels du projet (cached)."""
    global _REAL_FILES_CACHE
    if _REAL_FILES_CACHE is not None:
        return _REAL_FILES_CACHE
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = set()
    # Mêmes répertoires que get_project_structure()
    for subdir in ["core", "Agents", "core/grimoire", "core/event_bus",
                    "core/capabilities", "core/memory", "core/games",
                    "core/plugins", "core/domains"]:
        dir_path = os.path.join(project_root, subdir.replace("/", os.sep))
        if os.path.isdir(dir_path):
            for f in os.listdir(dir_path):
                if f.endswith(".py"):
                    result.add(f"{subdir}/{f}")
    # Racine
    for f in os.listdir(project_root):
        if f.endswith(".py") and os.path.isfile(os.path.join(project_root, f)):
            result.add(f)
    # config/ et tests/
    for subdir in ["config", "tests"]:
        dir_path = os.path.join(project_root, subdir)
        if os.path.isdir(dir_path):
            for f in os.listdir(dir_path):
                if f.endswith(".py"):
                    result.add(f"{subdir}/{f}")
    _REAL_FILES_CACHE = result
    return _REAL_FILES_CACHE


def _find_closest_file(path: str) -> str:
    """Trouve le fichier réel le plus proche par basename.
    1. Match exact → retourne tel quel
    2. Même basename dans le projet → retourne (préfère même parent si ambiguïté)
    3. Aucun match → retourne ''
    """
    real_files = _get_real_files()
    if path in real_files:
        return path
    basename = os.path.basename(path.replace("\\", "/"))
    if not basename:
        return ""
    # Chercher toutes les correspondances par basename
    candidates = [f for f in real_files if f.endswith("/" + basename) or f == basename]
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]
    # Préférer le même répertoire parent
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    for c in candidates:
        c_parent = c.rsplit("/", 1)[0] if "/" in c else ""
        if c_parent == parent:
            return c
    return candidates[0]


def _dedup_repetitive_text(content: str) -> str:
    """Detecte et tronque les reponses LLM qui repetent le meme paragraphe en boucle.
    Incident 2026-03-13 : strategist copiait le meme bloc 4 fois dans une seule reponse."""
    if len(content) < 200:
        return content
    # Decouper en paragraphes non-vides
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    if len(paragraphs) < 3:
        return content
    # Detecter les paragraphes quasi-identiques (mots communs > 80%)
    seen = []
    unique_paragraphs = []
    for p in paragraphs:
        words = set(p.lower().split())
        is_dup = False
        for seen_words in seen:
            if not words or not seen_words:
                continue
            overlap = len(words & seen_words) / max(len(words), len(seen_words))
            if overlap > 0.8:
                is_dup = True
                break
        if not is_dup:
            unique_paragraphs.append(p)
            seen.append(words)
        # else: paragraphe duplique, on le jette
    if len(unique_paragraphs) < len(paragraphs):
        dropped = len(paragraphs) - len(unique_paragraphs)
        logger.info(f"[COUNCIL] Anti-boucle : {dropped} paragraphes repetitifs supprimes")
        return "\n\n".join(unique_paragraphs)
    return content


def _score_argument(content: str) -> dict:
    """Score un argument de Council sur des critères objectifs.
    Retourne {"score": 0.0-1.0, "confidence": 0.0-1.0, "breakdown": {...}}."""
    if not content or len(content.strip()) < 20:
        return {"score": 0.0, "confidence": 0.0, "breakdown": {}}

    points = 0.0
    breakdown = {}

    # 1. Citations de fichiers réels du projet (0-3 pts)
    files_cited = _FILE_PATTERN.findall(content)
    unique_files = set(files_cited)
    file_score = min(len(unique_files), 3)
    points += file_score
    breakdown["fichiers_cités"] = len(unique_files)

    # 2. Verbes d'action concrets (0-2 pts)
    actions = _ACTION_VERBS.findall(content)
    action_score = min(len(actions), 2)
    points += action_score
    breakdown["actions_proposées"] = len(actions)

    # 3. Blocs de code (0-2 pts)
    code_blocks = _CODE_BLOCK.findall(content)
    code_score = min(len(code_blocks), 2)
    points += code_score
    breakdown["blocs_code"] = len(code_blocks)

    # 4. Longueur substantielle (0-2 pts) — plus c'est développé, mieux c'est
    length = len(content.strip())
    if length >= 500:
        length_score = 2.0
    elif length >= 200:
        length_score = 1.0
    else:
        length_score = 0.5
    points += length_score
    breakdown["longueur"] = length

    # 5. Pénalité : contenu en anglais (-1 pt)
    english_markers = len(re.findall(
        r'\b(?:should|would|could|implement|function|however|therefore|moreover)\b',
        content, re.IGNORECASE
    ))
    if english_markers >= 3:
        points -= 1.0
        breakdown["pénalité_anglais"] = english_markers

    # Normaliser sur [0, 1]
    max_points = 9.0  # 3+2+2+2
    score = max(0.0, min(points / max_points, 1.0))

    # Confidence = à quel point le score est fiable (basé sur la richesse du contenu)
    confidence = min(1.0, (len(unique_files) + len(actions) + len(code_blocks)) / 5.0)

    return {"score": round(score, 2), "confidence": round(confidence, 2), "breakdown": breakdown}


def _strip_markdown_prefix(text: str) -> str:
    """Retire les préfixes markdown courants (#, *, >, -) en début de texte."""
    cleaned = text.strip()
    # Retirer les headers markdown (##, ###, etc.)
    cleaned = re.sub(r'^#{1,6}\s*', '', cleaned)
    # Retirer le gras/italique AUTOUR du premier mot (ex: **CONSENSUS**)
    cleaned = re.sub(r'^[\*_]{1,3}(.+?)[\*_]{1,3}', r'\1', cleaned)
    # Retirer le gras/italique en préfixe seul (ex: **CONSENSUS suite)
    cleaned = re.sub(r'^[\*_]{1,3}\s*', '', cleaned)
    # Retirer les blockquotes (>)
    cleaned = re.sub(r'^>\s*', '', cleaned)
    # Retirer les tirets de liste (- )
    cleaned = re.sub(r'^-\s+', '', cleaned)
    return cleaned.strip()


def _is_consensus(text: str) -> bool:
    """Vérifie que la réponse COMMENCE par un marqueur de consensus (tolère le markdown)
    ET que le contenu après le marqueur est substantiel (>= MIN_CONSENSUS_CONTENT_LENGTH chars).

    V6.0 (2026-04-20) : conserve pour backward-compat. Le nouveau parseur
    est _is_consensus_v2 qui est utilise dans Council.run().
    """
    cleaned = _strip_markdown_prefix(text)
    cleaned_upper = cleaned.upper()
    for marker in CONSENSUS_MARKERS:
        if cleaned_upper.startswith(marker):
            # Extraire le contenu après le marqueur
            remainder = cleaned[len(marker):].strip(" :\n\t-—")
            if len(remainder) < MIN_CONSENSUS_CONTENT_LENGTH:
                return False  # Shallow consensus — contenu insuffisant
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════
# V6.0 (Phase 7 Reforme 2) : Le Vote Flou — parser robuste anti-8B
# ═══════════════════════════════════════════════════════════════════════
# Audit Phase 6 : 0% de consensus en 10 jours. Cause : les LLM 8B locaux
# font des fautes de frappe sur leurs marqueurs (CONSENU, CONSENSS) et
# l'ancien _is_consensus exigeait un startswith strict sur une liste
# fermee. De plus le quorum 2/3 rendait le vote impossible en pratique.
#
# Strategie 3 couches :
#  Couche 1 - contrainte de forme : MAJUSCULES en debut de ligne avec
#             terminateur. Elimine consequence, consentement, accordeon.
#  Couche 2 - fautes canoniques : stems listes explicitement (pas de
#             Levenshtein qui matcherait consentement a distance 2).
#  Couche 3 - vote structure VOTE:/VERDICT: en debut de reponse (case
#             insensible car c'est un format commande).

# Stems canoniques + variantes observees dans les logs (47 occurrences
# CONSENU sur 7 jours). Case-sensitive : majuscules requises.
# APPROUV[ÉE]E? -> APPROUVE / APPROUVÉ / APPROUVEE / APPROUVÉE.
_CONSENSUS_REGEX_STRICT = re.compile(
    r'(?:^|\n)\s*'                              # debut de ligne obligatoire
    r'(?:CONSENSUS|CONSENSU|CONSENUS|CONSENU|'  # racines canoniques
    r'CONSENSS|APPROUV[EÉ]E?|'                  # APPROUVE / APPROUVÉ / APPROUVEE
    r'ACCORD\s+FINAL|D[\'’]ACCORD)'
    r'(?=\s*[\n.:,!—-]|$)',                     # terminateur (pas en milieu de phrase)
    re.MULTILINE                                 # CASE-SENSITIVE
)

# Format commande pour prompts structures. Case-insensitive car le LLM
# peut ecrire "Vote: pour" ou "VERDICT : POUR" indifferamment.
_VOTE_LINE_FLEX = re.compile(
    r'^\s*(?:VOTE|VERDICT|POSITION)\s*[:=]\s*'
    r'(?:POUR|OUI|APPROUV[EÉ]E?)\b',
    re.MULTILINE | re.IGNORECASE
)

# Detection d'un vote NEGATIF explicite : invalide le consensus meme si
# un marqueur positif apparait ailleurs dans le texte.
_VOTE_NEGATIVE = re.compile(
    r'^\s*(?:VOTE|VERDICT|POSITION)\s*[:=]\s*'
    r'(?:CONTRE|NON|REJETE|REFUS|OPPOSE)\b',
    re.MULTILINE | re.IGNORECASE
)


def _is_consensus_v2(text: str) -> bool:
    """V6.0 : parser robuste tolerant aux fautes 8B, refractaire aux
    faux positifs du vocabulaire courant (consequence, consentement,
    accordeon).

    3 couches :
      1. Filtre longueur minimale (MIN_CONSENSUS_CONTENT_LENGTH)
      2. Rejet explicite si vote negatif en debut de texte
      3. Match positif via VOTE:/VERDICT: OU marqueur majuscule en
         debut de ligne avec terminateur ponctuation
    """
    if not text or len(text) < MIN_CONSENSUS_CONTENT_LENGTH:
        return False
    # Retirer les prefixes markdown (## CONSENSUS, **CONSENSUS**, etc.)
    # avant le match pour gerer les LLM locaux qui formatent en markdown.
    cleaned = _strip_markdown_prefix(text)
    # Rejet si vote negatif explicite
    if _VOTE_NEGATIVE.search(cleaned):
        return False
    # Match positif (soit vote structure, soit marqueur majuscule)
    return bool(_VOTE_LINE_FLEX.search(cleaned) or
                _CONSENSUS_REGEX_STRICT.search(cleaned))


def _parse_president_verdict(response: str) -> Dict[str, str]:
    """Parse la réponse du président (architect) pour extraire le verdict.
    Retourne {"verdict": "PERTINENT"|"REDIRECT"|"ABORT", "feedback": str}."""
    if not response or not response.strip():
        return {"verdict": "PERTINENT", "feedback": ""}

    cleaned = _strip_markdown_prefix(response.strip())
    first_line = cleaned.split("\n", 1)[0].strip()
    first_word = first_line.split()[0].upper().rstrip(":") if first_line.split() else ""

    for verdict in ("PERTINENT", "REDIRECT", "ABORT"):
        if first_word == verdict:
            feedback = first_line[len(verdict):].strip(" :\t—-")
            if not feedback and "\n" in cleaned:
                feedback = cleaned.split("\n", 1)[1].strip()
            return {"verdict": verdict, "feedback": feedback}

    # Fallback : scanner les 100 premiers caractères
    head = cleaned[:100].upper()
    for verdict in ("ABORT", "REDIRECT", "PERTINENT"):
        if verdict in head:
            feedback = cleaned[head.index(verdict) + len(verdict):].strip(" :\t—-\n")
            return {"verdict": verdict, "feedback": feedback[:200]}

    return {"verdict": "PERTINENT", "feedback": ""}


def parse_council_mission(raw_mission: str) -> Optional[Dict[str, Any]]:
    """
    Parse la syntaxe 'agent1, agent2 - mission' ou 'agent1, agent2 : mission'.
    Retourne {"participants": [...], "mission": str} ou None si syntaxe invalide.
    """
    pattern = r"^([\w]+(?:\s*,\s*[\w]+)+)\s*[-:]\s*(.+)$"
    match = re.match(pattern, raw_mission.strip(), re.DOTALL)
    if not match:
        return None
    participants_raw = match.group(1)
    mission = match.group(2).strip()
    participants = [p.strip().lower() for p in participants_raw.split(",")]
    if len(participants) < 2:
        return None
    return {"participants": participants, "mission": mission}


class Council:
    """
    Système de débat multi-tours entre agents.
    V2 : Anti-écho — force la critique au tour 1, consensus interdit avant le tour 2.
    """

    def __init__(self, agents: Dict[str, Any], participants: List[str],
                 mission: str, max_rounds: int = 5, enable_student: bool = True,
                 enable_advocate: bool = True):
        self.agents = agents
        self.participants = list(participants)  # copie mutable
        self.mission = mission
        self.max_rounds = max_rounds
        self.enable_student = enable_student
        self.enable_advocate = enable_advocate
        self.council_id = str(uuid.uuid4())[:8]
        self.transcript: List[Dict[str, Any]] = []

        # ─── Phase 7 Reforme 1 (2026-04-20) : Separation des pouvoirs ───
        # Le President (architect) ne peut PAS etre participant, sinon il perd
        # son role d'arbitre (cf. _evaluate_round qui skip l'evaluation si le
        # president est dans self.participants).
        #
        # Audit Phase 6 : 89% des councils avaient architect dans les
        # participants -> 0% de consensus sur 10 jours. Sans arbitre, pas de
        # REDIRECT ni ABORT possible -> les debats tournent dans le vide.
        _FALLBACK_PARTICIPANTS = ("evolution", "coder", "writer", "researcher",
                                  "strategist", "formatter")
        if PRESIDENT_AGENT_NAME in self.participants:
            _original = list(self.participants)
            self.participants = [p for p in self.participants
                                 if p != PRESIDENT_AGENT_NAME]
            for cand in _FALLBACK_PARTICIPANTS:
                if cand in self.agents and cand not in self.participants:
                    self.participants.append(cand)
                    break
            logger.info(
                f"[COUNCIL {self.council_id}] Separation des pouvoirs : "
                f"'{PRESIDENT_AGENT_NAME}' retire de {_original} -> "
                f"{self.participants} (il reste President arbitre)."
            )

        # Securite : au moins 2 participants, sinon run() va rejeter.
        # Si le filtre a laisse < 2, on complete avec des fallback dispos.
        while len(self.participants) < 2:
            added = False
            for cand in _FALLBACK_PARTICIPANTS:
                if cand in self.agents and cand not in self.participants:
                    self.participants.append(cand)
                    added = True
                    break
            if not added:
                break  # agents epuises, laisser run() rejeter proprement

    def _build_student_contribution(self, round_num: int) -> str:
        """Construit la contribution de Prométhée-étudiant (déterministe, 0 LLM).
        Round 1 : ouverture (émotion + goals + desires + question).
        Round 2+ : recadrage si problèmes détectés, sinon relance classique."""
        if round_num > 1:
            recadrage = self._build_student_recadrage(round_num)
            if recadrage:
                return recadrage
            return self._build_student_followup(round_num)

        parts = []

        # Émotion cardiaque
        try:
            from core.cardiac_engine import heart
            emotion = heart.current_emotion
            coherence = heart.coherence
            if emotion:
                parts.append(f"Je ressens {emotion}")
                if coherence < 0.4:
                    parts.append("(mon intuition hesite)")
        except Exception:
            pass

        # Goals préfrontaux
        try:
            from core.prefrontal import prefrontal
            wm = prefrontal.get_working_memory()
            if wm:
                goal_titles = [s.get("goal_title", "") for s in wm[:2] if s.get("goal_title")]
                if goal_titles:
                    parts.append(f"Mes objectifs: {', '.join(goal_titles)}")
        except Exception:
            pass

        # Narrative desires
        try:
            from core.desire_engine import desires
            narrative = desires.get_dominant_narrative(2)
            if narrative:
                parts.append(narrative)
        except Exception:
            pass

        # Voix intérieure
        try:
            from core.inner_voice import inner_voice
            ctx = inner_voice.get_voice_context()
            if ctx and ctx.get("identity"):
                parts.append(ctx["identity"][:80])
        except Exception:
            pass

        if not parts:
            return ""

        # Reformuler la mission en question
        mission_short = self.mission[:100].rstrip(".")
        question = f"Comment {mission_short.lower()} en lien avec ces preoccupations ?"

        text = "QUESTION: " + " | ".join(parts) + f" — {question}"
        return text[:300]

    def _build_student_followup(self, round_num: int) -> str:
        """Relance de l'étudiant aux rounds 2+.
        Analyse le round précédent, détecte les angles morts, relance."""
        prev_round = round_num - 1
        prev_entries = [
            e for e in self.transcript
            if e["round"] == prev_round and not e.get("is_student")
        ]
        if not prev_entries:
            return ""

        parts = []

        # Fichiers mentionnés par les profs
        mentioned_files = set()
        for e in prev_entries:
            mentioned_files.update(_FILE_PATTERN.findall(e.get("content", "")))

        # Détecter si les profs ont abordé les goals
        try:
            from core.prefrontal import prefrontal
            wm = prefrontal.get_working_memory()
            if wm:
                for slot in wm[:2]:
                    title = slot.get("goal_title", "")
                    if not title:
                        continue
                    title_kw = _extract_keywords(title, top_n=3)
                    if not title_kw:
                        continue
                    all_content = " ".join(e.get("content", "") for e in prev_entries)
                    content_kw = _extract_keywords(all_content, top_n=20)
                    overlap = title_kw & content_kw
                    if len(overlap) < max(1, len(title_kw) * 0.5):
                        parts.append(f"Personne n'a aborde '{title}'")
        except Exception:
            pass

        # Détecter pulsions non addressées
        try:
            from core.desire_engine import desires
            narrative = desires.get_dominant_narrative(1)
            if narrative:
                narr_kw = _extract_keywords(narrative, top_n=3)
                if narr_kw:
                    all_content = " ".join(e.get("content", "") for e in prev_entries)
                    content_kw = _extract_keywords(all_content, top_n=20)
                    overlap = narr_kw & content_kw
                    if len(overlap) < max(1, len(narr_kw) * 0.5):
                        short = narrative[:50]
                        parts.append(f"Ma pulsion '{short}' n'a pas ete abordee")
        except Exception:
            pass

        # Cohérence cardiaque basse
        try:
            from core.cardiac_engine import heart
            if heart.coherence < 0.4:
                parts.append("Mon intuition hesite sur cette direction")
        except Exception:
            pass

        # Bon argument d'un agent
        best = max(prev_entries, key=lambda e: e.get("score", 0), default=None)
        if best and best.get("score", 0) >= 0.4:
            best_files = _FILE_PATTERN.findall(best.get("content", ""))
            file_ref = best_files[0] if best_files else ""
            if file_ref:
                parts.append(f"L'analyse de {best['agent']} sur {file_ref} est pertinente")

        if not parts:
            return ""

        text = "QUESTION: " + " | ".join(parts)
        return text[:300]

    def _build_student_recadrage(self, round_num: int) -> str:
        """Recadrage actif de l'étudiant aux rounds 2+ (déterministe, 0 LLM).
        Détecte : fichiers hallucines, hors-sujet, répétition.
        Retourne un texte préfixé RECADRAGE: (max 400 chars) ou '' si rien à signaler."""
        prev_round = round_num - 1
        prev_entries = [
            e for e in self.transcript
            if e["round"] == prev_round
            and not e.get("is_student") and not e.get("is_advocate")
        ]
        if not prev_entries:
            return ""

        issues = []
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # A. Fichiers hallucines
        for entry in prev_entries:
            files_cited = _FILE_PATTERN.findall(entry.get("content", ""))
            hallucinated = []
            for fpath in files_cited:
                clean = fpath.rstrip(".,;:)\"'`*")
                full = os.path.join(project_root, clean)
                if not os.path.exists(full):
                    hallucinated.append(clean)
            if len(hallucinated) >= 2:
                unique_h = list(dict.fromkeys(hallucinated))[:3]
                issues.append(
                    f"{entry['agent'].upper()} cite {len(hallucinated)} fichiers "
                    f"inexistants ({', '.join(unique_h)})"
                )

        # B. Hors-sujet (mission drift) — seuil relâché + cooldown par agent
        # On ne signale hors-sujet que si ZERO overlap ET max 1 recadrage par agent par débat
        mission_kw = _extract_keywords(self.mission, top_n=8)
        if mission_kw:
            # Compter les recadrages hors-sujet déjà émis par agent
            already_warned = set()
            for e in self.transcript:
                if e.get("is_student") and "hors-sujet" in e.get("content", "").lower():
                    for p in self.participants:
                        if p.upper() in e.get("content", ""):
                            already_warned.add(p)
            for entry in prev_entries:
                if entry["agent"] in already_warned:
                    continue  # Déjà recadré une fois, on laisse tranquille
                content_kw = _extract_keywords(entry.get("content", ""), top_n=15)
                overlap = mission_kw & content_kw
                if len(overlap) == 0:
                    issues.append(
                        f"{entry['agent'].upper()} est hors-sujet"
                    )

        # C. Répétition (agent dit la même chose qu'au round N-2)
        if prev_round >= 2:
            prev_prev_entries = {
                e["agent"]: e for e in self.transcript
                if e["round"] == prev_round - 1
                and not e.get("is_student") and not e.get("is_advocate")
            }
            for entry in prev_entries:
                prev_prev = prev_prev_entries.get(entry["agent"])
                if prev_prev:
                    kw_now = _extract_keywords(entry.get("content", ""), top_n=10)
                    kw_prev = _extract_keywords(prev_prev.get("content", ""), top_n=10)
                    if kw_now and kw_prev:
                        overlap_ratio = len(kw_now & kw_prev) / max(len(kw_now), 1)
                        if overlap_ratio > 0.8:
                            issues.append(
                                f"{entry['agent'].upper()} repete le meme contenu"
                            )

        if not issues:
            return ""

        text = "RECADRAGE: " + " | ".join(issues)
        return text[:400]

    def _build_advocate_contribution(self, round_num: int) -> str:
        """Avocat du Diable — critique deterministe du round precedent (0 LLM).
        Detecte : fichiers hallucines, paraphrase/echo, angles morts (tests, rollback, effets secondaires).
        Retourne un texte prefixe OBJECTION: (max 400 chars) ou '' si rien a signaler."""
        prev_round = round_num - 1
        prev_entries = [
            e for e in self.transcript
            if e["round"] == prev_round
            and not e.get("is_student") and not e.get("is_advocate")
        ]
        if not prev_entries:
            return ""

        objections = []

        # 1. Validation fichiers : extraire les chemins et verifier l'existence
        hallucinated = []
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for entry in prev_entries:
            files_cited = _FILE_PATTERN.findall(entry.get("content", ""))
            for fpath in files_cited:
                # Nettoyer le chemin (retirer ponctuation trailing + backtick/asterisque)
                clean = fpath.rstrip(".,;:)\"'`*")
                full = os.path.join(project_root, clean)
                if not os.path.exists(full):
                    hallucinated.append(clean)
        if hallucinated:
            unique_halluc = list(dict.fromkeys(hallucinated))[:3]
            objections.append(
                f"FICHIERS INEXISTANTS: {', '.join(unique_halluc)}"
            )

        # 2. Detection de paraphrase/echo : 2+ agents avec les memes mots-cles
        if len(prev_entries) >= 2:
            agent_keywords = {}
            for entry in prev_entries:
                content_lower = entry.get("content", "").lower()
                keywords = set(_ACTION_VERBS.findall(content_lower))
                files = set(f.rstrip(".,;:)\"'`*") for f in _FILE_PATTERN.findall(content_lower))
                agent_keywords[entry["agent"]] = keywords | files

            # Chercher des paires avec >= 3 mots-cles communs
            agents_list = list(agent_keywords.keys())
            for i in range(len(agents_list)):
                for j in range(i + 1, len(agents_list)):
                    common = agent_keywords[agents_list[i]] & agent_keywords[agents_list[j]]
                    if len(common) >= 3:
                        objections.append(
                            f"ECHO: {agents_list[i]} et {agents_list[j]} "
                            f"repetent les memes termes ({', '.join(list(common)[:3])})"
                        )
                        break
                if len(objections) >= 2:
                    break

        # 3. Angles morts : verifier si les propositions mentionnent tests, rollback, effets secondaires
        all_content = " ".join(e.get("content", "") for e in prev_entries).lower()
        missing = []
        if "test" not in all_content and "pytest" not in all_content:
            missing.append("tests")
        if "rollback" not in all_content and "revert" not in all_content and "backup" not in all_content:
            missing.append("rollback")
        if "effet" not in all_content and "impact" not in all_content and "risque" not in all_content:
            missing.append("effets secondaires")
        if missing:
            objections.append(f"ANGLES MORTS: personne n'a mentionne {', '.join(missing)}")

        if not objections:
            return ""

        text = "OBJECTION: " + " | ".join(objections)
        return text[:400]

    def _sanitize_file_references(self, content: str) -> str:
        """Post-traitement déterministe : corrige les chemins fichiers dans les réponses.
        - Nettoie backticks/astérisques trailing
        - Corrige les chemins avec mauvais répertoire (matching par basename)
        - Marque [chemin→INEXISTANT] si aucun match
        """
        if not content:
            return content
        matches = _FILE_PATTERN.findall(content)
        if not matches:
            return content
        # Dédupliquer en préservant l'ordre
        seen = set()
        unique_matches = []
        for m in matches:
            if m not in seen:
                seen.add(m)
                unique_matches.append(m)
        real_files = _get_real_files()
        for raw_match in unique_matches:
            # Ignorer les matches déjà marqués INEXISTANT (éviter cascade)
            if "→INEXISTANT]" in raw_match or "→INEXISTANT" in raw_match:
                continue
            clean = raw_match.rstrip(".,;:)\"'`*")
            if clean in real_files:
                # Fichier réel — juste nettoyer le trailing si différent
                if raw_match != clean:
                    content = content.replace(raw_match, clean)
                continue
            # Chercher le plus proche par basename
            closest = _find_closest_file(clean)
            if closest:
                logger.info(f"Council sanitizer: {clean} → {closest}")
                content = content.replace(raw_match, closest)
            else:
                logger.info(f"Council sanitizer: {clean} → INEXISTANT")
                content = content.replace(raw_match, f"[{clean}→INEXISTANT]")
        return content

    def _format_transcript(self) -> str:
        """Formate le transcript pour l'injecter dans le prompt des agents.
        Optimisé : tours anciens résumés (1 ligne/agent), 2 derniers tours en détail."""
        if not self.transcript:
            return "(Aucune contribution précédente)"

        max_round = max(e["round"] for e in self.transcript)
        cutoff = max_round  # Garder SEUL le dernier round en détail (8B model-friendly)

        lines = []

        # Tours anciens : résumé compact (1 ligne par agent)
        old = [e for e in self.transcript if e["round"] < cutoff]
        if old:
            lines.append(f"--- TOURS 1-{cutoff - 1} (resume) ---")
            for e in old:
                if e.get("is_student"):
                    short = e["content"][:80].replace("\n", " ")
                    lines.append(f"[T{e['round']}] ETUDIANT: {short}...")
                elif e.get("is_advocate"):
                    short = e["content"][:80].replace("\n", " ")
                    lines.append(f"[T{e['round']}] AVOCAT: {short}...")
                else:
                    short = e["content"][:80].replace("\n", " ")
                    lines.append(f"[T{e['round']}] {e['agent'].upper()}: {short}...")

        # Tours récents : détail complet
        recent = [e for e in self.transcript if e["round"] >= cutoff]
        for entry in recent:
            if entry.get("is_student"):
                lines.append(
                    f"[Tour {entry['round']}] PROMETHEE-ETUDIANT :\n{entry['content']}"
                )
            elif entry.get("is_advocate"):
                lines.append(
                    f"[Tour {entry['round']}] AVOCAT DU DIABLE :\n{entry['content']}"
                )
            else:
                score = entry.get("score", 0)
                score_label = "★★★" if score >= 0.6 else "★★" if score >= 0.3 else "★"
                lines.append(
                    f"[Tour {entry['round']}] {entry['agent'].upper()} "
                    f"(pertinence: {score_label} {score:.0%}) :\n{entry['content']}"
                )
        return "\n---\n".join(lines)

    def _build_prompt(self, agent_name: str, current_round: int, president_feedback: str = "") -> str:
        """Construit le prompt pour un agent à un tour donné."""
        history = self._format_transcript()

        # Injection du trait dominant (PSYCHE)
        personality_line = ""
        try:
            from core.psyche import psyche
            trait_name, trait_value = psyche.get_dominant_trait(agent_name)
            personality_line = (
                f"TA PERSONNALITÉ: {trait_name.upper()} ({trait_value:.0f}/100). "
                f"Tes réponses reflètent naturellement ce trait dominant.\n"
            )
        except Exception:
            pass

        # Instructions différenciées selon le tour
        # V6.0 Reforme 2 : format de vote structure en DEBUT de reponse.
        # Les LLM 8B respectent beaucoup mieux les gabarits de debut que
        # les consignes abstraites ou tardives.
        if current_round < MIN_ROUNDS_BEFORE_CONSENSUS:
            round_instructions = (
                f"INSTRUCTIONS TOUR {current_round} (CRITIQUE OBLIGATOIRE) :\n"
                f"- Tu ne peux PAS donner ton accord (pas de CONSENSUS, APPROUVE, etc.).\n"
                f"- Tu DOIS identifier au moins UN problème, UN risque ou UNE question.\n"
                f"- Sois précis et technique : cite des fichiers, des fonctions, des cas limites.\n"
                f"- Si la proposition mentionne des technologies que le projet n'utilise pas "
                f"(Kubernetes, Docker, Kafka, blockchain, etc.), signale-le comme hors-périmètre.\n"
                f"- FORMAT : commence obligatoirement ta reponse par une ligne "
                f"`VOTE: CONTRE` suivie d'une ligne vide, puis ta critique.\n"
            )
        else:
            round_instructions = (
                f"INSTRUCTIONS TOUR {current_round} :\n"
                f"- Analyse les critiques des tours précédents.\n"
                f"- Si toutes les critiques ont été adressées ET que la solution est concrète "
                f"et applicable au projet, c'est le moment de voter POUR.\n"
                f"- Sinon, apporte de nouvelles critiques ou propositions et vote CONTRE.\n"
                f"- Rappel : une bonne solution est SIMPLE et cible des fichiers EXISTANTS.\n"
                f"- FORMAT OBLIGATOIRE — commence par l'UNE de ces deux lignes :\n"
                f"    VOTE: POUR      (si tu valides la solution actuelle)\n"
                f"    VOTE: CONTRE    (si tu veux continuer le debat)\n"
                f"  Puis une ligne vide, puis ta justification.\n"
            )

        # Structure projet réelle (anti-hallucination de fichiers)
        try:
            project_files = _get_project_structure()
        except Exception:
            project_files = ""

        # Feedback du président (si REDIRECT au tour précédent)
        president_block = ""
        if president_feedback:
            president_block = (
                f"\n⚠️ FEEDBACK DU PRÉSIDENT (architect) :\n"
                f"{president_feedback}\n"
                f"Tu DOIS prendre en compte ce feedback dans ta réponse.\n\n"
            )

        # Intuitions du système (spreading activation — cache RAM, 0 requête)
        intuition_block = ""
        try:
            from core.spreading_activation import activation_engine
            thoughts = activation_engine.get_latent_thoughts(max_thoughts=3)
            bridges = activation_engine.get_creative_bridges(unused_only=True)
            parts = []
            if thoughts:
                parts.append("Concepts actifs: " + ", ".join(thoughts))
            for b in bridges[:1]:
                parts.append(b.hypothesis)
            if parts:
                intuition_block = "\n[INTUITIONS DU SYSTÈME]\n" + "\n".join(parts) + "\n"
        except Exception:
            pass

        # Bloc étudiant : si l'étudiant a parlé ce round, instruire l'agent de répondre
        student_block = ""
        student_entries = [e for e in self.transcript if e.get("is_student") and e["round"] == current_round]
        if student_entries:
            student_text = student_entries[-1]["content"]
            if student_text.startswith("RECADRAGE:"):
                student_block = (
                    f"\nPROMETHEE-ETUDIANT (MODERATEUR) :\n"
                    f'"{student_text}"\n\n'
                    f"⚠️ Le systeme a detecte des PROBLEMES dans ta contribution precedente.\n"
                    f"- Si tu es cite : CORRIGE les problemes signales.\n"
                    f"- Cite UNIQUEMENT des fichiers de la liste FICHIERS AUTORISES.\n"
                    f"- Reste sur le SUJET du debat.\n\n"
                )
            else:
                student_block = (
                    f"\nPROMETHEE-ETUDIANT (le systeme lui-meme) DEMANDE :\n"
                    f'"{student_text}"\n\n'
                    f"Tu es son PROFESSEUR. Reponds a ses preoccupations reelles.\n"
                    f"- Adresse DIRECTEMENT les questions de l'etudiant.\n"
                    f"- Propose des actions concretes liees a ses objectifs.\n\n"
                )

        # Bloc avocat du diable : si l'avocat a parle ce round, instruire l'agent de defendre
        advocate_block = ""
        advocate_entries = [e for e in self.transcript if e.get("is_advocate") and e["round"] == current_round]
        if advocate_entries:
            advocate_text = advocate_entries[-1]["content"]
            advocate_block = (
                f"\nAVOCAT DU DIABLE (critique constructive) :\n"
                f'"{advocate_text}"\n\n'
                f"DEFENDS ta proposition face a ces objections. "
                f"Cite des fichiers REELS et explique les risques.\n\n"
            )

        # Prompt restructuré : contexte léger en haut, fichiers + guardrail EN FIN (biais de récence 8B)
        # Tronquer la mission pour éviter l'explosion de contexte
        mission_display = self.mission
        if len(mission_display) > 600:
            mission_display = mission_display[:600] + "..."

        # Garde anti-code pour le coder (il génère du code halluciné au lieu de débattre)
        coder_guard = ""
        if agent_name == "coder":
            coder_guard = (
                "\n⛔ INTERDIT : NE GÉNÈRE PAS DE CODE (pas de ```python, pas de class, pas de def).\n"
                "Tu es ici pour DÉBATTRE et CRITIQUER, pas pour coder.\n"
                "Exprime tes idées en TEXTE, cite des fichiers EXISTANTS, propose des ACTIONS.\n"
            )

        return (
            f"Tu participes à un CONSEIL multi-agents.\n"
            f"LANGUE OBLIGATOIRE : Réponds UNIQUEMENT en français.\n"
            f"{_COUNCIL_PROJECT_CONTEXT}\n\n"
            f"MISSION : {mission_display}\n"
            f"PARTICIPANTS : {', '.join(p.upper() for p in self.participants)}\n"
            f"TOUR : {current_round}/{self.max_rounds}\n"
            f"TON RÔLE : {agent_name.upper()}\n"
            f"{personality_line}\n"
            f"{student_block}"
            f"{advocate_block}"
            f"HISTORIQUE DU DÉBAT :\n{history}\n\n"
            f"{round_instructions}\n"
            f"{president_block}"
            f"{intuition_block}"
            f"{'='*60}\n"
            f"FICHIERS AUTORISÉS (SEULS fichiers que tu peux citer) :\n"
            f"{project_files}\n"
            f"⛔ Cite UNIQUEMENT des fichiers individuels de la liste ci-dessus (ex: core/grimoire/code_reviewer.py).\n"
            f"Ne cite JAMAIS un répertoire seul (core/, Agents/, core/grimoire/). Tout fichier NON listé ci-dessus est une HALLUCINATION.\n"
            f"{_council_guardrail()}"
            f"{coder_guard}"
        )

    def _build_president_prompt(self, round_num: int) -> str:
        """Construit le prompt court pour le président (architect) évaluateur."""
        # Contributions du tour courant (tronquées, sans l'étudiant ni l'avocat)
        round_entries = [e for e in self.transcript if e["round"] == round_num and not e.get("is_student") and not e.get("is_advocate")]
        contributions = "\n".join(
            f"- {e['agent'].upper()} : {e['content'][:300]}"
            for e in round_entries
        )

        # Structure projet réelle pour que le président vérifie les fichiers cités
        try:
            project_files = _get_project_structure()
        except Exception:
            project_files = ""

        return (
            f"Tu es le PRÉSIDENT du conseil. Tu évalues la QUALITÉ du débat (tu ne participes PAS).\n"
            f"MISSION : {self.mission}\n"
            f"TOUR {round_num}/{self.max_rounds}\n\n"
            f"{project_files}\n\n"
            f"CONTRIBUTIONS DE CE TOUR :\n{contributions}\n\n"
            f"CRITÈRES D'ÉVALUATION :\n"
            f"1. Les propositions sont-elles pertinentes pour la mission ?\n"
            f"2. Y a-t-il des technologies HORS PÉRIMÈTRE (Kubernetes, Docker, Kafka, blockchain, microservices) ?\n"
            f"3. Les fichiers mentionnés existent-ils dans la STRUCTURE DU PROJET CI-DESSUS ? "
            f"Si un participant cite un fichier qui N'EXISTE PAS dans cette liste → REDIRECT.\n"
            f"4. Le débat tourne-t-il en rond (répétitions entre tours) ?\n\n"
            f"VERDICT — Réponds par UN SEUL MOT en première ligne :\n"
            f"- PERTINENT : le débat avance bien, continuer\n"
            f"- REDIRECT : le débat dérive ou cite des fichiers inexistants, suivi d'une consigne de recadrage\n"
            f"- ABORT : le débat est irrémédiablement hors-sujet, arrêter\n"
        )

    async def _evaluate_round(self, round_num: int) -> Dict[str, str]:
        """Évalue le tour via le président (architect). Retourne le verdict."""
        # Skip si trop tôt
        if round_num < MIN_ROUNDS_BEFORE_PRESIDENT:
            return {"verdict": "PERTINENT", "feedback": ""}

        # Skip si architect est participant (double rôle)
        if PRESIDENT_AGENT_NAME in self.participants:
            return {"verdict": "PERTINENT", "feedback": ""}

        # Skip si architect absent
        if PRESIDENT_AGENT_NAME not in self.agents:
            return {"verdict": "PERTINENT", "feedback": ""}

        try:
            architect = self.agents[PRESIDENT_AGENT_NAME]
            prompt = self._build_president_prompt(round_num)
            # Borne le président : un architect qui hang (Ollama lag) ne doit pas
            # figer tout le council. Sur timeout → verdict NEUTRE (ni ABORT ni
            # consensus fabriqué) : on laisse le round suivant ou la deadline trancher.
            try:
                response = await asyncio.wait_for(
                    architect.generate_content(prompt),
                    timeout=COUNCIL_PRESIDENT_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"Council {self.council_id} — président timeout "
                    f"({COUNCIL_PRESIDENT_TIMEOUT_S:.0f}s) au tour {round_num}, verdict neutre."
                )
                return {"verdict": "PERTINENT", "feedback": ""}
            result = _parse_president_verdict(response)

            # Publication événement
            await bus.publish("COUNCIL_PRESIDENT_VERDICT", {
                "council_id": self.council_id,
                "round": round_num,
                "verdict": result["verdict"],
                "feedback": result["feedback"],
            })

            logger.info(
                f"Council {self.council_id} Tour {round_num} — "
                f"Président: {result['verdict']}"
            )
            return result

        except Exception as e:
            logger.warning(f"Council {self.council_id} — Erreur président: {e}")
            return {"verdict": "PERTINENT", "feedback": ""}

    def _extract_partial_insight(self) -> Optional[Dict[str, Any]]:
        """V6.0 Reforme 3 : extrait la valeur d'un debat max_rounds.

        Retourne un dict avec :
          - convergence_keywords : mots-cles presents chez >= 50% des agents
          - divergence_by_agent : mots uniques a chaque agent (top 5 par agent)
          - best_argument : meilleur argument (agent + score + extrait)
        Ou None si moins de 2 contributions exploitables.
        """
        real_entries = [e for e in self.transcript
                        if not e.get("is_student") and not e.get("is_advocate")]
        if len(real_entries) < 2:
            return None

        # Grouper les mots-cles par agent (union sur tous ses tours)
        agents_kw: Dict[str, set] = {}
        for e in real_entries:
            kw = _extract_keywords(e.get("content", ""), top_n=10)
            agents_kw.setdefault(e["agent"], set()).update(kw)

        if len(agents_kw) < 2:
            return None

        # Convergence : mots-cles presents chez >= 50% des agents
        all_kw: set = set()
        for kws in agents_kw.values():
            all_kw |= kws
        threshold = max(1, len(agents_kw) // 2)
        convergence = sorted(
            kw for kw in all_kw
            if sum(1 for ks in agents_kw.values() if kw in ks) >= threshold
        )[:10]

        # Divergence : mots-cles propres a un seul agent (top 5/agent)
        divergence: Dict[str, list] = {}
        for agent, kws in agents_kw.items():
            others_kw: set = set()
            for other_agent, other_kws in agents_kw.items():
                if other_agent != agent:
                    others_kw |= other_kws
            unique = kws - others_kw
            if unique:
                divergence[agent] = sorted(unique)[:5]

        # Meilleur argument individuel
        scored = [e for e in real_entries if e.get("score") is not None]
        best = max(scored, key=lambda e: e.get("score", 0), default=None)
        best_arg = None
        if best:
            best_arg = {
                "agent": best.get("agent", ""),
                "score": round(float(best.get("score", 0)), 2),
                "excerpt": best.get("content", "")[:300],
            }

        return {
            "convergence_keywords": convergence,
            "divergence_by_agent": divergence,
            "best_argument": best_arg,
        }

    def _divergence_trajectory(self, max_round: Optional[int] = None) -> List[float]:
        """Trajectoire de divergence inter-agents (1 - Jaccard des mots-cles) tour
        par tour, jusqu'a max_round INCLUS (None = tous les tours). Exclut etudiant
        et avocat (deterministes, ils fausseraient la mesure).

        Brique PURE partagee entre le Gradient post-mortem (_compute_gradient) et le
        halting shadow incremental (_shadow_halt_check). Extraite a l'identique de
        _compute_gradient (refactor iso-comportement)."""
        rounds: Dict[int, list] = {}
        for e in self.transcript:
            if e.get("is_student") or e.get("is_advocate"):
                continue
            if max_round is not None and e["round"] > max_round:
                continue
            rounds.setdefault(e["round"], []).append(e)

        trajectory: List[float] = []
        for r in sorted(rounds):
            entries = rounds[r]
            if len(entries) < 2:
                continue
            kws = [_extract_keywords(e.get("content", ""), top_n=10) for e in entries]
            divs = []
            for i in range(len(kws)):
                for j in range(i + 1, len(kws)):
                    union = kws[i] | kws[j]
                    jacc = (len(kws[i] & kws[j]) / len(union)) if union else 0.0
                    divs.append(1.0 - jacc)
            if divs:
                trajectory.append(sum(divs) / len(divs))
        return trajectory

    def _shadow_halt_check(self, round_num: int) -> Optional[Dict[str, Any]]:
        """HALTING APPRIS (q_hat shadow, Point 1 TRM) — evalue EN COURS de debat si
        la convergence permettrait d'ecourter, SANS jamais ecourter (mode shadow).

        Reutilise les SEUILS du Gradient pour la coherence. Verdicts :
          - 'sterile_plateau' : echo fige haut (divergence finale >= seuil, pente plate)
            -> debat ecourtable sans rien perdre = le would_halt cible.
          - 'converging'      : la divergence decroit nettement (le pont s'approche).
          - 'undecided'       : ni l'un ni l'autre (on laisse explorer).
        Fusible : < _GRADIENT_MIN_ROUNDS tours mesures -> None (trop court pour juger,
        meme garde-fou que le Gradient).

        MESURE SEULEMENT : logge (logger + jsonl append-only) ; ne casse JAMAIS le run
        (try/except total sur l'I/O) ; ne modifie ni status, ni gradient, ni payload bus."""
        if COUNCIL_HALT_MODE == "off":
            return None
        traj = self._divergence_trajectory(max_round=round_num)
        n = len(traj)
        if n < _GRADIENT_MIN_ROUNDS:
            return None  # trop court pour juger une pente (meme fusible que le Gradient)
        slope = (traj[-1] - traj[0]) / (n - 1)
        last = traj[-1]
        if slope <= -_GRADIENT_SLOPE_EPS:
            verdict = "converging"                  # le pont s'approche
        elif last >= _GRADIENT_HIGH_DIVERGENCE:
            verdict = "sterile_plateau"             # echo : ecourtable sans rien perdre
        else:
            verdict = "undecided"                   # on ne punit que la stagnation prouvee
        would_halt = (verdict == "sterile_plateau")

        record = {
            "council_id": self.council_id,
            "round": round_num,
            "max_rounds": self.max_rounds,
            "trajectory": [round(d, 3) for d in traj],
            "slope": round(slope, 4),
            "verdict": verdict,
            "would_halt": would_halt,
            "rounds_saved_if_active": (self.max_rounds - round_num) if would_halt else 0,
            "mode": COUNCIL_HALT_MODE,
            "timestamp": time.time(),
        }
        logger.info(
            f"[COUNCIL {self.council_id}] HALT-SHADOW t{round_num} verdict={verdict} "
            f"would_halt={would_halt} slope={record['slope']} last={round(last, 3)}"
        )
        try:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(root, _HALT_SHADOW_LOG.replace("/", os.sep))
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass  # le shadow ne casse JAMAIS le debat (doctrine fail-safe)
        return record

    def _compute_gradient(self, consensus_reached: bool) -> Dict[str, Any]:
        """LE GRADIENT (V1) — pente de convergence inter-agents sur la duree du debat.

        Distingue un debat FERTILE (divergence qui decroit tour apres tour, "le pont
        s'approche") d'un debat STERILE (divergence figee haute, "l'echo"). Sert a
        decoupler le score d'un council du simple fait d'avoir tenu N tours.
        Retourne {trajectory, slope, verdict in (fertile|sterile|n/a), rounds}.
        Thermometre, PAS arbitre : il mesure, il ne tranche pas le debat.
        """
        # Divergence moyenne inter-agents (1 - Jaccard des mots-cles) par tour.
        # Brique partagee avec le halting shadow (cf _divergence_trajectory).
        trajectory = self._divergence_trajectory()

        # Pente : delta normalise du premier au dernier tour mesure
        n = len(trajectory)
        slope = (trajectory[-1] - trajectory[0]) / (n - 1) if n >= 2 else 0.0

        # Verdict + fusibles
        if consensus_reached:
            verdict = "n/a"                                   # consensus = immunite (il a conclu)
        elif n < _GRADIENT_MIN_ROUNDS:
            verdict = "n/a"                                   # fusible : trop court pour juger
        elif slope <= -_GRADIENT_SLOPE_EPS:
            verdict = "fertile"                               # le pont s'approche
        elif trajectory and trajectory[-1] >= _GRADIENT_HIGH_DIVERGENCE:
            verdict = "sterile"                               # echo : divergence figee haute
        else:
            verdict = "fertile"                               # bienveillant : on ne punit que la stagnation prouvee

        return {
            "trajectory": [round(d, 3) for d in trajectory],
            "slope": round(slope, 4),
            "verdict": verdict,
            "rounds": n,
        }

    async def run(self) -> Dict[str, Any]:
        """Exécute le débat multi-tours."""
        # Validation : au moins 2 participants
        if len(self.participants) < 2:
            return {"status": "error", "reason": "Il faut au moins 2 participants pour un conseil."}

        # Validation : tous les agents existent
        missing = [p for p in self.participants if p not in self.agents]
        if missing:
            return {"status": "error", "reason": f"Agent(s) introuvable(s) : {', '.join(missing)}"}

        # Publication début
        await bus.publish("COUNCIL_START", {
            "council_id": self.council_id,
            "participants": self.participants,
            "mission": self.mission,
            "max_rounds": self.max_rounds
        })

        # Signaler la question au CuriosityReflex
        await bus.publish("COUNCIL_QUESTION", {
            "council_id": self.council_id,
            "topic": self.mission,
        })

        rounds_used = 0
        consensus_reached = False
        aborted = False
        abort_reason = ""
        president_feedback = ""

        # --- WATCHDOG : état d'ouverture (guillotine fixe + dérivée de menace) ---
        _wd_deadline = time.monotonic() + COUNCIL_MAX_WALL_S
        _wd_threat_at_start = _read_threat_level()
        watchdog_aborted = False
        watchdog_reason = ""

        for round_num in range(1, self.max_rounds + 1):
            rounds_used = round_num
            round_consensus_count = 0

            # WATCHDOG — contrôles COOPÉRATIFs en frontière de round (pas de cancel
            # en plein milieu : transcript préservé, état global propre).
            # 1) Deadline absolue, DÉCOUPLÉE du reptile (tient même si Ollama emballé).
            if time.monotonic() >= _wd_deadline:
                watchdog_aborted = True
                watchdog_reason = f"deadline {COUNCIL_MAX_WALL_S:.0f}s dépassée"
                logger.warning(f"[COUNCIL {self.council_id}] WATCHDOG: {watchdog_reason} — abort.")
                break
            # 2) Avortement sur la DÉRIVÉE de menace (rise depuis l'ouverture), JAMAIS
            #    sur le niveau absolu (immunisé contre le Fantôme à 5.0, plat -> rise=0).
            _wd_rise = _read_threat_level() - _wd_threat_at_start
            if _wd_rise >= COUNCIL_THREAT_RISE_ABORT:
                watchdog_aborted = True
                watchdog_reason = f"pic de menace +{_wd_rise:.1f} pendant le débat"
                logger.warning(f"[COUNCIL {self.council_id}] WATCHDOG: {watchdog_reason} — abort.")
                break

            # --- Étudiant ouvre/relance ---
            if self.enable_student:
                student_text = self._build_student_contribution(round_num)
                if student_text:
                    entry = {
                        "agent": "promethee",
                        "round": round_num,
                        "content": student_text,
                        "score": 0.0,
                        "confidence": 0.0,
                        "breakdown": {},
                        "timestamp": time.time(),
                        "is_student": True,
                    }
                    self.transcript.append(entry)
                    await bus.publish("COUNCIL_TURN", {
                        "council_id": self.council_id,
                        "agent": "promethee",
                        "round": round_num,
                        "content": student_text,
                        "is_student": True,
                    })

            # --- Professeurs répondent ---
            for participant in self.participants:
                agent = self.agents[participant]
                prompt = self._build_prompt(participant, round_num, president_feedback)

                # Forcer le modèle base pour les councils (les LoRA spécialisés sont trop lents
                # et produisent du texte répétitif en débat — incident 2026-03-13)
                agent._force_local_next = True
                _saved_model = getattr(agent, "_council_model_override", None)
                from config import Config
                agent._council_model_override = Config.DEFAULT_LOCAL_MODEL
                _orig_specific = Config.AGENT_SPECIFIC_LOCAL_MODELS.get(participant)
                Config.AGENT_SPECIFIC_LOCAL_MODELS[participant] = Config.DEFAULT_LOCAL_MODEL

                # Appel via generate_content avec timeout (évite latences 33 min)
                try:
                    content = await asyncio.wait_for(
                        agent.generate_content(prompt),
                        timeout=45.0  # 45s max par agent (était 90s — trop de GPU gaspillé sur timeouts)
                    )
                except asyncio.TimeoutError:
                    content = (
                        f"[TIMEOUT] {participant.upper()} n'a pas répondu dans les 45 secondes. "
                        f"Contribution ignorée pour ce tour."
                    )
                    logger.warning(f"[COUNCIL] Timeout 45s pour {participant} au tour {round_num}")
                finally:
                    # Restaurer le modèle spécifique de l'agent après l'appel council
                    if _orig_specific is not None:
                        Config.AGENT_SPECIFIC_LOCAL_MODELS[participant] = _orig_specific
                    elif participant in Config.AGENT_SPECIFIC_LOCAL_MODELS:
                        del Config.AGENT_SPECIFIC_LOCAL_MODELS[participant]
                content = self._sanitize_file_references(content)
                content = _dedup_repetitive_text(content)

                # Scorer l'argument
                arg_score = _score_argument(content)

                # Enregistrer dans le transcript
                entry = {
                    "agent": participant,
                    "round": round_num,
                    "content": content,
                    "score": arg_score["score"],
                    "confidence": arg_score["confidence"],
                    "breakdown": arg_score["breakdown"],
                    "timestamp": time.time()
                }
                self.transcript.append(entry)

                # Publication tour de parole (avec score)
                await bus.publish("COUNCIL_TURN", {
                    "council_id": self.council_id,
                    "agent": participant,
                    "round": round_num,
                    "max_rounds": self.max_rounds,
                    "content": content,
                    "score": arg_score["score"],
                    "confidence": arg_score["confidence"]
                })

                # V6.0 : utilise _is_consensus_v2 (parser robuste 3 couches).
                # Consensus ignore avant MIN_ROUNDS_BEFORE_CONSENSUS.
                if round_num >= MIN_ROUNDS_BEFORE_CONSENSUS and _is_consensus_v2(content):
                    round_consensus_count += 1

            # --- Avocat du Diable (round 2+) ---
            if self.enable_advocate and round_num >= 2:
                advocate_text = self._build_advocate_contribution(round_num)
                if advocate_text:
                    entry = {
                        "agent": "advocat",
                        "round": round_num,
                        "content": advocate_text,
                        "score": 0.0,
                        "confidence": 0.0,
                        "breakdown": {},
                        "timestamp": time.time(),
                        "is_advocate": True,
                    }
                    self.transcript.append(entry)
                    await bus.publish("COUNCIL_TURN", {
                        "council_id": self.council_id,
                        "agent": "advocat",
                        "round": round_num,
                        "content": advocate_text,
                        "is_advocate": True,
                    })

            # --- Évaluation présidentielle ---
            verdict = await self._evaluate_round(round_num)
            if verdict["verdict"] == "ABORT":
                aborted = True
                abort_reason = verdict["feedback"]
                break
            elif verdict["verdict"] == "REDIRECT":
                president_feedback = verdict["feedback"]
            else:
                president_feedback = ""

            # --- HALTING APPRIS (q_hat shadow, Point 1 TRM) ---
            # Mesure la convergence du debat EN COURS pour journaliser un "would_halt"
            # (plateau sterile ecourtable). Mode shadow : LOGGE seulement, n'ecourte
            # JAMAIS (doctrine shadow-reader). Le fusible <3 tours vit dans la methode.
            self._shadow_halt_check(round_num)

            # V6.0 Reforme 2 : Majorite simple validee par le President.
            # Avant : quorum ceil(2/3) -> impossible avec 3 LLM 8B qui
            # hallucinent individuellement. Maintenant : 1 marqueur clair
            # + le President n'a pas emis ABORT au dernier round suffit.
            # Le President (architect, retire des participants par
            # Reforme 1) conserve son droit de veto via ABORT.
            if (round_consensus_count >= 1 and
                    verdict.get("verdict") != "ABORT"):
                consensus_reached = True
                logger.info(
                    f"[COUNCIL {self.council_id}] Consensus atteint au tour "
                    f"{round_num} : {round_consensus_count} vote(s) POUR, "
                    f"President={verdict.get('verdict', 'PERTINENT')}."
                )
                break

        # Résumé final
        if watchdog_aborted:
            status = WATCHDOG_TIMEOUT_STATUS
        elif aborted:
            status = "aborted"
        elif consensus_reached:
            status = "consensus"
        else:
            status = "max_rounds"
        last_contributions = [e for e in self.transcript if e["round"] == rounds_used and not e.get("is_student") and not e.get("is_advocate")]
        final_summary = "\n".join(
            f"[{e['agent'].upper()}] {e['content'][:200]}" for e in last_contributions
        )
        if watchdog_aborted:
            final_summary = f"[WATCHDOG — {watchdog_reason}] délibération écourtée.\n\n{final_summary}"
        elif aborted:
            final_summary = f"[PRÉSIDENT — ABORT] {abort_reason}\n\n{final_summary}"

        # Publication fin
        await bus.publish("COUNCIL_END", {
            "council_id": self.council_id,
            "status": status,
            "rounds_used": rounds_used,
            "participants": self.participants,
            "final_summary": final_summary,
            "mission": self.mission,
        })

        # Chunking SOAR : si consensus atteint, publier la regle apprise
        # Le routeur peut l'utiliser comme raccourci Grimoire (N0.5)
        if status == "consensus" and final_summary and len(final_summary) > 100:
            await bus.publish("COUNCIL_RULE_LEARNED", {
                "mission": self.mission[:200],
                "decision": final_summary[:500],
                "participants": self.participants,
                "rounds": rounds_used,
            })

        # V6.0 Reforme 3 : capturer la valeur partielle d'un max_rounds.
        # Un debat de N tours sans consensus formel contient quand meme
        # des signaux exploitables (convergences partielles, divergences
        # structurelles, meilleur argument individuel). Au lieu de jeter,
        # on extrait et on journalise pour alimenter les futurs debats.
        if status == "max_rounds" and rounds_used >= 2:
            partial = self._extract_partial_insight()
            if partial:
                await bus.publish("COUNCIL_PARTIAL_INSIGHT", {
                    "council_id": self.council_id,
                    "mission": self.mission[:200],
                    "rounds_used": rounds_used,
                    **partial,
                })
                logger.info(
                    f"[COUNCIL {self.council_id}] Partial insight : "
                    f"{len(partial.get('convergence_keywords', []))} convergences, "
                    f"{len(partial.get('divergence_by_agent', {}))} divergences."
                )
                # Journaliser dans strategic_journal (appel direct, pas bus)
                try:
                    from core.strategic_journal import journal as strat_journal
                    if hasattr(strat_journal, "append_partial_insight"):
                        strat_journal.append_partial_insight(
                            mission=self.mission[:200],
                            rounds_used=rounds_used,
                            partial=partial,
                            participants=self.participants,
                        )
                except Exception as e:
                    logger.warning(f"[COUNCIL] Journal partial_insight echoue: {e}")

        # Publication AGENT_RESPONSE pour le dialogue principal
        await bus.publish("AGENT_RESPONSE", {
            "agent": "CONSEIL",
            "content": f"[{status.upper()}] après {rounds_used} tour(s).\n\n{final_summary}",
            "timestamp": str(time.time())
        })

        # Métriques de scoring agrégées (exclure student et advocate)
        all_scores = [e.get("score", 0) for e in self.transcript if e.get("score") is not None and not e.get("is_student") and not e.get("is_advocate")]
        avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
        agent_entries = [e for e in self.transcript if not e.get("is_student") and not e.get("is_advocate")]
        best_entry = max(agent_entries, key=lambda e: e.get("score", 0)) if agent_entries else {}

        gradient = self._compute_gradient(consensus_reached)
        # ANTI-SPIRALE : un abort-watchdog n'est PAS un débat stérile. On neutralise
        # son verdict pour qu'il ne soit JAMAIS classé "sterile" (autonomy_engine:2169
        # plafonnerait la note -> DIP dopamine -> spirale menace↑→abort→échec→menace↑↑).
        # L'avortement est de l'hygiène, pas une défaite.
        if watchdog_aborted:
            gradient["verdict"] = WATCHDOG_TIMEOUT_STATUS
        # Observabilite (2026-06-01) : le Gradient ne doit pas etre un capteur muet
        # (travers des moniteurs morts). On journalise verdict + pente + trajectoire
        # a chaque fin de debat pour pouvoir auditer ses decisions a posteriori.
        logger.info(
            f"[COUNCIL {self.council_id}] GRADIENT verdict={gradient['verdict']} "
            f"slope={gradient['slope']} rounds={gradient['rounds']} "
            f"trajectory={gradient['trajectory']}"
        )
        result = {
            "status": status,
            "rounds_used": rounds_used,
            "transcript": self.transcript,
            "final_summary": final_summary,
            "gradient": gradient,
            "scoring": {
                "avg_score": round(avg_score, 2),
                "best_agent": best_entry.get("agent", ""),
                "best_score": best_entry.get("score", 0),
            }
        }
        if abort_reason:
            result["abort_reason"] = abort_reason
        return result
