"""Orquestrador do Agente BTP.

Conexão: a aba do SAP JÁ ABERTA e logada no navegador do usuário (favorito "Agente BTP").
Preparação (automática, por trás, só na primeira vez):
  1. pelo dicionário de dados do SAP ($metadata) dos apps abertos na aba;
  2. observando em silêncio as consultas que a tela do Monitor faz (prioridade, por ser real).
Uma thread dedicada executa os comandos; o servidor web só lê o estado.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import config as cfg
from . import historico
from .analisador import AnaliseFalhou, analisar
from .autoconfig import candidatos_pela_aba, leitura_por_chave, receita_monitor
from . import diagnostico as diag_mod
from .cliente import CacheExecucao, ClienteBTP, Ritmo
from .exploracao import Explorador
from . import preferencias
from .exportacao import gerar_planilha, nome_analise, verificar_planilha
from .entrada import wo_valida
from .loja import Loja
from .modelos import STATUS_FALHA, Auditoria, ErroConsulta, FalhaComunicacao, SessaoExpirada, Status, TempoEsgotado
from .motor import Motor
from . import odata
from .ponte import PonteAba
from .raiox import raio_x, resumo_texto
from .raiox import salvar as salvar_raiox
from .sondagem import sondar

log = logging.getLogger("agente_btp")

RAIZ = cfg.RAIZ
PASTA_SAIDAS = RAIZ / "saidas"
ARQUIVO_LOJA = RAIZ / "dados" / "execucoes.sqlite3"
# "Conectado" só com sessão CONFIRMADA há pouco (sonda ou consulta de dados bem-sucedida).
VALIDADE_SESSAO_SEG = 120.0
ESPERA_APRENDIZADO_SEG = 300
LIMITE_CANDIDATOS = 80
LIMIAR_SEM_REGISTROS = 0.9   # acima disso, a ligação com as mensagens é considerada errada


class Agente:
    def __init__(self):
        self._fila: queue.Queue = queue.Queue()
        self._trava = threading.Lock()
        self._trava_config = threading.Lock()
        self._parar = threading.Event()
        self._diag: dict = {}
        self._candidatos: list = []
        self._testados: set[str] = set()
        self._achados: dict = {}
        self._sondadas: set[str] = set()
        self._modelos: list = []
        self._explorados: set[str] = set()
        self._resposta: dict | None = None
        self._resposta_fonte = threading.Event()
        self._raiox_pronto = threading.Event()
        self.ponte = PonteAba()
        self.ponte.observador = self._ao_observar
        self.loja = Loja(ARQUIVO_LOJA)
        self._motor: Motor | None = None
        self._run_id: str | None = None
        self._sessao = {"estado": "nao_verificada", "em": 0.0, "motivo": ""}
        self._modelos_lidos = False      # dicionário ($metadata) da aba já lido?
        self._estado = {
            "ocupado": False,
            "fase": "ocioso",            # ocioso | preparando | processando | concluido
            "progresso": {"atual": 0, "total": 0},
            "contagem": {},
            "inicio": None,
            "ultimo_arquivo": None,
            "ultimo_numero": None,
            "etapa": "",
            "indicadores": {},
            "confirmacao": None,
            "ultimo_raiox": None,
            "aviso": "",
            "mensagens": [],
            "run_id": None,
            "situacao": "",
        }
        if cfg.descartar_se_antiga():
            self._msg("Configuração de versão anterior descartada; será refeita na próxima análise.")
        threading.Thread(target=self._laco, name="agente-btp", daemon=True).start()

    # ------------------------------------------------ API pública (servidor web)
    def estado(self) -> dict:
        with self._trava:
            copia = dict(self._estado)
            copia["mensagens"] = list(self._estado["mensagens"][-200:])
            copia["contagem"] = dict(self._estado["contagem"])
            copia["indicadores"] = dict(self._estado["indicadores"])
        copia["pronto"] = cfg.ARQUIVO_CONFIG.exists()
        copia["observadas"] = self.ponte.observadas
        copia.update(self.estado_conexao())
        copia["decorrido_seg"] = round(time.monotonic() - copia.pop("inicio"), 1) if copia["inicio"] else 0
        run_id = copia.get("run_id")
        if run_id:
            # Contadores, registro e progresso saem da MESMA base (nunca de contas paralelas).
            c = self.loja.contadores(run_id)
            total = sum(c.values())
            feitas = c["validada"] + c["divergente"] + c["incompleta"] + c["falha"]
            taxa = self.loja.taxa(run_id)
            restantes = c["pendente"] + c["em_andamento"]
            copia["execucao"] = {
                "run_id": run_id, "numero": copia.get("ultimo_numero"), "contadores": c, "total": total,
                "feitas": feitas, "taxa_wo_s": round(taxa, 2),
                "eta_seg": round(restantes / taxa) if taxa and restantes and copia["fase"] == "processando" else None,
                "erros_por_categoria": self._erros_por_categoria(run_id),
                "situacao": copia.get("situacao") or "",
                "motivo_pausa": self._motor.motivo_pausa if self._motor and copia.get("situacao") == "pausado_sessao" else "",
            }
            copia["progresso"] = {"atual": feitas, "total": total}
            por_situacao = self.loja.por_situacao(run_id)
            copia["indicadores"] = _indicadores_loja(c, por_situacao)
            if copia["fase"] != "concluido":
                copia["contagem"] = por_situacao          # chips ao vivo, da mesma base
            copia["mensagens"] = copia["mensagens"][-30:] + self.loja.log(run_id, 200)
        retomar = None if self._estado["ocupado"] else self.loja.retomavel(aceitar=lambda ex: config_atual(ex["config"]))
        copia["retomavel"] = None if not retomar else {
            "run_id": retomar["run_id"], "numero": retomar["numero"], "total": retomar["total"],
            "validadas": retomar["contadores"]["validada"],
            "feitas": sum(retomar["contadores"][k] for k in ("validada", "divergente", "incompleta", "falha")),
            "restantes": retomar["restantes"],
            "situacao": retomar["situacao"], "descricao": retomar["descricao"]}
        return copia

    def estado_conexao(self) -> dict:
        """Estado HONESTO da conexão: só "conectado" com batimento recente da aba E sessão do BTP
        confirmada há pouco (sonda ou consulta de dados bem-sucedida)."""
        silencio = self.ponte.silencio_seg
        s = dict(self._sessao)
        verificada_ha = round(time.monotonic() - s["em"], 1) if s["em"] else None
        if not self.ponte.conectada:
            conexao = "desconectado"
        elif s["estado"] == "expirada":
            conexao = "sessao_expirada"
        elif s["estado"] == "valida" and verificada_ha is not None and verificada_ha <= VALIDADE_SESSAO_SEG:
            conexao = "conectado"
        else:
            conexao = "aba_conectada"     # aba fala com o programa; sessão do BTP ainda não confirmada
        return {"conexao": conexao, "batimento_seg": silencio, "sessao": s["estado"],
                "sessao_verificada_ha_seg": verificada_ha, "sessao_motivo": s["motivo"]}

    def _sessao_ok(self) -> None:
        self._sessao = {"estado": "valida", "em": time.monotonic(), "motivo": ""}

    def _sessao_caiu(self, motivo: str) -> None:
        self._sessao = {"estado": "expirada", "em": time.monotonic(), "motivo": motivo}

    def _erros_por_categoria(self, run_id: str) -> dict:
        saida: dict = {}
        estados = self.loja.estados(run_id)
        for motivo, wos in self.loja.wos_por_motivo(run_id).items():
            n = sum(1 for w in wos if estados.get(w) in ("falha", "incompleta", "divergente"))
            if n and motivo != "wo_inexistente":
                cat = diag_mod.categoria(motivo)
                saida.setdefault(cat, {})[motivo] = n
        return saida

    def sondar(self, api=None, wo: str | None = None) -> tuple:
        """SONDA da sessão: a MESMA consulta confirmada no tráfego real da tela (lista do Monitor
        filtrada por uma WO). Sessão viva = 200 com JSON/OData. Não inventa endereço."""
        try:
            api = api or cfg.carregar()
        except (FileNotFoundError, ValueError) as exc:
            return None, "sem_configuracao", {"erro": str(exc)}
        wo = wo or getattr(self, "_wo_sonda", None) or "00000|000000000"
        cliente = ClienteBTP(api, self.ponte.get, tempos={"lista": 20.0, "detalhes": 20.0})
        try:
            cliente._get(odata.preencher_modelo(api.url_lista, wo=wo), "lista")
        except SessaoExpirada as exc:
            self._sessao_caiu(exc.motivo)
            return False, exc.motivo, exc.evidencia
        except (FalhaComunicacao, TempoEsgotado) as exc:
            return None, exc.motivo, exc.evidencia
        except ErroConsulta as exc:
            # O BTP RESPONDEU (não é página de login): a sessão está viva, a consulta é que falhou.
            self._sessao_ok()
            return True, exc.motivo, exc.evidencia
        self._sessao_ok()
        return True, "", {}

    def enviar(self, comando: str, *args) -> None:
        if comando in ("executar", "retomar"):
            # Ocupado JÁ no pedido (não só quando a fila o pega): um 2º clique não inicia outra análise.
            self._definir(ocupado=True, fase="preparando", aviso="")
        self._fila.put((comando, args))

    def aba_conectada(self) -> None:
        agora = time.monotonic()
        if agora - getattr(self, "_ultimo_aviso_conexao", -60) > 30:   # sem repetir no log
            self._msg("Aba do BTP conectada.")
            self._ultimo_aviso_conexao = agora
        self._candidatos = []   # nova aba/página: o que foi lido antes pode não valer mais
        self._modelos = []
        self._modelos_lidos = False
        self._testados.clear()
        self._sondadas.clear()
        self._sessao = {"estado": "nao_verificada", "em": 0.0, "motivo": ""}
        if self._motor:
            self._motor.cutucar()          # análise pausada pela sessão: tenta retomar já
        if not cfg.ARQUIVO_CONFIG.exists():
            self.ponte.iniciar_captura()   # aprende em silêncio enquanto o usuário usa o Monitor
            self.enviar("preparar")
        elif not self._estado["ocupado"]:
            self.enviar("sondar")          # mostra na tela se a sessão do BTP está válida

    def parar(self) -> None:
        self._parar.set()
        self._resposta_fonte.set()
        if self._motor:
            self._motor.parar()
        self._msg("Interrupção solicitada; as WOs em andamento são concluídas e as demais ficam pendentes.")

    def pausar(self) -> bool:
        if not self._motor:
            return False
        self._motor.pausar()
        self._msg("Pausa solicitada; as WOs em andamento são concluídas.")
        return True

    def continuar(self) -> bool:
        if not self._motor:
            return False
        self._motor.continuar()
        return True

    def responder_fonte(self, dados: dict) -> None:
        """Resposta do usuário à conferência da fonte (tela): sim | outra | mostrar."""
        self._resposta = dados
        self._resposta_fonte.set()

    # ------------------------------------------------ conferência da fonte com o usuário
    def _confirmar_fonte(self, wos: list[str]) -> bool:
        """Antes do lote, mostra uma amostra REAL e só segue com a confirmação do usuário.

        Evita o erro de processar 499 WOs com uma tabela parecida mas errada: quem conhece a
        janela Mensagens confere uma vez; a fonte confirmada fica gravada para as próximas.
        """
        for _ in range(6):                      # no máximo 6 fontes diferentes por execução
            if not cfg.ARQUIVO_CONFIG.exists():
                return False
            api = cfg.carregar()
            if api.confirmada:
                return True
            if self._origem_config() != "receita_btp" and self._trocar_pela_receita(wos):
                api = cfg.carregar()
            amostra = self._amostra_da_fonte(api, wos)
            self._resposta, self._resposta_fonte = None, threading.Event()
            self._definir(fase="confirmando", confirmacao=amostra, aviso="")
            self._msg("Aguardando a conferência da amostra na tela do agente.")
            self._resposta_fonte.wait(timeout=1800)
            resposta = self._resposta or {}
            self._definir(confirmacao=None)
            if self._parar.is_set() or not resposta:
                self._definir(aviso="Análise não iniciada: a amostra não foi conferida.")
                return False
            if resposta.get("resposta") == "sim":
                dados = json.loads(cfg.ARQUIVO_CONFIG.read_text(encoding="utf-8"))
                dados.update(confirmada=True, mensagem_origem=resposta.get("mensagem") or "item")
                cfg.salvar(dados)
                self._msg("Fonte de dados confirmada pelo usuário.")
                self._definir(fase="preparando")
                return True
            # Não é a janela Mensagens: esta fonte nunca mais é usada.
            dados = json.loads(cfg.ARQUIVO_CONFIG.read_text(encoding="utf-8"))
            _rejeitar(dados)
            cfg.ARQUIVO_CONFIG.unlink(missing_ok=True)
            self._msg("Fonte recusada na conferência; procurando outra.")
            self._candidatos, self._testados, self._sondadas, self._explorados = [], set(), set(), set()
            self._definir(fase="preparando")
            if resposta.get("resposta") == "mostrar":
                if not self._aprender_pela_tela(amostra.get("wo") or next((w for w in wos if wo_valida(w)), "")):
                    return False
            elif not self._garantir_preparo(wos):
                return False
        self._definir(aviso="Nenhuma das fontes encontradas foi confirmada. Use \"Vou mostrar no BTP\".")
        return False

    def _amostra_da_fonte(self, api, wos: list[str]) -> dict:
        """Lê as primeiras WOs (sem auditoria) até achar itens, para o usuário conferir."""
        cliente = ClienteBTP(api, self.ponte.get)
        lidas = []
        for wo in [w for w in wos if wo_valida(w)][:8]:
            try:
                leitura = cliente.ler_wo(wo)
            except (ErroConsulta, FalhaComunicacao) as exc:
                lidas.append(f"{wo}: {exc}")
                continue
            if leitura.itens:
                itens = list(leitura.itens.values())[:6]
                return {"wo": wo, "total": len(leitura.itens), "lidas": lidas,
                        "tem_mensagem_alerta": any(o.valores.get("mensagem_alerta") for o in itens),
                        "itens": [{"material": o.valores["material"], "fornecedor": o.valores["fornecedor"],
                                   "quantidade": odata_texto(o.valores["quantidade_faltante"]),
                                   "mensagem_item": o.valores.get("mensagem_item", ""),
                                   "mensagem_alerta": o.valores.get("mensagem_alerta", "")} for o in itens],
                        "campos": api.campos, "fonte": _descrever_fonte(api)}
            lidas.append(f"{wo}: nenhum item")
        return {"wo": "", "total": 0, "lidas": lidas, "itens": [], "campos": api.campos,
                "tem_mensagem_alerta": False, "fonte": _descrever_fonte(api)}

    def _aprender_pela_tela(self, wo: str) -> bool:
        """O usuário abre a janela Mensagens no BTP; o agente aprende com as consultas da tela."""
        self.ponte.iniciar_captura()
        limite = time.monotonic() + ESPERA_APRENDIZADO_SEG
        while time.monotonic() < limite and not self._parar.is_set():
            if cfg.ARQUIVO_CONFIG.exists():
                self._definir(aviso="")
                return True
            restante = int(limite - time.monotonic())
            self._definir(aviso=f"Na aba do BTP: filtre a WO {wo} no Monitor de Alertas e abra a janela "
                                f"'Mensagens'. O agente aprende com isso e mostra a amostra de novo. ({restante} s)")
            time.sleep(1)
        self._definir(aviso="A janela 'Mensagens' não foi aberta a tempo. Execute de novo.")
        return False

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
        anterior = self._diag.get("dicionario", {})
        diag = {"quando": datetime.now().isoformat(timespec="seconds"),
                "testes": anterior.get("testes", []), "exploracao": anterior.get("exploracao", [])}
        modelos: list = []
        try:
            self._candidatos = candidatos_pela_aba(self.ponte, diag, modelos)
            diag["candidatos"] = len(self._candidatos)
        except Exception as exc:  # noqa: BLE001
            self._candidatos = []
            diag["erro"] = str(exc)[:500]
            log.info("Dicionário de dados: %s", exc)
        self._modelos = modelos
        self._modelos_lidos = True
        self._diag["dicionario"] = diag
        return self._candidatos

    def _receita_disponivel(self) -> dict | None:
        """O caminho CONFIRMADO no BTP real (Pendencias -> UltimoRetornoView, status E), se o
        serviço da aba o oferece e o usuário não o recusou."""
        for servico, modelo in self._modelos:
            dados = receita_monitor(servico, modelo)
            if dados and not _rejeitada(dados):
                return dados
        return None

    def _trocar_pela_receita(self, wos: list[str]) -> bool:
        """Fonte ainda não confirmada e diferente da receita: se a receita traz as mensagens das
        WOs, ela substitui a fonte (a conferência da amostra mostra a receita)."""
        if not self._modelos_lidos:
            self._buscar_candidatos()
        dados = self._receita_disponivel()
        if not dados:
            return False
        amostra = [w for w in wos if wo_valida(w)][:5]
        if self._validar(dados, amostra, self._transporte_com_cache(self.ponte.get), {}) != 2:
            return False
        self._gravar_config(dados, "receita_btp", ["Fonte trocada pelo caminho confirmado da janela Mensagens: "
                                                   "Pendencias -> UltimoRetornoView (status E)."])
        self._msg("Fonte de dados trocada pelo caminho confirmado da janela Mensagens (UltimoRetornoView).")
        return True

    def _validar(self, dados: dict, amostra: list[str], get, achados: dict) -> int:
        """2 = trouxe as mensagens da WO; 1 = nenhuma WO da amostra está na lista; 0 = não serve.

        Achar a WO na lista NÃO basta: se a lista traz o alerta e as mensagens vêm vazias,
        a ligação com a janela "Mensagens" está errada e o caminho é recusado.
        """
        try:
            cliente = ClienteBTP(cfg.a_partir_de_dict(dados), get)
        except ValueError:
            return 0
        com_alerta, falhas = 0, 0
        for wo in amostra:
            try:
                alertas = cliente.buscar_alertas(wo)
                if not alertas:
                    continue
                com_alerta += 1
                achados.setdefault(dados["url_lista"], (wo, alertas[0], dados))
                mensagens = cliente.buscar_mensagens(alertas[0])
                if mensagens:
                    linha = cliente.converter(wo, alertas[0], mensagens[0])
                    outras_wos = {str(v).strip() for v in mensagens[0].values()
                                  if isinstance(v, str) and wo_valida(v.strip())} - {wo}
                    if linha.wo != wo or outras_wos:   # mensagens de outra WO: ligação errada
                        return 0
                    return 2 if (linha.material or linha.mensagem) else 0
            except ErroConsulta:
                return 0
            except FalhaComunicacao:
                falhas += 1          # sem resposta não há como julgar: nunca conta como "lista vazia"
                continue
            if com_alerta >= 2:                 # 2 WOs com alerta e nenhuma mensagem: caminho errado
                return 0
        return 0 if (com_alerta or falhas) else 1

    @staticmethod
    def _transporte_com_cache(get):
        """Durante a preparação, a mesma consulta (ex.: a lista de uma WO) é feita uma vez só."""
        memoria: dict = {}

        def ler(url, accept=None):
            chave = (url, accept)
            if chave not in memoria:
                resposta = get(url, accept) if accept else get(url)
                if resposta.status != 200:
                    return resposta
                memoria[chave] = resposta
            return memoria[chave]
        return ler

    def _preparar_com_wos(self, wos: list[str]) -> bool:
        """Testa os caminhos possíveis com as WOs do usuário; grava só o que trouxer as mensagens."""
        candidatos = self._candidatos or self._buscar_candidatos()
        amostra = [w for w in wos if wo_valida(w)][:5]
        get = self._transporte_com_cache(self.ponte.get)
        testes = self._diag.setdefault("dicionario", {}).setdefault("testes", [])
        # 0º) Caminho confirmado no BTP real (consultas da própria janela Mensagens).
        for servico, modelo in self._modelos:
            dados = receita_monitor(servico, modelo)
            if not dados or _rejeitada(dados):
                continue
            nota = self._validar(dados, amostra, get, {})
            testes.append({"lista": dados["url_lista"], "nota": nota, "mensagens": dados["url_mensagens"],
                           "receita": "janela Mensagens (UltimoRetornoView, status E)"})
            if nota == 2:
                self._gravar_config(dados, "receita_btp", ["Caminho da janela Mensagens confirmado no BTP real: "
                                                           "Pendencias -> UltimoRetornoView (status E)."])
                return True
        # 1º) Pergunta direto aos dados: qual conjunto devolve os itens da janela "Mensagens"?
        novos = [m for m in self._modelos if m[0] not in self._explorados]
        if novos and amostra and not self._parar.is_set():
            self._explorados.update(m[0] for m in novos)
            explorador = Explorador(get, novos, amostra, lambda n: self._definir(
                aviso=f"Identificando a origem dos itens da janela 'Mensagens' ({n} consultas)…"))
            try:
                achados = explorador.explorar()
            finally:
                self._diag["dicionario"].setdefault("exploracao", []).extend(explorador.relato)
                self._definir(aviso="")
            for dados, relatorio in achados[:15]:
                if self._parar.is_set():
                    break
                if _rejeitada(dados):
                    continue
                nota = self._validar(dados, amostra, get, {})
                testes.append({"lista": dados["url_lista"], "nota": nota, "exploracao": relatorio[0],
                               "mensagens": dados.get("url_mensagens") or "mesmo conjunto"})
                if nota == 2:
                    self._gravar_config(dados, "exploracao", relatorio)
                    return True
        fraca = None
        for dados, relatorio in candidatos[:LIMITE_CANDIDATOS]:
            if _rejeitada(dados):
                continue
            chave = json.dumps(dados, sort_keys=True)
            if chave in self._testados:
                continue
            if self._parar.is_set() or cfg.ARQUIVO_CONFIG.exists():
                return cfg.ARQUIVO_CONFIG.exists()
            self._testados.add(chave)
            nota = self._validar(dados, amostra, get, self._achados)
            testes.append({"lista": dados["url_lista"], "nota": nota,
                           "mensagens": dados.get("url_mensagens") or dados.get("campo_mensagens_link")
                           or dados.get("campo_mensagens_expandidas")})
            if nota == 2:
                self._gravar_config(dados, "dicionario", relatorio)
                return True
            if nota == 1 and fraca is None:
                fraca = (dados, relatorio)
        # Nenhum caminho do dicionário trouxe as mensagens: procura nos dados da própria linha.
        for base in self._diag.get("dicionario", {}).get("listas", [])[:10]:
            if base["url_lista"] in self._achados or self._parar.is_set():
                continue
            cliente = ClienteBTP(cfg.ApiConfig(base["url_lista"], base["campo_consolidado_lista"], None, ".", {}), get)
            for wo in amostra[:3]:
                try:
                    alertas = cliente.buscar_alertas(wo)
                except (ErroConsulta, FalhaComunicacao):
                    break
                if alertas:
                    self._achados[base["url_lista"]] = (wo, alertas[0], dict(base, campos={}))
                    break
        for url_lista, (wo, alerta, base) in list(self._achados.items()):
            if url_lista in self._sondadas:
                continue
            self._sondadas.add(url_lista)

            def ler(url):
                return ClienteBTP(cfg.ApiConfig(url_lista, "", None, ".", {}), get)._get(url)

            for parcial, relatorio in sondar(alerta, wo, url_lista, ler):
                dados = dict(base, url_mensagens=None, campo_mensagens_expandidas=None,
                              campo_mensagens_link=None, campo_mensagens_json=None)
                dados.update(parcial)
                nota = self._validar(dados, amostra, get, {})
                testes.append({"lista": url_lista, "sondagem": relatorio[0], "nota": nota})
                if nota == 2:
                    self._gravar_config(dados, "sondagem", relatorio)
                    return True
        if fraca and not self._achados:
            # Nenhuma WO da amostra está no Monitor: não há como conferir as mensagens agora.
            # Fica com a melhor sem erro; a conferência pós-lote a descarta se não servir.
            self._gravar_config(fraca[0], "dicionario", fraca[1])
            return True
        return False

    def _ao_observar(self, consultas: list[dict]) -> None:
        """Chamado a cada lote de consultas observadas na aba (thread HTTP)."""
        if not self._trava_config.acquire(blocking=False):
            return
        try:
            if cfg.ARQUIVO_CONFIG.exists():
                self.ponte.parar_captura()
                return
            # A observação da tela NÃO decide antes de o dicionário ser lido: se o serviço oferece o
            # caminho confirmado da janela Mensagens, é ele que vale (caso real 27/10: a observação
            # gravou RetornoDaBaixa — histórico com dois campos de quantidade — e 2.900 WOs ficaram
            # "incompletas").
            if not self._modelos_lidos or self._receita_disponivel():
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
        if not dados.get("url_item"):
            dados = dict(dados, **leitura_por_chave(dados, self._modelos))
        cfg.salvar(dict(dados, origem=origem))
        if not dados.get("url_item"):
            relatorio = list(relatorio) + ["Releitura por chave indisponível: o serviço não declara chave do item."]
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

    # ------------------------------------------------ Raio-X da fonte (para suporte)
    def refazer_fonte(self) -> None:
        """A fonte gravada não serve: ela é recusada (nunca mais usada) e apagada."""
        if cfg.ARQUIVO_CONFIG.exists():
            try:
                _rejeitar(json.loads(cfg.ARQUIVO_CONFIG.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
            cfg.ARQUIVO_CONFIG.unlink(missing_ok=True)
        self._candidatos, self._testados, self._sondadas, self._explorados = [], set(), set(), set()
        self._msg("Fonte de dados apagada pelo usuário; será procurada e conferida de novo.")

    def raiox_pronto(self) -> None:
        self._raiox_pronto.set()

    def _cmd_raiox(self, wo: str, consolidado: str = "", espera_seg: int = 180) -> None:
        """Fotografa o que o serviço tem para uma WO conhecida + as consultas da tela do BTP."""
        if not self.ponte.autenticado():
            self._definir(fase="ocioso", aviso="Conecte a aba do BTP antes de gerar o Raio-X.")
            return
        self._raiox_pronto = threading.Event()
        self._definir(fase="raiox", inicio=time.monotonic(), ultimo_raiox=None, aviso="", etapa="",
                      progresso={"atual": 0, "total": 0})
        self.ponte.iniciar_captura()
        self._msg(f"Raio-X da fonte: WO {wo}" + (f", consolidado {consolidado}" if consolidado else ""))
        try:
            rel = raio_x(self.ponte, wo, consolidado, lambda t: self._definir(etapa=t))
        except SessaoExpirada:
            self._definir(fase="ocioso", aviso="A sessão do BTP caiu durante o Raio-X. Faça login e repita.")
            return
        limite = time.monotonic() + espera_seg
        while time.monotonic() < limite and not self._raiox_pronto.is_set() and not self._parar.is_set():
            self._definir(etapa="", aviso=f"Agora, na aba do BTP: filtre a WO {wo} no Monitor de Alertas, abra a "
                                          f"janela 'Mensagens' e clique em 'Já abri a janela Mensagens' "
                                          f"(restam {int(limite - time.monotonic())} s).")
            time.sleep(1)
        tela = [dict(c, corpo_resposta=str(c.get("corpo_resposta", ""))[:20000],
                     corpo_requisicao=str(c.get("corpo_requisicao", ""))[:5000])
                for c in self.ponte.consultas_observadas()[-120:]]
        rel["tela_do_btp"] = tela
        rel["resumo"] = resumo_texto(rel)
        if cfg.ARQUIVO_CONFIG.exists():
            rel["configuracao_atual"] = json.loads(cfg.ARQUIVO_CONFIG.read_text(encoding="utf-8"))
            self.ponte.parar_captura()
        PASTA_SAIDAS.mkdir(parents=True, exist_ok=True)
        nome = f"RaioX {wo.replace('|', '-')} {datetime.now():%Y%m%d-%H%M%S}.json"
        salvar_raiox(rel, PASTA_SAIDAS / nome)
        self._definir(fase="raiox_ok", ultimo_raiox=nome, etapa="",
                      aviso=f"Raio-X pronto: {len(tela)} consulta(s) da tela observadas. "
                            "Baixe o arquivo e envie para análise.")
        self._msg(f"Raio-X gravado: {nome}")

    def diagnostico_execucao(self, run_id: str) -> dict | None:
        """O que deu errado numa análise: motivos classificados, evidência, origem, linha do tempo."""
        ex = self.loja.obter(run_id)
        if not ex:
            return None
        c = ex["contadores"]
        por_motivo = self.loja.wos_por_motivo(run_id)
        estados = self.loja.estados(run_id)
        problemas = []
        for p in self.loja.problemas(run_id):
            wos = por_motivo.get(p["motivo"], [])
            problemas.append({
                "motivo": p["motivo"], "categoria": p["categoria"], "descricao": diag_mod.descricao(p["motivo"]),
                "ocorrencias": p["ocorrencias"], "wos_afetadas": len(wos), "exemplos_wo": wos[:10],
                "primeira_ocorrencia": p["primeira"], "proxima_acao": diag_mod.proxima_acao(p["motivo"])})
        vistos = {p["motivo"] for p in problemas}
        for motivo, wos in por_motivo.items():
            afetadas = [w for w in wos if estados.get(w) != "validada" or motivo == "wo_inexistente"]
            if motivo in vistos or not afetadas:
                continue
            problemas.append({"motivo": motivo, "categoria": diag_mod.categoria(motivo),
                              "descricao": diag_mod.descricao(motivo), "ocorrencias": len(afetadas),
                              "wos_afetadas": len(afetadas), "exemplos_wo": afetadas[:10],
                              "primeira_ocorrencia": {}, "proxima_acao": diag_mod.proxima_acao(motivo)})
        problemas.sort(key=lambda p: (p["categoria"] == "ENTRADA", -p["wos_afetadas"], -p["ocorrencias"]))
        linha_do_tempo = [{"quando": datetime.fromtimestamp(e["quando"]).isoformat(timespec="seconds"),
                           "evento": e["texto"], "motivo": e["motivo"]}
                          for e in self.loja.eventos(run_id, tipos=("marco",), limite=500)]
        sondas = self.loja.eventos(run_id, tipos=("sonda",), limite=50)
        restantes = len(self.loja.a_processar(run_id))
        if ex["situacao"] == "pausado_sessao":
            acao = diag_mod.proxima_acao(ex["motivo"] or "sessao_expirada")
        elif restantes:
            acao = f"Use 'Retomar' na tela: {restantes} WO(s) ainda a fazer (as {c['validada']} validadas não são refeitas)."
        elif problemas and problemas[0]["categoria"] != "ENTRADA":
            acao = problemas[0]["proxima_acao"]
        else:
            acao = "Nada a fazer: análise concluída."
        return {"gerado_em": datetime.now().isoformat(timespec="seconds"), "run_id": run_id, "numero": ex["numero"],
                "situacao": ex["situacao"], "motivo_situacao": ex["motivo"], "descricao": ex["descricao"],
                "total": ex["total"], "contadores": c, "problemas": problemas, "linha_do_tempo": linha_do_tempo,
                "ultimas_sondas": [{"quando": datetime.fromtimestamp(e["quando"]).isoformat(timespec="seconds"),
                                    **json.loads(e["detalhe"] or "{}")} for e in sondas[-10:]],
                "proxima_acao": acao, "conexao_agora": self.estado_conexao()}

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

    def _cmd_sondar(self) -> None:
        if cfg.ARQUIVO_CONFIG.exists() and self.ponte.conectada:
            ok, motivo, _ = self.sondar()
            if ok is False:
                self._msg("A aba está conectada, mas a sessão do BTP expirou: faça login de novo na aba.")

    def _cmd_executar(self, wos: list[str], descricao: str = "") -> None:
        inicio_relogio = datetime.now()
        self._parar.clear()
        with self._trava:
            self._estado["mensagens"] = []      # o registro é POR análise (não acumula a anterior)
        self._definir(fase="preparando", progresso={"atual": 0, "total": len(wos)}, contagem={},
                      ultimo_arquivo=None, ultimo_numero=None, aviso="", inicio=time.monotonic(),
                      etapa="", indicadores=_indicadores([], len(wos)), run_id=None, situacao="")
        if not self.ponte.conectada:
            self._definir(fase="ocioso", aviso="Conecte a aba do BTP: clique no favorito 'Agente BTP' na aba já logada.")
            return
        if not self._garantir_preparo(wos) or not self._confirmar_fonte(wos):
            if self._estado["fase"] not in ("falhou",):
                self._definir(fase="ocioso", confirmacao=None)
            self._registrar_historico(inicio_relogio, descricao, wos, [], None,
                                      "Interrompida" if self._parar.is_set() else "Não preparada")
            return
        prefs = preferencias.carregar()
        if preferencias.ultima_migracao:
            self._msg("Configuração atualizada para a nova política: 2 conferências por WO e até 5 quando há "
                      "divergência; sem pausas fixas (" + "; ".join(preferencias.ultima_migracao) + ").")
            preferencias.ultima_migracao.clear()
        dados_api = json.loads(cfg.ARQUIVO_CONFIG.read_text(encoding="utf-8"))
        numero = historico.proximo_numero()
        run_id = inicio_relogio.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
        unicas = self.loja.criar(run_id, numero, descricao, wos, preferencias.como_dict(prefs), dados_api)
        for linha in self._estado["mensagens"]:
            self.loja.evento(run_id, "marco", linha.split("  ", 1)[-1])
        with self._trava:
            self._estado["mensagens"] = []
        if len(unicas) < len(wos):
            self.loja.evento(run_id, "marco", f"{len(wos) - len(unicas)} WO(s) repetida(s) na entrada: cada uma "
                             "é consultada uma vez.")
        self._rodar(run_id, inicio_relogio)

    def _cmd_retomar(self, run_id: str) -> None:
        """Continua uma análise: só as WOs pendentes, em andamento ou com falha; as validadas,
        divergentes e incompletas já gravadas NÃO são refeitas (idempotente)."""
        ex = self.loja.obter(run_id)
        self._parar.clear()
        with self._trava:
            self._estado["mensagens"] = []
        if not ex:
            self._definir(fase="ocioso", aviso="Análise não encontrada para retomar.")
            return
        if not config_atual(ex["config"]):
            self._definir(fase="ocioso", aviso=f"A análise nº {ex['numero']} usou uma fonte de dados que foi descartada "
                                               "(não era a janela Mensagens). Inicie uma nova análise.")
            return
        if not self.ponte.conectada:
            self._definir(fase="ocioso", aviso="Conecte a aba do BTP: clique no favorito 'Agente BTP' na aba já logada.")
            return
        c = ex["contadores"]
        self.loja.evento(run_id, "marco", f"Retomada: {c['validada']} de {ex['total']} já validadas; "
                         f"{len(self.loja.a_processar(run_id))} a fazer.")
        self._rodar(run_id, datetime.fromisoformat(ex["criada_em"]))

    def _rodar(self, run_id: str, inicio_relogio: datetime) -> None:
        ex = self.loja.obter(run_id)
        prefs = preferencias.validar(ex["prefs"])[0]     # a MESMA política e a MESMA fonte da análise
        api = cfg.a_partir_de_dict(ex["config"])
        numero = ex["numero"]
        self._wo_sonda = next((w for w in self.loja.a_processar(run_id) + [w for w in self.loja.estados(run_id)]
                               if wo_valida(w)), None)
        self._definir(fase="processando", inicio=time.monotonic(), run_id=run_id, ultimo_numero=numero,
                      situacao="em_andamento", aviso="", ultimo_arquivo=None)
        self.loja.evento(run_id, "marco", f"Análise nº {numero}: {ex['total']} WO(s) · {prefs.auditorias_por_wo} "
                         f"conferência(s) por WO (até {prefs.auditorias_por_wo + prefs.consultas_extras_divergencia} "
                         f"em divergência) · {prefs.concorrencia} em paralelo · lote de {prefs.tamanho_lote}.")
        if prefs.auditorias_independentes and not (api.url_item and api.chaves_item):
            self.loja.evento(run_id, "marco", "Releitura por chave indisponível neste serviço: as auditorias "
                             "serão novas consultas.")
        ritmo = Ritmo(prefs.intervalo_consultas_seg)
        cache = CacheExecucao()
        if prefs.tamanho_lote <= 1:
            cache.lote_ativo = False

        def ao_erro(exc):
            self.loja.problema(run_id, exc.motivo, dict(exc.evidencia, origem=diag_mod.origem(exc)))

        def fabrica():
            return ClienteBTP(api, self.ponte.get, intervalo=prefs.intervalo_consultas_seg, ritmo=ritmo,
                              cache=cache, ao_erro=ao_erro, ao_sucesso=self._sessao_ok,
                              tempos={"lista": prefs.tempo_consulta_wo_seg,
                                      "detalhes": prefs.tempo_consulta_detalhes_seg})

        def ao_situacao(situacao, motivo):
            self._definir(situacao=situacao, etapa="")
            if situacao == "pausado_sessao":
                self._definir(aviso="A sessão do BTP expirou. Faça login de novo na aba do BTP (e clique no "
                                    "favorito, se preciso): o agente retoma sozinho. Nada foi perdido.")
            elif situacao == "pausado_usuario":
                self._definir(aviso="Análise pausada. Clique em Retomar para continuar.")
            else:
                self._definir(aviso="")

        motor = Motor(self.loja, run_id, fabrica, prefs, sonda=lambda: self.sondar(api, self._wo_sonda),
                      conectada=lambda: self.ponte.conectada, anuncios=lambda: self.ponte.anuncios,
                      ao_etapa=lambda texto: self._definir(etapa=texto), ao_situacao=ao_situacao)
        self._motor = motor
        t0, n0 = time.monotonic(), cache.consultas
        try:
            situacao = motor.executar()
        finally:
            self._motor = None
        duracao = time.monotonic() - t0
        c = self.loja.contadores(run_id)
        self.loja.evento(run_id, "marco", f"Desempenho: {cache.consultas - n0} consulta(s) ao BTP em "
                         f"{duracao:.0f} s" + (f" ({(c['validada'] + c['divergente'] + c['incompleta'] + c['falha']) / duracao:.2f} WO/s)"
                                               if duracao > 0 else "")
                         + ("" if cache.lote_ativo else f"; consulta em lote desativada ({cache.lote_motivo})"))
        resultados = self.loja.resultados(run_id, leve=True)
        arquivo, erro_planilha = self._planilha(run_id, resultados, prefs, api, inicio_relogio, ex["descricao"])
        contagem: dict[str, int] = {}
        for r in resultados:
            contagem[r.status.value] = contagem.get(r.status.value, 0) + 1
        validos = [r for r in resultados if r.status not in (Status.WO_INVALIDA, Status.NAO_PROCESSADA)]
        sem_registros = sum(r.status is Status.SEM_REGISTROS for r in validos)
        aviso = erro_planilha
        if aviso:
            pass
        elif situacao == "interrompida" and c["pendente"]:
            aviso = (f"Análise interrompida: {c['pendente']} WO(s) pendente(s). Use 'Retomar' — as já feitas não "
                     "são refeitas.")
        elif any("HTTP 404" in r.observacao for r in resultados):
            # O SAP foi atualizado e o endereço mudou: o agente se prepara de novo sozinho.
            cfg.ARQUIVO_CONFIG.unlink(missing_ok=True)
            self.ponte.iniciar_captura()
            self.enviar("preparar")
            aviso = "O BTP foi atualizado. O agente já está se ajustando; execute de novo em instantes."
        elif len(validos) >= 3 and sem_registros >= LIMIAR_SEM_REGISTROS * len(validos):
            # A lista encontrou as WOs, mas quase nenhuma trouxe mensagens: a ligação com a
            # janela "Mensagens" não é a certa. Descarta e aprende na próxima execução.
            cfg.ARQUIVO_CONFIG.unlink(missing_ok=True)
            self._candidatos = []
            self.ponte.iniciar_captura()
            aviso = (f"{sem_registros} de {len(validos)} WOs vieram sem os itens da janela 'Mensagens'. "
                     "A configuração foi refeita: execute novamente; se for solicitado, abra a janela "
                     "'Mensagens' de uma WO na aba do BTP.")
        elif self._origem_config() == "dicionario" and validos and all(
                r.status in (Status.ERRO, Status.NAO_ENCONTRADA) for r in validos):
            # A preparação automática pelo dicionário não serviu: descarta e aprende observando.
            cfg.ARQUIVO_CONFIG.unlink(missing_ok=True)
            self.ponte.iniciar_captura()
            aviso = ("Não consegui ler os dados desta vez. Na aba do BTP, abra a janela 'Mensagens' "
                     "de qualquer WO uma vez e execute de novo.")
        nome = arquivo.name if arquivo else None
        self._definir(fase="concluido", contagem=contagem, ultimo_arquivo=nome, aviso=aviso,
                      ultimo_numero=numero, etapa="", situacao=situacao)
        estado_hist = ("Erro na planilha" if erro_planilha else
                       "Interrompida" if situacao == "interrompida" else "Concluída")
        self._registrar_historico(inicio_relogio, ex["descricao"], [r.wo for r in resultados],
                                  [r for r in resultados if r.status is not Status.NAO_PROCESSADA], nome,
                                  estado_hist, numero, run_id=run_id, total=ex["total"])
        self.loja.evento(run_id, "marco", f"Planilha: {nome}" if nome else "O Excel não foi gerado.")

    def _planilha(self, run_id, resultados, prefs, api, emitida_em, descricao, parcial=False):
        """Gera o Excel a partir da base (a qualquer momento). Devolve (arquivo, erro)."""
        ex = self.loja.obter(run_id)
        if parcial:
            destino = PASTA_SAIDAS / f"Análise {ex['numero']} - {ex['total']} WOs (parcial).xlsx"
        elif ex.get("arquivo"):
            destino = PASTA_SAIDAS / ex["arquivo"]       # retomada: atualiza o MESMO arquivo da análise
        else:
            destino = nome_analise(PASTA_SAIDAS, ex["numero"], ex["total"])
        try:
            arquivo = gerar_planilha(resultados, destino, numero=ex["numero"], emitida_em=emitida_em, origem=descricao,
                                     prefs=prefs, fonte_campos=api.campos,
                                     releitura_por_chave=bool(api.url_item and api.chaves_item))
            problemas = verificar_planilha(arquivo, resultados, prefs.exportar_incompletos)
            if not parcial:
                self.loja.definir_arquivo(run_id, arquivo.name)
            if problemas:
                erro = "A conferência do Excel gravado encontrou diferenças: " + "; ".join(problemas[:5])
                log.error(erro)
                return arquivo, erro
            return arquivo, ""
        except Exception as exc:  # noqa: BLE001 — a análise feita não se perde (está na base)
            log.exception("Falha ao gerar o Excel")
            self.loja.problema(run_id, "excecao_app", diag_mod.evidencia(
                erro=f"Excel: {exc}", origem=diag_mod.origem(exc), traceback=diag_mod.rastreio(exc)))
            return None, f"Erro ao gerar ou gravar o Excel: {exc}"

    def planilha_parcial(self, run_id: str) -> Path | None:
        ex = self.loja.obter(run_id)
        if not ex:
            return None
        prefs = preferencias.validar(ex["prefs"])[0]
        api = cfg.a_partir_de_dict(ex["config"])
        arquivo, _ = self._planilha(run_id, self.loja.resultados(run_id, leve=True), prefs, api,
                                    datetime.fromisoformat(ex["criada_em"]), ex["descricao"], parcial=True)
        return arquivo

    def _registrar_historico(self, inicio, descricao, wos, resultados, arquivo, situacao, numero=None,
                             run_id=None, total=None) -> None:
        fim = datetime.now()
        contagem: dict[str, int] = {}
        for r in resultados:
            contagem[r.status.value] = contagem.get(r.status.value, 0) + 1
        total = len(wos) if total is None else total
        try:
            historico.registrar({
                "id": run_id or inicio.strftime("%Y%m%d%H%M%S%f"),
                "run_id": run_id,
                "numero": numero,
                "inicio": inicio.isoformat(timespec="seconds"),
                "fim": fim.isoformat(timespec="seconds"),
                "duracao_seg": round((fim - inicio).total_seconds()),
                "origem": descricao or "WOs coladas",
                "total": total,
                "linhas_excel": sum(r.registros for r in resultados),
                "situacao": situacao,
                "contagem": contagem,
                "indicadores": _indicadores(resultados, total),
                "arquivo": arquivo,
                "wos": [{"wo": r.wo, "status": r.status.value, "registros": r.registros,
                         "auditoria": r.auditoria.value,
                         "auditorias": f"{r.rodadas_concluidas}/{r.rodadas_previstas}" if r.rodadas_previstas else "",
                         "observacao": "; ".join(x for x in (r.observacao if r.observacao != "OK" else "",
                                                             r.observacao_auditoria) if x)}
                        for r in resultados],
            })
        except Exception:  # noqa: BLE001 — histórico nunca derruba a análise
            log.exception("Falha ao gravar o histórico")

    def _garantir_preparo(self, wos: list[str]) -> bool:
        """Na primeira vez, prepara a conexão com o Monitor sem exigir nada técnico do usuário."""
        if cfg.ARQUIVO_CONFIG.exists():
            return True
        self._msg("Preparando a conexão com o Monitor de alertas (só na primeira vez)…")
        self._testados, self._achados, self._sondadas = set(), {}, set()
        self._explorados = set()
        self.ponte.iniciar_captura()
        try:
            if self._preparar_com_wos(wos):
                return True
        except SessaoExpirada:
            self._definir(aviso="A sessão do BTP caiu. Faça login na aba e clique no favorito 'Agente BTP'.")
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
            self._definir(aviso=f"Ação necessária (uma única vez): na aba do BTP, filtre uma WO no Monitor de "
                                f"alertas e abra a janela 'Mensagens'. A análise prossegue automaticamente. "
                                f"({restante} s)")
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
        self._definir(aviso="Não foi possível identificar a origem das mensagens. Baixe o diagnóstico "
                            "(abaixo) e envie o arquivo para ajuste.")
        self._definir(fase="falhou")
        return False

    # ------------------------------------------------ auxiliares
    def _definir(self, **valores) -> None:
        with self._trava:
            self._estado.update(valores)

    def _msg(self, texto: str) -> None:
        log.info(texto)
        run_id = self._estado.get("run_id") if self._motor else None
        if run_id:
            self.loja.evento(run_id, "marco", texto)   # durante a análise: no registro da própria análise
            return
        with self._trava:
            self._estado["mensagens"].append(f"{datetime.now():%H:%M:%S}  {texto}")


ARQUIVO_REJEITADAS = RAIZ / "config" / "fontes_recusadas.json"


def _assinatura(dados: dict) -> str:
    chaves = ("url_lista", "url_mensagens", "campo_mensagens_expandidas", "campo_mensagens_link",
              "campo_mensagens_json")
    return json.dumps([dados.get(k) for k in chaves] + [sorted((dados.get("campos") or {}).items())])


def _rejeitar(dados: dict) -> None:
    try:
        lista = json.loads(ARQUIVO_REJEITADAS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        lista = []
    lista.append(_assinatura(dados))
    ARQUIVO_REJEITADAS.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO_REJEITADAS.write_text(json.dumps(sorted(set(lista)), ensure_ascii=False, indent=1), encoding="utf-8")


def _rejeitada(dados: dict) -> bool:
    try:
        return _assinatura(dados) in json.loads(ARQUIVO_REJEITADAS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def odata_texto(valor) -> str:
    from .exportacao import _txt
    return _txt(valor)


def _indicadores(resultados: list, total: int) -> dict:
    """Painel da tela: só conta uma WO como validada DEPOIS de todas as auditorias dela."""
    validos = [r for r in resultados if r.status is not Status.WO_INVALIDA]
    return {
        "processadas": len(resultados), "pendentes": max(total - len(resultados), 0),
        "validadas": sum(r.auditoria is Auditoria.VALIDADO for r in validos),
        "divergentes": sum(r.auditoria is Auditoria.DIVERGENTE for r in validos),
        "incompletas": sum(r.auditoria in (Auditoria.INCOMPLETO, Auditoria.NAO_CONCLUIDO)
                           and r.status not in STATUS_FALHA for r in validos),
        "falhas": sum(r.status in STATUS_FALHA for r in resultados),
        "com_ocorrencias": sum(r.status is Status.PROCESSADA for r in resultados),
        "ocorrencias": sum(r.registros for r in resultados),
    }


def _descrever_fonte(api) -> str:
    """De qual tabela do BTP os itens vêm (mostrado na conferência da amostra)."""
    def conjunto(url):
        return (url or "").split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].split("(", 1)[0]
    lista = conjunto(api.url_lista)
    if api.url_mensagens:
        itens = conjunto(api.url_mensagens)
        filtro = " (só linhas com status E)" if "status eq 'E'" in api.url_mensagens else ""
        return f"{lista} → {itens}{filtro}"
    return f"{lista} (a própria lista traz os itens)" if api.campo_mensagens_expandidas == "." else lista


def config_atual(config: dict) -> bool:
    """A fonte gravada numa análise ainda é aceita pela versão atual do programa?"""
    return int((config or {}).get("versao", 0) or 0) >= cfg.VERSAO_CONFIG


def _indicadores_loja(c: dict, por_situacao: dict) -> dict:
    """Painel da tela a partir da base (mesma fonte do registro)."""
    feitas = c["validada"] + c["divergente"] + c["incompleta"] + c["falha"]
    return {"processadas": feitas, "pendentes": c["pendente"] + c["em_andamento"],
            "validadas": c["validada"], "divergentes": c["divergente"], "incompletas": c["incompleta"],
            "falhas": c["falha"], "com_ocorrencias": por_situacao.get(Status.PROCESSADA.value, 0),
            "em_andamento": c["em_andamento"]}


def caminho_saida(nome: str) -> Path | None:
    """Resolve um arquivo de saída sem permitir sair da pasta (../)."""
    caminho = (PASTA_SAIDAS / nome).resolve()
    if caminho.parent != PASTA_SAIDAS.resolve() or not caminho.is_file():
        return None
    return caminho
