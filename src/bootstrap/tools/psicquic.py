META_INF = {
        "type": "function",
        "function": {
            "name": "psicquic",
            "description": "Client des bases de données d'interactions protein-proteine experiemntale",
            "parameters": {
                "type": "object",
                "properties": {
                    "uniprot_id": {
                        "type": "string",
                        "description": "Identifiant de la protéine",
                    }
                },
                "required": ["uniprot_id"],
            },
        },
    }

from pypsicquic import PsicquicStore
import rich_click as click

def run(uniprot_id):
    psq = PsicquicStore()
    try:
        resp = psq.get_from(["uniprotkb:" + uniprot_id])
    except Exception as e:
        return 'no experiemntal interactor in databases'

    res = set()
    for _ in resp:
        res.update([ x.replace('uniprotkb:', '')  for x in _.interactors if x.startswith('uniprotkb:') ] )
    return res - {uniprot_id}

@click.command()
@click.argument("uniprot_id")
def test(uniprot_id):
    psq = PsicquicStore()
    resp = psq.get_from(["uniprotkb:" + uniprot_id])
    res = set()
    for _ in resp:
        res.update([ x.replace('uniprotkb:', '')  for x in _.interactors if x.startswith('uniprotkb:') ] )
        #print(_.interactors)
    #print (res - {uniprot_id})
    return res - {uniprot_id}
