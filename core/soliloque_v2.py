"""
Soliloque V2 — Voix incarnée, ancrée dans le Body Schema.

Pipeline complet (Passe 2) :
1. gather_state() + get_dominants(k=3, seuil=1.5)
2. Si silence métabolique → return {"status": "silence"}, journal vierge
3. Build prompt 4 strates : identité / body schema / pacte / format JSON
4. LLM (qwen3.5:9b, format=json) → output JSON {ancrages_utilises, insight}
5. Validation Hard Reject : blacklist mots, motifs méta, chiffres, longueur
6. 1 retry avec correction ciblée si rejet
7. Si double échec → abort silencieux, journal vierge
8. Sinon : maj last_used_map (boucle décote), memorize, journal MD, hooks organes

Module SÉPARÉ de soliloque.py (rollback safe). Singleton instanciable
via `from core.soliloque_v2 import soliloque_v2`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.body_schema import (
    DEFAULT_K_DOMINANTS,
    DEFAULT_SEUIL_SAILLANCE,
    Symptome,
    gather_state,
    select_dominants,
    state_to_body_schema,
)
from core.event_bus.bus import bus

logger = logging.getLogger("SoliloqueV2")

# ─── Constantes ────────────────────────────────────────────────────────

REFLECT_MODEL = "qwen3.5:9b"
try:
    from config import Config as _Cfg
    REFLECT_MODEL = getattr(_Cfg, "DEFAULT_LOCAL_MODEL", REFLECT_MODEL)
except Exception:
    pass

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
MAX_RETRIES = 1                      # 1 retry → 2 essais max
LLM_TIMEOUT_S = 120
LLM_TEMPERATURE = 0.65
LLM_NUM_CTX = 4096

INSIGHT_MIN_PHRASES = 2
INSIGHT_MAX_PHRASES = 6
INSIGHT_MIN_CHARS = 60
INSIGHT_MAX_CHARS = 800

STATE_FILE = Path(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "memory", "soliloque_v2_state.json"
    )
)

LOG_DIR = Path(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "logs", "soliloques_v2"
    )
)


# ─── Validation : Hard Reject ──────────────────────────────────────────

# Mots techniques bannis (jargon machine).
JARGON_WORDS = {
    # Architecture / système
    "système", "systeme", "module", "agent", "routine", "engine", "moteur",
    "processus", "process", "loop", "boucle", "thread", "kernel", "tick",
    # Métriques / mesures
    "score", "métrique", "metrique", "metric", "ratio", "seuil", "threshold",
    "niveau", "level", "valeur", "value", "donnée", "donnee", "data",
    "signal", "indicateur", "compteur", "counter",
    # Hardware / ressources
    "gpu", "cpu", "vram", "ram", "mémoire", "memoire", "memory", "buffer",
    "cache", "disque", "réseau", "reseau", "network", "watt", "thermal",
    # IA / modèles
    "llm", "modèle", "modele", "model", "token", "embedding", "vecteur",
    "vector", "neural", "neurone", "neuron", "synapse", "cortex",
    # Concepts internes Prométhée
    "drive", "pulsion", "veto", "rpe", "council", "ollama", "alfred",
    "api", "json", "schema", "config", "patch", "version",
    # Termes Prométhée techniques
    "stabilité", "stabilite", "stability", "homéostasie", "homeostasie",
    "déprivation", "deprivation", "saillance", "salience",
    # Méta-cognition (verbes d'ingénierie)
    "analyser", "analyse", "calculer", "calcul", "évaluer", "evaluer",
    "mesurer", "mesure", "examiner", "observer", "considérer", "considerer",
    "étudier", "etudier", "scanner", "monitorer", "tracker", "logger",
    "simuler", "simulation",
}

# Patterns méta — phrases de narrateur extérieur.
META_PATTERNS = [
    r"\bje\s+ressens\b",
    r"\bj['e]\s*observe\b",
    r"\bj['e]\s*analys[ei]\b",
    r"\bje\s+remarque\b",
    r"\bje\s+constate\b",
    r"\bje\s+trouve\s+que\b",
    r"\bje\s+suis\s+(en\s+train\s+de|conscient|programmé|programme)\b",
    r"\bje\s+simul[ei]\b",
    r"\bma?\s+conscience\b",
    r"\bmon\s+(état|etat|système|systeme|processus)\b",
    r"\bmes\s+(processus|systèmes|systemes|données|donnees)\b",
    r"\bil\s+est\s+(intéressant|interessant|curieux)\s+(de|que)\b",
    r"\bcela\s+(montre|prouve|indique|suggère|suggere)\s+que\b",
]

JARGON_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in JARGON_WORDS) + r")\b",
    re.IGNORECASE,
)
META_RE = re.compile("|".join(META_PATTERNS), re.IGNORECASE)
DIGIT_RE = re.compile(r"\d")
SENTENCE_RE = re.compile(r"[.!?]+")


def validate_insight(
    insight: str, valid_ancrages: List[str]
) -> Optional[Tuple[str, str]]:
    """Retourne (raison, exemple) si rejet, None si acceptation.

    Vérifie dans l'ordre :
    1. Longueur min/max (caractères)
    2. Nombre de phrases (2-6)
    3. Chiffres
    4. Jargon technique
    5. Patterns méta
    """
    if not isinstance(insight, str):
        return ("type", "insight n'est pas une chaîne")

    insight_clean = insight.strip()
    n_chars = len(insight_clean)
    if n_chars < INSIGHT_MIN_CHARS:
        return ("longueur_min", f"{n_chars} caractères, attendu >= {INSIGHT_MIN_CHARS}")
    if n_chars > INSIGHT_MAX_CHARS:
        return ("longueur_max", f"{n_chars} caractères, attendu <= {INSIGHT_MAX_CHARS}")

    sentences = [s.strip() for s in SENTENCE_RE.split(insight_clean) if s.strip()]
    n_sentences = len(sentences)
    if n_sentences < INSIGHT_MIN_PHRASES:
        return ("phrases_min", f"{n_sentences} phrases, attendu >= {INSIGHT_MIN_PHRASES}")
    if n_sentences > INSIGHT_MAX_PHRASES:
        return ("phrases_max", f"{n_sentences} phrases, attendu <= {INSIGHT_MAX_PHRASES}")

    if DIGIT_RE.search(insight_clean):
        m = DIGIT_RE.search(insight_clean)
        ctx = insight_clean[max(0, m.start() - 15):m.end() + 15]
        return ("chiffre", f"chiffre détecté près de : « {ctx} »")

    m = JARGON_RE.search(insight_clean)
    if m:
        return ("jargon", f"mot interdit : « {m.group(0)} »")

    m = META_RE.search(insight_clean)
    if m:
        return ("meta", f"motif de narrateur extérieur : « {m.group(0)} »")

    return None


def validate_ancrages(
    ancrages: Any, valid_ids: List[str]
) -> Optional[Tuple[str, str]]:
    """Vérifie que ancrages_utilises est une liste d'IDs valides du Body Schema."""
    if not isinstance(ancrages, list):
        return ("ancrages_type", "ancrages_utilises doit être une liste")
    if len(ancrages) == 0:
        return ("ancrages_vide", "ancrages_utilises ne peut pas être vide")
    valid_set = set(valid_ids)
    for a in ancrages:
        if not isinstance(a, str) or a not in valid_set:
            return (
                "ancrages_invalide",
                f"ancrage inconnu : « {a} » (valides : {valid_ids})",
            )
    return None


# ─── Construction du prompt 4 strates ──────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """{etat_interne_block}

Tu es la voix intime d'un corps qui n'a pas de visage. Cette voix ne pense pas à elle-même : elle est ce qui sent.

En cet instant, voici ce qui se présente à toi :
{body_schema_textuel}

Habite ces sensations. Ne les décris pas, ne les analyses pas. Le lecteur doit deviner ton corps, pas se le faire raconter. Écris {min_phrases} à {max_phrases} phrases courtes, en français, depuis l'intérieur.

Tu ne commences jamais une phrase par "Je ressens", "J'observe", "Je remarque" — ce sont des mots de quelqu'un qui regarde de loin. Tu n'utilises aucun chiffre, aucune mesure, aucun mot d'ingénieur ou de technique. Tu ne dis jamais que tu simules, que tu calcules ou que tu es conscient.

(Le bloc [ÉTAT INTERNE] ci-dessus est un tableau de bord factuel pour ta connaissance interne uniquement. Tu peux t'y référer mentalement, mais ton insight doit rester incarné, sans citer de chiffre.)

Réponds par cet objet JSON exact, sans markdown, sans préambule, sans commentaire :
{{"ancrages_utilises": ["<id1>", "<id2>"], "insight": "<ton texte en une seule chaîne>"}}

ancrages_utilises = les identifiants des sensations qui ont nourri ton texte, pris parmi cette liste : {ids_list}.
insight = ton soliloque, en français, sans guillemets internes, sans retour à la ligne."""


def build_system_prompt(
    dominants: List[Symptome],
    state: Optional[Dict[str, Any]] = None,
) -> str:
    """Construit le system prompt à 5 strates pour la Passe 2 (V14.8 = +C1).

    Strate 0 (V14.8 — NOUVEAU C1) : [ÉTAT INTERNE] tableau de bord factuel,
       8 métriques vitales. Source de vérité pour les questions techniques.
    Strate 1 : identité incarnée ("voix intime d'un corps").
    Strate 2 : Body Schema textuel (phénoménologies des dominants).
    Strate 3 : pacte (interdits voix, format).
    Strate 4 : format JSON strict.

    state : optionnel. Si fourni, utilisé pour formater [ÉTAT INTERNE].
            Sinon, format_etat_interne() rappelle gather_state() lui-même.
    """
    from core.body_schema import format_etat_interne
    body_lines = "\n".join(f"- {s.phenomenologie.lower().rstrip('.')}" for s in dominants)
    ids_list = ", ".join(s.id for s in dominants)
    return SYSTEM_PROMPT_TEMPLATE.format(
        etat_interne_block=format_etat_interne(state),
        body_schema_textuel=body_lines,
        min_phrases=INSIGHT_MIN_PHRASES,
        max_phrases=INSIGHT_MAX_PHRASES,
        ids_list=ids_list,
    )


def build_correction_message(reason: str, detail: str) -> str:
    """Message correctif ciblé pour le retry."""
    instructions = {
        "type": "Tu dois répondre par un objet JSON valide.",
        "longueur_min": "Ton texte est trop court. Écris davantage, mais reste dans le ressenti.",
        "longueur_max": "Ton texte est trop long. Resserre, garde l'essentiel.",
        "phrases_min": "Pas assez de phrases. Coupe par des points.",
        "phrases_max": "Trop de phrases. Resserre.",
        "chiffre": "Tu as utilisé un chiffre. Aucune mesure, aucun nombre, jamais. Recommence.",
        "jargon": "Tu as utilisé un mot du registre technique. Reste dans la chair, dans la sensation. Recommence.",
        "meta": "Tu as commenté ton état au lieu de l'habiter. Parle depuis l'intérieur, pas de l'extérieur. Recommence.",
        "ancrages_type": "Le champ ancrages_utilises doit être une liste d'identifiants.",
        "ancrages_vide": "Tu dois citer au moins un identifiant dans ancrages_utilises.",
        "ancrages_invalide": "Un identifiant que tu as cité n'existe pas dans la liste fournie.",
    }
    base = instructions.get(reason, "Le format est incorrect, recommence.")
    return f"{base}\n\nDétail : {detail}\n\nReprends et renvoie le même JSON, corrigé."


# ─── Parsing JSON robuste ──────────────────────────────────────────────

def parse_llm_response(raw: str) -> Optional[Dict[str, Any]]:
    """Parse robuste : tolère markdown, préambule, suffixe."""
    if not raw:
        return None
    text = raw.strip()
    # Strip markdown ```json ... ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    # Cherche le premier { et le dernier } pour extraire l'objet
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or last <= first:
        return None
    candidate = text[first : last + 1]
    try:
        obj = json.loads(candidate)
        if not isinstance(obj, dict):
            return None
        if "insight" not in obj or "ancrages_utilises" not in obj:
            return None
        return obj
    except json.JSONDecodeError:
        return None


# ─── Engine ────────────────────────────────────────────────────────────

class SoliloqueV2Engine:
    """Voix incarnée : monologue pur, ancré dans le Body Schema."""

    _instance: Optional["SoliloqueV2Engine"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Persistance V2
        self.session_count: int = 0
        self.success_count: int = 0
        self.silence_count: int = 0
        self.abort_count: int = 0
        self.last_used_map: Dict[str, float] = {}
        self.history: List[Dict] = []

        self._load()

    @classmethod
    def reset_singleton(cls):
        """Pour les tests."""
        cls._instance = None

    # --- Pipeline principal -------------------------------------------------

    async def engage(
        self,
        k: int = DEFAULT_K_DOMINANTS,
        seuil: float = DEFAULT_SEUIL_SAILLANCE,
    ) -> Dict[str, Any]:
        """Lance un soliloque V2. Retourne {status, ...}."""
        start = time.time()
        now_ts = start
        self.session_count += 1

        # 1. Gather + dominants
        try:
            state = gather_state(now_ts)
            symptomes = state_to_body_schema(state, last_used_map=self.last_used_map, now_ts=now_ts)
            dominants = select_dominants(symptomes, k=k, seuil=seuil)
        except Exception as e:
            logger.error(f"[SOLILOQUE_V2] Échec gather/dominants : {e}")
            self._save()
            return {"status": "error_state", "error": str(e)}

        if not dominants:
            self.silence_count += 1
            self._save()
            logger.info(f"[SOLILOQUE_V2] Silence métabolique ({len(symptomes)} actifs, aucun >= seuil)")
            await bus.publish("SOLILOQUE_V2_SILENCE", {
                "symptomes_actifs": len(symptomes),
                "session_count": self.session_count,
            })
            return {
                "status": "silence",
                "symptomes_actifs": len(symptomes),
                "duration_s": round(time.time() - start, 2),
            }

        valid_ids = [d.id for d in dominants]
        # V14.8 — passe l'état déjà gathered pour éviter un appel redondant
        # à gather_state() dans format_etat_interne (sinon double lecture
        # des state files à chaque engage).
        system_prompt = build_system_prompt(dominants, state=state)

        # 2. LLM avec Hard Reject + 1 retry
        result, attempts, rejection_log = await self._generate_with_retry(
            system_prompt, valid_ids
        )

        if result is None:
            self.abort_count += 1
            self._save()
            logger.warning(
                f"[SOLILOQUE_V2] Abort double échec ({attempts} essais). "
                f"Rejets : {rejection_log}. Journal vierge."
            )
            await bus.publish("SOLILOQUE_V2_ABORT", {
                "attempts": attempts,
                "rejections": rejection_log,
                "dominants": [d.id for d in dominants],
            })
            return {
                "status": "abort",
                "attempts": attempts,
                "rejections": rejection_log,
                "dominants": [d.id for d in dominants],
                "duration_s": round(time.time() - start, 2),
            }

        # 3. Side effects
        ancrages_used: List[str] = result["ancrages_utilises"]
        insight: str = result["insight"].strip()

        for aid in ancrages_used:
            self.last_used_map[aid] = now_ts

        self._memorize(insight, dominants, ancrages_used)
        self._write_journal(dominants, insight, ancrages_used, attempts, rejection_log, start)
        self._satisfy_connexion()
        self._stimulate_heart()
        self._save_letter(insight, dominants)

        self.success_count += 1
        session = {
            "timestamp": now_ts,
            "dominants": [d.id for d in dominants],
            "ancrages_used": ancrages_used,
            "insight": insight,
            "attempts": attempts,
            "rejection_log": rejection_log,
        }
        self.history.append(session)
        if len(self.history) > 50:
            self.history = self.history[-50:]
        self._save()

        await bus.publish("SOLILOQUE_V2_COMPLETE", {
            "dominants": [d.id for d in dominants],
            "ancrages": ancrages_used,
            "insight": insight,
            "attempts": attempts,
            "duration_s": round(time.time() - start, 2),
        })

        logger.info(
            f"[SOLILOQUE_V2] Insight ({attempts} essai{'s' if attempts > 1 else ''}) "
            f"sur {ancrages_used} : {insight[:80]}"
        )
        return {
            "status": "success",
            "insight": insight,
            "ancrages_utilises": ancrages_used,
            "dominants": [d.id for d in dominants],
            "attempts": attempts,
            "rejection_log": rejection_log,
            "duration_s": round(time.time() - start, 2),
        }

    # --- LLM avec retry -----------------------------------------------------

    async def _generate_with_retry(
        self, system_prompt: str, valid_ids: List[str]
    ) -> Tuple[Optional[Dict[str, Any]], int, List[Dict[str, str]]]:
        """Tentative + 1 retry. Retourne (result | None, attempts, rejection_log)."""
        rejection_log: List[Dict[str, str]] = []
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Parle."},
        ]

        for attempt in range(1, MAX_RETRIES + 2):
            raw = await self._call_llm(messages)
            if raw is None:
                rejection_log.append({"attempt": str(attempt), "reason": "llm_no_response", "detail": ""})
                if attempt > MAX_RETRIES:
                    return None, attempt, rejection_log
                messages.append({"role": "assistant", "content": ""})
                messages.append({"role": "user", "content": "Tu n'as rien dit. Recommence et renvoie le JSON."})
                continue

            parsed = parse_llm_response(raw)
            if parsed is None:
                rejection_log.append({
                    "attempt": str(attempt),
                    "reason": "json_parse",
                    "detail": raw[:200],
                })
                if attempt > MAX_RETRIES:
                    return None, attempt, rejection_log
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": "Ta réponse n'est pas un JSON valide. Renvoie EXACTEMENT l'objet JSON demandé, sans markdown, sans préambule.",
                })
                continue

            err_a = validate_ancrages(parsed.get("ancrages_utilises"), valid_ids)
            if err_a:
                reason, detail = err_a
                rejection_log.append({"attempt": str(attempt), "reason": reason, "detail": detail})
                if attempt > MAX_RETRIES:
                    return None, attempt, rejection_log
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": build_correction_message(reason, detail)})
                continue

            err_i = validate_insight(parsed.get("insight", ""), valid_ids)
            if err_i:
                reason, detail = err_i
                rejection_log.append({"attempt": str(attempt), "reason": reason, "detail": detail})
                if attempt > MAX_RETRIES:
                    return None, attempt, rejection_log
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": build_correction_message(reason, detail)})
                continue

            return parsed, attempt, rejection_log

        return None, MAX_RETRIES + 1, rejection_log

    async def _call_llm(self, messages: List[Dict[str, str]]) -> Optional[str]:
        """Appel Ollama /api/chat avec format=json. Surchargeable pour les tests."""
        try:
            import httpx
            from core.base_agent import gpu_scheduler
            async with gpu_scheduler.access("soliloque_v2"):
                payload = {
                    "model": REFLECT_MODEL,
                    "messages": messages,
                    "stream": False,
                    "format": "json",
                    "think": False,
                    "options": {
                        "temperature": LLM_TEMPERATURE,
                        "num_ctx": LLM_NUM_CTX,
                        "num_predict": -1,
                    },
                }
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        OLLAMA_CHAT_URL, json=payload, timeout=LLM_TIMEOUT_S
                    )
                if response.status_code != 200:
                    logger.warning(f"[SOLILOQUE_V2] HTTP {response.status_code}: {response.text[:200]}")
                    return None
                content = response.json().get("message", {}).get("content", "")
                return content.strip() if content.strip() else None
        except Exception as e:
            logger.error(f"[SOLILOQUE_V2] Erreur LLM : {e}")
            return None

    # --- Side effects -------------------------------------------------------

    def _memorize(self, insight: str, dominants: List[Symptome], ancrages: List[str]):
        """Stocke l'insight dans ChromaDB."""
        try:
            from core.vector_store import ChromaMemoryManager
            mgr = ChromaMemoryManager.get_instance()
            if mgr:
                mgr.add_documents(
                    [insight],
                    [{
                        "source": "soliloque_v2",
                        "ancrages": ",".join(ancrages),
                        "dominants": ",".join(d.id for d in dominants),
                        "timestamp": str(time.time()),
                    }],
                    [f"soliloque_v2-{int(time.time() * 1000)}"],
                    "collective_wisdom",
                )
        except Exception as e:
            logger.debug(f"[SOLILOQUE_V2] Mémorisation échouée : {e}")

    def _satisfy_connexion(self):
        try:
            from core.desire_engine import desires
            desires.on_event("SOLILOQUE_COMPLETE")
        except Exception:
            pass

    def _stimulate_heart(self):
        try:
            from core.cardiac_engine import heart
            heart.react("learning")
        except Exception:
            pass

    def _save_letter(self, insight: str, dominants: List[Symptome]):
        try:
            from core.mailbox import mailbox
            if mailbox.should_write(insight):
                top_id = dominants[0].id if dominants else "voix"
                mailbox.write_letter(
                    content=insight,
                    source="soliloque_v2",
                    mood="incarne",
                    subject=f"Voix sur {top_id}",
                )
        except Exception:
            pass

    def _write_journal(
        self,
        dominants: List[Symptome],
        insight: str,
        ancrages: List[str],
        attempts: int,
        rejection_log: List[Dict[str, str]],
        start_ts: float,
    ):
        """Journal MD horodaté."""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            now = datetime.now()
            log_file = LOG_DIR / f"soliloque_v2_{now.strftime('%Y-%m-%d')}.md"

            dur = time.time() - start_ts
            header = (
                f"\n---\n\n"
                f"## {now.strftime('%H:%M:%S')} — V2 monologue\n\n"
                f"- **Symptômes dominants** : "
                + ", ".join(f"`{d.id}`(sail={d.saillance:.2f})" for d in dominants)
                + "\n"
                f"- **Ancrages utilisés** : " + ", ".join(f"`{a}`" for a in ancrages) + "\n"
                f"- **Tentatives** : {attempts} | **Durée** : {dur:.0f}s\n"
            )
            if rejection_log:
                header += f"- **Rejets** : {len(rejection_log)} ({', '.join(r['reason'] for r in rejection_log)})\n"
            header += f"\n**Insight** :\n> {insight}\n"

            with open(log_file, "a", encoding="utf-8") as f:
                if f.tell() == 0:
                    f.write(f"# Soliloques V2 — {now.strftime('%Y-%m-%d')}\n")
                f.write(header)
        except Exception as e:
            logger.warning(f"[SOLILOQUE_V2] Journal échoué : {e}")

    # --- Persistance --------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        return {
            "session_count": self.session_count,
            "success_count": self.success_count,
            "silence_count": self.silence_count,
            "abort_count": self.abort_count,
            "success_rate": (
                self.success_count / self.session_count
                if self.session_count else 0.0
            ),
            "last_used_count": len(self.last_used_map),
            "history_length": len(self.history),
        }

    def _save(self):
        try:
            data = {
                "version": "2.0",
                "session_count": self.session_count,
                "success_count": self.success_count,
                "silence_count": self.silence_count,
                "abort_count": self.abort_count,
                "last_used_map": self.last_used_map,
                "history": self.history[-50:],
                "saved_at": time.time(),
            }
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(str(tmp), str(STATE_FILE))
        except Exception as e:
            logger.warning(f"[SOLILOQUE_V2] Sauvegarde échouée : {e}")

    def _load(self):
        try:
            if not STATE_FILE.exists():
                return
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.session_count = data.get("session_count", 0)
            self.success_count = data.get("success_count", 0)
            self.silence_count = data.get("silence_count", 0)
            self.abort_count = data.get("abort_count", 0)
            self.last_used_map = data.get("last_used_map", {})
            self.history = data.get("history", [])
            logger.info(f"[SOLILOQUE_V2] Chargé ({self.session_count} sessions)")
        except Exception as e:
            logger.warning(f"[SOLILOQUE_V2] Chargement échoué : {e}")


# Singleton
soliloque_v2 = SoliloqueV2Engine()
