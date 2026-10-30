"""Mede o desempenho ANTES (versão 72c9acd) e DEPOIS, no BTP falso com latência por consulta.

    python testes/medir_desempenho.py [latencia_seg] [wos_versao_antiga] [wos_versao_nova]

Latência padrão 0,06 s por consulta: é o que sobra dos ~3,0–3,5 s/WO observados no BTP real
depois de descontar as pausas fixas da versão antiga (5 × 0,5 s). Respostas maiores custam um
pouco mais (0,5 ms por registro devolvido). A projeção para 13.623 WOs supõe que o BTP atende
4 consultas simultâneas com a mesma latência — isso só a medição real confirma.
"""
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "testes"))

from btp_lote_falso import BTPLote, config  # noqa: E402
from agente_btp.preferencias import Preferencias  # noqa: E402
from test_execucao import Ambiente  # noqa: E402

TOTAL_REAL = 13623


class ComLatencia(BTPLote):
    def __call__(self, url, accept=None, timeout=None):
        r = super().__call__(url, accept, timeout)
        try:
            n = len(json.loads(r.corpo).get("value", [])) if r.status == 200 else 0
        except (ValueError, AttributeError):
            n = 0
        time.sleep(self.latencia_base + 0.0005 * n)
        return r


def versao_antiga():
    destino = Path(tempfile.mkdtemp())
    pasta = destino / "agente_btp_antigo"
    pasta.mkdir()
    nomes = subprocess.run(["git", "-C", str(RAIZ), "ls-tree", "--name-only", "72c9acd", "agente_btp/"],
                           capture_output=True, text=True, check=True).stdout.split()
    for nome in nomes:
        if nome.endswith(".py"):
            (pasta / Path(nome).name).write_text(subprocess.run(
                ["git", "-C", str(RAIZ), "show", f"72c9acd:{nome}"], capture_output=True, text=True,
                check=True).stdout, encoding="utf-8")
    sys.path.insert(0, str(destino))
    return destino, [importlib.import_module(f"agente_btp_antigo.{m}")
                     for m in ("processador", "cliente", "config", "preferencias")]


def medir_antiga(latencia, n):
    destino, (proc, cli, cfgm, prefm) = versao_antiga()
    btp = ComLatencia(n)
    btp.latencia_base = latencia
    api = cfgm.a_partir_de_dict(asdict(config()))
    inicio = time.monotonic()
    proc.processar_lote(cli.ClienteBTP(api, btp), btp.wos, prefs=prefm.Preferencias())
    duracao = time.monotonic() - inicio
    shutil.rmtree(destino, ignore_errors=True)
    return duracao / n, btp.consultas / n


def medir_nova(latencia, n, prefs):
    btp = ComLatencia(n)
    btp.latencia_base = latencia
    amb = Ambiente(btp, prefs)
    inicio = time.monotonic()
    assert amb.rodar() == "concluida"
    duracao = time.monotonic() - inicio
    return duracao / n, btp.consultas / n, amb.cache.lote_ativo


def main():
    latencia = float(sys.argv[1]) if len(sys.argv) > 1 else 0.06
    n_antiga = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    n_nova = int(sys.argv[3]) if len(sys.argv) > 3 else 1000
    print(f"latência simulada: {latencia * 1000:.0f} ms por consulta (+0,5 ms por registro)\n")
    linhas = []
    s, c = medir_antiga(latencia, n_antiga)
    linhas.append(("ANTES (72c9acd): 5 auditorias, pausas fixas, 1 WO por vez", n_antiga, s, c, None))
    for rotulo, prefs in [
            ("DEPOIS sem lote: 2 auditorias (+3 em divergência), 4 em paralelo", Preferencias(tamanho_lote=1)),
            ("DEPOIS (padrão): lote de 20 WOs, 4 em paralelo", Preferencias())]:
        s, c, lote = medir_nova(latencia, n_nova, prefs)
        linhas.append((rotulo, n_nova, s, c, lote))
    print(f"{'cenário':66} {'WOs':>5} {'s/WO':>7} {'WO/s':>6} {'consultas/WO':>13} {'13.623 WOs':>11}")
    for rotulo, n, s, c, lote in linhas:
        projecao = s * TOTAL_REAL / 60
        print(f"{rotulo:66} {n:5d} {s:7.3f} {1 / s:6.1f} {c:13.2f} {projecao:8.0f} min"
              + ("" if lote in (None, True) else "  (lote desativado)"))


if __name__ == "__main__":
    main()
