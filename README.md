# bootstrap — `tlc`, a tool-calling LLM assistant

`tlc` is a small command-line assistant that sends a question to an LLM served by the
[Albert API](https://albert.api.etalab.gouv.fr) (OpenAI-compatible) and lets the model
call Python **tools** (for example a PubMed search) before writing its final answer.

Tools are plain Python files dropped into `src/bootstrap/tools/`; they are discovered
automatically at start-up, so adding a capability does not require touching the main loop.

---

## Installation

### Requirements

- Python **3.12+**
- [uv](https://docs.astral.sh/uv/) (recommended) or `pip`
- An Albert API key

### Install with uv

```bash
cd bootstrap
uv sync            # creates .venv and installs dependencies + the `tlc` command
```

### Install with pip (alternative)

```bash
cd bootstrap
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Configure the API key

Create a `.env` file at the root of the project (next to `pyproject.toml`):

```dotenv
ALBERT_API_KEY=your-key-here
```

The file is read with `python-dotenv` at start-up. Do not commit it.

---

## Usage

`tlc` can be run from any directory: tools are loaded from the `tools/` folder next to the
package source.

```bash
uv run tlc --question "Quelles sont les dernières études sur l'efficacité du vaccin antigrippal ?"
```

Or, with the virtual environment activated:

```bash
tlc --question "Explique ce qu'est la protéine spike du SARS-CoV-2" --t 0.1
```

### Options

| Option       | Default          | Description                                   |
|--------------|------------------|-----------------------------------------------|
| `--model`    | `gpt-oss-120b`   | Model name on the Albert API                  |
| `--question` | *(prompted)*     | The question to ask                           |
| `--t`        | `0.2`            | Sampling temperature                          |
| `--help`     |                  | Show help                                     |

If `--question` is omitted, `tlc` prompts for it interactively (press Enter to accept the
default question). `--t` is never prompted: it falls back to `0.2`.

### What happens under the hood

1. The question is sent to the model together with the JSON description of every loaded tool.
2. If the model answers directly, the answer is printed and the program exits.
3. If the model requests one or more tools, each tool's `run(...)` function is executed with
   the arguments chosen by the model, and its result is appended to the conversation with the
   `tool` role. If the model requests a tool that does not exist, an error message listing the
   available tools is returned to it instead, so it can retry with a valid name.
4. The model is queried again with those results. This loop runs at most **5 turns**.

The system prompt asks the model to answer in French and to cite the articles it relies on
(title, year, PMID).

---

## Creating a new tool

A tool is a single Python file in `src/bootstrap/tools/`. `ToolLoader`
(`src/bootstrap/tool_loader.py`) imports every `*.py` file in that folder — except files whose
name starts with `_` — and requires each one to define:

| Name       | Type     | Role                                                                      |
|------------|----------|---------------------------------------------------------------------------|
| `META_INF` | `dict`   | The tool description sent to the model (OpenAI function-calling schema)   |
| `run`      | function | The Python code executed when the model calls the tool                    |

A file missing either one makes `tlc` stop with a `TypeError` at start-up.

### Rules

1. **The file name must equal `META_INF["function"]["name"]`.** Tools are registered under the
   file name (`weather.py` → `weather`), but the model calls them by the name in `META_INF`.
   If the two differ, the call is ignored (`No Tool named ...`).
2. **Tool names must be unique** across the folder.
3. **`run` parameters must match the `properties`** declared in `META_INF`: the model's JSON
   arguments are passed as keyword arguments (`run(**arguments)`). Give optional parameters a
   default value and leave them out of `required`.
4. **`run` must return a string**: it becomes the content of the `tool` message read by the model.
5. **Do not raise exceptions** for expected failures (network error, nothing found…): catch them
   and return an explanatory string so the model can tell the user what went wrong.
6. Write a precise `description`: it is the only thing the model uses to decide *when* to call
   the tool and *how* to fill its arguments.
7. To disable a tool without deleting it, rename it with a leading underscore
   (`_my_tool.py`).

### Template

Create `src/bootstrap/tools/uniprot_search.py`:

```python
import requests

META_INF = {
    "type": "function",
    "function": {
        "name": "uniprot_search",          # must equal the file name (without .py)
        "description": "Search the UniProt protein database and return accession, "
                       "protein name and organism for the best matches. Use it to "
                       "identify a protein or find its UniProt accession.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Protein or gene name, e.g. 'spike SARS-CoV-2' or 'TP53 human'.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of entries to return (default 5).",
                },
            },
            "required": ["query"],
        },
    },
}


def run(query, max_results=5):
    """Called with the arguments chosen by the model. Must return a string."""
    try:
        r = requests.get(
            "https://rest.uniprot.org/uniprotkb/search",
            params={"query": query, "size": max_results, "format": "json"},
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return f"No UniProt entry found for '{query}'."

        lines = []
        for entry in results:
            name = (entry.get("proteinDescription", {})
                         .get("recommendedName", {})
                         .get("fullName", {})
                         .get("value", "unknown name"))
            organism = entry.get("organism", {}).get("scientificName", "unknown organism")
            lines.append(f"{entry['primaryAccession']} - {name} ({organism})")
        return "\n".join(lines)

    except Exception as err:
        return f"Error during UniProt search: {err}"
```

### Test it

Check that the tool loads and runs on its own:

```bash
uv run python -c "
from pathlib import Path
import bootstrap.tools
from bootstrap.tool_loader import ToolLoader
tl = ToolLoader(Path(bootstrap.tools.__file__).parent)
print([t['function']['name'] for t in tl.get_info()])
print(tl.get_exec('uniprot_search')(query='TP53 human', max_results=2))
"
```

Then ask `tlc` a question that should trigger it:

```bash
uv run tlc --question "Quel est l'identifiant UniProt de la protéine p53 humaine ?"
```

If the tool needs a new third-party package, add it with `uv add <package>`.

---

## Project layout

```
bootstrap/
├── pyproject.toml
├── .env                      # ALBERT_API_KEY (not committed)
└── src/bootstrap/
    ├── __init__.py           # `tlc` entry point: CLI + tool-calling loop
    ├── tool_loader.py        # discovers and loads tools
    └── tools/
        ├── pubmed_search.py  # PubMed search (NCBI E-utilities)
        └── dummy.py          # minimal example tool
```
