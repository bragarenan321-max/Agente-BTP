"""Primitivas de desenho dos diagramas do e-book (SVG puro, sem dependências).

Dois tipos de diagrama:
  - Fluxo: caixas posicionadas numa GRADE (coluna, linha) e setas ortogonais entre elas.
    A posição em grade evita sobreposição; cada seta sai e entra pelo lado mais natural.
  - Sequência: participantes em colunas, linhas de vida verticais e mensagens numeradas.

Formas (o significado é dado também pelo texto e pela legenda, nunca só pela cor):
  processo  retângulo       — uma etapa executada pelo agente
  decisao   losango         — uma pergunta com caminhos diferentes
  dado      retângulo em aba — informação guardada (arquivo, base, configuração)
  externo   retângulo duplo — sistema fora do agente (BTP, navegador, usuário)
  inicio    pílula          — começo ou fim
  erro      retângulo tracejado vermelho — caminho de falha
  resultado retângulo com borda grossa — saída entregue ao usuário
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

FONTE = "Inter, 'DejaVu Sans', sans-serif"
CORES = {
    "processo": ("#FFFFFF", "#3B4250", "#1D2026"),
    "decisao": ("#FFF7E6", "#B26B00", "#5A3A00"),
    "dado": ("#EEF3F8", "#4A6380", "#1D2A3A"),
    "externo": ("#F1F1F3", "#5F636B", "#1D2026"),
    "inicio": ("#1D2026", "#1D2026", "#FFFFFF"),
    "erro": ("#FDECEA", "#C21F14", "#7A140D"),
    "resultado": ("#E7F5EE", "#1E8E52", "#0F4D2C"),
    "nota": ("#FFFFFF", "#B9BCC2", "#5F636B"),
    "marca": ("#FDECEA", "#C8261E", "#7A140D"),
    "alerta": ("#FFF7E6", "#B26B00", "#5A3A00"),
}


@dataclass
class No:
    id: str
    col: float
    lin: float
    texto: str
    tipo: str = "processo"
    larg: float | None = None      # em células (1 = largura padrão)
    alt: float | None = None


@dataclass
class Seta:
    de: str
    para: str
    rotulo: str = ""
    lado_de: str = ""              # n, s, l (leste), o (oeste); vazio = automático
    lado_para: str = ""
    tracejada: bool = False
    via: list = field(default_factory=list)   # pontos intermediários (em células) para desviar
    dupla: bool = False                       # seta nas duas pontas (comunicação de ida e volta)


class Fluxo:
    """Diagrama de caixas em grade. cel_l/cel_a = tamanho da célula; caixa ocupa ~80% dela."""

    def __init__(self, titulo: str, colunas: int, linhas: int, cel_l: int = 190, cel_a: int = 86,
                 caixa_l: int = 160, caixa_a: int = 56, margem: int = 18):
        self.titulo, self.colunas, self.linhas = titulo, colunas, linhas
        self.cel_l, self.cel_a, self.caixa_l, self.caixa_a, self.margem = cel_l, cel_a, caixa_l, caixa_a, margem
        self.nos: dict[str, No] = {}
        self.setas: list[Seta] = []
        self.grupos: list[tuple] = []
        self.legenda_itens: list[str] = []

    # ------------------------------------------------ construção
    def no(self, id, col, lin, texto, tipo="processo", larg=None, alt=None):
        self.nos[id] = No(id, col, lin, texto, tipo, larg, alt)
        return self

    def seta(self, de, para, rotulo="", lado_de="", lado_para="", tracejada=False, via=None, dupla=False):
        self.setas.append(Seta(de, para, rotulo, lado_de, lado_para, tracejada, via or [], dupla))
        return self

    def grupo(self, col0, lin0, col1, lin1, rotulo, tipo="nota"):
        """Moldura tracejada agrupando células (col0..col1, lin0..lin1), com rótulo no topo."""
        self.grupos.append((col0, lin0, col1, lin1, rotulo, tipo))
        return self

    def legenda(self, *tipos):
        self.legenda_itens = list(tipos)
        return self

    # ------------------------------------------------ geometria (medida pelo conteúdo)
    @staticmethod
    def _largura_texto(texto: str, tam: float = 12) -> float:
        return max(len(t) for t in texto.split("\n")) * tam * 0.58

    def _medir(self) -> None:
        """Largura de cada coluna = caixa mais larga dela; vão = rótulo horizontal mais largo que o cruza."""
        larg = [float(self.caixa_l * 0.6)] * self.colunas
        for n in self.nos.values():
            if (n.larg or 1) == 1 and float(n.col).is_integer():
                tam = 12 if len(n.texto.split("\n")) <= 3 else 11
                fator = 1.45 if n.tipo == "decisao" else 1
                larg[int(n.col)] = max(larg[int(n.col)], self._largura_texto(n.texto, tam) * fator + 26)
        vaos = [64.0] * max(0, self.colunas - 1)
        for st in self.setas:
            a, b = self.nos[st.de], self.nos[st.para]
            if st.rotulo and a.lin == b.lin and abs(a.col - b.col) == 1 and not st.via:
                c = int(min(a.col, b.col))
                vaos[c] = max(vaos[c], self._largura_texto(st.rotulo, 11) + 34)
        self._larg, self._vaos = larg, vaos
        self._esq = []
        x = self.margem
        for c in range(self.colunas):
            self._esq.append(x)
            x += larg[c] + (vaos[c] if c < len(vaos) else 0)
        self._largura_total = x + self.margem

    def _x(self, col: float) -> float:
        c0 = int(col)
        centro = lambda c: self._esq[c] + self._larg[c] / 2
        if float(col).is_integer() or c0 + 1 >= self.colunas:
            return centro(min(c0, self.colunas - 1))
        return centro(c0) + (centro(c0 + 1) - centro(c0)) * (col - c0)

    def _centro(self, n: No):
        return self._x(n.col), self.margem + (n.lin + 0.5) * self.cel_a

    def _dim(self, n: No):
        linhas = len(n.texto.split("\n"))
        h = max(self.caixa_a, linhas * 15 + 18) * (n.alt or 1)
        if (n.larg or 1) != 1 or not float(n.col).is_integer():
            tam = 12 if linhas <= 3 else 11
            w = max(self._largura_texto(n.texto, tam) + 26, self.caixa_l * (n.larg or 1))
        else:
            w = self._larg[int(n.col)]
        return w, h

    def _ponto(self, n: No, lado: str):
        x, y = self._centro(n)
        w, h = self._dim(n)
        extra = 6 if n.tipo == "decisao" else 0
        return {"n": (x, y - h / 2 - extra), "s": (x, y + h / 2 + extra), "l": (x + w / 2, y), "o": (x - w / 2, y)}[lado]

    def _lados(self, a: No, b: No, s: Seta):
        if s.lado_de and s.lado_para:
            return s.lado_de, s.lado_para
        dx, dy = b.col - a.col, b.lin - a.lin
        if abs(dy) >= abs(dx) and dy != 0:
            ld, lp = ("s", "n") if dy > 0 else ("n", "s")
        else:
            ld, lp = ("l", "o") if dx > 0 else ("o", "l")
        return s.lado_de or ld, s.lado_para or lp

    # ------------------------------------------------ desenho
    def svg(self) -> str:
        self._medir()
        largura = self._largura_total
        altura = self.margem * 2 + self.linhas * self.cel_a + 26 * self._linhas_legenda(largura) + (8 if self.legenda_itens else 0)
        partes = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largura} {altura}" '
                  f'width="{largura}" height="{altura}" role="img" aria-label="{escape(self.titulo)}" '
                  f'font-family="{FONTE}">',
                  '<defs><marker id="pt" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                  'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#3B4250"/></marker>'
                  '<marker id="pte" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                  'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#C21F14"/></marker></defs>',
                  f'<rect x="0" y="0" width="{largura}" height="{altura}" fill="#FFFFFF"/>']
        for col0, lin0, col1, lin1, rotulo, tipo in self.grupos:
            x0 = self._esq[col0] - 14
            y0 = self.margem + lin0 * self.cel_a + 4
            x1 = self._esq[col1] + self._larg[col1] + 14
            y1 = self.margem + (lin1 + 1) * self.cel_a - 4
            borda = CORES.get(tipo, CORES["nota"])[1]
            partes.append(f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="10" fill="none" '
                          f'stroke="{borda}" stroke-dasharray="5 4" stroke-width="1.2"/>')
            partes.append(f'<rect x="{x0 + 10}" y="{y0 - 8}" width="{len(rotulo) * 6.6 + 14}" height="16" '
                          f'fill="#FFFFFF"/><text x="{x0 + 17}" y="{y0 + 4}" font-size="11" font-weight="600" '
                          f'fill="{borda}">{escape(rotulo)}</text>')
        for s in self.setas:
            partes.append(self._seta_svg(s))
        for n in self.nos.values():
            partes.append(self._no_svg(n))
        if self.legenda_itens:
            partes.append(self._legenda_svg(self.margem + self.linhas * self.cel_a + 8, largura))
        partes.append("</svg>")
        return "\n".join(partes)

    def _seta_svg(self, s: Seta) -> str:
        a, b = self.nos[s.de], self.nos[s.para]
        ld, lp = self._lados(a, b, s)
        x0, y0 = self._ponto(a, ld)
        x1, y1 = self._ponto(b, lp)
        pts = [(x0, y0)]
        if s.via:
            for c, l in s.via:
                pts.append((self._x(c), self.margem + (l + 0.5) * self.cel_a))
        elif x0 != x1 and y0 != y1:
            if ld in ("n", "s") and lp in ("n", "s"):
                ym = (y0 + y1) / 2
                pts += [(x0, ym), (x1, ym)]
            elif ld in ("l", "o") and lp in ("l", "o"):
                xm = (x0 + x1) / 2
                pts += [(xm, y0), (xm, y1)]
            elif ld in ("n", "s"):
                pts.append((x0, y1))
            else:
                pts.append((x1, y0))
        pts.append((x1, y1))
        # caminhos com via: completa com cotovelos ortogonais entre os pontos
        caminho = [pts[0]]
        for p in pts[1:]:
            q = caminho[-1]
            if q[0] != p[0] and q[1] != p[1]:
                caminho.append((p[0], q[1]) if len(caminho) == 1 and ld in ("l", "o") else (q[0], p[1]))
            caminho.append(p)
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in caminho)
        cor = "#C21F14" if s.tracejada else "#3B4250"
        marcador = "pte" if s.tracejada else "pt"
        traco = ' stroke-dasharray="6 4"' if s.tracejada else ""
        inicio_m = f' marker-start="url(#{marcador})"' if s.dupla else ""
        saida = [f'<path d="{d}" fill="none" stroke="{cor}" stroke-width="1.5"{traco}{inicio_m} '
                 f'marker-end="url(#{marcador})"/>']
        if s.rotulo:
            # rótulo no segmento mais longo: ao lado da linha (vertical) ou centrado nela (horizontal)
            seg = max(zip(caminho, caminho[1:]), key=lambda p: abs(p[0][0] - p[1][0]) + abs(p[0][1] - p[1][1]))
            mx, my = (seg[0][0] + seg[1][0]) / 2, (seg[0][1] + seg[1][1]) / 2
            linhas = s.rotulo.split("\n")
            w = max(len(t) for t in linhas) * 6.3 + 10
            h = 15 * len(linhas) + 2
            vertical = seg[0][0] == seg[1][0]
            comprimento = abs(seg[0][0] - seg[1][0]) + abs(seg[0][1] - seg[1][1])
            if not vertical and comprimento < w + 6:
                print(f"[diagrama {self.titulo}] rótulo '{s.rotulo}' não cabe no vão ({comprimento:.0f} < {w:.0f})")
            x0 = mx + 6 if vertical else mx - w / 2
            saida.append(f'<rect x="{x0:.1f}" y="{my - h / 2:.1f}" width="{w:.1f}" height="{h}" rx="3" '
                         f'fill="#FFFFFF" stroke="#E4E5E8"/>')
            for i, t in enumerate(linhas):
                saida.append(f'<text x="{x0 + w / 2:.1f}" y="{my - h / 2 + 12 + i * 15:.1f}" font-size="11" '
                             f'text-anchor="middle" fill="{cor}">{escape(t)}</text>')
        return "\n".join(saida)

    def _no_svg(self, n: No) -> str:
        x, y = self._centro(n)
        w, h = self._dim(n)
        fundo, borda, texto = CORES[n.tipo]
        linhas = n.texto.split("\n")
        partes = []
        if n.tipo == "decisao":
            partes.append(f'<polygon points="{x},{y - h / 2 - 6} {x + w / 2},{y} {x},{y + h / 2 + 6} {x - w / 2},{y}" '
                          f'fill="{fundo}" stroke="{borda}" stroke-width="1.6"/>')
        elif n.tipo == "inicio":
            partes.append(f'<rect x="{x - w / 2}" y="{y - h / 2}" width="{w}" height="{h}" rx="{h / 2}" '
                          f'fill="{fundo}" stroke="{borda}" stroke-width="1.6"/>')
        elif n.tipo == "dado":
            partes.append(f'<path d="M{x - w / 2},{y - h / 2 + 8} L{x - w / 2 + 14},{y - h / 2} L{x + w / 2},{y - h / 2} '
                          f'L{x + w / 2},{y + h / 2} L{x - w / 2},{y + h / 2} Z" fill="{fundo}" stroke="{borda}" '
                          f'stroke-width="1.6"/>')
        elif n.tipo == "externo":
            partes.append(f'<rect x="{x - w / 2}" y="{y - h / 2}" width="{w}" height="{h}" rx="4" fill="{fundo}" '
                          f'stroke="{borda}" stroke-width="1.6"/>')
            partes.append(f'<rect x="{x - w / 2 + 4}" y="{y - h / 2 + 4}" width="{w - 8}" height="{h - 8}" rx="2" '
                          f'fill="none" stroke="{borda}" stroke-width="0.8"/>')
        elif n.tipo == "erro":
            partes.append(f'<rect x="{x - w / 2}" y="{y - h / 2}" width="{w}" height="{h}" rx="6" fill="{fundo}" '
                          f'stroke="{borda}" stroke-width="1.6" stroke-dasharray="5 3"/>')
        elif n.tipo == "nota":
            partes.append(f'<rect x="{x - w / 2}" y="{y - h / 2}" width="{w}" height="{h}" rx="4" fill="{fundo}" '
                          f'stroke="{borda}" stroke-width="1" stroke-dasharray="3 3"/>')
        else:
            grossa = "2.6" if n.tipo in ("resultado", "marca") else "1.6"
            partes.append(f'<rect x="{x - w / 2}" y="{y - h / 2}" width="{w}" height="{h}" rx="7" fill="{fundo}" '
                          f'stroke="{borda}" stroke-width="{grossa}"/>')
        tam = 12 if len(linhas) <= 3 else 11
        limite = w - 12 if n.tipo != "decisao" else w * 0.72
        for t in linhas:
            if len(t) * tam * 0.56 > limite:
                print(f"[diagrama {self.titulo}] texto '{t}' maior que a caixa ({len(t) * tam * 0.56:.0f} > {limite:.0f})")
        inicio = y - (len(linhas) - 1) * (tam + 3) / 2 + tam * 0.36
        for i, t in enumerate(linhas):
            peso = "600" if i == 0 and len(linhas) > 1 and not t.startswith("(") else "500"
            fonte = tam - 1 if t.startswith("(") or t.startswith("·") else tam
            cor = texto if not t.startswith("(") else CORES["nota"][2] if n.tipo != "inicio" else "#D0D2D6"
            partes.append(f'<text x="{x}" y="{inicio + i * (tam + 3):.1f}" font-size="{fonte}" font-weight="{peso}" '
                          f'text-anchor="middle" fill="{cor}">{escape(t)}</text>')
        return "\n".join(partes)

    NOMES = {"processo": "etapa do agente", "decisao": "decisão", "dado": "dado guardado",
             "externo": "sistema externo", "inicio": "início / fim", "erro": "caminho de falha",
             "resultado": "resultado", "marca": "ponto-chave", "nota": "observação", "alerta": "atenção"}

    def _linhas_legenda(self, largura) -> int:
        if not self.legenda_itens:
            return 0
        linhas, x = 1, self.margem
        for t in self.legenda_itens:
            passo = 32 + len(self.NOMES[t]) * 6.4
            if x + passo > largura - self.margem:
                linhas, x = linhas + 1, self.margem
            x += passo
        return linhas

    def _legenda_svg(self, y, largura) -> str:
        nomes = {"processo": "etapa do agente", "decisao": "decisão", "dado": "dado guardado",
                 "externo": "sistema externo", "inicio": "início / fim", "erro": "caminho de falha",
                 "resultado": "resultado", "marca": "ponto-chave", "nota": "observação", "alerta": "atenção"}
        partes, x = [], self.margem
        for t in self.legenda_itens:
            if x + 32 + len(nomes[t]) * 6.4 > largura - self.margem:
                x, y = self.margem, y + 26
            fundo, borda, _ = CORES[t]
            traco = ' stroke-dasharray="4 2"' if t in ("erro", "nota") else ""
            partes.append(f'<rect x="{x}" y="{y}" width="16" height="12" rx="2" fill="{fundo}" stroke="{borda}"{traco}/>')
            partes.append(f'<text x="{x + 22}" y="{y + 10}" font-size="11" fill="#5F636B">{nomes[t]}</text>')
            x += 32 + len(nomes[t]) * 6.4
        return "\n".join(partes)


class Sequencia:
    """Diagrama de sequência: quem fala com quem, em que ordem."""

    def __init__(self, titulo: str, participantes: list[tuple[str, str, str]], larg_col: int = 165,
                 passo: int = 40, margem: int = 18):
        # participantes: (id, rótulo com \n, tipo)
        self.titulo, self.participantes = titulo, participantes
        self.larg_col, self.passo, self.margem = larg_col, passo, margem
        self.eventos: list[tuple] = []

    def msg(self, de, para, texto, tracejada=False):
        self.eventos.append(("msg", de, para, texto, tracejada))
        return self

    def nota(self, sobre, texto, ate=None):
        self.eventos.append(("nota", sobre, ate or sobre, texto, False))
        return self

    def separador(self, texto):
        self.eventos.append(("sep", None, None, texto, False))
        return self

    def _x(self, pid):
        i = [p[0] for p in self.participantes].index(pid)
        return self.margem + (i + 0.5) * self.larg_col

    def svg(self) -> str:
        largura = self.margem * 2 + len(self.participantes) * self.larg_col
        topo = 66
        altura = topo + len(self.eventos) * self.passo + 30
        partes = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largura} {altura}" width="{largura}" '
                  f'height="{altura}" role="img" aria-label="{escape(self.titulo)}" font-family="{FONTE}">',
                  '<defs><marker id="sq" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                  'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#3B4250"/></marker></defs>',
                  f'<rect width="{largura}" height="{altura}" fill="#FFFFFF"/>']
        for pid, rotulo, tipo in self.participantes:
            x = self._x(pid)
            fundo, borda, cor = CORES[tipo]
            partes.append(f'<line x1="{x}" y1="{topo - 8}" x2="{x}" y2="{altura - 12}" stroke="#B9BCC2" '
                          f'stroke-dasharray="4 4"/>')
            linhas = rotulo.split("\n")
            partes.append(f'<rect x="{x - self.larg_col / 2 + 8}" y="8" width="{self.larg_col - 16}" height="{topo - 22}" '
                          f'rx="6" fill="{fundo}" stroke="{borda}" stroke-width="1.6"/>')
            for i, t in enumerate(linhas):
                partes.append(f'<text x="{x}" y="{27 + i * 14 - (len(linhas) - 1) * 4}" font-size="{12 if i == 0 else 10.5}" '
                              f'font-weight="{600 if i == 0 else 400}" text-anchor="middle" fill="{cor}">{escape(t)}</text>')
        y = topo + self.passo / 2
        n = 0
        for tipo, de, para, texto, tracejada in self.eventos:
            if tipo == "msg":
                n += 1
                x0, x1 = self._x(de), self._x(para)
                traco = ' stroke-dasharray="6 4"' if tracejada else ""
                if de == para:
                    partes.append(f'<path d="M{x0},{y - 6} h28 v14 h-26" fill="none" stroke="#3B4250" stroke-width="1.4"'
                                  f'{traco} marker-end="url(#sq)"/>')
                    tx, ancora = x0 + 34, "start"
                else:
                    partes.append(f'<line x1="{x0}" y1="{y}" x2="{x1 - (8 if x1 > x0 else -8)}" y2="{y}" stroke="#3B4250" '
                                  f'stroke-width="1.4"{traco} marker-end="url(#sq)"/>')
                    tx, ancora = (x0 + x1) / 2, "middle"
                rot = f"{n}. {texto}"
                partes.append(f'<text x="{tx}" y="{y - 6}" font-size="11" text-anchor="{ancora}" fill="#1D2026">'
                              f'{escape(rot)}</text>')
            elif tipo == "nota":
                xa, xb = sorted((self._x(de), self._x(para)))
                xa, xb = xa - self.larg_col / 2 + 14, xb + self.larg_col / 2 - 14
                partes.append(f'<rect x="{xa}" y="{y - 13}" width="{xb - xa}" height="24" rx="4" fill="#FFF7E6" '
                              f'stroke="#E3C27A"/>')
                partes.append(f'<text x="{(xa + xb) / 2}" y="{y + 3}" font-size="10.5" text-anchor="middle" '
                              f'fill="#5A3A00">{escape(texto)}</text>')
            else:
                partes.append(f'<line x1="{self.margem}" y1="{y}" x2="{largura - self.margem}" y2="{y}" stroke="#C8261E" '
                              f'stroke-dasharray="2 3"/>')
                w = len(texto) * 6.5 + 16
                partes.append(f'<rect x="{largura / 2 - w / 2}" y="{y - 9}" width="{w}" height="18" rx="9" fill="#FFFFFF" '
                              f'stroke="#C8261E"/><text x="{largura / 2}" y="{y + 4}" font-size="10.5" font-weight="600" '
                              f'text-anchor="middle" fill="#7A140D">{escape(texto)}</text>')
            y += self.passo
        partes.append("</svg>")
        return "\n".join(partes)
