META_INF = {
        "type": "function",
        "function": {
            "name": "dummy",
            "description": "Outil de test qui renvoie simplement 'hello world'. "
                           "Ne l'utilise QUE si l'utilisateur demande explicitement de tester les outils ; "
                           "il ne fournit aucune information scientifique.",
            "parameters": {
                "type": "object",
                "properties": {},     # aucun argument
                "required": [],
            },
        },
    }

def run():
    return "hello world"
