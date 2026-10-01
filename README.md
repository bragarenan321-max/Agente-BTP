# Agente-BTP
Automação destinada à coleta e análise de informações em um sistema web corporativo, executada a partir de uma interface web local.

Status: Em fase de levantamento e especificação do processo manual.

1. Objetivo

O Agente BTP tem como objetivo automatizar a execução de consultas e análises em um sistema web corporativo, permitindo que o usuário:

abra a aplicação do Agente BTP;
realize manualmente o login no sistema;
conclua a autenticação de dois fatores pelo celular;
conecte a automação à sessão já autenticada;
escolha o tipo de análise desejada;
execute automaticamente o procedimento correspondente;
colete e processe as informações encontradas;
apresente o resultado ao usuário.

As informações específicas que serão coletadas ainda serão definidas durante o levantamento do processo manual.

2. Escopo inicial

O projeto utilizará apenas um sistema web externo para realização das consultas.

A URL atualmente identificada para autenticação é:

https://ipc.claro.com.br/idp/SSO.saml2

O login não fará parte da automação.

A autenticação inicial será realizada manualmente pelo usuário.

3. Fluxo geral
Usuário
   │
   ▼
Abrir Agente BTP
   │
   ▼
Iniciar servidor local temporário
   │
   ▼
Abrir interface web do BTP
   │
   ▼
Abrir navegador controlado
   │
   ▼
Usuário realiza login
   │
   ▼
Usuário confirma autenticação no celular
   │
   ▼
Sessão autenticada
   │
   ▼
BTP conecta-se à sessão
   │
   ▼
Usuário escolhe o tipo de análise
   │
   ▼
Configuração dos parâmetros/filtros
   │
   ▼
Executar análise
   │
   ▼
Navegação automatizada
   │
   ▼
Coleta dos dados
   │
   ▼
Processamento
   │
   ▼
Resultado
4. Autenticação
4.1 Responsabilidade do usuário

O usuário será responsável por:

acessar o sistema;
informar suas credenciais;
realizar o login;
concluir a verificação solicitada pelo sistema;
aprovar a autenticação pelo celular.
4.2 Responsabilidade da automação

A automação será responsável por:

aguardar a conclusão da autenticação;
conectar-se à sessão autenticada;
verificar se o sistema está disponível para utilização;
continuar o processo a partir do ponto em que o usuário terminou o login.
4.3 Regras de segurança

A automação deverá:

não armazenar senha do usuário;
não automatizar ou contornar o MFA;
não solicitar código de autenticação ao usuário;
utilizar somente a sessão que já tenha sido autenticada;
manter credenciais fora do código-fonte e dos arquivos de configuração versionados.
5. Interface do Agente

O Agente BTP deverá possuir uma interface web executada localmente.

O fluxo inicial será semelhante ao conceito utilizado no Agente Atlas:

Abrir Site
    │
    ▼
Inicialização do Agente BTP
    │
    ▼
Servidor local temporário
    │
    ▼
Interface Web

A interface será responsável por centralizar as operações da automação.

Interface inicial prevista
┌─────────────────────────────────────────┐
│              AGENTE BTP                 │
├─────────────────────────────────────────┤
│                                         │
│ Sistema: ● Não conectado                │
│                                         │
│ [ CONECTAR AO SISTEMA ]                 │
│                                         │
│ Tipo de análise:                        │
│ [ Selecionar análise              ▼ ]   │
│                                         │
│ [ EXECUTAR ANÁLISE ]                    │
│                                         │
│ Status:                                  │
│ Aguardando conexão...                   │
│                                         │
└─────────────────────────────────────────┘

A interface poderá evoluir conforme novas análises forem adicionadas.

6. Conexão com o sistema

Depois que o usuário concluir o login e a autenticação, o Agente BTP deverá assumir o controle da sessão autenticada.

Fluxo esperado:

Navegador
   │
   ├── Login manual
   ├── MFA manual
   │
   ▼
Sessão autenticada
   │
   ▼
Agente BTP
   │
   ▼
Conexão com navegador/sessão
   │
   ▼
Execução da análise

A implementação exata dessa conexão ainda será definida durante a fase de desenvolvimento.

Estratégia preliminar

A arquitetura deverá permitir que o agente controle um navegador dedicado à tarefa, evitando depender do navegador pessoal do usuário ou de abas não relacionadas ao processo.

7. Execução das análises

O Agente BTP deverá ser estruturado para permitir múltiplos tipos de análise.

A interface deverá permitir selecionar qual procedimento será executado.

Exemplo:

Tipo de análise:

- Análise 01
- Análise 02
- Análise 03
- Análise 04

Cada análise deverá possuir seu próprio fluxo de execução.

Estrutura conceitual
AGENTE BTP
│
├── Interface
│
├── Gerenciador de sessão
│
├── Conexão com navegador
│
├── Executor
│
├── Análises
│   ├── analise_01
│   ├── analise_02
│   ├── analise_03
│   └── ...
│
├── Coleta
│
├── Processamento
│
├── Exportação
│
└── Logs

Essa estrutura deverá permitir a inclusão de novas análises sem necessidade de reescrever o sistema inteiro.

8. Processo manual atual

Antes da implementação da automação, o processo será documentado integralmente de forma manual.

Etapas já identificadas
1. Login

O usuário acessa o sistema e realiza o login manualmente.

2. Autenticação

O sistema solicita uma verificação adicional.

O usuário recebe a solicitação no celular e aprova o acesso.

3. Acesso ao sistema

Após a autenticação, o usuário entra no sistema.

4. Acesso à funcionalidade

O usuário acessa uma opção específica do sistema.

Funcionalidade:
[A DEFINIR]
5. Aplicação dos filtros

O usuário seleciona os filtros necessários para determinar quais informações serão consultadas.

Filtros:
[A DEFINIR]
6. Seleção do resultado

O usuário seleciona a opção correspondente ao resultado desejado.

7. Abertura das informações

O sistema carrega uma aba/janela minimizada sobre a página atual.

Essa área contém as informações necessárias para a análise.

8. Coleta

O usuário coleta manualmente as informações apresentadas.

Dados coletados:
[A DEFINIR]
9. Dados
Entrada

Ainda não definida.

[A DEFINIR]
Informações coletadas

Ainda não definidas.

[A DEFINIR]
Saída

Ainda não definida.

[A DEFINIR]
10. Regras de negócio

As regras de negócio serão levantadas durante a documentação do processo manual.

Por enquanto:

[A DEFINIR]

Cada regra deverá posteriormente ser documentada em formato determinístico.

Exemplo de estrutura:

SE condição
    ENTÃO executar ação
SENÃO
    executar outra ação

O objetivo é garantir que decisões atualmente tomadas pelo usuário possam ser convertidas em regras reproduzíveis pelo software.

11. Tratamento de erros

O sistema deverá posteriormente tratar situações como:

- Sessão não autenticada
- Falha na conexão com o navegador
- Sistema indisponível
- Página não carregada
- Filtro não encontrado
- Resultado não encontrado
- Informação ausente
- Tempo de carregamento excedido
- Sessão expirada

Os tratamentos específicos ainda serão definidos.

12. Logs

O Agente BTP deverá registrar informações relevantes da execução para permitir:

acompanhamento do processo;
identificação de falhas;
diagnóstico de problemas;
auditoria da execução;
identificação da etapa em que ocorreu um erro.

Exemplo:

[INFO] Agente iniciado
[INFO] Navegador detectado
[INFO] Sessão autenticada
[INFO] Conectado ao sistema
[INFO] Análise selecionada: XXXXX
[INFO] Iniciando consulta
[INFO] Coleta iniciada
[INFO] Coleta concluída
[INFO] Resultado gerado

Os dados sensíveis não deverão ser registrados nos logs.

13. Estrutura prevista do projeto

Estrutura inicial, sujeita a alteração durante o desenvolvimento:

agente-btp/
│
├── README.md
│
├── abrir_site/
│   └── ...
│
├── app/
│   ├── ...
│
├── automacao/
│   ├── navegador/
│   ├── sessao/
│   └── executor/
│
├── analises/
│   ├── analise_01/
│   ├── analise_02/
│   └── ...
│
├── coleta/
│   └── ...
│
├── processamento/
│   └── ...
│
├── exportacao/
│   └── ...
│
├── logs/
│   └── ...
│
├── testes/
│   └── ...
│
└── requirements.txt

A estrutura definitiva será determinada após o levantamento técnico do sistema.

14. Fases do projeto
Fase 1. Levantamento manual

Documentar detalhadamente todo o procedimento realizado pelo usuário.

Objetivos:

identificar cada clique;
identificar cada tela;
identificar filtros;
identificar resultados;
identificar decisões;
identificar exceções;
identificar dados necessários.
Fase 2. Investigação técnica

Depois que o processo estiver documentado:

analisar o comportamento do site;
identificar requisições;
identificar APIs/endpoints quando existentes;
verificar carregamento dinâmico;
analisar a abertura da aba/janela de informações;
definir como a sessão autenticada será utilizada;
determinar a tecnologia de automação adequada.
Fase 3. Prova de conceito

Criar uma primeira implementação capaz de:

Abrir BTP
   ↓
Abrir navegador
   ↓
Login manual
   ↓
MFA manual
   ↓
Conectar sessão
   ↓
Acessar funcionalidade
   ↓
Executar uma análise
Fase 4. Implementação

Desenvolver os módulos definitivos:

interface;
gerenciamento da sessão;
automação;
análises;
coleta;
processamento;
exportação;
logs;
tratamento de erros.
Fase 5. Testes

Validar:

conexão;
autenticação;
navegação;
filtros;
coleta;
processamento;
resultados;
recuperação de erros;
expiração de sessão;
execução repetida.
Fase 6. Empacotamento

Após a aplicação estar funcional, criar o mecanismo de distribuição para que o usuário possa simplesmente executar:

Abrir Site

e iniciar todo o ambiente necessário.

15. Princípios do projeto

O Agente BTP deverá seguir alguns princípios fundamentais:

Login manual

As credenciais e a autenticação inicial pertencem ao usuário.

Automação após autenticação

A automação assume somente depois que a sessão estiver autenticada.

Modularidade

Cada tipo de análise deverá ser independente.

Rastreabilidade

As etapas importantes da execução deverão possuir logs.

Segurança

Credenciais, tokens e informações sensíveis não deverão ser armazenados desnecessariamente.

Evolução incremental

O projeto deverá ser desenvolvido a partir do procedimento manual real, adicionando automações conforme cada etapa for compreendida e validada.

16. Pontos ainda não definidos

Os seguintes itens serão preenchidos durante o levantamento:

[ ] Nome da funcionalidade acessada
[ ] Sequência completa de navegação
[ ] Filtros disponíveis
[ ] Valores dos filtros
[ ] Critérios de seleção
[ ] Informações coletadas
[ ] Regras de negócio
[ ] Formato da saída
[ ] Forma de exportação
[ ] Quantidade de análises
[ ] Tratamento de exceções
[ ] Estratégia definitiva de conexão com o navegador
[ ] Tecnologia definitiva da automação
[ ] Estrutura definitiva dos módulos
17. Próximo passo

O próximo estágio do projeto não é programar.

Primeiro será construído o:

Procedimento Operacional Manual do BTP

Esse documento deverá descrever, em detalhes, tudo aquilo que o usuário faz para executar cada análise manualmente.

Somente depois que o procedimento estiver completamente documentado será definida a implementação automatizada.

PROCESSO MANUAL
      ↓
MAPEAMENTO
      ↓
REGRAS
      ↓
ARQUITETURA
      ↓
PROVA DE CONCEITO
      ↓
CÓDIGO
      ↓
TESTES
      ↓
AGENTE BTP
Status

Projeto: Agente BTP
Status: Levantamento inicial
Login: Manual
MFA: Manual
Sistema externo: Claro / BTP
Interface: Web local
Execução: Automatizada após autenticação
Análises: Em definição
Dados coletados: Em definição
Saída: Em definição
