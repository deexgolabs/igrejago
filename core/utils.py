"""Utilitários pequenos e sem lar óbvio em outro módulo."""

import json

from django.core.serializers.json import DjangoJSONEncoder

# Achado numa revisão de segurança: vários templates embutem
# `json.dumps(...)` direto num `<script>` via `{{ x|safe }}` — `json.dumps`
# não escapa `<`/`>`/`&`, então um texto livre definido por staff (ex.:
# nome de departamento) contendo `</script>` quebra pra fora da tag e
# executa como HTML/JS na página de quem estiver olhando o mesmo
# dashboard. `json_script` do Django resolveria isso, mas trocaria o
# padrão de uso em cada template (viraria `<script type="application/json">`
# + `JSON.parse` em vez da variável JS direta já usada em todo canto) —
# em vez disso, este helper faz só o mínimo: serializa e troca os 3
# caracteres perigosos por escape unicode, que o JS decodifica de volta
# sem problema, mas nunca forma literalmente `</script>` no HTML fonte.
_ESCAPES = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026"}


def json_para_script(data):
    """`json.dumps` seguro pra embutir dentro de um `<script>` — o
    template continua usando `{{ x|safe }}` como já fazia, só que agora
    a string em si já vem garantidamente sem `</script>` literal."""
    bruto = json.dumps(data, cls=DjangoJSONEncoder)
    for perigoso, seguro in _ESCAPES.items():
        bruto = bruto.replace(perigoso, seguro)
    return bruto
