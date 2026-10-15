"""Serviço falso que imita o Monitor real (CAP/OData V4, nomes do diagnóstico de 14/10/2026).

Usado pelos testes de unidade e pelo teste de ponta a ponta no navegador.
"""
import json
import re
from urllib.parse import unquote

from agente_btp.cliente import Resposta

WO = "06162|231715939"
CONSOL = "105012-20260916-mEeF+Ow/Dh"


def ok(corpo):
    return Resposta(200, "application/json", json.dumps(corpo))


SRV = "/MonitorPendenciashtml5.comsapbuildstandardmonitorDePendencias/work-order/"
NOMES_REAIS = """OrdensDoConsolidado WoListConsolidado ServiceOrder RetornoDaBaixa
ConsolidadoRevisadoView ConsolidadoRevisado OrdensDoConsolidadoExpandido OrdemDeServicoDescr OrdemDeServicoDescrTOA
ErrosDoTecnicoExpandido TimestampInputWO IndicadoresDePerformance StatusFinalizaWO WorkOrder BaixaPendencias
StatusWO StatusWOTOA WorkOrderLancadasTOA TotalItems TipoWoHelp WorkOrderHelp CabecalhoWO IntegracaoPendenteView
OrdensBOM RelatorioDeErros SelectErros DesviosDaBOM BomParaWorkOrder MotorView ProvisaoBaixa SeriaisWO""".split()


def servico_real(ligado=False, retorno=False):
    """CAP/OData V4 como o do Monitor real: muitos conjuntos, iscas com material/quantidade."""
    tipos, conjuntos = [], []

    def ent(nome, props, chave="ID"):
        p = "".join(f'<Property Name="{n}" Type="{t}"/>' for n, t in props)
        tipos.append(f'<EntityType Name="{nome}"><Key><PropertyRef Name="{chave}"/></Key>{p}</EntityType>')
        conjuntos.append(f'<EntitySet Name="{nome}" EntityType="S.{nome}"/>')
    S, G, D = "Edm.String", "Edm.Guid", "Edm.Decimal"
    ent("Pendencias", [("ID", G), ("workOrderID", S), ("consolidID", S), ("status", S), ("mensagem", S)])
    ent("ErrosMaterial", [("ID", G), ("consolidID" if ligado else "workOrderID_workOrderID", S),
                          ("material", S), ("descricao", S),
                          ("fornecedor", S), ("quantidade", D), ("quantidadeFaltante", D), ("mensagem", S)])
    ent("MaterialConsolid", [("ID", G), ("workOrderID_workOrderID", S), ("material", S), ("fornecedor", S),
                             ("quantidade", D)])
    ent("LogMaterialConsolid", [("ID", G), ("workOrderID", S), ("material", S), ("fornecedor", S),
                                ("quantidade", D), ("mensagem", S)])
    # Isca igual à tabela que o agente escolheu no BTP real (15/10): material de 8 dígitos,
    # fornecedor fixo, quantidade 0 e "mensagem" = tipo do erro ("estoque"/"bom").
    ent("MateriaisLancadosStatus", [("ID", G), ("workOrderID", S), ("material", S), ("fornecedor", S),
                                    ("quantidade", D), ("quantidadeLancada", D), ("tipoErro", S)])
    # A tabela da janela "Mensagens" (confirmada no BTP real): último retorno da baixa, com
    # linhas "Ok" (status O) e linhas com erro (status E). A janela mostra só status E.
    if retorno:
        ent("UltimoRetornoView", [("consolidID", S), ("workOrderID", S), ("status", S), ("material", S),
                                  ("fornecedorID", S), ("quantidade", S), ("quantidadeDoc", S),
                                  ("mensagem", S), ("zeile", S), ("aplicacao", S), ("contador", "Edm.Int32")],
            chave="zeile")
    ent("LimpaWorkOrder", [("ID", G), ("workOrderID", S)])
    for nome in NOMES_REAIS:
        ent(nome, [("ID", G), ("workOrderID", S), ("tipoWo", S), ("descricao", S)])
    xml = ('<?xml version="1.0"?><edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">'
           '<edmx:DataServices><Schema Namespace="S" xmlns="http://docs.oasis-open.org/odata/ns/edm">'
           + "".join(tipos) + '<EntityContainer Name="C">' + "".join(conjuntos)
           + "</EntityContainer></Schema></edmx:DataServices></edmx:Edmx>")
    itens = {WO: [{"ID": "g1", "workOrderID_workOrderID": WO, "material": "000000000022066463",
                   "descricao": "CABO", "fornecedor": "0000138480", "quantidade": 3, "quantidadeFaltante": 2,
                   "mensagem": "Estoque indisponível"}]}
    urls = []

    def get(url, accept=None, timeout=None):
        urls.append(url)
        c = unquote(url)
        if not c.startswith(SRV):
            return Resposta(404, "application/json", "{}")
        rel = c[len(SRV):]
        if rel == "$metadata":
            return Resposta(200, "application/xml", xml)
        conjunto, _, q = rel.partition("?")
        m = re.search(r"(\w+) eq '([^']*)'", q)
        campo, valor = (m.group(1), m.group(2)) if m else ("", "")
        if conjunto.startswith("ErrosMaterial(") and not q:   # releitura do item pela chave
            achado = [i for i in itens[WO] if f"({i['ID']})" in conjunto or f"('{i['ID']}')" in conjunto]
            return ok(dict(achado[0], **({"consolidID": CONSOL} if ligado else {}))) if achado else \
                Resposta(404, "application/json", '{"error":{"message":"nao existe"}}')
        if conjunto == "ErrosMaterial" and campo == "workOrderID_workOrderID" and not ligado:
            return ok({"value": itens.get(valor, [])})
        if conjunto == "ErrosMaterial" and campo == "consolidID" and ligado:
            return ok({"value": [dict(i, consolidID=valor) for i in itens[WO]] if valor == CONSOL else []})
        if conjunto == "MaterialConsolid" and campo == "workOrderID_workOrderID" and valor in itens:
            return ok({"value": [{"ID": "m1", "workOrderID_workOrderID": valor, "material": "000000000022066463",
                                  "fornecedor": "0000138480", "quantidade": 5}]})
        if conjunto == "LogMaterialConsolid" and valor in itens:
            return ok({"value": [{"ID": "l1", "workOrderID": valor, "material": "000000000022066463",
                                  "fornecedor": "0000138480", "quantidade": 5, "mensagem": "Material alterado"}]})
        if conjunto == "Pendencias" and valor in itens:
            return ok({"value": [{"ID": "p1", "workOrderID": valor, "consolidID": CONSOL, "status": "Pendente",
                                  "mensagem": "Erro durante baixa de estoque. Clique para mais detalhes"}]})
        if conjunto == "MateriaisLancadosStatus" and valor in itens:
            return ok({"value": [{"ID": "d1", "workOrderID": valor, "material": "22065513", "fornecedor": "148963",
                                  "quantidade": 0, "quantidadeLancada": 0, "tipoErro": "estoque"},
                                 {"ID": "d2", "workOrderID": valor, "material": "22057657", "fornecedor": "148963",
                                  "quantidade": 0, "quantidadeLancada": 0, "tipoErro": "bom"}]})
        if conjunto == "UltimoRetornoView" and retorno:
            conds = dict(re.findall(r"(\w+) eq '([^']*)'", q))
            linhas = [
                {"zeile": "0010", "consolidID": CONSOL, "workOrderID": WO, "status": "O", "aplicacao": "OPEX",
                 "material": "000000000022061736", "fornecedorID": "0000148963", "quantidade": "6.8e+1",
                 "quantidadeDoc": "6.8e+1", "mensagem": "Ok", "contador": 2},
                {"zeile": "0020", "consolidID": CONSOL, "workOrderID": WO, "status": "E", "aplicacao": "OPEX",
                 "material": "000000000022056366", "fornecedorID": "0000148963", "quantidade": "1e+0",
                 "quantidadeDoc": "0e+0", "mensagem": "Estoque indisponível", "contador": 2},
                {"zeile": "0030", "consolidID": CONSOL, "workOrderID": WO, "status": "E", "aplicacao": "OPEX",
                 "material": "000000000022060284", "fornecedorID": "0000148963", "quantidade": "1e+0",
                 "quantidadeDoc": "0e+0", "mensagem": "Estoque indisponível", "contador": 2}]
            return ok({"value": [l for l in linhas if all(str(l.get(k)) == v for k, v in conds.items())]})
        if conjunto == "LimpaWorkOrder":
            raise AssertionError("conjunto com nome de ação não pode ser consultado")
        return ok({"value": [{"ID": "x", "workOrderID": valor, "tipoWo": "INST", "descricao": "Instalação"}]
                   if campo == "workOrderID" and valor in itens else []})
    return get, urls
