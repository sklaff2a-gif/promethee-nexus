"""Tension Protocol — Interface pour la fermeture homeostatique des goals.

Tout organe qui genere des goals via le systeme de pulsions doit implementer
cette interface pour permettre au prefrontal de mesurer si la tension qui a
declenche le goal est REELLEMENT retombee, plutot que de fermer le goal sur
le simple fait que ses routines aient termine (Loi de Goodhart).

Concept central : credit assignment via modele counterfactual.
  causal_drop = expected_if_no_action - actual_current_tension

Si causal_drop > seuil → l'action a vraiment aide → fermer le goal.
Si causal_drop ≈ 0 → decay/rise naturel uniquement, l'action n'a rien fait.
Si causal_drop < 0 → l'action a fait pire que rien faire (frustration).

Architecture :
  1. Goal cree → l'organe source enregistre tension_at_birth dans goal.metadata
  2. Routine executee → desire_engine.on_event() applique des deltas via EVENT_IMPACT
  3. _check_goal_completion → appelle source_organ.measure_tension(goal.metadata)
  4. Si is_resolved → fermeture homeostatique, dopamine RPE positif
  5. Sinon → 3 garde-fous : budget, frustration progressive, mutation

Cette interface est progressive : les organes qui ne l'implementent pas encore
font tomber les goals dans le fallback bureaucratique (Mur de la Honte).
"""

from dataclasses import dataclass, field
from typing import Protocol, Optional, Any, Dict


@dataclass
class TensionMeasurement:
    """Resultat d'une mesure de tension par un organe source.

    Tous les champs sont en valeurs absolues de tension (echelle organe-specifique).
    Pour desire_engine, c'est typiquement la deprivation 0-100.
    """
    current_tension: float
    """Tension actuelle (lue maintenant)."""

    tension_at_birth: float
    """Tension au moment ou le goal a ete cree (snapshot)."""

    expected_if_no_action: float
    """Counterfactual : ce que serait la tension SANS aucune action.
    Pour les drives qui montent naturellement: tension_at_birth + rise_rate * elapsed.
    Pour les drives qui descendent naturellement: tension_at_birth - decay_rate * elapsed.
    Sert de baseline pour isoler la contribution causale de l'action."""

    causal_drop: float
    """expected_if_no_action - current_tension.
    > 0 : action a aide (la tension est plus basse que sans action)
    ≈ 0 : decay/rise naturel uniquement
    < 0 : action contre-productive"""

    explicit_attribution: float = 0.0
    """Somme des satisfactions attribuees explicitement a ce goal_id par les
    routines (Solution C). Plus precis que causal_drop quand disponible."""

    is_resolved: bool = False
    """True si la tension a baisse assez pour considerer le goal comme resolu.
    Calcule par l'organe selon ses propres seuils (ex: causal_drop > 50% de tension_at_birth)."""

    is_worsened: bool = False
    """True si causal_drop est significativement negatif. Declenche frustration."""

    extra: Dict[str, Any] = field(default_factory=dict)
    """Donnees supplementaires propres a chaque organe (ex: desire_engine peut
    retourner le rise_rate effectif, le drive name, etc.)."""


class TensionSource(Protocol):
    """Protocole : tout organe qui genere des goals doit implementer cette methode.

    Les organes existants : desire_engine (drives), hippocampus (KNOWLEDGE_GAP),
    synaptic_network (EUREKA_BRIDGE), council (consensus).

    L'implementation est progressive : tant qu'un organe ne l'a pas, ses goals
    tombent dans le fallback bureaucratique du _check_goal_completion.
    """

    def measure_tension(self, goal_meta: Dict[str, Any]) -> TensionMeasurement:
        """Mesure la tension pour un goal donne.

        Args:
            goal_meta: doit contenir au minimum :
                - source_organ: nom de l'organe (ex: "desire_engine")
                - source_key: identifiant interne (ex: "CURIOSITE")
                - tension_at_birth: snapshot a la creation (float)
                - created_at: timestamp de creation (float)
                - goal_id: identifiant du goal (pour attribution explicite)

        Returns:
            TensionMeasurement avec causal_drop, is_resolved, etc.
        """
        ...


def make_goal_metadata(source_organ: str, source_key: str,
                       tension_at_birth: float, goal_id: str,
                       created_at: float) -> Dict[str, Any]:
    """Helper pour construire un metadata coherent a la creation d'un goal."""
    return {
        "source_organ": source_organ,
        "source_key": source_key,
        "tension_at_birth": float(tension_at_birth),
        "created_at": float(created_at),
        "goal_id": goal_id,
        "attempts_count": 0,
        "fruitless_cycles": 0,
        "max_fruitless": 5,  # Garde-fou : nombre max de cycles steriles avant abandon
        "completion_mode": None,  # Sera rempli a la fermeture : "homeostatic"|"bureaucratic"|"abandoned"
    }
