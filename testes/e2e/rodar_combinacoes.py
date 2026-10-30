"""Reconexões em laço? Roda PROGRAMA x EXTENSÃO (possivelmente de versões diferentes)
num navegador real, contra o BTP falso, e conta quantas vezes a aba se registrou.

Uso: python rodar_combinacoes.py <pasta_do_programa> <pasta_da_extensao> [segundos]
Saída: 'registros=N' (o normal é 1 ou 2; laço = dezenas/centenas).
"""
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

PROGRAMA, EXTENSAO = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
DURACAO = float(sys.argv[3]) if len(sys.argv) > 3 else 20
sys.path.insert(0, str(Path(__file__).parent))
os.environ["AGENTE_BTP_URL"] = "http://127.0.0.1:8798/site#Shell-home"
import sap_falso  # noqa: E402

sap_falso.iniciar(8798)
sys.path.insert(0, str(PROGRAMA))
os.chdir(PROGRAMA)
from playwright.sync_api import sync_playwright  # noqa: E402

from agente_btp.agente import Agente  # noqa: E402
from agente_btp.servidor import criar_app  # noqa: E402

agente = Agente()
registros = []
original = agente.ponte.registrar_agente
agente.ponte.registrar_agente = lambda a, p: (registros.append(a), original(a, p))
app = criar_app(agente)
threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8767, threaded=True), daemon=True).start()
time.sleep(1)

ext = Path(tempfile.mkdtemp()) / "ext"
shutil.copytree(EXTENSAO, ext)
for arq in ext.glob("*"):
    if arq.suffix in (".js", ".json"):
        arq.write_text(re.sub(r"https://claro-prd\.launchpad\.cfapps\.br10\.hana\.ondemand\.com",
                              "http://127.0.0.1:8798", arq.read_text(encoding="utf-8")), encoding="utf-8")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        tempfile.mkdtemp(), headless=False,
        args=["--headless=new", f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
    aba = ctx.new_page()
    aba.goto("http://127.0.0.1:8798/site#Shell-home")
    aba.wait_for_timeout(int(DURACAO * 1000))
    selo = aba.inner_text("#agente-btp-selo") if aba.locator("#agente-btp-selo").count() else "(sem selo)"
    print(f"registros={len(registros)} | conexao={agente.estado()['conexao']} | selo={selo}")
    ctx.close()
os._exit(0)
