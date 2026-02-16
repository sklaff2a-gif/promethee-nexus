import re
import json
import logging
from core.base_agent import BaseAgent

logger = logging.getLogger("data_analyst")


def _detect_csv(text: str) -> dict:
    """Détecte et analyse un contenu CSV dans le texte."""
    lines = text.strip().splitlines()
    if len(lines) < 2:
        return {"detected": False}

    # Heuristique : la première ligne non-vide avec des séparateurs est un header CSV
    for sep in [",", ";", "\t", "|"]:
        # Chercher le début du CSV (ligne avec >= 2 séparateurs)
        for i, line in enumerate(lines):
            parts = line.split(sep)
            if len(parts) >= 2 and all(p.strip() for p in parts):
                # Vérifier que les lignes suivantes ont le même nombre de colonnes
                headers = [p.strip().strip('"').strip("'") for p in parts]
                data_lines = 0
                for j in range(i + 1, min(i + 50, len(lines))):
                    if lines[j].strip() and len(lines[j].split(sep)) == len(parts):
                        data_lines += 1
                if data_lines >= 1:
                    return {
                        "detected": True,
                        "format": "CSV",
                        "separator": repr(sep),
                        "headers": headers,
                        "num_columns": len(headers),
                        "num_data_lines": len(lines) - i - 1,
                    }

    return {"detected": False}


def _detect_json(text: str) -> dict:
    """Détecte et analyse un contenu JSON dans le texte."""
    # Chercher un bloc JSON (commence par { ou [)
    json_match = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', text)
    if not json_match:
        return {"detected": False}

    try:
        data = json.loads(json_match.group(1))
    except (json.JSONDecodeError, RecursionError):
        return {"detected": False}

    if isinstance(data, dict):
        return {
            "detected": True,
            "format": "JSON",
            "type": "object",
            "top_level_keys": list(data.keys())[:20],
            "num_keys": len(data),
        }
    elif isinstance(data, list):
        element_type = type(data[0]).__name__ if data else "empty"
        sample_keys = list(data[0].keys())[:10] if data and isinstance(data[0], dict) else []
        return {
            "detected": True,
            "format": "JSON",
            "type": "array",
            "num_elements": len(data),
            "element_type": element_type,
            "sample_keys": sample_keys,
        }

    return {"detected": False}


def _detect_data_format(text: str) -> dict:
    """Détecte le format de données (CSV ou JSON) dans le texte."""
    json_info = _detect_json(text)
    if json_info["detected"]:
        return json_info

    csv_info = _detect_csv(text)
    if csv_info["detected"]:
        return csv_info

    return {"detected": False}


class DataAnalyst(BaseAgent):
    def __init__(self):
        super().__init__(
            name="data_analyst",
            role="Analyste de données",
            description="Interprète CSV/JSON, statistiques, tendances, visualisations pandas/matplotlib"
        )

    async def process_task(self, task_payload):
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")
        full_text = f"{mission}\n{context}"

        # Pré-traitement : détection du format de données
        data_info = _detect_data_format(full_text)

        enrichment = ""
        if data_info["detected"]:
            enrichment = f"\n\n--- ANALYSE AUTOMATIQUE DES DONNÉES ---\nFormat détecté : {data_info['format']}\n"
            if data_info["format"] == "CSV":
                enrichment += (
                    f"Séparateur : {data_info['separator']}\n"
                    f"Colonnes ({data_info['num_columns']}) : {', '.join(data_info['headers'])}\n"
                    f"Lignes de données : {data_info['num_data_lines']}\n"
                )
            elif data_info["format"] == "JSON":
                enrichment += f"Type : {data_info['type']}\n"
                if data_info["type"] == "object":
                    enrichment += f"Clés ({data_info['num_keys']}) : {', '.join(data_info['top_level_keys'])}\n"
                elif data_info["type"] == "array":
                    enrichment += (
                        f"Éléments : {data_info['num_elements']}\n"
                        f"Type d'élément : {data_info['element_type']}\n"
                    )
                    if data_info.get("sample_keys"):
                        enrichment += f"Clés d'un élément : {', '.join(data_info['sample_keys'])}\n"
            enrichment += "--- FIN ANALYSE ---\n"

        prompt = (
            "Tu es un analyste de données expert. "
            "Analyse des données, identifie des patterns et tendances, "
            "propose des visualisations, et génère du code pandas/matplotlib.\n\n"
            f"{mission}"
            f"{enrichment}"
        )
        response = await self.generate_content(prompt)
        return {"status": "success", "result": response, "data_info": data_info}
