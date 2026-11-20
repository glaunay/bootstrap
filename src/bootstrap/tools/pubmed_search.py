META_INF =  {
        "type": "function",
        "function": {
            "name": "pubmed_search",
            "description": "Cherche des articles scientifiques sur PubMed et renvoie leurs titres, auteurs et résumés. "
                           "Utile pour trouver des études récentes, des études cliniques ou des informations "
                           "scientifiques précises sur des virus, des maladies ou des sujets de biologie.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "La requête de recherche, de préférence en anglais "
                                       "(par exemple 'influenza vaccine efficacy' ou 'SARS-CoV-2 spike protein').",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Nombre maximum d'articles à récupérer (5 maximum recommandé).",
                    },
                },
                "required": ["query"],    # max_results est facultatif
            },
        },
    }

import requests
import xml.etree.ElementTree as ET

def run(query, max_results=5):
    """La vraie fonction Python : cherche des articles sur PubMed et renvoie titres et résumés."""
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    try:
        return _search(base, query, max_results)
    except requests.exceptions.HTTPError as e:
        raise ConnectionError(f"PubMed HTTP error: {e}") from e
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"PubMed network error: {e}") from e
    # Toute autre erreur (XML inattendu...) remonte telle quelle et sera journalisée par /errors.


def _search(base, query, max_results):
    # 1. Première requête : PubMed nous renvoie les identifiants (PMID) des articles les plus pertinents
    r = requests.get(base + "esearch.fcgi", timeout=20, params={
        "db": "pubmed", "term": query, "retmode": "json", "retmax": max_results, "sort": "relevance",
    })
    r.raise_for_status()                         # lève une erreur si le serveur a répondu autre chose que 200
    liste_pmid = r.json()["esearchresult"]["idlist"]
    if not liste_pmid:
        return f"Aucun article trouvé sur PubMed pour '{query}'."

    # 2. Deuxième requête : avec ces identifiants, on récupère les articles complets (en XML)
    r = requests.get(base + "efetch.fcgi", timeout=20, params={
        "db": "pubmed", "id": ",".join(liste_pmid), "retmode": "xml",
    })
    r.raise_for_status()

    # 3. On parcourt le XML pour en tirer le titre, l'année et le résumé de chaque article
    texte = ""
    for article in ET.fromstring(r.content).findall(".//PubmedArticle"):
        pmid = article.find(".//PMID").text
        # itertext() récupère tout le texte, même s'il y a de la mise en forme (italique, exposant...)
        titre = "".join(article.find(".//ArticleTitle").itertext())
        annee = article.find(".//PubDate/Year")
        # Un résumé peut être découpé en plusieurs parties (Background, Methods...), on les recolle
        resume = " ".join("".join(p.itertext()) for p in article.findall(".//Abstract/AbstractText"))

        texte += f"PMID {pmid} - {titre} ({annee.text if annee is not None else 'année inconnue'})\n"
        texte += f"{resume[:1500] or 'Pas de résumé disponible'}\n\n"    # on coupe pour ne pas surcharger le modèle

    return texte
