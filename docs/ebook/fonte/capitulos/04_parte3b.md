## 14. Como o agente interpreta e organiza os dados {#cap14}

Extrair e interpretar não são a mesma coisa. Extrair é **obter** os registros do BTP; interpretar é **entender** o que eles dizem — converter textos em números, reconhecer campos, comparar leituras e decidir o que pode ser afirmado. Este capítulo trata da segunda parte.

### 14.1 O caminho de um valor

@@figura transformacao

| Etapa | Dados recebidos | Transformação | Resultado | Onde |
|---|---|---|---|---|
| Leitura da resposta | Texto JSON devolvido pelo BTP | Interpretação do formato OData (V2 ou V4) e paginação | Lista de registros (dicionários) | `odata.extrair_pagina`, `ClienteBTP._get` |
| Reconhecimento dos campos | Registro com nomes técnicos | Leitura pelos nomes gravados em `config/api.json` | Valores com nomes do agente | `ClienteBTP.ocorrencia`, `odata.obter_campo` |
| Conversão | Valores brutos (texto, números) | Identificadores → texto; quantidade → número exato | `material`, `fornecedor`, `quantidade_faltante`... | `odata.como_texto`, `odata.como_quantidade` |
| Comparação | Leituras da mesma WO | Comparação por chave do item, campo a campo | Valores finais + notas + estado | `auditoria.consolidar` |
| Organização | Linhas finais de todas as WOs | Remoção de cópias, filtro de mensagem, preenchimento com "-" | Linhas da planilha | `exportacao._linhas_dados` |

### 14.2 Lendo a resposta OData

@@codigo agente_btp/odata.py extrair_pagina

A mesma função entende os dois formatos do padrão OData: a versão 2 envolve os registros em `{"d": {"results": [...]}}` e indica a próxima página em `__next`; a versão 4 usa `{"value": [...]}` e `@odata.nextLink`. Se o servidor devolver um objeto `error`, a função levanta `ErroConsulta` com a mensagem do próprio serviço. Se o texto nem for JSON, também.

A paginação é feita por `ClienteBTP._get`, que segue o link da próxima página até no máximo 50 páginas (`LIMITE_PAGINAS`). Um resultado maior que isso é interrompido com erro, em vez de seguir indefinidamente.

### 14.3 Identificadores como texto, quantidades como números exatos

@@codigo agente_btp/odata.py como_texto

@@codigo agente_btp/odata.py como_quantidade

::: conceito
**Por que isso importa.** Um material `000000000022026169` lido como número vira `22026169` — os zeros somem e o código deixa de ser idêntico ao do BTP. Por isso todo identificador é **texto** (`como_texto`). Já a quantidade chega do serviço como texto em notação variada (`"2.000"`, `"1e+0"`). Converter para número de ponto flutuante (o tipo `float` comum) pode introduzir resíduos como `1.9999999`; por isso `como_quantidade` usa `Decimal`, um tipo numérico **exato**, e devolve um inteiro quando não há parte fracionária (`"1e+0"` vira `1`). Um valor que não é número vira `None` (vazio) — o agente não "chuta".
:::

### 14.4 A estrutura de dados central

Todos os módulos trocam informações por meio de algumas estruturas definidas em `agente_btp/modelos.py`. As duas classificações mais importantes são:

@@codigo agente_btp/modelos.py Status

@@codigo agente_btp/modelos.py Auditoria

Elas respondem a perguntas **diferentes**, e é importante não misturá-las:

| Pergunta | Classificação | Exemplos |
|---|---|---|
| O que o Monitor diz sobre esta WO? | `Status` (situação operacional) | Com itens faltantes; Sem itens; Não consta no Monitor; Formato inválido |
| O quanto podemos confiar no que foi lido? | `Auditoria` (fidelidade) | Validado; Divergente; Incompleto; Não concluído |

Uma WO pode ser "Não consta no Monitor" e "Validado" ao mesmo tempo: significa que **todas** as leituras confirmaram que ela não tem alerta. Já uma WO "Com itens faltantes" e "Divergente" teve itens, mas algum valor mudou entre as leituras.

O resultado de cada item é uma `LinhaMensagem`:

@@codigo agente_btp/modelos.py LinhaMensagem

### 14.5 A auditoria: comparar para poder afirmar

A ideia da auditoria é simples: **ler mais de uma vez e só afirmar o que se repetiu**. Se a quantidade faltante de um material foi lida como 2 na extração e como 2 nas duas auditorias, o agente afirma "2" com segurança. Se foi lida como 1 e depois 4, o agente **não escolhe** — nem pela maioria, nem pela última leitura —, a menos que o próprio BTP explique a mudança.

@@figura auditoria

A decisão é tomada pela função `consolidar`. O coração dela é o laço por item:

@@codigo agente_btp/auditoria.py consolidar 99-153

::: leitura
- **Linhas 99–104** — para cada chave de item (de todas as leituras), reúne as observações; a primeira leitura em que o item aparece é a **referência**.
- **Linha 106** — conta quantas auditorias realmente conferiram este item (uma releitura por chave que não conseguiu reler o item não conta).
- **Linhas 108–113** — **item que sumiu ou apareceu** entre leituras: divergência.
- **Linhas 114–127** — **campo a campo**: se todas as leituras trazem o mesmo valor, ele é o valor final; se não, é divergência, e `_resolver` tenta explicá-la pelo registro de alteração do BTP. Sem explicação, o valor final fica **vazio** e a nota diz "Não resolvida: revisar no BTP".
- **Linhas 128–133** — **campo vazio**: se algum dos cinco campos não veio do BTP, o item é incompleto.
- **Linhas 134–140** — **quantidade ambígua**: se o registro tem outros campos de quantidade e o nome do campo usado não indica claramente "faltante", o item é incompleto — o agente se recusa a escolher.
- **Linhas 141–150** — a ordem de gravidade: divergente vence incompleto, que vence não concluído; só sem nenhum desses o item é **validado**.
:::

### 14.6 Quando o BTP explica a mudança

@@codigo agente_btp/auditoria.py _resolver

Uma divergência pode ter causa legítima: o item foi alterado no BTP durante a análise. `_resolver` só aceita essa explicação com evidência rigorosa: precisa haver um campo de data de alteração **real** (não um texto qualquer) em pelo menos três leituras, as datas não podem voltar no tempo e pelo menos **duas leituras feitas depois da última alteração** precisam concordar. Nesse caso, o valor atual é aceito e a planilha registra: "mudou no BTP durante a consulta (...); vale o valor atual". Os testes `test_divergencia_resolvida_pelo_registro_de_alteracao_do_btp`, `test_campo_que_nao_e_data_nao_serve_de_evidencia` e `test_data_que_volta_no_tempo_nao_resolve` verificam cada uma dessas condições.

### 14.7 Leituras extras em caso de divergência

Quando a primeira consolidação aponta divergência e a preferência "Diante de divergência" está em "Reconsultar e sinalizar" (padrão), `_concluir` (em `processador.py`) faz até `consultas_extras_divergencia` leituras adicionais (padrão 3) — sempre **individuais**, mesmo que a WO tenha sido lida em lote, para usar um caminho diferente. Depois, consolida de novo com todas as leituras. As leituras extras **nunca aprovam sozinhas**: elas entram na mesma comparação rigorosa. O teste `test_r4_divergencia_escalona_para_5_leituras_e_nunca_valida` verifica que uma quantidade que muda a cada leitura chega a 5 leituras e continua divergente.

### 14.8 A situação operacional da WO

Depois da auditoria, `_concluir` decide a situação da WO olhando **todas** as leituras, não só a primeira (que pode ter vindo vazia): se há itens, "Com itens faltantes"; se há linhas na lista mas nenhum item, "Sem itens"; se não há linha nenhuma, "Não consta no Monitor".

### 14.9 Do resultado ao estado gravado

A base local guarda um estado simplificado por WO, usado pela tela, pela retomada e pela planilha:

@@codigo agente_btp/loja.py estado_da_wo

@@figura estados_wo

Toda WO não validada recebe também um **motivo** da taxonomia do diagnóstico (`loja.motivo_da_wo`): por exemplo, `campo_ausente:fornecedor`, `quantidade_ambigua`, `divergencia_persistente`, `timeout_wo`. O teste `test_toda_wo_nao_validada_tem_motivo_no_diagnostico` garante que nenhuma WO fica sem explicação.

### 14.10 Gravação transacional

@@codigo agente_btp/loja.py Loja.concluir

::: leitura
- **Linha 330** — o resultado completo da WO é serializado em JSON e comprimido (`zlib`) antes de ir para a base: a planilha pode ser gerada depois sem consultar o BTP de novo.
- **Linhas 333–335** — só grava se a WO estiver **em andamento**. Uma WO já concluída nunca é sobrescrita por engano (por exemplo, por um trabalhador atrasado).
- **Linhas 336–343** — o resultado e a linha do registro de eventos são gravados **na mesma transação**: ou os dois entram, ou nenhum. Por isso contadores, registro e planilha nunca discordam (teste `test_r6_contadores_registro_e_planilha_concordam`).
:::

Quando a sessão cai, as WOs em andamento voltam a "pendente", nunca a "falha":

@@codigo agente_btp/loja.py Loja.devolver

### 14.11 Itens repetidos e itens que não vão para a planilha

Antes de virar linha, os itens de cada WO passam por duas organizações:

1. **Remoção de cópias idênticas** (`exportacao.itens_unicos`): o mesmo item que o BTP devolve mais de uma vez com dados idênticos vira uma linha só, com a observação "O BTP traz este item N vezes nesta WO". Nada é somado nem escolhido — as cópias são idênticas em tudo que vai para a planilha.
2. **Filtro da mensagem** (`exportacao.entra_na_planilha`): só itens cuja mensagem do item seja a configurada (padrão "Estoque indisponível") viram linha; os demais vão para a Observação da WO. O Capítulo 15 detalha.

@@codigo agente_btp/exportacao.py itens_unicos

## 15. Como o agente produz o resultado final {#cap15}

### 15.1 A ideia

O resultado é uma planilha Excel que precisa ser, ao mesmo tempo, **fácil de usar** (filtrar, somar, ler) e **fiel** (cada valor igual ao que o BTP mostrava, sem zeros perdidos nem fórmulas acidentais). O agente atinge os dois objetivos gerando a planilha **sempre a partir da base local** e, logo depois, **reabrindo o arquivo para conferi-lo**.

@@figura geracao_planilha

### 15.2 Quando a planilha é gerada

| Momento | Quem gera | Arquivo |
|---|---|---|
| Fim da análise | `Agente._rodar` → `_planilha` | `Análise N - X WOs.xlsx` |
| Durante a análise (botão "Baixar planilha parcial") | `planilha_parcial` | `Análise N - X WOs (parcial).xlsx` |
| Histórico → Baixar relatório | `regerar_relatorio` | Substitui o arquivo da análise, com as regras atuais |
| Retomada concluída | `_planilha` | Atualiza o mesmo arquivo da análise |

@@codigo agente_btp/agente.py Agente._planilha

::: leitura
- **Linhas 901–906** — o nome do arquivo: parcial, o mesmo da análise (numa retomada) ou um novo, gerado por `nome_analise` (que nunca sobrescreve um arquivo existente: acrescenta "(2)", "(3)"...).
- **Linha 908** — carrega a tabela de valores vigente (Tipo Material e preços).
- **Linhas 909–913** — gera e, em seguida, **confere** o arquivo.
- **Linhas 916–919** — se a conferência encontrar diferenças, o erro é devolvido e aparece na tela e no histórico ("Erro na planilha").
- **Linhas 921–925** — se a geração falhar com exceção, ela é registrada no diagnóstico com o traceback; a análise **não se perde**, porque está na base e o relatório pode ser gerado de novo.
:::

### 15.3 A estrutura do arquivo

@@figura abas_excel

| Aba | Visível | Conteúdo |
|---|---|---|
| **Dados** | Sim | Uma linha por item faltante: WO, Material SAP, Tipo Material, Fornecedor SAP, Quantidade faltante, Mensagem, Observação, Status. WO sem item na planilha aparece numa linha com "-" e a explicação na Observação |
| **Materiais** | Sim | Uma linha por Material SAP, somando todas as WOs: Tipo Material, Quantidade total, Valor total (R$), Valor unitário (R$), Observação; e a linha **Total** |
| **Análise** | Sim | Números da análise, materiais mais recorrentes (com as WOs), mensagens e a situação de cada WO |
| **Rastreio** | Não (oculta) | Cada leitura feita ao BTP: WO, material, item, rodada, tipo, horário, valores e comparação com a 1ª leitura |
| **Tabela de valores** | Não (oculta) | A tabela de preços importada, com o material em 18 dígitos (como no BTP), para uso em fórmulas como PROCV |

### 15.4 As regras de preenchimento da aba Dados

@@codigo agente_btp/exportacao.py _linhas_dados

::: leitura
- **Linha 200** — separa os itens da WO em "vão para a planilha" e "outros" (`_itens_da_wo`), já sem as cópias idênticas.
- **Linha 201** — os "outros" viram uma nota: "Outros itens no BTP, fora da planilha (mensagem diferente de "Estoque indisponível"): <material> (qtd N): <mensagem>; ...".
- **Linhas 202–207** — cada item vira uma linha. A Observação junta, nesta ordem: o que precisa ser conferido no BTP (`observacao_item`), a nota de repetição e — só na primeira linha da WO — a nota dos outros itens. O segundo valor da tupla (`bool(conferir)`) diz se a célula de Observação deve ser destacada em amarelo: só quando há algo a conferir.
- **Linhas 208–218** — WO sem nenhum item na planilha: uma linha com "-" nas colunas de item e a explicação (sem alerta no Monitor; nenhum item com a mensagem do filtro; falha de consulta com o motivo; pendente...).
- `_preencher` troca todo valor vazio por "-": nenhuma célula da aba Dados fica em branco.
:::

### 15.5 O filtro "Estoque indisponível"

@@codigo agente_btp/exportacao.py entra_na_planilha

@@codigo agente_btp/exportacao.py mensagem_do_item

::: nobtp
A comparação é feita com a **mensagem do item** (a da janela Mensagens), mesmo quando a planilha mostra a mensagem do alerta ou as duas. A normalização (`_normalizar`) remove acentos, maiúsculas, espaços extras e pontuação final — "ESTOQUE INDISPONIVEL " entra; "Estoque insuficiente" não. A mensagem configurada vem da preferência `mensagem_planilha` (Configurações → Execução); vazia, todos os itens entram. Para análises antigas, que não guardavam a mensagem do item separadamente, `mensagem_do_item` recupera o trecho anterior a " — Alerta: " (formato usado quando a opção era "as duas").
:::

::: exemplo
Medição feita sobre as linhas da planilha real de 13.623 WOs enviada pelo usuário (sem as cópias idênticas): 15.219 itens no total, dos quais **13.873** com "Estoque indisponível" ficam na planilha e **1.346** vão para a Observação. Entre as outras mensagens, as mais comuns eram "Os dados de centro do material ... estão bloqueados ..." e "Estoque insuficiente". Das 11.443 WOs com itens, 11.163 tinham só "Estoque indisponível", 37 eram mistas e 243 só tinham outras mensagens (essas viram uma linha "-" com a explicação). Fonte: `docs/relatorio-correcao-raiz.md`, seção 15.
:::

### 15.6 A aba Materiais e a tabela de valores

@@codigo agente_btp/exportacao.py linhas_materiais

::: leitura
- **Linhas 249–252** — agrupa os itens pelo **número** do material (`chave_material`: `000000000022056365` e `22056365` são o mesmo material).
- **Linhas 255–256** — a quantidade total soma só os itens **validados** e com quantidade. Item não conferido fica fora da soma, e a Observação avisa ("1 item(ns) não conferido(s) fora da soma: conferir no BTP").
- **Linhas 257–259** — o valor unitário vem da tabela importada; o valor total é quantidade × unitário, calculado com `Decimal` (exato).
- **Linhas 261–266** — material fora da tabela, ou sem preço, ou tabela não importada: "-" e o motivo.
- **Linha 274** — ordena pelo maior valor total; materiais sem valor ficam no fim.
- **Linhas 275–278** — a linha Total soma os valores conhecidos e informa quantos materiais ficaram fora.
:::

A tabela de valores é importada em Configurações (rota `POST /api/materiais/tabela`). O módulo `materiais.py` procura no cabeçalho as colunas **Material**, **Texto breve material** (vira o Tipo Material) e **Unitário**, recusa a importação se o mesmo material aparecer com tipo ou valor diferentes e guarda o resultado em `config/tabela_materiais.json`, gravado de forma atômica (arquivo temporário + substituição).

### 15.7 Gravar sem interpretar

@@codigo agente_btp/exportacao.py _celula

Duas proteções em poucas linhas:

1. Um texto que começa com `=` seria interpretado pelo Excel como **fórmula**. A linha 507 detecta esse caso e força o tipo "texto". O teste `test_texto_com_igual_e_controle_nao_vira_formula_nem_quebra` verifica com a mensagem `"=> Saldo insuficiente (-2)"`.
2. Caracteres de controle que o formato `.xlsx` não aceita (por exemplo, uma tabulação vertical vinda do BTP) derrubariam a gravação. `_limpar` os remove.

Além disso, as colunas de identificadores recebem o formato de célula `@` (texto), para que o Excel nunca converta o material num número.

### 15.8 Escrita contínua para volumes grandes

A planilha é gravada no modo *write-only* do openpyxl: cada linha vai direto para o arquivo, sem montar a planilha inteira na memória. A seção 6 de `docs/relatorio-correcao-raiz.md` mediu o efeito com 13.623 WOs: pico de memória de 1.487 MB para 387 MB, e a geração deixou de ter custo quadrático. O teste `test_excel_de_lote_grande_nao_e_quadratico` protege contra a volta do problema.

### 15.9 A conferência depois de gravar

@@codigo agente_btp/exportacao.py verificar_planilha

A conferência recalcula, a partir dos mesmos resultados, o que **deveria** estar no arquivo e compara com o que **está** gravado, célula a célula, nas abas Dados, Materiais e Tabela de valores. Também acusa qualquer célula gravada como fórmula. O arquivo é aberto em modo somente leitura, que, segundo o comentário das linhas 452–453, reduziu a memória da conferência de cerca de 840 MB para 3 MB com 13 mil WOs. O teste `test_verificacao_detecta_excel_adulterado` altera uma célula do arquivo e confirma que a conferência acusa a diferença.

### 15.10 Como o usuário sabe que terminou e onde está o arquivo

A tela mostra o selo **Concluída** (ou "Concluída com ressalvas", "Concluída com divergências", "Interrompida"), a barra cheia — verde sem ressalvas, âmbar com ressalvas — e o botão **Baixar relatório auditado (.xlsx)**. O arquivo fica na pasta `saidas/` e é servido pela rota `GET /api/download/<nome>`, que só aceita arquivos de dentro dessa pasta (função `caminho_saida`, que recusa nomes com `../`).

### 15.11 O histórico

Cada análise é registrada em `historico/historico.json` desde o início (como "Em andamento") e atualizada ao final. A lista de WOs, que pode ter dezenas de milhares de itens, fica num arquivo próprio da análise (`historico/analises/<id>.json`), para que o histórico continue leve. A gravação é atômica e o histórico guarda até 300 análises (`LIMITE` em `historico.py`). O número da análise vem de `historico/contador.txt` e nunca se repete, mesmo depois de apagar o histórico.

## 16. O papel de cada função e módulo importante {#cap16}

Este capítulo reúne, em fichas, as funções que sustentam o funcionamento do agente. Cada ficha responde às mesmas perguntas, para facilitar a comparação. Os trechos de código correspondentes foram mostrados nos capítulos anteriores, indicados em cada ficha.

### 16.1 `Agente.enviar` e `Agente._laco` — a fila de comandos

| Pergunta | Resposta |
|---|---|
| Onde | `agente_btp/agente.py`, classe `Agente` (Cap. 10) |
| Responsabilidade | Receber comandos do servidor e executá-los um por vez, numa thread própria |
| Por que existe | Para o servidor responder imediatamente à tela e para nunca haver duas análises ao mesmo tempo |
| Como é acionada | Rotas como `/api/executar` chamam `enviar("executar", ...)`; `_laco` roda desde a inicialização |
| Recebe | Nome do comando e argumentos (lista de WOs, `run_id`...) |
| Produz | Atualizações no estado (`_estado`) que a tela lê |
| Chama | `_cmd_executar`, `_cmd_retomar`, `_cmd_refazer`, `_cmd_preparar`, `_cmd_raiox`, `_cmd_sondar` |
| Erros | Exceção num comando é registrada com traceback e mostrada na tela; o laço continua |
| Como verificar | Rotas retornam 409 ("já está executando") num segundo pedido; testes de `test_execucao.py` |
| Impacto de alterar | Afeta todos os comandos; mudar o momento em que `ocupado` é marcado reabre a corrida de cliques duplos |

### 16.2 `Agente._cmd_executar` e `Agente._rodar` — uma análise

| Pergunta | Resposta |
|---|---|
| Onde | `agente_btp/agente.py` |
| Responsabilidade | Garantir a fonte, confirmar a amostra, criar a análise na base, executar o motor, gerar a planilha e registrar o histórico |
| Por que existe | É o roteiro de uma análise do começo ao fim |
| Como é acionada | Comando "executar" (e "retomar"/"refazer", que reutilizam `_rodar`) |
| Recebe | Lista de WOs e descrição da origem ("250 WO(s) coladas + planilha lista.xlsx") |
| Usa | Preferências e fonte **gravadas na análise** — uma retomada usa as mesmas regras do início |
| Produz | Análise na base, planilha, registro no histórico, avisos na tela |
| Chama | `_garantir_preparo`, `_confirmar_fonte`, `Loja.criar`, `Motor.executar`, `_planilha`, `_registrar_historico` |
| Condições | Aba desconectada impede o início; fonte não confirmada pede a amostra |
| Erros | HTTP 404 em massa refaz a fonte; ≥ 90% das WOs sem itens refaz a fonte ("ligação errada") |
| Como verificar | `test_r2_retomar_so_refaz_o_que_falta_e_e_idempotente`; `test_lote_quase_todo_sem_registros_refaz_a_configuracao` |
| Impacto de alterar | É o ponto que costura tudo: mudanças aqui afetam tela, base, planilha e histórico |

### 16.3 `Motor.executar` — paralelismo e sessão

| Pergunta | Resposta |
|---|---|
| Onde | `agente_btp/motor.py` (Caps. 16 e 26) |
| Responsabilidade | Distribuir as WOs pendentes em grupos e trabalhadores, verificar a sessão e pausar/retomar |
| Por que existe | Isolar a lógica de execução concorrente e de sessão da lógica de cada WO |
| Recebe | Base, `run_id`, uma fábrica de clientes, preferências e funções de sonda e de conexão |
| Produz | Situação final: `concluida` ou `interrompida` |
| Chama | `processar_grupo`, `processar_wo_seguro`, `Loja.concluir`, `Loja.devolver`, a sonda |
| Condições | `concorrencia` (padrão 4) trabalhadores; `tamanho_lote` (padrão 20) WOs por grupo; sonda a cada 60 s ou 100 WOs |
| Erros | Sessão caída pausa; exceção inesperada vira falha do grupo com traceback; tempo máximo da análise interrompe |
| Como verificar | `test_r1_queda_no_meio_com_paralelismo_e_lote_nunca_vira_falha`; `rodar_sessao.py` (navegador) |
| Impacto de alterar | Afeta desempenho e a garantia de não perder WOs |

@@figura motor_laco

### 16.4 `processar_grupo` e `processar_wo` — uma WO do início ao fim

| Pergunta | Resposta |
|---|---|
| Onde | `agente_btp/processador.py` (Cap. 13) |
| Responsabilidade | Extração inicial, auditorias, leituras extras e situação da WO |
| Recebe | Cliente, WOs, preferências |
| Produz | `ResultadoWO` por WO |
| Chama | `ClienteBTP.ler_lote`, `ler_wo`, `reler`, `reler_lote`; `auditoria.consolidar` |
| Condições | Lote só com mais de uma WO válida e consulta repartível; verificação de equivalência |
| Erros | Falha de consulta vira resultado da WO (`processar_wo_seguro`); `SessaoExpirada` sobe para o motor |
| Como verificar | `test_r4_equivalencia_lote_paralelo_x_individual_serial_com_releitura_por_chave`; `test_tempo_maximo_da_wo_interrompe_as_auditorias` |
| Impacto de alterar | Afeta fidelidade e número de consultas |

### 16.5 `ClienteBTP` — as consultas

| Pergunta | Resposta |
|---|---|
| Onde | `agente_btp/cliente.py` (Caps. 11, 13) |
| Responsabilidade | Montar URLs, chamar o transporte (a ponte), classificar respostas, paginar, repartir lotes, reler por chave |
| Recebe | `ApiConfig`, função de transporte, tempos máximos, ritmo, cache |
| Produz | `Leitura` (itens por chave) ou exceções classificadas |
| Chama | `ponte.get` (transporte), `odata.*`, `classificar` |
| Erros | `SessaoExpirada`, `FalhaComunicacao`, `ErroConsulta`, `TempoEsgotado`, `LoteInconsistente` |
| Como verificar | `test_paginacao_segue_next`; `test_r3_deteccao_de_sessao_morta`; `test_linha_de_outra_wo_e_descartada_e_avisada` |
| Impacto de alterar | É a fronteira com o BTP: um erro aqui afeta todas as WOs |

### 16.6 `classificar` — o que significa cada resposta

| Pergunta | Resposta |
|---|---|
| Onde | `agente_btp/cliente.py` (Cap. 26) |
| Responsabilidade | Decidir se uma resposta é dado válido, sessão expirada, falha passageira ou erro |
| Recebe | `Resposta` (situação, tipo, corpo, redirecionado) e a URL |
| Produz | O corpo, ou uma exceção com motivo e evidência |
| Condições | Página HTML de login com situação 200 também conta como sessão expirada |
| Como verificar | `test_redirecionamento_e_sessao_expirada`; `test_r3_deteccao_de_sessao_morta` |
| Impacto de alterar | Classificar errado pode pausar sem motivo ou marcar falha onde era sessão |

### 16.7 `PonteAba` — a fila com a aba

| Pergunta | Resposta |
|---|---|
| Onde | `agente_btp/ponte.py` (Cap. 11) |
| Responsabilidade | Entregar consultas à aba e respostas a quem pediu; saber se a aba está conectada |
| Recebe | Consultas (`get`) e ciclos da aba (`ciclo`, `proxima_tarefa`, `entregar_resposta`) |
| Produz | `Resposta` para o cliente; tarefas para a aba |
| Erros | Aba que some: as tarefas entregues voltam para o início da fila (`_devolver_entregues`) |
| Como verificar | `test_ponte_com_varios_trabalhadores_na_aba_entrega_cada_resposta_a_quem_pediu`; `test_tarefa_entregue_a_aba_que_sumiu_volta_para_a_fila`; `test_aba_ocupada_com_consulta_lenta_nao_e_dada_como_desconectada` |
| Impacto de alterar | Afeta toda a comunicação; um erro de sincronização pode entregar resposta a quem não pediu |

### 16.8 `auditoria.consolidar` — a decisão

| Pergunta | Resposta |
|---|---|
| Onde | `agente_btp/auditoria.py` (Cap. 14) |
| Responsabilidade | Comparar leituras e decidir valores finais e estado |
| Recebe | Leituras de uma WO e o número de auditorias previstas |
| Produz | Linhas finais, estado da WO, observação, registros para a aba Rastreio |
| Condições | Divergência só é resolvida com evidência de alteração do BTP |
| Como verificar | `test_divergencia_de_quantidade_nao_e_aprovada_nem_por_maioria`; `test_quantidade_ambigua_nao_e_validada`; `test_item_nao_relido_nao_fica_validado` |
| Impacto de alterar | É a garantia de fidelidade do projeto; qualquer afrouxamento deixa passar valores errados |

### 16.9 `Loja` — a memória permanente

| Pergunta | Resposta |
|---|---|
| Onde | `agente_btp/loja.py` (Caps. 14, 26) |
| Responsabilidade | Guardar análises, WOs, eventos e problemas; responder contadores e retomada |
| Produz | Contadores da tela, lista do que retomar, resultados para a planilha, linha do tempo do diagnóstico |
| Erros | Transação falha é desfeita (ROLLBACK) e a exceção sobe |
| Como verificar | `test_programa_fechado_no_meio_retoma_de_onde_parou`; `test_serializacao_ida_e_volta_mantem_a_planilha` |
| Impacto de alterar | Mudar o esquema exige cuidado com bases já existentes nos computadores dos usuários (não há migração de esquema no código) |

### 16.10 `gerar_planilha` e `verificar_planilha` — o produto

| Pergunta | Resposta |
|---|---|
| Onde | `agente_btp/exportacao.py` (Cap. 15) |
| Responsabilidade | Gravar o Excel e conferi-lo |
| Recebe | Resultados (da base), destino, preferências, tabela de valores |
| Produz | Arquivo `.xlsx`; lista de diferenças (vazia = ok) |
| Condições | Filtro de mensagem; exportação de não validados (preferência `exportar_incompletos`) |
| Como verificar | `test_estrutura_completa_do_excel`; `test_planilha_so_com_estoque_indisponivel_e_outros_na_observacao`; `test_coluna_tipo_aba_materiais_e_tabela_oculta` |
| Impacto de alterar | Toda mudança de coluna exige atualizar `linhas_dados`, `verificar_planilha` e os testes juntos |

### 16.11 `Agente._garantir_preparo` e `_confirmar_fonte` — a fonte certa

| Pergunta | Resposta |
|---|---|
| Onde | `agente_btp/agente.py` (Cap. 12) |
| Responsabilidade | Descobrir a fonte e obter a confirmação do usuário |
| Recebe | WOs da análise (as 5 primeiras válidas servem de amostra) |
| Produz | `config/api.json` confirmado, ou falha com diagnóstico |
| Chama | `receita_monitor`, `Explorador`, `candidatos_pela_aba`, `sondar`, `analisar`, `_validar` |
| Como verificar | `test_isca_do_btp_real_nao_e_escolhida`; `test_fonte_nao_confirmada_e_trocada_pelo_caminho_confirmado_antes_da_amostra`; `test_lote_so_comeca_depois_da_conferencia_e_recusa_troca_a_fonte` |
| Impacto de alterar | Uma fonte errada invalida a análise inteira; por isso existe a conferência humana |

## 17. A relação entre os componentes {#cap17}

### 17.1 Quem depende de quem

A tabela foi gerada a partir das instruções `from .x import ...` de cada módulo (as importações internas do pacote):

| Módulo | Usa (importa) |
|---|---|
| `__main__` | agente, servidor |
| `servidor` | agente, config, entrada, historico, materiais, ponte, preferencias |
| `agente` | analisador, autoconfig, cliente, config, diagnostico, entrada, exploracao, exportacao, historico, loja, materiais, modelos, motor, odata, ponte, preferencias, raiox, sondagem |
| `motor` | cliente, diagnostico, loja, modelos, preferencias, processador |
| `processador` | auditoria, cliente, entrada, modelos, preferencias |
| `cliente` | campos, config, diagnostico, modelos, odata |
| `ponte` | cliente (só a classe `Resposta`), diagnostico, modelos |
| `auditoria` | campos, modelos, odata |
| `exportacao` | loja, materiais, modelos |
| `loja` | diagnostico, modelos |
| `odata` | modelos |
| `historico`, `materiais`, `preferencias` | config |
| `config`, `modelos`, `diagnostico`, `campos`, `entrada` | (nenhum módulo interno) |

### 17.2 Leitura da tabela

- **Quem inicia operações:** `__main__` (o processo), `servidor` (cada pedido HTTP) e `agente` (cada comando).
- **Quem fornece dados:** `cliente` (do BTP, via `ponte`), `entrada` (WOs do usuário), `materiais` (preços), `loja` (resultados gravados).
- **Quem transforma:** `odata` (formato), `auditoria` (decisão), `exportacao` (planilha).
- **Quem produz resultados:** `exportacao` (Excel), `historico` (lista de análises), `diagnostico` (explicações).
- **Componentes compartilhados por quase todos:** `modelos` (estruturas e exceções) e `diagnostico` (motivos). Alterar o nome de um motivo ou de um campo de `ResultadoWO` afeta muitos módulos e a base de dados.
- **Mais fáceis de substituir:** `historico` (um arquivo JSON) e `materiais` (uma tabela de consulta); a interface deles é pequena.
- **Mais delicados:** `cliente` e `auditoria` — o primeiro é a fronteira com o BTP; o segundo, a garantia de fidelidade.

### 17.3 O caminho de uma informação: a quantidade faltante de um material

::: exemplo
Dados fictícios. Suponha a WO `12345|678901234` com um item do material `000000000022056366`, quantidade faltante 1, mensagem "Estoque indisponível".

1. **BTP → aba.** O serviço `UltimoRetornoView` devolve o registro com `"quantidade": "1e+0"` (formato observado no serviço real, segundo o comentário de `receita_monitor`).
2. **Aba → ponte → cliente.** O script devolve o corpo como texto; a ponte o entrega ao cliente como `Resposta`.
3. **Cliente.** `classificar` aceita (200, JSON); `extrair_pagina` devolve o registro; `ocorrencia` lê `quantidade` (nome gravado na fonte) e `como_quantidade("1e+0")` devolve `1`.
4. **Processador.** O mesmo valor é lido de novo nas auditorias.
5. **Auditoria.** Três leituras com `1` → valor final `1`, estado **Validado**.
6. **Motor → loja.** O resultado é serializado e gravado na transação da WO.
7. **Exportação.** A linha entra na aba Dados (mensagem do item = "Estoque indisponível"); na aba Materiais, `1` soma-se às quantidades validadas do mesmo material em outras WOs e é multiplicado pelo valor unitário da tabela.
8. **Conferência.** `verificar_planilha` recalcula a linha e confirma que a célula gravada contém `1`.
:::

### 17.4 Onde uma mudança se propaga

| Se mudar... | Revise também | Por quê |
|---|---|---|
| Um campo de `LinhaMensagem` ou `ResultadoWO` | `loja.serializar`/`desserializar`, `exportacao`, testes | O resultado é gravado na base e lido para a planilha |
| Um motivo da taxonomia | `loja.motivo_da_wo`, `index.html` (rótulos), testes | Motivos aparecem na base, no diagnóstico e na tela |
| As colunas da aba Dados | `linhas_dados`, `verificar_planilha`, `COLUNAS_DADOS`, testes | A conferência compara coluna a coluna |
| O formato das tarefas da ponte | `ponte.py`, `agente_btp.js` e a cópia em `extensao/` | Os dois lados precisam falar o mesmo formato |
| A receita confirmada | `autoconfig.receita_monitor`, `VERSAO_CONFIG`, testes de preparo | Fontes antigas precisam ser descartadas |
| Uma preferência | `preferencias.py` (campo, faixa), `index.html` (`CONFIG`), testes | A tela gera o formulário a partir de `CONFIG` |
