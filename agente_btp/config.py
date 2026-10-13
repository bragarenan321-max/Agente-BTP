"""Configuração da camada de dados (endpoints OData e nomes dos campos).

Os valores reais são preenchidos a partir da captura do Modo Descoberta e ficam em
config/api.json (fora do controle de versão). Ver config/api.exemplo.json.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_CONFIG = RAIZ / "config" / "api.json"

CAMPOS_OBRIGATORIOS = ("wo", "material", "fornecedor", "quantidade_faltante", "mensagem")


@dataclass
class ConsultaConfig:
    caminho: str = ""
    filtro: str = ""
    extras: dict[str, str] = field(default_factory=dict)


@dataclass
class ApiConfig:
    servico: str
    lista: ConsultaConfig
    # Campo da linha da lista que contém o nº do consolidado.
    campo_consolidado_lista: str
    # Mensagens: ou consulta própria (mensagens.caminho) ou vindas expandidas
    # dentro da linha da lista (campo_mensagens_expandidas).
    mensagens: ConsultaConfig | None
    campo_mensagens_expandidas: str | None
    # Campos de cada mensagem -> colunas da planilha.
    campos: dict[str, str]


def carregar(caminho: Path = ARQUIVO_CONFIG) -> ApiConfig:
    if not caminho.exists():
        raise FileNotFoundError(
            f"Configuração da API não encontrada em {caminho}. "
            "Execute o Modo Descoberta e envie a captura para configurar."
        )
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    return a_partir_de_dict(dados)


def a_partir_de_dict(dados: dict) -> ApiConfig:
    lista = ConsultaConfig(**dados["lista"])
    mensagens = ConsultaConfig(**dados["mensagens"]) if dados.get("mensagens") else None
    expandidas = dados.get("campo_mensagens_expandidas")
    if not mensagens and not expandidas:
        raise ValueError("Informe 'mensagens' ou 'campo_mensagens_expandidas' na configuração.")
    campos = dados["campos"]
    faltando = [c for c in CAMPOS_OBRIGATORIOS if c not in campos]
    if faltando:
        raise ValueError(f"Campos ausentes na configuração: {', '.join(faltando)}")
    return ApiConfig(
        servico=dados["servico"],
        lista=lista,
        campo_consolidado_lista=dados["campo_consolidado_lista"],
        mensagens=mensagens,
        campo_mensagens_expandidas=expandidas,
        campos=campos,
    )
