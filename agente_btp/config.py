"""Configuração da camada de dados (endpoints OData e nomes dos campos).

Gerada automaticamente pelo agente (autoconfig.py ou analisador.py) e gravada em
config/api.json (fora do controle de versão). Ver config/api.exemplo.json.

As URLs são MODELOS: caminho + query string, com marcadores substituídos a cada consulta:
  {wo}               -> WO informada
  {linha[Campo]}     -> valor do Campo na linha do alerta (consulta de mensagens)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_CONFIG = RAIZ / "config" / "api.json"

# Endereço do SAP (só para validar de qual site a aba conectada vem; o agente não o abre).
URL_LAUNCHPAD = os.environ.get(
    "AGENTE_BTP_URL", "https://claro-prd.launchpad.cfapps.br10.hana.ondemand.com/site#Shell-home")

CAMPOS_OBRIGATORIOS = ("material", "fornecedor", "quantidade_faltante", "mensagem")


@dataclass
class ApiConfig:
    url_lista: str
    campo_consolidado_lista: str
    # Mensagens: consulta própria (url_mensagens) ou expandidas dentro da linha da lista.
    url_mensagens: str | None
    campo_mensagens_expandidas: str | None
    campos: dict[str, str]


def carregar(caminho: Path = ARQUIVO_CONFIG) -> ApiConfig:
    if not caminho.exists():
        raise FileNotFoundError(
            "Conexão com o Monitor de alertas ainda não preparada."
        )
    return a_partir_de_dict(json.loads(caminho.read_text(encoding="utf-8")))


def salvar(dados: dict, caminho: Path = ARQUIVO_CONFIG) -> None:
    a_partir_de_dict(dados)  # valida antes de gravar
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def a_partir_de_dict(dados: dict) -> ApiConfig:
    if "{wo}" not in dados.get("url_lista", ""):
        raise ValueError("'url_lista' precisa conter o marcador {wo}.")
    if not dados.get("url_mensagens") and not dados.get("campo_mensagens_expandidas"):
        raise ValueError("Informe 'url_mensagens' ou 'campo_mensagens_expandidas'.")
    campos = dados.get("campos", {})
    faltando = [c for c in CAMPOS_OBRIGATORIOS if not campos.get(c)]
    if faltando:
        raise ValueError(f"Campos ausentes na configuração: {', '.join(faltando)}")
    return ApiConfig(
        url_lista=dados["url_lista"],
        campo_consolidado_lista=dados.get("campo_consolidado_lista") or "",
        url_mensagens=dados.get("url_mensagens"),
        campo_mensagens_expandidas=dados.get("campo_mensagens_expandidas"),
        campos=campos,
    )
