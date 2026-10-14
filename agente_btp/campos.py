"""Reconhecimento dos campos do SAP pelo NOME (por partes) e, quando houver, pelo VALOR.

Nomes técnicos variam muito entre sistemas: Wo, NumeroWo, WorkOrder, ORDEM_SERVICO;
Matnr, Material, CodMaterial; Lifnr, Fornecedor, Vendor... Por isso o nome é quebrado
em partes (camelCase, _, -) e cada parte é comparada com um vocabulário.
"""
from __future__ import annotations

import re

ESSENCIAIS = ("material", "fornecedor", "quantidade_faltante", "mensagem")

# Ordem = precedência (um campo recebe o primeiro tipo que casar).
_VOCABULARIO = (
    ("wo", lambda p, n: "wo" in p or "workorder" in n or ("work" in p and "order" in p)
        or ("ordem" in p and ("servico" in p or "serv" in p or len(p) == 1))),
    ("consolidado", lambda p, n: any(x.startswith("consol") for x in p)),
    ("material", lambda p, n: any(x in ("matnr", "material", "materiais", "mat", "produto", "sku")
                                  or x.startswith("materia") for x in p)),
    ("fornecedor", lambda p, n: any(x.startswith("fornec") or x in ("lifnr", "vendor", "supplier", "forn")
                                    for x in p)),
    ("quantidade_faltante", lambda p, n: any(x.startswith("quant") or x in ("qtd", "qtde", "qty", "menge",
                                                                         "falta", "faltante") for x in p)),
    ("mensagem", lambda p, n: any(x.startswith("mensag") or x in ("message", "msg", "texto", "text", "erro",
                                                                  "error", "descricao", "desc") for x in p)),
)
# Partes que indicam descrição, não código (Material vs MaterialText).
_DESCRITIVAS = {"text", "texto", "desc", "descricao", "nome", "name"}


def partes(nome: str) -> list[str]:
    return [p.lower() for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", nome)]


def tipo_do_campo(nome: str) -> str | None:
    p = partes(nome)
    n = nome.lower().replace("_", "")
    for tipo, regra in _VOCABULARIO:
        if regra(p, n):
            return tipo
    return None


def descritivo(nome: str) -> bool:
    return bool(_DESCRITIVAS.intersection(partes(nome)))


# Formatos de VALOR típicos do SAP (o que se vê na janela "Mensagens").
_VALOR = {
    "material": re.compile(r"^\d{18}$"),        # MATNR com zeros à esquerda
    "fornecedor": re.compile(r"^\d{10}$"),      # LIFNR com zeros à esquerda
}
