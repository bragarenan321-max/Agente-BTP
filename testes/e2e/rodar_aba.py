"""Ponta a ponta: aba do SAP (falso) já aberta + favorito + Agente BTP, SEM etapa manual.

Cenário A: o agente se prepara sozinho pelo dicionário de dados ($metadata).
Cenário B: sem dicionário disponível, aprende observando o usuário usar o Monitor.

Rode a partir de uma CÓPIA do projeto (ele grava config/ e saidas/):
    python testes/e2e/rodar_aba.py
"""
import json
import os
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
os.environ["AGENTE_BTP_URL"] = "http://127.0.0.1:8798/site#Shell-home"
import sap_falso  # noqa: E402

sap_falso.iniciar(8798)
sys.path.insert(0, os.getcwd())
from openpyxl import load_workbook  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from agente_btp import config as cfg  # noqa: E402
from agente_btp.agente import PASTA_SAIDAS, Agente  # noqa: E402
from agente_btp.servidor import criar_app  # noqa: E402

agente = Agente()
app = criar_app(agente)
threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8767, threaded=True), daemon=True).start()
time.sleep(1)
favorito = json.load(urllib.request.urlopen("http://127.0.0.1:8767/api/favorito"))
WOS = ["88412|126579742", "42340|231184065", "02235|230771207", "123"]


SO = os.environ.get("CENARIOS", "ABCDE")


def cenario(nome, url_aba, b):
    if nome[0] not in SO:
        return
    print(f"\n===== {nome} =====")
    cfg.ARQUIVO_CONFIG.unlink(missing_ok=True)
    aba = b.new_page()
    aba.goto(url_aba)
    aba.evaluate(favorito["console"])            # = clicar no favorito

    def esperar(cond, t=90):
        fim = time.time() + t
        while time.time() < fim:
            if cond(agente.estado()):
                return
            aba.wait_for_timeout(300)
        raise SystemExit("TIMEOUT\n" + json.dumps(agente.estado(), ensure_ascii=False, indent=1))

    esperar(lambda e: e["conexao"] == "conectado")
    agente.enviar("executar", WOS)               # o usuário só clica em Executar
    aba.wait_for_timeout(500)
    esperar(lambda e: e["fase"] == "concluido" and not e["ocupado"])
    e = agente.estado()
    print("origem da configuração:", json.loads(cfg.ARQUIVO_CONFIG.read_text())["origem"])
    print("contagem:", e["contagem"], "| aviso:", e["aviso"] or "-")
    for teste in agente._diag.get("dicionario", {}).get("testes", []):
        print("   teste:", teste["nota"], teste["lista"].split("/")[-1][:40], "|",
              str(teste.get("mensagens") or teste.get("sondagem")).split("/")[-2:])
    wb = load_workbook(PASTA_SAIDAS / e["ultimo_arquivo"])
    print("arquivo:", e["ultimo_arquivo"], "| abas:", wb.sheetnames)
    for linha in wb["Itens faltantes"].iter_rows(values_only=True):
        print("  ", linha)
    aba.close()


with sync_playwright() as p:
    b = p.chromium.launch()
    cenario("A: dicionário de dados", "http://127.0.0.1:8798/site#Shell-home", b)
    sap_falso.ESTADO["sem_metadata"] = True        # B: dicionário bloqueado -> só a observação resolve
    cenario("B: observando o Monitor", "http://127.0.0.1:8798/site?semfontes=1&simular=1#Shell-home", b)
    sap_falso.ESTADO["sem_metadata"] = False
    cenario("C: OData V4, nomes diferentes, navegação e conjunto-isca",
            "http://127.0.0.1:8798/site?cap=1#Shell-home", b)
    cenario("D: caso real — filtro óbvio vazio; mensagens pelo link de cada linha",
            "http://127.0.0.1:8798/site?srv=srv3#Shell-home", b)
    cenario("E: caso real — mensagens fora do dicionário; aprende com o clique em 'Mensagens'",
            "http://127.0.0.1:8798/site?srv=srv4&simular=srv4#Shell-home", b)
    # Histórico: as duas análises aparecem; apagar uma remove também o Excel dela.
    hist = json.load(urllib.request.urlopen("http://127.0.0.1:8767/api/historico"))
    print("\nhistórico:", [(h["situacao"], h["total"], h["contagem"].get("Com itens faltantes")) for h in hist])
    detalhe = json.load(urllib.request.urlopen(f"http://127.0.0.1:8767/api/historico/{hist[0]['id']}"))
    print("detalhe WOs:", [(w["wo"], w["status"]) for w in detalhe["wos"]])
    ui = b.new_page(viewport={"width": 1100, "height": 1000})
    ui.goto("http://127.0.0.1:8767/"); ui.wait_for_timeout(800)
    ui.click("#abaHistorico"); ui.wait_for_timeout(600)
    ui.click("#tabHist tr.clicavel"); ui.wait_for_timeout(800)
    ui.screenshot(path=os.environ.get("PRINT_HIST", "historico.png"), full_page=True)
    excel = PASTA_SAIDAS / hist[1]["arquivo"]
    req = urllib.request.Request(f"http://127.0.0.1:8767/api/historico/{hist[1]['id']}", method="DELETE")
    print("apagar:", json.load(urllib.request.urlopen(req)), "| excel ainda existe:", excel.exists())
    print("restantes:", len(json.load(urllib.request.urlopen("http://127.0.0.1:8767/api/historico"))))
    b.close()
os._exit(0)
