import logging
from typing import Dict, Any
from core.base_agent import BaseAgent # On suppose une classe de base V10

logger = logging.getLogger("writer")

class DivineWriter(BaseAgent):
    """
    DivineWriter - Migré depuis Prométhée V9 (Olympe).
    Rôle: AI Content Strategist
    """
    
    def __init__(self):
        super().__init__(
            name="writer",
            role="AI Content Strategist",
            description="Agent migré depuis la V9"
        )
        # L'ADN de l'agent (Prompt Système transféré)
        self.system_instructions = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                 🎯 DIVINE WRITER V2.0 - CONTENT MISSION                      ║
╚══════════════════════════════════════════════════════════════════════════════╝

{error_context}

[📋 CONTENT BRIEF]
Task: \"{mission}\"
Format: {format_type.value.upper()}
Target Quality: SEO 80+, Readability Grade A

{format_specs[format_type]}

[[INFRA] CONTENT GENERATION V2.0]

```python
from typing import Dict, List
import re
from datetime import datetime
from l1_file_writer import write_to_disk

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: CONTENT QUALITY ANALYZER
# ═══════════════════════════════════════════════════════════════════════════

def analyze_readability(text: str) -> Dict:
    '''
    Flesch Reading Ease simplifiée
    '''
    words = len(text.split())
    sentences = text.count('.') + text.count('!') + text.count('?')
    if sentences == 0:
        sentences = 1
    
    avg_words_per_sentence = words / sentences
    
    if avg_words_per_sentence < 15:
        grade = \"Easy\"
    elif avg_words_per_sentence < 25:
        grade = \"Medium\"
    else:
        grade = \"Complex\"
    
    return {{
        \"word_count\": words,
        \"reading_time_min\": max(1, words // 200),
        \"readability\": grade,
        \"avg_words_per_sentence\": round(avg_words_per_sentence, 1)
    }}

def analyze_seo(text: str, keywords: List[str]) -> float:
    '''
    SEO Score basé sur keyword density
    '''
    text_lower = text.lower()
    total_words = len(text_lower.split())
    
    keyword_count = sum(text_lower.count(kw.lower()) for kw in keywords)
    density = (keyword_count / total_words * 100) if total_words > 0 else 0
    
    # Optimal density: 1-3%
    if 1 <= density <= 3:
        score = 100
    elif 0.5 <= density < 1 or 3 < density <= 4:
        score = 80
    elif density < 0.5:
        score = 50
    else:
        score = 30  # Keyword stuffing
    
    return score

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2: CONTENT GENERATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def generate_content() -> str:
    '''
    Génère contenu selon format spécifié
    '''
    
    # CONTENU ICI (généré par LLM selon mission)
    content = '''
[VOTRE CONTENU GÉNIAL ICI]

Pour format TWEET: 280 chars max
Pour BLOG: Structure Intro → Body → Conclusion
Pour TECHNICAL: Documentation complète
Pour MARKETING: AIDA framework
    '''
    
    return content.strip()

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3: QUALITY VALIDATION & EXPORT
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print(\"[WRITE] Divine Writer V2.0 - Starting content generation...\")
    
    # Generate
    content = generate_content()
    
    # Analyze quality
    metrics = analyze_readability(content)
    
    # Extract keywords for SEO
    keywords = [\"{mission.split()[0]}\", \"{mission.split()[1] if len(mission.split()) > 1 else ''}\"]
    seo_score = analyze_seo(content, keywords)
    
    print(f\"\\n📊 Content Metrics:\")
    print(f\"  • Words: {{metrics['word_count']}}\")
    print(f\"  • Reading Time: {{metrics['reading_time_min']}} min\")
    print(f\"  • Readability: {{metrics['readability']}}\")
    print(f\"  • SEO Score: {{seo_score:.0f}}/100\")
    
    # Save
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    format_type = \"{format_type.value}\"
    if format_type == \"tweet\":
        folder = \"MARKETING_ASSETS\"
    elif format_type == \"technical\":
        folder = \"DOCS\"
    else:
        folder = \"MARKETING_ASSETS\"
    
    filename = f\"{{folder}}/content_{{timestamp}}.md\"
    write_to_disk(filename, content)
    
    print(f\"\\n[OK] SUCCESS: Content generated → {{filename}}\")

if __name__ == \"__main__\":
    main()
```

[[OK] QUALITY GATES]
- [ ] Format respecté (TWEET: 280 chars, BLOG: structure)
- [ ] SEO optimisé (keywords dans titre + intro)
- [ ] Readability acceptable (Grade A/B)
- [ ] CTA présent
- [ ] Saved via l1_file_writer

[[GO] INNOVATIONS V2.0]
1. **Readability Analysis**: Flesch-Kincaid scoring
2. **SEO Optimization**: Keyword density analysis
3. **Multi-Format**: Adaptation automatique
4. **Quality Metrics**: Word count, reading time
5. **A/B Testing Ready**: Structure pour variants

Generate professional content NOW.
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
