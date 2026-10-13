"""Ponto de entrada: python -m agente_btp"""
from __future__ import annotations

import logging
import threading
import webbrowser

from .agente import RAIZ, Agente
from .servidor import criar_app

HOST, PORTA = "127.0.0.1", 8765


def configurar_log() -> None:
    pasta = RAIZ / "logs"
    pasta.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(pasta / "agente_btp.log", encoding="utf-8"),
                  logging.StreamHandler()],
    )
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def main() -> None:
    configurar_log()
    app = criar_app(Agente())
    url = f"http://{HOST}:{PORTA}"
    logging.getLogger("agente_btp").info("Agente BTP iniciado em %s", url)
    threading.Timer(1.0, webbrowser.open, args=[url]).start()
    app.run(host=HOST, port=PORTA, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
