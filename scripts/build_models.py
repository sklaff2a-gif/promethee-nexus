#!/usr/bin/env python3
"""
Construit les modèles Ollama personnalisés pour Prométhée.
Parcourt config/modelfiles/*.modelfile et exécute `ollama create` pour chacun.

Usage : python scripts/build_models.py
"""
import subprocess
import sys
from pathlib import Path

MODELFILES_DIR = Path(__file__).resolve().parent.parent / "config" / "modelfiles"


def build_models():
    modelfiles = sorted(MODELFILES_DIR.glob("*.modelfile"))
    if not modelfiles:
        print(f"Aucun .modelfile trouvé dans {MODELFILES_DIR}")
        sys.exit(1)

    print(f"Trouvé {len(modelfiles)} modelfile(s) dans {MODELFILES_DIR}\n")
    success = 0
    errors = []

    for mf in modelfiles:
        # Nom du modèle = nom du fichier sans extension
        model_name = mf.stem  # ex: promethee-coder
        print(f"--- Construction de '{model_name}' depuis {mf.name} ---")

        try:
            result = subprocess.run(
                ["ollama", "create", model_name, "-f", str(mf)],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                print(f"  OK : {model_name} créé avec succès")
                success += 1
            else:
                err_msg = result.stderr.strip() or result.stdout.strip()
                print(f"  ERREUR : {err_msg}")
                errors.append((model_name, err_msg))
        except FileNotFoundError:
            print("  ERREUR : 'ollama' non trouvé dans le PATH. Installez Ollama.")
            sys.exit(1)
        except subprocess.TimeoutExpired:
            print(f"  ERREUR : timeout pour {model_name}")
            errors.append((model_name, "timeout"))

    # Résumé
    print(f"\n{'='*50}")
    print(f"Résultat : {success}/{len(modelfiles)} modèles construits")
    if errors:
        print("Erreurs :")
        for name, err in errors:
            print(f"  - {name} : {err}")
        sys.exit(1)
    else:
        print("Tous les modèles sont prêts.")

    # Vérification
    print(f"\n--- Vérification (ollama list | grep promethee) ---")
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=30
        )
        for line in result.stdout.splitlines():
            if "promethee" in line.lower():
                print(f"  {line}")
    except Exception as e:
        print(f"  Impossible de vérifier : {e}")


if __name__ == "__main__":
    build_models()
