"""Cliente de dados SOMENTE LEITURA.

Só existe uma operação de rede: GET. Não há caminho no código para POST, PUT,
PATCH, DELETE ou $batch — assim o agente não consegue acionar "Reprocessar" nem
"Efetuar Baixa", nem por engano.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable
from urllib.parse import quote, urljoin, urlsplit

from . import odata
from .config import ApiConfig
from .campos import campos_de_quantidade
from .diagnostico import evidencia
from .modelos import (ErroConsulta, FalhaComunicacao, Leitura, LinhaMensagem, Ocorrencia,
                      SessaoExpirada, TempoEsgotado)


@dataclass
class Resposta:
    status: int
    content_type: str
    corpo: str
    redirecionado: bool = False
    retry_after: str = ""


# Transporte: recebe uma URL e devolve a Resposta de um GET feito com a sessão
# autenticada. Em produção é a aba do SAP (ponte.py); nos testes, um falso.
Transporte = Callable[[str], Resposta]

LIMITE_PAGINAS = 50


# Página de login/SSO no lugar dos dados (approuter/IdP). Só padrões de formulário/URL de
# autenticação — nunca uma palavra solta que possa aparecer numa mensagem do BTP.
_LOGIN = re.compile(r"(j_security_check|SAMLRequest|SAMLResponse|/saml2?/|idp/SSO|/oauth/authorize|"
                    r"accounts\.sap\.com|name=[\"']j_username|id=[\"']logOnForm|sap-login|/login/callback)", re.I)


def marcador_login(corpo: str) -> str | None:
    m = _LOGIN.search(corpo[:20000] if corpo else "")
    return m.group(0) if m else None


def classificar(resposta: Resposta, url: str = "") -> str:
    """Valida a resposta HTTP e devolve o corpo, ou levanta a exceção adequada.

    Cada exceção leva o MOTIVO (taxonomia do diagnóstico) e a EVIDÊNCIA da resposta.
    Sessão (pausa a análise): redirecionamento, 401, 403, HTML ou página de login.
    """
    def ev(**extra):
        return evidencia(url, resposta.status, resposta.content_type, resposta.corpo, **extra)
    if resposta.redirecionado or resposta.status == 0:
        raise SessaoExpirada("Sessão expirada: o BTP redirecionou para o login.", "sessao_expirada",
                             ev(redirecionado=True))
    if resposta.status == 401:
        raise SessaoExpirada("Sessão expirada ou não autenticada (HTTP 401).", "http_401", ev())
    if resposta.status == 403:
        raise SessaoExpirada("Acesso negado (HTTP 403).", "http_403", ev())
    if resposta.status == 429:
        raise FalhaComunicacao("O BTP pediu para reduzir o ritmo (HTTP 429).", "http_429",
                               ev(retry_after=resposta.retry_after))
    if resposta.status >= 500:
        raise FalhaComunicacao(f"Servidor indisponível (HTTP {resposta.status}).", "http_5xx", ev())
    if resposta.status == 404:
        raise ErroConsulta("Endereço de dados não encontrado (HTTP 404).", "http_404", ev())
    if resposta.status >= 400:
        raise ErroConsulta(f"Consulta rejeitada (HTTP {resposta.status}).", "consulta_rejeitada", ev())
    corpo = resposta.corpo or ""
    marca = marcador_login(corpo) if corpo.lstrip()[:1] == "<" or "html" in resposta.content_type.lower() else None
    if marca or "html" in resposta.content_type.lower():
        # O approuter devolve a página de login em HTML (status 200) quando a sessão cai.
        raise SessaoExpirada("Recebida página HTML no lugar de dados (provável login).", "sessao_expirada",
                             ev(marcador_login=marca or "content-type html"))
    return corpo


class Ritmo:
    """Ritmo ADAPTATIVO compartilhado por todas as consultas da análise (sem pausas fixas).

    Começa sem espera. HTTP 429 ou 5xx => espera cresce (respeita Retry-After); cada sucesso
    reduz a espera até voltar a zero. `minimo` = intervalo mínimo configurado pelo usuário.
    """

    def __init__(self, minimo: float = 0.0, dormir=time.sleep):
        self.minimo = max(0.0, minimo)
        self.espera = 0.0
        self._ultima = 0.0
        self._trava = threading.Lock()
        self._dormir = dormir
        self.limitadas = 0

    def antes(self) -> None:
        with self._trava:
            alvo = max(self.espera, self.minimo)
            agora = time.monotonic()
            pausa = self._ultima + alvo - agora if alvo else 0.0
            self._ultima = max(agora, self._ultima + alvo) if alvo else agora
        if pausa > 0:
            self._dormir(pausa)

    def depois(self, status: int, retry_after: str = "") -> None:
        with self._trava:
            if status == 429 or status >= 500:
                self.limitadas += status == 429
                pedido = 0.0
                try:
                    pedido = float(retry_after) if retry_after else 0.0
                except ValueError:
                    pedido = 0.0
                self.espera = min(30.0, max(self.espera * 2, 0.5 if status == 429 else 0.2, pedido))
            elif 200 <= status < 300:
                self.espera = self.espera * 0.8 if self.espera > 0.01 else 0.0


class CacheExecucao:
    """O que vale para a análise inteira e não é dado do BTP (cada leitura continua indo ao BTP):
    modelos de URL já interpretados, disponibilidade da releitura por chave e da consulta em lote,
    contagem de consultas."""

    def __init__(self):
        self._trava = threading.Lock()
        self.consultas = 0
        self.releitura_falhas = 0
        self.releitura_sucessos = 0
        self.lote_ativo = True
        self.lote_motivo = ""
        self.tamanho_lote: int | None = None
        self.filtros: dict = {}

    def contar(self) -> None:
        with self._trava:
            self.consultas += 1

    @property
    def releitura_indisponivel(self) -> bool:
        # 3 WOs seguidas sem conseguir reler NENHUM item pela chave => o serviço não oferece o caminho.
        return self.releitura_sucessos == 0 and self.releitura_falhas >= 3

    def registrar_releitura(self, ok: bool) -> None:
        with self._trava:
            if ok:
                self.releitura_sucessos += 1
            else:
                self.releitura_falhas += 1

    def desativar_lote(self, motivo: str) -> bool:
        with self._trava:
            if not self.lote_ativo:
                return False
            self.lote_ativo, self.lote_motivo = False, motivo
            return True

    def reduzir_lote(self, atual: int) -> int:
        with self._trava:
            self.tamanho_lote = max(1, min(self.tamanho_lote or atual, atual) // 2)
            if self.tamanho_lote <= 1:
                self.lote_ativo, self.lote_motivo = False, "consultas em lote recusadas pelo BTP"
            return self.tamanho_lote


class LoteInconsistente(Exception):
    """A consulta em lote não pôde ser repartida por WO com segurança: o chamador refaz WO a WO."""


class ClienteBTP:
    """Leitura das WOs no BTP. `tempos` = tempo máximo (s) por tipo de consulta; `prazo` =
    instante (time.monotonic) em que acaba o tempo da WO atual; `intervalo` = pausa entre consultas."""

    def __init__(self, config: ApiConfig, transporte: Transporte, tempos: dict | None = None,
                 intervalo: float = 0.0, ritmo: Ritmo | None = None, cache: CacheExecucao | None = None,
                 ao_erro: Callable[[Exception], None] | None = None,
                 ao_sucesso: Callable[[], None] | None = None):
        self.config = config
        self._transporte = transporte
        self.tempos = tempos or {}
        self.intervalo = intervalo
        self.prazo: float | None = None
        self._ultima = 0.0
        self.ritmo = ritmo
        self.cache = cache or CacheExecucao()
        self.ao_erro = ao_erro
        self.ao_sucesso = ao_sucesso
        self.consultas = 0
        m = re.search(r"\$filter=([\w/]+)\s+eq\s+'\{wo\}'", config.url_lista)
        self.campo_wo_lista = m.group(1) if m else None

    # ------------------------------------------------ transporte com limites de tempo
    def _chamar(self, url: str, tipo: str) -> Resposta:
        if self.intervalo and not self.ritmo:
            espera = self._ultima + self.intervalo - time.monotonic()
            if espera > 0:
                time.sleep(espera)
        limite = self.tempos.get("lista" if tipo == "lote_lista" else "detalhes" if tipo == "lote" else tipo)
        if self.prazo is not None:
            restante = self.prazo - time.monotonic()
            if restante < 0.2:   # não dá tempo para uma consulta: encerra em vez de abortar no meio
                raise TempoEsgotado("Tempo máximo da WO atingido.", "timeout_lote" if tipo.startswith("lote")
                                    else "timeout_wo", evidencia(url))
            limite = min(limite, restante) if limite else restante
        if limite:
            limite = max(limite, 1.0)   # a aba nunca recebe 0 (lá, 0 viraria o padrão de 60 s)
        if self.ritmo:
            self.ritmo.antes()
        self.consultas += 1
        self.cache.contar()
        try:
            resposta = self._transporte(url, timeout=limite) if (limite and self.tempos) else self._transporte(url)
        except FalhaComunicacao as exc:
            if tipo.startswith("lote") and exc.motivo == "timeout_wo":
                exc.motivo = "timeout_lote"
            raise
        finally:
            self._ultima = time.monotonic()
        if self.ritmo:
            self.ritmo.depois(resposta.status, resposta.retry_after)
        return resposta

    def _get(self, url: str, tipo: str = "detalhes") -> list[dict]:
        """GET com paginação do servidor (__next / @odata.nextLink)."""
        registros: list[dict] = []
        try:
            for _ in range(LIMITE_PAGINAS):
                resposta = self._chamar(url, tipo)
                corpo = classificar(resposta, url)
                try:
                    pagina, proximo = odata.extrair_pagina(corpo)
                except ErroConsulta as exc:
                    exc.evidencia = evidencia(url, resposta.status, resposta.content_type, corpo)
                    if exc.motivo == "consulta_rejeitada":
                        exc.motivo = "erro_servico" if "erro" in str(exc) else "parse_falhou"
                    raise
                registros.extend(pagina)
                if self.ao_sucesso:
                    self.ao_sucesso()
                if not proximo:
                    return registros
                url = urljoin(url, proximo)
            raise ErroConsulta(f"Mais de {LIMITE_PAGINAS} páginas de resultado; consulta interrompida.",
                               "parse_falhou", evidencia(url))
        except (SessaoExpirada, FalhaComunicacao, ErroConsulta, TempoEsgotado) as exc:
            exc.evidencia.setdefault("url", url)
            if self.ao_erro:
                self.ao_erro(exc)
            raise

    def buscar_alertas(self, wo: str) -> list[dict]:
        return self._get(odata.preencher_modelo(self.config.url_lista, wo=wo), "lista")

    def buscar_mensagens(self, alerta: dict) -> list[dict]:
        if self.config.campo_mensagens_link:
            uri = link_navegacao(alerta, self.config.campo_mensagens_link)
            if not uri:
                return []
            return self._get(relativizar(uri, self.config.url_lista))
        if self.config.campo_mensagens_json:
            return registros_json(odata.obter_campo(alerta, self.config.campo_mensagens_json))
        if self.config.campo_mensagens_expandidas == ".":
            return [alerta]  # a própria linha já é a mensagem
        if self.config.campo_mensagens_expandidas:
            bruto = odata.obter_campo(alerta, self.config.campo_mensagens_expandidas)
            if isinstance(bruto, dict) and "results" in bruto:
                bruto = bruto["results"]
            return list(bruto or [])
        return self._get(odata.preencher_modelo(self.config.url_mensagens, linha=alerta))

    # ------------------------------------------------ leitura completa de uma WO
    def ler_wo(self, wo: str, rodada: int = 0, tipo: str = "Extração inicial") -> Leitura:
        """Lê todas as ocorrências da WO, do jeito que o BTP as entrega, sem somar nem agrupar.

        - Linha da lista de OUTRA WO (filtro ignorado pelo servidor) é descartada e avisada.
        - O mesmo item que chega duas vezes (ex.: dois alertas da WO apontando para os
          mesmos itens) é contado UMA vez; a repetição fica registrada em `duplicadas`.
        """
        return self._montar(wo, rodada, tipo, self.buscar_alertas(wo), self.buscar_mensagens)

    def _montar(self, wo: str, rodada: int, tipo: str, alertas: list[dict], mensagens_de) -> Leitura:
        """Monta a Leitura de UMA WO a partir das linhas da lista e das mensagens de cada linha.
        Usado igualmente pela leitura individual e pela leitura em lote (mesmas regras)."""
        leitura = Leitura(rodada, tipo)
        validos = []
        for a in alertas:
            lido = odata.como_texto(odata.obter_campo(a, self.campo_wo_lista)) if self.campo_wo_lista else ""
            if lido and lido != wo:
                leitura.avisos.append(f"Linha da lista de outra WO ({lido}) descartada: o filtro não foi aplicado.")
                continue
            validos.append(a)
        leitura.alertas = len(validos)
        validos = self._sem_consulta_repetida(validos, leitura)
        itens: dict = {}
        sem_chave = 0
        mesma_consulta = self.config.campo_mensagens_expandidas == "."   # cada linha já é um item
        vistos_no_alerta: dict[str, int] = {}
        for a in validos:
            if not mesma_consulta:
                vistos_no_alerta = {}
            for m in mensagens_de(a):
                o = self.ocorrencia(wo, a, m)
                if o.valores["wo"] != wo:
                    leitura.avisos.append(f"Item de outra WO ({o.valores['wo']}) descartado.")
                    continue
                if o.chave.startswith("conteudo:"):
                    # Sem chave do BTP: itens iguais DENTRO do mesmo alerta são itens distintos
                    # (numerados pela ordem); o mesmo item vindo de outra linha idêntica da lista
                    # recebe o mesmo número e aí sim é repetição.
                    sem_chave += 1
                    n = vistos_no_alerta.get(o.chave, 0) + 1
                    vistos_no_alerta[o.chave] = n
                    if n > 1:
                        o.chave = f"{o.chave}#{n}"
                if o.chave in itens:
                    leitura.duplicadas += 1
                    continue
                itens[o.chave] = o
        if sem_chave:
            leitura.avisos.append(f"{sem_chave} item(ns) sem chave no BTP: identificados pelo conteúdo "
                                  "(sem quantidade, mensagem ou data de alteração).")
        leitura.itens = itens
        if leitura.duplicadas:
            leitura.avisos.append(f"{leitura.duplicadas} item(ns) repetido(s) no retorno do BTP contado(s) uma vez.")
        return leitura

    # ------------------------------------------------ leitura EM LOTE (várias WOs por consulta)
    @property
    def pode_ler_em_lote(self) -> bool:
        """Só quando a consulta da lista é exatamente `$filter=<campo> eq '{wo}'` (repartível por WO)
        e as mensagens vêm de um modo que a leitura em lote reproduz sem ambiguidade."""
        if not self.cache.lote_ativo or not self.campo_wo_lista:
            return False
        if self.config.campo_mensagens_link:
            return True   # lista em lote; cada link continua individual
        if self.config.url_mensagens and not self.config.campo_mensagens_expandidas:
            return self._filtro_mensagens(None) is not None
        return bool(self.config.campo_mensagens_expandidas or self.config.campo_mensagens_json)

    def _url_lista_lote(self, wos: list[str]) -> str:
        modelo = self.config.url_lista
        m = re.search(r"\$filter=([\w/]+)\s+eq\s+'\{wo\}'", modelo)
        campo = m.group(1)
        filtro = " or ".join(f"{campo} eq '{odata.codificar_valor(w)}'" for w in wos)
        return quote(modelo[:m.start()] + f"$filter=({filtro})" + modelo[m.end():], safe=odata._SEGUROS_MODELO)

    def _filtro_mensagens(self, alerta: dict | None):
        """Divide a consulta de mensagens de UMA linha em (caminho, condições, resto da query).
        Condições = [(campo, tipo, valor)] de um filtro que é só `campo eq valor` ligados por `and`.
        None = formato que a leitura em lote não sabe repartir com segurança.
        `alerta=None` só verifica o formato do modelo."""
        modelo = self.config.url_mensagens or ""
        if "{wo}" in modelo or "?" not in modelo:
            return None
        caminho, _, query = modelo.partition("?")
        partes = query.split("&")
        filtros = [p for p in partes if p.startswith("$filter=")]
        if len(filtros) != 1 or "{" in caminho:
            return None
        resto = "&".join(p for p in partes if not p.startswith("$filter="))
        if "{" in resto:
            return None
        expr = filtros[0][len("$filter="):]
        conds = []
        for termo in re.split(r"\s+and\s+", expr.strip()):
            m = re.fullmatch(r"([\w/]+)\s+eq\s+(?:'([^']*)'|(-?\d+(?:\.\d+)?))", termo.strip())
            if not m:
                return None
            campo, texto, numero = m.groups()
            if texto is None:
                conds.append((campo, "n", numero))
                continue
            v = re.fullmatch(r"\{linha\[(\w+)\]\}", texto)
            if v:
                bruto = alerta.get(v.group(1)) if alerta is not None else ""
                if bruto is None or isinstance(bruto, (dict, list)):
                    return None   # a consulta individual também não teria como ser montada
                conds.append((campo, "s", str(bruto)))   # igual a preencher_modelo (sem strip)
            elif "{" in texto:
                return None
            else:
                conds.append((campo, "s", texto))
        return caminho, conds, resto

    @staticmethod
    def _expr(conds) -> str:
        partes = []
        for campo, tipo, valor in conds:
            partes.append(f"{campo} eq '{odata.codificar_valor(valor)}'" if tipo == "s" else f"{campo} eq {valor}")
        return " and ".join(partes)

    @staticmethod
    def _casa(registro: dict, conds) -> bool:
        for campo, tipo, valor in conds:
            lido = odata.obter_campo(registro, campo)
            if tipo == "s":
                if lido is None or isinstance(lido, (dict, list)) or str(lido) != valor:
                    return False
            else:
                try:
                    if Decimal(str(lido)) != Decimal(valor):
                        return False
                except (InvalidOperation, ValueError):
                    return False
        return True

    def _mensagens_em_lote(self, alertas: list[dict], limite_url: int = 6000) -> dict[int, list[dict]]:
        """Mensagens de várias linhas da lista em poucas consultas (filtros unidos por `or`).
        Cada registro devolvido é atribuído a TODAS as linhas cujo filtro ele satisfaz — o mesmo que
        a consulta individual de cada linha devolveria. Registro que não casa com nenhuma => erro."""
        planos = []
        for i, a in enumerate(alertas):
            plano = self._filtro_mensagens(a)
            if plano is None:
                raise LoteInconsistente("filtro de mensagens não repartível")
            planos.append((i, plano))
        if not planos:
            return {}
        caminho, _, resto = planos[0][1]
        if any(p[0] != caminho or p[2] != resto for _, p in planos):
            raise LoteInconsistente("consultas de mensagens com caminhos diferentes")
        unicos: dict[str, tuple] = {}
        for _, (_, conds, _) in planos:
            unicos.setdefault(self._expr(conds), conds)
        grupos, atual, tamanho = [], [], 0
        for expr in unicos:
            if atual and (tamanho + len(expr) + 8 > limite_url or len(atual) >= 40):
                grupos.append(atual)
                atual, tamanho = [], 0
            atual.append(expr)
            tamanho += len(expr) + 8
        if atual:
            grupos.append(atual)
        recebidos: list[dict] = []
        for exprs in grupos:
            filtro = " or ".join(f"({e})" for e in exprs) if len(exprs) > 1 else exprs[0]
            url = quote(caminho + "?" + "&".join(x for x in ([f"$filter={filtro}"] + ([resto] if resto else []))),
                        safe=odata._SEGUROS_MODELO)
            recebidos.extend(self._get(url, "lote"))
        saida: dict[int, list[dict]] = {i: [] for i, _ in planos}
        for registro in recebidos:
            donos = [i for i, (_, conds, _) in planos if self._casa(registro, conds)]
            if not donos:
                raise LoteInconsistente("registro de mensagem que não pertence a nenhuma linha consultada")
            for i in donos:
                saida[i].append(registro)
        return saida

    def _consulta_dos_itens(self, alerta: dict) -> str | None:
        """Endereço que busca os itens desta linha da lista, quando os itens vêm por consulta
        própria (ex.: UltimoRetornoView filtrado por consolidado + WO + status)."""
        c = self.config
        if not c.url_mensagens or c.campo_mensagens_expandidas or c.campo_mensagens_link or c.campo_mensagens_json:
            return None
        return odata.preencher_modelo(c.url_mensagens, linha=alerta)

    def _sem_consulta_repetida(self, alertas: list[dict], leitura: Leitura) -> list[dict]:
        """Linhas da lista que levam à MESMA consulta de itens (ex.: duas linhas da WO em
        Pendencias com o mesmo consolidado) trazem os MESMOS registros do BTP: são lidas uma
        vez. Antes, cada linha gerava uma cópia de cada item (linhas repetidas na planilha).

        A linha que representa o grupo é escolhida pelo conteúdo (não pela ordem da resposta),
        para a identificação dos itens ser a mesma em todas as leituras. Se a mensagem do
        alerta entra na planilha e difere entre as linhas do grupo, nada é juntado."""
        grupos: dict[str, list[dict]] = OrderedDict()
        soltos: list[dict] = []
        for a in alertas:
            consulta = self._consulta_dos_itens(a)
            if consulta is None:
                soltos.append(a)
            else:
                grupos.setdefault(consulta, []).append(a)
        if not grupos or all(len(g) == 1 for g in grupos.values()):
            return alertas
        usa_alerta = self.config.mensagem_origem in ("alerta", "ambas") and self.config.campo_mensagem_alerta
        saida = list(soltos)
        juntadas = 0
        for grupo in grupos.values():
            if usa_alerta and len({odata.como_texto(odata.obter_campo(a, self.config.campo_mensagem_alerta))
                                   for a in grupo}) > 1:
                saida.extend(grupo)
                continue
            saida.append(min(grupo, key=lambda a: json.dumps(
                {k: v for k, v in a.items() if not k.startswith(("__", "@")) and not isinstance(v, (dict, list))},
                sort_keys=True, default=str)))
            juntadas += len(grupo) - 1
        if juntadas:
            leitura.avisos.append(f"{juntadas} linha(s) da lista com a mesma consulta de itens (mesmo consolidado): "
                                  "itens lidos uma vez, sem repetir na planilha.")
        return saida

    def ler_lote(self, wos: list[str], rodada: int, tipo: str) -> dict[str, Leitura]:
        """Lê várias WOs com UMA consulta da lista (+ poucas de mensagens) e reparte por WO.

        O resultado de cada WO é montado pelas MESMAS regras da leitura individual (_montar).
        Linha devolvida que não é de nenhuma WO pedida => LoteInconsistente (refazer WO a WO)."""
        if not self.pode_ler_em_lote:
            raise LoteInconsistente("leitura em lote indisponível")
        pedidas = set(wos)
        por_wo: dict[str, list[dict]] = {w: [] for w in wos}
        for a in self._get(self._url_lista_lote(wos), "lote_lista"):
            lido = odata.como_texto(odata.obter_campo(a, self.campo_wo_lista))
            if lido not in pedidas:
                raise LoteInconsistente(f"linha da lista de WO não pedida ({lido or 'vazio'})")
            por_wo[lido].append(a)
        todos = [a for w in wos for a in por_wo[w]]
        if self.config.url_mensagens and not self.config.campo_mensagens_expandidas \
                and not self.config.campo_mensagens_link and not self.config.campo_mensagens_json:
            msgs = self._mensagens_em_lote(todos)
            indice = {id(a): i for i, a in enumerate(todos)}

            def mensagens_de(a):
                return msgs[indice[id(a)]]
        else:
            mensagens_de = self.buscar_mensagens
        return {w: self._montar(w, rodada, tipo, por_wo[w], mensagens_de) for w in wos}

    @property
    def pode_reler_por_chave(self) -> bool:
        return bool(self.config.url_item and self.config.chaves_item)

    def _chaves_do_endereco(self):
        """Do endereço de releitura (ex.: Conj(a='{linha[a]}',b={linha[b]}) ou Conj({linha[ID]}))
        tira o conjunto e, por chave, o campo da linha e se o valor vai entre aspas.
        None = formato que a releitura em lote não sabe montar com segurança."""
        url = self.config.url_item or ""
        m = re.fullmatch(r"(.*/)?([\w.]+)\((.*)\)", url)
        if not m or "?" in url:
            return None
        caminho = (m.group(1) or "") + m.group(2)
        partes = [p.strip() for p in m.group(3).split(",")]
        chaves = []
        for p in partes:
            k = re.fullmatch(r"(?:(\w+)=)?('?)\{linha\[(\w+)\]\}\2", p)
            if not k:
                return None
            nome = k.group(1) or (self.config.chaves_item or [None])[0]
            if not nome or nome != k.group(3):
                return None
            chaves.append((nome, bool(k.group(2))))
        return caminho, chaves

    def reler_lote(self, pedidos: dict[str, Leitura], rodada: int) -> dict[str, Leitura | None]:
        """Releitura por chave de VÁRIAS WOs com poucas consultas: filtra o conjunto do endereço de
        releitura pelas chaves de cada item (`a eq 'x' and b eq 'y'`, unidas por `or`).
        Mesmo resultado da releitura item a item: item que não volta = ausente (None); item sem
        chave = não relido (avisado). WO sem NENHUM item relido volta None (o chamador refaz WO a WO,
        como na releitura individual). Erro do BTP propaga (o chamador refaz WO a WO)."""
        forma = self._chaves_do_endereco()
        if forma is None:
            raise LoteInconsistente("endereço de releitura não repartível")
        caminho, chaves = forma
        alvos = []      # (wo, chave do item, ocorrência base, condições)
        saida: dict[str, Leitura | None] = {}
        for wo, base in pedidos.items():
            leitura = Leitura(rodada, "Releitura por chave", itens={})
            saida[wo] = leitura
            for chave, o in base.itens.items():
                if not all(o.bruto.get(k) not in (None, "") for k in self.config.chaves_item):
                    leitura.avisos.append(f"Item {chave}: chave não disponível para releitura.")
                    continue
                conds = [(k, "s" if aspas else "n", str(o.bruto[k])) for k, aspas in chaves]
                if any(t == "n" and not re.fullmatch(r"-?\d+(\.\d+)?", v) for _, t, v in conds):
                    raise LoteInconsistente("chave sem aspas que não é número")
                alvos.append((wo, chave, o, conds))
        recebidos: list[dict] = []
        grupo: list[str] = []

        def consultar(exprs):
            filtro = " or ".join(f"({e})" for e in exprs) if len(exprs) > 1 else exprs[0]
            recebidos.extend(self._get(quote(f"{caminho}?$filter={filtro}", safe=odata._SEGUROS_MODELO), "lote"))
        exprs = list(dict.fromkeys(self._expr(c) for *_, c in alvos))
        tamanho = 0
        for e in exprs:
            if grupo and (tamanho + len(e) > 6000 or len(grupo) >= 40):
                consultar(grupo)
                grupo, tamanho = [], 0
            grupo.append(e)
            tamanho += len(e) + 8
        if grupo:
            consultar(grupo)
        for wo, chave, o, conds in alvos:
            achados = [r for r in recebidos if self._casa(r, conds)]
            saida[wo].itens[chave] = self.ocorrencia(wo, o.alerta, achados[0]) if achados else None
        for wo in pedidos:
            if not any(v is not None for v in saida[wo].itens.values()):
                saida[wo] = None
        return saida

    def reler(self, wo: str, base: Leitura, rodada: int) -> Leitura:
        """Auditoria independente: busca CADA item pelo seu endereço próprio (chave), por outro
        caminho que não a consulta da lista. Item que não existe mais fica como ausente (None)."""
        leitura = Leitura(rodada, "Releitura por chave", itens={})
        encontrados = nao_encontrados = recusados = 0
        for chave, o in base.itens.items():
            if not all(o.bruto.get(k) not in (None, "") for k in self.config.chaves_item):
                leitura.avisos.append(f"Item {chave}: chave não disponível para releitura.")
                continue   # fica fora da leitura: "não relido" (diferente de "ausente" = None)
            try:
                registros = self._get(odata.preencher_modelo(self.config.url_item, linha=o.bruto))
            except ErroConsulta as exc:
                if "404" not in str(exc):
                    # Endereço recusado (ex.: 400 por formato da chave): não prova nada sobre o
                    # item; fica "não relido", nunca "ausente".
                    recusados += 1
                    leitura.avisos.append(f"Item {chave}: releitura recusada pelo serviço ({exc}).")
                    continue
                registros = []
            leitura.itens[chave] = self.ocorrencia(wo, o.alerta, registros[0]) if registros else None
            encontrados += bool(registros)
            nao_encontrados += not registros
        if not encontrados:
            # Nenhum item pôde ser lido pelo endereço próprio: é o serviço que não oferece esse
            # caminho (não prova que os itens sumiram). Quem chama troca por nova consulta.
            raise ReleituraIndisponivel("O serviço não permite ler os itens pela chave "
                                        f"({nao_encontrados + recusados} tentativa(s) sem resposta"
                                        + ("; itens sem chave" if not (nao_encontrados or recusados) else "")
                                        + ").")
        return leitura

    def ocorrencia(self, wo: str, alerta: dict, mensagem: dict) -> Ocorrencia:
        campos = self.config.campos

        def campo(nome):
            return odata.obter_campo(mensagem, campos[nome]) if campos.get(nome) else None

        consolidado = campo("consolidado")
        if consolidado in (None, ""):
            consolidado = odata.obter_campo(alerta, self.config.campo_consolidado_lista)
        bruto = {k: v for k, v in mensagem.items()
                 if not k.startswith(("__", "@")) and not isinstance(v, (dict, list))}
        valores = {
            "wo": odata.como_texto(campo("wo")) if campos.get("wo") else wo,
            "material": odata.como_texto(campo("material")),
            "fornecedor": odata.como_texto(campo("fornecedor")),
            "quantidade_faltante": odata.como_quantidade(campo("quantidade_faltante")),
            "mensagem": odata.como_texto(campo("mensagem")),
            "consolidado": odata.como_texto(consolidado),
            "mensagem_alerta": odata.como_texto(odata.obter_campo(alerta, self.config.campo_mensagem_alerta))
                               if self.config.campo_mensagem_alerta else "",
        }
        valores["mensagem_item"] = valores["mensagem"]
        if self.config.mensagem_origem == "alerta" and valores["mensagem_alerta"]:
            valores["mensagem"] = valores["mensagem_alerta"]
        elif (self.config.mensagem_origem == "ambas" and valores["mensagem"] and valores["mensagem_alerta"]
              and valores["mensagem_alerta"] != valores["mensagem"]):
            # As duas, sempre na mesma ordem: a do item (janela Mensagens) e a do alerta (lista do Monitor).
            valores["mensagem"] = f'{valores["mensagem"]} — Alerta: {valores["mensagem_alerta"]}'
        elif not valores["mensagem"] and valores["mensagem_alerta"]:
            valores["mensagem"] = valores["mensagem_alerta"]   # o item não tem texto próprio
        fonte = {c: campos[c] for c in ("wo", "material", "fornecedor", "quantidade_faltante", "mensagem")
                 if campos.get(c)}
        quantidades = campos_de_quantidade(bruto)
        outras = {} if self.config.campos_confirmados else {
            k: v for k, v in quantidades.items() if k != campos.get("quantidade_faltante")}
        # Evidência de alteração: só campo com nome de alteração E valor que é DATA de fato
        # ("alteradoPor", "motivoAlteracao"... não servem).
        tempo = next((str(v) for k, v in bruto.items()
                      if _MARCA_TEMPO.search(k) and odata.instante(v) is not None), None)
        chaves = self.config.chaves_item or []
        if chaves and all(bruto.get(k) not in (None, "") for k in chaves):
            chave = "|".join(f"{k}={bruto[k]}" for k in chaves)
        else:
            # Sem chave declarada: identifica pelo que NÃO muda quando o BTP atualiza o item —
            # sem quantidades, sem mensagem, sem campos de alteração — mais o alerta de origem.
            # Assim uma mudança de quantidade aparece como DIVERGÊNCIA, não como item novo.
            variaveis = set(quantidades) | {campos.get("mensagem")} | {
                k for k in bruto if _MARCA_TEMPO.search(k)}
            identidade = {k: v for k, v in bruto.items() if k not in variaveis}
            origem = {k: v for k, v in alerta.items() if not k.startswith(("__", "@"))
                      and not isinstance(v, (dict, list)) and k not in variaveis
                      and k not in campos_de_quantidade(alerta)} if alerta is not mensagem else {}
            chave = "conteudo:" + hashlib.sha1(json.dumps([origem, identidade], sort_keys=True, default=str)
                                                .encode()).hexdigest()[:12]
        return Ocorrencia(chave, valores, bruto, alerta, fonte, outras, tempo)

    def converter(self, wo: str, alerta: dict, mensagem: dict) -> LinhaMensagem:
        v = self.ocorrencia(wo, alerta, mensagem).valores
        return LinhaMensagem(consolidado=v["consolidado"], wo=v["wo"], material=v["material"],
                             fornecedor=v["fornecedor"], quantidade_faltante=v["quantidade_faltante"],
                             mensagem=v["mensagem"])


class ReleituraIndisponivel(ErroConsulta):
    """O BTP não atende a leitura de item pela chave (nenhum item encontrado por esse caminho)."""


# Campo de "última alteração" do registro (evidência para explicar divergências).
_MARCA_TEMPO = re.compile(r"(modif|chang|alterad|atualiz|updated|lastchange)", re.I)


def link_navegacao(registro: dict, campo: str) -> str | None:
    """URI da navegação que o próprio OData informa na linha (V2: {"__deferred": {"uri": ...}})."""
    valor = registro.get(campo)
    if isinstance(valor, dict):
        valor = (valor.get("__deferred") or {}).get("uri")
    if not valor:
        valor = registro.get(campo + "@odata.navigationLink")   # V4
    return valor if isinstance(valor, str) and valor else None


def relativizar(uri: str, url_lista: str) -> str:
    """Converte a URI informada pelo servidor em caminho da própria aba.

    Atrás do approuter, o Gateway pode informar o endereço interno do servidor; por isso
    a parte a partir do conjunto de dados é recolocada sobre o caminho do serviço já usado.
    """
    caminho_lista = url_lista.split("?", 1)[0]
    raiz, _, conjunto = caminho_lista.rpartition("/")
    partes = urlsplit(uri)
    caminho = partes.path if (partes.scheme or uri.startswith("/")) else raiz + "/" + uri
    if partes.query:
        caminho += "?" + partes.query
    if conjunto:
        i = caminho.rfind("/" + conjunto + "(")
        if i >= 0:
            return raiz + caminho[i:]
    return caminho


def registros_json(valor) -> list[dict]:
    """Mensagens guardadas como texto JSON num campo da linha (lista, {results:[]} ou {value:[]})."""
    if isinstance(valor, str):
        texto = valor.strip()
        if not texto.startswith(("[", "{")):
            return []
        try:
            valor = json.loads(texto)
        except json.JSONDecodeError:
            return []
    if isinstance(valor, dict):
        valor = valor.get("results", valor.get("value", valor.get("d", [valor])))
        if isinstance(valor, dict):
            valor = valor.get("results", [valor])
    return [v for v in valor if isinstance(v, dict)] if isinstance(valor, list) else []
