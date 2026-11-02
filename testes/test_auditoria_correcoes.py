"""Correções da auditoria de 30/10/2026 (docs/auditoria/relatorio-auditoria-2026-10-30.md).

Cada teste reproduz um defeito do relatório (E1–E9) e falha na versão anterior às correções.
"""
import io
import json
import logging

import pytest

from agente_btp import historico, materiais, preferencias
from agente_btp.exportacao import gerar_planilha, linhas_dados, verificar_planilha
from agente_btp.modelos import Auditoria, LinhaMensagem, ResultadoWO, Status
from agente_btp.preferencias import Preferencias

WO = "11111|111111111"
FILTRO = "Estoque indisponível"
FORA = {"Origin": "https://site-malicioso.example", "Content-Type": "text/plain"}


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(preferencias, "ARQUIVO", tmp_path / "p.json")
    monkeypatch.setattr(historico, "ARQUIVO", tmp_path / "h" / "historico.json")
    monkeypatch.setattr(historico, "CONTADOR", tmp_path / "h" / "contador.txt")
    monkeypatch.setattr(materiais, "arquivo_tabela", lambda: tmp_path / "tabela_materiais.json")
    # Fonte e fontes recusadas também na pasta temporária: um pedido que escape da proteção
    # (ex.: /api/fonte/refazer) nunca pode alterar a config/ real do projeto.
    from agente_btp import agente as agente_mod, config as cfg
    monkeypatch.setattr(cfg, "ARQUIVO_CONFIG", tmp_path / "api.json")
    monkeypatch.setattr(agente_mod, "ARQUIVO_REJEITADAS", tmp_path / "fontes_recusadas.json")
    from agente_btp.agente import Agente
    from agente_btp.servidor import criar_app
    return criar_app(Agente()).test_client()


# ---------------------------------------------------------------- E1: pedidos de outros sites
@pytest.mark.parametrize("rota, corpo", [
    ("/api/configuracoes", json.dumps({"auditorias_por_wo": 0, "mensagem_planilha": ""})),
    ("/api/parar", ""),
    ("/api/fonte/refazer", ""),
    ("/api/configuracoes/padrao", ""),
])
def test_e1_pedido_de_outro_site_e_recusado(cliente, rota, corpo):
    r = cliente.post(rota, data=corpo, headers=FORA)
    assert r.status_code == 403
    assert preferencias.carregar().auditorias_por_wo == 2              # nada mudou


def test_e1_formulario_de_outro_site_nao_inicia_analise(cliente):
    r = cliente.post("/api/executar", data={"texto": WO},
                     headers={"Origin": "https://site-malicioso.example"})
    assert r.status_code == 403


def test_e1_origem_nula_e_apagar_historico_de_fora_sao_recusados(cliente):
    assert cliente.post("/api/parar", headers={"Origin": "null"}).status_code == 403
    assert cliente.delete("/api/historico", headers={"Origin": "https://x.example"}).status_code == 403


def test_e1_a_propria_tela_continua_funcionando(cliente):
    for origem in ("http://127.0.0.1:8767", "http://localhost:8767", None):
        h = {"Origin": origem} if origem else {}
        r = cliente.post("/api/configuracoes", json={"auditorias_por_wo": 3}, headers=h)
        assert r.status_code == 200 and r.get_json()["valores"]["auditorias_por_wo"] == 3


def test_e1_canal_da_aba_continua_com_a_regra_propria(cliente):
    from agente_btp.servidor import ORIGEM_SAP
    r = cliente.post("/api/agente/ola", json={"agente": "a1", "pagina": "/"}, headers={"Origin": ORIGEM_SAP})
    assert r.status_code == 200
    assert cliente.post("/api/agente/ola", json={"agente": "a1"},
                        headers={"Origin": "https://x.example"}).status_code == 403


# ---------------------------------------------------------------- E2: DNS rebinding
@pytest.mark.parametrize("rota", ["/api/estado", "/api/historico", "/api/diagnostico", "/"])
def test_e2_endereco_estranho_e_recusado(cliente, rota):
    assert cliente.get(rota, headers={"Host": "atacante.example:8767"}).status_code == 403


def test_e2_enderecos_locais_sao_aceitos(cliente):
    for host in ("127.0.0.1:8767", "localhost:8767", "localhost"):
        assert cliente.get("/api/estado", headers={"Host": host}).status_code == 200


# ---------------------------------------------------------------- E3: item de estoque omitido
def _item(material, msg, auditoria, qtd=1):
    notas = ({"quantidade_faltante": "Divergente entre leituras: r0=1 / r1=4. Não resolvida: revisar no BTP."}
             if auditoria is Auditoria.DIVERGENTE else {})
    return LinhaMensagem("", WO, material, "0000099999", qtd if auditoria is Auditoria.VALIDADO else None, msg,
                         chave=material, auditoria=auditoria, notas=notas, mensagem_item=msg, mensagens_lidas=[msg])


def test_e3_item_de_estoque_omitido_nao_vira_nenhum_item(tmp_path):
    r = ResultadoWO(WO, Status.PROCESSADA, [_item("000000000012345678", FILTRO, Auditoria.DIVERGENTE),
                                            _item("000000000099999999", "Estoque insuficiente", Auditoria.VALIDADO)],
                    auditoria=Auditoria.DIVERGENTE)
    linha = linhas_dados([r], False, None, FILTRO)[0]
    assert "Nenhum item com" not in linha[6]
    assert linha[6].startswith(f'1 item(ns) "{FILTRO}" não validado(s) omitido(s)')
    assert "Estoque insuficiente" in linha[6]                          # os outros itens seguem descritos
    destino = gerar_planilha([r], tmp_path / "e3.xlsx", prefs=Preferencias(exportar_incompletos=False))
    assert verificar_planilha(destino, [r], False) == []
    from openpyxl import load_workbook
    wb = load_workbook(destino)
    assert wb["Dados"]["G2"].fill.fgColor.rgb.endswith("FFF2CC")       # pede conferência: destaque
    numeros = {l[0]: l[1] for l in wb["Análise"].iter_rows(values_only=True) if l and len(l) > 1}
    assert numeros["WOs só com itens de outra mensagem (na Observação)"] == 0
    situacao = [l for l in wb["Análise"].iter_rows(values_only=True) if l and l[0] == WO][0]
    assert "só outras mensagens" not in situacao[1]


def test_e3_contagem_de_omitidos_nao_inclui_outras_mensagens():
    r = ResultadoWO(WO, Status.PROCESSADA, [_item("000000000012345678", FILTRO, Auditoria.DIVERGENTE),
                                            _item("000000000099999999", "Outra", Auditoria.DIVERGENTE)],
                    auditoria=Auditoria.DIVERGENTE)
    assert linhas_dados([r], False, None, FILTRO)[0][6].startswith(f'1 item(ns) "{FILTRO}" não validado(s)')


# ---------------------------------------------------------------- E4: histórico ilegível
def test_e4_historico_ilegivel_e_guardado_antes_de_ser_substituido(tmp_path, caplog):
    arq = tmp_path / "historico.json"
    arq.write_text("{corrompido", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        historico.registrar({"id": "a1", "numero": 1}, arq)
    copias = list(tmp_path.glob("historico.corrompido-*.json"))
    assert len(copias) == 1 and copias[0].read_text(encoding="utf-8") == "{corrompido"
    assert [a["id"] for a in historico.listar(arq)] == ["a1"]
    assert "ilegível" in caplog.text


# ---------------------------------------------------------------- E5: tabela de valores ilegível
def test_e5_tabela_ilegivel_da_erro_claro(tmp_path, cliente):
    (tmp_path / "tabela_materiais.json").write_text("lixo", encoding="utf-8")
    with pytest.raises(materiais.TabelaInvalida, match="(?i)importe"):
        materiais.carregar(tmp_path / "tabela_materiais.json")
    r = cliente.get("/api/materiais/tabela")
    assert r.status_code == 200 and r.is_json
    assert r.get_json()["carregada"] is False and "importe" in r.get_json()["erro"].lower()


# ---------------------------------------------------------------- E6: separadores em texto
@pytest.mark.parametrize("texto, esperado", [
    ("1.500", "1500"), ("12.345.678", "12345678"), ("1.500,25", "1500.25"), ("1,234.56", "1234.56"),
    ("0,13", "0.13"), ("12.5", "12.5"), ("0.13", "0.13"), ("R$ 2,50", "2.50"), ("1500", "1500"),
])
def test_e6_valor_em_texto_com_separadores(texto, esperado):
    from decimal import Decimal
    assert materiais._valor(texto) == Decimal(esperado)


def test_e6_celula_numerica_nao_muda():
    from decimal import Decimal
    assert materiais._valor(0.13) == Decimal("0.13") and materiais._valor(1500) == Decimal(1500)


# ---------------------------------------------------------------- E7: arquivo grande demais
def test_e7_arquivo_grande_demais_responde_em_json(cliente):
    r = cliente.post("/api/entrada/planilha",
                     data={"arquivo": (io.BytesIO(b"x" * (21 * 1024 * 1024)), "g.xlsx")},
                     content_type="multipart/form-data")
    assert r.status_code == 413 and r.is_json and "20 MB" in r.get_json()["erro"]


# ---------------------------------------------------------------- E9: falha ao atualizar a extensão
def test_e9_falha_ao_copiar_para_a_extensao_vai_para_o_log(tmp_path, monkeypatch, caplog):
    from agente_btp import servidor
    pasta = tmp_path / "extensao"
    pasta.mkdir()
    monkeypatch.setattr(servidor, "PASTA_EXTENSAO", pasta)
    def falha(*a, **k):
        raise PermissionError("acesso negado")
    monkeypatch.setattr(type(pasta / "agente_btp.js"), "write_text", falha)
    with caplog.at_level(logging.WARNING):
        servidor.sincronizar_extensao("codigo novo")
    assert "extensão" in caplog.text and "acesso negado" in caplog.text
