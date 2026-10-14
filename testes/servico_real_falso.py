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
NOMES_REAIS = """OrdensDoConsolidado WoListConsolidado ServiceOrder RetornoDaBaixa UltimoRetornoView
ConsolidadoRevisadoView ConsolidadoRevisado OrdensDoConsolidadoExpandido OrdemDeServicoDescr OrdemDeServicoDescrTOA
ErrosDoTecnicoExpandido TimestampInputWO IndicadoresDePerformance StatusFinalizaWO WorkOrder BaixaPendencias
StatusWO StatusWOTOA WorkOrderLancadasTOA TotalItems TipoWoHelp WorkOrderHelp CabecalhoWO IntegracaoPendenteView
OrdensBOM RelatorioDeErros SelectErros DesviosDaBOM BomParaWorkOrder MotorView ProvisaoBaixa SeriaisWO""".split()


def servico_real(ligado=False):
    """CAP/OData V4 como o do Monitor real: muitos conjuntos, iscas com material/quantidade."""
    tipos, conjuntos = [], []

    def ent(nome, props, chave="ID"):
        p = "".join(f'<Property Name="{n}" Type="{t}"/>' for n, t in props)
        tipos.append(f'<EntityType Name="{nome}"><Key><PropertyRef Name="{chave}"/></Key>{p}</EntityType>')
        conjuntos.append(f'<EntitySet Name="{nome}" EntityType="S.{nome}"/>')
    S, G, D = "Edm.String", "Edm.Guid", "Edm.Decimal"
    ent("Pendencias", [("ID", G), ("workOrderID", S), ("consolidID", S), ("status", S)])
    ent("ErrosMaterial", [("ID", G), ("consolidID" if ligado else "workOrderID_workOrderID", S),
                          ("material", S), ("descricao", S),
                          ("fornecedor", S), ("quantidadeFaltante", D), ("mensagem", S)])
    ent("MaterialConsolid", [("ID", G), ("workOrderID_workOrderID", S), ("material", S), ("fornecedor", S),
                             ("quantidade", D)])
    ent("LogMaterialConsolid", [("ID", G), ("workOrderID", S), ("material", S), ("fornecedor", S),
                                ("quantidade", D), ("mensagem", S)])
    ent("LimpaWorkOrder", [("ID", G), ("workOrderID", S)])
    for nome in NOMES_REAIS:
        ent(nome, [("ID", G), ("workOrderID", S), ("tipoWo", S), ("descricao", S)])
    xml = ('<?xml version="1.0"?><edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">'
           '<edmx:DataServices><Schema Namespace="S" xmlns="http://docs.oasis-open.org/odata/ns/edm">'
           + "".join(tipos) + '<EntityContainer Name="C">' + "".join(conjuntos)
           + "</EntityContainer></Schema></edmx:DataServices></edmx:Edmx>")
    itens = {WO: [{"ID": "g1", "workOrderID_workOrderID": WO, "material": "000000000022066463",
                   "descricao": "CABO", "fornecedor": "0000138480", "quantidadeFaltante": 1,
                   "mensagem": "Estoque indisponível"}]}
    urls = []

    def get(url, accept=None):
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
            return ok({"value": [{"ID": "p1", "workOrderID": valor, "consolidID": CONSOL, "status": "Pendente"}]})
        if conjunto == "LimpaWorkOrder":
            raise AssertionError("conjunto com nome de ação não pode ser consultado")
        return ok({"value": [{"ID": "x", "workOrderID": valor, "tipoWo": "INST", "descricao": "Instalação"}]
                   if campo == "workOrderID" and valor in itens else []})
    return get, urls
