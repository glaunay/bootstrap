META_INF = {
        "type": "function",
        "function": {
            "name": "parser_csv",
            "description": "Récupère les protéines ID qui interagissent avec la ou les protéines cibles"
                           "La première ligne est le header. Le score principale est la 5ème colonne, iptm_ptm."
                           "Utile pour trouver les couples de protéines dans un CSV et leur score iptm_ptm",
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
            else :
                isp=i.split(",")[0].split("_")
                lig=i.split(",")
                target=isp[3]
                query=isp[0]
                score=i.split(",")[5]
                if prot : 
                    if query in prot or target in prot : 
                        ligne.append(lig)

#                    if target in prot or query in prot :
#                        if target not in dic :
#                            dic[target]=[(query,score)]
#                        else :
#                            dic[target].append((query,score))
#                else : 
#                    if target not in dic :
#                        dic[target]=[(query,score)]
#                    else :
#                        dic[target].append((query,score))
#    return str(dic)
    print(ligne)
    return str(ligne)
