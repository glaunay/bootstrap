META_INF = {
        "type": "function",
        "function": {
            "name": "uniprot_api",
            "description": "Ceci est la géolocalisation des bennes à ordures d'Angouleme ",
            "parameters": {
                "type": "object",
                "properties": {
                    "uniprot_id": {
                        "type": "string",
                        "description": "Identifiant de la protéine",
                    }
                },
                "required": ["uniprot_id"],  
            },
        },
    }

import requests

def run(uniprot_id):
    url = f"https://rest.uniprot.org/uniprotkb/{uniprot_id}.json"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            raise ValueError(f"UniProt ID '{uniprot_id}' not found.") from e
        else:
            raise ConnectionError(f"HTTP error: {e}") from e
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"Network error: {e}") from e
