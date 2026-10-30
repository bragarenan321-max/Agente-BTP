import json

import pytest

from agente_btp import config as cfg
from agente_btp.analisador import AnaliseFalhou, analisar, mapear_campos
from agente_btp.cliente import ClienteBTP, Resposta
from agente_btp.processador import processar_wo

WO = "88412|126579742"
SRV = "/comclaromonitor/~1700~/sap/opu/odata/sap/ZMONITOR_SRV/"
ALERTA = {"__metadata": {"uri": "x"}, "Wo": WO, "ConsolidadoNum": "105012-20260916-mEeF+Ow/Dh",
          "Fornecedor": "137628", "Mensagem": "Erro durante baixa de estoque"}
MSG = {"Wo": WO, "Matnr": "000000000022026169", "MaterialText": "CABO COAXIAL",
       "Lifnr": "0000137628", "QtdFaltante": "2.000", "Mensagem": "Estoque indisponível"}


def batch_v2(gets, respostas):
    req = "".join(f"--batch_1\r\nContent-Type: application/http\r\n\r\nGET {g} HTTP/1.1\r\n"
                  f"Accept: application/json\r\n\r\n\r\n" for g in gets) + "--batch_1--"
    resp = "".join(f"--x\r\nContent-Type: application/http\r\n\r\nHTTP/1.1 200 OK\r\n"
                   f"Content-Type: application/json\r\n\r\n{json.dumps(r)}\r\n" for r in respostas) + "--x--"
    return {"metodo": "POST", "url": "https://h" + SRV + "$batch", "status": 202,
            "content_type": "multipart/mixed", "corpo_requisicao": req, "corpo_resposta": resp}


def captura_v2():
    wo_enc = "88412%7C126579742"
    lista = batch_v2([f"AlertaSet?$skip=0&$top=20&$filter=Wo%20eq%20'{wo_enc}'&$inlinecount=allpages"],
                     [{"d": {"results": [ALERTA], "__count": "1"}}])
    msgs = batch_v2(["MensagemSet?$filter=ConsolidadoNum%20eq%20'105012-20260916-mEeF%2BOw%2FDh'"],
                    [{"d": {"results": [MSG]}}])
    ruido = {"metodo": "GET", "url": "https://h" + SRV + "$metadata", "status": 200,
             "content_type": "application/xml", "corpo_requisicao": "", "corpo_resposta": "<x/>"}
    return {"requisicoes": [ruido, lista, msgs]}


def test_analisa_batch_v2_e_gera_modelos():
    dados, relatorio = analisar(captura_v2())
    assert dados["url_lista"] == SRV + "AlertaSet?$filter=Wo eq '{wo}'"
    assert dados["url_mensagens"] == SRV + "MensagemSet?$filter=ConsolidadoNum eq '{linha[ConsolidadoNum]}'"
    assert dados["campos"]["material"] == "Matnr"          # não MaterialText
    assert dados["campos"]["fornecedor"] == "Lifnr"
    assert dados["campos"]["quantidade_faltante"] == "QtdFaltante"
    assert dados["campos"]["wo"] == "Wo"
    assert dados["campo_consolidado_lista"] == "ConsolidadoNum"
    assert any(WO in linha for linha in relatorio)


def test_config_gerada_funciona_de_ponta_a_ponta():
    dados, _ = analisar(captura_v2())
    urls = []

    def transporte(url):
        urls.append(url)
        corpo = {"d": {"results": [ALERTA]}} if "AlertaSet" in url else {"d": {"results": [MSG]}}
        return Resposta(200, "application/json", json.dumps(corpo))

    r = processar_wo(ClienteBTP(cfg.a_partir_de_dict(dados), transporte), WO)
    assert r.registros == 1
    linha = r.linhas[0]
    assert (linha.consolidado, linha.material, linha.fornecedor, linha.quantidade_faltante) == (
        "105012-20260916-mEeF+Ow/Dh", "000000000022026169", "0000137628", 2)
    assert urls[0] == SRV + "AlertaSet?$filter=Wo%20eq%20'88412%7C126579742'"
    assert urls[1] == SRV + "MensagemSet?$filter=ConsolidadoNum%20eq%20'105012-20260916-mEeF%2BOw%2FDh'"


def test_analisa_v4_get_direto_com_navegacao():
    lista = {"metodo": "GET", "status": 200, "content_type": "application/json",
             "url": "https://h/odata/v4/monitor/Alertas?$filter=wo%20eq%20'88412%7C126579742'&$top=30",
             "corpo_resposta": json.dumps({"value": [{"ID": "a1b2c3d4", "wo": WO, "consolidado": "C1"}]})}
    msgs = {"metodo": "GET", "status": 200, "content_type": "application/json",
            "url": "https://h/odata/v4/monitor/Alertas(ID='a1b2c3d4')/mensagens",
            "corpo_resposta": json.dumps({"value": [{"material": "000000000022026169",
                                                     "fornecedor": "0000137628",
                                                     "quantidade": 2, "mensagem": "Estoque indisponível"}]})}
    dados, _ = analisar({"requisicoes": [lista, msgs]})
    assert dados["url_lista"] == "/odata/v4/monitor/Alertas?$filter=wo eq '{wo}'"
    assert dados["url_mensagens"] == "/odata/v4/monitor/Alertas(ID='{linha[ID]}')/mensagens"


def test_mensagens_expandidas_na_lista():
    alerta = dict(ALERTA, to_Mensagens={"results": [MSG]})
    req = batch_v2(["AlertaSet?$filter=Wo%20eq%20'88412%7C126579742'&$expand=to_Mensagens"],
                   [{"d": {"results": [alerta]}}])
    dados, _ = analisar({"requisicoes": [req]})
    assert dados["campo_mensagens_expandidas"] == "to_Mensagens"
    assert dados["url_lista"].endswith("&$expand=to_Mensagens")


def test_falha_sem_consulta_de_mensagens():
    req = batch_v2(["AlertaSet?$filter=Wo%20eq%20'88412%7C126579742'"], [{"d": {"results": [ALERTA]}}])
    with pytest.raises(AnaliseFalhou, match="Mensagens"):
        analisar({"requisicoes": [req]})


def test_falha_captura_vazia():
    with pytest.raises(AnaliseFalhou):
        analisar({"requisicoes": []})


def test_mapear_campos_prefere_valor_compativel():
    campos = mapear_campos({"MaterialText": "CABO", "Material": "0001"}, WO)
    assert campos["material"] == "Material"


# ---------------------------------------------------------------- preparação pelo $metadata
from agente_btp.autoconfig import MetadataSemCorrespondencia, configurar, ler_metadata  # noqa: E402

EDMX = """<?xml version="1.0"?><edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
<edmx:DataServices><Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="S">
<EntityType Name="Alerta"><Property Name="Wo" Type="Edm.String"/><Property Name="ConsolidadoNum" Type="Edm.String"/>
 <Property Name="Workcenter" Type="Edm.String"/><Property Name="Mensagem" Type="Edm.String"/></EntityType>
<EntityType Name="Msg"><Property Name="Wo" Type="Edm.String"/><Property Name="ConsolidadoNum" Type="Edm.String"/>
 <Property Name="MaterialText" Type="Edm.String"/><Property Name="Matnr" Type="Edm.String"/>
 <Property Name="Lifnr" Type="Edm.String"/><Property Name="QtdFaltante" Type="Edm.Decimal"/>
 <Property Name="Mensagem" Type="Edm.String"/></EntityType>
<EntityContainer Name="C"><EntitySet Name="Alertas" EntityType="S.Alerta"/><EntitySet Name="Mensagens" EntityType="S.Msg"/>
</EntityContainer></Schema></edmx:DataServices></edmx:Edmx>"""


def test_ler_metadata_v4():
    conjuntos = ler_metadata(EDMX)
    assert set(conjuntos) == {"Alertas", "Mensagens"}
    assert conjuntos["Mensagens"]["QtdFaltante"] == "Edm.Decimal"


def test_configurar_pelo_metadata():
    dados, _ = configurar("/srv/", EDMX)
    assert dados["url_lista"] == "/srv/Alertas?$filter=Wo eq '{wo}'"
    assert dados["url_mensagens"] == ("/srv/Mensagens?$filter=ConsolidadoNum eq '{linha[ConsolidadoNum]}'"
                                      " and Wo eq '{linha[Wo]}'")
    assert dados["campos"]["material"] == "Matnr"          # não MaterialText
    assert dados["campos"]["quantidade_faltante"] == "QtdFaltante"
    cfg.a_partir_de_dict(dados)                             # configuração válida


def test_metadata_sem_mensagens_falha():
    xml = EDMX.replace('Name="Lifnr"', 'Name="Outro"').replace('Name="QtdFaltante"', 'Name="Valor"')
    with pytest.raises(MetadataSemCorrespondencia):
        configurar("/srv/", xml)


def test_metadata_um_conjunto_so():
    xml = EDMX.replace('<EntitySet Name="Alertas" EntityType="S.Alerta"/>', "")
    dados, _ = configurar("/srv/", xml)
    assert dados["url_lista"] == "/srv/Mensagens?$filter=Wo eq '{wo}'"
    assert dados["campo_mensagens_expandidas"] == "."


def test_raiz_servico():
    from agente_btp.autoconfig import raiz_servico
    assert raiz_servico("/app/~1~/sap/opu/odata/sap/ZMON_SRV/AlertaSet?$top=1") == "/app/~1~/sap/opu/odata/sap/ZMON_SRV/"
    assert raiz_servico("/odata/v4/monitor/Alertas?$filter=x") == "/odata/v4/monitor/"
    assert raiz_servico("/qualquer/srv/$batch") == "/qualquer/srv/"
    assert raiz_servico("/nada/aqui") is None
