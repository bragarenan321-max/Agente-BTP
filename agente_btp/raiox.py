"""Raio-X da fonte: fotografa TUDO o que o serviço do Monitor tem para uma WO conhecida.

Uso: o usuário escolhe uma WO cuja janela "Mensagens" ele conhece (ex.: tem o Download do BTP).
O agente então:
  1. lista os serviços de dados da aba e lê o dicionário ($metadata) de cada um;
  2. consulta cada conjunto filtrando pela WO e, se informado, pelo Consolidado;
  3. observa as consultas que a TELA do BTP faz enquanto o usuário abre a janela Mensagens.
O resultado (JSON) mostra de onde vem cada valor, sem adivinhação. Somente leitura (GET);
nenhum cookie ou token é gravado; conjuntos com nome de ação não são consultados.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from .autoconfig import TIPOS_NUMERO, caminho_servico, ler_modelo, raiz_servico
from .campos import tipo_do_campo
from .exploracao import _ACAO
from .odata import codificar_valor, extrair_pagina
from .modelos import ErroConsulta, FalhaComunicacao

TOP = 50
LIMITE_CONSULTAS = 400


def _resumo(registros: list[dict], limite: int = 15) -> list[dict]:
    saida = []
    for r in registros[:limite]:
        saida.append({k: (str(v)[:200] if not isinstance(v, (int, float, bool)) or isinstance(v, bool) else v)
                      for k, v in r.items() if not k.startswith(("__", "@")) and not isinstance(v, (dict, list))})
    return saida


def _literal(valor: str, tipo: str, v4: bool) -> str | None:
    if tipo in TIPOS_NUMERO:
        return valor if valor.replace(".", "", 1).isdigit() else None
    if tipo == "Edm.Guid":
        return None
    if tipo not in ("Edm.String", ""):
        return None
    return "'" + codificar_valor(valor) + "'"


def raio_x(conexao, wo: str, consolidado: str = "", avancar=lambda texto: None) -> dict:
    inicio = time.monotonic()
    rel = {"gerado_em": datetime.now().isoformat(timespec="seconds"), "wo": wo, "consolidado": consolidado,
           "pagina_da_aba": getattr(conexao, "pagina", ""), "servicos": [], "conjuntos": []}
    fontes = conexao.fontes()
    rel["fontes"] = fontes[:80]
    servicos = []
    for f in fontes:
        for chave in ("url", "url_original"):
            if f.get(chave):
                try:
                    s = caminho_servico(f[chave])
                    if s not in servicos:
                        servicos.append(s)
                except Exception:  # noqa: BLE001
                    pass
        if f.get("recurso"):
            s = raiz_servico(f["recurso"])
            if s and s not in servicos:
                servicos.append(s)
    consultas = 0
    valores_busca = [("wo", wo)] + ([("consolidado", consolidado)] if consolidado else [])
    for servico in servicos[:10]:
        try:
            r = conexao.get(servico + "$metadata", accept="application/xml")
            modelo = ler_modelo(r.corpo) if r.status == 200 else None
        except Exception as exc:  # noqa: BLE001
            rel["servicos"].append({"servico": servico, "erro": str(exc)[:200]})
            continue
        if not modelo:
            rel["servicos"].append({"servico": servico, "erro": f"HTTP {r.status}"})
            continue
        rel["servicos"].append({"servico": servico, "conjuntos": len(modelo["conjuntos"]),
                                "funcoes": [f["nome"] for f in modelo["funcoes"]]})
        for nome, info in modelo["conjuntos"].items():
            item = {"servico": servico, "conjunto": nome, "chaves": info["chaves"],
                    "campos": info["props"], "navegacoes": info.get("destinos", {}), "consultas": []}
            if _ACAO.match(nome):
                item["ignorado"] = "nome de ação"
                rel["conjuntos"].append(item)
                continue
            for papel, valor in valores_busca:
                for campo, tipo in info["props"].items():
                    if tipo_do_campo(campo) != papel and not (papel == "wo" and "wo" in campo.lower()
                                                                and tipo_do_campo(campo) is None):
                        continue
                    literal = _literal(valor, tipo, modelo["v4"])
                    if not literal or consultas >= LIMITE_CONSULTAS:
                        continue
                    url = f"{servico}{nome}?$filter={campo}%20eq%20{literal}&$top={TOP}"
                    consultas += 1
                    avancar(f"Raio-X: {nome} ({consultas} consultas)")
                    try:
                        resp = conexao.get(url)
                        if resp.status != 200:
                            item["consultas"].append({"filtro": f"{campo} = {papel}", "http": resp.status,
                                                      "corpo": resp.corpo[:300]})
                            continue
                        regs = [x for x in extrair_pagina(resp.corpo)[0] if isinstance(x, dict)]
                    except (ErroConsulta, FalhaComunicacao) as exc:
                        item["consultas"].append({"filtro": f"{campo} = {papel}", "erro": str(exc)[:200]})
                        continue
                    item["consultas"].append({"filtro": f"{campo} = {papel}", "registros": len(regs),
                                              "amostra": _resumo(regs)})
            rel["conjuntos"].append(item)
    rel["consultas_feitas"] = consultas
    rel["duracao_seg"] = round(time.monotonic() - inicio, 1)
    return rel


def resumo_texto(rel: dict) -> str:
    """Linhas legíveis: quais conjuntos devolveram registros para a WO/consolidado."""
    linhas = []
    for c in rel.get("conjuntos", []):
        for q in c.get("consultas", []):
            if q.get("registros"):
                campos = ", ".join(list(q["amostra"][0].keys())[:12]) if q.get("amostra") else ""
                linhas.append(f"{c['conjunto']} [{q['filtro']}]: {q['registros']} registro(s) — {campos}")
    return "\n".join(linhas)


def salvar(rel: dict, destino) -> None:
    destino.write_text(json.dumps(rel, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
