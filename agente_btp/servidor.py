"""Interface web local (http://127.0.0.1:8765)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

from .agente import Agente, caminho_saida
from .entrada import ler_wos_planilha, ler_wos_texto

PASTA_WEB = Path(__file__).resolve().parent / "web"


def criar_app(agente: Agente) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

    @app.get("/")
    def index():
        return send_file(PASTA_WEB / "index.html")

    @app.get("/api/estado")
    def estado():
        return jsonify(agente.estado())

    @app.post("/api/conectar")
    def conectar():
        agente.enviar("conectar")
        return jsonify(ok=True)

    @app.post("/api/descoberta/iniciar")
    def descoberta_iniciar():
        agente.enviar("descoberta_iniciar")
        return jsonify(ok=True)

    @app.post("/api/descoberta/salvar")
    def descoberta_salvar():
        agente.enviar("descoberta_salvar")
        return jsonify(ok=True)

    @app.post("/api/executar")
    def executar():
        if agente.estado()["ocupado"]:
            return jsonify(ok=False, erro="O agente já está executando uma tarefa."), 409
        wos = ler_wos_texto(request.form.get("texto", ""))
        arquivo = request.files.get("arquivo")
        if arquivo and arquivo.filename:
            with tempfile.TemporaryDirectory() as pasta:
                caminho = Path(pasta) / "entrada.xlsx"
                arquivo.save(caminho)
                try:
                    wos += [w for w in ler_wos_planilha(caminho) if w not in wos]
                except Exception as exc:  # noqa: BLE001
                    return jsonify(ok=False, erro=f"Planilha inválida: {exc}"), 400
        if not wos:
            return jsonify(ok=False, erro="Informe ao menos uma WO."), 400
        agente.enviar("executar", wos)
        return jsonify(ok=True, total=len(wos))

    @app.post("/api/parar")
    def parar():
        agente.parar()
        return jsonify(ok=True)

    @app.get("/api/download/<nome>")
    def download(nome: str):
        caminho = caminho_saida(nome)
        if not caminho:
            abort(404)
        return send_file(caminho, as_attachment=True)

    return app
