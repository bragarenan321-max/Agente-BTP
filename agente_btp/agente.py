"""Orquestrador do Agente BTP.

Conexão: a aba do SAP JÁ ABERTA e logada no navegador do usuário (favorito "Agente BTP").
Preparação (automática, por trás, só na primeira vez):
  1. pelo dicionário de dados do SAP ($metadata) dos apps abertos na aba;
  2. observando em silêncio as consultas que a tela do Monitor faz (prioridade, por ser real).
Uma thread dedicada executa os comandos; o servidor web só lê o estado.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config as cfg
from . import historico
from .analisador import AnaliseFalhou, analisar
from .autoconfig import MetadataSemCorrespondencia, candidatos_pela_aba
from .cliente import ClienteBTP
from .exportacao import gerar_planilha, nome_padrao
from .entrada import wo_valida
from .modelos import ErroConsulta, FalhaComunicacao, SessaoExpirada, Status
from .ponte import PonteAba
from .processador import processar_lote

log = logging.getLogger("agente_btp")

RAIZ = cfg.RAIZ
PASTA_SAIDAS = RAIZ / "saidas"
ESPERA_APRENDIZADO_SEG = 180


class Agente:
    def __init__(self):
        self._fila: queue.Queue = queue.Queue()
        self._trava = threading.Lock()
        self._trava_config = threading.Lock()
        self._parar = threading.Event()
        self._diag: dict = {}
        self._candidatos: list = []
        self.ponte = PonteAba()
        self.ponte.observador = self._ao_observar
        self._estado = {
            "ocupado": False,
            "fase": "ocioso",            # ocioso | preparando | processando | concluido
            "progresso": {"atual": 0, "total": 0},
            "contagem": {},
            "inicio": None,
            "ultimo_arquivo": None,
            "aviso": "",
            "mensagens": [],
        }
        threading.Thread(target=self._laco, name="agente-btp", daemon=True).start()

    # ------------------------------------------------ API pública (servidor web)
    def estado(self) -> dict:
        with self._trava:
            copia = dict(self._estado)
            copia["mensagens"] = list(self._estado["mensagens"][-200:])
            copia["contagem"] = dict(self._estado["contagem"])
        copia["pronto"] = cfg.ARQUIVO_CONFIG.exists()
        copia["observadas"] = self.ponte.observadas
        copia["conexao"] = ("conectado" if self.ponte.autenticado() else
                            "sem_login" if self.ponte.conectada else "desconectado")
        copia["decorrido_seg"] = round(time.monotonic() - copia.pop("inicio"), 1) if copia["inicio"] else 0
        return copia

    def enviar(self, comando: str, *args) -> None:
        self._fila.put((comando, args))

    def aba_conectada(self) -> None:
        self._msg("Aba do SAP conectada.")
        self._candidatos = []   # nova aba/página: o que foi lido antes pode não valer mais
        if not cfg.ARQUIVO_CONFIG.exists():
            self.ponte.iniciar_captura()   # aprende em silêncio enquanto o usuário usa o Monitor
            self.enviar("preparar")

    def parar(self) -> None:
        self._parar.set()
        self._msg("Interrupção solicitada; a WO atual será concluída.")

    # ------------------------------------------------ execução
    def _laco(self) -> None:
        while True:
            comando, args = self._fila.get()
            self._definir(ocupado=True)
            try:
                getattr(self, f"_cmd_{comando}")(*args)
            except Exception as exc:  # noqa: BLE001
                log.exception("Falha no comando %s", comando)
                self._msg(f"ERRO: {exc}")
            finally:
                self._definir(ocupado=False)

    def _cmd_preparar(self) -> None:
        """Ao conectar: já busca as configurações candidatas (o teste é feito com as WOs)."""
        if cfg.ARQUIVO_CONFIG.exists() or not self.ponte.conectada:
            return
        self._buscar_candidatos()

    def _buscar_candidatos(self) -> list:
        diag = {"quando": datetime.now().isoformat(timespec="seconds")}
        try:
            self._candidatos = candidatos_pela_aba(self.ponte, diag)
            diag["candidatos"] = len(self._candidatos)
        except Exception as exc:  # noqa: BLE001
            self._candidatos = []
            diag["erro"] = str(exc)[:500]
            log.info("Dicionário de dados: %s", exc)
        self._diag["dicionario"] = diag
        return self._candidatos

    def _validar(self, dados: dict, amostra: list[str]) -> int:
        """2 = trouxe dados reais; 1 = sem erro mas sem dados; 0 = não serve."""
        try:
            cliente = ClienteBTP(cfg.a_partir_de_dict(dados), self.ponte.get)
        except ValueError:
            return 0
        for wo in amostra:
            try:
                alertas = cliente.buscar_alertas(wo)
                if alertas:
                    mensagens = cliente.buscar_mensagens(alertas[0])
                    if not mensagens:
                        continue
                    linha = cliente.converter(wo, alertas[0], mensagens[0])
                    return 2 if (linha.material or linha.mensagem) else 0
            except ErroConsulta:
                return 0
            except FalhaComunicacao:
                continue
        return 1

    def _preparar_com_wos(self, wos: list[str]) -> bool:
        """Testa as candidatas com as WOs do usuário e grava a que trouxer dados de verdade."""
        candidatos = self._candidatos or self._buscar_candidatos()
        if not candidatos:
            return False
        amostra = [w for w in wos if wo_valida(w)][:5]
        testes, fraca = [], None
        for dados, relatorio in candidatos[:20]:
            nota = self._validar(dados, amostra)
            testes.append({"lista": dados["url_lista"], "mensagens": dados.get("url_mensagens"), "nota": nota})
            if nota == 2:
                self._diag.setdefault("dicionario", {})["testes"] = testes
                self._gravar_config(dados, "dicionario", relatorio)
                return True
            if nota == 1 and fraca is None:
                fraca = (dados, relatorio)
        self._diag.setdefault("dicionario", {})["testes"] = testes
        if fraca:
            # Nenhuma das WOs de teste tem pendência; fica com a melhor sem erro.
            self._gravar_config(fraca[0], "dicionario", fraca[1])
            return True
        return False

    def _ao_observar(self, consultas: list[dict]) -> None:
        """Chamado a cada lote de consultas observadas na aba (thread HTTP)."""
        if not self._trava_config.acquire(blocking=False):
            return
        try:
            if cfg.ARQUIVO_CONFIG.exists() and self._origem_config() == "observacao":
                self.ponte.parar_captura()
                return
            try:
                dados, relatorio = analisar({"requisicoes": consultas})
            except AnaliseFalhou:
                return
            self._gravar_config(dados, "observacao", relatorio)
            self.ponte.parar_captura()
        finally:
            self._trava_config.release()

    def _gravar_config(self, dados: dict, origem: str, relatorio: list[str]) -> None:
        cfg.salvar(dict(dados, origem=origem))
        for linha in relatorio:
            log.info("Configuração (%s): %s", origem, linha)
        self._msg("Conexão com o Monitor de alertas preparada.")

    @staticmethod
    def _origem_config() -> str:
        try:
            import json
            return json.loads(cfg.ARQUIVO_CONFIG.read_text(encoding="utf-8")).get("origem", "")
        except Exception:  # noqa: BLE001
            return ""

    def diagnostico(self) -> dict:
        """Tudo o que ajuda a entender por que a preparação não concluiu (sem senhas/cookies)."""
        consultas = [dict(c, corpo_resposta=str(c.get("corpo_resposta", ""))[:3000],
                          corpo_requisicao=str(c.get("corpo_requisicao", ""))[:3000])
                     for c in self.ponte.consultas_observadas()[-60:]]
        e = self.estado()
        return {"gerado_em": datetime.now().isoformat(timespec="seconds"),
                "conexao": e["conexao"], "pronto": e["pronto"], "versao_agente": self.ponte.versao_agente,
                "pagina_da_aba": self.ponte.pagina, "consultas_observadas_total": self.ponte.observadas,
                "preparacao": self._diag, "consultas_observadas": consultas,
                "registro": e["mensagens"][-200:]}

    def _cmd_executar(self, wos: list[str], descricao: str = "") -> None:
        inicio_relogio = datetime.now()
        self._parar.clear()
        self._definir(fase="preparando", progresso={"atual": 0, "total": len(wos)}, contagem={},
                      ultimo_arquivo=None, aviso="", inicio=time.monotonic())
        if not self.ponte.autenticado():
            self._definir(fase="ocioso", aviso="Conecte a aba do SAP: clique no favorito 'Agente BTP' na aba já logada.")
            return
        if not self._garantir_preparo(wos):
            if self._estado["fase"] != "falhou":
                self._definir(fase="ocioso")
            self._registrar_historico(inicio_relogio, descricao, wos, [], None,
                                      "Interrompida" if self._parar.is_set() else "Não preparada")
            return

        self._definir(fase="processando", inicio=time.monotonic())
        self._msg(f"Processando {len(wos)} WO(s).")
        cliente = ClienteBTP(cfg.carregar(), self.ponte.get)

        def ao_progredir(atual, total, resultado):
            with self._trava:
                cont = self._estado["contagem"]
                cont[resultado.status.value] = cont.get(resultado.status.value, 0) + 1
                self._estado["progresso"] = {"atual": atual, "total": total}
            self._msg(f"[{atual}/{total}] {resultado.wo} — {resultado.status.value}"
                      + (f" — {resultado.registros} linha(s)" if resultado.registros else ""))

        resultados = processar_lote(cliente, wos, ao_progredir, self._parar.is_set)
        contagem: dict[str, int] = {}
        for r in resultados:
            contagem[r.status.value] = contagem.get(r.status.value, 0) + 1
        arquivo = gerar_planilha(resultados, nome_padrao(PASTA_SAIDAS))

        aviso = ""
        if any(r.status is Status.SESSAO_EXPIRADA for r in resultados):
            aviso = ("A sessão do SAP caiu no meio do processamento. Faça login de novo na aba, "
                     "clique no favorito 'Agente BTP' e execute novamente.")
        elif any("HTTP 404" in r.observacao for r in resultados):
            # O SAP foi atualizado e o endereço mudou: o agente se prepara de novo sozinho.
            cfg.ARQUIVO_CONFIG.unlink(missing_ok=True)
            self.ponte.iniciar_captura()
            self.enviar("preparar")
            aviso = "O SAP foi atualizado. O agente já está se ajustando; execute de novo em instantes."
        elif self._origem_config() == "dicionario" and resultados and all(
                r.status in (Status.ERRO, Status.NAO_ENCONTRADA) for r in resultados):
            # A preparação automática pelo dicionário não serviu: descarta e aprende observando.
            cfg.ARQUIVO_CONFIG.unlink(missing_ok=True)
            self.ponte.iniciar_captura()
            aviso = ("Não consegui ler os dados desta vez. Na aba do SAP, abra a janela 'Mensagens' "
                     "de qualquer WO uma vez e execute de novo.")
        self._definir(fase="concluido", contagem=contagem, ultimo_arquivo=arquivo.name, aviso=aviso,
                      progresso={"atual": len(resultados), "total": len(wos)})
        situacao = ("Sessão expirada" if any(r.status is Status.SESSAO_EXPIRADA for r in resultados) else
                    "Interrompida" if self._parar.is_set() else "Concluída")
        self._registrar_historico(inicio_relogio, descricao, wos, resultados, arquivo.name, situacao)
        self._msg(f"Concluído. Planilha: {arquivo.name}")

    def _registrar_historico(self, inicio, descricao, wos, resultados, arquivo, situacao) -> None:
        fim = datetime.now()
        contagem: dict[str, int] = {}
        for r in resultados:
            contagem[r.status.value] = contagem.get(r.status.value, 0) + 1
        try:
            historico.registrar({
                "id": inicio.strftime("%Y%m%d%H%M%S%f"),
                "inicio": inicio.isoformat(timespec="seconds"),
                "fim": fim.isoformat(timespec="seconds"),
                "duracao_seg": round((fim - inicio).total_seconds()),
                "origem": descricao or "WOs coladas",
                "total": len(wos),
                "linhas_excel": sum(r.registros for r in resultados),
                "situacao": situacao,
                "contagem": contagem,
                "arquivo": arquivo,
                "wos": [{"wo": r.wo, "status": r.status.value, "registros": r.registros,
                         "observacao": r.observacao} for r in resultados],
            })
        except Exception:  # noqa: BLE001 — histórico nunca derruba a análise
            log.exception("Falha ao gravar o histórico")

    def _garantir_preparo(self, wos: list[str]) -> bool:
        """Na primeira vez, prepara a conexão com o Monitor sem exigir nada técnico do usuário."""
        if cfg.ARQUIVO_CONFIG.exists():
            return True
        self._msg("Preparando a conexão com o Monitor de alertas (só na primeira vez)…")
        self.ponte.iniciar_captura()
        try:
            if self._preparar_com_wos(wos):
                return True
        except SessaoExpirada:
            self._definir(aviso="A sessão do SAP caiu. Faça login na aba e clique no favorito 'Agente BTP'.")
            return False
        limite = time.monotonic() + ESPERA_APRENDIZADO_SEG
        proxima_tentativa = time.monotonic() + 20
        while not self._parar.is_set():
            restante = int(limite - time.monotonic())
            if cfg.ARQUIVO_CONFIG.exists():
                self._definir(aviso="")
                return True
            if restante <= 0:
                break
            self._definir(aviso=f"Na aba do SAP, abra o Monitor de alertas e clique em qualquer WO para abrir a "
                                f"janela 'Mensagens'. O agente aprende com isso e continua sozinho. ({restante} s)")
            if time.monotonic() >= proxima_tentativa:   # o usuário pode ter acabado de abrir o Monitor
                proxima_tentativa = time.monotonic() + 20
                self._candidatos = []
                try:
                    if self._preparar_com_wos(wos):
                        self._definir(aviso="")
                        return True
                except SessaoExpirada:
                    break
            time.sleep(1)
        self._definir(aviso="Não consegui preparar a conexão com o Monitor. Clique em 'Baixar diagnóstico' "
                            "(abaixo) e envie o arquivo para ajuste.")
        self._definir(fase="falhou")
        return False

    # ------------------------------------------------ auxiliares
    def _definir(self, **valores) -> None:
        with self._trava:
            self._estado.update(valores)

    def _msg(self, texto: str) -> None:
        log.info(texto)
        with self._trava:
            self._estado["mensagens"].append(f"{datetime.now():%H:%M:%S}  {texto}")


def caminho_saida(nome: str) -> Path | None:
    """Resolve um arquivo de saída sem permitir sair da pasta (../)."""
    caminho = (PASTA_SAIDAS / nome).resolve()
    if caminho.parent != PASTA_SAIDAS.resolve() or not caminho.is_file():
        return None
    return caminho
