"""Configuração automática pelo dicionário de dados do SAP ($metadata) — sem ação do usuário.

Raciocínio:
1. A aba informa quais serviços OData os apps abertos usam (configuração do UI5).
2. Para cada serviço, o $metadata descreve os conjuntos de dados e seus campos.
3. O conjunto das MENSAGENS é o que tem material, fornecedor, quantidade e mensagem.
   O da LISTA é o que tem a WO (e de preferência o Consolidado).
4. A ligação entre os dois é feita pelo Consolidado (ou, na falta dele, pela WO).

É um caminho de reserva/aceleração: o aprendizado observando a tela (analisador.py),
quando acontece, tem prioridade por se basear em consultas reais.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from .analisador import ESSENCIAIS, PALAVRAS

TIPOS_TEXTO = {"Edm.String"}
TIPOS_NUMERO = {"Edm.Decimal", "Edm.Int16", "Edm.Int32", "Edm.Int64", "Edm.Double", "Edm.Single", "Edm.Byte"}


class MetadataSemCorrespondencia(Exception):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def ler_metadata(xml: str) -> dict[str, dict[str, str]]:
    """{conjunto: {campo: tipo}} a partir do EDMX (OData V2 ou V4)."""
    raiz = ET.fromstring(xml)
    tipos: dict[str, dict[str, str]] = {}
    for el in raiz.iter():
        if _local(el.tag) == "EntityType" and el.get("Name"):
            tipos[el.get("Name")] = {p.get("Name"): p.get("Type", "")
                                     for p in el if _local(p.tag) == "Property" and p.get("Name")}
    conjuntos = {}
    for el in raiz.iter():
        if _local(el.tag) == "EntitySet" and el.get("Name"):
            tipo = (el.get("EntityType") or "").rsplit(".", 1)[-1]
            conjuntos[el.get("Name")] = tipos.get(tipo, {})
    return conjuntos


def _afinidade(nome: str, tipo: str) -> int:
    if nome in ("material", "fornecedor", "mensagem", "consolidado", "wo"):
        return 2 if tipo in TIPOS_TEXTO else 0
    if nome == "quantidade_faltante":
        return 2 if tipo in TIPOS_NUMERO else 1 if tipo in TIPOS_TEXTO else 0
    return 1


def mapear_por_nomes(campos: dict[str, str]) -> dict[str, str]:
    candidatos: dict[str, list[tuple[int, int, str]]] = {}
    for campo, tipo in campos.items():
        for nome, padrao in PALAVRAS.items():
            if padrao.search(campo):
                # maior afinidade de tipo; empate -> nome mais curto (Material > MaterialText)
                candidatos.setdefault(nome, []).append((_afinidade(nome, tipo), -len(campo), campo))
                break
    return {nome: max(opcoes)[2] for nome, opcoes in candidatos.items() if max(opcoes)[0] > 0}


def _pontos_mensagens(m: dict) -> int:
    return sum(1 for c in ESSENCIAIS if c in m)


def _pontos_lista(m: dict) -> int:
    if "wo" not in m:
        return -1
    return 3 + (2 if "consolidado" in m else 0) + (1 if "mensagem" in m else 0) - (2 if "material" in m else 0)


def caminho_servico(url: str) -> str:
    """Normaliza a URL do serviço para caminho relativo à origem, terminado em '/'."""
    partes = urlsplit(url)
    caminho = partes.path if partes.scheme else url.split("?", 1)[0]
    if not caminho.startswith("/"):
        raise MetadataSemCorrespondencia(f"URL de serviço não utilizável: {url}")
    return caminho if caminho.endswith("/") else caminho + "/"


def configurar(servico: str, metadata_xml: str) -> tuple[dict, list[str]]:
    conjuntos = ler_metadata(metadata_xml)
    mapas = {nome: mapear_por_nomes(campos) for nome, campos in conjuntos.items()}

    mensagens = max(mapas, key=lambda n: (_pontos_mensagens(mapas[n]), -len(n)), default=None)
    if not mensagens or _pontos_mensagens(mapas[mensagens]) < 4:
        raise MetadataSemCorrespondencia("nenhum conjunto com material, fornecedor, quantidade e mensagem")
    m_msg = mapas[mensagens]

    outros = [n for n in mapas if n != mensagens and _pontos_lista(mapas[n]) >= 0]
    lista = max(outros, key=lambda n: _pontos_lista(mapas[n]), default=None)

    relatorio = [f"Serviço de dados: {servico}"]
    if lista is None:
        if "wo" not in m_msg:
            raise MetadataSemCorrespondencia("nenhum conjunto com o campo da WO")
        # As próprias linhas de mensagem já trazem a WO: uma consulta só.
        dados = {"url_lista": f"{servico}{mensagens}?$filter={m_msg['wo']} eq '{{wo}}'",
                 "campo_consolidado_lista": m_msg.get("consolidado", ""),
                 "url_mensagens": None, "campo_mensagens_expandidas": ".", "campos": m_msg}
        relatorio.append(f"Mensagens consultadas direto pela WO em '{mensagens}'.")
        return dados, relatorio

    m_lista = mapas[lista]
    filtros = []
    if "consolidado" in m_msg and "consolidado" in m_lista:
        filtros.append(f"{m_msg['consolidado']} eq '{{linha[{m_lista['consolidado']}]}}'")
    if "wo" in m_msg:
        filtros.append(f"{m_msg['wo']} eq '{{linha[{m_lista['wo']}]}}'")
    if not filtros:
        raise MetadataSemCorrespondencia(f"não há campo que ligue '{lista}' a '{mensagens}'")
    dados = {
        "url_lista": f"{servico}{lista}?$filter={m_lista['wo']} eq '{{wo}}'",
        "campo_consolidado_lista": m_lista.get("consolidado", ""),
        "url_mensagens": f"{servico}{mensagens}?$filter=" + " and ".join(filtros),
        "campo_mensagens_expandidas": None,
        "campos": m_msg,
    }
    relatorio.append(f"Lista: '{lista}' · Mensagens: '{mensagens}'.")
    return dados, relatorio


def configurar_pela_aba(conexao) -> tuple[dict, list[str]]:
    """Pergunta à aba os serviços em uso e tenta cada $metadata até um servir."""
    fontes = conexao.fontes()
    servicos = []
    for f in fontes:
        try:
            s = caminho_servico(f.get("url", ""))
        except MetadataSemCorrespondencia:
            continue
        if s not in servicos:
            servicos.append(s)
    if not servicos:
        raise MetadataSemCorrespondencia("o Monitor de alertas não está aberto na aba")
    motivos = []
    for servico in servicos:
        try:
            r = conexao.get(servico + "$metadata", accept="application/xml")
            if r.status != 200:
                motivos.append(f"{servico}: HTTP {r.status}")
                continue
            return configurar(servico, r.corpo)
        except (MetadataSemCorrespondencia, ET.ParseError) as exc:
            motivos.append(f"{servico}: {exc}")
    raise MetadataSemCorrespondencia("; ".join(motivos))
