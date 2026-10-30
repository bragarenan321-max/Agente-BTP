"""Ponto de entrada: python -m agente_btp"""
from __future__ import annotations

import logging
import os
import threading
import webbrowser

from .agente import RAIZ, Agente
from .servidor import criar_app

# 8767: não colide com o Agente Atlas (8765), que pode estar aberto ao mesmo tempo.
HOST, PORTA = "127.0.0.1", 8767


def configurar_log() -> None:
    """Registro só em ARQUIVO (logs/agente_btp.log), nunca na janela preta.

    No Windows, um clique na janela preta a coloca em modo de seleção; enquanto isso, qualquer
    escrita nela CONGELA o programa (a análise parava no meio, ex.: 391 de 499 WOs).
    """
    pasta = RAIZ / "logs"
    pasta.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(pasta / "agente_btp.log", encoding="utf-8")],
    )
    logging.getLogger("werkzeug").setLevel(logging.ERROR)


def desligar_selecao_da_janela() -> None:
    """Desliga o modo de seleção (QuickEdit) só desta janela do Windows, para nada congelar."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        entrada = kernel32.GetStdHandle(-10)          # STD_INPUT_HANDLE
        modo = ctypes.c_uint32()
        if kernel32.GetConsoleMode(entrada, ctypes.byref(modo)):
            kernel32.SetConsoleMode(entrada, (modo.value & ~0x0040) | 0x0080)   # -QUICK_EDIT +EXTENDED_FLAGS
    except Exception:  # noqa: BLE001 — sem console (pythonw) ou API indisponível: nada a fazer
        pass


def main() -> None:
    desligar_selecao_da_janela()
    configurar_log()
    app = criar_app(Agente())
    url = f"http://{HOST}:{PORTA}"
    logging.getLogger("agente_btp").info("Agente BTP iniciado em %s", url)
    print(f"Agente BTP aberto em {url}\nNão feche esta janela enquanto usar o agente. "
          "O registro detalhado fica em logs\\agente_btp.log.", flush=True)
    threading.Timer(1.0, webbrowser.open, args=[url]).start()
    import flask.cli
    flask.cli.show_server_banner = lambda *a, **k: None   # nada escrito na janela durante o uso
    app.run(host=HOST, port=PORTA, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
