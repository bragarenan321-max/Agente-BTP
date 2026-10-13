"""Leitura e validação das WOs informadas pelo usuário."""
from __future__ import annotations

import re
from pathlib import Path

# Formato observado nos prints: 5 dígitos | 9 dígitos (ex.: 02235|230771207).
PADRAO_WO = re.compile(r"^\d{5}\|\d{9}$")


def normalizar_wo(valor) -> str:
    """Converte para texto sem alterar dígitos (preserva zeros à esquerda)."""
    if valor is None:
        return ""
    texto = str(valor).strip()
    # Tolera espaços ao redor da barra: "02235 | 230771207".
    return re.sub(r"\s*\|\s*", "|", texto)


def wo_valida(wo: str) -> bool:
    return bool(PADRAO_WO.match(wo))


def ler_wos_texto(texto: str) -> list[str]:
    """Uma WO por linha; linhas vazias ignoradas; duplicadas removidas (ordem mantida)."""
    wos = [normalizar_wo(linha) for linha in texto.splitlines()]
    return _sem_duplicadas([w for w in wos if w])


def ler_wos_planilha(caminho: str | Path) -> list[str]:
    """Lê a coluna cujo cabeçalho é 'WO' (primeira aba, primeira linha como cabeçalho)."""
    from openpyxl import load_workbook

    wb = load_workbook(caminho, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        linhas = ws.iter_rows(values_only=True)
        cabecalho = next(linhas, None)
        if not cabecalho:
            raise ValueError("A planilha está vazia.")
        indice = None
        for i, nome in enumerate(cabecalho):
            if nome is not None and str(nome).strip().upper() == "WO":
                indice = i
                break
        if indice is None:
            raise ValueError("Coluna 'WO' não encontrada na primeira linha da planilha.")
        wos = [normalizar_wo(linha[indice]) for linha in linhas if indice < len(linha)]
        return _sem_duplicadas([w for w in wos if w])
    finally:
        wb.close()


def _sem_duplicadas(itens: list[str]) -> list[str]:
    vistos: set[str] = set()
    saida = []
    for item in itens:
        if item not in vistos:
            vistos.add(item)
            saida.append(item)
    return saida
