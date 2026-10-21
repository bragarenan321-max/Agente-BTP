"""BTP falso para lotes grandes: Pendencias + UltimoRetornoView com $filter avaliado DE VERDADE.

Imita o caminho confirmado no BTP real (Pendencias -> UltimoRetornoView, status 'E'):
  - $filter com eq / and / or / parênteses e aspas escapadas ('');
  - paginação do servidor (@odata.nextLink) acima de `pagina` registros;
  - leitura de UM item pela chave: UltimoRetornoView(consolidID='..',zeile='..');
  - queda de sessão programável (redirecionamento, 401, 403 ou HTML de login com status 200);
  - HTTP 429 / 503 programáveis; latência por consulta (para medir desempenho).
Dados determinísticos (semente), com zeros à esquerda, consolidados com + / e ', WOs fora do
Monitor, alertas sem itens, dois alertas por WO, linhas da lista repetidas e linhas "Ok" (status O).
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from urllib.parse import parse_qsl, quote, unquote, urlsplit

from agente_btp import config as cfg
from agente_btp.autoconfig import receita_monitor
from agente_btp.cliente import Resposta

SRV = "/MonitorPendenciashtml5.comsapbuildstandardmonitorDePendencias/work-order/"
MENSAGENS = ["Estoque indisponível", "Saldo insuficiente no depósito 0001", "Material bloqueado para baixa",
             "Lote 'A' vencido", "Erro durante baixa: centro=BR01/1000"]
LOGIN_HTML = ('<html><body><form method="post" action="https://ipc.claro.com.br/idp/SSO.saml2">'
              '<input type="hidden" name="SAMLRequest" value="PHNhbWxwOkF1dGhuUmVx"/></form></body></html>')


# ------------------------------------------------------------------ avaliador de $filter
_TOKEN = re.compile(r"\s*(\(|\)|'(?:[^']|'')*'|-?\d+(?:\.\d+)?|[\w/]+)")


def _tokens(expr: str) -> list[str]:
    pos, saida = 0, []
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m:
            if expr[pos:].strip() == "":
                break
            raise ValueError(f"filtro inválido perto de: {expr[pos:pos + 30]!r}")
        saida.append(m.group(1))
        pos = m.end()
    return saida


def compilar(expr: str):
    """Filtro OData -> função(registro) -> bool (analisado UMA vez por consulta)."""
    toks = _tokens(expr)
    i = 0

    def termo():
        nonlocal i
        if toks[i] == "(":
            i += 1
            v = ou()
            assert toks[i] == ")", "parêntese"
            i += 1
            return v
        campo, op, valor = toks[i], toks[i + 1], toks[i + 2]
        i += 3
        assert op == "eq", op
        if valor.startswith("'"):
            texto = valor[1:-1].replace("''", "'")
            return lambda r: r.get(campo) is not None and str(r.get(campo)) == texto
        numero = float(valor)
        return lambda r: r.get(campo) is not None and float(r.get(campo)) == numero

    def e():
        nonlocal i
        partes = [termo()]
        while i < len(toks) and toks[i] == "and":
            i += 1
            partes.append(termo())
        return partes[0] if len(partes) == 1 else (lambda r: all(p(r) for p in partes))

    def ou():
        nonlocal i
        partes = [e()]
        while i < len(toks) and toks[i] == "or":
            i += 1
            partes.append(e())
        return partes[0] if len(partes) == 1 else (lambda r: any(p(r) for p in partes))
    funcao = ou()
    assert i == len(toks), f"sobrou: {toks[i:]}"
    return funcao


def avaliar(expr: str, registro: dict) -> bool:
    return compilar(expr)(registro)


# ------------------------------------------------------------------ dados
def gerar(n: int, semente: int = 7):
    rng = random.Random(semente)
    wos, pend, ret, vistos = [], [], [], set()
    while len(wos) < n:
        w = f"{rng.randint(0, 99999):05d}|{rng.randint(0, 999999999):09d}"
        if w not in vistos:
            vistos.add(w)
            wos.append(w)
    tipos = {}
    for i, w in enumerate(wos):
        tipo = ["fora", "sem_itens", "dois_alertas", "lista_repetida", "normal", "normal", "normal",
                "normal", "normal", "normal"][i % 10]
        tipos[w] = tipo
        if tipo == "fora":
            continue
        alertas = 2 if tipo == "dois_alertas" else 1
        for a in range(alertas):
            sufixo = "".join(rng.choice("abcXYZ019+/") for _ in range(8))
            consol = f"{rng.randint(100000, 999999)}-20260916-{sufixo}" + ("'Q" if i % 37 == 0 else "")
            linha = {"ID": f"p{i}-{a}", "workOrderID": w, "consolidID": consol, "status": "Pendente",
                     "mensagem": "Erro durante baixa de estoque. Clique para mais detalhes"}
            pend.append(linha)
            if tipo == "lista_repetida":
                pend.append(dict(linha, ID=f"p{i}-{a}-r"))
            for z in range(rng.randint(0, 2)):        # linhas "Ok" (não aparecem na janela Mensagens)
                ret.append({"consolidID": consol, "workOrderID": w, "status": "O", "zeile": f"{900 + z:04d}",
                            "material": f"{rng.randint(1, 99999999):018d}", "fornecedorID": "0000148963",
                            "quantidade": "6.8e+1", "quantidadeDoc": "6.8e+1", "mensagem": "Ok",
                            "aplicacao": "OPEX", "contador": 2})
            if tipo == "sem_itens":
                continue
            for z in range(rng.randint(1, 4)):
                ret.append({"consolidID": consol, "workOrderID": w, "status": "E", "zeile": f"{(z + 1) * 10:04d}",
                            "material": f"{rng.randint(1, 99999999):018d}",
                            "fornecedorID": f"{rng.randint(1, 9999999):010d}",
                            "quantidade": rng.choice(["1e+0", "2e+0", "1.5e+0", "1.2e+1"]), "quantidadeDoc": "0e+0",
                            "mensagem": rng.choice(MENSAGENS), "aplicacao": "OPEX", "contador": 2})
    return wos, pend, ret, tipos


def config(com_chave: bool = False):
    modelo = {"conjuntos": {
        "Pendencias": {"props": {"ID": "Edm.String", "workOrderID": "Edm.String", "consolidID": "Edm.String",
                                 "status": "Edm.String", "mensagem": "Edm.String"}},
        "UltimoRetornoView": {"props": {k: "Edm.String" for k in (
            "consolidID", "workOrderID", "status", "material", "fornecedorID", "quantidade", "quantidadeDoc",
            "mensagem", "zeile", "aplicacao")}}}}
    dados = receita_monitor(SRV, modelo)
    if com_chave:
        dados.update(url_item=SRV + "UltimoRetornoView(consolidID='{linha[consolidID]}',zeile='{linha[zeile]}')",
                     chaves_item=["consolidID", "zeile"])
    return cfg.a_partir_de_dict(dict(dados, confirmada=True))


class BTPLote:
    def __init__(self, n: int = 500, semente: int = 7, pagina: int = 1000, latencia: float = 0.0):
        self.wos, self.pendencias, self.retorno, self.tipos = gerar(n, semente)
        self.pagina, self.latencia = pagina, latencia
        self.sessao_ok = True
        self.modo_queda = "redirecionar"      # redirecionar | 401 | 403 | html
        self.cair_quando = None               # função(url) -> True para derrubar a sessão nessa consulta
        self.status_por_wo: dict[str, int] = {}   # WO -> status HTTP forçado (ex.: 503, 403)
        self.erros_429 = 0
        self.volateis: set[str] = set()       # WOs cuja quantidade muda a cada leitura
        self.consultas = 0
        self.urls: list[str] = []
        self._trava = threading.Lock()
        self._leituras_volateis = 0

    def __call__(self, url, accept=None, timeout=None):
        with self._trava:
            self.consultas += 1
            self.urls.append(url)
            if self.cair_quando and self.cair_quando(url):
                self.sessao_ok = False
            forcar_429 = self.erros_429 > 0
            if forcar_429:
                self.erros_429 -= 1
        if self.latencia:
            time.sleep(self.latencia)
        if not self.sessao_ok:
            return {"redirecionar": Resposta(0, "", "", redirecionado=True),
                    "401": Resposta(401, "application/json", '{"error":"unauthorized"}'),
                    "403": Resposta(403, "text/plain", "Forbidden"),
                    "html": Resposta(200, "text/html; charset=utf-8", LOGIN_HTML)}[self.modo_queda]
        if forcar_429:
            return Resposta(429, "text/plain", "Too Many Requests", retry_after="0")
        c = unquote(url)
        for wo, status in self.status_por_wo.items():
            if f"'{wo}'" in c:
                return Resposta(status, "text/plain", f"erro {status}")
        if not c.startswith(SRV):
            return Resposta(404, "application/json", '{"error":{"message":"nao existe"}}')
        partes = urlsplit(c[len(SRV):])
        conjunto = partes.path
        q = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
        m = re.fullmatch(r"UltimoRetornoView\((.*)\)", conjunto)
        if m:
            chaves = dict((k, v.replace("''", "'")) for k, v in re.findall(r"(\w+)='((?:[^']|'')*)'", m.group(1)))
            achado = [r for r in self.retorno if all(str(r.get(k)) == v for k, v in chaves.items())]
            if not achado:
                return Resposta(404, "application/json", '{"error":{"message":"nao existe"}}')
            return Resposta(200, "application/json", json.dumps(self._vivo(achado[0])))
        tabela = {"Pendencias": self.pendencias, "UltimoRetornoView": self.retorno}.get(conjunto)
        if tabela is None:
            return Resposta(404, "application/json", '{"error":{"message":"nao existe"}}')
        filtro = q.get("$filter", "")
        try:
            teste = compilar(filtro) if filtro else (lambda r: True)
            regs = [r for r in tabela if teste(r)]
        except (ValueError, AssertionError, IndexError) as exc:
            return Resposta(400, "application/json", json.dumps({"error": {"message": f"filtro: {exc}"}}))
        pulo = int(q.get("$skip", 0))
        pagina = regs[pulo:pulo + self.pagina]
        corpo = {"value": [self._vivo(r) for r in pagina]}
        if pulo + self.pagina < len(regs):
            corpo["@odata.nextLink"] = (f"{conjunto}?$filter={quote(filtro, safe=chr(39) + '()')}&$skip={pulo + self.pagina}" if filtro
                                        else f"{conjunto}?$skip={pulo + self.pagina}")
        return Resposta(200, "application/json", json.dumps(corpo))

    def _vivo(self, r: dict) -> dict:
        if r.get("workOrderID") in self.volateis and r.get("status") == "E":
            with self._trava:
                self._leituras_volateis += 1
                n = self._leituras_volateis
            return dict(r, quantidade=f"{n}e+0")
        return dict(r)
