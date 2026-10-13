"""Interface web local (http://127.0.0.1:8765)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from urllib.parse import urlsplit

from flask import Flask, Response, abort, jsonify, request, send_file

from .agente import Agente, caminho_saida
from .entrada import ler_wos_planilha, ler_wos_texto
from .navegador import URL_LAUNCHPAD

PASTA_WEB = Path(__file__).resolve().parent / "web"
_u = urlsplit(URL_LAUNCHPAD)
ORIGEM_SAP = f"{_u.scheme}://{_u.netloc}"


def codigo_carregador(origem_local: str) -> str:
    """JavaScript que busca o agente no programa local e o executa na aba do SAP."""
    return ("(async()=>{try{const r=await fetch('%s/agente_btp.js',{cache:'no-store',"
            "targetAddressSpace:'loopback'});(0,eval)(await r.text())}catch(e){alert('Agente BTP "
            "nao encontrado. Abra o programa (Abrir Agente BTP.bat), permita o acesso a rede local "
            "e clique de novo. '+e)}})()" % origem_local)


def criar_app(agente: Agente) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

    @app.get("/")
    def index():
        return send_file(PASTA_WEB / "index.html")

    # ------------------------------------------------ canal com a aba do SAP
    @app.after_request
    def cors(resposta):
        if request.path == "/agente_btp.js" or request.path.startswith("/api/agente/"):
            resposta.headers["Access-Control-Allow-Origin"] = ORIGEM_SAP
            resposta.headers["Access-Control-Allow-Private-Network"] = "true"
            resposta.headers["Access-Control-Allow-Local-Network"] = "true"
            resposta.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resposta.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resposta.headers["Vary"] = "Origin"
        return resposta

    def exigir_origem_sap():
        # O navegador sempre envia Origin em pedidos de outra origem; nenhum outro
        # site consegue se passar pela aba do SAP.
        if request.headers.get("Origin") != ORIGEM_SAP:
            abort(403)

    @app.get("/agente_btp.js")
    def agente_js():
        origem_local = request.host_url.rstrip("/")
        codigo = (PASTA_WEB / "agente_btp.js").read_text(encoding="utf-8")
        corpo = f"window.__BTP_SERVIDOR = '{origem_local}';\n" + codigo
        return Response(corpo, mimetype="application/javascript",
                        headers={"Cache-Control": "no-store"})

    @app.post("/api/agente/ola")
    def agente_ola():
        exigir_origem_sap()
        dados = request.get_json(force=True, silent=True) or {}
        agente.ponte.registrar_agente(str(dados.get("agente", "")), str(dados.get("pagina", "")))
        agente.aba_conectada()
        return jsonify(ok=True)

    @app.get("/api/agente/tarefa")
    def agente_tarefa():
        exigir_origem_sap()
        return jsonify(agente.ponte.proxima_tarefa(
            request.args.get("agente", ""), request.args.get("autenticado") == "1",
            request.args.get("pagina", ""), request.args.get("capturando") == "1"))

    @app.post("/api/agente/resposta")
    def agente_resposta():
        exigir_origem_sap()
        agente.ponte.entregar_resposta(request.get_json(force=True, silent=True) or {})
        return jsonify(ok=True)

    @app.post("/api/agente/captura")
    def agente_captura():
        exigir_origem_sap()
        dados = request.get_json(force=True, silent=True) or {}
        if dados.get("agente") == agente.ponte.agente_id:
            agente.ponte.adicionar_captura(dados.get("itens") or [])
        return jsonify(ok=True)

    @app.get("/api/favorito")
    def favorito():
        codigo = codigo_carregador(request.host_url.rstrip("/"))
        return jsonify(favorito="javascript:" + codigo, console=codigo)

    @app.post("/api/usar_aba")
    def usar_aba():
        agente.enviar("usar_aba")
        return jsonify(ok=True)

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
        agente.salvar_descoberta()
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
