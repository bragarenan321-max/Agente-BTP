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
# Configurações de versões anteriores aceitavam caminhos que não traziam as mensagens:
# só vale a configuração gravada por esta versão (ou posterior), já validada com dados reais.
VERSAO_CONFIG = 5   # 5: descarta a fonte confirmada em 15/10 (tabela de materiais com "Ok", não a janela Mensagens)


@dataclass
class ApiConfig:
    url_lista: str
    campo_consolidado_lista: str
    # Mensagens: consulta própria (url_mensagens), expandidas dentro da linha da lista,
    # link de navegação da própria linha (__deferred) ou texto JSON guardado num campo.
    url_mensagens: str | None
    campo_mensagens_expandidas: str | None
    campos: dict[str, str]
    campo_mensagens_link: str | None = None
    campo_mensagens_json: str | None = None
    # Releitura de UM item pela chave (auditoria independente). Ex.: /srv/Itens({linha[ID]})
    url_item: str | None = None
    chaves_item: list | None = None
    # Mensagem do ALERTA (coluna Mensagem do Monitor, ex.: "Erro durante baixa de estoque…")
    campo_mensagem_alerta: str | None = None
    # Qual mensagem vai para a planilha: "item" (janela Mensagens) ou "alerta" (lista do Monitor)
    mensagem_origem: str = "item"
    confirmada: bool = False


def carregar(caminho: Path | None = None) -> ApiConfig:
    caminho = caminho or ARQUIVO_CONFIG
    if not caminho.exists():
        raise FileNotFoundError(
            "Conexão com o Monitor de alertas ainda não preparada."
        )
    return a_partir_de_dict(json.loads(caminho.read_text(encoding="utf-8")))


def salvar(dados: dict, caminho: Path | None = None) -> None:
    caminho = caminho or ARQUIVO_CONFIG
    a_partir_de_dict(dados)  # valida antes de gravar
    caminho.parent.mkdir(parents=True, exist_ok=True)
    dados = dict(dados, versao=VERSAO_CONFIG)
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def descartar_se_antiga(caminho: Path | None = None) -> bool:
    """Apaga a configuração gravada por versão anterior (ou ilegível). True se apagou."""
    caminho = caminho or ARQUIVO_CONFIG
    if not caminho.exists():
        return False
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        if int(dados.get("versao", 0)) >= VERSAO_CONFIG:
            a_partir_de_dict(dados)
            return False
    except (ValueError, TypeError, AttributeError):
        pass
    caminho.unlink(missing_ok=True)
    return True


def a_partir_de_dict(dados: dict) -> ApiConfig:
    if "{wo}" not in dados.get("url_lista", ""):
        raise ValueError("'url_lista' precisa conter o marcador {wo}.")
    modos = ("url_mensagens", "campo_mensagens_expandidas", "campo_mensagens_link", "campo_mensagens_json")
    if not any(dados.get(m) for m in modos):
        raise ValueError("Informe de onde vêm as mensagens: " + ", ".join(modos) + ".")
    campos = dados.get("campos", {})
    # Pelo menos 3 das 4 colunas da janela "Mensagens", sempre com Material ou Mensagem.
    presentes = [c for c in CAMPOS_OBRIGATORIOS if campos.get(c)
                 or (c == "mensagem" and dados.get("campo_mensagem_alerta"))]
    if len(presentes) < 3 or not (campos.get("material") or campos.get("mensagem")):
        faltando = [c for c in CAMPOS_OBRIGATORIOS if not campos.get(c)]
        raise ValueError(f"Campos ausentes na configuração: {', '.join(faltando)}")
    return ApiConfig(
        url_lista=dados["url_lista"],
        campo_consolidado_lista=dados.get("campo_consolidado_lista") or "",
        url_mensagens=dados.get("url_mensagens"),
        campo_mensagens_expandidas=dados.get("campo_mensagens_expandidas"),
        campos=campos,
        campo_mensagens_link=dados.get("campo_mensagens_link"),
        campo_mensagens_json=dados.get("campo_mensagens_json"),
        url_item=dados.get("url_item"),
        chaves_item=dados.get("chaves_item"),
        campo_mensagem_alerta=dados.get("campo_mensagem_alerta"),
        mensagem_origem=dados.get("mensagem_origem") or "item",
        confirmada=bool(dados.get("confirmada")),
    )
