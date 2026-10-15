"""Geração do relatório Excel — tudo derivado de UMA estrutura: os ResultadoWO já auditados.

Ordem das abas:
  1 Dados extraídos          — exatamente WO, Material SAP, Fornecedor SAP, Quantidade faltante, Mensagem
  2 Situação por WO          — consolidação por WO (auditoria e situação operacional separadas)
  3 Análise por material SAP — material + fornecedor, com as WOs relacionadas
  4 Itens faltantes          — visão operacional, com consolidado e resultado da auditoria
  5 Resumo                   — indicadores com o significado de cada um
  6 Auditoria                — cada leitura feita ao BTP e o resultado da comparação

Nenhuma aba relê a página: as análises não podem alterar os dados de origem. Ao final, o
arquivo gravado é reaberto e a aba 1 é conferida com os dados (verificar_planilha).
"""
from __future__ import annotations

import math
from collections import Counter, OrderedDict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .modelos import COLUNAS_RESULTADO, STATUS_FALHA, Auditoria, LinhaMensagem, ResultadoWO, Status

ORIGEM_DADOS = "Monitor de Alertas · BTP"
ABAS = ["Dados extraídos", "Situação por WO", "Análise por material SAP", "Itens faltantes", "Resumo", "Auditoria"]

VERMELHO = "DA291C"
_CAB_FONTE = Font(bold=True, color="FFFFFF")
_CAB_FUNDO = PatternFill("solid", fgColor=VERMELHO)
_FINA = Side(style="thin", color="D9D9D9")
_BORDA = Border(bottom=_FINA)
_QUEBRA = Alignment(wrap_text=True, vertical="top")
_TOPO = Alignment(vertical="top")
# Sinalização (legenda na aba Resumo)
_FUNDO = {Auditoria.DIVERGENTE: PatternFill("solid", fgColor="FFE699"),     # amarelo
          Auditoria.INCOMPLETO: PatternFill("solid", fgColor="F8CBAD"),     # salmão
          Auditoria.NAO_CONCLUIDO: PatternFill("solid", fgColor="D9D9D9")}  # cinza
FORMATO_QTD = "#,##0.###"


def nome_analise(pasta: str | Path, numero: int, total_wos: int) -> Path:
    """Ex.: 'Análise 12 - 499 WOs.xlsx'. Nunca sobrescreve um arquivo existente."""
    base = f"Análise {numero} - {total_wos} WO{'s' if total_wos != 1 else ''}"
    destino = Path(pasta) / f"{base}.xlsx"
    extra = 2
    while destino.exists():
        destino = Path(pasta) / f"{base} ({extra}).xlsx"
        extra += 1
    return destino


def linhas_exportadas(resultados: list[ResultadoWO], exportar_incompletos: bool = True) -> list[LinhaMensagem]:
    """As ocorrências que vão para a aba 1 (e para as análises), na ordem das WOs."""
    return [l for r in resultados for l in r.linhas
            if exportar_incompletos or l.auditoria is Auditoria.VALIDADO]


def _txt(v) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _soma(linhas: list[LinhaMensagem]):
    """Soma só quando é válida: todas validadas e com quantidade numérica. Senão None."""
    if not linhas or any(l.auditoria is not Auditoria.VALIDADO or l.quantidade_faltante is None for l in linhas):
        return None
    total = sum((Decimal(str(l.quantidade_faltante)) for l in linhas), Decimal(0))
    return int(total) if total == total.to_integral_value() else total


def _contagem_auditoria(linhas: list[LinhaMensagem]) -> str:
    c = Counter(l.auditoria.value for l in linhas)
    return ", ".join(f"{n} {s.lower()}" for s, n in c.most_common())


def gerar_planilha(resultados: list[ResultadoWO], destino: str | Path, numero: int | None = None,
                   emitida_em: datetime | None = None, origem: str = "", prefs=None,
                   fonte_campos: dict | None = None, releitura_por_chave: bool | None = None) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    emitida_em = emitida_em or datetime.now()
    exportar_incompletos = True if prefs is None else prefs.exportar_incompletos
    linhas = linhas_exportadas(resultados, exportar_incompletos)
    rotulo = f"Análise nº {numero}" if numero else "Análise"
    subtitulo = (f"{rotulo} · {ORIGEM_DADOS} · emitida em {emitida_em:%d/%m/%Y %H:%M} · "
                 f"{len(resultados)} WO(s) consultada(s) · {len(linhas)} ocorrência(s)")
    wb = Workbook()

    # ================================================================ 1. Dados extraídos
    ws = wb.active
    ws.title = ABAS[0]
    _cabecalho(ws, COLUNAS_RESULTADO, linha=1)
    for l in linhas:
        limpas = _append(ws, l.como_lista())
        n = ws.max_row
        for col in (1, 2, 3, 5):
            ws.cell(row=n, column=col).number_format = "@"
        ws.cell(row=n, column=4).number_format = FORMATO_QTD
        ws.cell(row=n, column=5).alignment = _QUEBRA
        for col in (1, 2, 3, 4):
            ws.cell(row=n, column=col).alignment = _TOPO
        if l.auditoria in _FUNDO:   # ocorrência não validada: sinalizada, nunca apresentada como certa
            for col in range(1, 6):
                ws.cell(row=n, column=col).fill = _FUNDO[l.auditoria]
            ws.cell(row=n, column=1).comment = Comment(f"Auditoria: {l.auditoria.value}. {l.observacao}"[:1500],
                                                       "Agente BTP")
        for col, campo in enumerate(("wo", "material", "fornecedor", "quantidade_faltante", "mensagem"), 1):
            nota = l.notas.get(campo, "") if col != 1 else ""
            if col in limpas:
                nota = (nota + " " if nota else "") + "Caracteres de controle do texto original foram removidos."
            if nota and col != 1:
                ws.cell(row=n, column=col).comment = Comment(nota[:1500], "Agente BTP")
    _finalizar(ws, [18, 22, 16, 12, 60], linha_cab=1, filtro=True)

    # ================================================================ 2. Situação por WO
    w2 = wb.create_sheet(ABAS[1])
    _titulo(w2, "Situação por WO", subtitulo)
    cab2 = ["WO", "Ocorrências", "Materiais SAP", "Fornecedores SAP", "Quantidade faltante total",
            "Mensagens", "Resultado da auditoria", "Auditorias concluídas", "Situação operacional", "Observação"]
    _cabecalho(w2, cab2, linha=4)
    for r in resultados:
        ls = linhas_exportadas([r], exportar_incompletos)
        materiais = list(OrderedDict.fromkeys(l.material or "(não obtido)" for l in ls))
        obs = [] if r.observacao == "OK" else [r.observacao]
        if r.observacao_auditoria:
            obs.append(r.observacao_auditoria)
        total = None
        if len(materiais) == 1:
            total = _soma(ls)
            if total is None:
                obs.append("Total não calculado: há quantidade não validada")
        elif len(materiais) > 1:
            obs.append("Vários materiais: o total por material está na aba Análise por material SAP")
        _append(w2, [r.wo, len(ls), ", ".join(materiais),
                   ", ".join(OrderedDict.fromkeys(l.fornecedor or "(não obtido)" for l in ls)), total,
                   " | ".join(OrderedDict.fromkeys(l.mensagem or "(não obtida)" for l in ls)),
                   r.auditoria.value,
                   f"{r.rodadas_concluidas} de {r.rodadas_previstas}" if r.rodadas_previstas else "—",
                   r.status.value, "; ".join(o for o in obs if o)])
        n = w2.max_row
        w2.cell(row=n, column=1).number_format = "@"
        w2.cell(row=n, column=5).number_format = FORMATO_QTD
        for col in (3, 4, 6, 10):
            w2.cell(row=n, column=col).alignment = _QUEBRA
        if r.auditoria in _FUNDO:
            w2.cell(row=n, column=7).fill = _FUNDO[r.auditoria]
    _finalizar(w2, [18, 11, 24, 18, 13, 44, 16, 12, 22, 50], linha_cab=4, filtro=bool(resultados))

    # ================================================================ 3. Análise por material SAP
    w3 = wb.create_sheet(ABAS[2])
    _titulo(w3, "Análise por Material SAP", subtitulo + " · materiais recorrentes primeiro")
    grupos: dict[tuple, list[LinhaMensagem]] = OrderedDict()
    for l in linhas:
        grupos.setdefault((l.material or "(não obtido)", l.fornecedor or "(não obtido)"), []).append(l)
    cab3 = ["Material SAP", "Fornecedor SAP", "Nº de WOs", "WOs relacionadas", "Ocorrências",
            "Quantidade faltante total", "Mensagens", "Situação da auditoria"]
    _cabecalho(w3, cab3, linha=4)
    for (material, fornecedor), ls in sorted(grupos.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        wos = list(OrderedDict.fromkeys(l.wo for l in ls))
        _append(w3, [material, fornecedor, len(wos), ", ".join(wos), len(ls), _soma(ls),
                   " | ".join(OrderedDict.fromkeys(l.mensagem or "(não obtida)" for l in ls)),
                   _contagem_auditoria(ls)])
        n = w3.max_row
        for col in (1, 2):
            w3.cell(row=n, column=col).number_format = "@"
        w3.cell(row=n, column=6).number_format = FORMATO_QTD
        for col in (4, 7, 8):
            w3.cell(row=n, column=col).alignment = _QUEBRA
    _finalizar(w3, [22, 16, 10, 40, 12, 13, 44, 26], linha_cab=4, filtro=bool(grupos))

    # ================================================================ 4. Itens faltantes
    w4 = wb.create_sheet(ABAS[3])
    _titulo(w4, "Itens Faltantes", subtitulo)
    cab4 = ["WO", "Material SAP", "Fornecedor SAP", "Quantidade faltante", "Mensagem", "Consolidado",
            "Auditoria", "Observação da auditoria"]
    _cabecalho(w4, cab4, linha=4)
    for l in linhas:
        _append(w4, l.como_lista() + [l.consolidado, l.auditoria.value, l.observacao])
        n = w4.max_row
        for col in (1, 2, 3, 5, 6):
            w4.cell(row=n, column=col).number_format = "@"
        w4.cell(row=n, column=4).number_format = FORMATO_QTD
        for col in (5, 8):
            w4.cell(row=n, column=col).alignment = _QUEBRA
        if l.auditoria in _FUNDO:
            w4.cell(row=n, column=7).fill = _FUNDO[l.auditoria]
    if not linhas:
        w4.cell(row=5, column=1, value="Nenhuma ocorrência retornada para as WOs consultadas.").font = \
            Font(italic=True, color="808080")
    _finalizar(w4, [18, 22, 16, 12, 50, 28, 14, 50], linha_cab=4, filtro=bool(linhas))

    # ================================================================ 5. Resumo
    w5 = wb.create_sheet(ABAS[4])
    _titulo(w5, "Resumo da Análise", subtitulo)
    _resumo(w5, resultados, linhas, numero, emitida_em, origem, prefs, fonte_campos, releitura_por_chave)

    # ================================================================ 6. Auditoria
    w6 = wb.create_sheet(ABAS[5])
    _titulo(w6, "Auditoria das Leituras", "Cada linha é uma leitura feita ao BTP. Tentativas de auditoria "
                                         "NÃO são ocorrências e não entram nos indicadores.")
    cab6 = ["WO", "Material SAP", "Ocorrência (identificação no BTP)", "Rodada", "Tipo de leitura", "Data/hora",
            "Quantidade faltante", "Fornecedor SAP", "Mensagem", "Resultado da comparação", "Situação final",
            "Observação"]
    _cabecalho(w6, cab6, linha=4)
    for r in resultados:
        for a in r.registros_auditoria:
            _append(w6, [a["wo"], a["material"], a["ocorrencia"], a["rodada"], a["tipo"],
                       a["quando"].replace(microsecond=0) if a.get("quando") else None, a["quantidade"],
                       a["fornecedor"], a["mensagem"], a["resultado"], a["situacao"], a["observacao"]])
            n = w6.max_row
            for col in (1, 2, 3, 8, 9):
                w6.cell(row=n, column=col).number_format = "@"
            w6.cell(row=n, column=6).number_format = "dd/mm/yyyy hh:mm:ss"
            w6.cell(row=n, column=7).number_format = FORMATO_QTD
        if not r.registros_auditoria:
            _append(w6, [r.wo, "—", "—", None, "—", r.data_hora.replace(microsecond=0), None, "", "",
                       r.observacao, r.auditoria.value, r.observacao_auditoria])
            w6.cell(row=w6.max_row, column=6).number_format = "dd/mm/yyyy hh:mm:ss"
    _finalizar(w6, [18, 22, 26, 8, 20, 19, 12, 16, 36, 44, 15, 44], linha_cab=4, filtro=w6.max_row > 4)

    wb.save(destino)
    return destino


def _resumo(ws, resultados, linhas, numero, emitida_em, origem, prefs, fonte_campos, releitura) -> None:
    validos = [r for r in resultados if r.status is not Status.WO_INVALIDA]
    aud = Counter(r.auditoria for r in validos)
    st = Counter(r.status for r in resultados)
    leituras = sum(len(r.leituras) for r in resultados)
    indicadores = [
        ("Nº da análise", numero or "—", "Número sequencial desta análise."),
        ("Data de emissão", emitida_em.replace(microsecond=0), "Início da execução."),
        ("Origem das WOs", origem or "WOs informadas", "De onde vieram as WOs consultadas."),
        ("WOs consultadas", len(resultados), "Total de WOs informadas (inclui formato inválido)."),
        ("WOs com alertas no Monitor", st[Status.PROCESSADA] + st[Status.SEM_REGISTROS],
         "WOs encontradas no Monitor de Alertas."),
        ("WOs com ocorrências (erro operacional)", st[Status.PROCESSADA],
         "WOs com pelo menos um item na janela Mensagens: material com problema."),
        ("Ocorrências de alertas", len(linhas),
         "Linhas da aba Dados extraídos: uma por item/mensagem do BTP. Auditorias não contam aqui."),
        ("Materiais SAP distintos", len({l.material for l in linhas if l.material}),
         "Códigos de material diferentes entre as ocorrências."),
        ("WOs validadas", aud[Auditoria.VALIDADO],
         "Todas as auditorias concluídas concordaram e não faltou nenhum campo."),
        ("WOs com divergências", aud[Auditoria.DIVERGENTE],
         "Algum valor mudou entre leituras; revisar no BTP (ver aba Auditoria)."),
        ("WOs com extração incompleta", aud[Auditoria.INCOMPLETO],
         "Algum dos cinco campos não foi obtido ou a quantidade faltante é ambígua."),
        ("WOs com auditoria não concluída", aud[Auditoria.NAO_CONCLUIDO],
         "Nem todas as auditorias configuradas puderam ser feitas (tempo, falha ou interrupção)."),
        ("WOs com falha de extração", sum(st[s] for s in STATUS_FALHA),
         "Erro, falha de comunicação, sessão expirada, tempo esgotado ou não processada."),
        ("Leituras realizadas ao BTP", leituras,
         "Extrações + auditorias + leituras extras. Medida de esforço, NÃO de ocorrências."),
    ]
    _cabecalho(ws, ["Indicador", "Valor", "Significado"], linha=4)
    for nome, valor, significado in indicadores:
        _append(ws, [nome, valor, significado])
        c = ws.cell(row=ws.max_row, column=2)
        c.alignment = Alignment(horizontal="left", vertical="top")
        if isinstance(valor, datetime):
            c.number_format = "dd/mm/yyyy hh:mm"
        ws.cell(row=ws.max_row, column=3).alignment = _QUEBRA

    def secao(titulo, cab, linhas_secao, pct=None):
        inicio = ws.max_row + 2
        ws.cell(row=inicio, column=1, value=titulo).font = Font(bold=True, size=12)
        _cabecalho(ws, cab, linha=inicio + 1)
        for valores in linhas_secao:
            _append(ws, list(valores))
            for col in range(1, len(cab) + 1):
                ws.cell(row=ws.max_row, column=col).alignment = _QUEBRA
            if pct:
                ws.cell(row=ws.max_row, column=pct).number_format = "0.0%"

    msgs = Counter(l.mensagem or "(não obtida)" for l in linhas)
    wos_msg = {m: len({l.wo for l in linhas if (l.mensagem or "(não obtida)") == m}) for m in msgs}
    secao("Mensagens encontradas (texto original do BTP)", ["Mensagem", "Ocorrências", "WOs"],
          [(m, n, wos_msg[m]) for m, n in msgs.most_common()])
    secao("Situação operacional das WOs", ["Situação", "WOs", "% do total"],
          [(s.value, st[s], st[s] / max(len(resultados), 1)) for s in Status if st[s]], pct=3)
    secao("Resultado da auditoria das WOs", ["Resultado", "WOs", "% das WOs válidas"],
          [(a.value, aud[a], aud[a] / max(len(validos), 1)) for a in Auditoria if aud[a]], pct=3)
    if prefs is not None:
        secao("Parâmetros usados nesta análise", ["Parâmetro", "Valor", "Observação"], [
            ("Auditorias por WO", prefs.auditorias_por_wo, ""),
            ("Auditorias independentes (releitura por chave)",
             "Sim" if prefs.auditorias_independentes else "Não",
             "" if releitura is None else ("Disponível neste serviço" if releitura else
                                           "Indisponível neste serviço: auditorias por nova consulta")),
            ("Diante de divergência", "Reconsultar e sinalizar" if prefs.divergencia == "reconsultar"
             else "Sinalizar para revisão", f"{prefs.consultas_extras_divergencia} leitura(s) extra(s)"),
            ("Tempo máximo por WO", f"{prefs.tempo_max_wo_seg:g} s", ""),
            ("Ocorrências não validadas no Excel", "Incluídas e sinalizadas" if prefs.exportar_incompletos
             else "Omitidas das abas de dados", ""),
        ])
    if fonte_campos:
        secao("Origem dos campos no BTP", ["Coluna", "Campo técnico no BTP", ""],
              [(rot, fonte_campos.get(c, "(não identificado)"), "")
               for c, rot in zip(("wo", "material", "fornecedor", "quantidade_faltante", "mensagem"),
                                 COLUNAS_RESULTADO)])
    secao("Legenda das cores", ["Cor", "Significado", ""], [
        ("Amarelo", "Divergente: valores diferentes entre leituras. Campo sem evidência fica vazio.", ""),
        ("Salmão", "Incompleto: campo não obtido ou quantidade ambígua.", ""),
        ("Cinza", "Auditoria não concluída.", ""),
        ("Sem cor", "Validado.", ""),
    ])
    for cor, linha in (("FFE699", -3), ("F8CBAD", -2), ("D9D9D9", -1)):
        ws.cell(row=ws.max_row + linha, column=1).fill = PatternFill("solid", fgColor=cor)
    _finalizar(ws, [44, 26, 70], linha_cab=4, filtro=False)


def verificar_planilha(destino: str | Path, resultados: list[ResultadoWO],
                       exportar_incompletos: bool = True) -> list[str]:
    """Reabre o arquivo gravado e confere a aba 1, célula a célula, com os dados auditados."""
    esperadas = linhas_exportadas(resultados, exportar_incompletos)
    wb = load_workbook(destino)   # sem read_only: precisa ver o TIPO de cada célula
    if wb.sheetnames != ABAS:
        return [f"Abas fora da ordem esperada: {wb.sheetnames}"]
    aba = wb[ABAS[0]]
    linhas = list(aba.iter_rows(values_only=True))
    problemas = [f"{c.coordinate}: gravado como fórmula ({c.value!r})"
                 for linha in aba.iter_rows() for c in linha if c.data_type == "f"]
    if list(linhas[0]) != COLUNAS_RESULTADO:
        problemas.append(f"Cabeçalho da aba 1 diferente: {linhas[0]}")
    gravadas = linhas[1:]
    if len(gravadas) != len(esperadas):
        problemas.append(f"Aba 1 tem {len(gravadas)} linha(s); esperado {len(esperadas)}.")
    for i, (g, e) in enumerate(zip(gravadas, esperadas), start=2):
        for col, (vg, ve) in enumerate(zip(g, [_limpar(v) for v in e.como_lista()])):
            numeros = isinstance(vg, (int, float)) and isinstance(ve, (int, float, Decimal))
            if numeros and math.isclose(float(vg), float(ve), rel_tol=1e-12):
                continue   # o Excel guarda número com 15 dígitos significativos
            if _txt(vg) != _txt(ve):
                problemas.append(f"Linha {i}, {COLUNAS_RESULTADO[col]}: gravado {vg!r}, esperado {ve!r}")
    return problemas


def _limpar(valor):
    """Remove caracteres de controle que o formato .xlsx não aceita (derrubariam a gravação)."""
    return ILLEGAL_CHARACTERS_RE.sub("", valor) if isinstance(valor, str) else valor


def _append(ws, valores: list) -> set[int]:
    """Acrescenta uma linha SEM interpretar nada: texto que começa com "=" fica texto (nunca
    fórmula). Devolve as colunas (1-based) cujo texto precisou ser limpo."""
    limpos = [_limpar(v) for v in valores]
    ws.append(limpos)
    for celula in ws[ws.max_row]:
        if isinstance(celula.value, str) and celula.data_type == "f":
            celula.data_type = "s"
    return {i for i, (a, b) in enumerate(zip(valores, limpos), start=1) if a != b}


def _titulo(ws, titulo: str, subtitulo: str) -> None:
    ws.cell(row=1, column=1, value=titulo).font = Font(bold=True, size=14, color=VERMELHO)
    ws.cell(row=2, column=1, value=subtitulo).font = Font(size=10, color="595959")
    ws.row_dimensions[1].height = 22


def _cabecalho(ws, colunas: list[str], linha: int) -> None:
    for i, nome in enumerate(colunas, start=1):
        celula = ws.cell(row=linha, column=i, value=nome or None)
        if nome:
            celula.font, celula.fill = _CAB_FONTE, _CAB_FUNDO
        celula.alignment = Alignment(vertical="center", wrap_text=True)


def _finalizar(ws, larguras: list[int], linha_cab: int, filtro: bool) -> None:
    for i, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(i)].width = largura
    ws.freeze_panes = f"A{linha_cab + 1}"
    ultima = get_column_letter(len(larguras))
    if filtro and ws.max_row > linha_cab:
        ws.auto_filter.ref = f"A{linha_cab}:{ultima}{ws.max_row}"
    for linha in ws.iter_rows(min_row=linha_cab + 1, max_row=ws.max_row):
        for celula in linha:
            celula.border = _BORDA
