# Parte IV — Como o código é construído e alterado {#parte4}

As partes anteriores explicaram **o que** o agente faz e **em que ordem**. Esta parte ensina a **ler** e a **modificar** o código com segurança. Primeiro, os conceitos de programação necessários, sempre com exemplos do próprio projeto. Depois, a anatomia de trechos importantes que ainda não foram analisados. Por fim, dois roteiros práticos: como acrescentar uma funcionalidade e como alterar uma existente sem quebrar o resto.

## 18. Como ler o código do Agente BTP {#cap18}

O programa local é escrito em **Python** (pasta `agente_btp/`, 25 módulos `.py`). A parte que roda dentro da aba do BTP é escrita em **JavaScript** (`agente_btp/web/agente_btp.js`), e a tela é uma página **HTML** com JavaScript embutido (`agente_btp/web/index.html`). Este capítulo usa principalmente Python, porque é onde está quase toda a lógica.

### 18.1 Variáveis e constantes

::: termo
**Variável** é um nome que guarda um valor enquanto o programa roda — como uma etiqueta colada numa caixa. **Constante** é uma variável que, por convenção, nunca muda depois de definida. Em Python, constantes são escritas em MAIÚSCULAS.
:::

No Agente BTP, as constantes concentram os números que governam o comportamento:

| Constante | Arquivo | Valor | O que controla |
|---|---|---|---|
| `LIMITE_PAGINAS` | `cliente.py`, linha 41 | 50 | Máximo de páginas seguidas numa resposta paginada |
| `INTERVALO_SONDA_SEG` | `motor.py`, linha 30 | 60.0 | De quanto em quanto tempo a sessão é verificada |
| `SONDA_A_CADA_WOS` | `motor.py`, linha 31 | 100 | Verificação da sessão a cada 100 WOs concluídas |
| `REVERIFICAR_PAUSA_SEG` | `motor.py`, linha 32 | 30.0 | Intervalo de nova tentativa enquanto pausado por sessão |
| `GRUPOS_VERIFICADOS_POR_INTEIRO` | `motor.py`, linha 36 | 3 | Grupos de lote conferidos WO a WO contra a consulta individual |
| `LIMITE_TRABALHADORES_ABA` | `ponte.py`, linha 29 | 6 | Consultas simultâneas máximas dentro da aba |
| `VERSAO_CONFIG` | `config.py` | 6 | Versão mínima aceita da fonte gravada |
| `LIMITE` | `historico.py`, linha 17 | 300 | Análises guardadas no histórico |
| `FILTRO_PADRAO` | `exportacao.py`, linha 159 | "Estoque indisponível" | Mensagem padrão dos itens da planilha |

::: atencao
Mudar uma constante parece inofensivo, mas muitas delas estão amarradas a testes e a decisões documentadas. Por exemplo, aumentar `LIMITE_TRABALHADORES_ABA` faz a aba disparar mais consultas simultâneas ao BTP, o que pode provocar respostas HTTP 429 ("reduza o ritmo"). Antes de alterar, procure onde a constante é usada (Capítulo 21).
:::

### 18.2 Tipos de dados

Todo valor tem um **tipo**, que define o que se pode fazer com ele. Os tipos que aparecem o tempo todo no agente:

| Tipo | Exemplo no projeto | Por que importa |
|---|---|---|
| `str` (texto) | `"02235|230771207"` (uma WO), `"000000000022056366"` (um material) | Identificadores são **sempre** texto, para preservar zeros à esquerda (Cap. 14.3) |
| `int` (inteiro) | `concorrencia = 4` | Contagens e limites |
| `float` (número com vírgula) | `tempo_max_wo_seg = 180.0` | Tempos em segundos |
| `Decimal` (número exato) | quantidade faltante, valores em R$ | Evita erros de arredondamento do `float` |
| `bool` (verdadeiro/falso) | `exportar_incompletos = True` | Liga/desliga opções |
| `None` (vazio) | quantidade que não veio do BTP | O agente usa `None` para dizer "não sei", em vez de inventar um valor |
| `list` (lista) | lista de WOs | Coleção ordenada |
| `dict` (dicionário) | registro devolvido pelo BTP: `{"material": "...", "quantidade": "1e+0"}` | Pares nome → valor |

O código usa **anotações de tipo**, que não mudam o comportamento, mas documentam a intenção. Em `def normalizar_wo(valor) -> str:` (`entrada.py`, linha 11), a seta `-> str` informa que a função devolve um texto. Em `quantidade_faltante: Decimal | int | None` (`modelos.py`, linha 60), a barra `|` significa "ou": o campo pode ser um número exato, um inteiro ou vazio.

### 18.3 Condições

Uma **condição** escolhe um caminho conforme uma pergunta de sim ou não. É o `if` (se), `elif` (senão, se) e `else` (senão). O exemplo mais importante do projeto é a decisão do estado de um item na auditoria (`auditoria.py`, linhas 141–150, mostrado no Capítulo 14):

```python
if divergente:
    estado = Auditoria.DIVERGENTE        # mais grave primeiro
elif incompleto:
    estado = Auditoria.INCOMPLETO
elif feitas < previstas or previstas == 0:
    estado = Auditoria.NAO_CONCLUIDO
else:
    estado = Auditoria.VALIDADO          # só chega aqui se nada acima for verdade
```

A **ordem** das perguntas é a regra de negócio: divergência pesa mais que falta de dado, que pesa mais que conferência incompleta. Trocar a ordem das linhas mudaria o significado do resultado.

### 18.4 Repetições

Uma **repetição** (laço) executa o mesmo bloco várias vezes. Em Python há duas formas:

- `for item in colecao:` — percorre cada elemento. Ex.: `for campo in CAMPOS:` compara os cinco campos de um item, um de cada vez (`auditoria.py`, linha 115).
- `while condição:` — repete enquanto a condição for verdadeira. Ex.: `while not self._parar.is_set():` mantém a espera da sessão até o usuário parar (`motor.py`, linha 300).

Há também a **compreensão de lista**, uma forma compacta de montar uma lista a partir de outra. Em `_preencher` (`exportacao.py`, linha 151):

```python
return [SEM_INFO if v is None or v == "" else v for v in valores]
```

Lê-se: "para cada `v` em `valores`, coloque `"-"` se `v` estiver vazio; senão, o próprio `v`". É assim que nenhuma célula da aba Dados fica em branco.

### 18.5 Funções, parâmetros e retornos

Uma **função** é um bloco com nome que recebe informações (**parâmetros**), faz um trabalho e devolve um resultado (**retorno**). A função mais simples e didática do projeto:

@@codigo agente_btp/entrada.py normalizar_wo

- `valor` é o parâmetro: o que veio da planilha ou do texto colado.
- `return ""` encerra a função cedo quando não há valor.
- `str(valor).strip()` converte para texto e tira espaços das pontas; `re.sub(...)` remove espaços ao redor da barra.
- O retorno é o texto da WO, **sem alterar nenhum dígito**.

Parâmetros podem ter **valor padrão**. Em `Loja.concluir(self, run_id, r, origem_dado: str = "", texto_log: str = "")`, quem chama pode omitir os dois últimos.

### 18.6 Importações e módulos

Um **módulo** é um arquivo `.py`. Um módulo usa outro por meio de `import`. Há dois tipos no projeto:

- **Da biblioteca padrão ou de terceiros:** `import json`, `from openpyxl import Workbook`.
- **Internas do pacote:** `from .modelos import Status` — o ponto inicial significa "deste mesmo pacote" (`agente_btp`).

A matriz de importações internas está no Capítulo 17. Ela é o melhor mapa para responder "se eu mudar este arquivo, quem pode ser afetado?".

### 18.7 Classes e objetos

Uma **classe** é um molde; um **objeto** é algo construído a partir do molde. O projeto usa classes de três maneiras:

1. **Estruturas de dados** com `@dataclass`, que gera automaticamente o código de criação. `Preferencias` (`preferencias.py`, linhas 21–45) é uma ficha com todos os campos das Configurações e seus padrões.
2. **Enumerações** (`Enum`): listas fechadas de valores possíveis. `Status` e `Auditoria` (`modelos.py`) garantem que ninguém escreva "Validdo" por engano — só existem os valores declarados.
3. **Componentes com estado**: `Agente`, `Motor`, `PonteAba`, `ClienteBTP`, `Loja`. Cada objeto guarda informações próprias (`self.loja`, `self.prefs`...) e oferece **métodos** (funções da classe). `self` é o próprio objeto.

### 18.8 Exceções

Uma **exceção** é a forma de um trecho de código avisar "não consegui continuar" — ela interrompe o caminho normal e sobe até alguém que a trate com `try`/`except`. O agente define sua própria família de exceções:

@@trecho agente_btp/modelos.py 130 158 "família de exceções do agente"

::: leitura
- `_ComMotivo` (linhas 130–138) é a "mãe": toda exceção do agente carrega um **motivo** (um código da taxonomia do diagnóstico, como `http_403`) e uma **evidência** (URL, situação HTTP, trecho da resposta, já sem segredos).
- As quatro filhas dizem **o que fazer** com o problema: `SessaoExpirada` pausa a análise; `FalhaComunicacao` pode ser repetida; `ErroConsulta` não adianta repetir; `TempoEsgotado` encerra a WO.
- Herança (`class SessaoExpirada(_ComMotivo)`) significa "é um tipo de". Um `except _ComMotivo` captura todas as quatro.
:::

### 18.9 Estruturas de dados centrais

| Estrutura | Arquivo | Representa |
|---|---|---|
| `Ocorrencia` | `modelos.py`, 78–86 | Um item lido numa leitura (valores + registro bruto) |
| `Leitura` | `modelos.py`, 90–103 | Uma rodada de leitura de uma WO (extração, auditoria, extra) |
| `LinhaMensagem` | `modelos.py`, 54–74 | Um item com os valores **finais** após a auditoria |
| `ResultadoWO` | `modelos.py`, 107–127 | Tudo sobre uma WO: situação, auditoria, linhas, leituras, motivo |
| `Resposta` | `cliente.py`, 29–34 | Uma resposta HTTP trazida pela aba |
| `ApiConfig` | `config.py`, 34–53 | A fonte de dados confirmada (URLs-modelo e nomes de campos) |

### 18.10 Entrada e saída

**Entrada e saída** (E/S) é tudo o que o programa lê ou grava fora da memória. No agente:

| Operação | Onde | Detalhe |
|---|---|---|
| Ler a planilha de WOs | `entrada.ler_wos_planilha` | openpyxl, só leitura |
| Gravar configurações | `preferencias.salvar`, `config.salvar`, `historico._gravar` | Arquivo temporário `.tmp` + `replace` (gravação atômica: nunca fica meio escrito) |
| Base de execução | `loja.Loja` | SQLite em `dados/` |
| Registro de eventos | `__main__.configurar_log` | Arquivo `logs/agente_btp.log`, nunca na janela preta |
| Relatório | `exportacao.gerar_planilha` | `.xlsx` em `saidas/` |

O padrão de gravação atômica aparece em três módulos. Em `historico.py`, linhas 38–42:

@@trecho agente_btp/historico.py 38 42 "gravação atômica"

Se o computador desligar no meio, o arquivo antigo continua intacto, porque a substituição (`replace`) é uma operação única do sistema operacional.

### 18.11 Comunicação entre componentes

Os componentes conversam de três formas:

1. **Chamada direta de função** — o caso comum: `motor` chama `processar_grupo`, que chama `consolidar`.
2. **HTTP local** — a tela e a aba falam com o servidor Flask por rotas como `/api/estado` e `/api/agente/ciclo` (Apêndice A).
3. **Filas entre threads** — o servidor entrega comandos ao `Agente` por uma fila (`Agente.enviar`), e a `PonteAba` guarda consultas numa fila até a aba buscá-las.

### 18.12 Execução simultânea: threads e funções assíncronas

::: conceito
**Thread** é uma linha de execução independente dentro do mesmo programa — como vários atendentes numa mesma loja. O agente usa threads para que o servidor continue respondendo à tela enquanto uma análise roda, e para que até 8 grupos de WOs sejam processados ao mesmo tempo (`concorrencia`, faixa 1–8).
:::

Para que as threads não atrapalhem umas às outras, o código usa:

- **Travas** (`threading.Lock`): só uma thread por vez entra no trecho protegido. Ex.: `with self._trava:` em `Loja.concluir`.
- **Eventos** (`threading.Event`): sinais de liga/desliga observados por várias threads. Ex.: `self._parar`, `self._sessao_caiu` no `Motor`.
- **Filas** (`queue.Queue`): os grupos de WOs ficam numa fila de onde cada trabalhador retira o próximo (`motor.py`, linha 177).

No JavaScript da aba, a simultaneidade é feita de outro jeito: **funções assíncronas** (`async function`). A palavra `await` significa "espere esta operação terminar, mas deixe o navegador fazer outras coisas enquanto isso". Em `agente_btp.js`, a função `trabalhador` (linha 231) é chamada várias vezes, e cada cópia fica num laço pedindo tarefas ao servidor e executando consultas com `await fetchOriginal(...)` (linha 195).

::: resumo
Para ler um trecho do agente: (1) identifique o módulo e a função; (2) leia a docstring (o texto entre aspas triplas logo abaixo do `def`), que quase sempre explica a regra; (3) siga as condições na ordem; (4) observe quais exceções podem ser levantadas e quem as captura; (5) consulte a matriz do Capítulo 17 para saber quem depende do trecho.
:::

## 19. Anatomia do código {#cap19}

Este capítulo analisa trechos ainda não examinados em detalhe, em ordem de importância para o funcionamento. Comentários didáticos acrescentados por este livro aparecem **fora** do código, nos blocos "Leitura do código"; os comentários que aparecem dentro dos trechos são **originais** do projeto.

### 19.1 Classificar cada resposta do BTP

@@codigo agente_btp/cliente.py classificar

| Aspecto | Descrição |
|---|---|
| Função | Transformar uma resposta HTTP em "dado aproveitável" ou numa exceção com motivo |
| Entrada | `Resposta` (situação, tipo de conteúdo, corpo, se houve redirecionamento) e a URL |
| Saída | O corpo (texto) ou uma das exceções `SessaoExpirada`, `FalhaComunicacao`, `ErroConsulta` |
| Dependências | `marcador_login` (padrões de página de login), `diagnostico.evidencia` |
| No fluxo | Chamada por `ClienteBTP._chamar` em **toda** consulta |

::: leitura
- **Linhas 63–69** — redirecionamento, 401 e 403 são tratados como **sessão**: a análise pausa e nenhuma WO vira falha. O motivo é diferente em cada caso (`sessao_expirada`, `http_401`, `http_403`), para o diagnóstico.
- **Linhas 70–74** — 429 (excesso de pedidos) e 5xx (falha do servidor) são **passageiros**: `FalhaComunicacao`, que `_com_retentativa` pode repetir.
- **Linhas 75–78** — 404 e outros 4xx são **erros da consulta**: repetir não resolve.
- **Linhas 79–84** — o caso sutil: quando a sessão cai, o *approuter* (o porteiro de entrada do BTP) pode devolver a página de login com situação **200**, que normalmente significaria sucesso. Por isso, um corpo HTML ou com marcas de formulário de login também é tratado como sessão expirada.
:::

::: atencao
**Efeito de uma alteração.** Classificar 403 como `ErroConsulta` faria uma queda de sessão marcar centenas de WOs como falha em segundos. O motor tem uma proteção complementar: se um erro de sessão acontecer e a sonda mostrar que a sessão está válida, as WOs do grupo são refeitas uma a uma e só a que repetir o erro falha (`motor._sessao_na_wo`, linhas 225–249).
:::

### 19.2 Repetir só o que vale a pena repetir

@@codigo agente_btp/processador.py _com_retentativa

::: leitura
- Repete **apenas** `FalhaComunicacao` (linha 37). Sessão, erro de consulta e tempo esgotado sobem imediatamente.
- A espera cresce: 1 s, 2 s, 4 s, limitada a 5 s (`min(2 ** tentativa, 5)`, linha 40) — a chamada "espera exponencial", que dá tempo ao servidor para se recuperar.
- Linha 41: se a espera ultrapassaria o prazo da WO, desiste na hora, em vez de dormir à toa.
- `tentativas_extras` vem da preferência `repeticoes_falha` (padrão 2, faixa 0–10).
:::

### 19.3 Um trabalhador do motor

@@codigo agente_btp/motor.py Motor._trabalhador

::: leitura
- **Linha 174** — cada trabalhador tem o seu próprio `ClienteBTP` (criado pela "fábrica"), com seu próprio ritmo.
- **Linhas 175–179** — retira grupos da fila até ela esvaziar ou até ser preciso segurar (pausa, parada, sessão caída).
- **Linha 180** — marca as WOs do grupo como "em andamento" na base **antes** de consultar. É isso que permite devolvê-las à fila numa queda.
- **Linhas 185–186** — os 3 primeiros grupos da análise são conferidos inteiramente contra a consulta individual; os seguintes, por amostra.
- **Linhas 190–193** — os dois destinos de erro: sessão vai para `_sessao_na_wo`; qualquer outra exceção inesperada vai para `_excecao`, que registra o *traceback* (o rastro das chamadas) e marca as WOs do grupo como falha **com motivo** `excecao_app`. Nenhuma exceção é silenciosamente ignorada.
:::

### 19.4 Validar as Configurações

@@codigo agente_btp/preferencias.py validar

::: leitura
- Começa pelos **padrões** (`asdict(Preferencias())`) e só substitui o que veio e é válido. Um campo ausente mantém o padrão.
- Converte cada valor para o tipo do padrão (linhas 72–79). Texto que não vira número é recusado com mensagem.
- Recusa infinito e "não é número" (linhas 82–84).
- Ajusta para dentro da faixa (linhas 85–89): `concorrencia = 50` vira 8 e o problema é relatado.
- Textos livres têm espaços normalizados e no máximo 120 caracteres (linhas 90–91).
- Opções fechadas (`divergencia`, `wo_sem_resultado`, `em_erro`) só aceitam os valores previstos (linhas 92–94).
:::

O resultado é que **nenhum valor recebido da tela chega ao motor sem passar por aqui**. Os testes `test_preferencias_persistem_e_sao_validadas` e `test_tempos_com_decimal_e_valores_invalidos` (em `test_auditoria.py`) exercitam esse caminho.

### 19.5 A taxonomia do diagnóstico

O arquivo `agente_btp/diagnostico.py` contém um dicionário `TAXONOMIA` (linhas 20–73) com 24 motivos. Cada motivo tem uma **categoria**, uma **descrição** e a **próxima ação** recomendada ao usuário:

@@trecho agente_btp/diagnostico.py 76 93 "consulta à taxonomia"

Motivos podem ter um complemento depois de dois-pontos: `campo_ausente:fornecedor`. `motivo_base` separa a parte que está na taxonomia; `descricao` acrescenta "Campo: fornecedor." ao texto. O Apêndice C lista todos os motivos.

### 19.6 Proteger segredos nas evidências

@@trecho agente_btp/diagnostico.py 96 122 "remoção de segredos"

::: leitura
- Antes de qualquer trecho de resposta ser guardado como evidência, `sanitizar` substitui padrões de *tokens* JWT, cabeçalhos `Bearer`, campos `SAMLResponse`, `JSESSIONID`, `password`, `senha` e semelhantes por `<removido>`.
- O corpo guardado é cortado em 800 caracteres (`LIMITE_CORPO`) e a URL em 1.500.
- **Limitação:** é uma lista de padrões conhecidos. Um segredo com formato diferente não seria reconhecido. Como o agente não lê nem guarda cookies (quem os envia é o próprio navegador, porque a consulta é feita com `credentials: 'same-origin'` a partir da aba já logada — `agente_btp.js`, linha 196), o risco real se concentra no corpo das respostas, que raramente contém segredos. <span class="interpretacao">INTERPRETAÇÃO TÉCNICA</span>
:::

### 19.7 Recusar arquivos fora da pasta de saídas

@@codigo agente_btp/agente.py caminho_saida

A rota `/api/download/<nome>` usa esta função. `resolve()` transforma o caminho em absoluto, resolvendo `..`; se a pasta do arquivo resultante não for exatamente `saidas/`, a resposta é "não encontrado". Um pedido como `/api/download/..%2Fconfig%2Fapi.json` não consegue sair da pasta.

### 19.8 O que este capítulo não mostrou

Por economia de espaço, ficaram de fora os módulos de **descoberta da fonte** usados só na preparação (`autoconfig.py`, 482 linhas; `analisador.py`, `exploracao.py`, `sondagem.py`, `raiox.py`). Seu papel foi explicado no Capítulo 12; para estudá-los, comece por `autoconfig.receita_monitor` (linhas 453–482), que descreve a fonte confirmada, e por `Agente._preparar_com_wos` (linhas 468–562), que define a ordem de tentativas.

## 20. Como implementar uma nova funcionalidade {#cap20}

Este capítulo usa um exemplo **proposto**, que **não está implementado** no projeto. O código mostrado aqui é ilustrativo e não foi testado; ele existe para ensinar o caminho.

::: proposta
**Funcionalidade de exemplo:** permitir que a planilha aceite **mais de uma** mensagem de item. Hoje a preferência `mensagem_planilha` aceita um único texto (padrão "Estoque indisponível"). A proposta é aceitar vários textos separados por ponto e vírgula, por exemplo `Estoque indisponível; Estoque insuficiente`.
:::

### 20.1 Os doze passos aplicados ao exemplo

| Passo | No exemplo |
|---|---|
| 1. Comportamento desejado | Item cuja mensagem do item seja **qualquer uma** das listadas vira linha; os demais continuam na Observação |
| 2. Componente responsável | `exportacao.entra_na_planilha` (linhas 176–178) decide se o item entra |
| 3. Dependências | `_itens_da_wo`, `nota_outros` (texto "mensagem diferente de ..."), `_linhas_dados` (texto "Nenhum item com ..."), `verificar_planilha` (recalcula com o mesmo filtro), a tela (`index.html`, campo `mensagem_planilha`, linha 1265) |
| 4. Informações necessárias | O texto da preferência; nenhuma informação nova do BTP |
| 5. Comportamento esperado | Separar por `;`, ignorar partes vazias, comparar cada parte normalizada (sem acento, maiúsculas, espaços) |
| 6. Arquivos a alterar | `exportacao.py`; o texto de ajuda em `index.html`; `testes/test_auditoria.py` |
| 7. Implementar | Ver 20.2 |
| 8. Integrar | Nenhuma rota nova: a preferência já é gravada e lida (`preferencias.py`) e passada a `gerar_planilha` e `verificar_planilha` (`agente.py`, linhas 909–913) |
| 9. Testar | Casos normal, vazio, com espaços extras e com um texto só (compatibilidade) |
| 10. Não regredir | Rodar a suíte inteira; os testes atuais do filtro precisam continuar passando |
| 11. Registrar | Commit com mensagem descritiva; seção nova em `docs/relatorio-correcao-raiz.md`, como o projeto faz a cada rodada |
| 12. Implantar | Gerar o `.zip` e substituir a pasta no computador do usuário (Cap. 9) |

### 20.2 A alteração proposta

<span class="hipotese">PROPOSTA — NÃO IMPLEMENTADA</span>

```python
# PROPOSTA (não faz parte do projeto): agente_btp/exportacao.py
def _filtros(filtro: str | None) -> set[str]:
    """Divide 'A; B' em {'a', 'b'} já normalizados. Vazio = sem filtro."""
    return {_normalizar(p) for p in (filtro or "").split(";") if p.strip()}


def entra_na_planilha(l: LinhaMensagem, filtro: str | None) -> bool:
    alvos = _filtros(filtro)
    return not alvos or _normalizar(mensagem_do_item(l)) in alvos
```

Note três escolhas:

1. **Compatibilidade:** um texto sem `;` produz um conjunto com um só elemento — o comportamento atual é preservado.
2. **Vazio continua significando "todos"**, como hoje.
3. **A normalização é a mesma** (`_normalizar`), então "ESTOQUE INDISPONIVEL" continua sendo aceito.

### 20.3 Os testes propostos

```python
# PROPOSTA (não faz parte do projeto): testes/test_auditoria.py
def test_planilha_com_duas_mensagens_no_filtro(tmp_path):
    # Monta duas WOs: uma com "Estoque indisponível", outra com "Estoque insuficiente"
    # e uma terceira mensagem qualquer. Gera com
    # Preferencias(mensagem_planilha="Estoque indisponível; Estoque insuficiente").
    # Espera: as duas primeiras viram linha; a terceira vai para a Observação;
    # verificar_planilha(...) devolve lista vazia.
    ...
```

O esqueleto acima está em **pseudocódigo** (as reticências indicam o que ainda precisaria ser escrito). Um teste real deveria seguir o modelo de `test_planilha_so_com_estoque_indisponivel_e_outros_na_observacao`, que já monta resultados e confere as abas.

### 20.4 Riscos desta funcionalidade

- **Texto de Observação:** `nota_outros` diz "mensagem diferente de "<filtro>"". Com vários textos, a frase continuaria correta, mas ficaria longa; vale ajustar a redação.
- **Análises antigas:** `regerar_relatorio` usa as preferências **gravadas na análise**, então relatórios antigos regenerados continuariam com o filtro antigo — isso é desejável, mas precisa ser explicado ao usuário.
- **Mensagem com ponto e vírgula:** se alguma mensagem do BTP contiver `;`, ela não poderia ser usada como filtro. <span class="naodet">NÃO DETERMINADO</span> se isso ocorre nos dados reais.

## 21. Como alterar uma funcionalidade existente sem comprometer o sistema {#cap21}

### 21.1 O método

1. **Encontrar a implementação atual.** Procure pelo texto que aparece na tela ou na planilha. No terminal, dentro da pasta do projeto: `grep -rn "Tipo Material" agente_btp testes` lista cada arquivo e linha onde o texto aparece.
2. **Encontrar quem depende dela.** Use a matriz do Capítulo 17 e procure o nome da função: `grep -rn "linhas_dados" agente_btp testes`.
3. **Entender os efeitos colaterais.** Pergunte: o resultado é gravado na base (afeta análises antigas)? É conferido por `verificar_planilha`? Aparece na tela? É usado em fórmulas pelo usuário?
4. **Preservar o comportamento existente.** Antes de mudar, rode os testes e guarde uma planilha gerada (a "linha de base").
5. **Testar.** `python -m pytest -q testes` (Capítulo 24); depois, os testes de navegador relevantes.
6. **Comparar antes e depois.** Gere o relatório da mesma análise antes e depois (Histórico → Baixar relatório, que regenera a partir da base) e compare as abas.
7. **Reconhecer uma regressão.** Um teste que passava e passou a falhar; uma coluna em posição diferente; contadores da tela que não batem com a planilha; a mensagem "A conferência do Excel gravado encontrou diferenças".
8. **Voltar a uma versão estável.** O projeto está sob Git: `git log --oneline` mostra os commits; `git revert <commit>` desfaz uma mudança criando um novo commit (sem apagar histórico). No computador do usuário, a versão estável é o `.zip` anterior — guardar uma cópia antes de substituir a pasta.

### 21.2 Exemplo hipotético: mover a coluna "Tipo Material"

::: proposta
**Cenário hipotético** (não implementado): o usuário pede que "Tipo Material" fique depois de "Fornecedor SAP" na aba Dados.
:::

**O que existe hoje** <span class="confirmado">CONFIRMADO</span>: a ordem das colunas está em `COLUNAS_DADOS` (`exportacao.py`, linhas 40–41), e a linha de cada item é montada em `_linhas_dados` (linhas 206–207) na mesma ordem. A gravação aplica formato de texto às colunas `(1, 2, 3, 4, 6, 7, 8)` e quebra de linha às colunas `(6, 7)` pelo **número** da coluna (linhas 307–308), e as larguras são uma lista posicional `[18, 22, 34, 16, 12, 55, 55, 13]` (linha 301).

**Onde a mudança se propaga:**

| Local | O que precisaria mudar | Risco se esquecer |
|---|---|---|
| `COLUNAS_DADOS` | Ordem dos nomes | Cabeçalho errado |
| `_linhas_dados` (duas listas) | Ordem dos valores | Valor sob o cabeçalho errado |
| Larguras (linha 301) | Ordem das larguras | Só aparência |
| Formatos por número (linhas 307–308) | Números das colunas de texto e de quebra | A coluna de quantidade poderia virar texto, e o Excel deixaria de somá-la |
| `_COL_OBS` | Calculado pelo nome, não muda | — |
| `verificar_planilha` | Usa as mesmas funções, acompanha sozinho | — |
| Testes | `test_estrutura_completa_do_excel` e `test_coluna_tipo_aba_materiais_e_tabela_oculta` conferem cabeçalhos | O teste falharia — e é bom que falhe, porque avisa |
| Usuário | Fórmulas como PROCV que apontam para colunas por letra | Fórmulas do usuário quebrariam sem aviso |

::: atencao
A última linha da tabela não é detectável por teste: é um efeito **fora** do programa. Mudanças na estrutura da planilha devem ser comunicadas a quem usa o arquivo.
:::
