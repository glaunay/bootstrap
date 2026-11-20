import json
import re
import os
import sys
from datetime import datetime
import rich_click as click
from pathlib import Path

from dotenv import load_dotenv
import openai
from openai import OpenAI
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .tool_loader import ToolLoader
from .tokens import calibrate, count_tokens, raw_count


COMMANDS = ["/quit", "/exit", "/reset", "/help", "/context", "/context-add", "/context-clear", "/errors", "/tokens", "/reasoning", "/reasoning-last"]
console = Console()

# Comptage systématique des tokens à chaque appel au LLM (activé avec /tokens on).
token_tracking = False

# Affichage du raisonnement du modèle (activé avec /reasoning on).
reasoning_display = False
REASONING_MAX_CHARS = 1500
# Raisonnement complet de la dernière question (un élément par tour), pour /reasoning-last.
last_reasoning: list[str] = []


def get_reasoning(message):
    """Texte du raisonnement renvoyé par le serveur, ou None.

    Albert (vLLM) le place dans `reasoning` ; d'anciennes versions utilisent `reasoning_content`.
    Le SDK openai garde ces champs non standard dans `model_extra`.
    """
    extra = getattr(message, "model_extra", None) or {}
    for champ in ("reasoning", "reasoning_content"):
        texte = extra.get(champ) or getattr(message, champ, None)
        if texte:
            return texte
    return None


def show_reasoning(texte, tour):
    if len(texte) > REASONING_MAX_CHARS:
        texte = texte[:REASONING_MAX_CHARS] + " […] (texte complet : /reasoning-last)"
    console.print(Panel(escape(texte), title=f"[dim]Raisonnement (tour {tour + 1})[/dim]",
                        border_style="dim", style="dim"))

# Journal des erreurs : quel service (API Albert ou outil) a échoué, quand, et pourquoi.
error_log: list[dict] = []


class ServiceError(Exception):
    """Erreur levée par un service externe, étiquetée avec le nom de ce service."""
    def __init__(self, service, cause, tokens="", hint=""):
        super().__init__(f"{service} : {type(cause).__name__} : {cause}")
        self.service = service
        self.cause = cause
        self.tokens = tokens
        self.hint = hint   # conseil affiché à l'utilisateur (quoi faire ensuite)


def log_error(service, err, tokens="", kind=None):
    """`kind` précise la nature de l'erreur quand le type d'exception ne suffit pas."""
    nom = type(err).__name__
    error_log.append({
        "heure": datetime.now().strftime("%H:%M:%S"),
        "service": service,
        "type": f"{kind} ({nom})" if kind else nom,
        "message": str(err),
        "tokens": tokens,
    })


# Messages typiques renvoyés par vLLM / Albert quand le contexte dépasse la limite du modèle.
_CONTEXT_TOO_LARGE = re.compile(
    r"maximum context length|context length|context window|too many tokens|prompt is too long"
    r"|input .*too long|reduce the length", re.IGNORECASE)


def is_context_too_large(err):
    """413, ou 400 dont le message parle de longueur de contexte."""
    if not isinstance(err, openai.APIStatusError):
        return False
    if err.status_code == 413:
        return True
    return err.status_code == 400 and bool(_CONTEXT_TOO_LARGE.search(str(err)))


def context_size(messages, tools, model):
    n, methode = count_tokens(messages, tools, model)
    return f"~{n} tokens ({methode}), {len(messages)} messages"


def conversation(client, model, t, tool_loader, messages):
    trace = []   # outils appelés pour cette question
    last_reasoning.clear()
    # On limite le nombre de tours, au cas où le modèle tournerait en rond
    for tour in range(5):
        # 1. On interroge le LLM, en lui donnant la liste des outils
        tools = tool_loader.get_info()
        if token_tracking:
            estimation_brute, _ = raw_count(messages, tools, model)   # pour la calibration
            n, methode = count_tokens(messages, tools, model)
        try:
            with console.status("Réflexion…"):   # spinner pendant l'appel à l'API
                reponse = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",   # le modèle décide lui-même s'il a besoin d'un outil
                    temperature=t,
                )
        except openai.RateLimitError as e:   # 429 : trop de requêtes / quota Albert atteint
            # Le client openai a déjà réessayé (max_retries) avant d'abandonner.
            service = f"Albert API ({client.base_url})"
            tokens = context_size(messages, tools, model)
            retry_after = e.response.headers.get("retry-after")
            attente = f"réessayez dans {retry_after} s" if retry_after else "réessayez dans un moment"
            log_error(service, e, tokens, kind="TropDeRequêtes")
            raise ServiceError(service, e, tokens,
                               hint=f"Limite de requêtes Albert atteinte : {attente}.") from e
        except openai.APIError as e:   # connexion, timeout, autre statut HTTP… côté API Albert
            service = f"Albert API ({client.base_url})"
            tokens = context_size(messages, tools, model)
            if is_context_too_large(e):
                log_error(service, e, tokens, kind="ContexteTropLong")
                raise ServiceError(service, e, tokens,
                                   hint="Contexte trop long pour le modèle : /reset pour vider "
                                        "l'historique, ou posez une question plus ciblée.") from e
            log_error(service, e, tokens)
            raise ServiceError(service, e, tokens) from e
        if token_tracking:
            exact = reponse.usage.prompt_tokens if reponse.usage else None
            console.print(f"[dim]◷ tokens : ~{n} estimés ({escape(methode)}), "
                          f"{exact if exact is not None else '?'} exacts, {len(messages)} messages[/dim]")
            if exact:   # nombre exact côté serveur : on corrige nos estimations suivantes
                calibrate(model, estimation_brute, exact)
        message = reponse.choices[0].message
        # Le raisonnement est seulement affiché : il n'est jamais remis dans `messages`
        # (ça gonflerait le contexte, et gpt-oss n'attend pas son ancien raisonnement en entrée).
        raisonnement = get_reasoning(message)
        if raisonnement:
            last_reasoning.append(raisonnement)
            if reasoning_display:
                show_reasoning(raisonnement, tour)
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
                try:
                    resultat = fonction(**arguments)
                except Exception as e:
                    # L'outil a planté (souvent une erreur réseau) : on le journalise et on
                    # renvoie l'erreur au modèle au lieu de faire tomber toute la question.
                    log_error(f"outil {nom}", e)
                    console.print(f"[red]✗ {escape(nom)} : {escape(type(e).__name__)}[/red]")
                    resultat = f"Erreur : l'outil '{nom}' a échoué ({type(e).__name__} : {e})"

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
@click.option("--t", help="temperature ", default=0.0)
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
                  "/errors             affiche les services ayant provoqué une erreur\n"
                  "/tokens [on|off]    compte les tokens à chaque appel (sans argument : bascule)\n"
                  "/reasoning [on|off] affiche le raisonnement du modèle (sans argument : bascule)\n"
                  "/reasoning-last     raisonnement complet de la dernière question\n"
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
        if cmd == "/tokens":
            global token_tracking
            if arg not in ("", "on", "off"):
                console.print("[yellow]Usage : /tokens [on|off][/yellow]")
                continue
            token_tracking = (not token_tracking) if not arg else (arg == "on")
            console.print(f"[dim]Comptage des tokens : {'activé' if token_tracking else 'désactivé'}.[/dim]")
            continue
        if cmd == "/reasoning":
            global reasoning_display
            if arg not in ("", "on", "off"):
                console.print("[yellow]Usage : /reasoning [on|off][/yellow]")
                continue
            reasoning_display = (not reasoning_display) if not arg else (arg == "on")
            console.print(f"[dim]Affichage du raisonnement : {'activé' if reasoning_display else 'désactivé'}.[/dim]")
            continue
        if cmd == "/reasoning-last":
            if not last_reasoning:
                console.print("[dim]Aucun raisonnement pour la dernière question.[/dim]")
                continue
            for i, texte in enumerate(last_reasoning):
                console.print(Panel(escape(texte), title=f"Raisonnement (tour {i + 1})", border_style="dim"))
            continue
        if cmd == "/errors":
            if not error_log:
                console.print("[dim]Aucune erreur enregistrée.[/dim]")
                continue
            table = Table(title="Erreurs de connexion / services")
            cols = ("heure", "service", "type", "message", "tokens")
            for col in cols:
                table.add_column(col)
            for e in error_log:
                table.add_row(*(escape(e[c]) for c in cols))
            console.print(table)
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
        except ServiceError as e:      # erreur déjà journalisée, on affiche le service fautif
            console.print(f"[red]Erreur ({escape(e.service)}) : {escape(str(e.cause))}[/red]")
            if e.tokens:
                console.print(f"[dim]Contexte : {escape(e.tokens)}[/dim]")
            if e.hint:
                console.print(f"[yellow]{escape(e.hint)}[/yellow]")
            messages = trim_unanswered(messages)
        except Exception as e:         # une erreur inattendue ne doit pas tuer la session
            log_error("inconnu", e)
            console.print(f"[red]Erreur : {escape(str(e))}[/red]")
            messages = trim_unanswered(messages)


def trim_unanswered(messages):
    """Retire la dernière question et les échanges d'outils qui n'ont pas abouti à une réponse."""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i]["role"] == "user":
            return messages[:i]
    return messages
