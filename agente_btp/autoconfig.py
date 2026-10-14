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

from .campos import ESSENCIAIS, peso_nome, tipo_do_campo

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
    funcoes_v4, imports_v4, funcoes = {}, [], []
    for el in raiz.iter():
        nome_el = _local(el.tag)
        if nome_el == "FunctionImport" and el.get("Name") and not v4:
            metodo = next((v for k, v in el.attrib.items() if _local(k) == "HttpMethod"), "GET")
            funcoes.append({"nome": el.get("Name"), "metodo": metodo.upper(),
                            "retorno": _tipo_simples(el.get("ReturnType") or ""),
                            "params": {q.get("Name"): q.get("Type", "") for q in el
                                       if _local(q.tag) == "Parameter" and q.get("Name")}})
        elif nome_el == "Function" and el.get("Name") and v4 and el.get("IsBound", "false") != "true":
            ret = next((q.get("Type", "") for q in el if _local(q.tag) == "ReturnType"), "")
            funcoes_v4[el.get("Name")] = {"retorno": _tipo_simples(ret), "params": {
                q.get("Name"): q.get("Type", "") for q in el if _local(q.tag) == "Parameter" and q.get("Name")}}
        elif nome_el == "FunctionImport" and el.get("Name") and v4:
            imports_v4.append((el.get("Name"), (el.get("Function") or "").rsplit(".", 1)[-1]))
    for nome, funcao in imports_v4:   # V4: só funções (Function) — Actions nunca são consideradas
        if funcao in funcoes_v4:
            funcoes.append(dict(funcoes_v4[funcao], nome=nome, metodo="GET"))
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
    return {"v4": v4, "conjuntos": conjuntos, "tipos": tipos, "funcoes": funcoes}


def _tipo_simples(tipo: str) -> str:
    return tipo.replace("Collection(", "").rstrip(")").rsplit(".", 1)[-1]


# Funções do serviço: só as que, pelo nome, são de CONSULTA. Qualquer nome que lembre
# ação (reprocessar, baixa, gravar...) é descartado, mesmo sendo GET.
_FUNCAO_LEITURA = re.compile(r"get|ler|busca|consult|list|obter|mensag|detal|item|msg|pend|falt|read|fetch|exib|show", re.I)
_FUNCAO_ACAO = re.compile(r"reproc|baixa|efetu|post|cria|creat|updat|atualiz|delet|apag|remov|exec|aprov|approv|"
                          r"estorn|cancel|envi|send|save|salv|grav|process|lanc|confirm|liber|reset|bloq|modif|"
                          r"alter|change|insert|write|escrev|corrig|ajust|run", re.I)


def funcao_de_leitura(nome: str, metodo: str = "GET") -> bool:
    return metodo == "GET" and bool(_FUNCAO_LEITURA.search(nome)) and not _FUNCAO_ACAO.search(nome)


# Campo que identifica registro (serve para ligar alerta e mensagens).
_IDENTIFICADOR = re.compile(r"(^|_)(id|guid|uuid|key|chave|num|numero|nr|nro|doc|documento|ordem|order|"
                            r"consol\w*|wo|alerta|alert)$|Id$|ID$|Guid$|Num$|Key$", re.I)


def _identificador(campo: str, chaves: list[str]) -> bool:
    tipo = tipo_do_campo(campo)
    if tipo in ESSENCIAIS:
        return False
    return tipo in ("wo", "consolidado") or campo in chaves or bool(_IDENTIFICADOR.search(campo))


def _ligacoes(lista: dict, msgs: dict, nome_lista: str) -> list[tuple[str, str, str]]:
    """Pares (campo nas mensagens, campo no alerta, tipo) que podem ligar os dois conjuntos."""
    pares = []
    for campo, tipo in msgs["props"].items():
        if campo in lista["props"] and _identificador(campo, lista["chaves"]):
            pares.append((campo, campo, tipo))
    prefixos = {lista["tipo"].lower(), nome_lista.lower(), nome_lista.lower().removesuffix("set"),
                nome_lista.lower().rstrip("s")}
    for chave in lista["chaves"]:
        for campo, tipo in msgs["props"].items():
            prefixo = campo[: -len(chave)].rstrip("_").lower() if campo.lower().endswith(chave.lower()) else None
            if prefixo and campo not in lista["props"] and prefixo in prefixos:
                pares.append((campo, chave, lista["props"].get(chave, tipo)))   # ex.: alerta_ID -> ID
    return pares


def _literal_filtro(campo_linha: str, tipo: str, v4: bool) -> str:
    return _literal_chave(campo_linha, tipo, v4)


def _afinidade(nome: str, campo: str, tipo: str) -> int:
    if nome in ("material", "fornecedor", "mensagem", "consolidado", "wo"):
        compativel = tipo in TIPOS_TEXTO
    elif nome == "quantidade_faltante":
        compativel = tipo in TIPOS_NUMERO or tipo in TIPOS_TEXTO
    else:
        compativel = True
    if not compativel:
        return 0
    # tipo compatível = 10; o nome desempata (quantidadeFaltante > quantidade; mensagem > descricao)
    return 10 + peso_nome(nome, campo) + (1 if nome == "quantidade_faltante" and tipo in TIPOS_NUMERO else 0)


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
            # 1) navegação declarada do alerta para as mensagens: pelo link que o próprio
            #    servidor informa na linha, pela URL montada com a chave e por $expand
            pred = _predicado(info["chaves"], info["props"], modelo["v4"])
            for nav, destino in info["destinos"].items():
                if destino != m:
                    continue
                if not modelo["v4"]:
                    saida.append((dict(base, url_mensagens=None, campo_mensagens_link=nav),
                                  [f"Lista '{lista}' → mensagens pelo link '{nav}' de cada linha."]))
                if pred:
                    saida.append((dict(base, url_mensagens=f"{servico}{lista}{pred}/{nav}"),
                                  [f"Lista '{lista}' → mensagens pela navegação '{nav}'."]))
                saida.append((dict(base, url_lista=base["url_lista"] + f"&$expand={nav}", url_mensagens=None,
                                   campo_mensagens_expandidas=nav),
                              [f"Lista '{lista}' com as mensagens expandidas em '{nav}'."]))
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
            # 2b) outros identificadores em comum (mesmo nome, ou chave do alerta: alerta_ID)
            ligacoes = [f"{cm} eq {_literal_filtro(cl, t, modelo['v4'])}"
                        for cm, cl, t in _ligacoes(info, conj[m], lista)]
            grupos = [[x] for x in ligacoes] + ([ligacoes] if len(ligacoes) > 1 else [])
            for grupo in grupos:
                saida.append((dict(base, url_mensagens=f"{servico}{m}?$filter=" + " and ".join(grupo)),
                              [f"Lista '{lista}' → mensagens '{m}' ligadas por {', '.join(grupo)}."]))
        # 3) um conjunto só: as linhas de mensagem já trazem a WO
        if "wo" in m_msg:
            saida.append(({"url_lista": f"{servico}{m}?$filter={m_msg['wo']} eq '{{wo}}'",
                           "campo_consolidado_lista": m_msg.get("consolidado", ""), "url_mensagens": None,
                           "campo_mensagens_expandidas": ".", "campos": m_msg},
                          [f"Mensagens consultadas direto pela WO em '{m}'."]))
    saida.extend(_candidatos_funcoes(servico, modelo, mapas, listas))
    if not saida:
        raise MetadataSemCorrespondencia("nenhum conjunto com WO e com material/fornecedor/quantidade/mensagem")
    for dados, rel in saida:
        rel.insert(0, f"Serviço de dados: {servico}")
    return saida


def _candidatos_funcoes(servico, modelo, mapas, listas) -> list[tuple[dict, list[str]]]:
    """Funções de consulta do serviço que devolvem as mensagens a partir do alerta."""
    saida = []
    for f in modelo["funcoes"]:
        retorno = modelo["tipos"].get(f["retorno"])
        if not retorno or not f["params"] or not funcao_de_leitura(f["nome"], f["metodo"]):
            continue
        m_msg = mapear_por_nomes(retorno["props"])
        if _pontos_mensagens(m_msg) < 3:
            continue
        for lista in listas:
            m_lista, props_lista = mapas[lista], modelo["conjuntos"][lista]["props"]
            ligados = {}
            for param, tipo in f["params"].items():
                alvo = param if param in props_lista else m_lista.get(tipo_do_campo(param) or "")
                if tipo_do_campo(param) not in (None, "wo", "consolidado") and param not in props_lista:
                    alvo = None
                if alvo:
                    ligados[param] = _literal_chave(alvo, tipo, modelo["v4"])
            if len(ligados) != len(f["params"]):
                continue
            if modelo["v4"]:
                url = f"{servico}{f['nome']}(" + ",".join(f"{p}={v}" for p, v in ligados.items()) + ")"
            else:
                url = f"{servico}{f['nome']}?" + "&".join(f"{p}={v}" for p, v in ligados.items())
            saida.append(({"url_lista": f"{servico}{lista}?$filter={m_lista['wo']} eq '{{wo}}'",
                           "campo_consolidado_lista": m_lista.get("consolidado", ""), "url_mensagens": url,
                           "campo_mensagens_expandidas": None, "campos": m_msg},
                          [f"Lista '{lista}' → mensagens pela função de consulta '{f['nome']}'."]))
    return saida


def listas_provaveis(servico: str, modelo: dict) -> list[dict]:
    """Consultas de LISTA (pela WO) do serviço — base para a sondagem pelos dados."""
    saida = []
    for nome, info in modelo["conjuntos"].items():
        m = mapear_por_nomes(info["props"])
        if _pontos_lista(m) >= 0:
            saida.append((_pontos_lista(m), {"url_lista": f"{servico}{nome}?$filter={m['wo']} eq '{{wo}}'",
                                             "campo_consolidado_lista": m.get("consolidado", "")}))
    return [d for _, d in sorted(saida, key=lambda x: -x[0])]


def candidatos_cruzados(modelos: list[tuple[str, dict]]) -> list[tuple[dict, list[str]]]:
    """Lista num serviço e mensagens em OUTRO (a janela 'Mensagens' pode usar serviço próprio)."""
    saida = []
    for servico_l, mod_l in modelos:
        for lista, info in mod_l["conjuntos"].items():
            m_lista = mapear_por_nomes(info["props"])
            if _pontos_lista(m_lista) < 0:
                continue
            for servico_m, mod_m in modelos:
                if servico_m == servico_l:
                    continue
                for m, info_m in mod_m["conjuntos"].items():
                    m_msg = mapear_por_nomes(info_m["props"])
                    if _pontos_mensagens(m_msg) < 3:
                        continue
                    filtros = [f"{m_msg[c]} eq '{{linha[{m_lista[c]}]}}'"
                               for c in ("consolidado", "wo") if c in m_msg and c in m_lista]
                    filtros += [f"{cm} eq {_literal_filtro(cl, t, mod_m['v4'])}"
                                for cm, cl, t in _ligacoes(info, info_m, lista)
                                if cm not in (m_msg.get("consolidado"), m_msg.get("wo"))]
                    for f in filtros:
                        saida.append(({"url_lista": f"{servico_l}{lista}?$filter={m_lista['wo']} eq '{{wo}}'",
                                       "campo_consolidado_lista": m_lista.get("consolidado", ""),
                                       "url_mensagens": f"{servico_m}{m}?$filter={f}",
                                       "campo_mensagens_expandidas": None, "campos": m_msg},
                                      [f"Lista '{lista}' ({servico_l}) → mensagens '{m}' ({servico_m})."]))
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


def candidatos_pela_aba(conexao, diag: dict | None = None,
                        modelos_saida: list | None = None) -> list[tuple[dict, list[str]]]:
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
    todos, modelos = [], []
    for servico in servicos[:15]:
        try:
            r = conexao.get(servico + "$metadata", accept="application/xml")
            if r.status != 200:
                raise MetadataSemCorrespondencia(f"HTTP {r.status}")
            modelos.append((servico, ler_modelo(r.corpo)))
            diag.setdefault("listas", []).extend(listas_provaveis(servico, modelos[-1][1]))
            achados = candidatos(servico, r.corpo)
            todos.extend(achados)
            diag["servicos"].append({"servico": servico, "resultado": f"{len(achados)} candidato(s)"})
        except Exception as exc:  # noqa: BLE001
            diag["servicos"].append({"servico": servico, "resultado": str(exc)[:200]})
    if modelos_saida is not None:
        modelos_saida.extend(modelos)
    if len(modelos) > 1:
        cruzados = candidatos_cruzados(modelos)
        todos.extend(cruzados)
        diag["cruzados"] = len(cruzados)
    if not todos and not diag.get("listas"):
        raise MetadataSemCorrespondencia("nenhum serviço com os dados do Monitor; ver diagnóstico")
    return todos


def configurar_pela_aba(conexao, diag: dict | None = None) -> tuple[dict, list[str]]:
    return candidatos_pela_aba(conexao, diag)[0]


def leitura_por_chave(dados: dict, modelos: list[tuple[str, dict]]) -> dict:
    """Endereço para reler UM item das mensagens pela chave (auditoria independente).

    Ex.: mensagens em /srv/Itens?$filter=... -> {"url_item": "/srv/Itens({linha[ID]})", "chaves_item": ["ID"]}.
    Devolve {} quando o serviço não declara chave para o conjunto (a limitação é registrada).
    """
    if dados.get("url_mensagens"):
        url = dados["url_mensagens"]
    elif dados.get("campo_mensagens_expandidas") == ".":
        url = dados["url_lista"]
    else:
        return {}
    caminho = url.split("?", 1)[0]
    for servico, modelo in modelos:
        if not caminho.startswith(servico):
            continue
        resto = caminho[len(servico):]
        conjuntos = modelo["conjuntos"]
        if "/" in resto:                       # navegação: Alertas({linha[ID]})/itens
            origem, _, nav = resto.rpartition("/")
            destino = conjuntos.get(origem.split("(", 1)[0], {}).get("destinos", {}).get(nav)
        else:
            destino = resto
        info = conjuntos.get(destino or "")
        if not info or not info["chaves"]:
            return {}
        predicado = _predicado(info["chaves"], info["props"], modelo["v4"])
        return {"url_item": f"{servico}{destino}{predicado}", "chaves_item": list(info["chaves"])}
    return {}
