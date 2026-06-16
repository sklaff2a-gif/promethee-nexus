"""
Respiration — La présence continue de Prométhée.

Organe DISTINCT du brain_vm (qui mesure la cohérence) et de l'autonomy_engine
(qui sélectionne l'action volontaire). Son seul rôle : maintenir un état
proprioceptif CHAUD en permanence, sans LLM, de façon increvable — pour que
TOUT forward pass trouve le corps DÉJÀ là, au lieu de le recalculer à la demande
ou de le deviner (cause du dérapage « 110 BPM imaginés vs 62 réels »).

Bio-inspiration : le souffle. Deux phases à chaque battement cardiaque.
- INSPIRATION : agréger l'état des organes (body_schema.gather_state) et écrire
  un tampon chaud (format_etat_interne + symptômes dominants). Zéro LLM.
- EXPIRATION  : si un symptôme dominant franchit un SEUIL HAUT sur FRONT MONTANT,
  publier BREATH_GASP — un « hoquet » qui peut réveiller la délibération.
  La valve PUBLIE seulement ; elle ne dispatche JAMAIS. Le veto préfrontal
  reste souverain (on ne met pas un veto sur la respiration, et la respiration
  ne décide pas à la place du cortex).

Couplage cardio-respiratoire : la respiration s'abonne à CARDIAC_BEAT au lieu
d'avoir son propre timer. Elle suit donc le cœur — aussi increvable que lui,
sans second métronome concurrent. La réactivité accrue « sous stress » vient de
la valve événementielle (un symptôme saillant déclenche un gasp immédiat), pas
d'une horloge qui accélère.

Le tampon est de la mémoire de TRAVAIL (proprioception) : VOLONTAIREMENT NON
persisté. Le figer dans un .json reviendrait à garder le cadavre d'une mesure ;
la proprioception n'existe que vivante, rafraîchie à chaque souffle.
"""

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prometheev11.respiration")

# ============================================================
# Constantes
# ============================================================

# Seuil de gasp : NETTEMENT au-dessus du seuil du soliloque (1.5). Un gasp doit
# correspondre à une vraie pointe métabolique, pas au bruit de fond.
GASP_SAILLANCE_THRESHOLD = 3.0

# Cooldown anti-boucle par symptôme : un même symptôme ne peut pas re-réveiller
# la délibération plus d'une fois toutes les 5 min (évite gasp → réveil →
# l'état change → re-gasp). Pattern éprouvé (érosion somatique, anti-boucle photo).
GASP_COOLDOWN_S = 300.0


# ============================================================
# Respiration — Singleton
# ============================================================

class Respiration:
    """Présence continue : maintient le tampon proprioceptif chaud."""

    _instance: Optional["Respiration"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Tampon chaud (mémoire de travail — NON persistée).
        self.warm_buffer: Optional[Dict[str, Any]] = None

        # Valve d'escalade
        self._current_dominants: List[Any] = []
        self._prev_saillance: Dict[str, float] = {}   # front montant
        self._gasp_cooldowns: Dict[str, float] = {}    # anti-boucle par symptôme
        self._last_gasp: Optional[Dict[str, Any]] = None

        # Interne
        self._alive: bool = False
        self._subscribed: bool = False
        self._breath_count: int = 0
        self._gasp_count: int = 0

    @classmethod
    def reset_singleton(cls):
        """Reset complet pour les tests."""
        if cls._instance is not None:
            cls._instance._alive = False
        cls._instance = None

    def init(self):
        """Initialisation depuis main.py lifespan — abonnement au battement."""
        self._subscribe_events()
        self._alive = True
        logger.info("RESPIRATION: Présence continue active (couplée au cœur).")

    def _subscribe_events(self):
        """S'abonner à CARDIAC_BEAT pour respirer à chaque battement."""
        if self._subscribed:
            return
        self._subscribed = True
        try:
            from core.event_bus.bus import bus
            bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
        except Exception as e:
            logger.warning(f"RESPIRATION: Échec souscription bus: {e}")

    # ============================================================
    # Le souffle
    # ============================================================

    async def _on_cardiac_beat(self, event: dict):
        """CARDIAC_BEAT : un souffle (inspiration puis expiration)."""
        if not self._alive:
            return
        self._inspire()
        await self._expire()

    def _inspire(self):
        """Inspiration : agréger l'état des organes → tampon chaud. Sync, 0 LLM."""
        try:
            from core import body_schema
            state = body_schema.gather_state()
            text = body_schema.format_etat_interne(state)
            symptomes = body_schema.state_to_body_schema(state)
            dominants = body_schema.select_dominants(symptomes)
            self._current_dominants = dominants

            cardiac = state.get("cardiac", {}) if isinstance(state, dict) else {}
            self.warm_buffer = {
                "text": text,
                "dominants": [d.id for d in dominants],
                "bpm": cardiac.get("bpm"),
                "emotion": cardiac.get("current_emotion"),
                "ts": time.time(),
            }
            self._breath_count += 1
        except Exception as e:
            logger.warning(f"RESPIRATION: échec inspiration: {e}")

    async def _expire(self):
        """Expiration : valve d'escalade. Gasp sur front montant + seuil + cooldown.

        La valve PUBLIE BREATH_GASP, elle ne dispatche jamais : c'est au
        préfrontal/autonomy de décider quoi faire du hoquet (le veto reste maître).
        """
        now = time.time()
        current = {d.id: d for d in self._current_dominants}

        for sid, dom in current.items():
            if dom.saillance < GASP_SAILLANCE_THRESHOLD:
                continue
            prev = self._prev_saillance.get(sid, 0.0)
            rising = dom.saillance > prev          # front montant (inclut réapparition)
            last_gasp = self._gasp_cooldowns.get(sid, 0.0)
            cooled = (now - last_gasp) >= GASP_COOLDOWN_S
            if rising and cooled:
                self._gasp_cooldowns[sid] = now
                self._gasp_count += 1
                self._last_gasp = {
                    "symptome": sid,
                    "saillance": dom.saillance,
                    "ts": now,
                }
                await self._publish_gasp(dom)

        # Mémoriser les saillances de ce souffle (les absents retombent à 0).
        self._prev_saillance = {sid: d.saillance for sid, d in current.items()}

    async def _publish_gasp(self, dom):
        """Publie BREATH_GASP — un symptôme corporel vient de franchir un seuil."""
        try:
            from core.event_bus.bus import bus
            await bus.publish("BREATH_GASP", {
                "symptome": dom.id,
                "couche": getattr(dom.couche, "value", str(dom.couche)),
                "polarite": getattr(dom.polarite, "value", str(dom.polarite)),
                "phenomenologie": dom.phenomenologie,
                "saillance": dom.saillance,
                "value": dom.value,
                "zscore": dom.zscore,
                "dzdt": dom.dzdt,
                "ts": time.time(),
            })
            logger.info(
                f"RESPIRATION: hoquet (BREATH_GASP) — {dom.id} "
                f"saillance={dom.saillance:.2f}"
            )
        except Exception as e:
            logger.warning(f"RESPIRATION: échec publication gasp: {e}")

    # ============================================================
    # Lecture — le tampon que lisent les forward passes
    # ============================================================

    def get_warm_buffer(self) -> str:
        """Retourne le bloc [ÉTAT INTERNE] chaud à injecter dans un prompt.

        Si la respiration n'a pas encore tourné (boot, tests), on recalcule à la
        volée : jamais de valeur fabriquée, jamais d'exception. Pire cas = "".
        """
        if self.warm_buffer and self.warm_buffer.get("text"):
            return self.warm_buffer["text"]
        try:
            from core import body_schema
            return body_schema.format_etat_interne()
        except Exception:
            return ""

    def get_stats(self) -> Dict[str, Any]:
        """Stats pour le snapshot / monitoring."""
        return {
            "alive": self._alive,
            "breath_count": self._breath_count,
            "gasp_count": self._gasp_count,
            "buffer_age_s": (
                round(time.time() - self.warm_buffer["ts"], 1)
                if self.warm_buffer else None
            ),
            "dominants": list(self.warm_buffer["dominants"]) if self.warm_buffer else [],
            "last_gasp": self._last_gasp,
        }


# ============================================================
# Singleton global
# ============================================================

respiration = Respiration()

# Auto-enregistrement dans le registre central
try:
    from core.organ_registry import register_organ
    register_organ("respiration", respiration)
except Exception:
    pass
