"""Ponta a ponta do MODO ABA: aba do SAP (falso) já aberta + favorito + Agente BTP.

Rode a partir de uma CÓPIA do projeto (ele grava config/, descoberta/ e saidas/):
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

from agente_btp.agente import PASTA_SAIDAS, Agente  # noqa: E402
from agente_btp.servidor import criar_app  # noqa: E402

agente = Agente()
app = criar_app(agente)
threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8767, threaded=True), daemon=True).start()
time.sleep(1)

# Origem diferente do SAP é recusada.
req = urllib.request.Request("http://127.0.0.1:8767/api/agente/tarefa?agente=x",
                             headers={"Origin": "https://site-malicioso.com"})
try:
    urllib.request.urlopen(req)
    raise SystemExit("FALHA: origem estranha foi aceita")
except urllib.error.HTTPError as e:
    print("origem estranha recusada:", e.code)

favorito = json.load(urllib.request.urlopen("http://127.0.0.1:8767/api/favorito"))

with sync_playwright() as p:
    b = p.chromium.launch()
    aba = b.new_page()
    aba.goto("http://127.0.0.1:8798/site#Shell-home")   # o usuário já está com a aba aberta
    aba.evaluate(favorito["console"])                    # = clicar no favorito

    def esperar(cond, t=60):
        fim = time.time() + t
        while time.time() < fim:
            if cond(agente.estado()):
                return
            aba.wait_for_timeout(300)
        raise SystemExit("TIMEOUT\n" + "\n".join(agente.estado()["mensagens"]))

    esperar(lambda e: e["conexao"] == "conectado")
    print("selo na aba:", aba.inner_text("#agente-btp-selo"))
    agente.enviar("descoberta_iniciar")
    esperar(lambda e: e["descoberta_ativa"])
    aba.wait_for_timeout(5000)
    print("selo na aba:", aba.inner_text("#agente-btp-selo"))
    agente.salvar_descoberta()
    esperar(lambda e: not e["ocupado"] and not e["descoberta_ativa"])

    agente.enviar("executar", ["88412|126579742", "42340|231184065", "02235|230771207", "123"])
    aba.wait_for_timeout(1000)
    esperar(lambda e: not e["ocupado"] and e["ultimo_arquivo"])
    arquivo = PASTA_SAIDAS / agente.estado()["ultimo_arquivo"]

    sap_falso.ESTADO["expirada"] = True                  # a sessão do SAP cai
    agente.enviar("executar", ["88412|126579742", "02235|230771207"])
    aba.wait_for_timeout(1000)
    esperar(lambda e: not e["ocupado"] and e["ultimo_arquivo"] != arquivo.name)
    arquivo2 = PASTA_SAIDAS / agente.estado()["ultimo_arquivo"]
    b.close()

print("\n".join(agente.estado()["mensagens"]))
for arq in (arquivo, arquivo2):
    wb = load_workbook(arq)
    for aba_ in wb.sheetnames:
        print(f"--- {arq.name} / {aba_}")
        for linha in wb[aba_].iter_rows(values_only=True):
            print(linha)
os._exit(0)
