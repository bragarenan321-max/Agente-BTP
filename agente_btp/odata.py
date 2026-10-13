"""Montagem de URLs e interpretação de respostas OData (V2 e V4)."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from .modelos import ErroConsulta


def literal(valor) -> str:
    """Escapa um valor para uso dentro de aspas simples em $filter (' vira '')."""
    return str(valor).replace("'", "''")


def montar_url(servico: str, caminho: str, filtro: str | None = None,
               extras: dict[str, str] | None = None) -> str:
    base = servico if servico.endswith("/") else servico + "/"
    parametros = []
    if filtro:
        parametros.append("$filter=" + quote(filtro, safe="'()"))
    for chave, valor in (extras or {}).items():
        parametros.append(f"{chave}=" + quote(str(valor), safe="'(),"))
    url = base + caminho.lstrip("/")
    if parametros:
        url += ("&" if "?" in url else "?") + "&".join(parametros)
    return url


def extrair_registros(corpo: str) -> list[dict]:
    """Aceita V2 ({d:{results:[...]}} ou {d:{...}}) e V4 ({value:[...]} ou entidade única)."""
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
            return list(d["results"])
        return [d] if isinstance(d, dict) else []

    if isinstance(dados, dict) and "value" in dados:
        return list(dados["value"])

    if isinstance(dados, dict):
        return [dados]
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
