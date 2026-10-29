"""Constrói o e-book do Agente BTP (PDF + DOCX + Markdown) a partir das fontes desta pasta.

    python docs/ebook/fonte/construir.py            # tudo
    python docs/ebook/fonte/construir.py --rapido   # só o PDF, sem DOCX (para revisar)

Etapas:
  1. Lê capitulos/*.md em ordem e resolve as macros:
       @@figura <nome>                         diagrama de diagramas.py, numerado por capítulo
       @@codigo <arquivo> <Simbolo> [de-ate]   trecho REAL do código (função/classe), com as linhas reais
       @@trecho <arquivo> <de> <ate>           trecho REAL por intervalo de linhas (JS, JSON, .bat...)
       @@arvore                                árvore de pastas do repositório (arquivos versionados)
       [[fig:nome]]                            referência cruzada ("Figura 6.2")
  2. pandoc: Markdown -> HTML; o script monta capa, folha de rosto e sumário.
  3. Chromium (Playwright): HTML -> PDF, em passagens:
       a) descobre a página de cada título pelos marcadores do PDF;
       b) preenche o sumário com essas páginas;
       c) monta o índice remissivo procurando os termos no texto do PDF;
     e por fim aplica cabeçalho (capítulo corrente) e rodapé (página) com reportlab.
  4. pandoc: Markdown -> DOCX (diagramas como PNG) e o Markdown editável consolidado.
O código do Agente BTP só é LIDO: nada fora de docs/ebook é alterado.
"""
from __future__ import annotations

import ast
import html
import io
import json
import re
import shlex
import subprocess
import sys
import unicodedata
from datetime import date
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[2]                       # raiz do repositório Agente-BTP
SAIDA = AQUI.parent / "saida"
DIAG_DIR = AQUI.parent / "diagramas"
sys.path.insert(0, str(AQUI))

from diagramas import CATALOGO  # noqa: E402

TITULO = "Agente BTP"
SUBTITULO = "Documentação técnica completa"
DESCRICAO = "Extração e auditoria de alertas do Monitor de alertas (SAP BTP) — da concepção à manutenção"


# ======================================================================== leitura do código real
def _ler(caminho: str) -> list[str]:
    return (RAIZ / caminho).read_text(encoding="utf-8").splitlines()


def localizar_simbolo(caminho: str, simbolo: str) -> tuple[int, int]:
    """Linhas (1-based, inclusivas) da função/classe/método no arquivo Python, decoradores incluídos."""
    arvore = ast.parse((RAIZ / caminho).read_text(encoding="utf-8"))
    alvo = arvore
    for parte in simbolo.split("."):
        candidatos = [n for n in getattr(alvo, "body", [])
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == parte]
        if not candidatos:
            raise SystemExit(f"[ERRO] {simbolo} não existe em {caminho}")
        alvo = candidatos[0]
    inicio = min([alvo.lineno] + [d.lineno for d in getattr(alvo, "decorator_list", [])])
    return inicio, alvo.end_lineno


def linguagem(caminho: str) -> str:
    return {".py": "python", ".js": "javascript", ".json": "json", ".bat": "batch", ".html": "html",
            ".md": "markdown", ".txt": "text", ".css": "css"}.get(Path(caminho).suffix, "text")


EVIDENCIAS: list[dict] = []     # cada trecho citado (para a verificação final e o apêndice)


def trecho_codigo(caminho: str, inicio: int, fim: int, rotulo: str, alvo: str) -> str:
    linhas = _ler(caminho)
    if not (1 <= inicio <= fim <= len(linhas)):
        raise SystemExit(f"[ERRO] linhas {inicio}-{fim} fora de {caminho} ({len(linhas)} linhas)")
    codigo = "\n".join(linhas[inicio - 1:fim])
    EVIDENCIAS.append({"arquivo": caminho, "inicio": inicio, "fim": fim, "rotulo": rotulo})
    cab = f"{caminho}" + (f" · {rotulo}" if rotulo else "") + f" · linhas {inicio}–{fim}"
    if alvo == "html":
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_by_name
        corpo = highlight(codigo, get_lexer_by_name(linguagem(caminho)),
                          HtmlFormatter(linenos="inline", linenostart=inicio, cssclass="hl", nowrap=False))
        return (f'\n```{{=html}}\n<figure class="codigo"><figcaption class="cod-cab"><span class="cod-arq">'
                f'{html.escape(caminho)}</span><span class="cod-loc">{html.escape(rotulo)}{" · " if rotulo else ""}'
                f'linhas {inicio}–{fim}</span></figcaption>{corpo}</figure>\n```\n')
    numeradas = "\n".join(f"{i:>4}  {t}" for i, t in enumerate(linhas[inicio - 1:fim], start=inicio))
    return f"\n**Código-fonte — {cab}**\n\n```{linguagem(caminho)}\n{numeradas}\n```\n"


# ======================================================================== macros
FIGURAS: dict[str, str] = {}      # nome -> "Figura 6.2"


def numerar_figuras(texto: str) -> None:
    capitulo, n = "0", 0
    for linha in texto.splitlines():
        m = re.match(r"^##\s+(\d+)\.\s", linha)
        if m:
            capitulo, n = m.group(1), 0
        elif re.match(r"^#\s+Ap[êe]ndice\s+([A-Z])", linha):
            capitulo, n = re.match(r"^#\s+Ap[êe]ndice\s+([A-Z])", linha).group(1), 0
        m = re.match(r"^@@figura\s+(\S+)", linha.strip())
        if m:
            n += 1
            nome = m.group(1)
            if nome in FIGURAS:
                raise SystemExit(f"[ERRO] figura repetida: {nome}")
            if nome not in CATALOGO:
                raise SystemExit(f"[ERRO] figura inexistente no catálogo: {nome}")
            FIGURAS[nome] = f"Figura {capitulo}.{n}"


def svg_figura(nome: str) -> str:
    titulo, fabricar = CATALOGO[nome]
    return fabricar().svg()


def resolver_macros(texto: str, alvo: str) -> str:
    saida = []
    for linha in texto.splitlines():
        s = linha.strip()
        if s.startswith("@@figura"):
            nome = s.split()[1]
            titulo = CATALOGO[nome][0]
            rotulo = FIGURAS[nome]
            if alvo == "html":
                saida.append(f'\n```{{=html}}\n<figure class="figura" id="fig-{nome}">{svg_figura(nome)}'
                             f'<figcaption><b>{rotulo}</b> — {html.escape(titulo)}</figcaption></figure>\n```\n')
            else:
                saida.append(f"\n![{rotulo} — {titulo}](diagramas/{nome}.png)\n")
        elif s.startswith("@@codigo"):
            partes = shlex.split(s)
            caminho, simbolo = partes[1], partes[2]
            inicio, fim = localizar_simbolo(caminho, simbolo)
            if len(partes) > 3:                     # recorte dentro do símbolo: linhas absolutas
                a, b = (int(x) for x in partes[3].split("-"))
                if not (inicio <= a <= b <= fim):
                    raise SystemExit(f"[ERRO] recorte {a}-{b} fora de {simbolo} ({inicio}-{fim}) em {caminho}")
                inicio, fim = a, b
            saida.append(trecho_codigo(caminho, inicio, fim, simbolo, alvo))
        elif s.startswith("@@trecho"):
            _, caminho, a, b, *resto = shlex.split(s)
            saida.append(trecho_codigo(caminho, int(a), int(b), resto[0] if resto else "", alvo))
        elif s.startswith("@@arvore"):
            saida.append(arvore_projeto(alvo))
        else:
            saida.append(linha)
    texto = "\n".join(saida)
    return re.sub(r"\[\[fig:([\w-]+)\]\]", lambda m: (
        f'<a href="#fig-{m.group(1)}">{FIGURAS[m.group(1)]}</a>' if alvo == "html" else FIGURAS[m.group(1)]), texto)


def arvore_projeto(alvo: str) -> str:
    arquivos = subprocess.run(["git", "-C", str(RAIZ), "ls-files"], capture_output=True, text=True,
                              check=True).stdout.splitlines()
    arquivos = [a for a in arquivos if not a.startswith(("historico/", "docs/ebook/"))]
    arvore: dict = {}
    for a in arquivos:
        no = arvore
        for p in a.split("/"):
            no = no.setdefault(p, {})
    linhas: list[str] = ["Agente-BTP/"]

    def desenhar(no, prefixo=""):
        itens = sorted(no.items(), key=lambda kv: (not kv[1], kv[0].lower()))
        for i, (nome, filho) in enumerate(itens):
            ultimo = i == len(itens) - 1
            linhas.append(prefixo + ("└── " if ultimo else "├── ") + nome + ("/" if filho else ""))
            if filho:
                desenhar(filho, prefixo + ("    " if ultimo else "│   "))
    desenhar(arvore)
    corpo = "\n".join(linhas)
    if alvo == "html":
        return f'\n```{{=html}}\n<pre class="arvore">{html.escape(corpo)}</pre>\n```\n'
    return f"\n```text\n{corpo}\n```\n"


# ======================================================================== blocos editoriais
BLOCOS = {
    "conceito": "CONCEITO FUNDAMENTAL", "nobtp": "COMO FUNCIONA NO BTP", "exemplo": "EXEMPLO PRÁTICO",
    "leitura": "LEITURA DO CÓDIGO", "atencao": "ATENÇÃO", "resumo": "RESUMO DO PROCESSO",
    "termo": "TERMO TÉCNICO", "proposta": "PROPOSTA DE MELHORIA", "evidencia": "EVIDÊNCIA",
}


def blocos_para_docx(texto: str) -> str:
    """::: conceito  ->  bloco com rótulo em negrito (o DOCX não tem as caixas coloridas do PDF)."""
    def troca(m):
        return f'::: {{custom-style="Bloco"}}\n**{BLOCOS[m.group(1)]}** · '
    return re.sub(r"^:::\s*(" + "|".join(BLOCOS) + r")\s*$\n", troca, texto, flags=re.M)


# ======================================================================== montagem
def ler_capitulos() -> str:
    arquivos = sorted((AQUI / "capitulos").glob("*.md"))
    return "\n\n".join(a.read_text(encoding="utf-8") for a in arquivos)


def pandoc(md: str, formato: str, extra: list[str] | None = None, saida: Path | None = None) -> str:
    cmd = ["pandoc", "-f", "markdown+fenced_divs+pipe_tables+raw_html+raw_attribute-implicit_figures+smart",
           "-t", formato, "--wrap=none"] + (extra or [])
    if saida:
        cmd += ["-o", str(saida)]
    r = subprocess.run(cmd, input=md, capture_output=True, text=True, cwd=str(SAIDA))
    if r.returncode:
        raise SystemExit(r.stderr)
    if r.stderr.strip():
        print("[pandoc]", r.stderr.strip()[:2000])
    return r.stdout


def titulos_do_html(corpo: str) -> list[tuple[int, str, str]]:
    """(nível, id, texto) de cada título do corpo, na ordem — a mesma ordem dos marcadores do PDF."""
    saida = []
    for m in re.finditer(r'<h([1-6])[^>]*id="([^"]+)"[^>]*>(.*?)</h\1>', corpo, re.S):
        texto = html.unescape(re.sub(r"<[^>]+>", "", m.group(3))).strip()
        saida.append((int(m.group(1)), m.group(2), " ".join(texto.split())))
    return saida


def sumario(titulos, paginas: dict[str, int] | None) -> str:
    itens = []
    for nivel, ident, texto in titulos:
        if nivel > 3:
            continue
        pag = str(paginas.get(ident, "")) if paginas else "000"
        itens.append(f'<li class="s{nivel}"><a href="#{ident}"><span class="st">{html.escape(texto)}</span>'
                     f'<span class="sp">{pag}</span></a></li>')
    return '<nav class="sumario" id="sumario"><div class="sum-tit">Sumário</div><ol>' + "".join(itens) + "</ol></nav>"


def capa() -> str:
    hoje = date.today()
    logo = ('<svg viewBox="0 0 32 32" width="64" height="64"><path d="M16 3.5 29 28h-6.6L16 15.6 9.6 28H3z" '
            'fill="#E3261E"/><circle cx="16" cy="24.4" r="2.9" fill="#E3261E"/></svg>')
    commit = subprocess.run(["git", "-C", str(RAIZ), "log", "-1", "--format=%h · %ad", "--date=short"],
                            capture_output=True, text=True).stdout.strip()
    return f"""
<section class="capa">
  <div class="capa-faixa"></div>
  <div class="capa-logo">{logo}</div>
  <div class="capa-sup">Documentação de referência</div>
  <div class="capa-tit">{TITULO}</div>
  <div class="capa-sub">{SUBTITULO}</div>
  <div class="capa-desc">{DESCRICAO}</div>
  <div class="capa-rodape">Edição de {hoje:%d/%m/%Y} · baseada no repositório em <b>{commit}</b></div>
</section>
<section class="rosto">
  <div class="rosto-tit">{TITULO} — {SUBTITULO}</div>
  <p>Documento produzido a partir da leitura do código-fonte, dos arquivos de configuração, dos testes
  automatizados, do histórico Git (39 commits, de 01/10/2026 a 29/10/2026) e dos relatórios técnicos existentes
  no repositório. Todo trecho de código reproduzido indica o arquivo e as linhas reais de onde foi retirado,
  na versão <b>{commit}</b>.</p>
  <p><b>Como ler as marcações de confiabilidade.</b> Ao longo do texto, quando a distinção importa:</p>
  <ul>
    <li><b>CONFIRMADO</b> — observado diretamente no código, na configuração, no histórico ou em teste executado;</li>
    <li><b>INTERPRETAÇÃO TÉCNICA</b> — conclusão sustentada por evidências, mas não declarada no projeto;</li>
    <li><b>HIPÓTESE</b> — explicação plausível que ainda exige confirmação;</li>
    <li><b>NÃO DETERMINADO</b> — informação que o material disponível não permite obter.</li>
  </ul>
  <p><b>Confidencialidade.</b> Nenhuma senha, cookie, token ou dado operacional real de clientes aparece neste
  documento. Endereços internos são mencionados apenas na forma em que já constam do código versionado, e
  exemplos de WOs e materiais são identificados como fictícios quando não vierem de evidência anonimizada.</p>
  <p><b>Ferramentas de produção.</b> Texto em Markdown convertido por pandoc; PDF gerado pelo Chromium;
  diagramas desenhados em SVG por script próprio; trechos de código extraídos automaticamente do repositório
  (<code>docs/ebook/fonte/construir.py</code>). Veja o <code>README_DOCUMENTACAO.md</code> para atualizar.</p>
</section>"""


def documento_html(corpo: str, titulos, paginas, indice_html: str = "") -> str:
    css = (AQUI / "estilo.css").read_text(encoding="utf-8")
    from pygments.formatters import HtmlFormatter
    css_codigo = HtmlFormatter(style="friendly").get_style_defs(".hl")
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>{TITULO} — {SUBTITULO}</title><style>{css}\n{css_codigo}</style></head><body>
{capa()}
{sumario(titulos, paginas)}
<main>{corpo}</main>
{indice_html}
</body></html>"""


# ======================================================================== PDF
def gerar_pdf(html_doc: str, destino: Path) -> None:
    from playwright.sync_api import sync_playwright
    arquivo_html = SAIDA / "_livro.html"
    arquivo_html.write_text(html_doc, encoding="utf-8")
    with sync_playwright() as p:
        nav = p.chromium.launch()
        pg = nav.new_page()
        pg.goto(arquivo_html.as_uri(), wait_until="load")
        pg.wait_for_timeout(300)
        pg.pdf(path=str(destino), format="A4", print_background=True, outline=True, tagged=True,
               prefer_css_page_size=True)
        nav.close()


def paginas_dos_titulos(pdf: Path, titulos) -> dict[str, int]:
    from pypdf import PdfReader
    leitor = PdfReader(str(pdf))
    lista: list[tuple[str, int]] = []

    def andar(itens):
        for x in itens:
            if isinstance(x, list):
                andar(x)
            else:
                lista.append((" ".join(str(x.title).split()), leitor.get_destination_page_number(x) + 1))
    andar(leitor.outline)
    if len(lista) != len(titulos):
        raise SystemExit(f"[ERRO] {len(lista)} marcadores no PDF x {len(titulos)} títulos no HTML")
    paginas = {}
    for (nivel, ident, texto), (titulo_pdf, pagina) in zip(titulos, lista):
        # o Chromium às vezes repete o texto do título no marcador (títulos com quebra de página forçada)
        # (inteiro ou só a 1ª linha do título quebrado); a posição já é garantida pela ordem.
        a, b = _norm(titulo_pdf), _norm(texto)
        if not (a.startswith(b[:12]) and a.endswith(b[-12:]) and len(b) <= len(a) <= 2 * len(b)):
            raise SystemExit(f"[ERRO] ordem dos títulos não confere: '{texto}' x '{titulo_pdf}'")
        paginas[ident] = pagina
    return paginas


def _norm(t: str) -> str:
    return re.sub(r"\W+", "", unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower())


def texto_por_pagina(pdf: Path) -> list[str]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    saida = []
    for i in range(len(doc)):
        tp = doc[i].get_textpage()
        saida.append(tp.get_text_range())
    return saida


def indice_remissivo(pdf: Path, primeira_pagina_corpo: int) -> str:
    termos = json.loads((AQUI / "indice_termos.json").read_text(encoding="utf-8"))
    textos = texto_por_pagina(pdf)
    entradas = []
    for termo, variantes in sorted(termos.items(), key=lambda kv: _norm(kv[0])):
        padroes = [re.compile(r"(?<![\wÀ-ÿ])" + re.escape(v) + r"(?![\wÀ-ÿ])", re.I) for v in variantes]
        paginas = [i + 1 for i, t in enumerate(textos)
                   if i + 1 >= primeira_pagina_corpo and any(p.search(" ".join(t.split())) for p in padroes)]
        if not paginas:
            print(f"[índice] termo sem ocorrência: {termo}")
            continue
        entradas.append((termo, _faixas(paginas[:14]) + (" …" if len(paginas) > 14 else "")))
    letras = {}
    for termo, pags in entradas:
        letra = unicodedata.normalize("NFKD", termo[0]).encode("ascii", "ignore").decode().upper() or termo[0]
        letras.setdefault(letra, []).append((termo, pags))
    corpo = "".join(f'<div class="ind-letra"><div class="ind-l">{l}</div>' + "".join(
        f'<div class="ind-item"><span>{html.escape(t)}</span><span class="ind-p">{p}</span></div>' for t, p in itens)
        + "</div>" for l, itens in letras.items())
    return (f'<section class="indice"><h1 id="indice-remissivo" class="sem-numero">Índice remissivo</h1>'
            f'<div class="ind-cols">{corpo}</div></section>')


def _faixas(paginas: list[int]) -> str:
    saida, i = [], 0
    while i < len(paginas):
        j = i
        while j + 1 < len(paginas) and paginas[j + 1] == paginas[j] + 1:
            j += 1
        saida.append(str(paginas[i]) if i == j else f"{paginas[i]}–{paginas[j]}")
        i = j + 1
    return ", ".join(saida)


def carimbar(pdf_entrada: Path, pdf_saida: Path, titulos, paginas, pular: int) -> None:
    """Cabeçalho (capítulo corrente) e rodapé (página) em todas as páginas, menos capa e rosto."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.colors import HexColor
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    fonte = "/usr/share/fonts/truetype/inter/Inter-Regular.ttf"
    for cand in (fonte, *[str(p) for p in Path("/usr/share/fonts").rglob("Inter-Regular.ttf")],
                 *[str(p) for p in Path("/usr/share/fonts").rglob("DejaVuSans.ttf")]):
        if Path(cand).exists():
            pdfmetrics.registerFont(TTFont("Rodape", cand))
            break
    leitor = PdfReader(str(pdf_entrada))
    escritor = PdfWriter(clone_from=leitor)
    inicio_partes = {paginas[i] for n, i, _ in titulos if n == 1}
    capitulos = sorted((paginas[i], t) for n, i, t in titulos if n in (1, 2))
    for indice, pagina in enumerate(escritor.pages):
        numero = indice + 1
        if numero <= pular:
            continue
        largura = float(pagina.mediabox.width)
        altura = float(pagina.mediabox.height)
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(largura, altura))
        cinza = HexColor("#8B8F96")
        c.setFillColor(cinza)
        c.setFont("Rodape", 8)
        if numero not in inicio_partes and numero > pular:
            atual = next((t for p, t in reversed(capitulos) if p <= numero), "")
            c.drawString(56, altura - 30, "Agente BTP — Documentação técnica")
            c.drawRightString(largura - 56, altura - 30, atual[:78])
            c.setStrokeColor(HexColor("#E4E5E8"))
            c.setLineWidth(0.5)
            c.line(56, altura - 36, largura - 56, altura - 36)
        c.setFont("Rodape", 8.5)
        c.drawCentredString(largura / 2, 26, str(numero))
        c.save()
        buf.seek(0)
        pagina.merge_page(PdfReader(buf).pages[0])
        pagina.compress_content_streams(level=9)   # merge_page grava o conteúdo descomprimido (127 MB)
    escritor.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    escritor.add_metadata({"/Title": f"{TITULO} — {SUBTITULO}", "/Author": "Documentação do projeto Agente BTP",
                           "/Subject": DESCRICAO})
    with open(pdf_saida, "wb") as f:
        escritor.write(f)


# ======================================================================== principal
def main(rapido: bool = False) -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    fonte_md = ler_capitulos()
    numerar_figuras(fonte_md)
    md_html = resolver_macros(fonte_md, "html")
    corpo = pandoc(md_html, "html5")
    titulos = titulos_do_html(corpo)
    print(f"{len(titulos)} títulos, {len(FIGURAS)} figuras, {len(EVIDENCIAS)} trechos de código")

    tmp1, tmp2, tmp3 = SAIDA / "_p1.pdf", SAIDA / "_p2.pdf", SAIDA / "_p3.pdf"
    gerar_pdf(documento_html(corpo, titulos, None), tmp1)
    pag1 = paginas_dos_titulos(tmp1, titulos)
    gerar_pdf(documento_html(corpo, titulos, pag1), tmp2)
    pag2 = paginas_dos_titulos(tmp2, titulos)
    if pag1 != pag2:
        print("[aviso] páginas mudaram ao preencher o sumário; usando a 2ª passagem")
    primeira = min(pag2.values())
    from pypdf import PdfReader
    total2 = len(PdfReader(str(tmp2)).pages)
    indice = indice_remissivo(tmp2, primeira)
    titulos_final = titulos + [(1, "indice-remissivo", "Índice remissivo")]
    pag_final = dict(pag2, **{"indice-remissivo": total2 + 1})
    gerar_pdf(documento_html(corpo, titulos_final, pag_final, indice), tmp3)
    pag3 = paginas_dos_titulos(tmp3, titulos_final)
    diferentes = {k: (pag_final[k], pag3[k]) for k in pag3 if pag3[k] != pag_final[k]}
    if diferentes:
        raise SystemExit(f"[ERRO] sumário não confere com o PDF final: {list(diferentes.items())[:5]}")
    final = SAIDA / "Agente_BTP_Documentacao_Completa.pdf"
    carimbar(tmp3, final, titulos_final, pag3, pular=2)
    for t in (tmp1, tmp2, tmp3):
        t.unlink(missing_ok=True)
    print(f"PDF: {final} ({len(PdfReader(str(final)).pages)} páginas)")
    (SAIDA / "evidencias_codigo.json").write_text(json.dumps(EVIDENCIAS, ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
    # diagramas soltos (SVG) para revisão futura
    for nome in CATALOGO:
        (DIAG_DIR / f"{nome}.svg").write_text(svg_figura(nome), encoding="utf-8")
    if rapido:
        return
    gerar_editaveis(fonte_md)


def gerar_editaveis(fonte_md: str) -> None:
    from playwright.sync_api import sync_playwright
    png_dir = SAIDA / "diagramas"
    png_dir.mkdir(exist_ok=True)
    with sync_playwright() as p:
        nav = p.chromium.launch()
        pg = nav.new_page(device_scale_factor=2)
        for nome in CATALOGO:
            svg = svg_figura(nome)
            pg.set_content(f'<html><body style="margin:0">{svg}</body></html>')
            pg.locator("svg").screenshot(path=str(png_dir / f"{nome}.png"))
        nav.close()
    md = resolver_macros(fonte_md, "md")
    md_docx = blocos_para_docx(md)
    (SAIDA / "Agente_BTP_Documentacao.md").write_text(md, encoding="utf-8")
    referencia = AQUI / "referencia.docx"
    extra = ["--toc", "--toc-depth=2", "--resource-path", str(SAIDA)] + (
        ["--reference-doc", str(referencia)] if referencia.exists() else [])
    pandoc(md_docx, "docx", extra, SAIDA / "Agente_BTP_Documentacao_Editavel.docx")
    print("DOCX e Markdown gerados.")


if __name__ == "__main__":
    main(rapido="--rapido" in sys.argv)
