"""Sondagem pelos DADOS: quando o dicionário não revela de onde vêm as mensagens.

Raciocínio:
1. A consulta da lista já funciona (a WO foi encontrada), logo temos a linha real do alerta.
2. Se as mensagens existem, elas estão ligadas a essa linha de um destes jeitos:
   a) um link de navegação que o próprio servidor informa na linha (V2: __deferred.uri;
      V4: Campo@odata.navigationLink);
   b) as mensagens já vêm dentro da linha (lista expandida);
   c) as mensagens vêm como texto JSON num campo da linha.
3. Cada possibilidade é lida e só é aceita se os registros tiverem a cara da janela
   "Mensagens" (material, fornecedor, quantidade, mensagem — pelo nome e pelo formato).
"""
from __future__ import annotations

from typing import Callable

from .analisador import mapear_campos
from .campos import ESSENCIAIS
from .cliente import link_navegacao, registros_json, relativizar
from .modelos import ErroConsulta, FalhaComunicacao


def _pontos(campos: dict) -> int:
    return sum(1 for c in ESSENCIAIS if c in campos)


def _parecem_mensagens(registros: list[dict], wo: str) -> dict | None:
    for r in registros[:3]:
        campos = mapear_campos(r, wo)
        if _pontos(campos) >= 3 and ("material" in campos or "mensagem" in campos):
            return campos
    return None


def sondar(alerta: dict, wo: str, url_lista: str,
           ler: Callable[[str], list[dict]]) -> list[tuple[dict, list[str]]]:
    """Configurações parciais (modo das mensagens + campos) achadas na linha do alerta."""
    achados: list[tuple[dict, list[str]]] = []
    for chave, valor in alerta.items():
        if chave.startswith("__"):
            continue
        nome = chave.removesuffix("@odata.navigationLink")
        # a) link de navegação informado pelo servidor
        uri = link_navegacao(alerta, nome) if (isinstance(valor, dict) or nome != chave) else None
        if uri:
            try:
                registros = ler(relativizar(uri, url_lista))
            except (ErroConsulta, FalhaComunicacao):   # navegação que falha é descartada;
                registros = []                          # sessão expirada interrompe o preparo
            campos = _parecem_mensagens(registros, wo)
            if campos:
                achados.append(({"campo_mensagens_link": nome, "url_mensagens": None,
                                 "campo_mensagens_expandidas": None, "campos": campos},
                                [f"Mensagens pelo link '{nome}' de cada linha da lista."]))
            continue
        # b) mensagens já dentro da linha
        filhos = valor.get("results") if isinstance(valor, dict) else valor
        if isinstance(filhos, list) and filhos and isinstance(filhos[0], dict):
            campos = _parecem_mensagens(filhos, wo)
            if campos:
                achados.append(({"campo_mensagens_expandidas": chave, "url_mensagens": None, "campos": campos},
                                [f"Mensagens dentro da linha da lista, no campo '{chave}'."]))
            continue
        # c) texto JSON num campo
        if isinstance(valor, str) and valor.lstrip()[:1] in ("[", "{"):
            campos = _parecem_mensagens(registros_json(valor), wo)
            if campos:
                achados.append(({"campo_mensagens_json": chave, "url_mensagens": None,
                                 "campo_mensagens_expandidas": None, "campos": campos},
                                [f"Mensagens em texto JSON no campo '{chave}' da linha."]))
    return achados
