"""Geração da planilha Excel da análise.

Abas:
  Itens faltantes  — o conteúdo da janela "Mensagens" de cada WO (uma linha por item);
  Resumo           — indicadores da análise;
  Situação por WO  — o resultado da consulta de cada WO (inclusive as sem pendência).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .modelos import COLUNAS_RESULTADO, ResultadoWO, Status

TITULO = "Análise de Pendências de Material"
ORIGEM_DADOS = "Monitor de Alertas · BTP"
COLUNAS_SITUACAO = ["WO", "Situação", "Itens", "Detalhe", "Consultada em"]
# Colunas (0-based) de COLUNAS_RESULTADO que são identificadores/texto: nunca viram número.
COLUNAS_TEXTO = {0, 1, 2, 4, 5}

VERMELHO = "DA291C"
_CAB_FONTE = Font(bold=True, color="FFFFFF")
_CAB_FUNDO = PatternFill("solid", fgColor=VERMELHO)
_FINA = Side(style="thin", color="D9D9D9")
_BORDA = Border(bottom=_FINA)


def nome_analise(pasta: str | Path, numero: int, total_wos: int) -> Path:
    """Ex.: 'Análise 12 - 499 WOs.xlsx'. Nunca sobrescreve um arquivo existente."""
    base = f"Análise {numero} - {total_wos} WO{'s' if total_wos != 1 else ''}"
    destino = Path(pasta) / f"{base}.xlsx"
    extra = 2
    while destino.exists():
        destino = Path(pasta) / f"{base} ({extra}).xlsx"
        extra += 1
    return destino


def gerar_planilha(resultados: list[ResultadoWO], destino: str | Path, numero: int | None = None,
                   emitida_em: datetime | None = None, origem: str = "") -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    emitida_em = emitida_em or datetime.now()
    itens = [linha for r in resultados for linha in r.linhas]
    rotulo = f"Análise nº {numero}" if numero else "Análise"
    subtitulo = (f"{rotulo} · {ORIGEM_DADOS} · emitida em {emitida_em:%d/%m/%Y %H:%M} · "
                 f"{len(resultados)} WO(s) consultada(s) · {len(itens)} item(ns) faltante(s)")

    wb = Workbook()

    # ---- Itens faltantes (conteúdo da janela "Mensagens")
    ws = wb.active
    ws.title = "Itens faltantes"
    _titulo(ws, "Itens Faltantes por WO", subtitulo, len(COLUNAS_RESULTADO))
    _cabecalho(ws, COLUNAS_RESULTADO, linha=4)
    for linha in itens:
        ws.append(linha.como_lista())
        for i in COLUNAS_TEXTO:
            ws.cell(row=ws.max_row, column=i + 1).number_format = "@"
        ws.cell(row=ws.max_row, column=4).number_format = "#,##0.###"
    if not itens:
        ws.cell(row=5, column=1, value="Nenhum item faltante retornado para as WOs consultadas.").font = \
            Font(italic=True, color="808080")
    _finalizar(ws, [20, 22, 14, 12, 44, 30], linha_cab=4, filtro=bool(itens))

    # ---- Resumo
    wr = wb.create_sheet("Resumo")
    _titulo(wr, TITULO, subtitulo, 3)
    contagem = Counter(r.status for r in resultados)
    qtd_total = sum((Decimal(str(l.quantidade_faltante)) for l in itens
                     if l.quantidade_faltante is not None), Decimal(0))
    indicadores = [
        ("Nº da análise", numero or "—"),
        ("Data de emissão", emitida_em.replace(microsecond=0)),
        ("Origem das WOs", origem or "WOs informadas"),
        ("WOs consultadas", len(resultados)),
        ("WOs com itens faltantes", contagem.get(Status.PROCESSADA, 0)),
        ("Itens faltantes (linhas)", len(itens)),
        ("Quantidade faltante total", int(qtd_total) if qtd_total == qtd_total.to_integral_value() else qtd_total),
        ("Materiais distintos", len({l.material for l in itens if l.material})),
        ("Fornecedores distintos", len({l.fornecedor for l in itens if l.fornecedor})),
    ]
    _cabecalho(wr, ["Indicador", "Valor"], linha=4)
    for nome, valor in indicadores:
        wr.append([nome, valor])
        celula = wr.cell(row=wr.max_row, column=2)
        celula.alignment = Alignment(horizontal="left")
        if isinstance(valor, datetime):
            celula.number_format = "dd/mm/yyyy hh:mm"
    inicio = wr.max_row + 2
    wr.cell(row=inicio, column=1, value="Distribuição por situação").font = Font(bold=True, size=12)
    _cabecalho(wr, ["Situação", "WOs", "% do total"], linha=inicio + 1)
    for status in Status:
        if contagem.get(status):
            wr.append([status.value, contagem[status], contagem[status] / max(len(resultados), 1)])
            wr.cell(row=wr.max_row, column=3).number_format = "0.0%"
    _finalizar(wr, [32, 24, 14], linha_cab=4, filtro=False)

    # ---- Situação por WO
    wl = wb.create_sheet("Situação por WO")
    _titulo(wl, "Situação da Consulta por WO", subtitulo, len(COLUNAS_SITUACAO))
    _cabecalho(wl, COLUNAS_SITUACAO, linha=4)
    for r in resultados:
        wl.append([r.wo, r.status.value, r.registros, "" if r.observacao == "OK" else r.observacao,
                   r.data_hora.replace(microsecond=0)])
        wl.cell(row=wl.max_row, column=1).number_format = "@"
        wl.cell(row=wl.max_row, column=5).number_format = "dd/mm/yyyy hh:mm:ss"
    _finalizar(wl, [20, 26, 8, 60, 20], linha_cab=4, filtro=bool(resultados))

    wb.save(destino)
    return destino


def _titulo(ws, titulo: str, subtitulo: str, colunas: int) -> None:
    ws.cell(row=1, column=1, value=titulo).font = Font(bold=True, size=14, color=VERMELHO)
    ws.cell(row=2, column=1, value=subtitulo).font = Font(size=10, color="595959")
    ws.row_dimensions[1].height = 22


def _cabecalho(ws, colunas: list[str], linha: int) -> None:
    for i, nome in enumerate(colunas, start=1):
        celula = ws.cell(row=linha, column=i, value=nome)
        celula.font, celula.fill = _CAB_FONTE, _CAB_FUNDO
        celula.alignment = Alignment(vertical="center")


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
