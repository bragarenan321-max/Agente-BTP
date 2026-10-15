# Relatório técnico — revisão de extração, auditoria e relatório (Agente BTP)

## 1. Como o agente lê o BTP (contexto necessário)
O agente **não clica na tela**. Ele lê o mesmo serviço de dados (OData) que alimenta o
Monitor de Alertas e a janela "Mensagens", pela aba já autenticada do usuário (somente GET).
Equivalências com o fluxo manual:

| Passo manual | No agente |
|---|---|
| Filtrar a WO e confirmar | Consulta da lista com `$filter=<campo WO> eq '<WO>'` + conferência de que cada linha devolvida é da WO pedida |
| Selecionar o registro / abrir "Mensagens" | Consulta dos itens do alerta (mesmo conjunto, navegação, filtro ou link do próprio BTP) |
| Ler material, fornecedor, quantidade, mensagem | Campos técnicos identificados no preparo (aba Resumo → "Origem dos campos no BTP") |

Por isso os "tempos" configuráveis são limites das **consultas** (não há tela para esperar carregar):
ao estourar, a aba aborta a consulta e a falha é registrada.

## 2. Divergência de quantidade (BTP = 2, Excel = 3)
**Causa principal (reproduzida em teste):** quando o registro do BTP traz dois campos de
quantidade — por exemplo `quantidade` (total = 3) e `quantidadeFaltante` (= 2) — o
reconhecimento de campos dava a mesma pontuação aos dois e desempatava pelo **nome mais curto**,
escolhendo a quantidade **total**. O defeito existia nos dois caminhos de preparo (pelos dados e
pelo dicionário `$metadata`).

**Correção na origem:** o nome passa a decidir entre campos do mesmo tipo
(`campos.peso_nome`): nomes com *falt/pend/saldo* vencem; *total/solicitada/disponível/
atendida/baixada/estoque…* perdem. Se houver vários campos de quantidade e nenhum for
claramente o faltante, a ocorrência fica **Incompleta** (ambígua), nunca validada. A configuração
antiga é descartada automaticamente (versão 3) e refeita com a regra certa. Os outros campos de
quantidade do registro ficam registrados na auditoria (ex.: "quantidade=3").

**Outras causas possíveis encontradas e corrigidas:**
1. *Duplicação:* duas linhas da lista para a mesma WO/consolidado traziam os mesmos itens duas
   vezes (totais dobravam). Agora cada item é identificado pela chave do BTP (ou pelo conteúdo
   completo) e conta uma vez; a repetição fica registrada.
2. *Resposta antiga:* a aba buscava os dados sem `cache: 'no-store'`; o navegador podia
   reaproveitar uma resposta. Agora toda leitura vai ao BTP.
3. *Filtro ignorado:* linhas de outra WO devolvidas pelo servidor eram aceitas. Agora são
   descartadas e avisadas.
4. *Fornecedor por formato:* um campo de 10 dígitos (ex.: pedido) podia ser tomado como
   fornecedor. Agora o nome do campo prevalece sobre o formato do valor.
5. *Mensagem x descrição:* "descricao" (do material) podia concorrer com "mensagem". Agora
   mensagem/erro/ocorrência prevalecem; o texto é gravado literalmente.
6. *Validação do preparo:* falha de comunicação durante o teste de um caminho era tratada como
   "WO fora do Monitor", permitindo gravar uma configuração sem conferência. Corrigido.

## 3. Auditoria por WO
- **Etapa A** — extração inicial (rodada 0), com dados brutos guardados.
- **Etapa B** — N auditorias (padrão 5). Cada uma é uma **nova leitura no BTP**. Com
  "auditorias independentes", as rodadas pares releem **cada item pelo seu endereço próprio**
  (outro caminho do serviço). Se o serviço não oferecer esse caminho (nenhum item encontrado por
  ele), a limitação é registrada e as rodadas seguem por nova consulta — sem acusar divergência
  falsa.
- **Etapa C** — comparação campo a campo (WO, material, fornecedor, quantidade, mensagem),
  quantidade de ocorrências, linhas da lista e duplicidades. Estados: Validado, Divergente,
  Incompleto, Não concluído (e "Não se aplica" para WO de formato inválido). Em divergência,
  leituras extras (configurável). **Maioria não é evidência:** o valor final só é registrado se
  o próprio BTP informar alteração do registro entre as leituras e as leituras posteriores
  concordarem; senão o campo fica vazio, destacado e com comentário.
- **Etapa D** — rastreabilidade na aba Auditoria (cada leitura, valores, resultado, data/hora).
- Com 0 auditorias configuradas, nada é marcado como Validado.

## 4. Arquivos alterados
`campos.py`, `analisador.py`, `autoconfig.py` (reconhecimento de campos; endereço de releitura),
`cliente.py` (leitura por WO, chave, duplicidade, tempos), `modelos.py` (origem/auditoria/estados),
`processador.py` (etapas A–C, tempo por WO e por lote, regras do lote), **novos** `auditoria.py`
e `preferencias.py`, `exportacao.py` (6 abas + conferência do arquivo), `agente.py`
(integração, painel, histórico), `servidor.py` (rotas de configuração), `ponte.py` e
`web/agente_btp.js` (tempo máximo por consulta, `no-store`), `web/index.html` (engrenagem,
painel, histórico), `config.py` (versão 3, releitura por chave), testes.

## 5. Testes
- Unidade: `python -m pytest testes` — 105 testes (reprodução do 2×3, zeros à esquerda, mensagem
  literal, WO↔material↔fornecedor, duplicidade, divergência, resolução por registro de alteração,
  campos ausentes, ambiguidade, falhas, tempos por consulta/WO/lote, interrupção, estrutura e
  conferência do Excel, persistência das configurações, API da engrenagem).
- Ponta a ponta no navegador (BTP simulado): `testes/e2e/rodar_aba.py` (6 cenários, incluindo o
  serviço do diagnóstico real com `quantidade`=3 e `quantidadeFaltante`=2 → Excel = 2),
  `rodar_extensao.py`, `rodar_combinacoes.py`.

## 6. Revisão independente (pós-entrega) — 9 defeitos corrigidos
1. Sem chave do BTP, uma mudança de quantidade partia o item em dois (com os dois valores). Agora a
   identidade por conteúdo ignora quantidade, mensagem e datas: a mudança vira divergência.
2. Itens iguais de alertas diferentes eram fundidos. A identidade inclui o alerta de origem; itens
   iguais na mesma consulta são numerados (não descartados).
3. Item não relido (sem chave) saía "Validado". Agora cada ocorrência conta só as auditorias que a
   conferiram; sem chave com valor, não há releitura.
4. "Evidência" de alteração aceitava campos que não são data (ex.: alteradoPor) e comparava datas
   como texto. Agora só datas reais, em ordem, contando apenas leituras posteriores à alteração.
5. WO aparecia "Não consta no Monitor" com ocorrência exportada. A situação considera todas as leituras.
6. Mensagem iniciada por "=" era gravada como fórmula. Agora é texto, e a conferência acusa fórmula.
7. Caractere de controle derrubava o Excel. Agora é removido, com comentário na célula.
8. Releitura recusada com 400 deixava todas as WOs "Não concluído". Agora cai para nova consulta.
9. Tempos com decimal eram truncados; "nan" passava. Corrigido.
Testes: 105 de unidade + 6 cenários ponta a ponta.

## 7. Limitações
- Não houve acesso ao BTP real nos testes: os nomes reais dos campos não são conhecidos; a
  aba Resumo mostra de qual campo técnico cada coluna foi lida, para conferência no primeiro uso.
- A releitura por chave depende de o serviço permitir; quando não permite, as auditorias são
  novas consultas pelo mesmo caminho (registrado no Excel).
- Leituras repetidas do mesmo serviço confirmam consistência, não a correção do mapeamento; por
  isso a conferência da origem dos campos no primeiro uso é importante.
- 5 auditorias multiplicam por ~6 as consultas ao BTP; para lotes grandes, ajuste na engrenagem.
