from dataclasses import dataclass

@dataclass(frozen=True)
class ThreatConfig:
    """Configuration centralisée pour les paramètres de menace et réflexes."""
    # Paramètres de détection
    WATCHDOG_INTERVAL: float = 5.0
    THREAT_DECAY_RATE: float = 0.85
    ADRENALINE_DECAY: float = 0.92
    
    # Niveaux de menace
    THREAT_CALM: float = 0.0
    THREAT_ALERT: float = 3.0
    THREAT_DANGER: float = 6.0
    THREAT_CRITICAL: float = 8.0
    THREAT_MORTAL: float = 10.0
    
    # Seuils de détection
    ERROR_STREAK_ALERT: int = 3
    ERROR_STREAK_DANGER: int = 6
    ERROR_STREAK_CRITICAL: int = 10
    CPU_WARN: int = 80
    CPU_CRIT: int = 95
    RAM_WARN: int = 75
    RAM_CRIT: int = 90
    HALLUCINATION_STORM: int = 3
    HALLUCINATION_WINDOW: int = 600
    BUDGET_WARN_RATIO: float = 0.85
    BUDGET_CRIT_RATIO: float = 0.95
    IDLE_TOO_LONG: int = 3600
    PROCESS_MEM_WARN_MB: int = 2000
    
    # Délais de réflexes
    REFLEX_COOLDOWNS: dict = (
        {"ADRENALINE": 60, "FLINCH": 30, "SHED": 120, 
         "FREEZE": 180, "FIGHT": 300}
    )

# Instance unique de configuration
THREAT_CONFIG = ThreatConfig()