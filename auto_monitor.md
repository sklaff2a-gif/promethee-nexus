# Cycle de monitoring autonome PROMETHEE

Tu es invoqué automatiquement toutes les 4 heures pour surveiller et maintenir le projet.

## Protocole strict

### Phase 1 : Analyse des logs en cours
```bash
cd "C:\MesProjets\PROMETHEE_V11_restructuration2026"
PYTHONIOENCODING=utf-8 python analyze_run.py "logs/" --date today
```
Si le dossier `logs/` est vide ou n'existe pas, cherche dans `log run copie/`.
Lis le rapport produit par le script.

### Phase 2 : Diagnostic
Identifie les patterns problématiques :
- Routines avec qualité 0.0 systématique → bug de scoring ou d'agent
- Erreurs 429 répétées → ajuster la stratégie Cloud
- Hallucinations (code hors-sujet, bibliothèques interdites) → renforcer les guardrails
- Monotonie (même routine en boucle) → vérifier le scoring
- Councils sans action → vérifier l'extraction transcript

Si AUCUN problème critique n'est détecté, arrête-toi ici et écris un résumé court dans `logs/autonomous_reports/YYYY-MM-DD_HHhMM.md`.

### Phase 3 : Correction (seulement si problème critique)
1. Lis les fichiers source concernés
2. Applique la correction minimale
3. Lance les tests :
```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/ -x --tb=short -q
```
4. Si les tests passent → commit avec le message :
```
fix(auto): [description courte du problème corrigé]
```
5. Push vers origin

### Phase 4 : Rapport
Écris un rapport concis dans `logs/autonomous_reports/YYYY-MM-DD_HHhMM.md` :
- Durée du run analysé
- Nombre de routines / qualité moyenne
- Problèmes détectés (ou "RAS")
- Corrections appliquées (ou "Aucune")
- Tests : passés/échoués

### Règles de sécurité
- NE MODIFIE PAS les fichiers dans `_PROTECTED_FILES` (main.py, config.py, guardian.py, etc.)
- NE MODIFIE PAS plus de 3 fichiers par cycle
- NE COMMIT PAS si les tests échouent
- NE PUSH PAS avec --force
- Si tu n'es pas sûr d'une correction, écris-la dans le rapport sans l'appliquer
