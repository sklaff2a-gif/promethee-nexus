"""
ReptilianCore — Le Tronc Cérébral de Prométhée.

Couche la plus primitive du modèle triunique (MacLean).
Agit AVANT la pensée consciente — réflexes purs, 0 LLM, boucle rapide (5s).

Responsabilités :
- Détection de menaces (capteurs système, erreurs, hallucinations, budget)
- Réflexes de survie (ADRENALINE, FLINCH, SHED, FREEZE, FIGHT, PAIN)
- Amygdale pavlovienne (conditionnement menace → réflexe)
- Watchdog continu (sentinelle qui ne dort jamais)
- Injection d'adrénaline vers CardiacEngine

Le reptilien ne pense pas — il réagit. Il ne planifie pas — il survit.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("prometheev11.reptilian")

# ============================================================
# Constantes — Le reptilien est RAPIDE et DÉTERMINISTE
# ============================================================

WATCHDOG_INTERVAL = 5.0       # Scan toutes les 5 secondes
THREAT_DECAY_RATE = 0.85      # Décroissance menace par tick (retour au calme)
ADRENALINE_DECAY = 0.92       # Décroissance adrénaline par tick

# Niveaux de menace (0-10)
THREAT_CALM = 0               # Tout va bien
THREAT_ALERT = 3              # Vigilance accrue
THREAT_DANGER = 6             # Réponse active requise
THREAT_CRITICAL = 8           # Réflexes d'urgence
THREAT_MORTAL = 10            # Survie pure — tout arrêter

# Planchers tissue (max, pas accumulation — évite boucle feedback)
TISSUE_OVERLOAD_THREAT = 3.0   # Modéré — au-dessus d'ALERT mais sous DANGER
TISSUE_EXTINCTION_THREAT = 5.0  # Sérieux mais pas FREEZE (< 7.0)

# Seuils de détection
THRESHOLDS = {
    "error_streak_alert": 3,
    "error_streak_danger": 6,
    "error_streak_critical": 10,
    "cpu_warn": 80,
    "cpu_crit": 95,
    "ram_warn": 75,
    "ram_crit": 90,
    "hallucination_storm": 3,      # 3 hallucinations en 10 min
    "hallucination_window": 600,   # Fenêtre de 10 minutes
    "budget_warn_ratio": 0.85,
    "budget_crit_ratio": 0.95,
    "idle_too_long": 3600,         # 1h sans activité
    "process_mem_warn_mb": 2000,   # 2 Go de RAM Python
}

# Seuils de sécurité absolus — garde-fou "grenouille bouillie"
# Toujours critique, même si les percentiles disent le contraire
ABSOLUTE_SAFETY_LIMITS = {
    "cpu_absolute_crit": 98,
    "ram_absolute_crit": 97,
}

# Seuils adaptatifs basés sur les percentiles glissants 24h
ADAPTIVE_THRESHOLDS = {
    "cpu_warn_percentile": 0.85,
    "cpu_crit_percentile": 0.97,
    "ram_warn_percentile": 0.80,
    "ram_crit_percentile": 0.95,
    # Planchers absolus — en dessous, les percentiles adaptatifs sont ignorés
    "cpu_warn_floor": 60,    # pas d'alerte CPU warn sous 60%
    "cpu_crit_floor": 85,    # pas d'alerte CPU crit sous 85%
    "ram_warn_floor": 70,    # pas d'alerte RAM warn sous 70%
    "ram_crit_floor": 80,    # pas d'alerte RAM crit sous 80%
}

# Cooldowns des réflexes (secondes) — anti-spam
REFLEX_COOLDOWNS = {
    "ADRENALINE": 60,
    "FLINCH": 30,
    "SHED": 120,
    "FREEZE": 180,
    "FIGHT": 300,
}

# --- Habituation (menaces chroniques) ---
HABITUATION_ONSET = 12              # Ticks avant début atténuation (~60s)
HABITUATION_FLOOR = 0.3             # Facteur minimum (menace réduite à 30%)
HABITUATION_DECAY_PER_TICK = 0.02   # Atténuation par tick après onset
HABITUATION_RECOVERY_RATE = 0.05    # Récupération quand la menace disparaît

# Fichier de persistance
REPTILIAN_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "reptilian_state.json"
)


# ============================================================
# Amygdale — Mémoire de menace (conditionnement pavlovien)
# ============================================================

@dataclass
class ThreatMemory:
    """Une mémoire de menace — conditionnement pavlovien."""
    pattern: str              # Ex: "ollama_down", "error_cascade:EXPANSION_CODE"
    severity: float           # 0-10 — gravité apprise
    occurrences: int          # Nombre de fois observée
    last_seen: float          # timestamp
    conditioned_reflex: str   # Réflexe associé (ex: "FREEZE", "SHED", "ADRENALINE")


# ============================================================
# ReptilianCore — Singleton
# ============================================================

class ReptilianCore:
    """Le Tronc Cérébral de Prométhée.

    Couche la plus primitive — agit AVANT la pensée consciente.
    Boucle rapide (5s), 0 LLM, réflexes purs.
    """

    _instance: Optional["ReptilianCore"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # --- État principal ---
        self.threat_level: float = 0.0           # [0.0, 10.0]
        self.adrenaline: float = 0.0             # [0.0, 1.0]
        self.reflexes_triggered: Dict[str, int] = {}  # Compteurs par réflexe
        self.threat_memories: Dict[str, ThreatMemory] = {}  # Amygdale

        # --- État interne ---
        self._last_threats: Dict[str, float] = {}
        self._hallucination_timestamps: List[float] = []
        self._reflex_cooldowns: Dict[str, float] = {}  # Réflexe → timestamp dernière activation
        self._watchdog_task: Optional[asyncio.Task] = None
        self._alive: bool = False
        self._subscribed: bool = False
        self._sleeping: bool = False  # Mode sieste : suspend le scan
        self._beat_counter: int = 0
        self._last_activity: float = time.time()

        # --- Capteurs bruts (pour télémétrie) ---
        self._last_cpu: float = 0.0
        self._last_ram: float = 0.0
        self._last_budget_ratio: float = 0.0
        self._ollama_ok: bool = True
        self._ollama_ok_streak: int = 0     # Cycles consécutifs OK
        self._ram_critical_logged: float = 0.0

        # --- Trackers adaptatifs (percentiles glissants 24h) ---
        from core.percentile_tracker import PercentileTracker
        self._cpu_tracker = PercentileTracker()
        self._ram_tracker = PercentileTracker()

        # --- Flags de réflexes actifs ---
        self._freeze_until: float = 0.0          # timestamp de fin de FREEZE
        self._shed_until: float = 0.0            # timestamp de fin de SHED
        self._shed_max_cost: int = 2             # Coût max pendant SHED
        self._flinch_reason: str = ""            # Raison du dernier FLINCH

        # --- Anti-spam FLINCH (cooldown exponentiel) ---
        self._flinch_consecutive: int = 0        # Déclenchements consécutifs même menace
        self._flinch_last_threat: str = ""       # Dernière menace top ayant déclenché FLINCH

        # --- Habituation (menaces chroniques) ---
        self._threat_persistence: Dict[str, int] = {}    # source → ticks consécutifs
        self._threat_habituation: Dict[str, float] = {}  # source → facteur atténuation [0.3, 1.0]

        # Charger l'état persisté
        self._load()

    @classmethod
    def reset_singleton(cls):
        """Reset complet pour les tests."""
        if cls._instance is not None:
            cls._instance._alive = False
            if cls._instance._watchdog_task and not cls._instance._watchdog_task.done():
                cls._instance._watchdog_task.cancel()
            cls._instance = None

    def init(self):
        """Initialisation appelée depuis main.py — souscriptions bus."""
        self._subscribe_events()
        logger.info(f"REPTILIEN: Tronc cérébral actif (menace={self.threat_level:.1f}).")

    # ============================================================
    # Watchdog Loop — Le gardien qui ne dort jamais
    # ============================================================

    async def start_watchdog(self):
        """Démarre la boucle du watchdog (asyncio task)."""
        self._alive = True
        try:
            await self._watchdog_loop()
        except asyncio.CancelledError:
            self._alive = False

    async def _watchdog_loop(self):
        """Boucle du tronc cérébral — scan, évalue, réagit, signale."""
        while self._alive:
            await asyncio.sleep(WATCHDOG_INTERVAL)

            # Mode sieste : le reptilien dort, décroissance passive uniquement
            if self._sleeping:
                self.threat_level *= THREAT_DECAY_RATE
                self.adrenaline = max(0.0, self.adrenaline * ADRENALINE_DECAY)
                continue

            # 1. SENTIR — détecter les menaces
            threats = await self._sense_threats()

            # 2. ÉVALUER — calculer le niveau de menace composite
            new_level = self._compute_threat_level(threats)

            # 3. DÉCROISSANCE — le calme revient naturellement (avec frein parasympathique)
            decayed = self.threat_level * THREAT_DECAY_RATE
            if new_level >= decayed:
                # Menace active → léger recul même en menace persistante
                self.threat_level = new_level * 0.98 + decayed * 0.02
            else:
                # Menace passée → decay normal
                self.threat_level = decayed

            # 4. ADRÉNALINE — décroissance naturelle
            self.adrenaline = max(0.0, self.adrenaline * ADRENALINE_DECAY)

            # 5. RÉAGIR — déclencher les réflexes appropriés
            if self.threat_level >= THREAT_ALERT:
                await self._trigger_reflexes(threats)

            # Reset escalade FLINCH quand la menace passe sous le seuil
            if self.threat_level < 4.0 and self._flinch_consecutive > 0:
                self._flinch_consecutive = 0
                self._flinch_last_threat = ""

            # 6. SIGNALER — publier l'état sur le bus
            if self.threat_level >= THREAT_ALERT or self._beat_counter % 12 == 0:
                self._publish_state()

            # 7. PERSISTER — sauvegarder toutes les 60s (~12 ticks)
            self._beat_counter += 1
            if self._beat_counter % 12 == 0:
                self.save()
                # Extinction amygdalienne (~60s)
                try:
                    from core.amygdala import amygdala as _amyg
                    _amyg.tick_extinction()
                except Exception:
                    pass

    # ============================================================
    # Capteurs de menaces — _sense_threats()
    # ============================================================

    async def _sense_threats(self) -> Dict[str, float]:
        """Scan rapide de tous les capteurs. Retourne {source: niveau_menace}."""
        threats: Dict[str, float] = {}

        # --- CPU / RAM (3 niveaux : sécurité absolue → adaptatif → cold start) ---
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0)
            mem = psutil.virtual_memory()
            ram = mem.percent
            self._last_cpu = cpu
            self._last_ram = ram

            # Enregistrer dans les trackers à chaque tick
            self._cpu_tracker.record(cpu)
            self._ram_tracker.record(ram)

            # --- CPU ---
            if cpu >= ABSOLUTE_SAFETY_LIMITS["cpu_absolute_crit"]:
                # Niveau 1 : sécurité absolue (grenouille bouillie)
                threats["cpu"] = 8.0
            elif self._cpu_tracker.is_ready():
                # Niveau 2 : adaptatif (percentiles glissants) + planchers absolus
                cpu_pct = self._cpu_tracker.get_percentile_of(cpu)
                if cpu_pct >= ADAPTIVE_THRESHOLDS["cpu_crit_percentile"] and cpu >= ADAPTIVE_THRESHOLDS["cpu_crit_floor"]:
                    threats["cpu"] = 8.0
                elif cpu_pct >= ADAPTIVE_THRESHOLDS["cpu_warn_percentile"] and cpu >= ADAPTIVE_THRESHOLDS["cpu_warn_floor"]:
                    threats["cpu"] = 4.0
            else:
                # Niveau 3 : cold start (seuils hardcodés)
                if cpu >= THRESHOLDS["cpu_crit"]:
                    threats["cpu"] = 8.0
                elif cpu >= THRESHOLDS["cpu_warn"]:
                    threats["cpu"] = 4.0

            # --- RAM ---
            if ram >= ABSOLUTE_SAFETY_LIMITS["ram_absolute_crit"]:
                # Niveau 1 : sécurité absolue
                threats["ram"] = 8.0
                now = time.time()
                if now - self._ram_critical_logged > 120:
                    logger.warning(
                        f"REPTILIEN: RAM CRITIQUE {ram:.0f}% (seuil absolu {ABSOLUTE_SAFETY_LIMITS['ram_absolute_crit']}%)"
                    )
                    self._ram_critical_logged = now
            elif self._ram_tracker.is_ready():
                # Niveau 2 : adaptatif + planchers absolus
                ram_pct = self._ram_tracker.get_percentile_of(ram)
                if ram_pct >= ADAPTIVE_THRESHOLDS["ram_crit_percentile"] and ram >= ADAPTIVE_THRESHOLDS["ram_crit_floor"]:
                    threats["ram"] = 8.0
                    now = time.time()
                    if now - self._ram_critical_logged > 120:
                        logger.warning(
                            f"REPTILIEN: RAM CRITIQUE {ram:.0f}% (percentile {ram_pct:.2f})"
                        )
                        self._ram_critical_logged = now
                elif ram_pct >= ADAPTIVE_THRESHOLDS["ram_warn_percentile"] and ram >= ADAPTIVE_THRESHOLDS["ram_warn_floor"]:
                    threats["ram"] = 4.0
                    now = time.time()
                    if now - self._ram_critical_logged > 300:
                        logger.info(f"REPTILIEN: RAM élevée {ram:.0f}% (percentile {ram_pct:.2f})")
                        self._ram_critical_logged = now
            else:
                # Niveau 3 : cold start
                if ram >= THRESHOLDS["ram_crit"]:
                    threats["ram"] = 8.0
                    now = time.time()
                    if now - self._ram_critical_logged > 120:
                        logger.warning(
                            f"REPTILIEN: RAM CRITIQUE {ram:.0f}% (seuil {THRESHOLDS['ram_crit']}%)"
                        )
                        self._ram_critical_logged = now
                elif ram >= THRESHOLDS["ram_warn"]:
                    threats["ram"] = 4.0
                    now = time.time()
                    if now - self._ram_critical_logged > 300:
                        logger.info(f"REPTILIEN: RAM élevée {ram:.0f}% (seuil {THRESHOLDS['ram_warn']}%)")
                        self._ram_critical_logged = now

            # Fuite mémoire Python
            proc = psutil.Process()
            proc_mem_mb = proc.memory_info().rss / (1024 * 1024)
            if proc_mem_mb >= THRESHOLDS["process_mem_warn_mb"]:
                threats["process_memory"] = 5.0
        except Exception:
            pass  # psutil indisponible → pas de menace détectée

        # --- Ollama process (adaptatif: skip si stable) ---
        OLLAMA_CHECK_SKIP = 12  # 12 cycles * 5s = 60s entre checks si stable
        if self._ollama_ok and self._ollama_ok_streak >= OLLAMA_CHECK_SKIP:
            self._ollama_ok_streak += 1  # Continue streak, skip check
        else:
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    resp = await client.get("http://localhost:11434/api/tags", timeout=2.0)
                    if resp.status_code != 200:
                        threats["ollama"] = 9.0
                        self._ollama_ok = False
                        self._ollama_ok_streak = 0
                    else:
                        self._ollama_ok = True
                        self._ollama_ok_streak += 1
            except Exception:
                threats["ollama"] = 9.0
                self._ollama_ok = False
                self._ollama_ok_streak = 0

        # --- Ollama GPU hang (circuit breaker) ---
        # Le process Ollama peut répondre à /api/tags mais le GPU est bloqué
        # → les inférences timeout. Le circuit breaker de BaseAgent le détecte.
        try:
            from core.base_agent import BaseAgent
            health = BaseAgent.get_ollama_health()
            if health["circuit_open"]:
                threats["ollama_hung"] = 8.0
            elif health["consecutive_timeouts"] >= 2:
                threats["ollama_hung"] = 5.0
        except Exception:
            pass

        # --- Error Streak (depuis AutonomyEngine) ---
        try:
            from core.autonomy_engine import autonomy
            streak = getattr(autonomy, "error_streak", 0)

            if streak >= THRESHOLDS["error_streak_critical"]:
                threats["error_streak"] = 9.0
            elif streak >= THRESHOLDS["error_streak_danger"]:
                threats["error_streak"] = 6.0
            elif streak >= THRESHOLDS["error_streak_alert"]:
                threats["error_streak"] = 3.0

            # Budget
            daily_count = getattr(autonomy, "daily_count", 0)
            budget_used = getattr(autonomy, "daily_budget_used", 0)
            from core.autonomy_engine import MAX_DAILY_ROUTINES, DAILY_BUDGET_POINTS
            count_ratio = daily_count / max(1, MAX_DAILY_ROUTINES)
            budget_ratio = budget_used / max(1, DAILY_BUDGET_POINTS)
            ratio = max(count_ratio, budget_ratio)
            self._last_budget_ratio = ratio

            if ratio >= THRESHOLDS["budget_crit_ratio"]:
                # Budget épuisé → SHED (limiter aux routines pas chères), PAS FREEZE
                # Le budget ne peut pas baisser pendant FREEZE → spirale infinie sinon
                threats["budget"] = 5.0
            elif ratio >= THRESHOLDS["budget_warn_ratio"]:
                threats["budget"] = 4.0

            # Mémoire corrompue
            health = getattr(autonomy, "last_health_check", None)
            if health and isinstance(health, dict):
                mem_status = health.get("memory", {})
                if isinstance(mem_status, dict) and mem_status.get("status") == "down":
                    threats["memory_corruption"] = 5.0
        except Exception:
            pass

        # --- Hallucination Storm (fenêtre glissante) ---
        now = time.time()
        window = THRESHOLDS["hallucination_window"]
        self._hallucination_timestamps = [
            t for t in self._hallucination_timestamps if now - t < window
        ]
        if len(self._hallucination_timestamps) >= THRESHOLDS["hallucination_storm"]:
            threats["hallucination_storm"] = 6.0

        # --- Inactivité prolongée ---
        # Ne PAS compter l'idle pendant FREEZE/SHED (c'est le système qui bloque,
        # sinon cercle vicieux : FREEZE→pas de routine→idle monte→menace reste→FREEZE)
        # Ne PAS compter l'idle pendant le sommeil circadien (repos volontaire)
        freeze_active = time.time() < self._freeze_until
        shed_active = time.time() < self._shed_until
        is_sleeping = False
        try:
            from core.circadian_rhythm import circadian
            is_sleeping = circadian.get_phase() in ("sommeil_profond", "crepuscule")
        except Exception:
            pass
        if now - self._last_activity > THRESHOLDS["idle_too_long"] and not freeze_active and not shed_active and not is_sleeping:
            threats["idle"] = 2.0

        # --- Habituation : atténuer les menaces chroniques stables ---
        self._update_habituation(threats)

        self._last_threats = threats
        return threats

    # ============================================================
    # Habituation — Atténuation des menaces chroniques
    # ============================================================

    def _update_habituation(self, threats: Dict[str, float]):
        """Atténue les menaces qui persistent sans changement significatif.

        Le reptilien s'habitue aux stimuli constants (ex: RAM à 62%).
        Après HABITUATION_ONSET ticks, le facteur d'atténuation descend
        progressivement de 1.0 vers HABITUATION_FLOOR (0.3).
        """
        active_sources = set(threats.keys())

        # Sources disparues → récupérer progressivement
        for source in list(self._threat_persistence.keys()):
            if source not in active_sources:
                hab = self._threat_habituation.get(source, 1.0)
                self._threat_habituation[source] = min(1.0, hab + HABITUATION_RECOVERY_RATE)
                self._threat_persistence[source] = 0
                if self._threat_habituation[source] >= 1.0:
                    self._threat_persistence.pop(source, None)
                    self._threat_habituation.pop(source, None)

        # Sources actives → compter et atténuer
        for source, level in list(threats.items()):
            self._threat_persistence[source] = self._threat_persistence.get(source, 0) + 1
            ticks = self._threat_persistence[source]

            if ticks > HABITUATION_ONSET:
                # Atténuation progressive après onset
                hab = self._threat_habituation.get(source, 1.0)
                hab = max(HABITUATION_FLOOR, hab - HABITUATION_DECAY_PER_TICK)
                self._threat_habituation[source] = hab
                threats[source] = level * hab

    # ============================================================
    # Calcul du threat level composite
    # ============================================================

    def _compute_threat_level(self, threats: Dict[str, float]) -> float:
        """Threat level = MAX des menaces + bonus cascade."""
        if not threats:
            return 0.0
        max_threat = max(threats.values())
        active_count = sum(1 for v in threats.values() if v >= THREAT_ALERT)
        # Chaque menace simultanée ajoute +0.5
        cascade_bonus = max(0, (active_count - 1)) * 0.5
        return min(10.0, max_threat + cascade_bonus)

    # ============================================================
    # Réflexes — Les 6 réponses primitives
    # ============================================================

    async def _trigger_reflexes(self, threats: Dict[str, float]):
        """Déclenche le réflexe approprié selon le niveau de menace."""
        now = time.time()

        # Budget épuisé seul → pas de réflexes (l'autonomie gère déjà la pause)
        budget_only = (len(threats) == 1 and "budget" in threats)
        if budget_only:
            if self._can_trigger("SHED", now):
                self._reflex_cooldowns["SHED"] = now
                logger.info(f"REPTILIEN: Budget épuisé (menace={self.threat_level:.1f}) — pas de réflexe, autonomie gère.")
            return

        # FREEZE — threat >= 7 (arrêt complet)
        if self.threat_level >= 7.0 and self._can_trigger("FREEZE", now):
            self._activate_freeze(now)
            self._condition_threat_from_current(threats, "FREEZE")
            await self._publish_alert("FREEZE", threats)

        # FIGHT — threat >= 6 + ollama down (tentative recovery)
        elif self.threat_level >= 6.0 and "ollama" in threats and self._can_trigger("FIGHT", now):
            await self._fight_response()
            self._condition_threat_from_current(threats, "FIGHT")
            await self._publish_alert("FIGHT", threats)

        # SHED — threat >= 5 (limiter aux routines légères)
        elif self.threat_level >= 5.0 and self._can_trigger("SHED", now):
            # Déterminer la cause principale pour graduer la sévérité
            shed_cause = max(threats, key=threats.get) if threats else ""
            self._activate_shed(now, cause=shed_cause)
            self._condition_threat_from_current(threats, "SHED")
            await self._publish_alert("SHED", threats)

        # FLINCH — threat >= 4 (veto routine en cours)
        elif self.threat_level >= 4.0 and self._can_trigger("FLINCH", now):
            self._activate_flinch(threats)
            self._condition_threat_from_current(threats, "FLINCH")
            await self._publish_alert("FLINCH", threats)

        # ADRENALINE — threat >= 3 (boost cardiaque)
        if self.threat_level >= 3.0 and self._can_trigger("ADRENALINE", now):
            self._inject_adrenaline()
            self._condition_threat_from_current(threats, "ADRENALINE")

    def _can_trigger(self, reflex: str, now: float) -> bool:
        """Vérifie le cooldown d'un réflexe (avec escalade anti-spam pour FLINCH)."""
        cooldown = REFLEX_COOLDOWNS.get(reflex, 30)
        # Anti-spam FLINCH : cooldown exponentiel si même menace persistante
        # 30s → 60s → 120s → 240s → 300s cap
        if reflex == "FLINCH" and self._flinch_consecutive > 1:
            escalation = min(2 ** (self._flinch_consecutive - 1), 10)
            cooldown = min(cooldown * escalation, 300)
        last = self._reflex_cooldowns.get(reflex, 0)
        return (now - last) >= cooldown

    def _record_reflex(self, reflex: str):
        """Enregistre le déclenchement d'un réflexe."""
        self._reflex_cooldowns[reflex] = time.time()
        self.reflexes_triggered[reflex] = self.reflexes_triggered.get(reflex, 0) + 1

    # --- FREEZE ---
    def _activate_freeze(self, now: float):
        """Arrêt complet des routines autonomes."""
        self._freeze_until = now + 180  # 3 minutes de freeze
        self._last_activity = now  # Reset idle pour éviter spirale FREEZE→idle→FREEZE
        self._record_reflex("FREEZE")
        logger.warning(f"REPTILIEN: FREEZE activé (menace={self.threat_level:.1f})")

    # --- SHED ---
    def _activate_shed(self, now: float, cause: str = ""):
        """Limiter aux routines légères. max_cost graduée selon la cause.
        Budget → max_cost=4 (permet GRIMOIRE/REFACTOR).
        Erreurs/hallucinations → max_cost=2 (strict)."""
        self._shed_until = now + 120  # 2 minutes de shed
        if cause == "budget":
            self._shed_max_cost = 4
        else:
            self._shed_max_cost = 2
        self._record_reflex("SHED")
        logger.warning(f"REPTILIEN: SHED activé (menace={self.threat_level:.1f}, max_cost={self._shed_max_cost}, cause={cause or 'unknown'})")

    # --- FLINCH ---
    def _activate_flinch(self, threats: Dict[str, float]):
        """Veto immédiat de la routine en cours."""
        top_threat = max(threats, key=threats.get) if threats else "unknown"
        # Anti-spam : tracker les déclenchements consécutifs pour la même menace
        if top_threat == self._flinch_last_threat:
            self._flinch_consecutive += 1
        else:
            self._flinch_consecutive = 1
            self._flinch_last_threat = top_threat
        self._flinch_reason = f"FLINCH: menace {top_threat} (niveau={self.threat_level:.1f})"
        self._record_reflex("FLINCH")
        # Log uniquement les 2 premiers, puis supprimé (anti-spam logs)
        if self._flinch_consecutive <= 2:
            logger.warning(f"REPTILIEN: {self._flinch_reason}")
        else:
            logger.debug(f"REPTILIEN: {self._flinch_reason} (supprimé x{self._flinch_consecutive})")

    # --- ADRENALINE ---
    def _inject_adrenaline(self):
        """Injecte de l'adrénaline → CardiacEngine."""
        self.adrenaline = min(1.0, self.adrenaline + 0.4)
        self._record_reflex("ADRENALINE")

        try:
            from core.cardiac_engine import heart
            heart.react("threat")
        except Exception:
            pass

        logger.info(f"REPTILIEN: Adrénaline injectée ({self.adrenaline:.2f})")

    # --- FIGHT ---
    async def _fight_response(self):
        """Tentative de recovery Ollama."""
        self._record_reflex("FIGHT")
        logger.warning("REPTILIEN: FIGHT — tentative de recovery Ollama")

        # Tester à nouveau Ollama après un court délai
        await asyncio.sleep(2.0)
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://localhost:11434/api/tags", timeout=3.0)
                if resp.status_code == 200:
                    logger.info("REPTILIEN: FIGHT — Ollama récupéré !")
                    self.threat_level = max(0, self.threat_level - 3.0)
                else:
                    logger.warning("REPTILIEN: FIGHT — Ollama toujours down")
        except Exception:
            logger.warning("REPTILIEN: FIGHT — Ollama injoignable")

    # ============================================================
    # Amygdale — Conditionnement pavlovien
    # ============================================================

    def _condition_threat_from_current(self, threats: Dict[str, float], reflex: str):
        """Conditionne les menaces actuelles au réflexe déclenché."""
        for pattern, severity in threats.items():
            if severity >= THREAT_ALERT:
                self._condition_threat(pattern, severity, reflex)
        # Pont vers la nouvelle amygdale
        try:
            from core.amygdala import amygdala as _amyg
            for pattern, severity in threats.items():
                if severity >= THREAT_ALERT:
                    _amyg.condition(pattern, valence=-severity / 10.0,
                                   arousal=min(1.0, severity / 8.0),
                                   reflex=reflex)
        except Exception:
            pass

    def _condition_threat(self, pattern: str, severity: float, reflex: str):
        """Renforce l'association pattern → réflexe (conditionnement)."""
        if pattern in self.threat_memories:
            mem = self.threat_memories[pattern]
            mem.occurrences += 1
            mem.severity = mem.severity * 0.7 + severity * 0.3  # Moyenne mobile
            mem.last_seen = time.time()
            # Le réflexe le plus grave l'emporte
            reflex_order = ["ADRENALINE", "FLINCH", "SHED", "FIGHT", "FREEZE"]
            if reflex in reflex_order and mem.conditioned_reflex in reflex_order:
                if reflex_order.index(reflex) > reflex_order.index(mem.conditioned_reflex):
                    mem.conditioned_reflex = reflex
        else:
            self.threat_memories[pattern] = ThreatMemory(
                pattern=pattern, severity=severity,
                occurrences=1, last_seen=time.time(),
                conditioned_reflex=reflex
            )

    def get_conditioned_response(self, pattern: str) -> Optional[ThreatMemory]:
        """Consulte l'amygdale pour un pattern donné."""
        return self.threat_memories.get(pattern)

    # ============================================================
    # Événements Bus
    # ============================================================

    def _subscribe_events(self):
        """Souscription aux événements du bus."""
        if self._subscribed:
            return
        self._subscribed = True

        try:
            from core.event_bus.bus import bus
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            bus.subscribe("CI_PIPELINE_RESULT", self._on_ci_result)
            bus.subscribe("HALLUCINATION_DETECTED", self._on_hallucination)
            bus.subscribe("AUTONOMY_HEARTBEAT", self._on_autonomy_heartbeat)
            bus.subscribe("OLLAMA_UNRESPONSIVE", self._on_ollama_event)
            bus.subscribe("TISSUE_ZONE_OVERLOAD", self._on_tissue_overload)
            bus.subscribe("TISSUE_EXTINCTION_RISK", self._on_tissue_extinction)
            bus.subscribe("TISSUE_THREAT_SUBSIDED", self._on_tissue_threat_subsided)
            bus.subscribe("TISSUE_PANDEMIC_START", self._on_tissue_pandemic)
            bus.subscribe("NAP_MODE", self._on_nap_mode)
            bus.subscribe("HYPOTHALAMUS_ALARM", self._on_hypothalamus_alarm)
            # --- Nerf vague numérique (signaux parasympathiques) ---
            bus.subscribe("DOPAMINE_SURGE", self._on_parasympathetic_signal)
            bus.subscribe("EUREKA_MOMENT", self._on_parasympathetic_signal)
            bus.subscribe("CURIOSITY_LEARNING", self._on_parasympathetic_signal)
            bus.subscribe("SENSORIUM_RECOVERY", self._on_parasympathetic_signal)
        except Exception as e:
            logger.warning(f"REPTILIEN: Échec souscription bus: {e}")

    async def _on_routine_complete(self, event: dict):
        """Routine terminée → apaisement si succès."""
        status = event.get("status", "")
        if status == "success":
            self.on_routine_success(event.get("intent", ""))
        self._last_activity = time.time()

    async def _on_ci_result(self, event: dict):
        """Résultat CI → calme si succès, menace si échec."""
        if event.get("success"):
            self.threat_level = max(0, self.threat_level - 1.0)
        else:
            self.threat_level = min(10.0, self.threat_level + 2.0)

    async def _on_hallucination(self, event: dict):
        """Hallucination détectée → incrémente le compteur storm."""
        self._hallucination_timestamps.append(time.time())
        logger.debug(f"REPTILIEN: Hallucination enregistrée ({len(self._hallucination_timestamps)} en fenêtre)")

    async def _on_autonomy_heartbeat(self, event: dict):
        """Sync avec le heartbeat autonomy pour les données fraîches."""
        self._last_activity = time.time()

    async def _on_ollama_event(self, event: dict):
        """Circuit breaker Ollama ouvert → menace immédiate sans attendre le prochain cycle."""
        timeouts = event.get("consecutive_timeouts", 0)
        threat_boost = 8.0 if timeouts >= 3 else 5.0
        self.threat_level = min(10.0, max(self.threat_level, threat_boost))
        logger.warning(f"REPTILIEN: Ollama unresponsive (timeouts={timeouts}, threat→{self.threat_level:.1f})")

    async def _on_tissue_overload(self, event: dict):
        """Zone tissu surchargée → plancher de menace (pas d'accumulation)."""
        zone = event.get("zone", "?")
        self.threat_level = max(self.threat_level, TISSUE_OVERLOAD_THREAT)
        logger.info(f"REPTILIEN: Tissue zone {zone} surchargée → threat plancher {TISSUE_OVERLOAD_THREAT}")

    async def _on_tissue_extinction(self, event: dict):
        """Population tissu en danger → plancher de menace (pas d'accumulation)."""
        pop = event.get("population", 0)
        self.threat_level = max(self.threat_level, TISSUE_EXTINCTION_THREAT)
        logger.warning(f"REPTILIEN: Tissue extinction risk (pop={pop}) → threat plancher {TISSUE_EXTINCTION_THREAT}")

    async def _on_tissue_threat_subsided(self, event: dict):
        """Zone threat du tissu apaisée → réduction menace (boucle rétroaction)."""
        self.threat_level = max(0.0, self.threat_level - 1.5)
        self.adrenaline = max(0.0, self.adrenaline - 0.1)
        logger.info(f"REPTILIEN: Tissue calme → threat -1.5 (now {self.threat_level:.1f})")

    async def _on_tissue_pandemic(self, event: dict):
        """Pandémie tissu déclenchée → plancher de menace."""
        motif = event.get("motif", "?")
        infected = event.get("infected_count", 0)
        self.threat_level = max(self.threat_level, 6.0)
        logger.warning(f"REPTILIEN: Pandémie tissu (motif={motif}, infectés={infected}) → threat plancher 6.0")

    async def _on_nap_mode(self, event: dict):
        """Mode sieste : endort ou réveille le reptilien."""
        active = event.get("active", False)
        if active:
            self._sleeping = True
            self.threat_level = 0.0
            self.adrenaline = 0.0
            self.save()
            logger.info("REPTILIEN: Sommeil activé — scan suspendu.")
        else:
            self._sleeping = False
            logger.info("REPTILIEN: Réveil — scan repris.")

    async def _on_hypothalamus_alarm(self, event: dict):
        """Alarme homéostatique → plancher de menace (pas d'accumulation).

        Utilise max() comme les handlers tissue, pas += additif.
        Cela empêche le bombardement cumulatif quand plusieurs variables
        sont en alarme simultanément.
        """
        variable = event.get("variable", "")
        severity = event.get("severity", 0.5)
        # Plancher proportionnel à la severity (max 3.0 pour ne pas éclipser les vraies menaces)
        floor = min(3.0, severity * 2.0)
        self.threat_level = max(self.threat_level, floor)
        logger.info(f"REPTILIEN: Alarme hypothalamique ({variable}, severity={severity:.2f}) → plancher {floor:.1f}")

    async def _on_parasympathetic_signal(self, event: dict):
        """Signal parasympathique — le cortex rassure le tronc cérébral.

        Effet proportionnel au stress actuel :
        - Calme (threat 0.5) → quasi pas d'effet (0.125)
        - Panique (threat 8.0) → effet maximum (-2.0)
        """
        relief = min(2.0, self.threat_level * 0.25)
        if relief > 0:
            self.threat_level = max(0.0, self.threat_level - relief)
            self.adrenaline = max(0.0, self.adrenaline - 0.15)

            # Notifier le coeur
            try:
                from core.cardiac_engine import heart
                heart.react("soothe")
            except Exception:
                pass

            logger.info(
                f"REPTILIEN: Signal parasympathique reçu → threat -{relief:.1f} "
                f"(now {self.threat_level:.1f})"
            )

    def on_routine_success(self, intent: str = ""):
        """Apaisement après un succès — appelé par AutonomyEngine."""
        # Réduction proportionnelle de la menace
        self.threat_level = max(0, self.threat_level - 1.0)
        self.adrenaline = max(0, self.adrenaline - 0.1)

        # Reset FLINCH + anti-spam
        self._flinch_reason = ""
        self._flinch_consecutive = 0
        self._flinch_last_threat = ""

    # ============================================================
    # Publication bus
    # ============================================================

    async def _publish_alert(self, reflex: str, threats: Dict[str, float]):
        """Publie REPTILIAN_ALERT sur le bus."""
        try:
            from core.event_bus.bus import bus
            await bus.publish("REPTILIAN_ALERT", {
                "reflex": reflex,
                "threat_level": round(self.threat_level, 1),
                "adrenaline": round(self.adrenaline, 2),
                "threats": {k: round(v, 1) for k, v in threats.items()},
                "timestamp": time.time(),
            })
        except Exception:
            pass

    def _publish_state(self):
        """Publie REPTILIAN_STATE (fire-and-forget)."""
        try:
            loop = asyncio.get_running_loop()
            from core.event_bus.bus import bus
            loop.create_task(bus.publish("REPTILIAN_STATE", self.get_stats()))
        except RuntimeError:
            pass

    # ============================================================
    # API Publique — consultée par AutonomyEngine
    # ============================================================

    def should_freeze(self) -> bool:
        """True si FREEZE actif."""
        return time.time() < self._freeze_until

    def should_shed(self) -> Tuple[bool, int]:
        """True + max_cost si SHED actif."""
        if time.time() < self._shed_until:
            return True, self._shed_max_cost
        return False, 0

    def should_flinch(self, intent: str = "") -> str:
        """Raison du veto ou '' si pas de FLINCH."""
        if self._flinch_reason:
            reason = self._flinch_reason
            self._flinch_reason = ""  # Consommé une fois
            return reason

        # Consulter l'amygdale pour cet intent
        if intent:
            mem = self.threat_memories.get(f"error_streak")
            if mem and mem.severity >= 7.0 and mem.conditioned_reflex in ("FREEZE", "SHED"):
                return f"amygdale: {mem.pattern} associé à {mem.conditioned_reflex} (gravité={mem.severity:.1f})"

        return ""

    def get_adrenaline(self) -> float:
        """Niveau d'adrénaline [0.0, 1.0]."""
        return self.adrenaline

    def get_threat_level(self) -> float:
        """Niveau de menace [0.0, 10.0]."""
        return self.threat_level

    def get_stats(self) -> Dict[str, Any]:
        """État complet pour endpoint API et télémétrie."""
        return {
            "threat_level": round(self.threat_level, 1),
            "adrenaline": round(self.adrenaline, 2),
            "freeze_active": self.should_freeze(),
            "shed_active": self.should_shed()[0],
            "last_threats": {k: round(v, 1) for k, v in self._last_threats.items()},
            "reflexes_triggered": dict(self.reflexes_triggered),
            "threat_memories_count": len(self.threat_memories),
            "hallucination_count_window": len(self._hallucination_timestamps),
            "beat_counter": self._beat_counter,
            "alive": self._alive,
            # Capteurs bruts pour télémétrie
            "cpu_percent": round(self._last_cpu, 1),
            "ram_percent": round(self._last_ram, 1),
            "budget_ratio": round(self._last_budget_ratio, 3),
            "ollama_ok": self._ollama_ok,
            # Trackers adaptatifs
            "cpu_tracker_ready": self._cpu_tracker.is_ready(),
            "cpu_tracker_samples": self._cpu_tracker.count(),
            "ram_tracker_ready": self._ram_tracker.is_ready(),
            "ram_tracker_samples": self._ram_tracker.count(),
        }

    # ============================================================
    # Persistance
    # ============================================================

    def save(self):
        """Sauvegarde atomique de l'état reptilien."""
        state = {
            "version": "1.0",
            "threat_level": self.threat_level,
            "adrenaline": self.adrenaline,
            "reflexes_triggered": self.reflexes_triggered,
            "threat_memories": {
                k: asdict(v) for k, v in self.threat_memories.items()
            },
            "freeze_until": self._freeze_until,
            "shed_until": self._shed_until,
            "shed_max_cost": self._shed_max_cost,
            "beat_counter": self._beat_counter,
            "cpu_tracker": self._cpu_tracker.to_dict(),
            "ram_tracker": self._ram_tracker.to_dict(),
            "threat_persistence": self._threat_persistence,
            "threat_habituation": self._threat_habituation,
            "timestamp": time.time(),
        }
        try:
            os.makedirs(os.path.dirname(REPTILIAN_STATE_FILE), exist_ok=True)
            tmp = REPTILIAN_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, REPTILIAN_STATE_FILE)
        except Exception as e:
            logger.warning(f"REPTILIEN: Échec sauvegarde: {e}")

    def _load(self):
        """Charge l'état reptilien depuis le fichier."""
        try:
            if not os.path.exists(REPTILIAN_STATE_FILE):
                return
            with open(REPTILIAN_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)

            self.threat_level = float(state.get("threat_level", 0.0))
            self.adrenaline = float(state.get("adrenaline", 0.0))
            self.reflexes_triggered = state.get("reflexes_triggered", {})
            self._freeze_until = float(state.get("freeze_until", 0.0))
            self._shed_until = float(state.get("shed_until", 0.0))
            self._shed_max_cost = int(state.get("shed_max_cost", 2))
            self._beat_counter = int(state.get("beat_counter", 0))

            # Restaurer les ThreatMemory
            raw_memories = state.get("threat_memories", {})
            for k, v in raw_memories.items():
                try:
                    self.threat_memories[k] = ThreatMemory(**v)
                except Exception:
                    pass

            # Restaurer les trackers adaptatifs
            try:
                from core.percentile_tracker import PercentileTracker
                if "cpu_tracker" in state:
                    self._cpu_tracker = PercentileTracker.from_dict(state["cpu_tracker"])
                if "ram_tracker" in state:
                    self._ram_tracker = PercentileTracker.from_dict(state["ram_tracker"])
            except Exception:
                pass  # Trackers restent vides — cold start

            # Restaurer l'habituation
            self._threat_persistence = state.get("threat_persistence", {})
            self._threat_habituation = {
                k: float(v) for k, v in state.get("threat_habituation", {}).items()
            }

            logger.info(f"REPTILIEN: État chargé (menace={self.threat_level:.1f}, "
                        f"mémoires={len(self.threat_memories)})")
        except Exception as e:
            logger.warning(f"REPTILIEN: Échec chargement état: {e}")


# ============================================================
# Singleton global
# ============================================================

reptile = ReptilianCore()
