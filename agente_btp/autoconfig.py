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

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from .campos import ESSENCIAIS, descritivo, tipo_do_campo

TIPOS_TEXTO = {"Edm.String"}
TIPOS_NUMERO = {"Edm.Decimal", "Edm.Int16", "Edm.Int32", "Edm.Int64", "Edm.Double", "Edm.Single", "Edm.Byte"}


class MetadataSemCorrespondencia(Exception):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def ler_metadata(xml: str) -> dict[str, dict[str, str]]:
    """{conjunto: {campo: tipo}} a partir do EDMX (OData V2 ou V4)."""
    return {nome: c["props"] for nome, c in ler_modelo(xml)["conjuntos"].items()}


def ler_modelo(xml: str) -> dict:
    """Conjuntos com campos, chaves e navegações (OData V2 e V4)."""
    raiz = ET.fromstring(xml)
    v4 = (raiz.get("Version") or "").startswith("4")
    tipos, assoc = {}, {}
    for el in raiz.iter():
        nome_el = _local(el.tag)
        if nome_el == "EntityType" and el.get("Name"):
            props = {p.get("Name"): p.get("Type", "") for p in el if _local(p.tag) == "Property" and p.get("Name")}
            chaves = [r.get("Name") for k in el if _local(k.tag) == "Key" for r in k if r.get("Name")]
            navs = {}
            for n in el:
                if _local(n.tag) != "NavigationProperty" or not n.get("Name"):
                    continue
                if n.get("Type"):   # V4: Type="Collection(NS.Item)" ou "NS.Item"
                    navs[n.get("Name")] = ("tipo", n.get("Type").replace("Collection(", "").rstrip(")").rsplit(".", 1)[-1])
                else:               # V2: Relationship + ToRole -> Association
                    navs[n.get("Name")] = ("assoc", (n.get("Relationship", "").rsplit(".", 1)[-1], n.get("ToRole")))
            tipos[el.get("Name")] = {"props": props, "chaves": chaves, "navs": navs}
        elif nome_el == "Association" and el.get("Name"):
            assoc[el.get("Name")] = {e.get("Role"): (e.get("Type") or "").rsplit(".", 1)[-1]
                                     for e in el if _local(e.tag) == "End"}
    conjuntos, set_do_tipo = {}, {}
    for el in raiz.iter():
        if _local(el.tag) == "EntitySet" and el.get("Name"):
            tipo = (el.get("EntityType") or "").rsplit(".", 1)[-1]
            set_do_tipo.setdefault(tipo, el.get("Name"))
            info = tipos.get(tipo, {"props": {}, "chaves": [], "navs": {}})
            conjuntos[el.get("Name")] = {"tipo": tipo, **info}
    for c in conjuntos.values():
        destinos = {}
        for nav, (modo, alvo) in c["navs"].items():
            tipo_alvo = alvo if modo == "tipo" else assoc.get(alvo[0], {}).get(alvo[1])
            if tipo_alvo in set_do_tipo:
                destinos[nav] = set_do_tipo[tipo_alvo]
        c["destinos"] = destinos
    return {"v4": v4, "conjuntos": conjuntos}


def _afinidade(nome: str, campo: str, tipo: str) -> int:
    base = 0 if descritivo(campo) and nome in ("material", "fornecedor") else 1
    if nome in ("material", "fornecedor", "mensagem", "consolidado", "wo"):
        return (2 if tipo in TIPOS_TEXTO else 0) + base
    if nome == "quantidade_faltante":
        return 2 if tipo in TIPOS_NUMERO else 1 if tipo in TIPOS_TEXTO else 0
    return 1


def mapear_por_nomes(campos: dict[str, str]) -> dict[str, str]:
    candidatos: dict[str, list[tuple[int, int, str]]] = {}
    for campo, tipo in campos.items():
        nome = tipo_do_campo(campo)
        if nome:
            # maior afinidade de tipo; empate -> nome mais curto (Material > MaterialText)
            candidatos.setdefault(nome, []).append((_afinidade(nome, campo, tipo), -len(campo), campo))
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


def _literal_chave(campo: str, tipo: str, v4: bool) -> str:
    marcador = "{linha[" + campo + "]}"
    if tipo == "Edm.Guid":
        return marcador if v4 else f"guid'{marcador}'"
    if tipo in TIPOS_NUMERO:
        return marcador
    return f"'{marcador}'"


def _predicado(chaves: list[str], props: dict[str, str], v4: bool) -> str | None:
    if not chaves:
        return None
    if len(chaves) == 1:
        return "(" + _literal_chave(chaves[0], props.get(chaves[0], ""), v4) + ")"
    return "(" + ",".join(f"{k}={_literal_chave(k, props.get(k, ''), v4)}" for k in chaves) + ")"


def candidatos(servico: str, metadata_xml: str) -> list[tuple[dict, list[str]]]:
    """Todas as configurações plausíveis, da mais provável para a menos provável.

    Nenhuma é aceita às cegas: o agente testa cada uma com as WOs do usuário.
    """
    modelo = ler_modelo(metadata_xml)
    conj = modelo["conjuntos"]
    mapas = {n: mapear_por_nomes(c["props"]) for n, c in conj.items()}
    msgs = sorted((n for n in mapas if _pontos_mensagens(mapas[n]) >= 3),
                  key=lambda n: (-_pontos_mensagens(mapas[n]), len(n)))
    listas = sorted((n for n in mapas if _pontos_lista(mapas[n]) >= 0),
                    key=lambda n: (-_pontos_lista(mapas[n]), len(n)))
    saida = []
    for m in msgs:
        m_msg = mapas[m]
        for lista in listas:
            if lista == m:
                continue
            m_lista, info = mapas[lista], conj[lista]
            base = {"url_lista": f"{servico}{lista}?$filter={m_lista['wo']} eq '{{wo}}'",
                    "campo_consolidado_lista": m_lista.get("consolidado", ""),
                    "campo_mensagens_expandidas": None, "campos": m_msg}
            # 1) navegação declarada do alerta para as mensagens
            pred = _predicado(info["chaves"], info["props"], modelo["v4"])
            for nav, destino in info["destinos"].items():
                if destino == m and pred:
                    saida.append((dict(base, url_mensagens=f"{servico}{lista}{pred}/{nav}"),
                                  [f"Lista '{lista}' → mensagens pela navegação '{nav}'."]))
            # 2) filtro pelos campos em comum (Consolidado e/ou WO)
            filtros = []
            if "consolidado" in m_msg and "consolidado" in m_lista:
                filtros.append(f"{m_msg['consolidado']} eq '{{linha[{m_lista['consolidado']}]}}'")
            if "wo" in m_msg:
                filtros.append(f"{m_msg['wo']} eq '{{linha[{m_lista['wo']}]}}'")
            for grupo in ([filtros] if len(filtros) < 2 else [filtros, filtros[:1], filtros[1:]]):
                if grupo:
                    saida.append((dict(base, url_mensagens=f"{servico}{m}?$filter=" + " and ".join(grupo)),
                                  [f"Lista '{lista}' → mensagens '{m}' filtradas por campos em comum."]))
        # 3) um conjunto só: as linhas de mensagem já trazem a WO
        if "wo" in m_msg:
            saida.append(({"url_lista": f"{servico}{m}?$filter={m_msg['wo']} eq '{{wo}}'",
                           "campo_consolidado_lista": m_msg.get("consolidado", ""), "url_mensagens": None,
                           "campo_mensagens_expandidas": ".", "campos": m_msg},
                          [f"Mensagens consultadas direto pela WO em '{m}'."]))
    if not saida:
        raise MetadataSemCorrespondencia("nenhum conjunto com WO e com material/fornecedor/quantidade/mensagem")
    for dados, rel in saida:
        rel.insert(0, f"Serviço de dados: {servico}")
    return saida


def configurar(servico: str, metadata_xml: str) -> tuple[dict, list[str]]:
    """A configuração mais provável (sem teste). Use candidatos() + validação no agente."""
    return candidatos(servico, metadata_xml)[0]


_RAIZES = (
    re.compile(r"^(.*?/sap/opu/odata4?/[^/?]+/[^/?]+/)"),   # Gateway V2: /sap/opu/odata/sap/ZSRV/
    re.compile(r"^(.*?/odata/v[24]/[^/?]+/)"),              # CAP: /odata/v4/servico/
    re.compile(r"^(.*/)\$(?:batch|metadata)"),              # qualquer serviço: tudo antes de $batch
)


def raiz_servico(url: str) -> str | None:
    """Raiz do serviço OData a partir de qualquer URL de consulta a ele."""
    caminho = urlsplit(url).path if "://" in url else url.split("?", 1)[0]
    for padrao in _RAIZES:
        m = padrao.match(caminho)
        if m:
            return m.group(1)
    return None


def candidatos_pela_aba(conexao, diag: dict | None = None) -> list[tuple[dict, list[str]]]:
    """Pergunta à aba os serviços em uso e reúne os candidatos de todos os $metadata."""
    diag = diag if diag is not None else {}
    fontes = conexao.fontes()
    diag["fontes"] = fontes[:50]
    servicos: list[str] = []
    for f in fontes:
        for chave in ("url", "url_original"):
            if f.get(chave):
                try:
                    s = caminho_servico(f[chave])
                except MetadataSemCorrespondencia:
                    s = None
                if s and s not in servicos:
                    servicos.append(s)
        if f.get("recurso"):
            s = raiz_servico(f["recurso"])
            if s and s not in servicos:
                servicos.append(s)
    diag["servicos"] = []
    if not servicos:
        raise MetadataSemCorrespondencia("nenhum serviço de dados encontrado na aba (o Monitor está aberto?)")
    todos = []
    for servico in servicos[:15]:
        try:
            r = conexao.get(servico + "$metadata", accept="application/xml")
            if r.status != 200:
                raise MetadataSemCorrespondencia(f"HTTP {r.status}")
            achados = candidatos(servico, r.corpo)
            todos.extend(achados)
            diag["servicos"].append({"servico": servico, "resultado": f"{len(achados)} candidato(s)"})
        except Exception as exc:  # noqa: BLE001
            diag["servicos"].append({"servico": servico, "resultado": str(exc)[:200]})
    if not todos:
        raise MetadataSemCorrespondencia("nenhum serviço com os dados do Monitor; ver diagnóstico")
    return todos


def configurar_pela_aba(conexao, diag: dict | None = None) -> tuple[dict, list[str]]:
    return candidatos_pela_aba(conexao, diag)[0]
