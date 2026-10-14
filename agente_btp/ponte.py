"""Ponte com a aba do SAP JÁ ABERTA no navegador do usuário (modelo do Agente Atlas).

O script agente_btp.js é injetado na aba logada (pelo favorito "Agente BTP"). Ele:
  1. pergunta ao servidor local (127.0.0.1) se há consulta a fazer (long-poll);
  2. faz o GET com a sessão da própria aba (same-origin) e devolve a resposta;
  3. enquanto o agente ainda não está preparado, observa as consultas que a tela do
     Monitor faz, para o agente aprender sozinho de onde vêm os dados.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from .cliente import Resposta
from .modelos import FalhaComunicacao, SessaoExpirada

ESPERA_LONG_POLL = 15.0      # quanto o pedido do agente fica aguardando uma tarefa
TOLERANCIA_SILENCIO = 30.0   # sem contato há mais que isso = aba desconectada
TIMEOUT_TAREFA = 90.0
LIMITE_CAPTURA = 400


class PonteAba:
    def __init__(self):
        self._cond = threading.Condition()
        self._fila: deque[dict] = deque()
        self._pendentes: dict[str, dict] = {}
        self.agente_id: str | None = None
        self._visto_em = 0.0
        self._aba_autenticada = False
        self.pagina = ""
        self._captura: list[dict] | None = None
        # Chamado com as consultas observadas, para o agente aprender sozinho.
        self.observador = None

    # ------------------------------------------------ lado da aba (threads HTTP)
    def registrar_agente(self, agente: str, pagina: str) -> None:
        with self._cond:
            self.agente_id = agente
            self.pagina = pagina
            self._visto_em = time.monotonic()

    def proxima_tarefa(self, agente: str, autenticado: bool, pagina: str,
                       capturando_na_aba: bool = False, espera: float = ESPERA_LONG_POLL) -> dict:
        with self._cond:
            if agente != self.agente_id:
                return {"substituido": True}
            self._visto_em = time.monotonic()
            self._aba_autenticada = autenticado
            self.pagina = pagina
            limite = time.monotonic() + espera
            # Responde na hora se há tarefa OU se a aba precisa saber que a observação
            # começou/terminou — senão ela perderia as primeiras consultas da tela.
            while (not self._fila and time.monotonic() < limite and agente == self.agente_id
                   and (self._captura is not None) == capturando_na_aba):
                self._cond.wait(timeout=min(1.0, limite - time.monotonic()))
                self._visto_em = time.monotonic()
            resposta = {"capturar": self._captura is not None}
            if self._fila and agente == self.agente_id:
                resposta["tarefa"] = self._fila.popleft()
            return resposta

    def entregar_resposta(self, dados: dict) -> None:
        with self._cond:
            self._visto_em = time.monotonic()
            pendente = self._pendentes.get(dados.get("id"))
            if pendente:
                pendente["resposta"] = dados
                pendente["evento"].set()

    def adicionar_captura(self, itens: list[dict]) -> None:
        with self._cond:
            self._visto_em = time.monotonic()
            if self._captura is None:
                return
            self._captura.extend(i for i in itens if isinstance(i, dict))
            del self._captura[:-LIMITE_CAPTURA]   # memória limitada: só as mais recentes
            copia = list(self._captura)
        if self.observador:
            self.observador(copia)  # fora da trava: a análise pode demorar

    # ------------------------------------------------ lado do agente (worker)
    @property
    def conectada(self) -> bool:
        return bool(self.agente_id) and time.monotonic() - self._visto_em < TOLERANCIA_SILENCIO

    def autenticado(self) -> bool:
        return self.conectada and self._aba_autenticada

    def get(self, url: str, accept: str | None = None) -> Resposta:
        if not url.startswith("/") or url.startswith("//"):
            raise FalhaComunicacao(f"URL recusada (precisa ser do próprio sistema): {url[:80]}")
        tarefa = {"url": url}
        if accept:
            tarefa["accept"] = accept
        r = self._executar(tarefa)
        if r.get("status") == -1:
            raise FalhaComunicacao(f"Falha de rede na aba: {str(r.get('body'))[:200]}")
        return Resposta(status=int(r.get("status", 0)), content_type=r.get("ct") or "",
                        corpo=r.get("body") or "", redirecionado=bool(r.get("redir")))

    def fontes(self) -> list[dict]:
        """Serviços OData usados pelos apps abertos na aba (lidos da configuração do UI5)."""
        r = self._executar({"tipo": "fontes"})
        try:
            dados = json.loads(r.get("body") or "[]")
        except json.JSONDecodeError:
            return []
        return [d for d in dados if isinstance(d, dict)]

    def _executar(self, tarefa: dict) -> dict:
        if not self.conectada:
            raise SessaoExpirada("A aba do SAP não está conectada. Clique no favorito 'Agente BTP' na aba.")
        tarefa = dict(tarefa, id=uuid.uuid4().hex)
        pendente = {"evento": threading.Event(), "resposta": None}
        with self._cond:
            self._pendentes[tarefa["id"]] = pendente
            self._fila.append(tarefa)
            self._cond.notify_all()
        try:
            if not pendente["evento"].wait(TIMEOUT_TAREFA):
                with self._cond:
                    if tarefa in self._fila:
                        self._fila.remove(tarefa)
                if not self.conectada:
                    raise SessaoExpirada("A aba do SAP foi fechada ou recarregada. Clique no favorito novamente.")
                raise FalhaComunicacao("A aba do SAP não respondeu a tempo.")
        finally:
            with self._cond:
                self._pendentes.pop(tarefa["id"], None)
        return pendente["resposta"]

    def aguardar(self, ms: int) -> None:
        time.sleep(ms / 1000)

    @property
    def capturando(self) -> bool:
        return self._captura is not None

    def iniciar_captura(self) -> None:
        with self._cond:
            if self._captura is not None:
                return
            self._captura = []
            self._cond.notify_all()

    def parar_captura(self) -> None:
        with self._cond:
            self._captura = None
            self._cond.notify_all()

    def salvar_captura(self, pasta: Path) -> Path:
        with self._cond:
            if self._captura is None:
                raise RuntimeError("Nenhuma captura em andamento.")
            itens, self._captura = self._captura, None
            self._cond.notify_all()
        pasta.mkdir(parents=True, exist_ok=True)
        destino = pasta / f"captura_{datetime.now():%Y%m%d_%H%M%S}.json"
        conteudo = {"gerado_em": datetime.now().isoformat(timespec="seconds"),
                    "origem": "aba", "fontes_de_dados": [], "requisicoes": itens}
        destino.write_text(json.dumps(conteudo, ensure_ascii=False, indent=2), encoding="utf-8")
        return destino
