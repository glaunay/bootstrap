import json
import os
import sys
import rich_click as click
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel

from .tool_loader import ToolLoader


COMMANDS = ["/quit", "/exit", "/reset", "/help", "/context", "/context-add", "/context-clear"]
console = Console()


def conversation(client, model, t, tool_loader, messages):
    trace = []   # outils appelés pour cette question
    # On limite le nombre de tours, au cas où le modèle tournerait en rond
    for tour in range(5):
        # 1. On interroge le LLM, en lui donnant la liste des outils
        with console.status("Réflexion…"):   # spinner pendant l'appel à l'API
            reponse = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tool_loader.get_info(),
                tool_choice="auto",   # le modèle décide lui-même s'il a besoin d'un outil
                temperature=t,
            )
        message = reponse.choices[0].message
        # 2. Est-ce que le modèle demande un outil ?
        # Non : il a sa réponse finale, on l'affiche, on la garde dans l'historique et on sort.
        if not message.tool_calls:
            outils_utilises = ", ".join(sorted({c["outil"] for c in trace})) or "aucun outil"
            console.print(Panel(Markdown(message.content or ""), title="Albert",
                                subtitle=f"[dim]via : {escape(outils_utilises)}[/dim]", border_style="cyan"))
            messages.append({"role": "assistant", "content": message.content or ""})
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
                console.print(f"[dim]⚙ {escape(nom)}({escape(str(arguments))})[/dim]", highlight=False)
                resultat = fonction(**arguments)

            trace.append({"tour": tour, "outil": nom, "arguments": appel.function.arguments,
                          "taille_resultat": len(str(resultat))})
            messages.append({"role": "tool", "tool_call_id": appel.id, "content": resultat})
        # 5. On repart au début de la boucle : le modèle voit maintenant le résultat de l'outil
        # et peut répondre, ou demander un autre outil.
    else:
        console.print("[yellow]Trop de tours sans réponse finale, on s'arrête.[/yellow]")

    return messages


@click.command()
@click.option("--model", default="gpt-oss-120b", help="LLM model")
@click.option("--t", help="temperature ", default=0.2)
def main(model, t) -> None:

    # --- Initialisation du client Albert ---
    load_dotenv()                        # lit le fichier .env (là où se trouve la clé)
    API_KEY = os.getenv("ALBERT_API_KEY")
    if not API_KEY:
        sys.exit("Il manque la clé API")

    client = OpenAI(base_url="https://albert.api.etalab.gouv.fr/v1", api_key=API_KEY)
    tool_loader = ToolLoader(Path(__file__).parent / "tools")

    # --- Prompt système = base fixe + ajouts de l'utilisateur (/context-add) ---
    base_system = ("Tu es un assistant scientifique. Utilise tes outils quand tu as besoin d'informations vérifiées, "
                    "et cite les articles (titre, année, PMID) sur lesquels tu t'appuies. Réponds en français. "
                    "Source all ur reponses inlcuding url to actual reference")
    extra_context: list[str] = []

    def build_system() -> str:
        if not extra_context:
            return base_system
        ajouts = "\n".join(f"- {c}" for c in extra_context)
        return f"{base_system}\n\nContexte supplémentaire fourni par l'utilisateur :\n{ajouts}"

    messages = [{"role": "system", "content": build_system()}]

    # --- REPL : l'historique vit dans `messages` tant que le programme tourne ---
    session = PromptSession(
        history=InMemoryHistory(),                         # flèches haut/bas, pour cette session seulement
        completer=WordCompleter(COMMANDS, sentence=True),  # Tab complète les /commandes
        complete_while_typing=True,
    )

    print("Tapez /help pour l'aide, /quit (ou Ctrl-D) pour sortir.")
    while True:
        try:
            q = session.prompt(HTML("\n<ansigreen><b>&gt; </b></ansigreen>")).strip()
        except KeyboardInterrupt:      # Ctrl-C : efface la ligne en cours
            continue
        except EOFError:               # Ctrl-D : quitte
            print("Au revoir.")
            break

        if not q:
            continue
        if q in ("/quit", "/exit"):
            print("Au revoir.")
            break
        cmd, _, arg = q.partition(" ")
        arg = arg.strip()

        if cmd == "/reset":            # efface l'historique, garde le contexte ajouté
            messages = [{"role": "system", "content": build_system()}]
            console.print("[dim]Historique effacé.[/dim]")
            continue
        if cmd == "/help":
            print("/context            affiche le prompt système\n"
                  "/context-add <txt>  ajoute du contexte au prompt système\n"
                  "/context-clear      revient au prompt système par défaut\n"
                  "/reset              efface l'historique\n"
                  "/quit               quitte")
            continue
        if cmd == "/context":
            console.print(Panel(escape(messages[0]["content"]), title="system", border_style="magenta"))
            continue
        if cmd == "/context-add":
            if not arg:
                console.print("[yellow]Usage : /context-add <texte>[/yellow]")
                continue
            extra_context.append(arg)
            messages[0]["content"] = build_system()
            console.print(f"[dim]Contexte ajouté ({len(extra_context)} élément(s)).[/dim]")
            continue
        if cmd == "/context-clear":
            extra_context.clear()
            messages[0]["content"] = build_system()
            console.print("[dim]Contexte réinitialisé.[/dim]")
            continue
        if cmd.startswith("/"):
            console.print(f"[yellow]Commande inconnue : {escape(cmd)} (voir /help)[/yellow]")
            continue



        messages.append({"role": "user", "content": q})
        try:
            messages = conversation(client, model, t, tool_loader, messages)
        except KeyboardInterrupt:      # Ctrl-C pendant la réponse : on abandonne cette question
            console.print("\n[yellow]Interrompu.[/yellow]")
            messages = trim_unanswered(messages)
        except Exception as e:         # une erreur d'API ne doit pas tuer la session
            console.print(f"[red]Erreur : {escape(str(e))}[/red]")
            messages = trim_unanswered(messages)


def trim_unanswered(messages):
    """Retire la dernière question et les échanges d'outils qui n'ont pas abouti à une réponse."""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i]["role"] == "user":
            return messages[:i]
    return messages
