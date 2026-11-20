META_INF = {
        "type": "function",
        "function": {
            "name": "find_file",
            "description": "Cherche des fichiers par motif glob (ex: '*.csv', '*P12345*') dans un dossier "
                           "et ses sous-dossiers (par défaut : le working directory). "
                           "Renvoie les chemins relatifs, un par ligne. "
                           "Utile pour trouver le chemin exact d'un fichier avant de le lire avec un autre outil.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Motif glob du nom de fichier.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Dossier racine de la recherche. Défaut : working directory.",
                    },
                },
                "required": ["pattern"],
            },
        },
    }

SKIP = {".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache"}
MAX = 50

import os
from fnmatch import fnmatch
from pathlib import Path

def run(pattern, path="."):
    root = Path(path).expanduser()
    if not root.is_dir():
        return f"Erreur : '{path}' n'est pas un dossier."

    hits = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP)   # on n'entre pas dans .venv, .git…
        hits += [str(Path(d, f).relative_to(root)) for f in sorted(files) if fnmatch(f, pattern)]
        if len(hits) > MAX:
            break

    if not hits:
        return f"Aucun fichier '{pattern}' dans {root.resolve()}"
    more = f"\n… (>{MAX}, affine le motif)" if len(hits) > MAX else ""
    return f"{root.resolve()}:\n" + "\n".join(sorted(hits[:MAX])) + more
