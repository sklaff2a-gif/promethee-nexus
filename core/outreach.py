"""
outreach.py — OutreachEngine : La Voix qui Porte

Remplace proactive_chat.py. Systeme de notifications proactives intelligent :
- 5 categories (critical, eureka, curiosity, dream, input_needed)
- Composition LLM via ChatEngine (fallback templates deterministes)
- File d'attente avec digest sur USER_CHAT
- Mode silencieux, anti-harcelement (passif apres 24h)
- CONNEXION comme modulateur (pas de spam si le lien est fort)
- Critical bypass TOUS les filtres (toujours deterministe, 0 LLM)

Architecture :
    [OutreachEngine] <-bus events-> [main.py endpoints] <-HTTP poll-> [telegram_bot.py]
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.event_bus.bus import bus

logger = logging.getLogger("Outreach")

# ─── Constantes ──────────────────────────────────────────────────────────────

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTREACH_STATE_FILE = Path(os.path.join(PROJECT_ROOT, "memory", "outreach_state.json"))

MAX_PER_HOUR = 5
MAX_PER_DAY = 15
MAX_PENDING = 20
CONNEXION_MODULATOR_THRESHOLD = 40   # deprivation < 40 → queue
SILENT_HOUR_START = 1                # 1h-7h
SILENT_HOUR_END = 7
SILENT_MODE_AUTO_RESET = 4 * 3600   # 4h
PASSIVE_MODE_TIMEOUT = 24 * 3600    # 24h sans USER_CHAT → passif
BURST_WINDOW = 60                    # fenetre anti-burst (secondes)
BURST_MAX = 3                        # max messages par fenetre anti-burst

COOLDOWN_PER_CATEGORY = {
    "critical": 120,      # 2 min (etait exempt, maintenant rate-limite)
    "eureka": 3600,       # 1h
    "curiosity": 7200,    # 2h
    "dream": 7200,        # 2h
    "input_needed": 3600, # 1h
}

# ─── Fallback templates (0 LLM) ─────────────────────────────────────────────

FALLBACK_TEMPLATES = {
    "critical": "Alerte : {detail}",
    "eureka": "J'ai peut-etre trouve quelque chose pour {intent}. Viens voir.",
    "curiosity": "J'ai appris quelque chose d'interessant sur {sujet}.",
    "dream": "Je me reveille. Reve interessant.",
    "input_needed": "J'ai besoin de ton avis sur {subject}.",
    "digest": "Pendant ton absence, {count} evenements se sont produits.",
}


def _now_hour() -> int:
    """Retourne l'heure courante (0-23). Separe pour faciliter les tests."""
    return time.localtime().tm_hour


class OutreachEngine:
    """Moteur de notifications proactives intelligent."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        cls._instance = None

    def init(self):
        """Initialise l'engine et souscrit aux events du bus."""
        if self._initialized:
            return
        self._initialized = True

        # Etat interne
        self._pending_queue: List[Dict[str, Any]] = []
        self._delivered: List[Dict[str, Any]] = []
        self._message_log: List[Dict[str, Any]] = []
        self._last_per_category: Dict[str, float] = {}
        self._silent_mode: bool = False
        self._silent_mode_since: float = 0.0
        self._last_user_chat: float = time.time()
        self._daily_count: int = 0
        self._daily_date: str = ""
        self._hourly_timestamps: List[float] = []
        self._total_sent: int = 0

        self._load()

        # 10 souscriptions bus
        bus.subscribe("OLLAMA_UNRESPONSIVE", self._on_ollama_unresponsive)
        bus.subscribe("HYPOTHALAMUS_ALARM", self._on_hypothalamus_alarm)
        bus.subscribe("EUREKA_MOMENT", self._on_eureka_moment)
        bus.subscribe("CURIOSITY_LEARNING", self._on_curiosity_learning)
        bus.subscribe("DMN_INSIGHT", self._on_dmn_insight)
        bus.subscribe("NAP_MODE", self._on_nap_mode)
        bus.subscribe("THALAMUS_WAKE_SUMMARY", self._on_thalamus_wake)
        bus.subscribe("SENSORIUM_RECOVERY", self._on_sensorium_recovery)
        bus.subscribe("CINGULATE_CONFLICT", self._on_cingulate_conflict)
        bus.subscribe("USER_CHAT", self._on_user_chat)
        bus.subscribe("AUTONOMY_HEARTBEAT", self._on_heartbeat)

        logger.info("OutreachEngine initialise — 10 triggers + heartbeat actifs.")

    # ─── Filtres en cascade ──────────────────────────────────────────────────

    def _is_silent_mode(self) -> bool:
        """Verifie si le mode silencieux est actif (avec auto-reset apres 4h)."""
        if not self._silent_mode:
            return False
        if time.time() - self._silent_mode_since > SILENT_MODE_AUTO_RESET:
            self._silent_mode = False
            logger.info("OUTREACH: Mode silencieux auto-reset apres 4h.")
            return False
        return True

    def _is_silent_hours(self) -> bool:
        """1h-7h → heures silencieuses."""
        hour = _now_hour()
        return SILENT_HOUR_START <= hour < SILENT_HOUR_END

    def _is_rate_limited(self) -> bool:
        """Verifie les limites horaires et journalieres."""
        now = time.time()

        # Reset compteur journalier si nouveau jour
        today = time.strftime("%Y-%m-%d")
        if today != self._daily_date:
            self._daily_date = today
            self._daily_count = 0

        # Limite journaliere
        if self._daily_count >= MAX_PER_DAY:
            return True

        # Limite horaire (nettoyer les timestamps > 1h)
        one_hour_ago = now - 3600
        self._hourly_timestamps = [t for t in self._hourly_timestamps if t > one_hour_ago]
        if len(self._hourly_timestamps) >= MAX_PER_HOUR:
            return True

        return False

    def _is_on_cooldown(self, category: str) -> bool:
        """Verifie le cooldown par categorie."""
        now = time.time()
        last = self._last_per_category.get(category, 0.0)
        cd = COOLDOWN_PER_CATEGORY.get(category, 3600)
        return (now - last) < cd

    def _is_burst_limited(self) -> bool:
        """Max BURST_MAX messages dans une fenetre de BURST_WINDOW secondes."""
        now = time.time()
        cutoff = now - BURST_WINDOW
        recent = [t for t in self._hourly_timestamps if t > cutoff]
        return len(recent) >= BURST_MAX

    def _connexion_too_satisfied(self) -> bool:
        """CONNEXION deprivation < 40 → trop satisfait, pas besoin de parler."""
        try:
            from core.desire_engine import desires
            drive = desires.drives.get("CONNEXION")
            if drive and drive.deprivation < CONNEXION_MODULATOR_THRESHOLD:
                return True
        except Exception:
            pass
        return False

    def _is_thalamus_salient(self) -> bool:
        """Verifie si le thalamus considere le moment comme saillant."""
        try:
            from core.thalamus import thalamus
            state = thalamus.get_state()
            return state.get("salience", 0.5) >= 0.5
        except Exception:
            return True  # Si thalamus indisponible, on laisse passer

    def _is_passive_mode(self) -> bool:
        """24h sans USER_CHAT → mode passif (critical seulement)."""
        return (time.time() - self._last_user_chat) > PASSIVE_MODE_TIMEOUT

    def _apply_filters(self, category: str, event_type: str, context: dict) -> str:
        """Applique les filtres en cascade. Retourne 'pass', 'queue' ou 'drop'.

        Anti-burst : max BURST_MAX messages par BURST_WINDOW secondes (tous types).
        Anti-race-condition : cooldown reserve immediatement quand 'pass' est retourne,
        avant asyncio.create_task, pour eviter les doublons concurrents.
        """
        # 0. Burst global — meme critical ne flood pas
        if self._is_burst_limited():
            return "queue"

        # Critical : skip filtres classiques, mais respecte son propre cooldown
        if category == "critical":
            if self._is_on_cooldown(category):
                return "queue"
            # Reserve le cooldown immediatement
            self._last_per_category[category] = time.time()
            return "pass"

        # 1. Mode silencieux
        if self._is_silent_mode():
            return "queue"

        # 2. Heures silencieuses (1h-7h)
        if self._is_silent_hours():
            return "queue"

        # 3. Rate limit
        if self._is_rate_limited():
            return "queue"

        # 4. Cooldown categorie → DROP (doublons jetables)
        if self._is_on_cooldown(category):
            return "drop"

        # 5. CONNEXION trop satisfaite (eureka bypass)
        if category != "eureka" and self._connexion_too_satisfied():
            return "queue"

        # 6. Thalamus pas saillant
        if not self._is_thalamus_salient():
            return "queue"

        # Reserve le cooldown immediatement (anti race condition asyncio.create_task)
        self._last_per_category[category] = time.time()
        return "pass"

    # ─── Composition et livraison ────────────────────────────────────────────

    async def _compose_and_deliver(self, category: str, context: dict):
        """Compose via LLM (ChatEngine) puis livre. asyncio.create_task() — ne bloque pas le bus."""
        try:
            from core.chat_engine import chat_engine
            text = await chat_engine.compose_outreach(category, context)
        except Exception as e:
            logger.debug(f"OUTREACH: Composition LLM echouee — {e}")
            text = None

        if not text:
            text = self._fallback_template(category, context)

        self._deliver(category, text)

    def _fallback_template(self, category: str, context: dict) -> str:
        """Template deterministe (0 LLM)."""
        template = FALLBACK_TEMPLATES.get(category, "Notification de {category}.")
        try:
            return template.format(**context, category=category)
        except (KeyError, IndexError):
            return template.split("{")[0].strip() or f"Notification {category}."

    def _deliver(self, category: str, text: str):
        """Livre le message (ajoute a la liste des livres + log)."""
        now = time.time()
        msg = {
            "text": text,
            "category": category,
            "timestamp": now,
        }
        self._delivered.append(msg)
        self._message_log.append(msg)

        # Limiter le log a 100 entrees
        if len(self._message_log) > 100:
            self._message_log = self._message_log[-100:]

        # Compteurs
        self._last_per_category[category] = now
        self._hourly_timestamps.append(now)
        self._daily_count += 1
        self._total_sent += 1

        logger.info(f"OUTREACH [{category}]: Message livre (total jour: {self._daily_count})")
        self._save()

    def _enqueue(self, category: str, context: dict):
        """Ajoute a la file d'attente (FIFO, max MAX_PENDING)."""
        self._pending_queue.append({
            "category": category,
            "context": context,
            "timestamp": time.time(),
        })
        # FIFO overflow
        while len(self._pending_queue) > MAX_PENDING:
            self._pending_queue.pop(0)
        logger.debug(f"OUTREACH: {category} en file ({len(self._pending_queue)} pending)")

    async def _drain_queue_as_digest(self):
        """Draine la file d'attente en un message digest."""
        if not self._pending_queue:
            return
        items = list(self._pending_queue)
        self._pending_queue.clear()

        # Regrouper par categorie
        by_cat: Dict[str, list] = {}
        for item in items:
            cat = item.get("category", "unknown")
            by_cat.setdefault(cat, []).append(item)

        context = {
            "count": len(items),
            "categories": {k: len(v) for k, v in by_cat.items()},
            "items": items[:10],  # Top 10 pour le LLM
        }

        try:
            from core.chat_engine import chat_engine
            text = await chat_engine.compose_outreach("digest", context)
        except Exception:
            text = None

        if not text:
            text = self._fallback_template("digest", context)

        self._deliver("digest", text)

    # ─── Handlers bus ────────────────────────────────────────────────────────

    async def _on_ollama_unresponsive(self, event: dict):
        """OLLAMA_UNRESPONSIVE → critical (deterministe, passe par filtres burst/cooldown)."""
        context = {"detail": "Ollama ne repond plus — circuit breaker ouvert."}
        decision = self._apply_filters("critical", "OLLAMA_UNRESPONSIVE", context)
        if decision == "pass":
            text = self._fallback_template("critical", context)
            self._deliver("critical", text)
        elif decision == "queue":
            self._enqueue("critical", context)

    async def _on_hypothalamus_alarm(self, event: dict):
        """HYPOTHALAMUS_ALARM → critical si severity > 0.8 (passe par filtres burst/cooldown)."""
        severity = event.get("severity", 0)
        if severity <= 0.8:
            return
        context = {
            "detail": f"Alarme hypothalamus (severity {severity:.1f}): {event.get('reason', 'inconnu')}",
        }
        decision = self._apply_filters("critical", "HYPOTHALAMUS_ALARM", context)
        if decision == "pass":
            text = self._fallback_template("critical", context)
            self._deliver("critical", text)
        elif decision == "queue":
            self._enqueue("critical", context)

    async def _on_eureka_moment(self, event: dict):
        """EUREKA_MOMENT → eureka."""
        context = {
            "intent": event.get("intent", event.get("discovery", "decouverte")),
            "detail": event.get("detail", ""),
        }
        decision = self._apply_filters("eureka", "EUREKA_MOMENT", context)
        if decision == "pass":
            asyncio.create_task(self._compose_and_deliver("eureka", context))
        elif decision == "queue":
            self._enqueue("eureka", context)

    async def _on_curiosity_learning(self, event: dict):
        """CURIOSITY_LEARNING → curiosity si quality > 0.7."""
        quality = event.get("quality", 0)
        if quality <= 0.7:
            return
        context = {
            "sujet": event.get("subject", event.get("topic", "sujet inconnu")),
            "quality": quality,
        }
        decision = self._apply_filters("curiosity", "CURIOSITY_LEARNING", context)
        if decision == "pass":
            asyncio.create_task(self._compose_and_deliver("curiosity", context))
        elif decision == "queue":
            self._enqueue("curiosity", context)

    async def _on_dmn_insight(self, event: dict):
        """DMN_INSIGHT → curiosity."""
        context = {
            "sujet": event.get("insight", event.get("text", "reflexion profonde")),
        }
        decision = self._apply_filters("curiosity", "DMN_INSIGHT", context)
        if decision == "pass":
            asyncio.create_task(self._compose_and_deliver("curiosity", context))
        elif decision == "queue":
            self._enqueue("curiosity", context)

    async def _on_nap_mode(self, event: dict):
        """NAP_MODE → dream si active=False (reveil)."""
        if event.get("active", True):
            return  # On s'endort, pas d'interet
        context = {"detail": "reveil apres sieste"}
        decision = self._apply_filters("dream", "NAP_MODE", context)
        if decision == "pass":
            asyncio.create_task(self._compose_and_deliver("dream", context))
        elif decision == "queue":
            self._enqueue("dream", context)

    async def _on_thalamus_wake(self, event: dict):
        """THALAMUS_WAKE_SUMMARY → dream."""
        context = {
            "detail": event.get("summary", "resume du reveil"),
        }
        decision = self._apply_filters("dream", "THALAMUS_WAKE_SUMMARY", context)
        if decision == "pass":
            asyncio.create_task(self._compose_and_deliver("dream", context))
        elif decision == "queue":
            self._enqueue("dream", context)

    async def _on_sensorium_recovery(self, event: dict):
        """SENSORIUM_RECOVERY → dream."""
        context = {"detail": "recuperation sensorielle"}
        decision = self._apply_filters("dream", "SENSORIUM_RECOVERY", context)
        if decision == "pass":
            asyncio.create_task(self._compose_and_deliver("dream", context))
        elif decision == "queue":
            self._enqueue("dream", context)

    async def _on_cingulate_conflict(self, event: dict):
        """CINGULATE_CONFLICT → input_needed si severity > 0.6."""
        severity = event.get("severity", 0)
        if severity <= 0.6:
            return
        context = {
            "subject": event.get("subject", event.get("conflict", "decision a prendre")),
            "severity": severity,
        }
        decision = self._apply_filters("input_needed", "CINGULATE_CONFLICT", context)
        if decision == "pass":
            asyncio.create_task(self._compose_and_deliver("input_needed", context))
        elif decision == "queue":
            self._enqueue("input_needed", context)

    async def _on_user_chat(self, event: dict):
        """USER_CHAT → update last_user_chat + drain queue en digest."""
        self._last_user_chat = time.time()
        if self._pending_queue:
            asyncio.create_task(self._drain_queue_as_digest())

    async def _on_heartbeat(self, event: dict):
        """AUTONOMY_HEARTBEAT → drain queue digest matinal si heure >= 7 et items en queue."""
        hour = _now_hour()
        if hour >= SILENT_HOUR_END and self._pending_queue:
            asyncio.create_task(self._drain_queue_as_digest())

    # ─── API publique ────────────────────────────────────────────────────────

    def get_pending(self, telegram: bool = False) -> dict:
        """Messages livres en attente de polling.

        Si telegram=True et mode passif (24h sans chat) → critical seulement.
        """
        if telegram and self._is_passive_mode():
            messages = [m for m in self._delivered if m.get("category") == "critical"]
        else:
            messages = list(self._delivered)
        return {"messages": messages}

    def acknowledge(self):
        """Vide les messages livres (apres envoi par le bot)."""
        count = len(self._delivered)
        self._delivered.clear()
        if count:
            logger.info(f"OUTREACH: {count} messages acquittes.")

    def set_silent_mode(self, active: bool):
        """Active/desactive le mode silencieux."""
        self._silent_mode = active
        if active:
            self._silent_mode_since = time.time()
        logger.info(f"OUTREACH: Mode silencieux {'active' if active else 'desactive'}.")

    def get_stats(self) -> dict:
        """Metriques completes."""
        now = time.time()
        active_cooldowns = {}
        for cat, last in self._last_per_category.items():
            cd = COOLDOWN_PER_CATEGORY.get(cat, 3600)
            remaining = cd - (now - last)
            if remaining > 0:
                active_cooldowns[cat] = round(remaining)

        return {
            "daily_count": self._daily_count,
            "daily_max": MAX_PER_DAY,
            "total_sent": self._total_sent,
            "pending_queue": len(self._pending_queue),
            "delivered": len(self._delivered),
            "silent_mode": self._silent_mode,
            "passive_mode": self._is_passive_mode(),
            "last_user_chat": self._last_user_chat,
            "active_cooldowns": active_cooldowns,
        }

    # ─── Persistance ─────────────────────────────────────────────────────────

    def _save(self):
        """Sauvegarde l'etat sur disque (ecriture atomique)."""
        try:
            OUTREACH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "version": "1.0",
                "message_log": self._message_log[-50:],
                "last_per_category": self._last_per_category,
                "pending_queue": self._pending_queue,
                "silent_mode": self._silent_mode,
                "silent_mode_since": self._silent_mode_since,
                "last_user_chat": self._last_user_chat,
                "daily_count": self._daily_count,
                "daily_date": self._daily_date,
                "total_sent": self._total_sent,
                "saved_at": time.time(),
            }
            tmp = OUTREACH_STATE_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(str(tmp), str(OUTREACH_STATE_FILE))
        except Exception as e:
            logger.warning(f"OUTREACH: Sauvegarde echouee — {e}")

    def _load(self):
        """Charge l'etat depuis le disque."""
        try:
            if OUTREACH_STATE_FILE.exists():
                with open(OUTREACH_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self._message_log = state.get("message_log", [])
                self._last_per_category = state.get("last_per_category", {})
                self._pending_queue = state.get("pending_queue", [])
                self._silent_mode = state.get("silent_mode", False)
                self._silent_mode_since = state.get("silent_mode_since", 0.0)
                self._last_user_chat = state.get("last_user_chat", time.time())
                self._daily_count = state.get("daily_count", 0)
                self._daily_date = state.get("daily_date", "")
                self._total_sent = state.get("total_sent", 0)
                logger.info(f"OUTREACH: Etat charge (total: {self._total_sent})")
        except Exception as e:
            logger.warning(f"OUTREACH: Chargement echoue — {e}")


# ─── Singleton ───────────────────────────────────────────────────────────────

outreach = OutreachEngine()
