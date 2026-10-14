"""Aprende a configuração da API observando as consultas reais que a tela do Monitor faz.

Raciocínio:
1. A consulta da LISTA é a que leva a WO na URL e devolve registros contendo a WO.
2. A consulta das MENSAGENS é a que devolve registros com campos de material,
   fornecedor, quantidade e mensagem.
3. Os valores da linha do alerta que aparecem na URL das mensagens viram marcadores
   {linha[Campo]}; a WO na URL da lista vira {wo}.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from . import odata
from .entrada import PADRAO_WO
from .campos import _VALOR, ESSENCIAIS, peso_nome, tipo_do_campo
from .modelos import ErroConsulta

_WO_LIVRE = re.compile(PADRAO_WO.pattern.strip("^$"))
_GET_BATCH = re.compile(r"^GET\s+(\S+)\s+HTTP/1\.[01]", re.M)
_STATUS_BATCH = re.compile(r"HTTP/1\.[01]\s+(\d{3})")
_PARAMS_PAGINACAO = {"$skip", "$top", "$skiptoken", "$inlinecount", "$count"}



class AnaliseFalhou(Exception):
    pass


@dataclass
class Chamada:
    ordem: int
    url: str            # caminho + query, decodificado, relativo à origem
    registros: list[dict]


# ---------------------------------------------------------------- extração
def expandir(captura: dict) -> list[Chamada]:
    """Transforma a captura em chamadas GET individuais (abre os $batch)."""
    chamadas: list[Chamada] = []
    for req in captura.get("requisicoes", []):
        partes = urlsplit(req["url"])
        caminho = unquote(partes.path) + ("?" + unquote(partes.query) if partes.query else "")
        if partes.path.endswith("$batch"):
            base = unquote(partes.path)[: -len("$batch")]
            pares = _abrir_batch(req.get("corpo_requisicao", ""), req.get("corpo_resposta", ""))
            for relativa, corpo in pares:
                url = relativa if relativa.startswith("/") else base + relativa
                _adicionar(chamadas, unquote(url), corpo)
        elif req.get("metodo") == "GET" and 200 <= req.get("status", 0) < 300:
            _adicionar(chamadas, caminho, req.get("corpo_resposta", ""))
    return chamadas


def _adicionar(chamadas: list[Chamada], url: str, corpo) -> None:
    if corpo is None:
        return
    try:
        texto = corpo if isinstance(corpo, str) else json.dumps(corpo)
        registros = odata.extrair_registros(texto)
    except ErroConsulta:
        return
    if "$metadata" in url:
        return
    chamadas.append(Chamada(len(chamadas), url, [r for r in registros if isinstance(r, dict)]))


def _abrir_batch(corpo_req: str, corpo_resp: str) -> list[tuple[str, object]]:
    corpo_req = corpo_req or ""
    if corpo_req.lstrip().startswith("{"):  # V4 JSON batch
        try:
            reqs = json.loads(corpo_req).get("requests", [])
            resps = {r.get("id"): r for r in json.loads(corpo_resp).get("responses", [])}
        except (json.JSONDecodeError, AttributeError):
            return []
        return [(r["url"], resps.get(r.get("id"), {}).get("body"))
                for r in reqs if r.get("method", "GET").upper() == "GET"
                and 200 <= resps.get(r.get("id"), {}).get("status", 0) < 300]
    # V2 multipart: i-ésimo GET <-> i-ésima resposta.
    urls = _GET_BATCH.findall(corpo_req)
    respostas = []
    for m in _STATUS_BATCH.finditer(corpo_resp or ""):
        status = int(m.group(1))
        inicio = corpo_resp.find("{", m.end())
        proxima = _STATUS_BATCH.search(corpo_resp, m.end())
        if inicio == -1 or (proxima and inicio > proxima.start()) or not 200 <= status < 300:
            respostas.append(None)
            continue
        try:
            dados, _ = json.JSONDecoder().raw_decode(corpo_resp, inicio)
            respostas.append(dados)
        except json.JSONDecodeError:
            respostas.append(None)
    return list(zip(urls, respostas))


# ---------------------------------------------------------------- análise
_DIGITOS = re.compile(r"^\d+$")
_NUMERO = re.compile(r"^-?\d+([.,]\d+)?$")


def _afinidade(nome: str, chave: str, valor) -> int:
    """Quanto o NOME e o VALOR combinam com a coluna.

    O nome manda: um campo cujo nome é da coluna (ex.: "fornecedor") sempre vence um campo
    que só tem o formato parecido (ex.: pedido de compra com 10 dígitos). Entre campos do
    mesmo tipo, o nome desempata (quantidadeFaltante vence quantidade total).
    """
    texto = str(valor).strip()
    if nome == "quantidade_faltante":
        valor_ok = isinstance(valor, (int, float)) and not isinstance(valor, bool) or bool(_NUMERO.match(texto))
    elif nome == "mensagem":
        valor_ok = bool(texto) and not _NUMERO.match(texto)
    elif nome in ("material", "fornecedor"):
        valor_ok = bool(_DIGITOS.match(texto)) or bool(texto)
    else:
        valor_ok = True
    if not valor_ok:
        return 0
    pelo_nome = 10 + peso_nome(nome, chave) if tipo_do_campo(chave) == nome else 0
    pelo_valor = 4 if nome in _VALOR and _VALOR[nome].match(texto) else 2 if (
        nome in ("material", "fornecedor") and _DIGITOS.match(texto)) else 1
    return pelo_nome + pelo_valor


def mapear_campos(registro: dict, wo: str) -> dict[str, str]:
    """Campo técnico de cada coluna, pelo nome (por partes) e pelo formato do valor."""
    campos: dict[str, str] = {}
    candidatos: dict[str, list[tuple[int, int, str]]] = {}
    for chave, valor in registro.items():
        if chave.startswith(("__", "@")) or isinstance(valor, (dict, list)):
            continue
        if str(valor) == wo:
            campos.setdefault("wo", chave)
            continue
        tipos = {tipo_do_campo(chave)} | {n for n, rx in _VALOR.items() if rx.match(str(valor).strip())}
        for nome in tipos - {None}:
            candidatos.setdefault(nome, []).append((_afinidade(nome, chave, valor), -len(chave), chave))
    usados = set(campos.values())
    for nome, opcoes in sorted(candidatos.items(), key=lambda kv: -max(kv[1])[0]):
        livres = [o for o in opcoes if o[2] not in usados and o[0] > 0]
        if nome not in campos and livres:
            campos[nome] = max(livres)[2]
            usados.add(campos[nome])
    return campos


def _pontos(campos: dict[str, str]) -> int:
    return sum(1 for c in ESSENCIAIS if c in campos)


def _modelo_lista(url: str, wo: str) -> str:
    caminho, _, query = url.partition("?")
    params = [p for p in query.split("&") if p and p.split("=", 1)[0] not in _PARAMS_PAGINACAO]
    limpa = caminho + ("?" + "&".join(params) if params else "")
    return _escapar_chaves(limpa).replace(wo, "{wo}")


def _modelo_mensagens(url: str, alerta: dict) -> tuple[str, int]:
    modelo = _escapar_chaves(url)
    trocas = 0
    candidatos = sorted(
        ((k, str(v)) for k, v in alerta.items()
         if not k.startswith(("__", "@")) and not isinstance(v, (dict, list)) and v is not None
         and len(str(v)) >= 4),
        key=lambda kv: -len(kv[1]))
    for chave, valor in candidatos:
        padrao = re.compile(r"(?<=['=(/,])" + re.escape(_escapar_chaves(valor)) + r"(?=['&),/]|$)")
        modelo, n = padrao.subn("{linha[" + chave + "]}", modelo)
        trocas += n
    return modelo, trocas


def _escapar_chaves(texto: str) -> str:
    return texto.replace("{", "{{").replace("}", "}}")


def analisar(captura: dict, wo: str | None = None) -> tuple[dict, list[str]]:
    """Devolve (configuração, relatório). Levanta AnaliseFalhou com o motivo."""
    chamadas = expandir(captura)
    relatorio = [f"{len(chamadas)} consulta(s) de dados encontradas na captura."]
    if not chamadas:
        raise AnaliseFalhou("Nenhuma consulta de dados foi capturada. Refaça a descoberta "
                            "executando o processo completo na aba do BTP enquanto a descoberta estiver ativa.")

    if not wo:
        for c in chamadas:
            m = _WO_LIVRE.search(c.url)
            if m:
                wo = m.group(0)
                break
    if not wo:
        raise AnaliseFalhou("Não encontrei nenhuma WO nas consultas. Ao refazer, filtre a lista pela WO.")
    relatorio.append(f"WO usada na descoberta: {wo}")

    lista = next((c for c in chamadas if wo in c.url
                  and any(str(v) == wo for r in c.registros for v in r.values())), None)
    if not lista:
        raise AnaliseFalhou(f"Não encontrei a consulta que filtra a lista pela WO {wo}.")
    relatorio.append(f"Consulta da lista: {lista.url[:160]}")

    config: dict = {"url_lista": _modelo_lista(lista.url, wo), "url_mensagens": None,
                    "campo_mensagens_expandidas": None}
    campos_lista = mapear_campos(lista.registros[0], wo)
    config["campo_consolidado_lista"] = campos_lista.get("consolidado", "")

    # Mensagens expandidas dentro da linha da lista?
    for chave, valor in lista.registros[0].items():
        filhos = valor.get("results") if isinstance(valor, dict) else valor
        if isinstance(filhos, list) and filhos and isinstance(filhos[0], dict):
            campos = mapear_campos(filhos[0], wo)
            if _pontos(campos) >= 3:
                config["campo_mensagens_expandidas"] = chave
                config["campos"] = campos
                relatorio.append(f"Mensagens vêm junto da lista, no campo '{chave}'.")
                break

    if not config["campo_mensagens_expandidas"]:
        melhor, melhor_campos, melhor_chave = None, {}, (-1, 0)
        for c in chamadas:
            if c is lista or not c.registros:
                continue
            campos = mapear_campos(c.registros[0], wo)
            chave = (_pontos(campos), 1 if c.ordem > lista.ordem else 0)
            if _pontos(campos) >= 3 and chave > melhor_chave:
                melhor, melhor_campos, melhor_chave = c, campos, chave
        if not melhor:
            raise AnaliseFalhou("Não encontrei a consulta da janela 'Mensagens'. Ao refazer, "
                                "clique na linha da WO até a janela abrir.")
        alerta = max(lista.registros, key=lambda r: _modelo_mensagens(melhor.url, r)[1])
        modelo, trocas = _modelo_mensagens(melhor.url, alerta)
        if trocas == 0:
            raise AnaliseFalhou("Encontrei a consulta das mensagens, mas não consegui relacioná-la "
                                "à linha do alerta. Envie o arquivo da descoberta para análise manual.")
        config["url_mensagens"] = modelo
        config["campos"] = melhor_campos
        relatorio.append(f"Consulta das mensagens: {melhor.url[:160]}")

    faltando = [c for c in ESSENCIAIS if c not in config["campos"]]
    if faltando:
        raise AnaliseFalhou("Campos não identificados nas mensagens: " + ", ".join(faltando)
                            + ". Envie o arquivo da descoberta para análise manual.")
    for nome, chave in config["campos"].items():
        relatorio.append(f"  {nome:>20} <- {chave}")
    return config, relatorio


if __name__ == "__main__":  # python -m agente_btp.analisador descoberta/captura_X.json [WO]
    import sys

    dados_captura = json.loads(open(sys.argv[1], encoding="utf-8").read())
    try:
        cfg_gerada, rel = analisar(dados_captura, sys.argv[2] if len(sys.argv) > 2 else None)
    except AnaliseFalhou as erro:
        sys.exit(f"FALHOU: {erro}")
    print("\n".join(rel))
    print(json.dumps(cfg_gerada, ensure_ascii=False, indent=2))
