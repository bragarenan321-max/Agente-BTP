"""Cliente de dados SOMENTE LEITURA.

Só existe uma operação de rede: GET. Não há caminho no código para POST, PUT,
PATCH, DELETE ou $batch — assim o agente não consegue acionar "Reprocessar" nem
"Efetuar Baixa", nem por engano.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin

from . import odata
from .config import ApiConfig
from .modelos import ErroConsulta, FalhaComunicacao, LinhaMensagem, SessaoExpirada


@dataclass
class Resposta:
    status: int
    content_type: str
    corpo: str
    redirecionado: bool = False


# Transporte: recebe uma URL e devolve a Resposta de um GET feito com a sessão
# autenticada. Em produção é a aba do SAP (ponte.py); nos testes, um falso.
Transporte = Callable[[str], Resposta]

LIMITE_PAGINAS = 50


def classificar(resposta: Resposta) -> str:
    """Valida a resposta HTTP e devolve o corpo, ou levanta a exceção adequada."""
    if resposta.redirecionado or resposta.status in (0, 401):
        raise SessaoExpirada("Sessão expirada ou não autenticada.")
    if resposta.status == 403:
        raise ErroConsulta("Acesso negado (HTTP 403).")
    if resposta.status >= 500:
        raise FalhaComunicacao(f"Servidor indisponível (HTTP {resposta.status}).")
    if resposta.status == 404:
        raise ErroConsulta("Endereço de dados não encontrado (HTTP 404).")
    if resposta.status >= 400:
        raise ErroConsulta(f"Consulta rejeitada (HTTP {resposta.status}).")
    if "html" in resposta.content_type.lower():
        # O approuter devolve a página de login em HTML quando a sessão cai.
        raise SessaoExpirada("Recebida página HTML no lugar de dados (provável login).")
    return resposta.corpo


class ClienteBTP:
    def __init__(self, config: ApiConfig, transporte: Transporte):
        self.config = config
        self._transporte = transporte

    def _get(self, url: str) -> list[dict]:
        """GET com paginação do servidor (__next / @odata.nextLink)."""
        registros: list[dict] = []
        for _ in range(LIMITE_PAGINAS):
            pagina, proximo = odata.extrair_pagina(classificar(self._transporte(url)))
            registros.extend(pagina)
            if not proximo:
                return registros
            url = urljoin(url, proximo)
        raise ErroConsulta(f"Mais de {LIMITE_PAGINAS} páginas de resultado; consulta interrompida.")

    def buscar_alertas(self, wo: str) -> list[dict]:
        return self._get(odata.preencher_modelo(self.config.url_lista, wo=wo))

    def buscar_mensagens(self, alerta: dict) -> list[dict]:
        if self.config.campo_mensagens_expandidas == ".":
            return [alerta]  # a própria linha já é a mensagem
        if self.config.campo_mensagens_expandidas:
            bruto = odata.obter_campo(alerta, self.config.campo_mensagens_expandidas)
            if isinstance(bruto, dict) and "results" in bruto:
                bruto = bruto["results"]
            return list(bruto or [])
        return self._get(odata.preencher_modelo(self.config.url_mensagens, linha=alerta))

    def converter(self, wo: str, alerta: dict, mensagem: dict) -> LinhaMensagem:
        campos = self.config.campos
        consolidado = odata.obter_campo(mensagem, campos["consolidado"]) if "consolidado" in campos else None
        if consolidado in (None, ""):
            consolidado = odata.obter_campo(alerta, self.config.campo_consolidado_lista)
        return LinhaMensagem(
            consolidado=odata.como_texto(consolidado),
            wo=odata.como_texto(odata.obter_campo(mensagem, campos["wo"])) if campos.get("wo") else wo,
            material=odata.como_texto(odata.obter_campo(mensagem, campos["material"])),
            fornecedor=odata.como_texto(odata.obter_campo(mensagem, campos["fornecedor"])),
            quantidade_faltante=odata.como_quantidade(
                odata.obter_campo(mensagem, campos["quantidade_faltante"])),
            mensagem=odata.como_texto(odata.obter_campo(mensagem, campos["mensagem"])),
        )
