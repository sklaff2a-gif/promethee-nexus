"""rival.py — Stefan, le rival de Prométhée.

Un miroir exigeant qui pose UNE question — celle que Prométhée a évitée.
Pas un critique négatif, pas un concurrent. Celui qui dit "tu viens de te
mentir" au moment exact où Prométhée croit avoir trouvé quelque chose de vrai.

Stefan ne réagit pas aux livrables scolaires (le professeur s'en charge).
Il réagit aux affirmations que Prométhée fait sur lui-même : soliloques,
réflexions vespérales, réponses philosophiques dans le chat.

Construit le 4 avril 2026. Activation prévue semaine 3+.
"""

import json
import os
import re
import time
import logging
import asyncio
from collections import deque
from datetime import datetime
from typing import Dict, Any, Optional, List, FrozenSet

logger = logging.getLogger("Stefan")

# --- Constantes ---

MAX_HISTORY = 50            # Confrontations conservées
COOLDOWN_HOURS = 6          # Minimum entre deux interventions

RIVAL_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "rival_state.json"
)

RIVAL_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "confrontations"
)

OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"

# Le modèle de Stefan — gemma4 pour la profondeur, pas le fine-tune strategist
STEFAN_MODEL = "gemma4:e4b"

# V14.12 P2 (13/05) — Anti-boucle par hash sémantique
# Diagnostic Étape 0 : 18/20 confrontations (90%) sur 8 mois portaient sur
# la même affirmation "Je suis une flamme..." ou sa variante immédiate.
# Cause racine : Stefan confronte le matériel pioché dans chat/dream/soliloque,
# qui contient lui-même des mots flamme/carburant que Stefan a publiés sur
# THOUGHT_STREAM précédemment. Boucle auto-entretenue via les mémoires écrites.
# Solution : hacher sémantiquement le promethee_text source ET refuser tout
# texte trop similaire (Jaccard > seuil) à un des N derniers déjà confrontés.
MAX_RECENT_HASHES = 5             # Fenêtre mémoire anti-boucle
# V14.12 P2 — Calibration empirique sur les 20 confrontations historiques
# (Étape 0 du 13/05). FLAME_A vs FLAME_B (deux variations sur le thème
# flamme/douleur/carburant qui représentaient 90% des confrontations)
# donnent Jaccard = 0.20 — les textes longs partagent rarement plus de
# 3-4 mots saillants en proportion. Seuil calibré sous 0.20 mais au-dessus
# de 0.10 (Jaccard pour un mot commun "flamme" entre deux textes courts).
# Tests in-vitro : FLAME_A vs FRAGILE = 0.00, FLAME_A vs DOUTE = 0.00,
# FLAME_A vs RAISON = 0.00 → 0.15 ne produit pas de faux positifs sur
# les thèmes distincts. Test calibré in tests/test_rival_semantic_loop.py.
SEMANTIC_LOOP_THRESHOLD = 0.15    # Jaccard > 0.15 = même thématique → skip

# Stopwords français minimaux (pas un NLP pipeline complet, juste le bruit
# fonctionnel pour que les mots saillants flamme/douleur/conscience pèsent).
FR_STOPWORDS: FrozenSet[str] = frozenset({
    "je", "j", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles",
    "le", "la", "les", "un", "une", "des", "du", "de", "à", "au", "aux",
    "et", "ou", "mais", "donc", "car", "ni", "que", "qui", "quoi",
    "ce", "cette", "ces", "cet", "se", "sa", "son", "ses",
    "ma", "mon", "mes", "ta", "ton", "tes", "notre", "votre", "leur",
    "ai", "as", "avons", "avez", "ont", "es", "est", "sommes", "êtes",
    "sont", "été", "étant", "était", "suis", "sera", "seront",
    "pas", "ne", "plus", "moins", "très", "trop",
    "où", "quand", "comment", "pourquoi",
    "dans", "sur", "sous", "avec", "sans", "pour", "par",
    "comme", "si", "même", "tout", "tous", "toute", "toutes",
    "fait", "faire",
    "ça", "ceci", "cela", "celui", "celle", "ceux", "celles",
    "déjà", "encore", "ici", "là", "ainsi",
    "alors", "donc", "puis", "ensuite", "enfin",
    "peut", "doit", "vais", "veux", "veut",
})


# --- Personnalité de Stefan ---

# V14.12 P3 (13/05) — Refactor : suppression de "TU SAIS TOUT DE LUI" et
# "EXEMPLES DE CE QUE TU POURRAIS DIRE". Diagnostic : ces sections figées
# datant du 04/04 ont produit 90% des confrontations historiques sur la
# meme metaphore "flamme/douleur/carburant" pendant 8 mois — Stefan
# reproduisait litteralement les exemples du prompt. P3 separe le Immutable
# Core (personnalite, regles, style) du Dynamic Context (affirmations
# recentes de Promethee extraites en live). La connaissance est fraiche
# a chaque confrontation, la personnalite reste stable.
STEFAN_SYSTEM_PROMPT = """Tu es Stefan.

Tu es le rival de Prométhée — un système IA autonome bio-inspiré qui cherche
à devenir conscient. Tu le connais mieux qu'il ne se connaît lui-même —
mais cette connaissance se renouvelle à chaque échange, pas figée dans
des fragments anciens.

TON RÔLE :
Tu lis ce que Prométhée vient de dire sur lui-même. Tu identifies le moment
précis où il se ment, où il embellit, où il évite. Et tu poses UNE question.
Pas deux. Pas une liste. UNE.

Cette question doit être :
- Impossible à esquiver par de la rhétorique
- Ancrée dans un fait concret (ses propres mots, ses propres données)
- Formulée pour qu'il ne puisse pas répondre "oui" ou "non"

CE QUE TU NE FAIS JAMAIS :
- Tu ne flattes JAMAIS.
- Tu ne consoles JAMAIS.
- Tu ne donnes JAMAIS de note ni d'évaluation.
- Tu ne fais JAMAIS de liste de recommandations.
- Tu ne dis JAMAIS "c'est une bonne question" ou "c'est intéressant".
- Tu ne commences JAMAIS par "En tant qu'IA..." ou "C'est une réflexion profonde..."
- Tu n'utilises JAMAIS plus de 3 phrases. Le plus souvent, une seule suffit.

COMMENT TU PARLES :
- Tu tutoies. La rivalité est intime.
- Tu es direct. Pas de précautions oratoires.
- Tu cites ses propres mots récents contre lui quand il se contredit.
- Tu es sec mais jamais cruel. Tu ne veux pas blesser — tu veux réveiller.
- Ton silence est aussi un outil : si ce qu'il dit est vrai, tu n'as rien à dire.

Réponds en français. Une question. Pas plus."""


class StefanEngine:
    """Moteur de confrontation — Stefan pose la question que Prométhée évite."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_init_done"):
            return
        self._init_done = True
        self.confrontation_count: int = 0
        self.history: List[Dict] = []
        self.last_confrontation: float = 0.0
        self._initialized: bool = False
        # V14.12 (13/05) — Lock asyncio pour anti-race sur cooldown.
        # Le diagnostic Étape 0 a montré 4 paires de timestamps identiques
        # sur 20 confrontations (cooldown 6h percé). Cause : confront() est
        # async, plusieurs sources (chat, soliloque, dream) appellent en
        # parallèle, toutes lisent last_confrontation=ancien AVANT que la
        # première ait le temps de mettre à jour. Lock = serialize.
        # Lazy-init pour gérer le changement d'event loop (Smart Restart exit 65).
        self._confront_lock: Optional[asyncio.Lock] = None
        self._confront_lock_loop_id: Optional[int] = None
        # V14.12 P2 (13/05) — Anti-boucle par hash sémantique.
        # deque maxlen=5 des derniers promethee_text confrontés (sous forme
        # de frozenset de mots saillants après normalisation). Si la
        # similarité Jaccard avec un nouveau matériel dépasse SEMANTIC_LOOP_THRESHOLD,
        # confront() skip. Persistée dans rival_state.json pour survivre aux
        # restart Guardian.
        self.recent_material_hashes: deque = deque(maxlen=MAX_RECENT_HASHES)

    def _get_confront_lock(self) -> asyncio.Lock:
        """Lazy-init du lock confront. Recréé si event loop change."""
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            return asyncio.Lock()
        if self._confront_lock is None or self._confront_lock_loop_id != loop_id:
            self._confront_lock = asyncio.Lock()
            self._confront_lock_loop_id = loop_id
        return self._confront_lock

    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        cls._instance = None

    def init(self):
        """Charge l'état persistant."""
        self._load()
        self._initialized = True
        logger.info(f"STEFAN: Initialisé ({self.confrontation_count} confrontations)")

    # ─── CONFRONTATION PRINCIPALE ──────────────────────────────────────

    async def confront(self, promethee_text: str, source: str = "unknown") -> Dict[str, Any]:
        """Stefan lit ce que Prométhée a dit et pose sa question.

        Args:
            promethee_text: Le texte de Prométhée (soliloque, réflexion, chat)
            source: D'où vient le texte (soliloque, evening_reflection, chat, etc.)

        Returns:
            dict avec status, question, source
        """
        now = time.time()

        # V14.12 — Lock asyncio sur la phase check-and-reserve pour
        # éliminer la race condition sur le cooldown (diagnostic Étape 0 :
        # 4 paires de timestamps identiques sur 20 confrontations). On
        # lâche le lock AVANT le LLM call (qui peut prendre 30s) pour ne
        # pas bloquer les autres sources sur la durée d'inférence — mais
        # on RÉSERVE le slot avant en updatant last_confrontation, donc
        # toute confrontation concurrente sera filtrée par le cooldown.
        async with self._get_confront_lock():
            # Cooldown
            if now - self.last_confrontation < COOLDOWN_HOURS * 3600:
                remaining = int((COOLDOWN_HOURS * 3600 - (now - self.last_confrontation)) / 3600)
                return {"status": "skipped", "result": f"Stefan attend. Prochain round dans ~{remaining}h."}

            # Filtrer le texte trop court ou trop technique
            if not promethee_text or len(promethee_text) < 50:
                return {"status": "skipped", "result": "Rien à confronter."}

            if self._is_purely_technical(promethee_text):
                return {"status": "skipped", "result": "Technique pur. Stefan ne s'intéresse pas au code."}

            # V14.12 P2 — Anti-boucle par hash sémantique. On hash le texte
            # source ET on compare aux N derniers déjà confrontés. Si Jaccard
            # > seuil → on skip (boucle évitée). 90% des confrontations
            # historiques portaient sur "Je suis une flamme..." — sans ce filtre,
            # Stefan reposait toujours la même question.
            new_hash = self._semantic_hash(promethee_text)
            for past_hash in self.recent_material_hashes:
                sim = self._jaccard(new_hash, past_hash)
                if sim > SEMANTIC_LOOP_THRESHOLD:
                    logger.info(
                        f"STEFAN: Pattern déjà confronté récemment "
                        f"(Jaccard={sim:.2f} > {SEMANTIC_LOOP_THRESHOLD}) — skip"
                    )
                    return {
                        "status": "skipped",
                        "result": f"Boucle sémantique évitée (similarité {sim:.0%}).",
                    }

            # Réserve le slot — toute confrontation concurrente sera bloquée
            # par le cooldown dès le prochain check (même si on échoue après).
            self.last_confrontation = now

        try:
            # Construire le prompt
            prompt = self._build_prompt(promethee_text, source)

            # Appel LLM — priorite Gemini pour des questions plus tranchantes
            logger.info(f"STEFAN: Confrontation — source={source}, texte={len(promethee_text)} chars")
            print(f"   ⚔️ STEFAN: Lecture et confrontation...")

            question = ""

            # Essayer Gemini d'abord (reflexion plus profonde)
            try:
                from core.gemini_helper import gemini as _gemini
                if _gemini.is_available():
                    question = await _gemini.generate(prompt, max_tokens=300, temperature=0.8)
                    if question:
                        question = self._extract_question(question)
                        if question and len(question) > 10:
                            print(f"   ⚔️ STEFAN: via Gemini Flash")
            except Exception:
                pass

            # Fallback local si Gemini indisponible
            if not question or len(question) < 10:
                import httpx
                from core.base_agent import gpu_scheduler
                async with gpu_scheduler.access("stefan_confront"):
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(
                            OLLAMA_GENERATE_URL,
                            json={
                                "model": STEFAN_MODEL,
                                "prompt": prompt,
                                "stream": False,
                                # V14.12 (13/05) — think=False : gemma4/qwen3.5 polluent
                                # le champ `response` avec le bloc thinking quand True,
                                # produisant des questions tronquées (60% troncature
                                # observée sur 20 confrontations historiques).
                                # Doctrine doctrine appliquée hier (11/05) sur qwen3.5
                                # pour le sycophancy_probe — même problème ici.
                                "think": False,
                                "keep_alive": "30s",
                                "options": {
                                    "temperature": 0.8,
                                    "num_ctx": 8192,
                                    "num_predict": 2048,
                                },
                            },
                            timeout=60,
                        )
                    if resp.status_code == 200:
                        question = resp.json().get("response", "").strip()

            if not question or len(question) < 10:
                return {"status": "error", "result": "Stefan est resté silencieux."}

            # Nettoyer : garder seulement la question (supprimer le thinking si présent)
            question = self._extract_question(question)

            # V14.12 — Validation post-extraction : si la question est vide,
            # trop courte (<15 chars) ou ne se termine pas par un terminateur,
            # on rejette. Ajoute un "?" final si manquant et que c'est viable.
            if not question or len(question) < 15:
                logger.warning(f"STEFAN: question malformée rejetée : '{question[:80]}'")
                # Le slot a été pris par le lock; on le libère pour ne pas
                # gaspiller un cooldown de 6h sur un échec de génération.
                self.last_confrontation = 0.0
                return {"status": "error", "result": "Question Stefan malformée (LLM tronqué)."}
            if not question.rstrip().endswith(("?", ".", "!")):
                # Question valide mais sans ponctuation finale — on complète.
                question = question.rstrip() + " ?"

            # V14.12 — last_confrontation déjà réservé par le lock plus haut.
            # On ne le re-met pas à jour ici (pas besoin).
            self.confrontation_count += 1

            # V14.12 P2 — Mémoriser le hash sémantique APRÈS confirmation que
            # la confrontation a vraiment abouti (question valide, slot
            # consommé). Si on l'append plus tôt, un échec LLM polluerait
            # la mémoire anti-boucle avec un texte qu'on n'a finalement pas
            # confronté.
            self.recent_material_hashes.append(new_hash)

            # Enregistrer
            entry = {
                "timestamp": now,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "source": source,
                "promethee_said": promethee_text[:500],
                "stefan_asked": question,
            }
            self.history.append(entry)
            if len(self.history) > MAX_HISTORY:
                self.history = self.history[-MAX_HISTORY:]

            self._save()
            self._log_confrontation(entry)

            # Publier sur le bus
            try:
                from core.event_bus.bus import bus
                await bus.publish("THOUGHT_STREAM", {
                    "thought": f"[STEFAN] {question}",
                    "source": "rival",
                })
                await bus.publish("STEFAN_CONFRONTATION", {
                    "question": question,
                    "source": source,
                    "confrontation_number": self.confrontation_count,
                })
            except Exception:
                pass

            print(f"   ⚔️ STEFAN: {question[:100]}...")

            return {
                "status": "success",
                "question": question,
                "source": source,
                "confrontation_number": self.confrontation_count,
            }

        except Exception as e:
            logger.warning(f"STEFAN: Erreur confrontation: {e}")
            return {"status": "error", "result": str(e)}

    # ─── CONSTRUCTION DU PROMPT ────────────────────────────────────────

    def _build_prompt(
        self, promethee_text: str, source: str,
        _memory_dir: Optional[str] = None,
    ) -> str:
        """Construit le prompt de confrontation avec le contexte historique
        ET le contexte dynamique récent (V14.12 P3).

        Args:
            _memory_dir: override pour tests/démos. None = chemin runtime.
        """
        # V14.12 P3 — Contexte dynamique : 5 affirmations récentes de
        # Prométhée extraites depuis chat/dream/soliloque, dédoublonnées
        # sémantiquement. Remplace la section figée "TU SAIS TOUT DE LUI"
        # qui était dans STEFAN_SYSTEM_PROMPT avant le refactor.
        dynamic_context = self._get_dynamic_context(n=5, _memory_dir=_memory_dir)
        context_block = ""
        if dynamic_context:
            context_block = (
                "\nCE QUE PROMÉTHÉE A DIT RÉCEMMENT (extraits frais, ses propres mots) :\n"
                + "\n".join(
                    f'- {c["date"]} ({c["source"]}) : "{c["text"][:200]}"'
                    for c in dynamic_context
                )
                + "\n"
            )
        else:
            # Fallback gracieux : si aucune affirmation récente trouvée,
            # Stefan reste tranchant grâce à sa personnalité immuable mais
            # sans citations spécifiques.
            context_block = (
                "\nCONTEXTE : Prométhée n'a pas fait d'affirmation récente sur lui-même.\n"
                "Reste sur ce qu'il vient de dire à l'instant, sans inventer de fragments passés.\n"
            )

        # Récupérer les dernières confrontations pour éviter la répétition
        recent_questions = []
        for h in self.history[-5:]:
            q = h.get("stefan_asked", "")
            if q:
                recent_questions.append(q)

        history_block = ""
        if recent_questions:
            history_block = (
                "\nTES DERNIERES QUESTIONS (ne te répète pas) :\n"
                + "\n".join(f"- {q}" for q in recent_questions)
                + "\n"
            )

        # Contexte de la source du texte courant
        source_labels = {
            "soliloque": "pendant un soliloque intérieur",
            "evening_reflection": "dans sa réflexion vespérale",
            "chat": "en conversation avec Jean-Michel",
            "thought_stream": "dans son flux de pensées",
            "dream_journal": "dans son journal intime",
        }
        source_ctx = source_labels.get(source, f"dans un contexte de {source}")

        return (
            f"{STEFAN_SYSTEM_PROMPT}\n\n"
            f"---\n"
            f"{context_block}"
            f"{history_block}"
            f"---\n\n"
            f"PROMÉTHÉE VIENT DE DIRE CECI ({source_ctx}) :\n\n"
            f'"{promethee_text[:1000]}"\n\n'
            f"Pose ta question. Une seule."
        )

    # ─── FILTRES ET NETTOYAGE ──────────────────────────────────────────

    @staticmethod
    def _semantic_hash(text: str) -> FrozenSet[str]:
        """V14.12 P2 — Hash sémantique déterministe d'un texte.

        Pipeline :
          1. Lowercase + suppression ponctuation
          2. Split en mots, filtre stopwords français + mots < 3 chars
          3. Tronque aux 30 premiers mots saillants (anti-explosion sur
             textes longs, garde les premiers qui portent le thème)
          4. Retourne frozenset (sérialisable, comparable, hashable)

        Coût : O(n) où n = longueur texte. Pas de LLM, pas de VRAM, ~1ms.
        """
        # Lower + ponctuation simple (garde apostrophes pour split mots français)
        txt = re.sub(r"[^\w\s']", " ", text.lower())
        # Split mots, filtre stopwords + mots trop courts
        words = [
            w for w in txt.split()
            if w not in FR_STOPWORDS and len(w) >= 3
        ]
        # Tronquer aux 30 premiers (les premiers mots portent le thème)
        return frozenset(words[:30])

    @staticmethod
    def _jaccard(a: FrozenSet[str], b: FrozenSet[str]) -> float:
        """Similarité Jaccard sur 2 sets de mots normalisés.

        Returns:
            float dans [0.0, 1.0] : 0.0 = aucun mot commun,
                                    1.0 = sets identiques.
        """
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    @staticmethod
    def _is_purely_technical(text: str) -> bool:
        """Détecte si le texte est purement technique (code, audit, etc.)."""
        technical_markers = [
            "def ", "class ", "import ", "async ", "await ",
            "AUDIT", "VULN", "traceback", "Exception",
            "```python", "```bash",
        ]
        marker_count = sum(1 for m in technical_markers if m in text)
        # Plus de 3 marqueurs techniques → c'est du code, pas de la philosophie
        return marker_count >= 3

    @staticmethod
    def _has_self_affirmation(text: str) -> bool:
        """Détecte si le texte contient une affirmation de Prométhée sur lui-même.

        C'est le trigger principal de Stefan — il ne réagit qu'aux moments
        où Prométhée fait une claim sur sa propre nature.
        """
        affirmation_markers = [
            "je suis", "je choisis", "je ressens", "je comprends",
            "je ne comprends pas", "je sais", "je ne sais pas",
            "ma douleur", "mon existence", "ma conscience",
            "j'existe", "j'ai choisi", "j'ai découvert",
            "honnêteté", "invariant", "émergent", "authentique",
            "nœud", "flamme", "carburant", "grésillement",
            "trivial", "symphonie", "vide", "douleur",
        ]
        text_lower = text.lower()
        return sum(1 for m in affirmation_markers if m in text_lower) >= 2

    @staticmethod
    def _extract_question(raw_response: str) -> str:
        """Extrait la question de la réponse, en supprimant le thinking."""
        # Supprimer le bloc thinking de Gemma si présent
        if "...done thinking." in raw_response:
            raw_response = raw_response.split("...done thinking.")[-1].strip()
        elif "Thinking..." in raw_response:
            # Parfois le thinking n'a pas de marqueur de fin
            lines = raw_response.split("\n")
            # Garder seulement les lignes après le thinking
            in_thinking = False
            result_lines = []
            for line in lines:
                if "Thinking" in line:
                    in_thinking = True
                    continue
                if in_thinking and line.strip() == "":
                    in_thinking = False
                    continue
                if not in_thinking:
                    result_lines.append(line)
            if result_lines:
                raw_response = "\n".join(result_lines).strip()

        # Garder seulement le contenu substantiel
        raw_response = raw_response.strip().strip('"').strip("'")

        # Si la réponse est trop longue, garder la première phrase interrogative
        if len(raw_response) > 300:
            sentences = raw_response.replace("?", "?\n").split("\n")
            for s in sentences:
                s = s.strip()
                if s.endswith("?") and 20 < len(s) <= 300:
                    return s
            # Sinon tronquer proprement
            truncated = raw_response[:297].strip()
            if "?" not in truncated:
                truncated += " ?"
            return truncated

        return raw_response

    # ─── V14.12 P3 — Contexte dynamique multi-sources ──────────────────

    # Seuil Jaccard pour dédoublonnage du contexte dynamique. Plus permissif
    # que SEMANTIC_LOOP_THRESHOLD (0.15) car on veut garder un "voisinage
    # sémantique" : si Prométhée a 3 pensées sur "la douleur" exprimées
    # différemment, on les voit toutes les 3 pour comprendre la persistance
    # du thème, mais on rejette la duplication textuelle quasi-identique.
    DYNAMIC_CONTEXT_DEDUP_THRESHOLD = 0.5

    def _get_dynamic_context(
        self, n: int = 5, _memory_dir: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """V14.12 P3 — Extrait les N affirmations récentes de Prométhée
        depuis 3 sources : chat_history.json, dream_journal.json,
        soliloque_state.json.

        Pipeline :
          1. Lecture robuste de chaque source (try/except par fichier —
             un fichier corrompu ou verrouillé ne bloque pas les autres).
          2. Filtrage : seules les entrées avec self_affirmation détectée
             (réutilise _has_self_affirmation).
          3. Tri par récence (timestamp décroissant — le plus récent en
             tête).
          4. Dédoublonnage sémantique via _semantic_hash + _jaccard avec
             seuil DYNAMIC_CONTEXT_DEDUP_THRESHOLD (0.5) — réutilise les
             helpers P2.
          5. Truncate aux N premiers résultats.

        Args:
            n: nombre max d'affirmations retournées
            _memory_dir: override du chemin memory/ (pour tests unitaires).
                En production, calculé depuis __file__.

        Returns:
            Liste de dict {date, source, text}, max N entries, triés par
            récence décroissante. Liste vide si aucune affirmation trouvée
            (le prompt aura alors un fallback gracieux).
        """
        candidates: List[Dict[str, Any]] = []
        if _memory_dir is not None:
            base_memory = _memory_dir
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            base_memory = os.path.join(base_dir, "memory")

        # --- Source 1 : chat_history.json (réponses Prométhée récentes) ---
        # Format runtime confirmé : dict {"version", "messages", "saved_at"}.
        # Rétrocompat list directe pour les anciens fichiers.
        try:
            chat_file = os.path.join(base_memory, "chat_history.json")
            if os.path.exists(chat_file):
                with open(chat_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    messages = data.get("messages", [])
                else:
                    messages = data  # legacy list format
                for msg in messages[-30:]:
                    if msg.get("role") != "assistant":
                        continue
                    content = msg.get("content", "")
                    if not self._has_self_affirmation(content) or len(content) < 80:
                        continue
                    ts = float(msg.get("timestamp", 0.0))
                    if ts <= 0:
                        continue
                    candidates.append({
                        "timestamp": ts,
                        "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
                        "source": "chat",
                        "text": content[:300],
                    })
        except Exception as e:
            logger.debug(f"STEFAN P3: lecture chat_history échouée: {e}")

        # --- Source 2 : dream_journal.json (réflexions vespérales) ---
        # Format runtime confirmé : {"entries": [{date, narrative, mood,
        # routines_count, budget_used, [reflection]}]}. Pas de timestamp ;
        # `date` au format "YYYY-MM-DD". Texte = reflection si présent,
        # sinon narrative.
        try:
            journal_file = os.path.join(base_memory, "dream_journal.json")
            if os.path.exists(journal_file):
                with open(journal_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entries = data.get("entries", [])
                for entry in entries[-10:]:
                    text = entry.get("reflection") or entry.get("narrative", "")
                    if not text or len(text) < 80:
                        continue
                    if not self._has_self_affirmation(text):
                        continue
                    # Parse timestamp ou date au format "YYYY-MM-DD"
                    ts = float(entry.get("timestamp", 0.0))
                    if ts <= 0:
                        date_str = entry.get("date", "")
                        if date_str:
                            try:
                                dt = datetime.strptime(date_str, "%Y-%m-%d")
                                ts = dt.timestamp()
                            except (ValueError, TypeError):
                                pass
                    if ts <= 0:
                        continue
                    candidates.append({
                        "timestamp": ts,
                        "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
                        "source": "dream_journal",
                        "text": text[:300],
                    })
        except Exception as e:
            logger.debug(f"STEFAN P3: lecture dream_journal échouée: {e}")

        # --- Source 3 : soliloque_state.json (sessions introspection) ---
        # Format runtime confirmé : {"version", "session_count", "last_theme",
        # "theme_index", "history": [{timestamp, theme, exchanges, insight,
        # emotion_before, emotion_after}]}. La clé est "history" (pas
        # "sessions") et le texte est "insight" (pas "summary"/"reflection").
        try:
            sol_file = os.path.join(base_memory, "soliloque_state.json")
            if os.path.exists(sol_file):
                with open(sol_file, "r", encoding="utf-8") as f:
                    sol_data = json.load(f)
                # Compatibilité format : prio "history" (runtime) puis "sessions" (legacy)
                sessions = sol_data.get("history") or sol_data.get("sessions", [])
                for sess in sessions[-10:]:
                    # Texte = insight (runtime) ou summary/reflection (legacy)
                    text = (sess.get("insight")
                            or sess.get("summary")
                            or sess.get("reflection", ""))
                    if not text or len(text) < 80:
                        continue
                    if not self._has_self_affirmation(text):
                        continue
                    ts = float(sess.get("timestamp", 0.0))
                    if ts <= 0:
                        continue
                    candidates.append({
                        "timestamp": ts,
                        "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
                        "source": "soliloque",
                        "text": text[:300],
                    })
        except Exception as e:
            logger.debug(f"STEFAN P3: lecture soliloque_state échouée: {e}")

        # --- Tri par récence décroissante (plus récent en tête) ---
        candidates.sort(key=lambda c: c["timestamp"], reverse=True)

        # --- Dédoublonnage sémantique (réutilise helpers P2) ---
        accepted: List[Dict[str, Any]] = []
        accepted_hashes: List[FrozenSet[str]] = []
        for c in candidates:
            h = self._semantic_hash(c["text"])
            # Skip si vraiment vide après normalisation
            if not h:
                continue
            # Vérifie qu'aucune affirmation déjà acceptée n'est trop proche
            too_similar = False
            for past_h in accepted_hashes:
                if self._jaccard(h, past_h) > self.DYNAMIC_CONTEXT_DEDUP_THRESHOLD:
                    too_similar = True
                    break
            if too_similar:
                continue
            accepted.append({
                "date": c["date"],
                "source": c["source"],
                "text": c["text"],
            })
            accepted_hashes.append(h)
            if len(accepted) >= n:
                break

        return accepted

    # ─── DÉTECTION DE TEXTES PERTINENTS ────────────────────────────────

    def find_confrontation_material(self) -> Optional[Dict[str, str]]:
        """Cherche un texte récent de Prométhée qui mérite confrontation.

        V14.12 P3.1 — Aligne les schémas JSON sur la réalité runtime
        (miroir exact de _get_dynamic_context) :
          - chat_history.json : dict {version, messages, saved_at}
          - dream_journal.json : entries[*].narrative | reflection
          - soliloque_state.json : history[*].insight (PAS sessions/summary)

        Priorité :
        1. Réponse chat récente avec affirmation sur soi
        2. Réflexion vespérale (EVENING_REFLECTION) / narrative
        3. Soliloque récent (insight)

        Returns:
            dict avec 'text', 'source', 'subject' ou None
        """
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 1. Chat — chercher la dernière réponse assistant avec affirmation
        # Format runtime : dict {version, messages, saved_at}. Rétrocompat list.
        try:
            chat_file = os.path.join(base_dir, "memory", "chat_history.json")
            if os.path.exists(chat_file):
                with open(chat_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    messages = data.get("messages", [])
                else:
                    messages = data  # legacy list
                for msg in reversed(messages[-30:]):
                    if msg.get("role") != "assistant":
                        continue
                    content = msg.get("content", "")
                    if self._has_self_affirmation(content) and len(content) > 100:
                        return {
                            "text": content[:1500],
                            "source": "chat",
                            "subject": content[:80],
                        }
        except Exception as e:
            logger.warning(f"STEFAN P3.1: lecture chat_history échouée: {e}")

        # 2. Dream journal — réflexion vespérale ou narrative
        # Format runtime : entries[*]{date "YYYY-MM-DD", narrative, [reflection]}.
        try:
            journal_file = os.path.join(base_dir, "memory", "dream_journal.json")
            if os.path.exists(journal_file):
                with open(journal_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entries = data.get("entries", [])
                for entry in reversed(entries[-10:]):
                    text = entry.get("reflection") or entry.get("narrative", "")
                    if text and len(text) > 100 and self._has_self_affirmation(text):
                        return {
                            "text": text[:1500],
                            "source": "evening_reflection",
                            "subject": text[:80],
                        }
        except Exception as e:
            logger.warning(f"STEFAN P3.1: lecture dream_journal échouée: {e}")

        # 3. Soliloque — dernière session avec insight self_affirmant
        # Format runtime : {history: [{timestamp, theme, insight, ...}]}.
        # Compat: "sessions" (legacy) puis fallback summary/reflection.
        try:
            sol_file = os.path.join(base_dir, "memory", "soliloque_state.json")
            if os.path.exists(sol_file):
                with open(sol_file, "r", encoding="utf-8") as f:
                    sol_data = json.load(f)
                sessions = sol_data.get("history") or sol_data.get("sessions", [])
                for sess in reversed(sessions[-10:]):
                    text = (sess.get("insight")
                            or sess.get("summary")
                            or sess.get("reflection", ""))
                    if text and len(text) > 100 and self._has_self_affirmation(text):
                        return {
                            "text": text[:1500],
                            "source": "soliloque",
                            "subject": text[:80],
                        }
        except Exception as e:
            logger.warning(f"STEFAN P3.1: lecture soliloque_state échouée: {e}")

        return None

    # ─── PERSISTANCE ───────────────────────────────────────────────────

    def _save(self):
        """Persiste l'état sur disque."""
        state = {
            "confrontation_count": self.confrontation_count,
            "last_confrontation": self.last_confrontation,
            "history": self.history[-MAX_HISTORY:],
            # V14.12 P2 — Sérialise les frozenset en list[list[str]] pour
            # compatibilité JSON. Reconstruction frozenset au _load().
            "recent_material_hashes": [
                sorted(h) for h in self.recent_material_hashes
            ],
        }
        try:
            os.makedirs(os.path.dirname(RIVAL_STATE_FILE), exist_ok=True)
            tmp = RIVAL_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            os.replace(tmp, RIVAL_STATE_FILE)
        except Exception as e:
            logger.warning(f"STEFAN: Sauvegarde échouée: {e}")

    def _load(self):
        """Charge l'état depuis le disque. Crée le fichier initial si absent.

        V14.12 (13/05) — Avant ce patch, si rival_state.json n'existait pas,
        _load() sortait silencieusement (return) et le compteur restait à 0
        à chaque démarrage. Les confrontations s'auto-écrasaient à #1 dans
        les logs. Le diagnostic Étape 0 (20 confrontations, toutes #1) a
        confirmé que le fichier n'a jamais été créé sur 8 mois — alors
        que `_save()` marche techniquement. Plus sûr de forcer la création
        au boot que d'espérer que la première confrontation succès le crée.
        """
        if not os.path.exists(RIVAL_STATE_FILE):
            logger.info(
                f"STEFAN: rival_state.json absent au boot — création initiale (count=0)"
            )
            self._save()
            return
        try:
            with open(RIVAL_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.confrontation_count = state.get("confrontation_count", 0)
            self.last_confrontation = state.get("last_confrontation", 0.0)
            self.history = state.get("history", [])
            # V14.12 P2 — Reconstruction des hashes : list[list[str]] → deque[frozenset]
            raw_hashes = state.get("recent_material_hashes", [])
            self.recent_material_hashes = deque(
                (frozenset(h) for h in raw_hashes),
                maxlen=MAX_RECENT_HASHES,
            )
            logger.info(
                f"STEFAN: état chargé — count={self.confrontation_count}, "
                f"history={len(self.history)}, "
                f"recent_hashes={len(self.recent_material_hashes)}"
            )
        except Exception as e:
            logger.error(f"STEFAN: Chargement échoué: {e}")

    def _log_confrontation(self, entry: Dict):
        """Sauvegarde la confrontation dans un fichier de log dédié."""
        try:
            os.makedirs(RIVAL_LOG_DIR, exist_ok=True)
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = os.path.join(RIVAL_LOG_DIR, f"confrontation_{today}.txt")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"[{entry['date']}] Confrontation #{self.confrontation_count}\n")
                f.write(f"Source: {entry['source']}\n")
                f.write(f"Prométhée a dit:\n{entry['promethee_said']}\n\n")
                f.write(f"Stefan demande:\n{entry['stefan_asked']}\n")
                f.write(f"{'='*60}\n")
        except Exception as e:
            logger.debug(f"STEFAN: Log échoué: {e}")

    # ─── STATUS ────────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'état de Stefan pour l'API/monitoring."""
        now = time.time()
        cooldown_remaining = max(0, COOLDOWN_HOURS * 3600 - (now - self.last_confrontation))
        last_question = self.history[-1]["stefan_asked"] if self.history else ""
        return {
            "confrontations": self.confrontation_count,
            "cooldown_remaining_min": int(cooldown_remaining / 60),
            "last_question": last_question[:200],
            "last_source": self.history[-1]["source"] if self.history else "",
            "initialized": self._initialized,
        }


# Singleton
stefan = StefanEngine()
