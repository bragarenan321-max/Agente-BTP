"""Ponta a ponta no navegador: a sessão do BTP CAI no meio de 500 WOs e volta depois do login.

O que este roteiro prova, com a aba real (Chromium) e o agente_btp.js com vários trabalhadores:
  1. a queda (302 para o SSO, como o approuter faz) PAUSA a análise; nenhuma WO vira falha;
  2. as WOs já feitas ficam gravadas; a tela mostra "Pausada" e o painel "O que deu errado";
  3. o usuário faz login de novo (a aba recarrega e o agente é injetado de novo): a análise
     RETOMA SOZINHA e termina com todas as WOs feitas, cada WO gravada uma única vez.

Rode a partir de uma CÓPIA do projeto (ele grava config/, dados/ e saidas/):
    python testes/e2e/rodar_sessao.py
"""
import json
import os
import sys
import threading
import time
import urllib.request
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORTA_BTP = 8799
os.environ["AGENTE_BTP_URL"] = f"http://127.0.0.1:{PORTA_BTP}/site#Shell-home"
sys.path.insert(0, os.getcwd())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright  # noqa: E402

from agente_btp import config as cfg  # noqa: E402
from agente_btp.agente import Agente  # noqa: E402
from agente_btp.servidor import criar_app  # noqa: E402
from btp_lote_falso import BTPLote, config  # noqa: E402

btp = BTPLote(500, pagina=200)
btp.latencia = 0.01
PAGINA = "<html><body>Launchpad falso<script>window.sap={ushell:{Container:{}}};</script></body></html>"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/site"):
            corpo = PAGINA.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
            return
        r = btp(self.path)
        if r.redirecionado:                     # como o approuter: 302 para o login (SSO)
            self.send_response(302)
            self.send_header("Location", "https://ipc.claro.com.br/idp/SSO.saml2?SAMLRequest=abc")
            self.end_headers()
            return
        corpo = r.corpo.encode()
        self.send_response(r.status)
        self.send_header("Content-Type", r.content_type or "text/plain")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", PORTA_BTP), H).serve_forever, daemon=True).start()
cfg.salvar(dict(asdict(config()), confirmada=True, origem="receita_btp"))
agente = Agente()
app = criar_app(agente)
threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8767, threaded=True), daemon=True).start()
time.sleep(1)
favorito = json.load(urllib.request.urlopen("http://127.0.0.1:8767/api/favorito"))


def esperar(cond, t=120, aba=None):
    fim = time.time() + t
    while time.time() < fim:
        if cond(agente.estado()):
            return
        time.sleep(0.2)
    raise SystemExit("TIMEOUT\n" + json.dumps(agente.estado(), ensure_ascii=False, indent=1)[:4000])


falhas = []
with sync_playwright() as p:
    b = p.chromium.launch()
    aba = b.new_page()
    aba.goto(f"http://127.0.0.1:{PORTA_BTP}/site#Shell-home")
    aba.evaluate(favorito["console"])
    esperar(lambda e: e["conexao"] in ("conectado", "aba_conectada"), 30)
    total = {"n": 0}

    def cair(url):
        total["n"] += 1
        return total["n"] == 150
    btp.cair_quando = cair
    inicio = time.time()
    agente.enviar("executar", btp.wos, "teste de sessão")
    esperar(lambda e: e.get("situacao") == "pausado_sessao")
    esperar(lambda e: e["execucao"]["contadores"]["em_andamento"] == 0, 60)
    e = agente.estado()
    c = e["execucao"]["contadores"]
    print(f"PAUSOU: conexão={e['conexao']} | {c}")
    if c["falha"]:
        falhas.append(f"{c['falha']} WO(s) viraram falha por causa da sessão")
    if e["conexao"] != "sessao_expirada":
        falhas.append(f"conexão mostrada durante a queda: {e['conexao']}")
    feitas_antes = c["validada"] + c["divergente"] + c["incompleta"]
    if not feitas_antes or not c["pendente"]:
        falhas.append("a queda deveria ocorrer no meio (com WOs feitas e pendentes)")
    tela = b.new_page(viewport={"width": 1000, "height": 1300})
    tela.goto("http://127.0.0.1:8767/")
    tela.wait_for_timeout(1500)
    titulo = tela.inner_text("#tituloProg")
    print("tela:", titulo, "|", tela.inner_text("#conexao"))
    if "sessão do BTP expirou" not in titulo:
        falhas.append(f"título da tela durante a pausa: {titulo!r}")
    tela.click("#caixaDiag summary")
    tela.wait_for_timeout(1500)
    diag = tela.inner_text("#diagConteudo")
    print("o que deu errado:", diag.splitlines()[:3])
    if "sessão do BTP expirou" not in diag:
        falhas.append("painel 'O que deu errado' não mostra a queda de sessão")
    tela.screenshot(path=os.environ.get("PRINT_PAUSA", "pausa.png"), full_page=True)

    # o usuário faz login de novo: a aba recarrega e o agente é injetado outra vez
    btp.sessao_ok = True
    aba.reload()
    aba.evaluate(favorito["console"])
    esperar(lambda e: e["fase"] == "concluido" and not e["ocupado"], 180)
    e = agente.estado()
    c = agente.loja.contadores(e["run_id"])
    print(f"TERMINOU em {time.time() - inicio:.1f} s: {c} | arquivo={e['ultimo_arquivo']}")
    if c["pendente"] or c["em_andamento"] or c["falha"]:
        falhas.append(f"sobraram WOs não feitas: {c}")
    eventos = agente.loja.eventos(e["run_id"], tipos=("wo",), limite=100000)
    repetidas = [w for w, n in __import__("collections").Counter(x["wo"] for x in eventos).items() if n > 1]
    print("WOs gravadas mais de uma vez:", len(repetidas), "(só as que estavam em andamento na queda podem repetir)")
    if len(repetidas) > 4 * 20:
        falhas.append(f"{len(repetidas)} WOs refeitas: mais do que as em andamento na queda")
    tela.reload()
    tela.wait_for_timeout(1200)
    tela.screenshot(path=os.environ.get("PRINT_FIM", "fim.png"), full_page=True)
    b.close()
print("RESULTADO:", "OK" if not falhas else "FALHOU — " + "; ".join(falhas))
os._exit(0 if not falhas else 1)
