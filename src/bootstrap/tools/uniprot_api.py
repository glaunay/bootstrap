import re
import requests
import rich_click as click

# Champs UniProt que le modèle peut demander (noms vérifiés sur
# https://rest.uniprot.org/configure/uniprotkb/result-fields). Liste volontairement courte :
# le schéma des outils est renvoyé au LLM à chaque appel.
FIELD_DOCS = {
    "accession": "identifiant UniProt (toujours inclus)",
    "protein_name": "nom de la protéine",
    "gene_names": "nom du gène",
    "organism_name": "organisme",
    "length": "longueur de la séquence (acides aminés)",
    "mass": "masse moléculaire",
    "protein_families": "famille protéique",
    "cc_function": "fonction (texte + PMIDs qui l'étayent)",
    "cc_catalytic_activity": "réactions catalysées",
    "cc_pathway": "voies métaboliques",
    "cc_subcellular_location": "localisation subcellulaire (ex. Secreted, Membrane, Nucleus)",
    "cc_tissue_specificity": "tissus d'expression",
    "cc_interaction": "partenaires d'interaction IntAct (avec nombre d'expériences)",
    "cc_subunit": "structure quaternaire / partenaires décrits en texte",
    "cc_domain": "domaines (texte)",
    "cc_ptm": "modifications post-traductionnelles",
    "cc_disease": "maladies associées",
    "cc_similarity": "similarités de séquence",
    "ft_signal": "peptide signal (positions)",
    "ft_transmem": "segments transmembranaires (positions)",
    "ft_domain": "domaines (positions)",
    "keyword": "mots-clés UniProt",
    "go": "tous les termes GO",
    "go_f": "termes GO fonction moléculaire",
    "go_p": "termes GO processus biologique",
    "go_c": "termes GO composant cellulaire",
    "lit_pubmed_id": "identifiants PubMed des références",
    "xref_pdb": "structures 3D PDB",
}

DEFAULT_FIELDS = ["accession", "protein_name", "gene_names", "organism_name",
                  "cc_function", "cc_interaction", "cc_subunit", "go", "lit_pubmed_id"]

META_INF = {
        "type": "function",
        "function": {
            "name": "uniprot_api",
            "description": "Fiche UniProt résumée d'une ou plusieurs protéines. "
                           "À utiliser dès qu'un identifiant UniProt (ex. P98160) est mentionné.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uniprot_id": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste d'identifiants UniProt (accessions) à chercher, ex. [\"P98160\"]",
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(FIELD_DOCS)},
                        "description": "Champs UniProt à récupérer. Par défaut : " + ", ".join(DEFAULT_FIELDS) + ". "
                                       "Ne demander que le nécessaire : par exemple, pour savoir si des protéines "
                                       "sont secrétées, fields=[\"gene_names\", \"cc_subcellular_location\"]. "
                                       "Signification : " + "; ".join(f"{k} = {v}" for k, v in FIELD_DOCS.items()),
                    },
                },
                "required": ["uniprot_id"],
            },
        },
    }

FUNCTION_MAX_CHARS = 600
TEXT_MAX_CHARS = 300        # autres commentaires texte (sous-unité, PTM, tissus…)
LIST_MAX = 6                # GO par aspect, mots-clés, maladies, localisations…
PUBMED_MAX = 10
INTERACTORS_MAX = 10
LOCATION_GROUPS_MAX = 12
GO_ASPECTS = {"F": "function", "P": "process", "C": "component"}
GO_FIELD_ASPECTS = {"go": "FPC", "go_f": "F", "go_p": "P", "go_c": "C"}
NAME_FIELDS = {"accession", "protein_name", "gene_names", "organism_name"}
# Citations en ligne "(PubMed:123, PubMed:456)" et "(By similarity)" : redondantes avec la liste [PMID …]
_INLINE_CITATIONS = re.compile(r"\s*\((?:PubMed:\d+(?:,\s*)?)+\)|\s*\(By similarity\)")


def run(uniprot_id, fields=None):
    if isinstance(uniprot_id, str):      # le modèle envoie parfois un identifiant seul
        uniprot_id = [uniprot_id]
    fields = validate_fields(fields)
    return "\n\n".join(summarize(fetch(acc.strip(), fields), fields) for acc in uniprot_id)


def validate_fields(fields):
    """Liste de champs valides, sans doublon, avec `accession` toujours présent."""
    if not fields:
        fields = DEFAULT_FIELDS
    if isinstance(fields, str):          # "a,b" ou "a"
        fields = fields.split(",")
    fields = [f.strip() for f in fields if f.strip()]
    invalid = [f for f in fields if f not in FIELD_DOCS]
    if invalid:
        raise ValueError(f"Champ(s) UniProt invalide(s) : {', '.join(invalid)}. "
                         f"Champs valides : {', '.join(FIELD_DOCS)}")
    return list(dict.fromkeys(["accession"] + fields))


def fetch(acc, fields):
    url = f"https://rest.uniprot.org/uniprotkb/{acc}.json"
    try:
        # On ne demande à UniProt que les champs voulus (bien plus léger que la fiche complète).
        response = requests.get(url, params={"fields": ",".join(fields)}, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if response.status_code in (400, 404):   # 400 = identifiant mal formé, 404 = inconnu
            raise ValueError(f"UniProt ID '{acc}' not found.") from e
        raise ConnectionError(f"HTTP error: {e}") from e
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"Network error: {e}") from e


# --- Outils de mise en forme --------------------------------------------------

def _cut(text, n):
    return text if len(text) <= n else text[:n].rstrip() + "…"


def _more(items, n, sep="; "):
    """Les n premiers éléments, puis '(+k)' s'il en reste."""
    shown = sep.join(items[:n])
    return shown + (f" (+{len(items) - n})" if len(items) > n else "")


def _unique(items):
    return list(dict.fromkeys(items))


def _comments(entry, kind):
    return [c for c in entry.get("comments", []) if c.get("commentType") == kind]


def _comment_texts(entry, kind):
    return [_INLINE_CITATIONS.sub("", t["value"]) for c in _comments(entry, kind) for t in c.get("texts", [])]


# --- Un rendu par champ (ou famille de champs) --------------------------------

def _name_line(entry):
    acc = entry.get("primaryAccession", "?")
    desc = entry.get("proteinDescription", {})
    rec = desc.get("recommendedName") or (desc.get("submissionNames") or [{}])[0]
    name = rec.get("fullName", {}).get("value")
    shorts = [s["value"] for s in rec.get("shortNames", [])]
    if name and shorts:
        name += f" ({', '.join(shorts)})"
    genes = [g["geneName"]["value"] for g in entry.get("genes", []) if "geneName" in g]
    organism = entry.get("organism", {}).get("scientificName")
    return " · ".join(p for p in (acc, ", ".join(genes), name, organism) if p)


def _function(entry):
    texts, pmids = [], []
    for c in _comments(entry, "FUNCTION"):
        for t in c.get("texts", []):
            texts.append(_INLINE_CITATIONS.sub("", t["value"]))
            pmids += [e["id"] for e in t.get("evidences", []) if e.get("source") == "PubMed"]
    if not texts:
        return None
    line = "Function: " + _cut(" ".join(texts), FUNCTION_MAX_CHARS)
    pmids = _unique(pmids)
    return line + (f" [PMID {_more(pmids, PUBMED_MAX, ', ')}]" if pmids else "")


def _interactions(entry):
    # IntAct a une entrée par couple d'isoformes : on regroupe par partenaire en sommant les expériences.
    experiments, genes = {}, {}
    for c in _comments(entry, "INTERACTION"):
        for i in c.get("interactions", []):
            other = i.get("interactantTwo", {})
            acc = other.get("uniProtKBAccession", "?").split("-")[0]   # P17948-2 -> P17948
            experiments[acc] = experiments.get(acc, 0) + (i.get("numberOfExperiments") or 0)
            genes.setdefault(acc, other.get("geneName"))
    if not experiments:
        return None
    ranked = sorted(experiments, key=experiments.get, reverse=True)   # les mieux étayés d'abord
    partners = [f"{genes[a]} ({a}, {experiments[a]} exp)" if genes[a] else f"{a} ({experiments[a]} exp)"
                for a in ranked]
    return "Interactors (IntAct): " + _more(partners, INTERACTORS_MAX)


def _text_comment(kind, label):
    def render(entry):
        texts = _comment_texts(entry, kind)
        return f"{label}: " + _cut(" ".join(texts), TEXT_MAX_CHARS) if texts else None
    return render


def _subcellular_location(entry):
    # Un bloc par forme de la protéine (isoforme, fragment clivé…) : on garde le nom de la forme,
    # sinon "Secreted" peut appartenir à un fragment et être perdu ou mal attribué.
    groups = []
    for c in _comments(entry, "SUBCELLULAR LOCATION"):
        locations = _unique(loc["location"]["value"] for loc in c.get("subcellularLocations", []) if "location" in loc)
        if locations:
            text = "; ".join(locations)   # pas de virgule : les noms en contiennent ("Cell projection, growth cone")
            groups.append(f"{c['molecule']}: {text}" if c.get("molecule") else text)
    # Plafond large : les localisations sont courtes et c'est souvent l'info cherchée.
    return "Location: " + _more(groups, LOCATION_GROUPS_MAX, " | ") if groups else None


def _catalytic_activity(entry):
    reactions = _unique(c["reaction"]["name"] for c in _comments(entry, "CATALYTIC ACTIVITY") if "reaction" in c)
    return "Catalytic activity: " + _cut(_more(reactions, 3), TEXT_MAX_CHARS) if reactions else None


def _disease(entry):
    diseases = _unique(
        f"{d['diseaseId']} ({d['acronym']})" if d.get("acronym") else d["diseaseId"]
        for c in _comments(entry, "DISEASE") if (d := c.get("disease")) and "diseaseId" in d)
    return "Disease: " + _more(diseases, LIST_MAX) if diseases else None


def _feature(ft_type, label):
    def render(entry):
        spans = [f"{f['location']['start']['value']}-{f['location']['end']['value']}"
                 + (f" {f['description']}" if f.get("description") else "")
                 for f in entry.get("features", []) if f.get("type") == ft_type]
        return f"{label}: " + _more(spans, LIST_MAX, ", ") if spans else None
    return render


def _length(entry):
    n = entry.get("sequence", {}).get("length")
    return f"Length: {n} aa" if n else None


def _mass(entry):
    m = entry.get("sequence", {}).get("molWeight")
    return f"Mass: {m / 1000:.1f} kDa" if m else None


def _keyword(entry):
    kws = [k["name"] for k in entry.get("keywords", [])]
    return "Keywords: " + _more(kws, 10) if kws else None


def _go(aspects):
    def render(entry):
        by_aspect = {k: [] for k in GO_ASPECTS}
        for x in entry.get("uniProtKBCrossReferences", []):
            if x.get("database") != "GO":
                continue
            for p in x.get("properties", []):
                if p["key"] == "GoTerm" and ":" in p["value"]:
                    aspect, term = p["value"].split(":", 1)
                    by_aspect.setdefault(aspect, []).append(term)
        parts = [f"{GO_ASPECTS[k]}: {_more(by_aspect[k], LIST_MAX)}" for k in aspects if by_aspect.get(k)]
        return "GO " + " | ".join(parts) if parts else None
    return render


def _xref(database, label):
    def render(entry):
        ids = [x["id"] for x in entry.get("uniProtKBCrossReferences", []) if x.get("database") == database]
        return f"{label} ({len(ids)}): " + _more(ids, PUBMED_MAX, ", ") if ids else None
    return render


def _pubmed(entry):
    pmids = _unique(x["id"] for r in entry.get("references", [])
                    for x in r.get("citation", {}).get("citationCrossReferences", [])
                    if x.get("database") == "PubMed")
    return f"PubMed ({len(pmids)}): " + _more(pmids, PUBMED_MAX, ", ") if pmids else None


# Ordre d'affichage fixe (indépendant de l'ordre demandé), pour une sortie prévisible.
RENDERERS = {
    "length": _length,
    "mass": _mass,
    "protein_families": _text_comment("SIMILARITY", "Family"),
    "cc_similarity": _text_comment("SIMILARITY", "Similarity"),
    "cc_function": _function,
    "cc_catalytic_activity": _catalytic_activity,
    "cc_pathway": _text_comment("PATHWAY", "Pathway"),
    "cc_subcellular_location": _subcellular_location,
    "cc_tissue_specificity": _text_comment("TISSUE SPECIFICITY", "Tissue specificity"),
    "cc_interaction": _interactions,
    "cc_subunit": _text_comment("SUBUNIT", "Subunit"),
    "cc_domain": _text_comment("DOMAIN", "Domain"),
    "cc_ptm": _text_comment("PTM", "PTM"),
    "cc_disease": _disease,
    "ft_signal": _feature("Signal", "Signal peptide"),
    "ft_transmem": _feature("Transmembrane", "Transmembrane"),
    "ft_domain": _feature("Domain", "Domains"),
    "keyword": _keyword,
    "go": _go("FPC"),
    "go_f": _go("F"),
    "go_p": _go("P"),
    "go_c": _go("C"),
    "lit_pubmed_id": _pubmed,
    "xref_pdb": _xref("PDB", "PDB"),
}
assert set(RENDERERS) | NAME_FIELDS == set(FIELD_DOCS), "chaque champ documenté doit avoir un rendu"


def summarize(entry, fields=DEFAULT_FIELDS):
    acc = entry.get("primaryAccession", "?")
    wanted = set(fields)
    # Si IntAct a déjà des partenaires, le texte SUBUNIT est un doublon : on ne le garde qu'en secours.
    if "cc_interaction" in wanted and _interactions(entry):
        wanted.discard("cc_subunit")
    # "go" couvre déjà go_f / go_p / go_c.
    if "go" in wanted:
        wanted -= {"go_f", "go_p", "go_c"}
    lines = [_name_line(entry)]
    lines += [render(entry) for field, render in RENDERERS.items() if field in wanted]
    lines.append(f"https://www.uniprot.org/uniprotkb/{acc}/entry")
    return "\n".join(l for l in lines if l)



def run(uniprot_id, fields=None):
    if isinstance(uniprot_id, str):      # le modèle envoie parfois un identifiant seul
        uniprot_id = [uniprot_id]
    fields = validate_fields(fields)
    return "\n\n".join(summarize(fetch(acc.strip(), fields), fields) for acc in uniprot_id)


@click.command()
@click.argument("uniprot_id")
def test(uniprot_id):
    if isinstance(uniprot_id, str):      # le modèle envoie parfois un identifiant seul
        uniprot_id = [uniprot_id]
    fields = validate_fields(None)
    print ("\n\n".join(summarize(fetch(acc.strip(), fields), fields) for acc in uniprot_id) )
