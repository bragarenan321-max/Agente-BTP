"""Estruturas de dados compartilhadas pelo Agente BTP."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Status(Enum):
    PROCESSADA = "Processada"
    SEM_REGISTROS = "Sem registros"
    NAO_ENCONTRADA = "Não encontrada no monitor"
    WO_INVALIDA = "WO inválida"
    SESSAO_EXPIRADA = "Sessão expirada"
    FALHA_COMUNICACAO = "Falha de comunicação"
    ERRO = "Erro"


# Colunas da planilha final, na mesma ordem do Download da janela "Mensagens".
COLUNAS_RESULTADO = [
    "Consolidado",
    "WO",
    "Nº do Material",
    "Fornecedor",
    "Quantidade faltante",
    "Mensagem",
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
            self.consolidado,
            self.wo,
            self.material,
            self.fornecedor,
            self.quantidade_faltante,
            self.mensagem,
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
