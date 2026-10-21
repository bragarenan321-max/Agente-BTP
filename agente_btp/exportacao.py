"""Relatório Excel — direto e autoexplicativo, derivado de UMA estrutura (os ResultadoWO auditados).

Abas:
  Dados    — TODAS as WOs consultadas: WO, Material SAP, Fornecedor SAP, Quantidade faltante,
             Mensagem e Observação (vazia quando está tudo certo; em português claro quando não).
  Análise  — números da análise, materiais mais recorrentes, mensagens e a situação de cada WO.
  Rastreio — (oculta) cada leitura feita ao BTP, para investigar uma divergência se preciso.

Ao final, o arquivo gravado é reaberto e a aba Dados é conferida célula a célula.
"""
from __future__ import annotations

import math
from collections import Counter, OrderedDict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .modelos import CAMPOS, ROTULO, STATUS_FALHA, Auditoria, LinhaMensagem, ResultadoWO, Status

ABAS = ["Dados", "Análise", "Rastreio"]
COLUNAS_DADOS = ["WO", "Material SAP", "Fornecedor SAP", "Quantidade faltante", "Mensagem", "Observação", "Status"]
# Status da WO na análise (o mesmo da base de execução e da tela).
ROTULO_ESTADO = {"validada": "Validada", "divergente": "Divergente", "incompleta": "Incompleta",
                 "falha": "Falha", "pendente": "Pendente"}

VERMELHO = "DA291C"
_CAB_FONTE = Font(bold=True, color="FFFFFF")
_CAB_FUNDO = PatternFill("solid", fgColor=VERMELHO)
_ATENCAO = PatternFill("solid", fgColor="FFF2CC")       # só a célula de Observação, quando há algo
_BORDA = Border(bottom=Side(style="thin", color="D9D9D9"))
_QUEBRA = Alignment(wrap_text=True, vertical="top")
_TOPO = Alignment(vertical="top")

# Situação da WO em linguagem direta (sem jargão).
SITUACAO = {
    Status.PROCESSADA: "Com itens faltantes",
    Status.SEM_REGISTROS: "No Monitor, sem itens na janela Mensagens",
    Status.NAO_ENCONTRADA: "Nenhum alerta encontrado no Monitor para esta WO",
    Status.WO_INVALIDA: "Número de WO inválido",
    Status.SESSAO_EXPIRADA: "Não consultada: sessão do BTP expirou",
    Status.FALHA_COMUNICACAO: "Não consultada: falha de comunicação",
    Status.TEMPO_ESGOTADO: "Não consultada: tempo esgotado",
    Status.NAO_PROCESSADA: "Pendente: ainda não consultada",
    Status.ERRO: "Não consultada: erro na consulta",
}


def nome_analise(pasta: str | Path, numero: int, total_wos: int) -> Path:
    """Ex.: 'Análise 12 - 499 WOs.xlsx'. Nunca sobrescreve um arquivo existente."""
    base = f"Análise {numero} - {total_wos} WO{'s' if total_wos != 1 else ''}"
    destino = Path(pasta) / f"{base}.xlsx"
    extra = 2
    while destino.exists():
        destino = Path(pasta) / f"{base} ({extra}).xlsx"
        extra += 1
    return destino


def _txt(v) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def observacao_item(l: LinhaMensagem) -> str:
    """Observação em português claro; vazia quando o item foi conferido sem problema."""
    if l.auditoria is Auditoria.VALIDADO:
        return ""
    partes = []
    for campo in CAMPOS:
        nota = l.notas.get(campo, "")
        if not nota:
            continue
        rotulo = ROTULO[campo]
        if nota.startswith("Divergente"):
            valores = nota.split(":", 1)[1].split(".")[0].strip()
            valores = " e ".join(v.split("=", 1)[-1] for v in valores.split(" / "))
            if "Resolvida" in nota:
                partes.append(f"{rotulo} mudou no BTP durante a consulta ({valores}); vale o valor atual")
            else:
                partes.append(f"{rotulo} diferente entre consultas ({valores}): conferir no BTP")
        elif nota.startswith("Ambígua"):
            partes.append(f"{rotulo}: o BTP tem mais de um campo de quantidade; conferir no BTP")
        elif nota.startswith("Não obtido"):
            partes.append(f"{rotulo} não informado pelo BTP")
        else:
            partes.append(f"{rotulo}: {nota}")
    if "ausente" in l.observacao or "Não estava" in l.observacao:
        partes.append("Item apareceu/sumiu entre consultas: conferir no BTP")
    if l.auditoria is Auditoria.NAO_CONCLUIDO and not partes:
        partes.append("Conferência não concluída (" + (l.observacao or "tempo ou falha") + ")")
    return "; ".join(partes) + ("." if partes else "")


def estado_rotulo(r: ResultadoWO) -> str:
    if r.status is Status.NAO_PROCESSADA:
        return ROTULO_ESTADO["pendente"]
    from .loja import estado_da_wo
    return ROTULO_ESTADO[estado_da_wo(r)]


def linhas_dados(resultados: list[ResultadoWO], exportar_incompletos: bool = True) -> list[list]:
    """Linhas da aba Dados: um item por linha; WO sem itens aparece numa linha com a observação."""
    saida = []
    for r in resultados:
        estado = estado_rotulo(r)
        itens = [l for l in r.linhas if exportar_incompletos or l.auditoria is Auditoria.VALIDADO]
        for l in itens:
            saida.append([l.wo, l.material, l.fornecedor, l.quantidade_faltante, l.mensagem, observacao_item(l),
                          estado])
        if not itens:
            obs = SITUACAO[r.status]
            if r.linhas:   # havia itens, mas não validados e a exportação deles está desligada
                obs = f"{len(r.linhas)} item(ns) com divergência omitido(s): conferir no BTP"
            elif r.status is Status.NAO_PROCESSADA:
                obs = r.observacao or obs
            elif r.status in STATUS_FALHA and r.observacao and r.observacao != "OK":
                obs = f"{obs} ({r.observacao})"
            saida.append([r.wo, None, None, None, None, obs, estado])
    return saida


def gerar_planilha(resultados: list[ResultadoWO], destino: str | Path, numero: int | None = None,
                   emitida_em: datetime | None = None, origem: str = "", prefs=None,
                   fonte_campos: dict | None = None, releitura_por_chave: bool | None = None) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    emitida_em = emitida_em or datetime.now()
    exportar_incompletos = True if prefs is None else prefs.exportar_incompletos
    linhas = [l for r in resultados for l in r.linhas if exportar_incompletos or l.auditoria is Auditoria.VALIDADO]
    wb = Workbook()

    # ================================================================ Dados
    ws = wb.active
    ws.title = ABAS[0]
    _cabecalho(ws, COLUNAS_DADOS, linha=1)
    for valores in linhas_dados(resultados, exportar_incompletos):
        _append(ws, valores)
        n = ws.max_row
        for col in (1, 2, 3, 5, 6, 7):
            ws.cell(row=n, column=col).number_format = "@"
        for col in (1, 2, 3, 4, 7):
            ws.cell(row=n, column=col).alignment = _TOPO
        for col in (5, 6):
            ws.cell(row=n, column=col).alignment = _QUEBRA
        if valores[5] and valores[1] is not None:      # item com observação: destaca só a observação
            ws.cell(row=n, column=6).fill = _ATENCAO
    _finalizar(ws, [18, 22, 16, 12, 55, 55, 13], linha_cab=1, filtro=True)

    # ================================================================ Análise
    wa = wb.create_sheet(ABAS[1])
    wa.cell(row=1, column=1, value=f"Análise{' nº ' + str(numero) if numero else ''} — Monitor de Alertas").font = \
        Font(bold=True, size=14, color=VERMELHO)
    wa.cell(row=2, column=1, value=f"Emitida em {emitida_em:%d/%m/%Y %H:%M}"
            + (f" · {origem}" if origem else "")).font = Font(size=10, color="595959")
    st = Counter(r.status for r in resultados)
    com_obs = sum(1 for l in linhas if l.auditoria is not Auditoria.VALIDADO)
    _secao(wa, "Números", ["Indicador", "Quantidade"], [
        ("WOs consultadas", len(resultados)),
        ("WOs com itens faltantes", st[Status.PROCESSADA]),
        ("WOs no Monitor sem itens na janela Mensagens", st[Status.SEM_REGISTROS]),
        ("WOs não encontradas no Monitor de Alertas", st[Status.NAO_ENCONTRADA]),
        ("WOs não consultadas (erro, tempo ou sessão)", sum(st[s] for s in STATUS_FALHA if s is not Status.NAO_PROCESSADA)),
        ("WOs pendentes (análise ainda não concluída)", st[Status.NAO_PROCESSADA]),
        ("WOs com número inválido", st[Status.WO_INVALIDA]),
        ("Itens faltantes (linhas com material na aba Dados)", len(linhas)),
        ("Itens conferidos sem diferença", len(linhas) - com_obs),
        ("Itens com observação (conferir no BTP)", com_obs),
    ], linha=4)

    grupos: dict[tuple, list[LinhaMensagem]] = OrderedDict()
    for l in linhas:
        grupos.setdefault((l.material or "(não informado)", l.fornecedor or "(não informado)"), []).append(l)
    materiais = []
    for (material, fornecedor), ls in sorted(grupos.items(), key=lambda kv: (-len({l.wo for l in kv[1]}), kv[0])):
        wos = list(OrderedDict.fromkeys(l.wo for l in ls))
        materiais.append((material, fornecedor, len(wos), ", ".join(wos), _soma(ls),
                          " | ".join(OrderedDict.fromkeys(l.mensagem or "(sem mensagem)" for l in ls))))
    _secao(wa, "Materiais (mais recorrentes primeiro)",
           ["Material SAP", "Fornecedor SAP", "Nº de WOs", "WOs", "Quantidade faltante total", "Mensagens"],
           materiais, texto={1, 2})

    msgs = Counter(l.mensagem or "(sem mensagem)" for l in linhas)
    _secao(wa, "Mensagens", ["Mensagem", "Itens", "WOs"],
           [(m, n, len({l.wo for l in linhas if (l.mensagem or "(sem mensagem)") == m})) for m, n in msgs.most_common()])

    por_wo = []
    for r in resultados:
        ls = [l for l in r.linhas if exportar_incompletos or l.auditoria is Auditoria.VALIDADO]
        obs = "; ".join(o for o in OrderedDict.fromkeys(observacao_item(l) for l in ls) if o)
        por_wo.append((r.wo, SITUACAO[r.status], len(ls),
                       ", ".join(OrderedDict.fromkeys(l.material for l in ls if l.material)),
                       obs or ("" if r.status not in STATUS_FALHA else r.observacao)))
    _secao(wa, "Situação de cada WO", ["WO", "Situação", "Itens", "Materiais SAP", "Observação"], por_wo, texto={1})
    for col, largura in enumerate([40, 30, 12, 40, 16, 50], start=1):
        wa.column_dimensions[get_column_letter(col)].width = largura

    # ================================================================ Rastreio (oculta)
    wr = wb.create_sheet(ABAS[2])
    wr.sheet_state = "hidden"
    _cabecalho(wr, ["WO", "Material SAP", "Identificação do item", "Leitura", "Tipo", "Data/hora", "Quantidade",
                    "Fornecedor SAP", "Mensagem", "Comparação", "Resultado"], linha=1)
    for r in resultados:
        for a in r.registros_auditoria:
            _append(wr, [a["wo"], a["material"], a["ocorrencia"], a["rodada"], a["tipo"],
                         a["quando"].replace(microsecond=0) if a.get("quando") else None, a["quantidade"],
                         a["fornecedor"], a["mensagem"], a["resultado"], a["situacao"]])
    if fonte_campos:
        _append(wr, [])
        _append(wr, ["Campos do BTP usados"] + [f"{ROTULO[c]} = {fonte_campos.get(c, '?')}" for c in CAMPOS])

    wb.save(destino)
    return destino


def _soma(linhas: list[LinhaMensagem]):
    """Soma só itens conferidos sem diferença e com quantidade; senão None (não chuta)."""
    if not linhas or any(l.auditoria is not Auditoria.VALIDADO or l.quantidade_faltante is None for l in linhas):
        return None
    total = sum((Decimal(str(l.quantidade_faltante)) for l in linhas), Decimal(0))
    return int(total) if total == total.to_integral_value() else total


def _secao(ws, titulo, cabecalho, linhas, linha=None, texto=frozenset()):
    inicio = linha or ws.max_row + 2
    ws.cell(row=inicio, column=1, value=titulo).font = Font(bold=True, size=12)
    _cabecalho(ws, cabecalho, linha=inicio + 1)
    for valores in linhas:
        _append(ws, list(valores))
        for col in range(1, len(cabecalho) + 1):
            c = ws.cell(row=ws.max_row, column=col)
            c.alignment = _QUEBRA
            c.border = _BORDA
            if col in texto:
                c.number_format = "@"
    if not linhas:
        ws.cell(row=inicio + 2, column=1, value="(nenhum)").font = Font(italic=True, color="808080")


def verificar_planilha(destino: str | Path, resultados: list[ResultadoWO],
                       exportar_incompletos: bool = True) -> list[str]:
    """Reabre o arquivo gravado e confere a aba Dados, célula a célula, com os dados auditados."""
    esperadas = linhas_dados(resultados, exportar_incompletos)
    wb = load_workbook(destino)   # sem read_only: precisa ver o TIPO de cada célula
    if wb.sheetnames != ABAS:
        return [f"Abas fora da ordem esperada: {wb.sheetnames}"]
    aba = wb[ABAS[0]]
    linhas = list(aba.iter_rows(values_only=True))
    problemas = [f"{c.coordinate}: gravado como fórmula ({c.value!r})"
                 for linha in aba.iter_rows() for c in linha if c.data_type == "f"]
    if list(linhas[0]) != COLUNAS_DADOS:
        problemas.append(f"Cabeçalho da aba Dados diferente: {linhas[0]}")
    gravadas = linhas[1:]
    if len(gravadas) != len(esperadas):
        problemas.append(f"Aba Dados tem {len(gravadas)} linha(s); esperado {len(esperadas)}.")
    for i, (g, e) in enumerate(zip(gravadas, esperadas), start=2):
        for col, (vg, ve) in enumerate(zip(g, [_limpar(v) for v in e])):
            numeros = isinstance(vg, (int, float)) and isinstance(ve, (int, float, Decimal))
            if numeros and math.isclose(float(vg), float(ve), rel_tol=1e-12):
                continue   # o Excel guarda número com 15 dígitos significativos
            if _txt(vg) != _txt(ve):
                problemas.append(f"Linha {i}, {COLUNAS_DADOS[col]}: gravado {vg!r}, esperado {ve!r}")
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
