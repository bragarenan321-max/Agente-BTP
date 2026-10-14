"""Estruturas de dados compartilhadas pelo Agente BTP."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Status(Enum):
    PROCESSADA = "Com itens faltantes"
    SEM_REGISTROS = "Sem itens"
    NAO_ENCONTRADA = "Não consta no Monitor"
    WO_INVALIDA = "Formato inválido"
    SESSAO_EXPIRADA = "Sessão expirada"
    FALHA_COMUNICACAO = "Falha de comunicação"
    ERRO = "Erro"


# Colunas da planilha final: as da janela "Mensagens", na mesma ordem; o Consolidado
# (referência do alerta no Monitor) vai por último.
COLUNAS_RESULTADO = [
    "WO",
    "Nº do Material",
    "Fornecedor",
    "Quantidade faltante",
    "Mensagem",
    "Consolidado",
]


@dataclass
class LinhaMensagem:
    consolidado: str
    wo: str
    material: str
    fornecedor: str
    quantidade_faltante: Decimal | int | None
    mensagem: str

    def como_lista(self) -> list:
        return [
            self.wo,
            self.material,
            self.fornecedor,
            self.quantidade_faltante,
            self.mensagem,
            self.consolidado,
        ]


@dataclass
class ResultadoWO:
    wo: str
    status: Status
    linhas: list[LinhaMensagem] = field(default_factory=list)
    observacao: str = ""
    data_hora: datetime = field(default_factory=datetime.now)

    @property
    def registros(self) -> int:
        return len(self.linhas)


class SessaoExpirada(Exception):
    """A sessão autenticada não é mais válida (redirecionou ao SSO, 401, HTML)."""


class FalhaComunicacao(Exception):
    """Timeout, erro de rede ou HTTP 5xx."""


class ErroConsulta(Exception):
    """Resposta inesperada do serviço (HTTP 4xx, JSON inválido etc.)."""
