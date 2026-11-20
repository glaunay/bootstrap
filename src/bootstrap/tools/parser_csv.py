META_INF = {
        "type": "function",
        "function": {
            "name": "parser_csv",
            "description": "Récupère les protéines ID qui interagissent avec la ou les protéines cibles."
                           "La première ligne est le header. La première colonne contient les identifiants de la query et de la target. Le score principale est la 5ème colonne, iptm_ptm. La dernière colonne est le path des fichiers structures."
                           "Utile pour trouver les couples de protéines dans un CSV et leur score iptm_ptm ainsi que différent champs, décris dans le header. Pour chaque protéine ID, vérifie leur information dans le CSV."
                           "Utile pour vérifier leurs présences dans le CSV avant de cherche sur uniprot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "csv": {
                        "type": "string",
                        "description": "Le nom du fichier CSV.",
                    },
                    "prot": {
                        "type": "list",
                        "description": "Liste de protéines ID cibles.",
                    },
                },
                "required": ["csv"],
            },
        },
    }

def run(csv,prot=None) :
    with open(csv,"r") as files :
        dic={}
        ligne=[]
        for i in files : 
            if "jobs," in i :
                header=i[:-1]
                ligne.append(header)
            else :
                isp=i.split(",")[0].split("_")
                lig=i.split(",")[:-1]
                target=isp[3]
                query=isp[0]
                score=i.split(",")[5]
                if prot : 
                    if query in prot or target in prot : 
                        ligne.append(lig)
    return str(ligne)
