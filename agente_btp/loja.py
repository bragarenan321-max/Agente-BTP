"""Registro PERSISTENTE de cada análise, WO a WO (SQLite em dados/execucoes.sqlite3).

Por que existe: antes, o resultado só existia em memória até o fim do lote; uma queda de sessão
no meio marcava TODAS as WOs restantes como falha e não havia como retomar.

Regras:
  - cada WO é gravada assim que termina (uma transação por WO), com o payload extraído;
  - uma WO concluída (validada/divergente/incompleta/falha) só muda se for REPROCESSADA de
    propósito (retomada); nenhuma operação reescreve em massa WOs já concluídas;
  - sessão caiu => só as WOs em andamento voltam para "pendente" (nunca viram falha);
  - contadores e registro (log) saem desta mesma base, na mesma transação.

Estados da WO: pendente, em_andamento, validada, divergente, incompleta, falha.
Nada de senha, cookie ou token é gravado aqui.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import zlib
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .modelos import (STATUS_FALHA, Auditoria, Leitura, LinhaMensagem, Ocorrencia, ResultadoWO, Status)
from .diagnostico import categoria

ESTADOS = ("pendente", "em_andamento", "validada", "divergente", "incompleta", "falha")
FINAIS = ("validada", "divergente", "incompleta", "falha")
# Situação da análise inteira.
SITUACOES = ("em_andamento", "pausado_sessao", "pausado_usuario", "interrompida", "concluida",
             "substituida")   # substituida = refeita numa análise nova (não é mais oferecida para retomar)

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS execucoes (
    run_id TEXT PRIMARY KEY, numero INTEGER, criada_em TEXT, atualizada_em TEXT, descricao TEXT,
    total INTEGER, situacao TEXT, motivo TEXT DEFAULT '', prefs TEXT, config TEXT, arquivo TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS wos (
    run_id TEXT, wo TEXT, ordem INTEGER, status TEXT, situacao TEXT DEFAULT '', tentativas INTEGER DEFAULT 0,
    motivo TEXT DEFAULT '', payload BLOB, hash_auditorias TEXT DEFAULT '', origem_dado TEXT DEFAULT '',
    criado_em REAL, iniciado_em REAL, concluido_em REAL,
    PRIMARY KEY (run_id, wo)
);
CREATE INDEX IF NOT EXISTS wos_status ON wos(run_id, status);
CREATE INDEX IF NOT EXISTS wos_fim ON wos(run_id, concluido_em);
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, quando REAL, tipo TEXT, texto TEXT,
    wo TEXT DEFAULT '', status TEXT DEFAULT '', motivo TEXT DEFAULT '', categoria TEXT DEFAULT '',
    detalhe TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS eventos_run ON eventos(run_id, id);
CREATE TABLE IF NOT EXISTS problemas (
    run_id TEXT, motivo TEXT, categoria TEXT, ocorrencias INTEGER, primeira TEXT, ultima_em REAL,
    PRIMARY KEY (run_id, motivo)
);
"""


# ------------------------------------------------------------------ estado da WO
def estado_da_wo(r: ResultadoWO) -> str:
    """Estado gravado a partir do resultado (a mesma regra vale para a tela e para a planilha)."""
    if r.status is Status.WO_INVALIDA or r.status in STATUS_FALHA:
        return "falha"
    if r.auditoria is Auditoria.VALIDADO:
        return "validada"
    if r.auditoria is Auditoria.DIVERGENTE:
        return "divergente"
    return "incompleta"


def motivo_da_wo(r: ResultadoWO) -> str:
    """Motivo (taxonomia do diagnóstico) que explica uma WO não validada ('' = sem problema)."""
    if r.status is Status.WO_INVALIDA:
        return "wo_invalida"
    if r.motivo:
        return r.motivo
    if r.status is Status.NAO_ENCONTRADA and r.auditoria is Auditoria.VALIDADO:
        return "wo_inexistente"
    if r.status in STATUS_FALHA:
        texto = r.observacao.lower()
        if r.status is Status.TEMPO_ESGOTADO or "tempo máximo" in texto:
            return "timeout_wo"
        if "429" in texto:
            return "http_429"
        if "http 5" in texto or "indisponível" in texto:
            return "http_5xx"
        if "404" in texto:
            return "http_404"
        if "json" in texto or "formato de resposta" in texto:
            return "parse_falhou"
        if "erro inesperado" in texto:
            return "excecao_app"
        if r.status is Status.SESSAO_EXPIRADA:
            return "sessao_expirada"
        if "rede" in texto:
            return "falha_rede"
        return "consulta_rejeitada" if r.status is Status.ERRO else "falha_rede"
    if r.auditoria is Auditoria.DIVERGENTE:
        return "divergencia_persistente"
    if r.auditoria in (Auditoria.INCOMPLETO, Auditoria.NAO_CONCLUIDO):
        for l in r.linhas:
            for campo, nota in l.notas.items():
                if nota.startswith("Não obtido"):
                    return f"campo_ausente:{campo}"
        if any(n.startswith("Ambígua") for l in r.linhas for n in l.notas.values()):
            return "quantidade_ambigua"
        if any("tempo máximo" in (l.erro or "") for l in r.leituras):
            return "timeout_wo"
        if any(l.erro for l in r.leituras):
            return "falha_rede"
        return "auditoria_incompleta"      # nunca uma WO não validada sem explicação
    return ""


# ------------------------------------------------------------------ serialização do resultado
def _cod(v):
    if isinstance(v, Decimal):
        return {"$dec": str(v)}
    if isinstance(v, datetime):
        return {"$dt": v.isoformat()}
    if isinstance(v, dict):
        return {str(k): _cod(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_cod(x) for x in v]
    if isinstance(v, (Status, Auditoria)):
        return v.name
    return v


def _dec(v):
    if isinstance(v, dict):
        if set(v) == {"$dec"}:
            return Decimal(v["$dec"])
        if set(v) == {"$dt"}:
            return datetime.fromisoformat(v["$dt"])
        return {k: _dec(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_dec(x) for x in v]
    return v


def serializar(r: ResultadoWO) -> dict:
    """Tudo o que a planilha e o diagnóstico usam. O registro bruto do BTP fica de fora (só os
    valores extraídos de cada leitura), para a base não crescer sem necessidade."""
    def ocorr(o):
        return None if o is None else {"chave": o.chave, "valores": o.valores, "fonte": o.fonte,
                                       "outras_quantidades": o.outras_quantidades, "marca_tempo": o.marca_tempo}
    return _cod({
        "wo": r.wo, "status": r.status, "observacao": r.observacao, "data_hora": r.data_hora,
        "auditoria": r.auditoria, "observacao_auditoria": r.observacao_auditoria,
        "rodadas_previstas": r.rodadas_previstas, "registros_auditoria": r.registros_auditoria,
        "motivo": r.motivo, "origem": r.origem,
        "linhas": [{"consolidado": l.consolidado, "wo": l.wo, "material": l.material, "fornecedor": l.fornecedor,
                    "quantidade_faltante": l.quantidade_faltante, "mensagem": l.mensagem, "chave": l.chave,
                    "auditoria": l.auditoria, "observacao": l.observacao, "notas": l.notas,
                    "mensagem_item": l.mensagem_item} for l in r.linhas],
        "leituras": [{"rodada": l.rodada, "tipo": l.tipo, "quando": l.quando, "alertas": l.alertas,
                      "duplicadas": l.duplicadas, "avisos": l.avisos, "erro": l.erro,
                      "itens": None if l.itens is None else [[k, ocorr(o)] for k, o in l.itens.items()]}
                     for l in r.leituras],
    })


def desserializar(d: dict, leve: bool = False) -> ResultadoWO:
    """`leve`: leituras sem os itens lidos em cada rodada (só para Excel/histórico, que usam as
    linhas finais e a aba de rastreio). Mantém quais leituras deram certo (rodadas concluídas)."""
    if leve:
        d = dict(d, leituras=[dict(l, itens=None if l["itens"] is None else []) for l in d["leituras"]])
    d = _dec(d)

    def ocorr(o):
        return None if o is None else Ocorrencia(o["chave"], o["valores"], {}, {}, o.get("fonte") or {},
                                                 o.get("outras_quantidades") or {}, o.get("marca_tempo"))
    leituras = [Leitura(l["rodada"], l["tipo"], l["quando"],
                        None if l["itens"] is None else {k: ocorr(o) for k, o in l["itens"]},
                        l["alertas"], l["duplicadas"], list(l["avisos"]), l["erro"]) for l in d["leituras"]]
    linhas = [LinhaMensagem(l["consolidado"], l["wo"], l["material"], l["fornecedor"], l["quantidade_faltante"],
                            l["mensagem"], l["chave"], Auditoria[l["auditoria"]], l["observacao"], l["notas"],
                            l.get("mensagem_item", ""))     # análises antigas não têm: ver exportacao.mensagem_do_item
              for l in d["linhas"]]
    return ResultadoWO(d["wo"], Status[d["status"]], linhas, d["observacao"], d["data_hora"],
                       Auditoria[d["auditoria"]], d["observacao_auditoria"], leituras, d["rodadas_previstas"],
                       d["registros_auditoria"], d.get("motivo", ""), d.get("origem", ""))


def hash_auditorias(r: ResultadoWO) -> str:
    """Impressão digital do que foi lido em cada rodada + valores finais (detecta reescrita)."""
    base = [[l.rodada, l.tipo, l.ok, sorted((k, sorted((c, str(v)) for c, v in o.valores.items()) if o else None)
                                           for k, o in (l.itens or {}).items())] for l in r.leituras]
    finais = [[str(x) for x in l.como_lista()] for l in r.linhas]
    return hashlib.sha256(json.dumps([base, finais, r.status.name, r.auditoria.name],
                                     ensure_ascii=False).encode()).hexdigest()


def pendente_como_resultado(wo: str, motivo: str = "") -> ResultadoWO:
    obs = "Ainda não consultada nesta análise (pendente)"
    if motivo:
        obs += f" — {motivo}"
    return ResultadoWO(wo, Status.NAO_PROCESSADA, auditoria=Auditoria.NAO_CONCLUIDO, observacao=obs)


# ------------------------------------------------------------------ a base
class Loja:
    def __init__(self, caminho: str | Path = ":memory:"):
        if str(caminho) != ":memory:":
            Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(caminho), check_same_thread=False, isolation_level=None, timeout=30)
        self._trava = threading.RLock()
        with self._trava:
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.execute("PRAGMA synchronous=NORMAL")
            self._con.executescript(_ESQUEMA)

    # ---------------------------------------------- utilidades
    def _q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._trava:
            cur = self._con.execute(sql, args)
            cols = [c[0] for c in cur.description] if cur.description else []
            return [dict(zip(cols, linha)) for linha in cur.fetchall()]

    def _transacao(self, passos) -> None:
        with self._trava:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                for sql, args in passos:
                    self._con.execute(sql, args)
                self._con.execute("COMMIT")
            except BaseException:
                self._con.execute("ROLLBACK")
                raise

    @staticmethod
    def _agora_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    # ---------------------------------------------- execução
    def criar(self, run_id: str, numero: int, descricao: str, wos: list[str], prefs: dict, config: dict) -> list[str]:
        """Cria a análise com TODAS as WOs pendentes (repetidas na entrada entram uma vez)."""
        unicas = list(dict.fromkeys(wos))
        agora = time.time()
        passos = [("INSERT INTO execucoes(run_id, numero, criada_em, atualizada_em, descricao, total, situacao, "
                   "prefs, config) VALUES (?,?,?,?,?,?,?,?,?)",
                   (run_id, numero, self._agora_iso(), self._agora_iso(), descricao, len(unicas), "em_andamento",
                    json.dumps(prefs), json.dumps(config)))]
        passos += [("INSERT INTO wos(run_id, wo, ordem, status, criado_em) VALUES (?,?,?,?,?)",
                    (run_id, w, i, "pendente", agora)) for i, w in enumerate(unicas)]
        self._transacao(passos)
        return unicas

    def obter(self, run_id: str) -> dict | None:
        linhas = self._q("SELECT * FROM execucoes WHERE run_id=?", (run_id,))
        if not linhas:
            return None
        ex = linhas[0]
        ex["prefs"] = json.loads(ex["prefs"] or "{}")
        ex["config"] = json.loads(ex["config"] or "{}")
        ex["contadores"] = self.contadores(run_id)
        return ex

    def listar(self, limite: int = 20) -> list[dict]:
        saida = []
        for ex in self._q("SELECT run_id, numero, criada_em, atualizada_em, descricao, total, situacao, motivo, "
                          "arquivo FROM execucoes ORDER BY criada_em DESC, numero DESC LIMIT ?", (limite,)):
            ex["contadores"] = self.contadores(ex["run_id"])
            saida.append(ex)
        return saida

    def retomavel(self, aceitar=lambda ex: True) -> dict | None:
        """O que oferecer para retomar: a análise mais recente NÃO TERMINADA (pendentes/em andamento);
        se não houver, a última análise, se ela tiver falhas que valha a pena refazer."""
        recentes = [ex for ex in self.listar(10) if ex["situacao"] != "substituida"
                    and aceitar(dict(ex, config=self._config(ex["run_id"])))]
        for ex in recentes:
            if ex["contadores"]["pendente"] + ex["contadores"]["em_andamento"] > 0:
                ex["restantes"] = len(self.a_processar(ex["run_id"]))
                return ex
        if recentes:
            ex = recentes[0]
            ex["restantes"] = len(self.a_processar(ex["run_id"]))
            if ex["restantes"]:
                return ex
        return None

    def wos_da_execucao(self, run_id: str) -> list[str]:
        """A lista de WOs da análise, na ordem da entrada (para refazê-la sem reimportar)."""
        return [l["wo"] for l in self._q("SELECT wo FROM wos WHERE run_id=? ORDER BY ordem", (run_id,))]

    def _config(self, run_id: str) -> dict:
        linhas = self._q("SELECT config FROM execucoes WHERE run_id=?", (run_id,))
        return json.loads(linhas[0]["config"] or "{}") if linhas else {}

    def apagar(self, run_id: str | None = None) -> None:
        """Remove a análise (ou todas) da base — junto com o histórico dela."""
        alvo = "" if run_id is None else " WHERE run_id=?"
        args = () if run_id is None else (run_id,)
        self._transacao([(f"DELETE FROM {t}{alvo}", args) for t in ("wos", "eventos", "problemas", "execucoes")])

    def definir_situacao(self, run_id: str, situacao: str, motivo: str = "") -> None:
        assert situacao in SITUACOES, situacao
        self._transacao([("UPDATE execucoes SET situacao=?, motivo=?, atualizada_em=? WHERE run_id=?",
                          (situacao, motivo, self._agora_iso(), run_id))])

    def definir_arquivo(self, run_id: str, arquivo: str) -> None:
        self._transacao([("UPDATE execucoes SET arquivo=? WHERE run_id=?", (arquivo, run_id))])

    # ---------------------------------------------- WOs
    def a_processar(self, run_id: str, incluir_incompletas: bool = False) -> list[str]:
        """WOs que uma retomada consulta: pendentes, em andamento e com falha — menos as de número
        inválido (refazer não muda nada: o problema é a entrada)."""
        estados = ["pendente", "em_andamento", "falha"] + (["incompleta"] if incluir_incompletas else [])
        marcas = ",".join("?" * len(estados))
        return [l["wo"] for l in self._q(f"SELECT wo FROM wos WHERE run_id=? AND status IN ({marcas}) "
                                         "AND NOT (status='falha' AND motivo='wo_invalida') ORDER BY ordem",
                                         (run_id, *estados))]

    def iniciar(self, run_id: str, wos: list[str]) -> None:
        agora = time.time()
        self._transacao([("UPDATE wos SET status='em_andamento', tentativas=tentativas+1, iniciado_em=? "
                          "WHERE run_id=? AND wo=?", (agora, run_id, w)) for w in wos])

    def concluir(self, run_id: str, r: ResultadoWO, origem_dado: str = "", texto_log: str = "") -> bool:
        """Grava o resultado de UMA WO e a linha de registro correspondente, juntos.
        Só grava WO que está em andamento (nunca sobrescreve uma concluída sem reprocessamento)."""
        estado = estado_da_wo(r)
        motivo = motivo_da_wo(r)
        payload = zlib.compress(json.dumps(serializar(r), ensure_ascii=False).encode("utf-8"))
        agora = time.time()
        with self._trava:
            atual = self._q("SELECT status FROM wos WHERE run_id=? AND wo=?", (run_id, r.wo))
            if not atual or atual[0]["status"] != "em_andamento":
                return False
            self._transacao([
                ("UPDATE wos SET status=?, situacao=?, motivo=?, payload=?, hash_auditorias=?, origem_dado=?, "
                 "concluido_em=? WHERE run_id=? AND wo=? AND status='em_andamento'",
                 (estado, r.status.value, motivo, payload, hash_auditorias(r), origem_dado, agora, run_id, r.wo)),
                ("INSERT INTO eventos(run_id, quando, tipo, texto, wo, status, motivo, categoria) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 (run_id, agora, "wo", texto_log, r.wo, estado, motivo, categoria(motivo) if motivo else "")),
            ])
        return True

    def devolver(self, run_id: str, wos: list[str], motivo: str) -> int:
        """Sessão caiu / interrupção: WOs EM ANDAMENTO voltam para pendente (não viram falha)."""
        passos = [("UPDATE wos SET status='pendente', motivo=? WHERE run_id=? AND wo=? AND status='em_andamento'",
                   (motivo, run_id, w)) for w in wos]
        self._transacao(passos)
        return len(passos)

    def devolver_em_andamento(self, run_id: str, motivo: str) -> int:
        with self._trava:
            n = self._q("SELECT COUNT(*) AS n FROM wos WHERE run_id=? AND status='em_andamento'", (run_id,))[0]["n"]
            self._transacao([("UPDATE wos SET status='pendente', motivo=? WHERE run_id=? AND status='em_andamento'",
                              (motivo, run_id))])
        return n

    def contadores(self, run_id: str) -> dict:
        c = {e: 0 for e in ESTADOS}
        for l in self._q("SELECT status, COUNT(*) AS n FROM wos WHERE run_id=? GROUP BY status", (run_id,)):
            c[l["status"]] = l["n"]
        return c

    def por_situacao(self, run_id: str) -> dict:
        """Contagem pela situação operacional (Com itens faltantes, Não consta...) das concluídas."""
        return {l["situacao"]: l["n"] for l in self._q(
            "SELECT situacao, COUNT(*) AS n FROM wos WHERE run_id=? AND status IN (?,?,?,?) GROUP BY situacao",
            (run_id, *FINAIS))}

    def estados(self, run_id: str) -> dict[str, str]:
        return {l["wo"]: l["status"] for l in self._q("SELECT wo, status FROM wos WHERE run_id=?", (run_id,))}

    def linha_wo(self, run_id: str, wo: str) -> dict | None:
        l = self._q("SELECT wo, status, situacao, tentativas, motivo, hash_auditorias, origem_dado, iniciado_em, "
                    "concluido_em FROM wos WHERE run_id=? AND wo=?", (run_id, wo))
        return l[0] if l else None

    def resultados(self, run_id: str, leve: bool = False) -> list[ResultadoWO]:
        """Todas as WOs na ordem da entrada; as não concluídas aparecem como pendentes.
        `leve=True` para Excel e histórico (13 mil WOs cabem com folga na memória)."""
        saida = []
        for l in self._q("SELECT wo, status, motivo, payload FROM wos WHERE run_id=? ORDER BY ordem", (run_id,)):
            if l["status"] in FINAIS and l["payload"]:
                saida.append(desserializar(json.loads(zlib.decompress(l["payload"]).decode("utf-8")), leve))
            else:
                saida.append(pendente_como_resultado(l["wo"]))
        return saida

    def estados_ordenados(self, run_id: str) -> list[str]:
        return [l["status"] for l in self._q("SELECT status FROM wos WHERE run_id=? ORDER BY ordem", (run_id,))]

    def taxa(self, run_id: str, janela: float = 60.0) -> float:
        """WOs concluídas por segundo na última janela (medida pela própria base)."""
        agora = time.time()
        l = self._q("SELECT COUNT(*) AS n, MIN(concluido_em) AS ini FROM wos WHERE run_id=? AND concluido_em>=?",
                    (run_id, agora - janela))[0]
        if not l["n"] or l["n"] < 2:
            return 0.0
        return l["n"] / max(agora - l["ini"], 1.0)

    # ---------------------------------------------- registro e diagnóstico
    def evento(self, run_id: str, tipo: str, texto: str, motivo: str = "", wo: str = "", detalhe: dict | None = None):
        self._transacao([("INSERT INTO eventos(run_id, quando, tipo, texto, wo, motivo, categoria, detalhe) "
                          "VALUES (?,?,?,?,?,?,?,?)",
                          (run_id, time.time(), tipo, texto, wo, motivo, categoria(motivo) if motivo else "",
                           json.dumps(detalhe, ensure_ascii=False, default=str) if detalhe else ""))])

    def eventos(self, run_id: str, tipos: tuple | None = None, limite: int = 200, desde: int = 0) -> list[dict]:
        sql, args = "SELECT * FROM eventos WHERE run_id=? AND id>?", [run_id, desde]
        if tipos:
            sql += " AND tipo IN (" + ",".join("?" * len(tipos)) + ")"
            args += list(tipos)
        linhas = self._q(sql + " ORDER BY id DESC LIMIT ?", (*args, limite))
        return list(reversed(linhas))

    def log(self, run_id: str, limite: int = 200) -> list[str]:
        return [f"{datetime.fromtimestamp(e['quando']):%H:%M:%S}  {e['texto']}"
                for e in self.eventos(run_id, limite=limite) if e["texto"]]

    def problema(self, run_id: str, motivo: str, evid: dict) -> None:
        """Conta a ocorrência de um motivo; guarda a evidência só da PRIMEIRA."""
        with self._trava:
            existe = self._q("SELECT ocorrencias FROM problemas WHERE run_id=? AND motivo=?", (run_id, motivo))
            if existe:
                self._transacao([("UPDATE problemas SET ocorrencias=ocorrencias+1, ultima_em=? WHERE run_id=? AND "
                                  "motivo=?", (time.time(), run_id, motivo))])
            else:
                self._transacao([("INSERT INTO problemas VALUES (?,?,?,?,?,?)",
                                  (run_id, motivo, categoria(motivo), 1,
                                   json.dumps(evid, ensure_ascii=False, default=str), time.time()))])

    def problemas(self, run_id: str) -> list[dict]:
        saida = []
        for p in self._q("SELECT * FROM problemas WHERE run_id=? ORDER BY ocorrencias DESC", (run_id,)):
            p["primeira"] = json.loads(p["primeira"] or "{}")
            saida.append(p)
        return saida

    def wos_por_motivo(self, run_id: str) -> dict[str, list[str]]:
        saida: dict[str, list[str]] = {}
        for l in self._q("SELECT wo, motivo FROM wos WHERE run_id=? AND motivo!='' ORDER BY ordem", (run_id,)):
            saida.setdefault(l["motivo"], []).append(l["wo"])
        return saida

    def fechar(self) -> None:
        with self._trava:
            self._con.close()
