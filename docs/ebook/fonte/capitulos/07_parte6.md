# Parte VI — Falhas, segurança e manutenção {#parte6}

Um programa que conversa com um sistema externo vai, cedo ou tarde, encontrar falhas que não são culpa dele: sessão expirada, servidor lento, aba fechada. Esta parte mostra como o Agente BTP reconhece e trata cada tipo de falha, traz um manual de solução de problemas, examina a segurança das informações e termina com uma avaliação franca das limitações e das melhorias possíveis.

## 26. Tratamento de erros {#cap26}

### 26.1 A ideia: cada erro tem um dono

O princípio que organiza o tratamento de erros do agente é **perguntar de quem é o problema** antes de decidir o que fazer:

| Dono | Exemplo | Reação correta |
|---|---|---|
| A **sessão** | Login expirou | Pausar tudo; nenhuma WO vira falha; retomar quando a sessão voltar |
| A **comunicação** | BTP lento, HTTP 503 | Repetir com espera crescente |
| A **WO** | Número inválido; 403 só nesta WO | Registrar falha **desta** WO, com motivo, e seguir |
| O **programa** | Erro inesperado no código | Registrar com rastro completo (*traceback*) e marcar as WOs afetadas |

O diagnóstico codifica esse princípio em três categorias (`diagnostico.py`, linhas 7–10): **FONTE** (o BTP ou o caminho até ele não entregou o dado), **APP** (o próprio agente falhou ou atingiu um limite configurado) e **ENTRADA** (a WO informada não serve).

@@figura erros

### 26.2 Mecanismo 1 — Classificação na origem

Toda resposta passa por `cliente.classificar` (Cap. 19.1), que converte a situação HTTP numa exceção com motivo. É a peça que permite às camadas superiores reagir corretamente sem examinar a resposta de novo.

### 26.3 Mecanismo 2 — Repetição com espera crescente

`processador._com_retentativa` (Cap. 19.2) repete apenas falhas de comunicação, até `repeticoes_falha` vezes, com esperas de 1, 2 e 4 s (máximo 5 s), respeitando o prazo da WO. Além disso, o `Ritmo` do cliente (`cliente.py`, linhas 88–123) aumenta a espera entre consultas quando o BTP responde 429 ou 5xx (dobrando até 30 s e respeitando o cabeçalho `Retry-After`) e a reduz a cada sucesso. <span class="confirmado">CONFIRMADO</span>

### 26.4 Mecanismo 3 — Falha de consulta vira resultado da WO

@@codigo agente_btp/processador.py processar_wo_seguro

Uma falha de consulta não derruba a análise: vira um `ResultadoWO` com situação, auditoria "Não concluído" e o motivo. A única exceção que continua subindo é `SessaoExpirada`, porque não é problema da WO (docstring, linhas 189–190).

### 26.5 Mecanismo 4 — Sessão: pausa, sonda e retomada automática

::: nobtp
**Sonda** é uma consulta leve feita só para saber se a sessão ainda é válida. O motor sonda antes de começar, a cada 60 s ou a cada 100 WOs concluídas (`motor.py`, linhas 30–31 e 153–154). Como toda consulta passa pelo *approuter* do BTP, a sonda também funciona como "mantenha viva" (*keep-alive*) da sessão.
:::

Quando uma consulta indica sessão expirada, o motor **não acredita de imediato**: sonda de novo (`_sessao_na_wo`, linha 226).

- **Sonda falhou** → a sessão caiu mesmo. As WOs do grupo voltam para "pendente" (`Loja.devolver`), o motivo é registrado e a análise entra em `pausado_sessao`.
- **Sonda passou** → o problema é de alguma WO específica (por exemplo, 403 por falta de permissão naquela WO). As WOs do grupo são refeitas uma a uma; só a que repetir o erro falha, com a observação "Só nesta WO: a sessão do BTP continua válida".

Durante a pausa, `_esperar_sessao` (linhas 292–315) tenta de novo quando a aba se anuncia (por exemplo, após recarregar com o novo login) ou a cada 30 s. Ao passar, registra "Sessão do BTP válida de novo" e continua. O tempo pausado **não conta** para o limite de tempo da análise (`_pausado_total`).

::: evidencia
`testes/e2e/rodar_sessao.py`, executado para este livro: queda simulada (302 para o SSO) com 60 de 500 WOs feitas → 80 WOs em andamento voltaram para a fila, nenhuma falha; após o novo login, 500 validadas. Teste unitário correspondente: `test_r1_queda_no_meio_com_paralelismo_e_lote_nunca_vira_falha`.
:::

### 26.6 Mecanismo 5 — Exceções inesperadas nunca são silenciosas

No motor, qualquer exceção que não seja de sessão vai para `_excecao` (linhas 251–260): grava no diagnóstico o motivo `excecao_app`, o tipo e a mensagem do erro, a **origem no código** (módulo, função, linha — `diagnostico.origem`) e o *traceback* (até 6.000 caracteres — `diagnostico.rastreio`), e marca as WOs em andamento do grupo como falha com essa explicação. A mesma regra vale para a sonda (linhas 264–269: "a sonda nunca derruba a análise") e para a geração do Excel (`Agente._planilha`, linhas 921–925).

### 26.7 Mecanismo 6 — Interrupção segura e retomada

| Situação | O que acontece | Retomável? |
|---|---|---|
| Usuário clica "Parar" | WOs em andamento terminam; as pendentes ficam pendentes | Sim |
| Tempo máximo da análise | Motivo `limite_execucao`; evento registrado | Sim |
| Preferência "Diante de erros: interromper" | Para na primeira falha de WO (exceto WO inválida) — `Motor._gravar`, linhas 214–217 | Sim |
| Programa fechado ou computador desligado | WOs concluídas já estão na base; as que estavam em andamento voltam a pendente quando a análise é retomada (`Motor.executar` chama `devolver_em_andamento`, `motor.py`, linha 86) | Sim |

A retomada usa as **mesmas preferências e a mesma fonte** gravadas no início da análise, refaz só o que falta e é idempotente — repetir a retomada não duplica nada (teste `test_r2_retomar_so_refaz_o_que_falta_e_e_idempotente`).

### 26.8 Mecanismo 7 — Continuidade parcial

A base guarda cada WO assim que termina. Por isso:

- a planilha parcial pode ser baixada a qualquer momento durante a análise (`planilha_parcial`);
- uma falha na geração do Excel não perde a análise — o relatório pode ser regerado pelo Histórico;
- uma consulta em lote que diverge da individual desativa o lote só **naquela análise** e continua WO a WO (motivo `lote_desativado`).

### 26.9 Situações ainda não tratadas ou tratadas parcialmente

| Situação | Comportamento atual | Classificação |
|---|---|---|
| Falha ao copiar o `agente_btp.js` para a pasta da extensão | `sincronizar_extensao` ignora `OSError` em silêncio (`servidor.py`, linhas 41–44); a extensão pode ficar com cópia antiga | <span class="confirmado">CONFIRMADO</span> no código |
| Porta 8767 ocupada por outro programa | O `.bat` encerra o processo; se o agente for iniciado sem o `.bat`, o Flask falha ao abrir a porta e a janela mostra o erro | <span class="interpretacao">INTERPRETAÇÃO TÉCNICA</span> |
| Disco cheio | Gravações de base, histórico ou Excel falham com exceção; o Excel tem tratamento, a base não tem tratamento específico | <span class="hipotese">HIPÓTESE</span> — não reproduzido |
| Mudança de formato no BTP que continue devolvendo JSON válido com os mesmos nomes de campo, mas significado diferente | Não detectável pelo agente | <span class="interpretacao">INTERPRETAÇÃO TÉCNICA</span> |

## 27. Manual de solução de problemas {#cap27}

As tabelas abaixo estão separadas por área. A coluna "Base" indica se o problema é **confirmado** (aparece no código, em testes ou em registros do projeto), um **risco plausível** pela arquitetura ou um **cenário hipotético** ainda não reproduzido.

### 27.1 Instalação e inicialização

| Sintoma | Causas possíveis | Como verificar | Como corrigir | Como confirmar | Base |
|---|---|---|---|---|---|
| Janela preta abre e fecha | Python ausente; erro no `pip` | Abrir o Prompt de Comando na pasta e rodar `py -3 --version` e o `.bat` dali, para ler a mensagem | Instalar Python com "Add to PATH"; garantir internet na 1ª execução | A tela abre em `127.0.0.1:8767` | Risco plausível |
| "No module named flask" | `.venv` corrompido ou instalação interrompida | Ver a mensagem na janela | Apagar a pasta `.venv` e abrir o `.bat` de novo | A janela não mostra erro | Risco plausível |
| Navegador não abre sozinho | Navegador padrão não configurado | A janela preta continua aberta sem erro | Abrir manualmente `http://127.0.0.1:8767` | Tela carrega | Risco plausível |
| A análise "congela" em Windows ao clicar na janela preta | Modo de seleção do console congela escritas | — | Corrigido: o registro vai só para arquivo e o modo de seleção é desligado (`desligar_selecao_da_janela`) | — | Confirmado (comentário de `__main__.py`, linhas 17–21: "a análise parava no meio, ex.: 391 de 499 WOs") |

### 27.2 Conexão com a aba

| Sintoma | Causas possíveis | Como verificar | Como corrigir | Como confirmar | Base |
|---|---|---|---|---|---|
| Selo "aguardando o programa" na aba | Programa fechado | A janela preta está aberta? | Abrir o `.bat` | Selo muda para "conectado" | Confirmado (`rodar_extensao.py`) |
| Favorito mostra "Agente BTP não encontrado" | Programa fechado; navegador bloqueou acesso à rede local | Mensagem do alerta (`servidor.codigo_carregador`) | Abrir o programa; **permitir** o acesso à rede local quando o navegador perguntar | Selo "conectado" | Confirmado no código |
| Tela mostra aba desconectada durante a análise | Aba recarregada, fechada, ou em segundo plano congelada pelo navegador | Painel "O que deu errado": motivo `aba_desconectada` ou `aba_sem_resposta` | Reabrir a aba logada e clicar no favorito (ou recarregar com a extensão); não minimizar a janela | A análise retoma | Confirmado (taxonomia) |
| Extensão não conecta após atualizar o programa | Cópia antiga do `agente_btp.js` na pasta da extensão; extensão não recarregada | Comparar a data de `extensao/agente_btp.js`; ver a versão no selo | Em `chrome://extensions`, clicar em "Recarregar" na extensão | Selo "conectado" | Risco plausível (ver 26.9) |
| Chamadas recusadas com 403 pelo programa | A aba não é do endereço configurado (`AGENTE_BTP_URL`) | Endereço da aba | Usar a aba do *launchpad* correto | Conexão aceita | Confirmado (`exigir_origem_sap`) |

### 27.3 Sessão e acesso ao BTP

| Sintoma | Causas possíveis | Como verificar | Como corrigir | Como confirmar | Base |
|---|---|---|---|---|---|
| Análise "Pausada" com motivo de sessão | Sessão expirou (redirecionamento, 401, página de login) | Painel "O que deu errado" | Fazer login de novo na aba | Retoma sozinha em até 30 s | Confirmado (`rodar_sessao.py`) |
| Algumas WOs com falha `http_403` e a sessão válida | Usuário sem permissão naquelas WOs | Observação "Só nesta WO: a sessão do BTP continua válida" | Verificar permissão no SAP | — | Confirmado no código |
| Muitas WOs com `http_404` | O app do BTP foi atualizado e o endereço mudou | Diagnóstico: motivo `http_404` | Configurações → "Refazer a fonte de dados" | Amostra confere com a janela Mensagens | Confirmado (taxonomia; o agente refaz a fonte sozinho com 404 em massa) |
| Lentidão, motivo `http_429` | BTP pedindo menos pedidos por segundo | Diagnóstico | Reduzir "Consultas em paralelo" | Sem novos 429 | Confirmado (taxonomia) |
| Falhas `http_5xx` | Instabilidade do BTP | Diagnóstico | Esperar e "Retomar" | WOs refeitas | Confirmado (taxonomia) |

### 27.4 Fonte de dados e extração

| Sintoma | Causas possíveis | Como verificar | Como corrigir | Como confirmar | Base |
|---|---|---|---|---|---|
| Quase todas as WOs "sem itens" | Fonte errada (ligação com as mensagens incorreta); ou WOs realmente sem alerta | O agente refaz a fonte sozinho acima de 90% (`LIMIAR_SEM_REGISTROS`) | Conferir a amostra com a janela Mensagens | Itens aparecem | Confirmado |
| Muitos itens "quantidade ambígua" | Tabela usada não é a janela Mensagens (tem dois campos de quantidade) | Motivo `quantidade_ambigua`; a amostra mostra a tabela | "Refazer a fonte de dados" | Itens validados | Confirmado (taxonomia e comentário de `agente.py`, linha 574, sobre 2.900 WOs afetadas por uma fonte assim) |
| Muitas WOs `wo_inexistente` | WOs digitadas erradas; WOs sem alerta no Monitor | Conferir algumas no BTP | Corrigir a lista | — | Confirmado (diagnóstico real: 2.180 de 13.623) |
| Itens "Divergente" | Valor mudou no BTP durante a análise; leitura instável | Observação com as leituras `r0=... / r1=...` e aba Rastreio | Conferir manualmente no BTP | — | Confirmado |
| Lote desativado logo no início | Leitura em lote diferente da individual | Evento `lote_desativado` | Nada: o resultado segue exato, só mais lento | — | Confirmado (diagnóstico real: lote desativado 1 s após o início) |

### 27.5 Planilha e arquivos

| Sintoma | Causas possíveis | Como verificar | Como corrigir | Como confirmar | Base |
|---|---|---|---|---|---|
| "A conferência do Excel gravado encontrou diferenças" | Arquivo alterado durante a gravação; erro de programa | Mensagem lista as primeiras diferenças | Histórico → Baixar relatório (regera); se repetir, enviar o diagnóstico | Sem mensagem | Confirmado (teste `test_verificacao_detecta_excel_adulterado`) |
| "Erro ao gerar ou gravar o Excel" | Arquivo aberto no Excel com o mesmo nome; disco cheio; permissão | Diagnóstico `excecao_app` com *traceback* | Fechar o arquivo no Excel; liberar espaço; regerar pelo Histórico | Arquivo baixado | Risco plausível |
| Tipo Material e valores "-" | Tabela de valores não importada ou material fora dela | Observação da aba Materiais | Importar a tabela atualizada em Configurações | Valores preenchidos | Confirmado |
| Importação da tabela recusada | Mesmo material com tipo ou valor diferentes; colunas não encontradas | Mensagem de `TabelaInvalida` | Corrigir a planilha de valores | Importação aceita | Confirmado (`materiais._montar`) |
| Zeros à esquerda sumiram na planilha de entrada | Coluna de WO formatada como número no Excel de origem | Ver a célula no Excel | Formatar a coluna como texto | WOs aceitas | Risco plausível |

### 27.6 Execução

| Sintoma | Causas possíveis | Como verificar | Como corrigir | Como confirmar | Base |
|---|---|---|---|---|---|
| "O agente já está executando uma análise" (409) | Clique duplo; outra análise em curso | Selo da tela | Aguardar ou "Parar" | — | Confirmado |
| Análise "Interrompida" com `limite_execucao` | Tempo máximo da análise atingido | Diagnóstico | Aumentar o tempo em Configurações e "Retomar" | Termina | Confirmado |
| WOs com `timeout_wo` | BTP lento; prazo de 180 s curto para muitas auditorias | Diagnóstico | "Retomar"; se persistir, aumentar os tempos | WOs concluídas | Confirmado (taxonomia) |
| Retomada não aparece | Análise já concluída ou apagada do histórico | Histórico | Iniciar nova análise | — | Confirmado |

## 28. Segurança e proteção das informações {#cap28}

### 28.1 Mecanismos implementados

| Tema | Mecanismo | Evidência |
|---|---|---|
| Credenciais | O agente **não** pede, não lê e não grava senha, cookie ou *token*. A autenticação é a do próprio navegador do usuário | `preferencias.py` (linhas 1–5); campos de `ApiConfig`; descrição do manifesto da extensão: "Não faz login, não lê cookies, não guarda dados." |
| Somente leitura | O transporte só faz `GET` (`agente_btp.js`, linha 196: `method: 'GET'`); não há código para "Reprocessar" ou "Efetuar Baixa" | <span class="confirmado">CONFIRMADO</span> |
| Acesso ao programa | Escuta só em `127.0.0.1` (o próprio computador) | `__main__.py`, linha 13 |
| Canal com a aba | Rotas `/api/agente/*` exigem o cabeçalho `Origin` igual ao do *launchpad* (`exigir_origem_sap`); CORS liberado só para essa origem | `servidor.py`, linhas 66–81 |
| Arquivos baixados | Download restrito à pasta `saidas/` | `caminho_saida` (Cap. 19.7) |
| Evidências do diagnóstico | Segredos conhecidos removidos; corpo limitado a 800 caracteres | `diagnostico.sanitizar` (Cap. 19.6) |
| Tamanho de envio | Pedidos ao programa limitados a 20 MB | `servidor.py`, `MAX_CONTENT_LENGTH` |
| Permissões da extensão | `scripting` e `tabs`, só no endereço do *launchpad* | `extensao/manifest.json` |
| Dados locais fora do Git | `.gitignore` exclui `config/api.json`, preferências, tabela, histórico, `dados/`, `saidas/`, `logs/` | `.gitignore` |

### 28.2 Que informações ficam no computador

| Local | Conteúdo sensível? |
|---|---|
| `saidas/*.xlsx` | **Sim** — dados operacionais: WOs, materiais, fornecedores, quantidades, valores. Tratar como documento interno |
| `dados/execucoes.sqlite3` | **Sim** — os mesmos dados, mais as leituras brutas (aba Rastreio) e evidências do diagnóstico |
| `historico/` | WOs de cada análise e resumos |
| `logs/agente_btp.log` | Números de WO, situação e contagem de itens por WO (`motor.py`, linha 213); erros com *traceback* |
| `config/api.json` | Endereços internos do serviço OData do BTP (não são segredos de acesso, mas revelam a estrutura interna) |

::: atencao
Ao enviar um "diagnóstico" para análise, ele contém WOs, URLs internas e trechos de respostas (já sem segredos conhecidos). Compartilhe apenas com quem pode ver esses dados.
:::

### 28.3 Riscos e recomendações (não implementadas)

| Risco | Explicação | Classificação |
|---|---|---|
| Pedidos de outros sites para as rotas da tela | As rotas da tela (por exemplo, `POST /api/parar`, que não tem corpo) não verificam a origem. Em tese, uma página maliciosa aberta no mesmo navegador poderia enviar um pedido simples a `127.0.0.1:8767`. Navegadores recentes aplicam proteções de acesso à rede local que tendem a bloquear isso | <span class="hipotese">HIPÓTESE</span> — não testado |
| Endereço interno no código | O endereço do *launchpad* está gravado em `config.py` e no manifesto da extensão | <span class="confirmado">CONFIRMADO</span>; baixo impacto, pois não dá acesso sem login |
| Dependências externas | `flask` e `openpyxl` são instalados sem versão máxima (`>=`); uma versão nova incompatível ou comprometida seria instalada automaticamente na próxima abertura do `.bat` | <span class="confirmado">CONFIRMADO</span> (`requirements.txt`) |
| Planilhas com dados operacionais | Ficam em `saidas/` sem prazo de expiração | <span class="confirmado">CONFIRMADO</span> |

::: proposta
1. Exigir um cabeçalho próprio (por exemplo `X-Agente-BTP: 1`) nas rotas de alteração da tela — pedidos com cabeçalho customizado exigem verificação prévia (*preflight*) do navegador, que outro site não passa.
2. Fixar as versões em `requirements.txt` (por exemplo `flask==3.0.3`) e atualizá-las de forma deliberada, após rodar os testes.
3. Oferecer em Configurações a limpeza de análises e planilhas antigas.
:::

## 29. Manutenção e evolução {#cap29}

### 29.1 Dependências

`requirements.txt` declara apenas `flask>=3.0` e `openpyxl>=3.1`. Para saber o que está instalado: `.venv\Scripts\python -m pip list`. Para ver se há versões novas: `.venv\Scripts\python -m pip list --outdated`.

Antes de aceitar uma atualização: ler as notas de versão (Apêndice D), atualizar numa cópia, rodar `python -m pytest -q testes` e os roteiros de navegador, e gerar uma planilha de uma análise conhecida para comparar.

### 29.2 Mudanças no BTP

O BTP é a dependência mais crítica e a que o projeto não controla. Sinais de que ele mudou:

- muitos `http_404` (endereço mudou);
- aumento súbito de `quantidade_ambigua` ou `campo_ausente` (campos renomeados);
- amostra que não corresponde mais à janela Mensagens;
- quase todas as WOs "sem itens".

A defesa já existente é a **re-preparação**: descartar a fonte ("Refazer a fonte de dados") e conferir a amostra de novo. Quando a mudança for estrutural, o ponto de partida no código é `autoconfig.receita_monitor` e os testes de `test_preparo.py`.

### 29.3 Logs e diagnóstico

- `logs/agente_btp.log`: uma linha por WO concluída e os erros, com data e hora.
- Diagnóstico da análise (Histórico): problemas agrupados por motivo e categoria, com evidência e próxima ação.
- Eventos da análise (rota `/api/execucoes/<id>/registro`): linha do tempo com sondas, pausas e marcos.

### 29.4 Versões e registro de modificações

O projeto usa Git (41 commits de código e documentação entre 01/10/2026 e 29/10/2026, antes dos commits deste livro). Cada rodada de correção é documentada em `docs/relatorio-correcao-raiz.md`, com o diagnóstico, o que mudou, os testes e as limitações. Esse hábito deve ser mantido. A tela exibe uma "versão" que é uma **impressão digital** de todos os arquivos do programa (`servidor.py`: os 10 primeiros caracteres de um *hash* SHA-1), o que permite saber, pela tela, se dois computadores rodam exatamente o mesmo código.

### 29.5 Regressão e versão estável

1. Antes de mudar: rodar os testes e guardar uma planilha de referência.
2. Depois: rodar de novo; comparar a planilha regerada da mesma análise.
3. Distribuir com um novo `.zip` e guardar o anterior. Voltar atrás = descompactar o anterior (a base em `dados/` é compatível enquanto o esquema não mudar).

### 29.6 Pontos que exigem atenção especial

- **Esquema da base SQLite:** não há migração de esquema no código. Uma mudança nas tabelas exige cuidado com bases existentes. <span class="confirmado">CONFIRMADO</span>
- **Duas cópias do `agente_btp.js`:** a da pasta `agente_btp/web/` (servida ao favorito) e a da extensão, sincronizada ao abrir o programa.
- **`VERSAO_CONFIG`:** aumentar o número descarta as fontes gravadas de todos os usuários na próxima abertura — é o mecanismo correto para forçar a re-preparação.

## 30. Limitações e possibilidades de melhoria {#cap30}

### 30.1 Limitações confirmadas

| Limitação | Consequência |
|---|---|
| Depende de uma aba do navegador aberta e logada | Não roda sem o usuário; a aba não pode ser fechada nem congelada |
| A escolha da fonte depende de confirmação humana | Uma confirmação errada valida dados da tabela errada |
| Auditoria mede estabilidade, não correção na origem | Um valor errado e constante no BTP passa como validado |
| Testes não usam o BTP real | Mudanças no BTP só aparecem em uso |
| Só Windows tem lançador (`.bat`) | Outros sistemas exigem rodar `python -m agente_btp` manualmente |
| README desatualizado ("padrão 5" auditorias, menções antigas) | Pode confundir quem lê o repositório |
| `config/api.exemplo.json` não reflete a fonte atual | Idem |
| Exceção ignorada em `sincronizar_extensao` | Extensão pode ficar com cópia antiga sem aviso |

### 30.2 Propostas classificadas

Critério: **Alta** = afeta a correção dos dados ou a segurança; **Média** = afeta a manutenção ou o diagnóstico; **Baixa** = conforto ou clareza.

| Prioridade | Proposta (não implementada) | Justificativa |
|---|---|---|
| Alta | Fixar versões das dependências | Evita que uma atualização automática quebre o programa na abertura |
| Alta | Cabeçalho obrigatório nas rotas de alteração da tela | Fecha o risco de pedidos de outros sites (28.3) |
| Alta | Roteiro de teste manual versionado no BTP real (5 WOs conhecidas) | Única forma de detectar mudança no BTP antes de uma análise grande |
| Média | Avisar na tela quando `sincronizar_extensao` falhar | Remove a única exceção silenciosa encontrada |
| Média | Atualizar `README.md` e `api.exemplo.json` | Coerência da documentação do repositório |
| Média | Migração de esquema da base | Permite evoluir a base sem perder análises antigas |
| Média | Painel de tendências entre análises (taxa de validadas, motivos) | Monitoramento: uma piora súbita indica mudança no BTP |
| Baixa | Filtro com várias mensagens (Cap. 20) | Flexibilidade da planilha |
| Baixa | Limpeza de análises antigas | Higiene de dados locais |
| Baixa | Dividir `agente.py` (1.117 linhas) em partes menores (preparação, execução, relatório) | Clareza do código |
