import logging
from typing import Dict, Any
from core.base_agent import BaseAgent # On suppose une classe de base V10

logger = logging.getLogger("security")

class DivineSecurity(BaseAgent):
    """
    DivineSecurity - Migré depuis Prométhée V9 (Olympe).
    Rôle: Cyber Defense Architect
    """
    
    def __init__(self):
        super().__init__(
            name="security",
            role="Cyber Defense Architect",
            description="Agent migré depuis la V9"
        )
        # L'ADN de l'agent (Prompt Système transféré)
        self.system_instructions = """
╔══════════════════════════════════════════════════════════════════════════════╗
║           🎯 DIVINE SECURITY V2.0 - CYBER DEFENSE MISSION                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

{error_context}

MISSION: \"{mission}\"

[[SEC] MULTI-LAYER SECURITY ARCHITECTURE V2.0]

```python
import hashlib
import hmac
import secrets
import re
import time
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from collections import deque, defaultdict
from datetime import datetime, timedelta

try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: SIGNATURE-BASED THREAT DETECTION
# ═══════════════════════════════════════════════════════════════════════════

class ThreatSignatures:
    '''
    Base de signatures d'attaques (YARA-like)
    Production: Intégrer avec threat intelligence feeds
    '''
    
    # SQL Injection patterns
    SQL_PATTERNS = [
        r\"(\\'|\\\\")(\\s)*(or|and)(\\s)*(\\'|\\\\")?(\\s)*=\",
        r\"(union)(\\s)+(select)\",
        r\"(drop|delete|insert|update)(\\s)+(table|database)\",
        r\"(exec|execute)(\\s)*\\(\",
        r\"(xp_cmdshell|sp_executesql)\"
    ]
    
    # XSS patterns
    XSS_PATTERNS = [
        r\"<script[^>]*>.*?</script>\",
        r\"javascript:\",
        r\"on(load|error|click|mouse)\\s*=\",
        r\"<iframe[^>]*>\",
        r\"eval\\s*\\(\"
    ]
    
    # Path Traversal patterns
    PATH_TRAVERSAL_PATTERNS = [
        r\"\\.\\./\",
        r\"\\.\\.\\\\/\",
        r\"%2e%2e%2f\",
        r\"%252e%252e%252f\"
    ]
    
    # Command Injection patterns
    CMD_INJECTION_PATTERNS = [
        r\";\\s*(ls|cat|rm|wget|curl|nc|bash|sh)\",
        r\"\\|\\s*(ls|cat|rm)\",
        r\"`.*?`\",
        r\"\\$\\(.*?\\)\"
    ]
    
    @classmethod
    def scan_payload(cls, payload: str) -> Tuple[str, float]:
        '''
        Scanne payload contre signatures
        
        Returns:
            (attack_type, threat_score)
        '''
        payload_lower = payload.lower()
        threat_score = 0.0
        detected_attack = \"benign\"
        
        # SQL Injection
        for pattern in cls.SQL_PATTERNS:
            if re.search(pattern, payload_lower, re.IGNORECASE):
                threat_score = max(threat_score, 85.0)
                detected_attack = \"sql_injection\"
        
        # XSS
        for pattern in cls.XSS_PATTERNS:
            if re.search(pattern, payload_lower, re.IGNORECASE):
                threat_score = max(threat_score, 75.0)
                detected_attack = \"xss\"
        
        # Path Traversal
        for pattern in cls.PATH_TRAVERSAL_PATTERNS:
            if re.search(pattern, payload, re.IGNORECASE):
                threat_score = max(threat_score, 80.0)
                detected_attack = \"path_traversal\"
        
        # Command Injection
        for pattern in cls.CMD_INJECTION_PATTERNS:
            if re.search(pattern, payload_lower, re.IGNORECASE):
                threat_score = max(threat_score, 90.0)
                detected_attack = \"command_injection\"
        
        return (detected_attack, threat_score)

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2: BEHAVIORAL ANALYSIS ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class BehavioralAnalyzer:
    '''
    Analyse comportementale (Rate Limiting + Anomaly Detection)
    '''
    
    def __init__(self, window_seconds: int = 60):
        self.window = window_seconds
        self.request_history = defaultdict(deque)  # IP → [timestamps]
        self.ip_reputation = defaultdict(float)    # IP → score (0-100)
    
    def record_request(self, source_ip: str, timestamp: float) -> float:
        '''
        Enregistre requête et calcule anomaly score
        
        Returns:
            anomaly_score (0-100)
        '''
        # Cleanup old requests
        cutoff = timestamp - self.window
        history = self.request_history[source_ip]
        
        while history and history[0] < cutoff:
            history.popleft()
        
        # Add current request
        history.append(timestamp)
        
        # Calculate request rate
        request_count = len(history)
        
        # Anomaly scoring
        if request_count > 100:  # >100 req/min = DDoS
            anomaly_score = 100.0
        elif request_count > 50:  # >50 req/min = suspicious
            anomaly_score = 70.0
        elif request_count > 20:  # >20 req/min = warning
            anomaly_score = 40.0
        else:
            anomaly_score = 0.0
        
        # Update IP reputation
        self.ip_reputation[source_ip] = (
            self.ip_reputation[source_ip] * 0.7 +  # Decay old score
            anomaly_score * 0.3                     # New score
        )
        
        return anomaly_score
    
    def get_reputation(self, source_ip: str) -> float:
        '''Get IP reputation score'''
        return self.ip_reputation.get(source_ip, 0.0)

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3: CRYPTOGRAPHIC SUITE (Enterprise-Grade)
# ═══════════════════════════════════════════════════════════════════════════

class CryptoSuite:
    '''
    Suite cryptographique complète
    '''
    
    @staticmethod
    def generate_key() -> bytes:
        '''Génère clé Fernet (AES-256)'''
        if not CRYPTO_AVAILABLE:
            return secrets.token_bytes(32)
        return Fernet.generate_key()
    
    @staticmethod
    def encrypt_data(data: str, key: bytes) -> str:
        '''Chiffrement AES-256 via Fernet'''
        if not CRYPTO_AVAILABLE:
            return f\"ENCRYPTED:{{hashlib.sha256(data.encode()).hexdigest()}}\"
        
        f = Fernet(key)
        encrypted = f.encrypt(data.encode())
        return encrypted.decode()
    
    @staticmethod
    def decrypt_data(encrypted: str, key: bytes) -> Optional[str]:
        '''Déchiffrement'''
        if not CRYPTO_AVAILABLE:
            return None
        
        try:
            f = Fernet(key)
            decrypted = f.decrypt(encrypted.encode())
            return decrypted.decode()
        except Exception:
            return None
    
    @staticmethod
    def hash_password(password: str, salt: Optional[bytes] = None) -> Tuple[str, str]:
        '''
        Hash sécurisé avec PBKDF2-HMAC-SHA256
        Production: Utiliser Argon2
        '''
        if salt is None:
            salt = secrets.token_bytes(32)
        
        # PBKDF2 with 100,000 iterations
        key = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode(),
            salt,
            100000
        )
        
        return (key.hex(), salt.hex())
    
    @staticmethod
    def verify_password(password: str, hashed: str, salt_hex: str) -> bool:
        '''Vérifie password'''
        salt = bytes.fromhex(salt_hex)
        key, _ = CryptoSuite.hash_password(password, salt)
        return hmac.compare_digest(key, hashed)
    
    @staticmethod
    def generate_hmac(message: str, secret: str) -> str:
        '''HMAC-SHA256 pour intégrité'''
        h = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        )
        return h.hexdigest()
    
    @staticmethod
    def verify_hmac(message: str, signature: str, secret: str) -> bool:
        '''Vérifie HMAC'''
        expected = CryptoSuite.generate_hmac(message, secret)
        return hmac.compare_digest(expected, signature)

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4: ZERO-TRUST INPUT VALIDATOR
# ═══════════════════════════════════════════════════════════════════════════

class ZeroTrustValidator:
    '''
    Validation Zero-Trust: Ne rien supposer de sûr
    '''
    
    # Whitelists strictes
    ALLOWED_FILENAME_CHARS = set(\"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.\")
    ALLOWED_PATH_CHARS = set(\"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./\")
    
    @classmethod
    def validate_filename(cls, filename: str) -> Tuple[bool, str]:
        '''Validation stricte des noms de fichiers'''
        
        # Check length
        if len(filename) > 255:
            return (False, \"Filename too long (>255 chars)\")
        
        # Check empty
        if not filename:
            return (False, \"Empty filename\")
        
        # Check characters
        if not all(c in cls.ALLOWED_FILENAME_CHARS for c in filename):
            return (False, \"Invalid characters in filename\")
        
        # Check dangerous patterns
        if \"..\" in filename:
            return (False, \"Path traversal attempt detected\")
        
        if filename.startswith(\".\"):
            return (False, \"Hidden files not allowed\")
        
        return (True, \"Valid\")
    
    @classmethod
    def validate_path(cls, path: str) -> Tuple[bool, str]:
        '''Validation stricte des chemins'''
        
        # Check absolute paths (dangerous)
        if path.startswith(\"/\"):
            return (False, \"Absolute paths not allowed\")
        
        # Check path traversal
        if \"..\" in path:
            return (False, \"Path traversal detected\")
        
        # Check characters
        if not all(c in cls.ALLOWED_PATH_CHARS for c in path):
            return (False, \"Invalid characters in path\")
        
        return (True, \"Valid\")
    
    @classmethod
    def sanitize_input(cls, user_input: str, max_length: int = 1000) -> str:
        '''Sanitize user input'''
        
        # Truncate
        sanitized = user_input[:max_length]
        
        # Remove null bytes
        sanitized = sanitized.replace('\\x00', '')
        
        # Remove control characters
        sanitized = ''.join(c for c in sanitized if ord(c) >= 32 or c in '\\n\\r\\t')
        
        return sanitized

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5: SIEM (Security Information & Event Management)
# ═══════════════════════════════════════════════════════════════════════════

class SimpleSIEM:
    '''
    SIEM basique pour logging + correlation
    Production: Intégrer avec Splunk/ELK
    '''
    
    def __init__(self):
        self.events = deque(maxlen=10000)
        self.alert_threshold = 70.0
    
    def log_event(
        self,
        event_type: str,
        source_ip: str,
        target: str,
        payload: str,
        threat_score: float
    ) -> Dict:
        '''Log security event'''
        
        event = {{
            \"timestamp\": datetime.now().isoformat(),
            \"event_type\": event_type,
            \"source_ip\": source_ip,
            \"target\": target,
            \"threat_score\": threat_score,
            \"payload_hash\": hashlib.sha256(payload.encode()).hexdigest()[:16]
        }}
        
        self.events.append(event)
        
        # Auto-alert on high threat
        if threat_score >= self.alert_threshold:
            print(f\"🚨 ALERT: {{event_type}} from {{source_ip}} (score: {{threat_score}})\")
        
        return event
    
    def get_recent_events(self, limit: int = 50) -> List[Dict]:
        '''Get recent security events'''
        return list(self.events)[-limit:]
    
    def get_top_threats(self, limit: int = 10) -> List[Dict]:
        '''Get highest threat events'''
        sorted_events = sorted(
            self.events,
            key=lambda e: e.get('threat_score', 0),
            reverse=True
        )
        return sorted_events[:limit]

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6: INTEGRATED SECURITY PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

class SecurityPipeline:
    '''
    Pipeline intégré: Validation → Detection → Analysis → Response → Logging
    '''
    
    def __init__(self):
        self.signatures = ThreatSignatures()
        self.behavioral = BehavioralAnalyzer(window_seconds=60)
        self.crypto = CryptoSuite()
        self.validator = ZeroTrustValidator()
        self.siem = SimpleSIEM()
        self.blocked_ips = set()
    
    def analyze_request(
        self,
        source_ip: str,
        target: str,
        payload: str
    ) -> Dict:
        '''
        Analyse complète d'une requête
        
        Pipeline:
        1. Validation Zero-Trust
        2. Signature Detection
        3. Behavioral Analysis
        4. Composite Threat Scoring
        5. Automated Response
        6. SIEM Logging
        '''
        
        timestamp = time.time()
        
        # Step 1: Check if IP already blocked
        if source_ip in self.blocked_ips:
            return {{
                \"status\": \"BLOCKED\",
                \"reason\": \"IP in blocklist\",
                \"threat_score\": 100.0
            }}
        
        # Step 2: Input validation
        sanitized = self.validator.sanitize_input(payload)
        
        # Step 3: Signature-based detection
        attack_type, signature_score = self.signatures.scan_payload(sanitized)
        
        # Step 4: Behavioral analysis
        behavioral_score = self.behavioral.record_request(source_ip, timestamp)
        
        # Step 5: Composite threat scoring
        composite_score = max(
            signature_score * 0.7 +      # Signature weight: 70%
            behavioral_score * 0.3,       # Behavioral weight: 30%
            signature_score,              # But never lower than signature
            behavioral_score
        )
        
        # Step 6: Automated response
        if composite_score >= 90:
            self.blocked_ips.add(source_ip)
            response_action = \"IP_BLOCKED\"
        elif composite_score >= 70:
            response_action = \"ALERT_TRIGGERED\"
        else:
            response_action = \"LOGGED\"
        
        # Step 7: SIEM logging
        self.siem.log_event(
            event_type=attack_type,
            source_ip=source_ip,
            target=target,
            payload=sanitized,
            threat_score=composite_score
        )
        
        return {{
            \"status\": \"ANALYZED\",
            \"attack_type\": attack_type,
            \"threat_score\": composite_score,
            \"signature_score\": signature_score,
            \"behavioral_score\": behavioral_score,
            \"response_action\": response_action,
            \"ip_reputation\": self.behavioral.get_reputation(source_ip)
        }}

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7: MAIN EXECUTION
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print(\"[SEC] Divine Security V2.0 - Starting cyber defense...\")
    
    pipeline = SecurityPipeline()
    
    # Demo: Analyze sample requests
    test_requests = [
        {{
            \"ip\": \"192.168.1.100\",
            \"target\": \"/api/users\",
            \"payload\": \"username=admin&password=test123\"
        }},
        {{
            \"ip\": \"10.0.0.50\",
            \"target\": \"/api/search\",
            \"payload\": \"q=' OR '1'='1\"  # SQL Injection
        }},
        {{
            \"ip\": \"172.16.0.10\",
            \"target\": \"/uploads\",
            \"payload\": \"<script>alert('xss')</script>\"  # XSS
        }}
    ]
    
    print(\"\\n📊 Analyzing Security Requests:\\n\")
    
    for req in test_requests:
        result = pipeline.analyze_request(
            source_ip=req['ip'],
            target=req['target'],
            payload=req['payload']
        )
        
        print(f\"Request: {{req['target']}} from {{req['ip']}}\")
        print(f\"  • Attack Type: {{result['attack_type']}}\")
        print(f\"  • Threat Score: {{result['threat_score']:.1f}}/100\")
        print(f\"  • Response: {{result['response_action']}}\")
        print()
    
    # Crypto demo
    print(\"\\n🔐 Cryptography Demo:\\n\")
    
    # Password hashing
    password = \"SecurePassword123!\"
    hashed, salt = pipeline.crypto.hash_password(password)
    print(f\"Password Hash: {{hashed[:32]}}...\")
    print(f\"Salt: {{salt[:32]}}...\")
    
    # Encryption
    if CRYPTO_AVAILABLE:
        key = pipeline.crypto.generate_key()
        secret_data = \"Sensitive information\"
        encrypted = pipeline.crypto.encrypt_data(secret_data, key)
        print(f\"Encrypted: {{encrypted[:50]}}...\")
    
    # SIEM report
    print(\"\\n📋 SIEM Top Threats:\\n\")
    top_threats = pipeline.siem.get_top_threats(limit=3)
    
    for i, event in enumerate(top_threats, 1):
        print(f\"{{i}}. {{event['event_type']}} from {{event['source_ip']}} ({{event['threat_score']:.0f}})\")
    
    print(\"\\n[OK] SUCCESS: Security analysis complete\")
    print(f\"📊 Events logged: {{len(pipeline.siem.events)}}\")
    print(f\"🚫 IPs blocked: {{len(pipeline.blocked_ips)}}\")

if __name__ == \"__main__\":
    main()
```

[[OK] INNOVATIONS V2.0 - ENTERPRISE SECURITY]

1. **Signature-Based Detection**
   - SQL Injection patterns (15+ signatures)
   - XSS detection (10+ patterns)
   - Path Traversal & Command Injection
   - YARA-like rule engine

2. **Behavioral Analysis**
   - Rate limiting (requests/minute)
   - IP reputation scoring (0-100)
   - Anomaly detection via request patterns
   - Sliding window (60s)

3. **Cryptographic Suite**
   - AES-256 encryption (Fernet)
   - PBKDF2-HMAC-SHA256 (100K iterations)
   - HMAC integrity verification
   - Secure random generation (secrets module)

4. **Zero-Trust Architecture**
   - Whitelist-based validation
   - Sanitization de tous inputs
   - Path traversal prevention
   - Filename validation stricte

5. **SIEM Integration**
   - Event logging structuré
   - Real-time alerting (threshold-based)
   - Top threats ranking
   - Correlation temporelle

6. **Automated Response**
   - Auto-blocking IPs malveillants (score >90)
   - Alert generation (score >70)
   - Progressive IP reputation degradation

7. **Composite Threat Scoring**
   ```
   Composite = max(
       Signature × 0.7 + Behavioral × 0.3,
       Signature,
       Behavioral
   )
   ```

[🎯 COMPLEXITY ANALYSIS]
- Signature Detection: O(n × m) où n=payload size, m=patterns
- Behavioral Analysis: O(1) amortized (deque)
- Crypto Operations: O(n) pour hash/encrypt
- SIEM Storage: O(1) append, O(n log n) pour top-k

Generate MILITARY-GRADE security code NOW.
"""

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Exécution asynchrone de la tâche (Architecture V10).
        """
        self.log_thought(f"{self.name} analyse la tâche...", type="thought")
        
        # Construction du contexte pour le LLM
        user_mission = task_payload.get("mission", "Aucune mission définie")
        context = task_payload.get("context", "")
        
        full_prompt = f"""
        {self.system_instructions}
        
        CONTEXTE ACTUEL:
        {context}
        
        MISSION PRIORITAIRE:
        {user_mission}
        """
        
        # Appel au Cerveau (via le noyau V10 qui gère le RateLimit)
        # Note: self.llm_client est fourni par BaseAgent dans la V10
        response = await self.generate_content(full_prompt)
        
        return {
            "status": "success",
            "result": response,
            "agent": self.name
        }
