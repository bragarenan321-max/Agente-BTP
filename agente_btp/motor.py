"""Execução de uma análise sobre a Loja: paralelismo controlado, sessão verificada, retomada.

  - Grupos de WOs (consulta em lote) distribuídos entre N trabalhadores (paralelismo, padrão 4).
  - Cada WO é gravada na Loja assim que termina; nada fica só em memória.
  - Sessão: SONDA antes de começar e a cada 60 s ou 100 WOs (é também o keep-alive: toda
    consulta pelo approuter renova a sessão). Sessão caiu => pausa (pausado_sessao): as WOs em
    andamento voltam para "pendente", nenhuma vira falha; a análise retoma SOZINHA quando a aba
    se anuncia de novo (ou a cada 30 s) e a sonda passa.
  - Erro de sessão numa WO com a sonda passando logo em seguida => o problema é DAQUELA WO
    (ex.: 403 só nela): vira falha dela, com o motivo, e a análise continua.
  - Pausar (usuário) é diferente de Parar: pausar segura a fila; parar encerra (retomável depois).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

from . import diagnostico
from .cliente import ClienteBTP
from .loja import Loja, estado_da_wo
from .modelos import Auditoria, ResultadoWO, SessaoExpirada, Status
from .preferencias import Preferencias
from .processador import processar_grupo, processar_wo_seguro

log = logging.getLogger("agente_btp")

INTERVALO_SONDA_SEG = 60.0
SONDA_A_CADA_WOS = 100
REVERIFICAR_PAUSA_SEG = 30.0
KEEPALIVE_PAUSA_USUARIO_SEG = 240.0
# Os primeiros grupos de cada análise são conferidos WO a WO contra a consulta individual
# (equivalência no dado real); depois, uma amostra por grupo.
GRUPOS_VERIFICADOS_POR_INTEIRO = 3

# (ok, motivo, evidência): ok True = sessão válida; False = sessão caiu; None = indeterminado
Sonda = Callable[[], tuple]


class Motor:
    def __init__(self, loja: Loja, run_id: str, fabrica_cliente: Callable[[], ClienteBTP], prefs: Preferencias,
                 sonda: Sonda, conectada: Callable[[], bool] = lambda: True, anuncios: Callable[[], int] = lambda: 0,
                 ao_etapa: Callable[[str], None] | None = None, ao_situacao: Callable[[str, str], None] | None = None,
                 dormir: Callable[[float], None] = time.sleep, relogio: Callable[[], float] = time.monotonic):
        self.loja, self.run_id, self.fabrica, self.prefs = loja, run_id, fabrica_cliente, prefs
        self.sonda, self.conectada, self.anuncios = sonda, conectada, anuncios
        self.ao_etapa = ao_etapa or (lambda texto: None)
        self.ao_situacao = ao_situacao or (lambda situacao, motivo: None)
        self._dormir, self._relogio = dormir, relogio
        self._parar = threading.Event()
        self._pausa_usuario = threading.Event()
        self._cutucar = threading.Event()        # "tente retomar agora" (aba anunciou / botão Retomar)
        self._sessao_caiu = threading.Event()
        self._interromper = threading.Event()    # em_erro == "interromper"
        self._limite = threading.Event()
        self._trava = threading.Lock()
        self._concluidas_desde_sonda = 0
        self._grupos = 0
        self.motivo_pausa = ""
        self.evidencia_pausa: dict = {}
        self.situacao = "em_andamento"

    # ------------------------------------------------ controles (chamados pela tela)
    def parar(self) -> None:
        self._parar.set()
        self._cutucar.set()

    def pausar(self) -> None:
        self._pausa_usuario.set()

    def continuar(self) -> None:
        self._pausa_usuario.clear()
        self._cutucar.set()

    def cutucar(self) -> None:
        self._cutucar.set()

    # ------------------------------------------------ execução
    def executar(self) -> str:
        inicio = self._relogio()
        self._pausado_total = 0.0
        self._inicio = inicio
        feitas: set[str] = set()
        voltaram = self.loja.devolver_em_andamento(self.run_id, "retomada após interrupção")
        if voltaram:
            self.loja.evento(self.run_id, "marco", f"{voltaram} WO(s) que estavam em andamento voltaram para a fila.")
        self._situacao("em_andamento")
        if not self._verificar_sessao_inicial():
            return self._encerrar("interrompida", "interrompida_usuario")
        while True:
            if self._parar.is_set():
                return self._encerrar("interrompida", "interrompida_usuario")
            if self._limite.is_set():
                return self._encerrar("interrompida", "limite_execucao")
            if self._interromper.is_set():
                return self._encerrar("interrompida", "")
            fila = [w for w in self.loja.a_processar(self.run_id) if w not in feitas]
            if not fila:
                return self._encerrar("concluida", "")
            resultado = self._passada(fila, feitas)
            if resultado == "sessao":
                if not self._esperar_sessao():
                    return self._encerrar("interrompida", "interrompida_usuario")
            elif resultado == "pausa":
                if not self._esperar_usuario():
                    return self._encerrar("interrompida", "interrompida_usuario")

    def _encerrar(self, situacao: str, motivo: str) -> str:
        devolvidas = self.loja.devolver_em_andamento(self.run_id, motivo or "interrompida")
        c = self.loja.contadores(self.run_id)
        texto = {"concluida": "Análise concluída.",
                 "interrompida": "Análise interrompida: as WOs não feitas continuam pendentes (use Retomar)."}[situacao]
        if motivo == "limite_execucao":
            texto = (f"Tempo máximo da análise ({self.prefs.tempo_max_lote_min:g} min) atingido: "
                     "as WOs não feitas continuam pendentes (use Retomar).")
        self.loja.evento(self.run_id, "marco", f"{texto} Validadas {c['validada']}, divergentes {c['divergente']}, "
                         f"incompletas {c['incompleta']}, falhas {c['falha']}, pendentes {c['pendente']}.",
                         motivo=motivo, detalhe={"devolvidas": devolvidas})
        if motivo in ("limite_execucao", "interrompida_usuario"):
            self.loja.problema(self.run_id, motivo, diagnostico.evidencia())
        self._situacao(situacao, motivo)
        return situacao

    def _situacao(self, situacao: str, motivo: str = "") -> None:
        self.situacao = situacao
        self.loja.definir_situacao(self.run_id, situacao, motivo)
        self.ao_situacao(situacao, motivo)

    # ------------------------------------------------ uma passada pela fila
    def _passada(self, fila: list[str], feitas: set[str]) -> str:
        p = self.prefs
        tamanho = max(1, p.tamanho_lote)
        grupos: queue.Queue = queue.Queue()
        for i in range(0, len(fila), tamanho):
            grupos.put(fila[i:i + tamanho])
        n = max(1, min(p.concorrencia, grupos.qsize()))
        trabalhadores = [threading.Thread(target=self._trabalhador, args=(grupos, feitas), daemon=True,
                                          name=f"agente-btp-{i + 1}") for i in range(n)]
        for t in trabalhadores:
            t.start()
        ultima_sonda = self._relogio()
        while any(t.is_alive() for t in trabalhadores):
            for t in trabalhadores:
                t.join(timeout=0.5)
            agora = self._relogio()
            if agora - self._inicio - self._pausado_total >= p.tempo_max_lote_min * 60:
                self._limite.set()
            if self._sessao_caiu.is_set() or self._parar.is_set():
                continue
            with self._trava:
                devido = self._concluidas_desde_sonda >= SONDA_A_CADA_WOS
            if devido or agora - ultima_sonda >= INTERVALO_SONDA_SEG:
                ultima_sonda = agora
                with self._trava:
                    self._concluidas_desde_sonda = 0
                ok, motivo, ev = self._sondar("periódica")
                if ok is False:
                    self._marcar_queda(motivo, ev)
        if self._sessao_caiu.is_set():
            return "sessao"
        if self._parar.is_set() or self._limite.is_set() or self._interromper.is_set():
            return "fim"
        if self._pausa_usuario.is_set():
            return "pausa"
        return "ok"

    def _deve_segurar(self) -> bool:
        return any(e.is_set() for e in (self._parar, self._sessao_caiu, self._pausa_usuario,
                                        self._limite, self._interromper))

    def _trabalhador(self, grupos: queue.Queue, feitas: set[str]) -> None:
        cliente = self.fabrica()
        while not self._deve_segurar():
            try:
                grupo = grupos.get_nowait()
            except queue.Empty:
                return
            self.loja.iniciar(self.run_id, grupo)

            def gravar(r: ResultadoWO, g=grupo):
                self._gravar(r, feitas)
            with self._trava:
                self._grupos += 1
                verificar = "todas" if self._grupos <= GRUPOS_VERIFICADOS_POR_INTEIRO else "amostra"
            try:
                processar_grupo(cliente, grupo, self.prefs, self.ao_etapa, None, verificar=verificar,
                                ao_lote_desativado=self._lote_desativado, ao_concluir=gravar)
            except SessaoExpirada as exc:
                self._sessao_na_wo(cliente, grupo, exc, feitas)
            except Exception as exc:  # noqa: BLE001 — nunca engolida: vira motivo com traceback
                self._excecao(grupo, exc, feitas)

    def _gravar(self, r: ResultadoWO, feitas: set[str]) -> None:
        estado = estado_da_wo(r)
        c = self.loja.contadores(self.run_id)
        feitos = c["validada"] + c["divergente"] + c["incompleta"] + c["falha"] + 1
        texto = (f"[{feitos}/{sum(c.values())}] {r.wo} — {r.status.value}"
                 + (f" — {r.registros} ocorrência(s)" if r.registros else "")
                 + f" — auditoria: {r.auditoria.value}"
                 + (f" ({r.rodadas_concluidas}/{r.rodadas_previstas})" if r.rodadas_previstas else ""))
        origem = r.origem or "consulta individual"
        tipos = {l.tipo for l in r.leituras}
        if "Releitura por chave" in tipos:
            origem += "; releitura por chave"
        if "Leitura extra" in tipos:
            origem += "; leituras extras individuais (divergência)"
        if self.loja.concluir(self.run_id, r, origem, texto):
            feitas.add(r.wo)
            with self._trava:
                self._concluidas_desde_sonda += 1
            log.info("WO %s | %s | %s | %d ocorrência(s)", r.wo, estado, r.status.value, r.registros)
            if self.prefs.em_erro == "interromper" and estado == "falha" and r.status is not Status.WO_INVALIDA:
                self._interromper.set()
                self.loja.evento(self.run_id, "marco", f"Análise interrompida após falha na WO {r.wo} "
                                 "(configuração: interromper diante de erros).", wo=r.wo)

    def _lote_desativado(self, motivo: str, detalhe: dict) -> None:
        self.loja.evento(self.run_id, "marco", f"Consulta em lote desativada nesta análise ({motivo}); "
                         "as WOs seguintes são consultadas uma a uma.", motivo="lote_desativado", detalhe=detalhe)
        self.loja.problema(self.run_id, "lote_desativado", diagnostico.evidencia(motivo=motivo, **{
            k: v for k, v in (detalhe or {}).items() if k in ("wo", "url", "status")}))

    def _sessao_na_wo(self, cliente: ClienteBTP, grupo: list[str], exc: SessaoExpirada, feitas: set[str]) -> None:
        ok, motivo, ev = self._sondar("confirmação após erro de sessão")
        if ok is not True:
            # A sessão caiu mesmo: as WOs ainda não concluídas do grupo voltam para a fila.
            self.loja.devolver(self.run_id, grupo, exc.motivo)
            self._marcar_queda(exc.motivo if ok is False else motivo or exc.motivo, exc.evidencia or ev)
            return
        # Sessão válida: o erro é de alguma WO específica. Refaz WO a WO; quem repetir, falha sozinha.
        self.loja.evento(self.run_id, "marco", f"Erro de sessão ({exc.motivo}) com a sessão válida: as WOs do "
                         "grupo são refeitas uma a uma.", motivo=exc.motivo, detalhe=exc.evidencia)
        for w in grupo:
            if w in feitas or self.loja.linha_wo(self.run_id, w)["status"] != "em_andamento":
                continue
            try:
                r = processar_wo_seguro(cliente, w, self.prefs, self.ao_etapa)
            except SessaoExpirada as e2:
                ok2, _, _ = self._sondar("confirmação após erro de sessão")
                if ok2 is not True:
                    self.loja.devolver(self.run_id, grupo, e2.motivo)
                    self._marcar_queda(e2.motivo, e2.evidencia)
                    return
                r = ResultadoWO(w, Status.ERRO, auditoria=Auditoria.NAO_CONCLUIDO, motivo=e2.motivo,
                                observacao=f"{e2} Só nesta WO: a sessão do BTP continua válida.",
                                origem="consulta individual")
            self._gravar(r, feitas)

    def _excecao(self, grupo: list[str], exc: Exception, feitas: set[str]) -> None:
        origem = diagnostico.origem(exc)
        log.exception("Erro inesperado no grupo %s", grupo[:3])
        self.loja.problema(self.run_id, "excecao_app", diagnostico.evidencia(
            wo=grupo[0], erro=f"{type(exc).__name__}: {exc}", origem=origem, traceback=diagnostico.rastreio(exc)))
        for w in grupo:
            linha = self.loja.linha_wo(self.run_id, w)
            if linha and linha["status"] == "em_andamento":
                self._gravar(ResultadoWO(w, Status.ERRO, auditoria=Auditoria.NAO_CONCLUIDO, motivo="excecao_app",
                                         observacao=f"Erro inesperado: {type(exc).__name__}: {exc}"), feitas)

    # ------------------------------------------------ sessão
    def _sondar(self, quando: str) -> tuple:
        try:
            ok, motivo, ev = self.sonda()
        except Exception as exc:  # noqa: BLE001 — a sonda nunca derruba a análise
            self.loja.problema(self.run_id, "excecao_app", diagnostico.evidencia(
                erro=f"sonda: {exc}", origem=diagnostico.origem(exc), traceback=diagnostico.rastreio(exc)))
            return None, "excecao_app", {}
        self.loja.evento(self.run_id, "sonda", "", motivo=motivo or "",
                         detalhe={"momento": quando, "ok": ok, **({"evidencia": ev} if ev and ok is not True else {})})
        return ok, motivo, ev

    def _marcar_queda(self, motivo: str, ev: dict) -> None:
        if self._sessao_caiu.is_set():
            return
        self.motivo_pausa, self.evidencia_pausa = motivo or "sessao_expirada", ev or {}
        self._sessao_caiu.set()
        self.loja.problema(self.run_id, self.motivo_pausa, ev or diagnostico.evidencia())
        self.loja.evento(self.run_id, "marco", "Sessão do BTP inválida ("
                         + diagnostico.descricao(self.motivo_pausa).rstrip(".")
                         + "). Análise PAUSADA: nenhuma WO foi marcada como falha por isso. Faça login de novo "
                           "na aba do BTP; o agente retoma sozinho.", motivo=self.motivo_pausa, detalhe=ev)

    def _verificar_sessao_inicial(self) -> bool:
        ok, motivo, ev = self._sondar("antes de começar")
        if ok is False:
            self._marcar_queda(motivo, ev)
            return self._esperar_sessao()
        return True

    def _esperar_sessao(self) -> bool:
        """Pausa até a sessão voltar (aba se anuncia e a sonda passa) ou o usuário parar."""
        inicio = self._relogio()
        self._situacao("pausado_sessao", self.motivo_pausa)
        anuncio = self.anuncios()
        ultima = self._relogio()
        self._cutucar.clear()
        try:
            while not self._parar.is_set():
                agora = self._relogio()
                if self._cutucar.is_set() or self.anuncios() != anuncio or agora - ultima >= REVERIFICAR_PAUSA_SEG:
                    self._cutucar.clear()
                    anuncio, ultima = self.anuncios(), agora
                    if self.conectada():
                        ok, motivo, ev = self._sondar("retomada automática")
                        if ok is True:
                            self._sessao_caiu.clear()
                            self.loja.evento(self.run_id, "marco", "Sessão do BTP válida de novo: análise retomada.")
                            self._situacao("em_andamento")
                            return True
                self._cutucar.wait(1.0)
            return False
        finally:
            self._pausado_total += self._relogio() - inicio

    def _esperar_usuario(self) -> bool:
        inicio = self._relogio()
        self._situacao("pausado_usuario")
        self.loja.evento(self.run_id, "marco", "Análise pausada pelo usuário.")
        ultima = self._relogio()
        try:
            while self._pausa_usuario.is_set() and not self._parar.is_set():
                if self._relogio() - ultima >= KEEPALIVE_PAUSA_USUARIO_SEG and self.conectada():
                    ultima = self._relogio()
                    self._sondar("keep-alive durante a pausa")
                self._cutucar.wait(1.0)
                self._cutucar.clear()
            if self._parar.is_set():
                return False
            self.loja.evento(self.run_id, "marco", "Análise retomada pelo usuário.")
            self._situacao("em_andamento")
            return True
        finally:
            self._pausado_total += self._relogio() - inicio
