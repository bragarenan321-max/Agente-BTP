"""Montagem de URLs e interpretação de respostas OData (V2 e V4)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from .modelos import ErroConsulta


def literal(valor) -> str:
    """Escapa um valor para uso dentro de aspas simples em $filter (' vira '')."""
    return str(valor).replace("'", "''")


# Caracteres do modelo que ficam como estão (estrutura da URL + marcadores {..}[..]).
_SEGUROS_MODELO = "/?=&$'(),:;@!*+-._~{}[]%"


def codificar_valor(valor) -> str:
    """Valor de dado dentro da URL: escapa aspas OData e codifica TUDO (|, +, /, espaço...)."""
    return quote(literal(valor), safe="")


def preencher_modelo(modelo: str, wo: str | None = None, linha: dict | None = None) -> str:
    """Substitui {wo} e {linha[Campo]} no modelo de URL, com os valores codificados."""
    base = quote(modelo, safe=_SEGUROS_MODELO)
    valores = {k: codificar_valor(v) for k, v in (linha or {}).items()
               if not isinstance(v, (dict, list))}
    try:
        return base.format(wo=codificar_valor(wo) if wo is not None else "", linha=valores)
    except KeyError as exc:
        raise ErroConsulta(f"Campo {exc} ausente na linha do alerta.") from exc


def extrair_registros(corpo: str) -> list[dict]:
    return extrair_pagina(corpo)[0]


def extrair_pagina(corpo: str) -> tuple[list[dict], str | None]:
    """Aceita V2 ({d:{results:[...]}} ou {d:{...}}) e V4 ({value:[...]} ou entidade única).

    Devolve (registros, link da próxima página ou None).
    """
    try:
        dados = json.loads(corpo)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ErroConsulta(f"Resposta não é JSON válido: {exc}") from exc

    if isinstance(dados, dict) and "error" in dados:
        mensagem = dados["error"].get("message")
        if isinstance(mensagem, dict):
            mensagem = mensagem.get("value")
        raise ErroConsulta(f"Serviço retornou erro: {mensagem}")

    if isinstance(dados, dict) and "d" in dados:
        d = dados["d"]
        if isinstance(d, dict) and "results" in d:
            return list(d["results"]), d.get("__next")
        if isinstance(d, list):
            return d, None
        return ([d] if isinstance(d, dict) else []), None

    if isinstance(dados, dict) and "value" in dados:
        return list(dados["value"]), dados.get("@odata.nextLink")

    if isinstance(dados, dict):
        return [dados], None
    raise ErroConsulta("Formato de resposta OData não reconhecido.")


def obter_campo(registro: dict, caminho: str):
    """Lê um campo, aceitando caminho com ponto para estruturas aninhadas (ex.: to_X.Campo)."""
    atual = registro
    for parte in caminho.split("."):
        if not isinstance(atual, dict):
            return None
        atual = atual.get(parte)
    return atual


def como_texto(valor) -> str:
    """Identificadores são sempre texto: nada de float, nada de perder zero à esquerda."""
    if valor is None:
        return ""
    return str(valor).strip()


def como_quantidade(valor):
    """OData entrega Edm.Decimal como string ("2.000"). Converte para número exato."""
    if valor is None or valor == "":
        return None
    try:
        numero = Decimal(str(valor).strip())
    except InvalidOperation:
        return None
    return int(numero) if numero == numero.to_integral_value() else numero


_DATA_V2 = re.compile(r"^/Date\((-?\d+)([+-]\d{4})?\)/$")
_DATA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def instante(valor) -> float | None:
    """Data/hora de um valor OData (V2 "/Date(ms)/" ou ISO 8601) em segundos; None se não for data."""
    if not isinstance(valor, str):
        return None
    texto = valor.strip()
    m = _DATA_V2.match(texto)
    if m:
        return int(m.group(1)) / 1000
    if _DATA_ISO.match(texto):
        try:
            iso = texto.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None
