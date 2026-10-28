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


def sem_rolagem_horizontal(pg):
    """True se nada passa da largura da janela; senão lista os elementos que passam (para corrigir)."""
    if pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"):
        return True
    print("    elementos além da largura:", pg.evaluate("""[...document.querySelectorAll('body *')]
        .filter(e => e.getClientRects().length && e.getBoundingClientRect().right > innerWidth + 1)
        .slice(0, 6).map(e => e.tagName + '#' + e.id + '.' + e.className + ' → ' + Math.round(e.getBoundingClientRect().right))"""))
    return False


def abrir_tour(pg):
    """Ajuda guiada (balões): botão Ajuda do topo."""
    pg.click("#bAjuda")


# Passos da ajuda guiada: (alvo, alternativa se o alvo não estiver visível, página esperada)
PASSOS = [("#pilulaConexao", None, "#vistaAnalise"), ("#resumoAgente", None, "#vistaAnalise"), ("#texto", None, "#vistaAnalise"),
          ("#zonaImportar", None, "#vistaAnalise"), ("#bExecutar", None, "#vistaAnalise"),
          ("#cartaoProgresso .exec-cab", None, "#vistaAnalise"), ("#blocoProgresso", None, "#vistaAnalise"),
          ("#painel", None, "#vistaAnalise"), ("#acoesResultado", None, "#vistaAnalise"), ("#secRecentes", None, "#vistaAnalise"),
          ("#vistaHistorico .tabela-cartao", None, "#vistaHistorico"),
          ("#detTecnico", "#vistaHistorico .tabela-cartao", "#vistaHistorico"),
          ("[aria-labelledby=tFonte]", None, "#vistaConfig"), ("[aria-labelledby=tTabela]", None, "#vistaConfig"),
          ("[aria-labelledby=tAparencia]", None, "#vistaConfig"), ("#cfgGrupos", None, "#vistaConfig"),
          ("#vistaConfig .pagina-acoes", None, "#vistaConfig"), ("#bAjuda", None, "#vistaConfig")]


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
    confere(pg.locator(".marca-selo svg").count() == 1, "logo própria no menu lateral")
    print("== menu lateral")
    lat = pg.locator(".lateral").bounding_box()
    confere(pg.is_visible(".lateral") and 200 <= lat["width"] <= 260 and lat["x"] == 0 and lat["height"] >= 880,
            f"menu lateral persistente à esquerda ({round(lat['width'])}px)")
    fundo_ativo = pg.evaluate("getComputedStyle(document.getElementById('abaAnalise')).backgroundColor")
    confere(fundo_ativo not in ("rgba(0, 0, 0, 0)", "transparent"), f"item ativo com fundo próprio ({fundo_ativo})")
    confere(pg.locator(".lat-grupo").all_inner_texts() == ["OPERAÇÃO", "SISTEMA", "AJUDA"]
            or [t.upper() for t in pg.locator(".lat-grupo").all_inner_texts()] == ["OPERAÇÃO", "SISTEMA", "AJUDA"],
            "grupos do menu: Operação, Sistema, Ajuda")
    pg.click("#bRecolher"); pg.wait_for_timeout(350)
    confere(round(pg.locator(".lateral").bounding_box()["width"]) == 68 and not pg.is_visible("#abaAnalise .rot"),
            "Recolher menu vira trilho de ícones")
    pg.reload(); pg.wait_for_timeout(1200)
    confere(round(pg.locator(".lateral").bounding_box()["width"]) == 68, "menu recolhido lembrado ao recarregar")
    pg.click("#bRecolher"); pg.wait_for_timeout(350)
    confere(round(pg.locator(".lateral").bounding_box()["width"]) > 200, "Expandir menu volta ao tamanho normal")
    pg.fill("#texto", "12345|123456789")
    pg.click("#abaHistorico"); pg.wait_for_timeout(200); pg.click("#abaConfig"); pg.wait_for_timeout(200); pg.click("#abaAnalise")
    confere(pg.input_value("#texto") == "12345|123456789", "texto digitado preservado ao trocar de página")
    pg.fill("#texto", ""); pg.dispatch_event("#texto", "input")
    confere(not pg.is_visible("#painel") and pg.is_visible("#estadoVazio"), "ocioso sem histórico: estado vazio, sem cartões vazios")
    pg.screenshot(path=str(PRINTS / "tela_inicial.png"), full_page=True)
    print("== tela sem explicações permanentes; ajuda só quando pedida")
    confere(not pg.is_visible("#dica"), "nenhuma dica aberta sem ser pedida")
    confere(pg.locator("#vistaAnalise .conferencia, #vazioTexto").count() == 0, "análise sem parágrafos explicativos fixos")
    pg.keyboard.press("F1")
    confere(pg.is_visible("#tutBalao") and pg.text_content("#tutPasso") == f"Passo 1 de {len(PASSOS)}", "F1 abre a ajuda guiada")
    pg.keyboard.press("Escape")
    print("== ajuda guiada com a tela ociosa (indicadores ocultos)")
    abrir_tour(pg)
    for _ in range(7):
        pg.keyboard.press("ArrowRight")
    pg.wait_for_timeout(400)
    confere(pg.text_content("#tutTitulo") == "Indicadores" and pg.is_visible("#painel")
            and sobrepoe(pg.locator("#tutFoco").bounding_box(), pg.locator("#painel").bounding_box()),
            "passo 'Indicadores' mostra o painel oculto e o destaca")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(1300)
    confere(pg.is_visible("#vistaAnalise"), "ao sair, continua na página em que estava")
    confere(not pg.is_visible("#painel"), "ao sair da ajuda, o painel volta a ficar oculto")

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
    from agente_btp import historico as hist
    confere(esperar(lambda: bool(hist.listar()) and hist.listar()[0]["situacao"] == "Em andamento", 10),
            "a análise já aparece no Histórico como 'Em andamento'")
    confere(pg.locator("#cartaoConfirma input[value=ambas]").count() == 1, "conferência da amostra oferece 'As duas'")

    print("== ajuda durante a execução (não pode alterar nada)")
    escritas = []
    pg.on("request", lambda r: escritas.append(r.url) if r.method != "GET" else None)
    abrir_tour(pg)
    confere(pg.is_visible("#tutBalao") and pg.text_content("#tutPasso") == f"Passo 1 de {len(PASSOS)}",
            f"ajuda abre no passo 1 de {len(PASSOS)}")
    confere(pg.is_disabled("#tutAnterior"), "Anterior desabilitado no passo 1")
    confere(pg.evaluate("document.activeElement.id") == "tutProximo", "foco no botão Próximo do balão")
    for _ in range(4):
        pg.keyboard.press("Tab")
    confere(pg.evaluate("!!document.activeElement.closest('#tutBalao')"), "Tab não sai do balão (foco preso)")
    for i, (alvo, alternativa, vista) in enumerate(PASSOS):
        if i:
            pg.keyboard.press("ArrowRight")
        pg.wait_for_timeout(600)
        if alternativa and not pg.is_visible(alvo):
            alvo = alternativa
        confere(pg.is_visible(vista), f"passo {i + 1}: ajuda leva à página certa ({vista})")
        foco, caixa = pg.locator("#tutFoco").bounding_box(), pg.locator(alvo).first.bounding_box()
        balao = pg.locator("#tutBalao").bounding_box()
        confere(sobrepoe(foco, caixa), f"passo {i + 1}: destaque envolve {alvo}")
        confere(0 <= balao["y"] and balao["y"] + balao["height"] <= 900 and 0 <= balao["x"] <= 1440 - balao["width"],
                f"passo {i + 1}: balão dentro da tela")
        if i in (2, 12):
            pg.screenshot(path=str(PRINTS / f"ajuda_passo{i + 1}.png"))
        if i == 6:
            pg.set_viewport_size({"width": 1100, "height": 760})
            pg.wait_for_timeout(450)
            confere(sobrepoe(pg.locator("#tutFoco").bounding_box(), pg.locator(alvo).first.bounding_box()),
                    "destaque acompanha o elemento após redimensionar")
            pg.mouse.wheel(0, 300)
            pg.wait_for_timeout(450)
            confere(sobrepoe(pg.locator("#tutFoco").bounding_box(), pg.locator(alvo).first.bounding_box()),
                    "destaque acompanha o elemento após rolar a página")
            pg.screenshot(path=str(PRINTS / "ajuda_passo7.png"))
            pg.set_viewport_size({"width": 1440, "height": 900})
    confere(pg.inner_text("#tutProximo") == "Concluir", "último passo mostra 'Concluir'")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    confere(not pg.is_visible("#tutorial") and not pg.is_visible("#tutFoco"), "Esc encerra e limpa o escurecimento")
    confere(pg.is_visible("#vistaAnalise"), "ao sair, volta para a página em que estava")
    confere(pg.evaluate("document.activeElement.id") == "bAjuda", "foco volta para o botão Ajuda")
    confere(not pg.evaluate("$('vistaAnalise').hasAttribute('inert')"), "tela volta a aceitar cliques")
    for _ in range(3):                                   # abre e fecha várias vezes
        abrir_tour(pg)
        pg.wait_for_timeout(150)
        pg.click("#tutSair")
    confere(not pg.is_visible("#tutorial"), "ajuda abre e fecha repetidamente")
    confere(not escritas, f"nenhuma requisição de escrita durante a ajuda ({len(escritas)})")

    print("== ajuda contextual")
    opacidade = pg.evaluate("getComputedStyle(document.querySelector('#painel .indicador.ok .ajuda-ctx')).opacity")
    confere(opacidade == "0", f"'?' dos indicadores discreto até passar o mouse (opacidade {opacidade})")
    pg.hover("#painel .indicador.ok"); pg.wait_for_timeout(250)
    pg.click("#painel .indicador.ok .ajuda-ctx")
    confere(pg.is_visible("#dica") and "leituras" in pg.inner_text("#dica"), "'?' de Validadas mostra a regra")
    pg.keyboard.press("Escape")
    confere(not pg.is_visible("#dica"), "Esc fecha a dica")
    pg.hover("#painel .indicador.ok"); pg.click("#painel .indicador.ok .ajuda-ctx")
    pg.click("#dica .dica-link"); pg.wait_for_timeout(500)
    confere(pg.is_visible("#tutBalao") and pg.text_content("#tutTitulo") == "Indicadores",
            "'Ver na Ajuda guiada' abre a ajuda no passo Indicadores")
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)

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
    confere(not pg.locator("#vistaAnalise #caixaDiag").count(), "diagnóstico não fica mais na tela principal")
    pg.screenshot(path=str(PRINTS / "tela_concluida.png"), full_page=True)
    pg.click("#bVerDiag")
    confere(esperar(lambda: pg.is_visible("#vistaHistorico") and pg.is_visible("#detTecnico"), 10),
            "'Ver diagnóstico desta análise' abre o detalhe no Histórico")
    confere(esperar(lambda: "Próxima ação" in pg.inner_text("#diagAcao"), 10), "diagnóstico da análise no Histórico")
    pg.click("#caixaLog summary")
    confere(esperar(lambda: "Análise concluída" in pg.text_content("#log"), 10), "registro de execução da análise no Histórico")
    diag = pg.get_attribute("#diagBaixar", "href")
    confere(urllib.request.urlopen(URL.rstrip("/") + diag).status == 200, "download do diagnóstico .json funciona")
    pg.screenshot(path=str(PRINTS / "historico_diagnostico.png"), full_page=True)
    pg.click("#abaAnalise")

    print("== histórico e configurações")
    pg.click("#abaHistorico")
    confere(esperar(lambda: pg.locator("#tabHist tr.clicavel").count() >= 1, 10), "histórico lista a análise")
    pg.click("#tabHist tr.clicavel")
    confere(esperar(lambda: pg.is_visible("#detalheHist") and pg.locator("#tabDet tr").count() > 10, 10),
            "detalhe da análise com as WOs")
    pg.screenshot(path=str(PRINTS / "historico.png"), full_page=True)
    pg.click("#abaConfig")
    confere(esperar(lambda: pg.locator("#cfg_auditorias_por_wo").count() == 1, 10), "configurações carregam")
    caixa = lambda sel: pg.locator(sel).first.bounding_box()
    confere(abs(caixa("[aria-labelledby=tAparencia]")["y"] - caixa("[aria-labelledby=tFonte]")["y"]) < 2,
            "Aparência e Fonte de dados lado a lado")
    secoes = pg.locator("#cfgGrupos .cfg-secao")
    confere(secoes.count() == 4 and abs(secoes.nth(0).bounding_box()["y"] - secoes.nth(1).bounding_box()["y"]) < 2,
            "parâmetros em 2 colunas (Auditoria | Tempo)")
    larguras = {round(pg.locator(f"#cfg_{i}").evaluate("e => e.parentElement.getBoundingClientRect().width"))
                for i in ("auditorias_por_wo", "tempo_consulta_wo_seg", "tempo_max_lote_min", "concorrencia")}
    confere(len(larguras) == 1, f"campos numéricos com a mesma largura (com ou sem unidade): {larguras}")
    pg.mouse.wheel(0, 1500); pg.wait_for_timeout(300)
    confere(pg.locator("#cfgSalvar").bounding_box()["y"] < 120, "Salvar continua visível ao rolar (cabeçalho fixo)")
    pg.mouse.wheel(0, -1500)
    confere(pg.locator("#vistaConfig .campo .ajuda, #vistaConfig p.meta").count() == 0,
            "configurações sem parágrafos explicativos (só rótulo, campo e unidade)")
    confere(pg.locator("#vistaConfig .campo [data-ajuda^=cfg_]").count() == 16, "cada parâmetro tem '?' com a explicação")
    pg.click("[data-ajuda=cfg_concorrencia]")
    confere("Padrão: 4" in pg.inner_text("#dica"), "'?' do parâmetro mostra a regra e o valor padrão")
    pg.keyboard.press("Escape")
    pg.screenshot(path=str(PRINTS / "configuracoes.png"), full_page=True)
    pg.click("#cfgSalvar")
    confere(esperar(lambda: "salvas" in pg.inner_text("#cfgStatus"), 10), "configurações salvam")
    confere(pg.locator("#cfgMensagem option[value=ambas]").count() == 1 and pg.locator("#vistaConfig #bRaiox").count() == 1,
            "Configurações: mensagem 'As duas' e Raio-X da fonte")
    pg.select_option("#cfgMensagem", "ambas")
    confere(esperar(lambda: agente.estado()["fonte"]["mensagem_origem"] == "ambas", 10), "mensagem 'As duas' gravada na fonte")
    pg.select_option("#cfgTema", "escuro")
    fundo = pg.evaluate("getComputedStyle(document.body).backgroundColor")
    confere(pg.evaluate("document.documentElement.dataset.tema") == "escuro" and fundo == "rgb(15, 16, 18)", f"tema escuro aplicado ({fundo})")
    pg.screenshot(path=str(PRINTS / "config_escuro.png"), full_page=True)
    pg.reload(); pg.wait_for_timeout(800)
    confere(pg.evaluate("document.documentElement.dataset.tema") == "escuro", "tema lembrado depois de recarregar")
    pg.click("#abaAnalise"); pg.wait_for_timeout(800)
    pg.screenshot(path=str(PRINTS / "tela_escura.png"), full_page=True)
    pg.click("#abaHistorico"); pg.wait_for_timeout(500); pg.click("#tabHist tr.clicavel"); pg.wait_for_timeout(800)
    pg.screenshot(path=str(PRINTS / "historico_escuro.png"), full_page=True)
    pg.click("#abaConfig"); pg.wait_for_timeout(300)
    pg.select_option("#cfgTema", "claro")
    abrir_tour(pg)
    confere(pg.is_visible("#vistaAnalise") and pg.is_visible("#tutBalao"), "ajuda aberta em Configurações começa na tela de análise")
    pg.keyboard.press("Escape")
    confere(pg.is_visible("#vistaConfig"), "ao sair, volta para a página em que o usuário estava")

    print("== telas pequenas")
    for largura in (390, 768):
        pg.set_viewport_size({"width": largura, "height": 844})
        pg.click("#abaAnalise")
        pg.wait_for_timeout(400)
        confere(sem_rolagem_horizontal(pg), f"{largura}px: sem rolagem horizontal")
        lb = pg.locator(".lateral").bounding_box()
        confere((largura == 768 and round(lb["width"]) == 68) or (largura == 390 and lb["width"] >= 380 and lb["height"] < 70),
                f"{largura}px: menu adaptado ({'trilho' if largura == 768 else 'barra superior'})")
        abrir_tour(pg)
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
