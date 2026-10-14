"""Interface web local (http://127.0.0.1:8767) e canal com a aba do SAP."""
from __future__ import annotations

import hashlib
import os
import json
import tempfile
from pathlib import Path

from urllib.parse import urlsplit

from flask import Flask, Response, abort, jsonify, request, send_file

from . import historico, preferencias
from .agente import Agente, caminho_saida
from .entrada import ler_wos_planilha, ler_wos_texto
from .config import URL_LAUNCHPAD

PASTA_WEB = Path(__file__).resolve().parent / "web"
_u = urlsplit(URL_LAUNCHPAD)
ORIGEM_SAP = f"{_u.scheme}://{_u.netloc}"


def codigo_carregador(origem_local: str) -> str:
    """JavaScript que busca o agente no programa local e o executa na aba do SAP."""
    return ("(async()=>{try{const r=await fetch('%s/agente_btp.js',{cache:'no-store',"
            "targetAddressSpace:'loopback'});(0,eval)(await r.text())}catch(e){alert('Agente BTP "
            "nao encontrado. Abra o programa (Abrir Agente BTP.bat), permita o acesso a rede local "
            "e clique de novo. '+e)}})()" % origem_local)


PASTA_EXTENSAO = PASTA_WEB.parent.parent / "extensao"


def sincronizar_extensao(codigo_agente: str) -> None:
    """A extensão leva uma cópia do agente; ao abrir o programa, garante que é a mesma."""
    destino = PASTA_EXTENSAO / "agente_btp.js"
    try:
        if PASTA_EXTENSAO.is_dir() and (not destino.exists()
                                         or destino.read_text(encoding="utf-8") != codigo_agente):
            destino.write_text(codigo_agente, encoding="utf-8")
    except OSError:
        pass


def criar_app(agente: Agente) -> Flask:
    app = Flask(__name__)
    codigo_agente = (PASTA_WEB / "agente_btp.js").read_text(encoding="utf-8")
    sincronizar_extensao(codigo_agente)
    # Versão = impressão digital de todo o programa: qualquer mudança gera outra versão.
    digital = hashlib.sha1()
    for arq in sorted(PASTA_WEB.parent.glob("*.py")) + sorted(PASTA_WEB.glob("*")):
        digital.update(arq.read_bytes())
    versao = digital.hexdigest()[:10]
    agente.ponte.versao_agente = versao
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

    @app.get("/")
    def index():
        html = (PASTA_WEB / "index.html").read_text(encoding="utf-8")
        return Response(html.replace("__VERSAO_PROGRAMA__", versao), mimetype="text/html",
                        headers={"Cache-Control": "no-store"})

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
        corpo = (f"window.__BTP_SERVIDOR = '{origem_local}';\nwindow.__BTP_VERSAO = '{versao}';\n"
                 + codigo_agente)
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
            request.args.get("pagina", ""), request.args.get("capturando") == "1",
            versao=request.args.get("versao", "")))

    @app.post("/api/agente/tchau")
    def agente_tchau():
        exigir_origem_sap()
        dados = request.get_json(force=True, silent=True) or {}
        agente.ponte.despedir(str(dados.get("agente", "")))
        return jsonify(ok=True)

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

    @app.post("/api/extensao/abrir")
    def abrir_pasta_extensao():
        """Abre no Explorer a pasta da extensão (para 'Carregar sem compactação')."""
        pasta = PASTA_WEB.parent.parent / "extensao"
        if os.name == "nt":
            try:
                os.startfile(str(pasta))  # noqa: S606 — só a pasta do próprio programa
            except OSError:
                pass
        return jsonify(caminho=str(pasta))

    @app.get("/api/estado")
    def estado():
        return jsonify(dict(agente.estado(), versao=versao))

    @app.post("/api/executar")
    def executar():
        if agente.estado()["ocupado"]:
            return jsonify(ok=False, erro="O agente já está executando uma análise."), 409
        wos = ler_wos_texto(request.form.get("texto", ""))
        partes = [f"{len(wos)} WO(s) coladas"] if wos else []
        arquivo = request.files.get("arquivo")
        if arquivo and arquivo.filename:
            with tempfile.TemporaryDirectory() as pasta:
                caminho = Path(pasta) / "entrada.xlsx"
                arquivo.save(caminho)
                try:
                    da_planilha = ler_wos_planilha(caminho)
                except Exception as exc:  # noqa: BLE001
                    return jsonify(ok=False, erro=f"Planilha inválida: {exc}"), 400
            novas = [w for w in da_planilha if w not in wos]
            wos += novas
            partes.append(f"planilha {arquivo.filename} ({len(da_planilha)} WOs)")
        if not wos:
            return jsonify(ok=False, erro="Informe ao menos uma WO."), 400
        agente.enviar("executar", wos, " + ".join(partes))
        return jsonify(ok=True, total=len(wos))

    # ------------------------------------------------ configurações (engrenagem)
    @app.get("/api/configuracoes")
    def ler_configuracoes():
        return jsonify(valores=preferencias.como_dict(preferencias.carregar()),
                       padrao=preferencias.como_dict(preferencias.Preferencias()))

    @app.post("/api/configuracoes")
    def gravar_configuracoes():
        if agente.estado()["ocupado"]:
            return jsonify(ok=False, erro="Aguarde a análise em andamento terminar."), 409
        prefs, problemas = preferencias.salvar(request.get_json(force=True, silent=True) or {})
        return jsonify(ok=True, valores=preferencias.como_dict(prefs), problemas=problemas)

    @app.post("/api/configuracoes/padrao")
    def restaurar_configuracoes():
        if agente.estado()["ocupado"]:
            return jsonify(ok=False, erro="Aguarde a análise em andamento terminar."), 409
        prefs, _ = preferencias.salvar({})
        return jsonify(ok=True, valores=preferencias.como_dict(prefs), problemas=[])

    @app.get("/api/historico")
    def lista_historico():
        return jsonify(historico.listar())

    @app.get("/api/historico/<id_>")
    def item_historico(id_: str):
        item = historico.obter(id_)
        if not item:
            abort(404)
        return jsonify(item)

    def _apagar(ids):
        removidas = historico.apagar(ids)
        for a in removidas:                    # a planilha da análise vai junto
            caminho = caminho_saida(a.get("arquivo") or "")
            if caminho:
                caminho.unlink(missing_ok=True)
        return jsonify(ok=True, apagadas=len(removidas))

    @app.delete("/api/historico/<id_>")
    def apagar_historico(id_: str):
        return _apagar([id_])

    @app.delete("/api/historico")
    def limpar_historico():
        if agente.estado()["ocupado"]:
            return jsonify(ok=False, erro="Aguarde a análise em andamento terminar."), 409
        return _apagar(None)

    @app.get("/api/diagnostico")
    def diagnostico():
        corpo = json.dumps(agente.diagnostico(), ensure_ascii=False, indent=1)
        return Response(corpo, mimetype="application/json", headers={
            "Content-Disposition": "attachment; filename=diagnostico_agente_btp.json"})

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
