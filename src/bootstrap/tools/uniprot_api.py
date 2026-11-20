META_INF = {
        "type": "function",
        "function": {
            "name": "uniprot_api",
            "description": "Fiche UniProt résumée d'une ou plusieurs protéines : nom, gène, organisme, fonction, "
                           "protéines interactantes, termes GO et identifiants PubMed. "
                           "À utiliser dès qu'un identifiant UniProt (ex. P98160) est mentionné.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uniprot_id": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste d'identifiants UniProt (accessions) à chercher, ex. [\"P98160\"]",
                    }
                },
                "required": ["uniprot_id"],
            },
        },
    }

import re

import requests

# On ne demande à UniProt que les champs utiles (~4x plus léger que la fiche complète).
FIELDS = ("accession,protein_name,gene_names,organism_name,"
          "cc_function,cc_interaction,cc_subunit,go,lit_pubmed_id")

FUNCTION_MAX_CHARS = 600
SUBUNIT_MAX_CHARS = 300
GO_MAX_PER_ASPECT = 6
PUBMED_MAX = 10
INTERACTORS_MAX = 10
GO_ASPECTS = {"F": "function", "P": "process", "C": "component"}
# Citations en ligne "(PubMed:123, PubMed:456)" et "(By similarity)" : redondantes avec la liste [PMID …]
_INLINE_CITATIONS = re.compile(r"\s*\((?:PubMed:\d+(?:,\s*)?)+\)|\s*\(By similarity\)")


def run(uniprot_id):
    if isinstance(uniprot_id, str):      # le modèle envoie parfois un identifiant seul
        uniprot_id = [uniprot_id]
    return "\n\n".join(summarize(fetch(acc.strip())) for acc in uniprot_id)


def fetch(acc):
    url = f"https://rest.uniprot.org/uniprotkb/{acc}.json"
    try:
        response = requests.get(url, params={"fields": FIELDS}, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if response.status_code in (400, 404):   # 400 = identifiant mal formé, 404 = inconnu
            raise ValueError(f"UniProt ID '{acc}' not found.") from e
        raise ConnectionError(f"HTTP error: {e}") from e
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"Network error: {e}") from e


# --- Mise en forme compacte -------------------------------------------------

def _cut(text, n):
    return text if len(text) <= n else text[:n].rstrip() + "…"


def _more(items, n):
    """Les n premiers éléments, puis '(+k)' s'il en reste."""
    shown = "; ".join(items[:n])
    return shown + (f" (+{len(items) - n})" if len(items) > n else "")


def _comments(entry, kind):
    return [c for c in entry.get("comments", []) if c.get("commentType") == kind]


def _name_line(entry):
    acc = entry.get("primaryAccession", "?")
    desc = entry.get("proteinDescription", {})
    rec = desc.get("recommendedName") or (desc.get("submissionNames") or [{}])[0]
    name = rec.get("fullName", {}).get("value", "nom inconnu")
    shorts = [s["value"] for s in rec.get("shortNames", [])]
    if shorts:
        name += f" ({', '.join(shorts)})"
    genes = [g["geneName"]["value"] for g in entry.get("genes", []) if "geneName" in g]
    organism = entry.get("organism", {}).get("scientificName")
    return " · ".join(p for p in (acc, ", ".join(genes), name, organism) if p)


def _function_line(entry):
    texts, pmids = [], []
    for c in _comments(entry, "FUNCTION"):
        for t in c.get("texts", []):
            texts.append(_INLINE_CITATIONS.sub("", t["value"]))
            pmids += [e["id"] for e in t.get("evidences", []) if e.get("source") == "PubMed"]
    if not texts:
        return None
    line = "Function: " + _cut(" ".join(texts), FUNCTION_MAX_CHARS)
    pmids = list(dict.fromkeys(pmids))   # sans doublons, ordre conservé
    if pmids:
        line += f" [PMID {_more(pmids, PUBMED_MAX).replace(';', ',')}]"
    return line


def _interactors_line(entry):
    # IntAct a une entrée par couple d'isoformes : on regroupe par partenaire en sommant les expériences.
    experiments, genes = {}, {}
    for c in _comments(entry, "INTERACTION"):
        for i in c.get("interactions", []):
            other = i.get("interactantTwo", {})
            acc = other.get("uniProtKBAccession", "?").split("-")[0]   # P17948-2 -> P17948
            experiments[acc] = experiments.get(acc, 0) + (i.get("numberOfExperiments") or 0)
            genes.setdefault(acc, other.get("geneName"))
    ranked = sorted(experiments, key=experiments.get, reverse=True)   # les mieux étayés d'abord
    partners = [f"{genes[a]} ({a}, {experiments[a]} exp)" if genes[a] else f"{a} ({experiments[a]} exp)"
                for a in ranked]
    if partners:
        return "Interactors (IntAct): " + _more(partners, INTERACTORS_MAX)
    # Pas de données IntAct : on se rabat sur le texte SUBUNIT
    subunit = " ".join(t["value"] for c in _comments(entry, "SUBUNIT") for t in c.get("texts", []))
    return "Subunit: " + _cut(subunit, SUBUNIT_MAX_CHARS) if subunit else None


def _go_line(entry):
    by_aspect = {k: [] for k in GO_ASPECTS}
    for x in entry.get("uniProtKBCrossReferences", []):
        if x.get("database") != "GO":
            continue
        for p in x.get("properties", []):
            if p["key"] == "GoTerm" and ":" in p["value"]:
                aspect, term = p["value"].split(":", 1)
                by_aspect.setdefault(aspect, []).append(term)
    parts = [f"{label}: {_more(by_aspect[k], GO_MAX_PER_ASPECT)}"
             for k, label in GO_ASPECTS.items() if by_aspect.get(k)]
    return "GO " + " | ".join(parts) if parts else None


def _pubmed_line(entry):
    pmids = [x["id"] for r in entry.get("references", [])
             for x in r.get("citation", {}).get("citationCrossReferences", [])
             if x.get("database") == "PubMed"]
    pmids = list(dict.fromkeys(pmids))
    if not pmids:
        return None
    return f"PubMed ({len(pmids)}): " + _more(pmids, PUBMED_MAX).replace(";", ",")


def summarize(entry):
    acc = entry.get("primaryAccession", "?")
    lines = [_name_line(entry), _function_line(entry), _interactors_line(entry),
             _go_line(entry), _pubmed_line(entry),
             f"https://www.uniprot.org/uniprotkb/{acc}/entry"]
    return "\n".join(l for l in lines if l)
