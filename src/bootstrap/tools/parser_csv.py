META_INF = {
        "type": "function",
        "function": {
            "name": "parser_csv",
            "description": "Récupère les protéines ID qui interagissent avec la protéine cible"
                           "Utile pour trouver les couples de protéines dans un CSV et leur score iptm_ptm",
            "parameters": {
                "type": "object",
                "properties": {
                    "csv": {
                        "type": "string",
                        "description": "Le nom du fichier CSV.",
                    },
                },
                "required": ["csv"],
            },
        },
    }

def run(csv) :
    with open(csv,"r") as files :
        dic={}
        for i in files : 
            if "jobs," in i :
                header=i[:-1]
            else :
                isp=i.split(",")[0].split("_")
                target=isp[3]
                query=isp[0]
                score=i.split(",")[5]
                if target not in dic :
                    dic[target]=[(query,score)]
                else :
                    dic[target].append((query,score))
    return str(dic)
