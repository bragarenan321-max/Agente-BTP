"""Estruturas de dados compartilhadas pelo Agente BTP.

Três camadas, sempre separadas:
  ORIGEM     — Ocorrencia: o que foi lido do BTP numa leitura (valores + registro bruto).
  AUDITORIA  — Leitura (uma por rodada) e o estado da validação (Validado, Divergente...).
  ANÁLISE    — produzida na exportação a partir das linhas finais; nunca altera a origem.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Status(Enum):
    """Situação OPERACIONAL da WO no Monitor (independe da auditoria)."""
    PROCESSADA = "Com itens faltantes"
    SEM_REGISTROS = "Sem itens"
    NAO_ENCONTRADA = "Não consta no Monitor"
    WO_INVALIDA = "Formato inválido"
    SESSAO_EXPIRADA = "Sessão expirada"
    FALHA_COMUNICACAO = "Falha de comunicação"
    TEMPO_ESGOTADO = "Tempo esgotado"
    NAO_PROCESSADA = "Não processada"
    ERRO = "Erro"


# Situações em que a extração falhou (não há dado confiável da WO).
STATUS_FALHA = {Status.SESSAO_EXPIRADA, Status.FALHA_COMUNICACAO, Status.TEMPO_ESGOTADO,
                Status.NAO_PROCESSADA, Status.ERRO}


class Auditoria(Enum):
    """Resultado da AUDITORIA (fidelidade da extração)."""
    VALIDADO = "Validado"
    DIVERGENTE = "Divergente"
    INCOMPLETO = "Incompleto"
    NAO_CONCLUIDO = "Não concluído"
    NAO_APLICAVEL = "Não se aplica"   # WO inválida: nada a auditar


# Gravidade para consolidar o estado da WO a partir das ocorrências.
GRAVIDADE = {Auditoria.VALIDADO: 0, Auditoria.NAO_APLICAVEL: 0, Auditoria.NAO_CONCLUIDO: 1,
             Auditoria.INCOMPLETO: 2, Auditoria.DIVERGENTE: 3}

# Os cinco campos obrigatórios (aba 1 do Excel, nesta ordem).
CAMPOS = ("wo", "material", "fornecedor", "quantidade_faltante", "mensagem")
COLUNAS_RESULTADO = ["WO", "Material SAP", "Fornecedor SAP", "Quantidade faltante", "Mensagem"]
ROTULO = dict(zip(CAMPOS, COLUNAS_RESULTADO))


@dataclass
class LinhaMensagem:
    """Uma ocorrência de alerta, com os valores FINAIS (após a auditoria)."""
    consolidado: str
    wo: str
    material: str
    fornecedor: str
    quantidade_faltante: Decimal | int | None
    mensagem: str
    chave: str = ""
    auditoria: Auditoria = Auditoria.NAO_CONCLUIDO
    observacao: str = ""
    # Notas por campo (divergente, não obtido...), viram comentários na célula do Excel.
    notas: dict = field(default_factory=dict)

    def como_lista(self) -> list:
        return [self.wo, self.material, self.fornecedor, self.quantidade_faltante, self.mensagem]

    def valor(self, campo: str):
        return getattr(self, campo)


@dataclass
class Ocorrencia:
    """Uma ocorrência como LIDA do BTP numa leitura (dado de origem, sem interpretação)."""
    chave: str
    valores: dict                      # campo -> valor (os 5 campos + consolidado)
    bruto: dict                        # registro do BTP (só campos simples)
    alerta: dict = field(default_factory=dict)
    fonte: dict = field(default_factory=dict)        # coluna -> campo técnico do BTP
    outras_quantidades: dict = field(default_factory=dict)
    marca_tempo: str | None = None     # campo de última alteração, se o BTP informar


@dataclass
class Leitura:
    """Uma rodada de leitura de uma WO no BTP (0 = extração inicial)."""
    rodada: int
    tipo: str                          # "Extração inicial" | "Nova consulta" | "Releitura por chave" | ...
    quando: datetime = field(default_factory=datetime.now)
    itens: dict | None = None          # chave -> Ocorrencia (None = leitura falhou)
    alertas: int | None = None         # linhas da WO na lista do Monitor (consultas completas)
    duplicadas: int = 0
    avisos: list = field(default_factory=list)
    erro: str = ""

    @property
    def ok(self) -> bool:
        return self.itens is not None and not self.erro


@dataclass
class ResultadoWO:
    wo: str
    status: Status
    linhas: list[LinhaMensagem] = field(default_factory=list)
    observacao: str = ""
    data_hora: datetime = field(default_factory=datetime.now)
    auditoria: Auditoria = Auditoria.NAO_CONCLUIDO
    observacao_auditoria: str = ""
    leituras: list[Leitura] = field(default_factory=list)
    rodadas_previstas: int = 0
    registros_auditoria: list[dict] = field(default_factory=list)
    motivo: str = ""     # motivo classificado (diagnóstico) quando a consulta falhou
    origem: str = ""     # caminho das leituras: "consulta individual" | "consulta em lote (N WOs)"

    @property
    def registros(self) -> int:
        return len(self.linhas)

    @property
    def rodadas_concluidas(self) -> int:
        return sum(1 for l in self.leituras if l.rodada > 0 and l.ok and l.tipo != "Leitura extra")


class _ComMotivo(Exception):
    """Exceção com MOTIVO classificado (taxonomia do diagnóstico) e EVIDÊNCIA da 1ª observação
    (URL, status HTTP, tipo e trecho do corpo da resposta — sem cookies nem tokens)."""
    motivo_padrao = "excecao_app"

    def __init__(self, mensagem: str = "", motivo: str | None = None, evidencia: dict | None = None):
        super().__init__(mensagem)
        self.motivo = motivo or self.motivo_padrao
        self.evidencia = dict(evidencia or {})


class SessaoExpirada(_ComMotivo):
    """A sessão autenticada não é mais válida (redirecionou ao SSO, 401, 403, HTML de login)."""
    motivo_padrao = "sessao_expirada"


class FalhaComunicacao(_ComMotivo):
    """Timeout de uma consulta, erro de rede, HTTP 429 ou 5xx (pode ser repetida)."""
    motivo_padrao = "falha_rede"


class ErroConsulta(_ComMotivo):
    """Resposta inesperada do serviço (HTTP 4xx, JSON inválido etc.)."""
    motivo_padrao = "consulta_rejeitada"


class TempoEsgotado(_ComMotivo):
    """O tempo máximo da WO (ou do lote) acabou; não adianta repetir."""
    motivo_padrao = "timeout_wo"
