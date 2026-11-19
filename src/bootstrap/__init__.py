import json
import os
import sys
import rich_click as click
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from .tool_loader import ToolLoader

@click.command()
@click.option("--model", default="gpt-oss-120b", help="LLM model")
@click.option("--question", prompt="question", help="The question to send to Albert ", default="Explique en deux phrases ce qu'est un trou noir.")
@click.option("--t", help="temperature ", default=0.2)
def main(model, question, t) -> None:

    # --- Initialisation du client Albert ---
    load_dotenv()                        # lit le fichier .env (là où se trouve la clé)
    API_KEY = os.getenv("ALBERT_API_KEY")
    if not API_KEY:
        sys.exit("Il manque la clé API")

    client = OpenAI(base_url="https://albert.api.etalab.gouv.fr/v1", api_key=API_KEY)



    # Un petit dictionnaire pour retrouver la fonction Python à partir du nom donné par le modèle.
    # Si vous ajoutez un outil, ajoutez-le ici aussi, et dans OUTILS juste au-dessus.
    #OUTILS, FONCTIONS = tool_loader("./tools")

    tool_loader = ToolLoader(Path(__file__).parent / "tools")

    # ----------------------------------------------------------------------
    # PARTIE 2 : la boucle
    # ----------------------------------------------------------------------

    messages = [
        {"role": "system", "content": "Tu es un assistant scientifique. Utilise tes outils quand tu as besoin d'informations vérifiées, "
                                      "et cite les articles (titre, année, PMID) sur lesquels tu t'appuies. Réponds en français."},
        {"role": "user", "content": question},
    ]

    # On limite le nombre de tours, au cas où le modèle tournerait en rond
    for tour in range(5):
        # 1. On interroge le LLM, en lui donnant la liste des outils
        reponse = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tool_loader.get_info(),
            tool_choice="auto",   # le modèle décide lui-même s'il a besoin d'un outil
            temperature=t,
        )
        message = reponse.choices[0].message
        # 2. Est-ce que le modèle demande un outil ?
        # Non : il a sa réponse finale, on l'affiche et on sort.
        if not message.tool_calls:
            print(message.content)
            break

        # Oui : il veut utiliser un ou plusieurs outils.
        # 3. On garde sa demande dans l'historique de la conversation...
        messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": appel.id,
                    "type": "function",
                    "function": {"name": appel.function.name, "arguments": appel.function.arguments},
                }
                for appel in message.tool_calls
            ],
        })

        # 4. ... puis on exécute chaque outil demandé, et on ajoute le résultat à l'historique avec le rôle "tool".
        # (Le résultat ne va jamais directement à l'utilisateur : c'est le modèle qui le lit et le met en forme.)
        for appel in message.tool_calls:
            nom = appel.function.name
            fonction = tool_loader.get_exec(nom)
            if fonction is None:
                # Outil inconnu : on répond quand même à cet appel (l'API exige une réponse "tool"
                # pour chaque tool_call_id), avec un message d'erreur que le modèle pourra lire.
                outils = ", ".join(t["function"]["name"] for t in tool_loader.get_info())
                resultat = f"Erreur : l'outil '{nom}' n'existe pas. Outils disponibles : {outils}"
            else:
                arguments = json.loads(appel.function.arguments)   # les arguments arrivent sous forme de texte JSON
                #print(f"-> le modèle appelle {nom}({arguments})")
                resultat = fonction(**arguments)

            messages.append({"role": "tool", "tool_call_id": appel.id, "content": resultat})
        # 5. On repart au début de la boucle : le modèle voit maintenant le résultat de l'outil
        # et peut répondre, ou demander un autre outil.
    else:
        print("Trop de tours sans réponse finale, on s'arrête.")
