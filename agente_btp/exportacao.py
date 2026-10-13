"""Geração da planilha Excel (aba Resultado + aba Log)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .modelos import COLUNAS_RESULTADO, ResultadoWO

COLUNAS_LOG = ["WO", "Data/Hora", "Status", "Registros", "Observação"]
# Índices (0-based) das colunas de COLUNAS_RESULTADO que são texto.
COLUNAS_TEXTO = {0, 1, 2, 3, 5}


def gerar_planilha(resultados: list[ResultadoWO], destino: str | Path) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Resultado"
    _cabecalho(ws, COLUNAS_RESULTADO)
    for resultado in resultados:
        for linha in resultado.linhas:
            ws.append(linha.como_lista())
            for i in COLUNAS_TEXTO:
                ws.cell(row=ws.max_row, column=i + 1).number_format = "@"
    _finalizar(ws, [16, 20, 22, 14, 20, 40])

    wl = wb.create_sheet("Log")
    _cabecalho(wl, COLUNAS_LOG)
    for r in resultados:
        wl.append([r.wo, r.data_hora.replace(microsecond=0), r.status.value, r.registros, r.observacao])
        wl.cell(row=wl.max_row, column=1).number_format = "@"
        wl.cell(row=wl.max_row, column=2).number_format = "dd/mm/yyyy hh:mm:ss"
    _finalizar(wl, [20, 20, 26, 11, 60])

    wb.save(destino)
    return destino


def nome_padrao(pasta: str | Path) -> Path:
    return Path(pasta) / f"Agente_BTP_{datetime.now():%Y%m%d_%H%M%S}.xlsx"


def _cabecalho(ws, colunas: list[str]) -> None:
    ws.append(colunas)
    for celula in ws[1]:
        celula.font = Font(bold=True)


def _finalizar(ws, larguras: list[int]) -> None:
    for i, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(i)].width = largura
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
