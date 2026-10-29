# Parte V — Configuração, execução e testes {#parte5}

Esta parte é o manual de operação. Ela documenta cada configuração, traz o procedimento numerado de uma análise do começo ao fim, apresenta os testes existentes (com os resultados obtidos na execução feita para este livro) e, por fim, explica como distinguir uma execução que **parece** concluída de uma execução **efetivamente válida**.

## 22. Configuração do ambiente {#cap22}

### 22.1 Onde ficam as configurações

O agente tem **quatro** arquivos de configuração, todos na pasta `config/` e todos **fora do controle de versão** (listados no `.gitignore`), porque pertencem a cada computador:

| Arquivo | Quem grava | Quem lê | Conteúdo |
|---|---|---|---|
| `config/api.json` | O próprio agente, na preparação (`Agente._gravar_config` → `config.salvar`) | `config.carregar`, usado pelo `ClienteBTP` | A fonte de dados confirmada: modelos de URL e nomes de campos |
| `config/preferencias.json` | Tela Configurações (`POST /api/configuracoes` → `preferencias.salvar`) | `preferencias.carregar` | Os parâmetros operacionais (seção 22.3) |
| `config/tabela_materiais.json` | Importação da tabela de valores (`POST /api/materiais/tabela` → `materiais.salvar`) | `materiais.carregar` | Material, Tipo Material e valor unitário |
| `config/fontes_recusadas.json` | O agente, quando o usuário responde "outra" na conferência da amostra | `Agente._rejeitada` | Assinaturas de fontes que o usuário recusou |

O repositório traz apenas `config/api.exemplo.json`, um **modelo ilustrativo** antigo (o próprio arquivo diz isso no campo `_comentario`). Ele **não** corresponde à fonte confirmada atual — esta foi descrita no Capítulo 12. <span class="confirmado">CONFIRMADO</span>

::: atencao
Nenhum desses arquivos guarda senha, cookie ou *token*. A docstring de `preferencias.py` (linhas 1–5) declara isso explicitamente, e a inspeção dos campos de `ApiConfig` (`config.py`, linhas 34–53) confirma: só há endereços-modelo e nomes de campos.
:::

### 22.2 A fonte de dados (`config/api.json`)

| Campo | O que controla | Formato | Se ausente ou inválido |
|---|---|---|---|
| `url_lista` | Consulta das linhas de alerta de uma WO | Texto com o marcador `{wo}` | `a_partir_de_dict` recusa: "'url_lista' precisa conter o marcador {wo}." |
| `url_mensagens` / `campo_mensagens_expandidas` / `campo_mensagens_link` / `campo_mensagens_json` | De onde vêm os itens da janela Mensagens (um dos quatro modos) | Texto ou vazio | Pelo menos um é obrigatório; senão: "Informe de onde vêm as mensagens" |
| `campos` | Nome técnico de cada campo no BTP (`material`, `fornecedor`, `quantidade_faltante`, `mensagem`...) | Dicionário | Precisa de pelo menos 3 dos 4 obrigatórios, sempre com material ou mensagem |
| `url_item`, `chaves_item` | Releitura de um item pela chave (auditoria independente) | Texto e lista | Sem eles, as auditorias são sempre novas consultas da WO |
| `mensagem_origem` | Mensagem que vai para a planilha: `item`, `alerta` ou `ambas` | Texto | Padrão `item` |
| `confirmada` | Se o usuário confirmou a amostra | Verdadeiro/falso | Fonte não confirmada pede a conferência antes da análise |
| `versao` | Versão da regra de fonte | Inteiro | Abaixo de `VERSAO_CONFIG` (6), o arquivo é apagado na inicialização (`descartar_se_antiga`) e a fonte é refeita |

**Como verificar se foi carregada:** a rota `GET /api/estado` informa a fonte em uso (resumo produzido por `Agente._fonte_resumo`), e a tela a mostra em Configurações. A função `config_atual` (`agente.py`, linhas 1098–1100) devolve `None` quando o arquivo não existe.

### 22.3 As preferências (`config/preferencias.json`)

A tabela a seguir foi montada a partir de `preferencias.py` (classe `Preferencias`, linhas 21–45; faixas `_FAIXAS`, linhas 49–54; opções `_OPCOES`, linhas 59–60) e dos rótulos da tela (`index.html`, objeto `CONFIG`).

| Rótulo na tela | Campo | Padrão | Faixa ou opções | Usado por |
|---|---|---|---|---|
| Auditorias por WO | `auditorias_por_wo` | 2 | 0–20 | `processador` |
| Auditorias independentes | `auditorias_independentes` | sim | sim/não | `processador` (alterna nova consulta e releitura por chave) |
| Repetições em caso de falha | `repeticoes_falha` | 2 | 0–10 | `_com_retentativa` |
| Diante de divergência | `divergencia` | reconsultar | reconsultar, revisar | `processador._concluir` |
| Leituras extras por divergência | `consultas_extras_divergencia` | 3 | 0–10 | `processador._concluir` |
| Tempo máximo — consulta da WO no Monitor | `tempo_consulta_wo_seg` | 30 s | 5–600 | `ClienteBTP` |
| Tempo máximo — detalhes do alerta | `tempo_consulta_detalhes_seg` | 30 s | 5–600 | `ClienteBTP` |
| Intervalo entre auditorias | `intervalo_auditorias_seg` | 0 s | 0–60 | `processador._pausa` |
| Tempo máximo por WO | `tempo_max_wo_seg` | 180 s | 10–3600 | `processador` (prazo da WO) |
| Tempo máximo da análise completa | `tempo_max_lote_min` | 240 min | 1–1440 | `Motor._passada` |
| Intervalo entre consultas ao BTP | `intervalo_consultas_seg` | 0 s | 0–30 | `cliente.Ritmo` |
| WO sem resultado | `wo_sem_resultado` | reconsultar | registrar, reconsultar | `processador` |
| Diante de erros | `em_erro` | continuar | continuar, interromper | `Motor._gravar` |
| Exportar resultados não validados | `exportar_incompletos` | sim | sim/não | `exportacao` |
| Mensagem dos itens na planilha | `mensagem_planilha` | Estoque indisponível | texto (até 120 caracteres); vazio = todos | `exportacao` |
| Consultas em paralelo | `concorrencia` | 4 | 1–8 | `Motor` (trabalhadores) e servidor (trabalhadores da aba, limitado a 6) |
| WOs por consulta em lote | `tamanho_lote` | 20 | 1–50 | `Motor` e `ClienteBTP.ler_lote` |

**Se o arquivo estiver ausente ou ilegível:** `carregar` devolve os padrões (linhas 118–121). **Se um valor estiver fora da faixa:** é ajustado ao limite mais próximo e o problema é relatado à tela (Cap. 19.4). **Migração:** arquivos da versão 1 (5 auditorias fixas) são convertidos para a política atual, mas só quando o valor gravado era igual ao padrão antigo (`migrar`, linhas 99–109) — uma escolha explícita do usuário é preservada.

::: atencao
O `README.md` do repositório ainda descreve "padrão 5" auditorias. Esse texto é anterior à versão 2 das preferências; o padrão **atual** é 2 (com escalonamento para até 5 leituras em caso de divergência). Ver Capítulo 30.
:::

### 22.4 Variável de ambiente

::: termo
**Variável de ambiente** é um valor definido no sistema operacional, fora do programa, que o programa pode ler ao iniciar. Serve para mudar um comportamento sem editar o código.
:::

O agente lê uma única variável: `AGENTE_BTP_URL` (`config.py`, linhas 21–22). Ela contém o endereço do *launchpad* do SAP e é usada **apenas** para saber de qual site a aba conectada deve vir — o servidor deriva dele a origem permitida (`ORIGEM_SAP`, `servidor.py`, linha 22) e recusa chamadas de outras origens. O agente não abre esse endereço. Quando a variável não está definida, vale o endereço gravado no código, que **não** é reproduzido neste livro por ser um endereço interno da empresa; aqui ele é representado por `<endereço do launchpad>`.

### 22.5 Endereço e porta do programa

Fixos no código: `127.0.0.1`, porta `8767` (`__main__.py`, linha 13). O endereço `127.0.0.1` ("loopback") só aceita conexões do próprio computador: outra máquina da rede não consegue acessar o agente. A porta 8767 foi escolhida para não colidir com outro programa da mesma família, o Agente Atlas, que usa a 8765 (comentário da linha 12).

### 22.6 Pastas de dados criadas em uso

| Pasta | Conteúdo | Pode apagar? |
|---|---|---|
| `dados/` | `execucoes.sqlite3` (base de todas as análises, com resultados comprimidos) | Sim, mas perde a possibilidade de retomar e de regerar relatórios |
| `historico/` | `historico.json`, `analises/`, `contador.txt` | Sim; o contador recomeça do 1 se `contador.txt` for apagado |
| `saidas/` | Planilhas geradas | Sim (são cópias; a base pode regerá-las) |
| `logs/` | `agente_btp.log` | Sim |
| `.venv/` | Ambiente Python criado pelo `.bat` | Sim; é recriado na próxima abertura |

## 23. Como executar o agente do início ao fim {#cap23}

Os passos abaixo seguem a ordem da sequência completa mostrada no Capítulo 31 ([[fig:seq_execucao]]).

Os passos abaixo valem para o computador do usuário (Windows), com o navegador já logado no BTP.

### Passo 1 — Preparar a pasta

- **O que é:** descompactar o `.zip` do projeto numa pasta do usuário (por exemplo, `Documentos\Agente-BTP`).
- **Resultado esperado:** a pasta contém `Abrir Agente BTP.bat`, `agente_btp\`, `extensao\`, `requirements.txt`.
- **Problema:** se o Windows bloquear o `.bat` ("o Windows protegeu o computador"), clicar em "Mais informações" → "Executar assim mesmo". Isso acontece com arquivos vindos de *download* ou e-mail.

### Passo 2 — Verificar o Python

- **Comando** (no Prompt de Comando): `py -3 --version`
- **Resultado esperado:** `Python 3.x.y`. <span class="naodet">NÃO DETERMINADO</span>: o projeto não declara versão mínima em nenhum arquivo. Os testes deste livro rodaram com Python 3.13.16. As anotações do tipo `str | None` não exigem, sozinhas, uma versão recente, porque todos os módulos com código usam `from __future__ import annotations` (só `__init__.py`, que não define funções, não usa); ainda assim, recomenda-se uma versão 3.10 ou mais nova, ainda mantida pelos desenvolvedores do Python.
- **Problema:** "py não é reconhecido" → instalar o Python pelo site oficial marcando "Add python.exe to PATH".

### Passo 3 — Abrir o agente

- **Ação:** dois cliques em `Abrir Agente BTP.bat`.
- **O que acontece** (linha a linha no Capítulo 9): encerra uma cópia antiga na porta 8767; cria `.venv` na primeira vez; instala `flask` e `openpyxl` (`pip install -r requirements.txt`); executa `python -m agente_btp`.
- **Resultado esperado:** a janela preta fica aberta e o navegador abre `http://127.0.0.1:8767` cerca de 1 segundo depois (`threading.Timer(1.0, webbrowser.open, ...)`, `__main__.py`, linha 55).
- **Problema:** a janela fecha sozinha ou mostra erro do `pip` → falta de internet na primeira execução, ou Python ausente. Ver Capítulo 27.

::: atencao
**Operação destrutiva embutida.** A linha 7 do `.bat` encerra à força (`taskkill /F`) qualquer processo que esteja escutando na porta 8767. Se outro programa usar essa porta, ele também será encerrado. Na prática, a porta é exclusiva do agente.
:::

### Passo 4 — Conectar a aba do BTP

- **Ação:** com a extensão instalada (Capítulo 9), basta ter a aba do BTP aberta e logada: o selo "Agente BTP: conectado" aparece na aba. Sem a extensão, clicar no favorito "Agente BTP" com a aba do BTP ativa.
- **Resultado esperado:** a tela do agente mostra a conexão como ativa (rota `/api/estado`, campo de conexão).
- **Problema:** "aguardando o programa" na aba → o `.bat` não está aberto. "Desconectado" na tela → a aba foi recarregada ou fechada; clicar de novo no favorito ou recarregar com a extensão.

### Passo 5 — Informar as WOs

- **Ação:** colar as WOs (uma por linha) ou enviar uma planilha.
- **Regras** (`entrada.py`): formato `00000|000000000`; espaços ao redor da barra são tolerados; duplicadas são removidas mantendo a ordem. Na planilha, o agente procura uma coluna com cabeçalho exatamente "WO", depois uma que contenha a palavra "WO", depois qualquer célula no formato de WO (linhas 30–60).
- **Problema:** "Não encontrei WOs na planilha" → nenhuma célula no formato esperado (por exemplo, zeros à esquerda perdidos porque a coluna estava como número no Excel).

### Passo 6 — Executar e conferir a amostra

- **Ação:** "Executar análise". Na primeira vez (ou depois de "Refazer a fonte de dados"), o agente prepara a fonte e mostra uma **amostra** de itens reais para o usuário conferir com a janela Mensagens do BTP.
- **Respostas possíveis:** "sim" (é a janela Mensagens — a fonte é gravada como confirmada); "outra" (a fonte é recusada e outra é procurada); "mostrar" (abre os detalhes).
- **Por que existe:** o agente não tem como provar sozinho que uma tabela do BTP é a mesma que o usuário vê; a confirmação humana fecha essa lacuna (Capítulo 12).

### Passo 7 — Acompanhar

- **O que ver:** barra de progresso, contadores (validadas, divergentes, incompletas, falhas), taxa de WOs por segundo e registro de eventos.
- **Pausar / Retomar / Parar:** "Pausar" segura a fila e mantém a sessão viva com uma sonda a cada 240 s (`KEEPALIVE_PAUSA_USUARIO_SEG`); "Parar" encerra, e a análise fica **retomável**.
- **Se a sessão cair:** a tela mostra "Pausada" e o painel "O que deu errado". Basta fazer login de novo na aba: o agente verifica a cada 30 s e retoma sozinho.

### Passo 8 — Verificar o resultado

- **Ação:** "Baixar relatório auditado (.xlsx)". Abrir a aba **Dados**, filtrar a coluna **Status** por tudo que não for "Validada" e ler a **Observação** dessas linhas (Capítulo 25).
- **Resultado esperado:** selo "Concluída" e nenhuma mensagem de erro de conferência do Excel.

### Passo 9 — Encerrar

- **Ação:** fechar a janela preta (ou Ctrl+C nela). A base, o histórico e as planilhas ficam gravados.
- **Problema:** fechar no meio de uma análise não perde as WOs concluídas: a análise aparece como "Retomar análise" na próxima abertura (teste `test_programa_fechado_no_meio_retoma_de_onde_parou`).

### Passo 10 — Diagnosticar falhas

- **Na tela:** Histórico → a análise → "Diagnóstico" (rota `/api/diagnostico`), que agrupa os problemas por categoria (FONTE, APP, ENTRADA) com a próxima ação recomendada.
- **No arquivo:** `logs/agente_btp.log`.
- **Na planilha:** a coluna Observação das WOs não validadas.

## 24. Como testar o funcionamento {#cap24}

### 24.1 O que é testar

::: conceito
**Testar** é executar o programa em situações controladas e comparar o que ele faz com o que deveria fazer. Um **teste automatizado** é um pequeno programa que faz isso sozinho e responde "passou" ou "falhou". A vantagem é poder repetir centenas de verificações em segundos a cada mudança.
:::

| Tipo | O que verifica | Existe no projeto? |
|---|---|---|
| Unitário | Uma função isolada (ex.: `como_quantidade("1e+0") == 1`) | **Sim**, em `testes/test_*.py` |
| Integração | Vários módulos juntos com um BTP simulado (ex.: motor + processador + loja + exportação) | **Sim**, em `testes/test_execucao.py` e `test_preparo.py`, com os simuladores `servico_real_falso.py` e `btp_lote_falso.py` |
| Ponta a ponta | Programa real + navegador real (Chromium) + SAP falso | **Sim**, em `testes/e2e/` |
| Regressão | Casos que reproduzem problemas reais já corrigidos, para não voltarem | **Sim**: testes nomeados `test_r1_`…`test_r6_` e os que citam números de análises reais |
| Manual | Uso no BTP real, com sessão real | Só pelo usuário; não há roteiro versionado. O e-book não pôde executá-lo (sem acesso ao BTP) |

@@figura testes

### 24.2 Os testes existentes

| Arquivo | Funções de teste | Foco |
|---|---|---|
| `test_auditoria.py` | 46 | Auditoria, conversões, planilha, preferências |
| `test_execucao.py` | 33 | Motor, sessão, retomada, lote, tempos |
| `test_preparo.py` | 21 | Descoberta e confirmação da fonte |
| `test_agente.py` | 20 | Cliente, classificação de respostas, paginação |
| `test_ponte.py` | 13 | Fila com a aba, vários trabalhadores, desconexão |
| `test_analisador.py` | 12 | Interpretação de consultas observadas |
| `test_materiais.py` | 10 | Tabela de valores, aba Materiais |
| `test_historico.py` | 4 | Histórico e numeração |
| **Total** | **159 funções** | O pytest conta **164** casos, porque alguns testes são parametrizados (rodam com vários dados) |

### 24.3 Resultado da execução feita para este livro

::: evidencia
Executado em 29/10/2026, sobre o commit em que este livro foi gerado, no ambiente Linux de produção do e-book (não no computador do usuário):

- `python3 -m pytest -q testes` → **164 passed in 33.85s**.
- `testes/e2e/rodar_tela.py` (cópia limpa do projeto) → **136 verificações ok, 0 falharam**.
- `testes/e2e/rodar_sessao.py` → **RESULTADO: OK** — a sessão caiu com 60 de 500 WOs validadas; 80 WOs em andamento voltaram para a fila e nenhuma virou falha; após o novo login a análise terminou com 500 validadas em 8,8 s.
- `testes/e2e/rodar_aba.py` → terminou sem erro (código de saída 0), incluindo a preparação pela aba e o apagamento de uma análise do histórico.
- `testes/e2e/rodar_extensao.py` → terminou sem erro; selo "conectado" na aba sem clique.
- `testes/e2e/rodar_combinacoes.py` **não** foi executado: exige como argumentos duas pastas de versões diferentes (programa e extensão).
:::

::: atencao
Uma primeira execução de `rodar_tela.py` **dentro** da pasta do projeto (e não numa cópia) teve 2 falhas, porque outros roteiros tinham deixado análises no histórico e o teste espera um histórico vazio. Os próprios roteiros avisam no cabeçalho: "Rode a partir de uma CÓPIA do projeto (ele grava config/, dados/ e saidas/)". Na cópia limpa, todas as 136 verificações passaram.
:::

### 24.4 Cenários essenciais e onde estão cobertos

| Cenário | Exemplo de teste |
|---|---|
| Execução bem-sucedida | `test_estrutura_completa_do_excel`; `rodar_extensao.py` |
| Ausência de dados (WO sem alerta) | testes de `Status.NAO_ENCONTRADA` em `test_agente.py`; `test_lote_quase_todo_sem_registros_refaz_a_configuracao` |
| Dados incompletos | `test_quantidade_ambigua_nao_e_validada`; `test_item_nao_relido_nao_fica_validado` |
| Respostas inesperadas | `test_linha_de_outra_wo_e_descartada_e_avisada`; `test_texto_com_igual_e_controle_nao_vira_formula_nem_quebra` |
| Falhas de conexão | `test_tarefa_entregue_a_aba_que_sumiu_volta_para_a_fila` |
| Falhas de autenticação | `test_redirecionamento_e_sessao_expirada`; `test_r3_deteccao_de_sessao_morta`; `rodar_sessao.py` |
| Erros na geração do resultado | `test_verificacao_detecta_excel_adulterado` |
| Repetição de operações | `test_r2_retomar_so_refaz_o_que_falta_e_e_idempotente` |
| Divergência entre leituras | `test_divergencia_de_quantidade_nao_e_aprovada_nem_por_maioria`; `test_r4_divergencia_escalona_para_5_leituras_e_nunca_valida` |
| Volume | `test_excel_de_lote_grande_nao_e_quadratico`; `testes/medir_desempenho.py` (medição, não teste) |

### 24.5 Como rodar

Na pasta do projeto, com as dependências de teste instaladas (`pip install pytest`; os roteiros de navegador exigem também `playwright` e o Chromium):

```
python -m pytest -q testes
python testes/e2e/rodar_tela.py      # a partir de uma CÓPIA do projeto
```

`pytest` e `playwright` **não** estão em `requirements.txt`: são ferramentas de desenvolvimento, não necessárias para usar o agente. <span class="confirmado">CONFIRMADO</span>

### 24.6 O que os testes não cobrem

- **O BTP real.** Todos os testes usam simuladores escritos a partir de observações do serviço real. Se o BTP mudar, os testes continuam passando. <span class="confirmado">CONFIRMADO</span>
- **Windows.** A execução para este livro foi em Linux; o `.bat` e o comportamento da janela de console não foram testados aqui.
- **A extensão instalada no Chrome do usuário** com políticas corporativas.

## 25. Como saber se o resultado está correto {#cap25}

### 25.1 Concluída não é o mesmo que correta

Uma análise **concluída** é aquela em que todas as WOs passaram pelo motor. Uma análise **válida** é aquela em que cada valor da planilha corresponde ao BTP. O agente separa as duas coisas na própria planilha, pela coluna **Status**, e na tela, pelo selo: "Concluída" (todas validadas), "Concluída com ressalvas" ou "Concluída com divergências".

### 25.2 As verificações que o agente faz sozinho

| Pergunta | Como o agente verifica | Onde |
|---|---|---|
| Os registros esperados foram encontrados? | Cada WO tem uma situação: com itens, sem itens, não consta no Monitor; uma WO "não consta" só é validada se **todas** as leituras concordarem | `processador._concluir`, `auditoria.consolidar` |
| Os campos foram preenchidos? | Campo vazio → item **Incompleto**, nota "Não obtido no BTP" | `consolidar`, linhas 128–133 |
| As relações estão corretas (item na WO certa)? | Linhas de outra WO numa resposta são descartadas e avisadas; a leitura em lote é comparada com a individual | `ClienteBTP`, `processar_grupo` (equivalência) |
| Há duplicações indevidas? | Cópias idênticas viram uma linha com a contagem; não somam na aba Materiais | `itens_unicos` |
| Os valores correspondem à fonte? | Leitura repetida (extração + 2 auditorias); divergência nunca é aprovada sem evidência do BTP | `consolidar`, `_resolver` |
| O arquivo foi gravado corretamente? | Reabertura e comparação célula a célula; fórmula acidental é acusada | `verificar_planilha` |
| Contadores, registro e planilha batem? | Gravação na mesma transação | `Loja.concluir` |

### 25.3 O que o usuário deve conferir

1. **Filtre a coluna Status** por "Divergente", "Incompleta" e "Falha". A Observação de cada linha diz o que conferir.
2. **Confira por amostragem** algumas WOs "Validada" diretamente na janela Mensagens do BTP, sobretudo na primeira análise depois de uma mudança no BTP.
3. **Compare os totais** da aba Análise com a quantidade de WOs enviadas.
4. **Observe a aba Materiais:** materiais "fora da tabela de valores" indicam que a tabela importada está desatualizada, não que o dado do BTP esteja errado.

### 25.4 Garantias que o agente **não** oferece

- **Não prova que a fonte é a janela Mensagens.** Depende da confirmação humana da amostra. Uma confirmação errada valida, com toda a auditoria, dados da tabela errada. Por isso a regra de segurança "≥ 90% das WOs sem itens refaz a fonte" existe, mas não cobre todos os casos. <span class="interpretacao">INTERPRETAÇÃO TÉCNICA</span>
- **Leituras repetidas não detectam um erro constante.** Se o BTP devolver sempre o mesmo valor errado, as três leituras concordam. A auditoria garante **estabilidade da leitura**, não correção do dado de origem.
- **Valores em R$** dependem inteiramente da tabela importada.

::: proposta
Uma verificação adicional possível (não implementada): uma "amostra de conferência" ao final de cada análise, com 5 WOs sorteadas e um atalho para abri-las no BTP, registrando no histórico que a conferência humana foi feita.
:::
