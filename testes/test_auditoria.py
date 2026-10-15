"""Fidelidade dos dados, auditoria por WO, Excel e configurações.

Os transportes falsos abaixo devolvem respostas que MUDAM a cada consulta quando o teste
precisa simular o BTP mudando (ou errando) entre uma leitura e outra.
"""
import json
import time
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from agente_btp import config as cfg
from agente_btp import preferencias
from agente_btp.analisador import mapear_campos
from agente_btp.autoconfig import leitura_por_chave, ler_modelo, mapear_por_nomes
from agente_btp.cliente import ClienteBTP, Resposta
from agente_btp.exportacao import ABAS, gerar_planilha, verificar_planilha
from agente_btp.modelos import Auditoria, Status
from agente_btp.preferencias import Preferencias
from agente_btp.processador import processar_lote, processar_wo

WO = "06162|231715939"
WO2 = "02235|230771207"


def item(**extra):
    base = {"ID": "i1", "workOrderID": WO, "material": "000000000022066463", "fornecedor": "0000138480",
            "quantidadeFaltante": 2, "mensagem": "Estoque indisponível"}
    base.update(extra)
    return base


def config_conjunto_unico(**extra):
    """Itens consultados direto pela WO (como no Monitor real)."""
    return cfg.a_partir_de_dict(dict({
        "url_lista": "/srv/Itens?$filter=workOrderID eq '{wo}'", "campo_consolidado_lista": "",
        "url_mensagens": None, "campo_mensagens_expandidas": ".",
        "campos": {"wo": "workOrderID", "material": "material", "fornecedor": "fornecedor",
                   "quantidade_faltante": "quantidadeFaltante", "mensagem": "mensagem"},
        "url_item": "/srv/Itens({linha[ID]})", "chaves_item": ["ID"]}, **extra))


class BTP:
    """Cada chamada a /srv/Itens?... devolve a próxima resposta da lista (a última se repete)."""

    def __init__(self, respostas, por_chave=None, falhas=0, atraso=0.0):
        self.respostas, self.por_chave, self.falhas, self.atraso = respostas, por_chave, falhas, atraso
        self.urls, self.n = [], 0

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        if self.atraso:
            time.sleep(self.atraso)
        if self.falhas:
            self.falhas -= 1
            return Resposta(503, "text/plain", "")
        if "/srv/Itens(" in url:
            r = self.por_chave(url) if self.por_chave else None
            return Resposta(404, "application/json", '{"error":{"message":"x"}}') if r is None else \
                Resposta(200, "application/json", json.dumps(r))
        regs = self.respostas[min(self.n, len(self.respostas) - 1)]
        self.n += 1
        return Resposta(200, "application/json", json.dumps({"value": regs}))


RAPIDO = Preferencias(intervalo_auditorias_seg=0)


# ============================================================ 1. a divergência 2 x 3 (causa)
def test_quantidade_faltante_vence_quantidade_total():
    """Causa da divergência 2 (BTP) x 3 (Excel): com 'quantidade' (total) e 'quantidadeFaltante' no
    mesmo registro, o desempate antigo escolhia o nome mais curto — a quantidade TOTAL."""
    reg = item(quantidade=3)
    assert mapear_campos(reg, WO)["quantidade_faltante"] == "quantidadeFaltante"
    tipos = {k: "Edm.Decimal" if "quant" in k else "Edm.String" for k in reg}
    assert mapear_por_nomes(tipos)["quantidade_faltante"] == "quantidadeFaltante"
    assert mapear_campos({"qtdSolicitada": 3, "qtdFalta": 2, "material": "1"}, WO)["quantidade_faltante"] == "qtdFalta"


def test_quantidade_extraida_e_a_faltante_e_a_total_fica_na_auditoria():
    r = processar_wo(ClienteBTP(config_conjunto_unico(), BTP([[item(quantidade=3)]],
                                                              por_chave=lambda u: item(quantidade=3))), WO, RAPIDO)
    [l] = r.linhas
    assert l.quantidade_faltante == 2 and l.auditoria is Auditoria.VALIDADO
    assert "quantidade=3" in l.observacao        # a alternativa fica registrada, não some


def test_nome_do_campo_vence_formato_parecido():
    """Pedido de compra com 10 dígitos não pode virar 'fornecedor' quando existe o campo fornecedor."""
    reg = {"material": "000000000022066463", "fornecedor": "138480", "pedidoCompra": "4500012345",
           "qtdFalta": 1, "mensagem": "x"}
    assert mapear_campos(reg, WO)["fornecedor"] == "fornecedor"


def test_mensagem_vence_descricao_do_material():
    reg = {"material": "000000000022066463", "descricao": "CABO COAXIAL", "mensagem": "Erro durante baixa de estoque"}
    assert mapear_campos(reg, WO)["mensagem"] == "mensagem"


# ============================================================ 2. fidelidade
def test_mensagem_original_e_zeros_preservados(tmp_path):
    itens = [item(mensagem="Erro durante baixa de estoque"),
             item(ID="i2", material="000000000011000042", fornecedor="0000000001", quantidadeFaltante="10.000",
                  mensagem="Estoque indisponível")]
    rs = processar_lote(ClienteBTP(config_conjunto_unico(), BTP([itens], por_chave=lambda u: next(
        i for i in itens if i["ID"] in u))), [WO], prefs=RAPIDO)
    destino = gerar_planilha(rs, tmp_path / "a.xlsx")
    linhas = list(load_workbook(destino)["Dados"].iter_rows(min_row=2, values_only=True))
    assert linhas == [(WO, "000000000022066463", "0000138480", 2, "Erro durante baixa de estoque", None),
                      (WO, "000000000011000042", "0000000001", 10, "Estoque indisponível", None)]
    assert verificar_planilha(destino, rs) == []


def test_cada_material_fica_com_o_seu_fornecedor_e_a_sua_wo():
    itens = {WO: [item(), item(ID="i2", material="000000000011000042", fornecedor="0000999999")],
             WO2: [item(ID="i3", workOrderID=WO2, material="000000000022066463", fornecedor="0000555555")]}

    def t(url, timeout=None):
        wo = WO2 if "02235" in url else WO
        return Resposta(200, "application/json", json.dumps({"value": itens[wo]}))
    rs = processar_lote(ClienteBTP(config_conjunto_unico(url_item=None, chaves_item=None), t), [WO, WO2],
                        prefs=RAPIDO)
    pares = [(l.wo, l.material, l.fornecedor) for r in rs for l in r.linhas]
    assert pares == [(WO, "000000000022066463", "0000138480"), (WO, "000000000011000042", "0000999999"),
                     (WO2, "000000000022066463", "0000555555")]


def test_linha_de_outra_wo_e_descartada_e_avisada():
    """Se o servidor ignorar o filtro, linhas de outras WOs não podem entrar como se fossem desta."""
    btp = BTP([[item(), item(ID="x", workOrderID=WO2)]], por_chave=lambda u: item())
    r = processar_wo(ClienteBTP(config_conjunto_unico(), btp), WO, RAPIDO)
    assert [l.wo for l in r.linhas] == [WO]
    assert "outra WO" in r.observacao


def test_item_repetido_conta_uma_vez():
    """Duas linhas do mesmo alerta apontando para o mesmo item: 1 ocorrência, não 2."""
    config = cfg.a_partir_de_dict({
        "url_lista": "/s/A?$filter=Wo eq '{wo}'", "campo_consolidado_lista": "C",
        "url_mensagens": "/s/M?$filter=C eq '{linha[C]}'", "chaves_item": ["ID"],
        "campos": {"material": "material", "fornecedor": "fornecedor", "quantidade_faltante": "quantidadeFaltante",
                   "mensagem": "mensagem"}})

    def t(url, timeout=None):
        if "/s/A" in url:
            return Resposta(200, "application/json", json.dumps({"value": [{"Wo": WO, "C": "X"}, {"Wo": WO, "C": "X"}]}))
        return Resposta(200, "application/json", json.dumps({"value": [item()]}))
    r = processar_wo(ClienteBTP(config, t), WO, RAPIDO)
    assert r.registros == 1 and r.linhas[0].quantidade_faltante == 2
    assert r.leituras[0].duplicadas == 1 and "repetido" in r.observacao


# ============================================================ 3. auditoria
def test_tudo_igual_valida_e_conta_as_rodadas():
    btp = BTP([[item()]], por_chave=lambda u: item())
    r = processar_wo(ClienteBTP(config_conjunto_unico(), btp), WO, RAPIDO)
    assert r.auditoria is Auditoria.VALIDADO and r.linhas[0].auditoria is Auditoria.VALIDADO
    assert r.rodadas_concluidas == 5 == r.rodadas_previstas
    tipos = [l.tipo for l in r.leituras]
    assert tipos == ["Extração inicial", "Nova consulta", "Releitura por chave", "Nova consulta",
                     "Releitura por chave", "Nova consulta"]
    assert sum("/srv/Itens(" in u for u in btp.urls) == 2           # releituras por outro caminho


def test_divergencia_de_quantidade_nao_e_aprovada_nem_por_maioria():
    """Extração diz 2; 4 de 5 auditorias dizem 3. Sem evidência, não se escolhe: vai para revisão."""
    btp = BTP([[item(quantidadeFaltante=2)], [item(quantidadeFaltante=3)]], por_chave=lambda u: item(quantidadeFaltante=3))
    r = processar_wo(ClienteBTP(config_conjunto_unico(), btp), WO, Preferencias(intervalo_auditorias_seg=0,
                                                                                 divergencia="revisar"))
    [l] = r.linhas
    assert r.auditoria is Auditoria.DIVERGENTE and l.auditoria is Auditoria.DIVERGENTE
    assert l.quantidade_faltante is None                       # nenhum valor sem evidência
    assert "r0=2" in l.notas["quantidade_faltante"] and "r1=3" in l.notas["quantidade_faltante"]
    assert l.material == "000000000022066463"                 # os campos que concordam ficam
    diferentes = [a for a in r.registros_auditoria if a["resultado"].startswith("Diferente")]
    assert diferentes and "Quantidade faltante (2 → 3)" in diferentes[0]["resultado"]


def test_divergencia_resolvida_pelo_registro_de_alteracao_do_btp():
    """O BTP informa que o item foi alterado entre as leituras: vale o valor atual, confirmado
    por leituras posteriores — e a ocorrência continua sinalizada como divergente."""
    antes = item(quantidadeFaltante=3, modifiedAt="2026-10-14T10:00:00Z")
    depois = item(quantidadeFaltante=2, modifiedAt="2026-10-14T10:05:00Z")
    btp = BTP([[antes], [depois]], por_chave=lambda u: depois)
    r = processar_wo(ClienteBTP(config_conjunto_unico(), btp), WO, RAPIDO)
    [l] = r.linhas
    assert l.auditoria is Auditoria.DIVERGENTE and l.quantidade_faltante == 2
    assert "Resolvida" in l.notas["quantidade_faltante"]


def test_leituras_extras_em_divergencia_sao_feitas_e_registradas():
    btp = BTP([[item()], [item(quantidadeFaltante=3)]], por_chave=lambda u: item(quantidadeFaltante=3))
    r = processar_wo(ClienteBTP(config_conjunto_unico(), btp), WO,
                     Preferencias(intervalo_auditorias_seg=0, consultas_extras_divergencia=2))
    assert [l.tipo for l in r.leituras].count("Leitura extra") == 2
    assert r.rodadas_concluidas == 5 and r.auditoria is Auditoria.DIVERGENTE   # extras não contam como auditoria


def test_ocorrencia_que_some_numa_auditoria_e_divergente():
    btp = BTP([[item(), item(ID="i2", material="000000000011000042")], [item()]],
              por_chave=lambda u: item() if "i1" in u else None)
    r = processar_wo(ClienteBTP(config_conjunto_unico(), btp), WO, RAPIDO)
    assert r.auditoria is Auditoria.DIVERGENTE
    sumida = next(l for l in r.linhas if l.material == "000000000011000042")
    assert sumida.auditoria is Auditoria.DIVERGENTE and "ausente" in sumida.observacao


def test_campo_ausente_fica_incompleto_e_vazio(tmp_path):
    btp = BTP([[item(fornecedor="")]], por_chave=lambda u: item(fornecedor=""))
    rs = processar_lote(ClienteBTP(config_conjunto_unico(), btp), [WO], prefs=RAPIDO)
    [l] = rs[0].linhas
    assert l.auditoria is Auditoria.INCOMPLETO and l.fornecedor == ""
    ws = load_workbook(gerar_planilha(rs, tmp_path / "x.xlsx"))["Dados"]
    assert ws["C2"].value is None                                     # sem valor fictício
    assert ws["F2"].value == "Fornecedor SAP não informado pelo BTP."


def test_quantidade_ambigua_nao_e_validada():
    config = config_conjunto_unico(campos={"material": "material", "fornecedor": "fornecedor",
                                           "quantidade_faltante": "quantidade", "mensagem": "mensagem"})
    btp = BTP([[item(quantidade=3, qtdTotal=5)]], por_chave=lambda u: item(quantidade=3, qtdTotal=5))
    r = processar_wo(ClienteBTP(config, btp), WO, RAPIDO)
    assert r.linhas[0].auditoria is Auditoria.INCOMPLETO
    assert "ambígua" in r.linhas[0].notas["quantidade_faltante"].lower()


def test_sem_releitura_por_chave_a_limitacao_e_registrada():
    btp = BTP([[item()]])
    r = processar_wo(ClienteBTP(config_conjunto_unico(url_item=None, chaves_item=None), btp), WO, RAPIDO)
    assert all(l.tipo != "Releitura por chave" for l in r.leituras)
    assert "Releitura por chave indisponível" in r.observacao_auditoria
    assert r.auditoria is Auditoria.VALIDADO


def test_falha_de_rede_e_repetida_e_a_auditoria_continua():
    btp = BTP([[item()]], por_chave=lambda u: item(), falhas=2)
    r = processar_wo(ClienteBTP(config_conjunto_unico(), btp), WO, RAPIDO)
    assert r.status is Status.PROCESSADA and r.auditoria is Auditoria.VALIDADO


def test_auditoria_que_falha_deixa_nao_concluido():
    estado = {"n": 0}

    def t(url, timeout=None):
        estado["n"] += 1
        if estado["n"] > 1:
            return Resposta(503, "text/plain", "")
        return Resposta(200, "application/json", json.dumps({"value": [item()]}))
    r = processar_wo(ClienteBTP(config_conjunto_unico(url_item=None, chaves_item=None), t), WO,
                     Preferencias(intervalo_auditorias_seg=0, repeticoes_falha=0))
    assert r.linhas[0].auditoria is Auditoria.NAO_CONCLUIDO and r.auditoria is Auditoria.NAO_CONCLUIDO
    assert r.rodadas_concluidas == 0 and "0 de 5" in r.observacao_auditoria


def test_wo_sem_alerta_e_auditada_e_nao_inventa_linha():
    r = processar_wo(ClienteBTP(config_conjunto_unico(), BTP([[]])), WO, RAPIDO)
    assert r.status is Status.NAO_ENCONTRADA and r.linhas == [] and r.auditoria is Auditoria.VALIDADO


# ============================================================ 4. tempo e lote
def test_tempo_maximo_da_wo_interrompe_as_auditorias():
    btp = BTP([[item()]], por_chave=lambda u: item(), atraso=0.15)
    prefs = Preferencias(intervalo_auditorias_seg=0, tempo_max_wo_seg=0.4)
    inicio = time.monotonic()
    r = processar_wo(ClienteBTP(config_conjunto_unico(), btp), WO, prefs)
    assert time.monotonic() - inicio < 1.5
    assert r.auditoria is Auditoria.NAO_CONCLUIDO and r.rodadas_concluidas < 5
    assert any("tempo máximo" in l.erro for l in r.leituras)


def test_tempo_por_consulta_chega_ao_transporte():
    vistos = []

    def t(url, timeout=None):
        vistos.append(timeout)
        return Resposta(200, "application/json", json.dumps({"value": [item()]}))
    c = ClienteBTP(config_conjunto_unico(url_item=None), t, tempos={"lista": 12, "detalhes": 7})
    processar_wo(c, WO, Preferencias(auditorias_por_wo=0))
    assert vistos and all(0 < v <= 12 for v in vistos)


def test_tempo_maximo_do_lote_marca_as_restantes_como_nao_processadas():
    btp = BTP([[item()]], atraso=0.05)
    rs = processar_lote(ClienteBTP(config_conjunto_unico(url_item=None), btp), [WO, WO2, WO],
                        prefs=Preferencias(auditorias_por_wo=0, tempo_max_lote_min=0.0005))
    assert rs[-1].status is Status.NAO_PROCESSADA and "lote" in rs[-1].observacao


def test_interromper_diante_de_erro():
    def t(url, timeout=None):
        return Resposta(400, "application/json", '{"error":{"message":"x"}}')
    rs = processar_lote(ClienteBTP(config_conjunto_unico(), t), [WO, WO2],
                        prefs=Preferencias(em_erro="interromper"))
    assert [r.status for r in rs] == [Status.ERRO, Status.NAO_PROCESSADA]


# ============================================================ 5. Excel
def _lote_misto():
    """WO validada (2 materiais) + WO divergente + WO sem alerta."""
    def t(url, timeout=None):
        if "02235" in url:
            t.n = getattr(t, "n", 0) + 1
            return Resposta(200, "application/json", json.dumps({"value": [
                item(ID="d1", workOrderID=WO2, quantidadeFaltante=1 if t.n == 1 else 4)]}))
        if "99999" in url:
            return Resposta(200, "application/json", json.dumps({"value": []}))
        return Resposta(200, "application/json", json.dumps({"value": [
            item(), item(ID="i2", material="000000000011000042", quantidadeFaltante=10, mensagem="Erro durante baixa de estoque")]}))
    return processar_lote(ClienteBTP(config_conjunto_unico(url_item=None, chaves_item=["ID"]), t),
                          [WO, WO2, "99999|999999999"], prefs=Preferencias(intervalo_auditorias_seg=0,
                                                                           divergencia="revisar"))


def test_estrutura_completa_do_excel(tmp_path):
    rs = _lote_misto()
    destino = gerar_planilha(rs, tmp_path / "x.xlsx", numero=3, prefs=Preferencias(),
                             fonte_campos={"quantidade_faltante": "quantidadeFaltante"})
    wb = load_workbook(destino)
    assert wb.sheetnames == ABAS
    d = list(wb["Dados"].iter_rows(values_only=True))
    assert len(d) == 5                                    # 3 itens + 1 linha da WO sem alerta (TODAS as WOs)
    assert d[3][:5] == (WO2, "000000000022066463", "0000138480", None, "Estoque indisponível")  # sem valor
    assert "Quantidade faltante diferente entre consultas (1 e 4" in d[3][5] and "conferir no BTP" in d[3][5]
    assert d[4] == ("99999|999999999", None, None, None, None, "Nenhum alerta encontrado no Monitor para esta WO")
    assert d[1][5] is None and d[2][5] is None            # conferidos: sem observação
    assert verificar_planilha(destino, rs) == []
    a = [r for r in wb["Análise"].iter_rows(values_only=True) if r[0]]
    numeros = {r[0]: r[1] for r in a}
    assert numeros["WOs consultadas"] == 3 and numeros["WOs com itens faltantes"] == 2
    assert numeros["Itens faltantes (linhas com material na aba Dados)"] == 3   # auditorias não inflam
    assert numeros["Itens com observação (conferir no BTP)"] == 1
    linha_mat = next(r for r in a if r[0] == "000000000022066463")
    assert linha_mat[2] == 2 and WO in linha_mat[3] and WO2 in linha_mat[3] and linha_mat[4] is None
    assert any(r[0] == "Erro durante baixa de estoque" and r[1] == 1 for r in a)   # mensagem literal
    assert len(list(wb["Rastreio"].iter_rows())) > 5


def test_excel_sem_incompletos_quando_configurado(tmp_path):
    rs = _lote_misto()
    destino = gerar_planilha(rs, tmp_path / "x.xlsx", prefs=Preferencias(exportar_incompletos=False))
    d = list(load_workbook(destino)["Dados"].iter_rows(min_row=2, values_only=True))
    assert [r[0] for r in d] == [WO, WO, WO2, "99999|999999999"]
    assert d[2][1] is None and "omitido" in d[2][5]
    assert verificar_planilha(destino, rs, exportar_incompletos=False) == []


def test_verificacao_detecta_excel_adulterado(tmp_path):
    rs = _lote_misto()
    destino = gerar_planilha(rs, tmp_path / "x.xlsx")
    wb = load_workbook(destino)
    wb["Dados"]["D2"] = 3
    wb.save(destino)
    assert any("Quantidade faltante" in p for p in verificar_planilha(destino, rs))


# ============================================================ 6. configurações
def test_preferencias_persistem_e_sao_validadas(tmp_path):
    arq = tmp_path / "p.json"
    assert preferencias.carregar(arq).auditorias_por_wo == 5          # padrão
    prefs, problemas = preferencias.salvar({"auditorias_por_wo": "7", "tempo_max_wo_seg": 99999,
                                            "em_erro": "explodir", "auditorias_independentes": False}, arq)
    assert prefs.auditorias_por_wo == 7 and prefs.tempo_max_wo_seg == 3600 and prefs.em_erro == "continuar"
    assert len(problemas) == 2
    lida = preferencias.carregar(arq)                                  # "fecha e abre" o programa
    assert lida.auditorias_por_wo == 7 and lida.auditorias_independentes is False


def test_tela_de_configuracoes_salva_pela_api(tmp_path, monkeypatch):
    monkeypatch.setattr(preferencias, "ARQUIVO", tmp_path / "p.json")
    from agente_btp.agente import Agente
    from agente_btp.servidor import criar_app
    c = criar_app(Agente()).test_client()
    assert c.get("/api/configuracoes").get_json()["valores"]["auditorias_por_wo"] == 5
    r = c.post("/api/configuracoes", json={"auditorias_por_wo": 3, "intervalo_auditorias_seg": 1.5})
    assert r.get_json()["valores"]["auditorias_por_wo"] == 3
    assert json.loads((tmp_path / "p.json").read_text())["intervalo_auditorias_seg"] == 1.5
    assert c.post("/api/configuracoes/padrao").get_json()["valores"]["auditorias_por_wo"] == 5


def test_endereco_de_releitura_pelo_dicionario():
    xml = """<?xml version="1.0"?><edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
    <edmx:DataServices><Schema Namespace="S" xmlns="http://docs.oasis-open.org/odata/ns/edm">
    <EntityType Name="E"><Key><PropertyRef Name="ID"/></Key><Property Name="ID" Type="Edm.Guid"/></EntityType>
    <EntityContainer Name="C"><EntitySet Name="ErrosMaterial" EntityType="S.E"/></EntityContainer>
    </Schema></edmx:DataServices></edmx:Edmx>"""
    dados = {"url_lista": "/srv/ErrosMaterial?$filter=wo eq '{wo}'", "campo_mensagens_expandidas": "."}
    assert leitura_por_chave(dados, [("/srv/", ler_modelo(xml))]) == {
        "url_item": "/srv/ErrosMaterial({linha[ID]})", "chaves_item": ["ID"]}


def test_quantidade_decimal_continua_exata():
    btp = BTP([[item(quantidadeFaltante="2.500")]], por_chave=lambda u: item(quantidadeFaltante="2.500"))
    r = processar_wo(ClienteBTP(config_conjunto_unico(), btp), WO, RAPIDO)
    assert r.linhas[0].quantidade_faltante == Decimal("2.5") and r.auditoria is Auditoria.VALIDADO


@pytest.mark.parametrize("n", [0, 1, 3])
def test_numero_de_auditorias_configuravel(n):
    btp = BTP([[item()]], por_chave=lambda u: item())
    r = processar_wo(ClienteBTP(config_conjunto_unico(), btp), WO, Preferencias(auditorias_por_wo=n,
                                                                                 intervalo_auditorias_seg=0))
    assert r.rodadas_previstas == n == r.rodadas_concluidas and len(r.leituras) == n + 1
    assert r.auditoria is (Auditoria.NAO_CONCLUIDO if n == 0 else Auditoria.VALIDADO)   # 0 = nada validado


def test_servico_sem_leitura_por_chave_nao_gera_divergencia_falsa():
    """404 em TODOS os itens = o serviço não oferece esse caminho; não prova que os itens sumiram."""
    btp = BTP([[item(), item(ID="i2", material="000000000011000042")]], por_chave=lambda u: None)
    r = processar_wo(ClienteBTP(config_conjunto_unico(), btp), WO, RAPIDO)
    assert r.auditoria is Auditoria.VALIDADO and r.rodadas_concluidas == 5
    assert "não permite ler os itens pela chave" in r.observacao_auditoria
    assert [l.tipo for l in r.leituras][2:] == ["Nova consulta"] * 4


def test_validacao_com_falha_de_comunicacao_nao_aceita_caminho_sem_conferir(tmp_path, monkeypatch):
    from agente_btp import agente as agente_mod
    monkeypatch.setattr(cfg, "ARQUIVO_CONFIG", tmp_path / "api.json")
    a = agente_mod.Agente()

    def falha(url, accept=None, timeout=None):
        return Resposta(503, "text/plain", "")
    dados = {"url_lista": "/s/A?$filter=W eq '{wo}'", "url_mensagens": "/s/M?$filter=C eq '{linha[C]}'",
             "campos": {"material": "M", "fornecedor": "F", "quantidade_faltante": "Q", "mensagem": "T"}}
    assert a._validar(dados, [WO], falha, {}) == 0


# ============================================================ regressões da revisão independente
def _sem_chave():
    return config_conjunto_unico(url_item=None, chaves_item=None)


def test_sem_chave_mudanca_de_quantidade_e_divergencia_e_nao_item_novo():
    btp = BTP([[item(ID=None, quantidadeFaltante=2)], [item(ID=None, quantidadeFaltante=3)]])
    r = processar_wo(ClienteBTP(_sem_chave(), btp), WO, Preferencias(intervalo_auditorias_seg=0, divergencia="revisar"))
    [l] = r.linhas                                    # 1 ocorrência, não 2
    assert l.auditoria is Auditoria.DIVERGENTE and l.quantidade_faltante is None


def test_itens_iguais_de_alertas_diferentes_nao_sao_fundidos():
    config = cfg.a_partir_de_dict({
        "url_lista": "/s/A?$filter=Wo eq '{wo}'", "campo_consolidado_lista": "C",
        "url_mensagens": None, "campo_mensagens_expandidas": "itens",
        "campos": {"material": "material", "fornecedor": "fornecedor",
                   "quantidade_faltante": "quantidadeFaltante", "mensagem": "mensagem"}})
    sem_id = {k: v for k, v in item().items() if k != "ID"}

    def t(url, timeout=None):
        return Resposta(200, "application/json", json.dumps({"value": [
            {"Wo": WO, "C": "C-001", "itens": [sem_id]}, {"Wo": WO, "C": "C-002", "itens": [sem_id]}]}))
    r = processar_wo(ClienteBTP(config, t), WO, RAPIDO)
    assert sorted(l.consolidado for l in r.linhas) == ["C-001", "C-002"]


def test_itens_iguais_na_mesma_consulta_sao_itens_distintos():
    sem_id = {k: v for k, v in item().items() if k != "ID"}
    r = processar_wo(ClienteBTP(_sem_chave(), BTP([[sem_id, sem_id]])), WO, RAPIDO)
    assert r.registros == 2


def test_item_nao_relido_nao_fica_validado():
    btp = BTP([[item(), item(ID=None, material="000000000011000042")]], por_chave=lambda u: item())
    r = processar_wo(ClienteBTP(config_conjunto_unico(), btp), WO, Preferencias(auditorias_por_wo=2,
                                                                                 intervalo_auditorias_seg=0))
    sem_chave = next(l for l in r.linhas if l.material == "000000000011000042")
    assert sem_chave.auditoria is Auditoria.NAO_CONCLUIDO and "1 de 2" in sem_chave.observacao


def test_campo_que_nao_e_data_nao_serve_de_evidencia():
    leituras = [item(quantidadeFaltante=q, alteradoPor=u) for q, u in ((2, "ZECA"), (3, "ANA"), (2, "ZECA"), (2, "ZECA"))]
    btp = BTP([[x] for x in leituras], por_chave=lambda u: leituras[-1])
    r = processar_wo(ClienteBTP(config_conjunto_unico(), btp), WO,
                     Preferencias(auditorias_por_wo=3, auditorias_independentes=False, divergencia="revisar"))
    assert r.linhas[0].quantidade_faltante is None and "Não resolvida" in r.linhas[0].notas["quantidade_faltante"]


def test_data_que_volta_no_tempo_nao_resolve():
    novo = item(alteradoEm="2026-10-15T10:00:00.500Z", quantidadeFaltante=3)
    velho = item(alteradoEm="2026-10-15T10:00:00Z", quantidadeFaltante=2)
    r = processar_wo(ClienteBTP(config_conjunto_unico(), BTP([[novo], [velho]])), WO,
                     Preferencias(auditorias_por_wo=3, auditorias_independentes=False, divergencia="revisar"))
    assert r.linhas[0].quantidade_faltante is None


def test_situacao_considera_todas_as_leituras():
    r = processar_wo(ClienteBTP(config_conjunto_unico(), BTP([[], [item()]], por_chave=lambda u: item())), WO,
                     Preferencias(intervalo_auditorias_seg=0, wo_sem_resultado="registrar"))
    assert r.status is Status.PROCESSADA and r.auditoria is Auditoria.DIVERGENTE


def test_texto_com_igual_e_controle_nao_vira_formula_nem_quebra(tmp_path):
    itens = [item(mensagem="=> Saldo insuficiente (-2)"), item(ID="i2", mensagem="Falta\x0bmaterial")]
    rs = processar_lote(ClienteBTP(_sem_chave(), BTP([itens])), [WO], prefs=RAPIDO)
    destino = gerar_planilha(rs, tmp_path / "x.xlsx")
    ws = load_workbook(destino)["Dados"]
    assert ws["E2"].value == "=> Saldo insuficiente (-2)" and ws["E2"].data_type == "s"
    assert ws["E3"].value == "Faltamaterial"
    assert verificar_planilha(destino, rs) == []


def test_verificacao_acusa_formula_na_aba_1(tmp_path):
    rs = processar_lote(ClienteBTP(_sem_chave(), BTP([[item()]])), [WO], prefs=RAPIDO)
    destino = gerar_planilha(rs, tmp_path / "x.xlsx")
    wb = load_workbook(destino)
    wb["Dados"]["E2"] = "=1+1"
    wb.save(destino)
    assert any("fórmula" in p for p in verificar_planilha(destino, rs))


def test_releitura_recusada_com_400_cai_para_nova_consulta():
    def t(url, timeout=None):
        if "/srv/Itens(" in url:
            return Resposta(400, "application/json", '{"error":{"message":"chave"}}')
        return Resposta(200, "application/json", json.dumps({"value": [item()]}))
    r = processar_wo(ClienteBTP(config_conjunto_unico(), t), WO, RAPIDO)
    assert r.auditoria is Auditoria.VALIDADO and r.rodadas_concluidas == 5
    assert "não permite ler os itens pela chave" in r.observacao_auditoria


def test_tempos_com_decimal_e_valores_invalidos(tmp_path):
    prefs, problemas = preferencias.salvar({"tempo_max_wo_seg": 12.5, "tempo_max_lote_min": 1.5,
                                            "tempo_consulta_wo_seg": "nan"}, tmp_path / "p.json")
    assert prefs.tempo_max_wo_seg == 12.5 and prefs.tempo_max_lote_min == 1.5
    assert prefs.tempo_consulta_wo_seg == 30.0 and problemas
