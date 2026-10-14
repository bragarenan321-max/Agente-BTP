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


def cenario(nome, url_aba, b):
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
    wb = load_workbook(PASTA_SAIDAS / e["ultimo_arquivo"])
    for linha in wb["Resultado"].iter_rows(values_only=True):
        print("  ", linha)
    aba.close()


with sync_playwright() as p:
    b = p.chromium.launch()
    cenario("A: dicionário de dados", "http://127.0.0.1:8798/site#Shell-home", b)
    cenario("B: observando o Monitor", "http://127.0.0.1:8798/site?semfontes=1&simular=1#Shell-home", b)
    b.close()
os._exit(0)
