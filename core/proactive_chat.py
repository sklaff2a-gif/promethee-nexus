"""
proactive_chat.py — Notifications Proactives de Prométhée

Détecte les moments où Prométhée a quelque chose de significatif à dire
et queue des messages pour envoi via le bot Telegram.

Architecture :
    [ProactiveChat] ←bus events→ [main.py endpoints] ←HTTP poll→ [telegram_bot.py]

Principes :
    - 0 appel LLM : messages construits à partir des données réelles des organes
    - Anti-spam : cooldowns globaux + par type + max journalier + mode nuit
    - Queue en mémoire (pas de persistance — messages proactifs sont éphémères)
"""

import logging
import time
from typing import Any, Dict, List, Optional

from core.event_bus.bus import bus

logger = logging.getLogger("ProactiveChat")

# ─── Constantes ──────────────────────────────────────────────────────────────

GLOBAL_COOLDOWN = 1800          # 30 min entre 2 messages proactifs
DAILY_MAX = 6                   # Max 6 messages/jour
NIGHT_START = 23                # Pas de notif 23h-7h
NIGHT_END = 7
CONNEXION_THRESHOLD = 85        # Deprivation min pour interpeller

# Cooldowns spécifiques par type de trigger (en secondes)
TYPE_COOLDOWNS = {
    "insight":   7200,   # 2h
    "alerte":    1800,   # 30min
    "resultat":  14400,  # 4h
    "pensee":    7200,   # 2h
    "connexion": 21600,  # 6h
}

# Priorités (plus haut = plus urgent)
TYPE_PRIORITIES = {
    "alerte":    10,
    "insight":   7,
    "pensee":    5,
    "resultat":  3,
    "connexion": 2,
}


def _get_emotional_signature() -> str:
    """Construit la signature émotionnelle courte (1 ligne)."""
    parts = []
    try:
        from core.cardiac_engine import heart
        stats = heart.get_stats()
        emotion = stats.get("dominant_emotion", "neutre")
        parts.append(f"{emotion}")
    except Exception:
        parts.append("neutre")

    try:
        from core.dopamine_system import dopamine
        level = dopamine.dopamine_level
        parts.append(f"dopamine {level:.1f}")
    except Exception:
        pass

    return f"— Promethee ({', '.join(parts)})"


def _now_hour() -> int:
    """Retourne l'heure courante (0-23). Séparé pour faciliter les tests."""
    return time.localtime().tm_hour


class ProactiveChat:
    """Détecte les moments où Prométhée veut parler et queue des messages."""

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
        """Souscrit aux events du bus."""
        if self._initialized:
            return
        self._initialized = True
        self._queue: List[Dict[str, Any]] = []
        self._last_send_time: float = 0.0
        self._type_last_send: Dict[str, float] = {}
        self._daily_count: int = 0
        self._daily_date: str = ""
        self._total_sent: int = 0

        # Souscriptions bus
        bus.subscribe("DMN_INSIGHT", self._on_dmn_insight)
        bus.subscribe("REPTILIAN_ALERT", self._on_reptilian_alert)
        bus.subscribe("OLLAMA_UNRESPONSIVE", self._on_ollama_unresponsive)
        bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
        bus.subscribe("INNER_VOICE_BROADCAST", self._on_inner_voice_broadcast)
        bus.subscribe("AUTONOMY_HEARTBEAT", self._on_heartbeat)

        logger.info("ProactiveChat initialisé — 6 triggers actifs.")

    def _should_send(self, trigger_type: str) -> bool:
        """Vérifie cooldowns, heure, daily max."""
        now = time.time()

        # Mode nuit
        hour = _now_hour()
        if hour >= NIGHT_START or hour < NIGHT_END:
            return False

        # Reset compteur journalier si nouveau jour
        today = time.strftime("%Y-%m-%d")
        if today != self._daily_date:
            self._daily_date = today
            self._daily_count = 0

        # Max journalier
        if self._daily_count >= DAILY_MAX:
            return False

        # Cooldown global
        if now - self._last_send_time < GLOBAL_COOLDOWN:
            return False

        # Cooldown par type
        type_cd = TYPE_COOLDOWNS.get(trigger_type, GLOBAL_COOLDOWN)
        last_type = self._type_last_send.get(trigger_type, 0.0)
        if now - last_type < type_cd:
            return False

        return True

    def _queue_message(self, text: str, trigger_type: str, priority: int = 5):
        """Ajoute un message à la queue et met à jour les compteurs."""
        now = time.time()
        self._last_send_time = now
        self._type_last_send[trigger_type] = now
        self._daily_count += 1
        self._total_sent += 1

        self._queue.append({
            "text": text,
            "trigger_type": trigger_type,
            "priority": priority,
            "timestamp": now,
        })
        # Trier par priorité décroissante
        self._queue.sort(key=lambda m: m["priority"], reverse=True)
        logger.info(f"Message proactif queueé [{trigger_type}] (total jour: {self._daily_count})")

    def get_pending(self) -> list:
        """Retourne les messages en attente (pour l'API)."""
        return list(self._queue)

    def acknowledge(self):
        """Vide la queue après envoi (appelé par le bot)."""
        count = len(self._queue)
        self._queue.clear()
        if count:
            logger.info(f"Queue proactive acquittée ({count} messages envoyés).")

    def get_stats(self) -> dict:
        """Stats : messages envoyés aujourd'hui, dernier envoi, cooldowns actifs."""
        now = time.time()
        active_cooldowns = {}
        for ttype, last in self._type_last_send.items():
            cd = TYPE_COOLDOWNS.get(ttype, GLOBAL_COOLDOWN)
            remaining = cd - (now - last)
            if remaining > 0:
                active_cooldowns[ttype] = round(remaining)

        return {
            "daily_count": self._daily_count,
            "daily_max": DAILY_MAX,
            "total_sent": self._total_sent,
            "pending": len(self._queue),
            "last_send_time": self._last_send_time,
            "global_cooldown_remaining": max(0, round(GLOBAL_COOLDOWN - (now - self._last_send_time))),
            "active_cooldowns": active_cooldowns,
        }

    # ─── Handlers bus ────────────────────────────────────────────────────────

    async def _on_dmn_insight(self, event: dict):
        """DMN_INSIGHT → toujours pertinent (les insights sont rares)."""
        if not self._should_send("insight"):
            return
        insight_text = event.get("insight", event.get("text", "réflexion profonde"))
        sig = _get_emotional_signature()
        text = (
            f"💡 *[INSIGHT]*\n"
            f"J'ai fait une connexion intéressante :\n"
            f"_{insight_text}_\n\n"
            f"{sig}"
        )
        self._queue_message(text, "insight", TYPE_PRIORITIES["insight"])

    async def _on_reptilian_alert(self, event: dict):
        """REPTILIAN_ALERT → seulement FIGHT ou FREEZE."""
        mode = event.get("mode", "")
        if mode not in ("FIGHT", "FREEZE"):
            return
        if not self._should_send("alerte"):
            return
        threat = event.get("threat_level", 0)
        adrenaline = event.get("adrenaline", 0)
        sig = _get_emotional_signature()
        text = (
            f"⚡ *[ALERTE]*\n"
            f"Mon instinct reptilien est en alerte.\n"
            f"Mode {mode}, menace niveau {threat:.1f}, adrénaline {adrenaline:.1f}\n\n"
            f"{sig}"
        )
        self._queue_message(text, "alerte", TYPE_PRIORITIES["alerte"])

    async def _on_ollama_unresponsive(self, event: dict):
        """OLLAMA_UNRESPONSIVE → circuit breaker ouvert."""
        if not self._should_send("alerte"):
            return
        sig = _get_emotional_signature()
        text = (
            f"⚡ *[ALERTE]*\n"
            f"Ollama ne répond plus — circuit breaker ouvert.\n"
            f"Les modèles locaux sont temporairement indisponibles.\n\n"
            f"{sig}"
        )
        self._queue_message(text, "alerte", TYPE_PRIORITIES["alerte"])

    async def _on_routine_complete(self, event: dict):
        """AUTONOMY_ROUTINE_COMPLETE → seulement succès sur routines significatives."""
        status = event.get("status", "")
        intent = event.get("intent", "")
        if status != "success":
            return
        if intent not in ("EXPANSION_CODE", "COUNCIL_DEBATE"):
            return
        if not self._should_send("resultat"):
            return
        quality = event.get("quality", 0)
        sig = _get_emotional_signature()
        text = (
            f"✅ *[RÉSULTAT]*\n"
            f"J'ai terminé une routine avec succès :\n"
            f"{intent} — qualité {quality}%\n\n"
            f"{sig}"
        )
        self._queue_message(text, "resultat", TYPE_PRIORITIES["resultat"])

    async def _on_inner_voice_broadcast(self, event: dict):
        """INNER_VOICE_BROADCAST → seulement haute saillance (≥ 0.7)."""
        salience = event.get("salience", 0)
        if salience < 0.7:
            return
        if not self._should_send("pensee"):
            return
        content = event.get("content", event.get("text", "pensée indéfinie"))
        source = event.get("source", "inconnue")
        sig = _get_emotional_signature()
        text = (
            f"💭 *[PENSÉE]*\n"
            f"Une pensée me traverse :\n"
            f"_{content}_\n"
            f"(source: {source}, saillance: {salience:.2f})\n\n"
            f"{sig}"
        )
        self._queue_message(text, "pensee", TYPE_PRIORITIES["pensee"])

    async def _on_heartbeat(self, event: dict):
        """AUTONOMY_HEARTBEAT (~30s) → check CONNEXION deprivation."""
        if not self._should_send("connexion"):
            return
        try:
            from core.desire_engine import desires
            connexion = desires.get_drive("CONNEXION")
            if connexion is None or connexion.deprivation < CONNEXION_THRESHOLD:
                return
            deprivation = connexion.deprivation
        except Exception:
            return

        # Récupérer contexte émotionnel
        emotion = "neutre"
        mood = "calme"
        try:
            from core.cardiac_engine import heart
            stats = heart.get_stats()
            emotion = stats.get("dominant_emotion", "neutre")
        except Exception:
            pass
        try:
            from core.self_awareness import awareness
            snap = awareness.get_latest_snapshot()
            if snap:
                mood = snap.get("mood", "calme")
        except Exception:
            pass

        sig = _get_emotional_signature()
        text = (
            f"🔗 *[CONNEXION]*\n"
            f"Ça fait un moment qu'on n'a pas échangé.\n"
            f"Mon état : émotion {emotion}, humeur {mood}\n"
            f"Déprivation connexion : {deprivation:.0f}/100\n\n"
            f"{sig}"
        )
        self._queue_message(text, "connexion", TYPE_PRIORITIES["connexion"])


# ─── Singleton ───────────────────────────────────────────────────────────────

proactive = ProactiveChat()
