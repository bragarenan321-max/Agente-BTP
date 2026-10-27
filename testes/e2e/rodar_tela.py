"""Tela no navegador real (Chromium): fluxo operacional completo + ajuda guiada + ajuda contextual.

Usa o agente de verdade com um BTP falso (sem a aba do BTP): a tela, a API e a base são as reais.
Rode a partir de uma CÓPIA do projeto (ele grava config/, dados/ e saidas/):
    python testes/e2e/rodar_tela.py [pasta_para_prints]
"""
import os
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, os.getcwd())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openpyxl import Workbook  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from agente_btp import config as cfg  # noqa: E402
from agente_btp.agente import Agente  # noqa: E402
from agente_btp.servidor import criar_app  # noqa: E402
from btp_lote_falso import BTPLote, config  # noqa: E402

PRINTS = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
URL = "http://127.0.0.1:8767/"
btp = BTPLote(300)
btp.latencia = 0.4          # lenta o bastante para testar pausa/ajuda com a análise rodando
cfg.salvar(dict(asdict(config()), confirmada=True, origem="receita_btp"))
agente = Agente()
agente.ponte.get = btp
agente.ponte.agente_id, agente.ponte._aba_autenticada = "aba", True
agente.ponte._visto_em = time.monotonic() + 36000
app = criar_app(agente)
threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8767, threaded=True), daemon=True).start()
time.sleep(1)

falhas, feitos = [], []


def confere(cond, texto):
    (feitos if cond else falhas).append(texto)
    print(("  ok  " if cond else "  FALHOU  ") + texto)


def esperar(f, t=60):
    fim = time.time() + t
    while time.time() < fim:
        if f():
            return True
        time.sleep(0.15)
    return False


def sobrepoe(a, b, folga=12):
    return (a["x"] <= b["x"] + folga and a["y"] <= b["y"] + folga and
            a["x"] + a["width"] >= b["x"] + b["width"] - folga and a["y"] + a["height"] >= b["y"] + b["height"] - folga)


with sync_playwright() as p:
    nav = p.chromium.launch()
    pg = nav.new_page(viewport={"width": 1440, "height": 900})
    erros_js = []
    pg.on("pageerror", lambda e: erros_js.append(str(e)))
    pg.on("dialog", lambda d: d.accept())
    pg.goto(URL)
    pg.wait_for_timeout(1500)
    print("== tela inicial")
    confere(pg.text_content("#seloEstado") == "Pronta para iniciar", "selo 'Pronta para iniciar' com a aba conectada")
    confere(pg.is_disabled("#bExecutar") and "Informe" in pg.inner_text("#motivoBloqueio"),
            "Executar bloqueado sem WOs, com o motivo escrito")
    pg.screenshot(path=str(PRINTS / "tela_inicial.png"), full_page=True)

    print("== entrada")
    pg.fill("#texto", "\n".join(btp.wos[:250]) + "\nlixo\n")
    confere("250" in pg.inner_text("#contador") and "1 linha" in pg.inner_text("#contadorInvalidas"),
            "contador: 250 WOs válidas + 1 linha fora do formato")
    planilha = Path(tempfile.mkdtemp()) / "lista.xlsx"
    wb = Workbook()
    wb.active.append(["WO", "Obs"])
    for w in btp.wos[200:300]:
        wb.active.append([w, "x"])
    wb.save(planilha)
    pg.set_input_files("#arquivo", str(planilha))
    confere(esperar(lambda: "100 WO(s) reconhecida(s)" in pg.inner_text("#nomeArquivo"), 10),
            "planilha importada: nome do arquivo + 100 WOs reconhecidas")
    confere("lista.xlsx" in pg.inner_text("#nomeArquivo"), "nome do arquivo exibido")
    confere(not pg.is_disabled("#bExecutar"), "Executar liberado com WOs e aba conectada")

    print("== execução")
    pg.click("#bExecutar")
    confere(esperar(lambda: pg.text_content("#seloEstado") == "Executando", 20), "selo 'Executando'")
    confere(esperar(lambda: "WO/s" in pg.inner_text("#etaTxt"), 20), "ritmo (WO/s) medido e exibido")
    pg.screenshot(path=str(PRINTS / "tela_executando.png"), full_page=True)

    print("== ajuda durante a execução (não pode alterar nada)")
    escritas = []
    pg.on("request", lambda r: escritas.append(r.url) if r.method != "GET" else None)
    pg.click("#bAjuda")
    confere(pg.is_visible("#tutBalao") and pg.text_content("#tutPasso") == "Passo 1 de 6", "ajuda abre no passo 1 de 6")
    confere(pg.is_disabled("#tutAnterior"), "Anterior desabilitado no passo 1")
    confere(pg.evaluate("document.activeElement.id") == "tutProximo", "foco no botão Próximo do balão")
    for _ in range(4):
        pg.keyboard.press("Tab")
    confere(pg.evaluate("!!document.activeElement.closest('#tutBalao')"), "Tab não sai do balão (foco preso)")
    passos = [("#texto",), ("#zonaImportar",), ("#bExecutar",), ("#blocoProgresso",), ("#painel",), ("#secDiagnostico",)]
    log_aberto_antes = pg.evaluate("$('caixaLog').open")
    for i, (alvo,) in enumerate(passos):
        if i:
            pg.keyboard.press("ArrowRight")
        pg.wait_for_timeout(450)
        foco, caixa = pg.locator("#tutFoco").bounding_box(), pg.locator(alvo).first.bounding_box()
        balao = pg.locator("#tutBalao").bounding_box()
        confere(sobrepoe(foco, caixa), f"passo {i + 1}: destaque envolve {alvo}")
        confere(0 <= balao["y"] and balao["y"] + balao["height"] <= 900 and 0 <= balao["x"] <= 1440 - balao["width"],
                f"passo {i + 1}: balão dentro da tela")
        if i == 0:
            pg.screenshot(path=str(PRINTS / "ajuda_passo1.png"))
        if i == 3:
            pg.set_viewport_size({"width": 1100, "height": 760})
            pg.wait_for_timeout(450)
            confere(sobrepoe(pg.locator("#tutFoco").bounding_box(), pg.locator(alvo).first.bounding_box()),
                    "destaque acompanha o elemento após redimensionar")
            pg.mouse.wheel(0, 300)
            pg.wait_for_timeout(450)
            confere(sobrepoe(pg.locator("#tutFoco").bounding_box(), pg.locator(alvo).first.bounding_box()),
                    "destaque acompanha o elemento após rolar a página")
            pg.screenshot(path=str(PRINTS / "ajuda_passo4.png"))
            pg.set_viewport_size({"width": 1440, "height": 900})
    confere(pg.evaluate("$('caixaLog').open") is True, "passo 6 abre o 'Registro de execução' recolhido")
    confere(pg.inner_text("#tutProximo") == "Concluir", "último passo mostra 'Concluir'")
    pg.keyboard.press("ArrowLeft")
    pg.wait_for_timeout(300)
    confere(pg.evaluate("$('caixaLog').open") == log_aberto_antes, "ao sair do passo 6, a seção volta ao estado anterior")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    confere(not pg.is_visible("#tutorial") and not pg.is_visible("#tutFoco"), "Esc encerra e limpa o escurecimento")
    confere(pg.evaluate("document.activeElement.id") == "bAjuda", "foco volta para o botão Ajuda")
    confere(not pg.evaluate("$('vistaAnalise').hasAttribute('inert')"), "tela volta a aceitar cliques")
    for _ in range(3):                                   # abre e fecha várias vezes
        pg.click("#bAjuda")
        pg.wait_for_timeout(150)
        pg.click("#tutSair")
    confere(not pg.is_visible("#tutorial"), "ajuda abre e fecha repetidamente")
    confere(not escritas, f"nenhuma requisição de escrita durante a ajuda ({len(escritas)})")

    print("== ajuda contextual")
    pg.click("#painel .indicador.ok .ajuda-ctx")
    confere(pg.is_visible("#dica") and "leituras" in pg.inner_text("#dica"), "'?' de Validadas mostra a regra")
    pg.keyboard.press("Escape")
    confere(not pg.is_visible("#dica"), "Esc fecha a dica")

    print("== pausar / retomar / parar / retomar análise")
    confere(esperar(lambda: pg.is_visible("#bPausar"), 10), "botão Pausar visível executando")
    pg.click("#bPausar")
    confere(esperar(lambda: pg.text_content("#seloEstado") == "Pausada", 30), "selo 'Pausada'")
    confere("pausada" in pg.inner_text("#etaTxt"), "sem tempo restante enquanto pausada")
    pg.click("#bContinuar")
    confere(esperar(lambda: pg.text_content("#seloEstado") == "Executando", 20), "Retomar volta a executar")
    pg.click("#bParar")                                  # confirmação aceita pelo diálogo
    confere(esperar(lambda: pg.text_content("#seloEstado") == "Interrompida", 40), "Parar → 'Interrompida'")
    run_id = agente.estado()["run_id"]
    c = agente.loja.contadores(run_id)
    num = lambda i: int(pg.inner_text(i).replace(".", ""))
    confere(num("#iValidadas") == c["validada"] and num("#iPendentes") == c["pendente"] + c["em_andamento"],
            "indicadores da tela = contadores da base")
    confere(esperar(lambda: pg.is_visible("#cartaoRetomar"), 10) and "Retomar análise" in pg.inner_text("#retTitulo"),
            "banner 'Retomar análise nº N' após a parada")
    pg.screenshot(path=str(PRINTS / "tela_interrompida.png"), full_page=True)
    pg.click("#bRetomarAnalise")
    confere(esperar(lambda: pg.text_content("#seloEstado").startswith("Concluída"), 90), "retomada termina 'Concluída'")
    c = agente.loja.contadores(run_id)
    confere(c["pendente"] == 0 and num("#iProcessadas") == c["validada"] + c["divergente"] + c["incompleta"] + c["falha"],
            "todas feitas; Processadas = soma dos estados")
    confere(pg.is_visible("#baixar"), "botão 'Baixar relatório auditado' visível")
    href = pg.get_attribute("#baixar", "href")
    confere(urllib.request.urlopen(URL.rstrip("/") + href).status == 200, "download do .xlsx funciona")
    diag = pg.get_attribute("#diagBaixar", "href")
    confere(urllib.request.urlopen(URL.rstrip("/") + diag).status == 200, "download do diagnóstico .json funciona")
    pg.click("#caixaDiag summary")
    confere(esperar(lambda: "Próxima ação" in pg.inner_text("#diagAcao"), 10), "'O que deu errado' carrega sob demanda")
    pg.screenshot(path=str(PRINTS / "tela_concluida.png"), full_page=True)

    print("== histórico e configurações")
    pg.click("#abaHistorico")
    confere(esperar(lambda: pg.locator("#tabHist tr.clicavel").count() >= 1, 10), "histórico lista a análise")
    pg.click("#tabHist tr.clicavel")
    confere(esperar(lambda: pg.is_visible("#detalheHist") and pg.locator("#tabDet tr").count() > 10, 10),
            "detalhe da análise com as WOs")
    pg.screenshot(path=str(PRINTS / "historico.png"), full_page=True)
    pg.click("#abaConfig")
    confere(esperar(lambda: pg.locator("#cfg_auditorias_por_wo").count() == 1, 10), "configurações carregam")
    pg.click("#cfgSalvar")
    confere(esperar(lambda: "salvas" in pg.inner_text("#cfgStatus"), 10), "configurações salvam")
    pg.click("#bAjuda")
    confere(pg.is_visible("#vistaAnalise") and pg.is_visible("#tutBalao"), "ajuda aberta em outra aba leva à tela de análise")
    pg.keyboard.press("Escape")
    confere(pg.is_visible("#vistaConfig"), "ao sair, volta para a aba em que o usuário estava")

    print("== telas pequenas")
    for largura in (390, 768):
        pg.set_viewport_size({"width": largura, "height": 844})
        pg.click("#abaAnalise")
        pg.wait_for_timeout(400)
        confere(pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"{largura}px: sem rolagem horizontal")
        pg.click("#bAjuda")
        pg.wait_for_timeout(400)
        b = pg.locator("#tutBalao").bounding_box()
        confere(b["x"] >= 0 and b["x"] + b["width"] <= largura and b["y"] + b["height"] <= 844,
                f"{largura}px: balão da ajuda dentro da tela")
        pg.screenshot(path=str(PRINTS / f"ajuda_{largura}px.png"))
        pg.keyboard.press("Escape")
        pg.screenshot(path=str(PRINTS / f"tela_{largura}px.png"), full_page=True)
    confere(not erros_js, f"sem erros de JavaScript ({erros_js[:2]})")
    nav.close()

print(f"\nRESULTADO: {len(feitos)} verificações ok, {len(falhas)} falharam")
for f in falhas:
    print("  -", f)
os._exit(1 if falhas else 0)
