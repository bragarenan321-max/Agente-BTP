# Documentação do Agente BTP (e-book)

## Arquivos produzidos (`docs/ebook/saida/`)

| Arquivo | O que é |
|---|---|
| `Agente_BTP_Documentacao_Completa.pdf` | O e-book: capa, sumário clicável, 7 partes, 33 capítulos, 4 apêndices, índice remissivo |
| `Agente_BTP_Documentacao_Editavel.docx` | Versão editável (Word), gerada do mesmo texto-fonte |
| `Agente_BTP_Documentacao.md` | Versão Markdown, com os trechos de código já resolvidos |
| `evidencias_codigo.json` | Lista de cada trecho de código citado: arquivo, símbolo e linhas |
| `diagramas/*.png` | Os 26 diagramas em imagem (usados pelo DOCX/MD) |

Os diagramas em vetor (SVG) ficam em `docs/ebook/diagramas/`.

## Como o livro é gerado

O texto fica em `docs/ebook/fonte/capitulos/*.md` (lidos em ordem alfabética). O script `fonte/construir.py`:

1. resolve as macros do texto:
   - `@@figura nome` — insere um diagrama de `fonte/diagramas.py` (catálogo `CATALOGO`);
   - `@@codigo arquivo Simbolo [a-b]` — copia do código **atual** a função ou classe (localizada pela árvore sintática), opcionalmente só as linhas a–b;
   - `@@trecho arquivo de ate "rótulo"` — copia um intervalo de linhas;
   - `@@arvore` — árvore dos arquivos versionados;
   - `[[fig:nome]]` — referência cruzada "Figura N.M";
2. converte para HTML com pandoc e aplica `fonte/estilo.css`;
3. imprime o PDF com o Chromium em três passagens, para numerar o sumário e montar o índice remissivo (`fonte/indice_termos.json`);
4. carimba cabeçalho (capítulo corrente) e rodapé (página).

```
cd docs/ebook/fonte
python3 construir.py --rapido   # só o PDF
python3 construir.py            # PDF + DOCX + MD + PNGs dos diagramas
```

Requisitos: Python 3 com `pypdf`, `pypdfium2`, `reportlab`, `pygments`, `playwright` (com Chromium) e o programa `pandoc`.

## Como atualizar depois de mudar o código

- Os trechos de código são extraídos na hora do build: se uma função mudar, o livro mostra a versão nova automaticamente.
- **Números de linha citados no texto corrido** (por exemplo, "linhas 141–150") **não** se atualizam sozinhos. Depois de alterar um arquivo, procure suas citações (`grep -n "nome_do_arquivo.py" fonte/capitulos/*.md`) e confira.
- Um recorte `@@codigo ... a-b` fora dos limites da função interrompe o build com erro, o que ajuda a detectar referências velhas.
- Números de testes (Cap. 24) devem ser obtidos rodando de novo `python -m pytest -q testes`.
