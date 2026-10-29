"""Catálogo dos diagramas do e-book. Cada um reflete o código real (arquivos e funções citados).

CATALOGO[nome] = (título da figura, função que devolve o diagrama)
"""
from __future__ import annotations

from diagramas_base import Fluxo, Sequencia


def contexto():
    f = Fluxo("Contexto do Agente BTP", 3, 3, cel_l=250, cel_a=104, caixa_l=178, caixa_a=62)
    f.no("usuario", 0, 0, "Usuário (operador)\n(cola WOs, confere, baixa)", "externo")
    f.no("tela", 1, 0, "Tela do agente\n(index.html no navegador)", "processo")
    f.no("programa", 1, 1, "Programa local\nPython + Flask\n(127.0.0.1:8767)", "marca")
    f.no("aba", 2, 1, "Aba do BTP no Chrome\n(sessão já logada\n+ extensão Agente BTP)", "externo")
    f.no("btp", 2, 2, "Serviço OData do Monitor\n(SAP BTP · Cloud Foundry)", "externo")
    f.no("disco", 0, 1, "Disco local\n(SQLite · JSON · XLSX)", "dado")
    f.no("excel", 0, 2, "Relatório Excel\nAnálise N - X WOs.xlsx", "resultado")
    f.seta("usuario", "tela", "usa")
    f.seta("tela", "programa", "HTTP local (JSON)", dupla=True)
    f.seta("aba", "programa", "tarefas", dupla=True)
    f.seta("aba", "btp", "GET com a sessão\ndo navegador", dupla=True)
    f.seta("programa", "disco", "grava")
    f.seta("disco", "excel", "gera")
    return f.legenda("processo", "externo", "dado", "resultado", "marca")


def antes_depois():
    f = Fluxo("Processo manual x processo com o agente", 2, 7.5, cel_l=300, cel_a=74, caixa_l=250, caixa_a=50)
    f.grupo(0, 0, 0, 6.45, "Manual — repetido para CADA WO", "erro")
    f.grupo(1, 0, 1, 6.45, "Com o Agente BTP — uma vez por lista", "resultado")
    passos = ["Abrir o tile Monitor de alertas", "Abrir o filtro da coluna WO", "Pesquisar e marcar a WO, OK",
              "Clicar na linha filtrada", "Abrir a janela Mensagens", "Copiar material, fornecedor,\nquantidade e mensagem"]
    for i, t in enumerate(passos):
        f.no(f"m{i}", 0, i + 0.3, t, "processo")
        if i:
            f.seta(f"m{i - 1}", f"m{i}")
    f.no("m6", 0, 6.3, "Próxima WO: volta ao passo 2", "erro")
    f.seta("m5", "m6")
    agente = ["Colar a lista ou importar .xlsx", "Clicar em Executar análise", "Conferir a amostra (só 1ª vez)",
              "Acompanhar barra e indicadores", "O agente lê e confere\ncada WO sozinho", "Baixar o Excel auditado"]
    for i, t in enumerate(agente):
        f.no(f"a{i}", 1, i + 0.3, t, "resultado" if i == 5 else "processo")
        if i:
            f.seta(f"a{i - 1}", f"a{i}")
    return f


def linha_tempo():
    f = Fluxo("Linha do tempo do desenvolvimento (histórico Git)", 4, 3, cel_l=178, cel_a=108, caixa_l=160, caixa_a=76)
    marcos = [
        ((0, 0), "01/10\nRepositório e README\n(especificação inicial)"),
        ((1, 0), "13/10\nMapeamento do processo\n+ 1ª versão (Excel)"),
        ((2, 0), "13/10\nConexão pela aba\njá aberta (favorito)"),
        ((3, 0), "14/10\nExtensão do Chrome,\nhistórico e auditoria"),
        ((3, 1), "15/10\nFonte real: Pendencias\n→ UltimoRetornoView"),
        ((2, 1), "21/10\nBase SQLite, retomada,\nsessão e lote paralelo"),
        ((1, 1), "22/10\nEscala: 13.623 WOs\nsem travar"),
        ((0, 1), "27/10\n1ª execução real →\ncorreções e redesign"),
        ((0, 2), "27/10\nItens repetidos e\ncélulas com \"-\""),
        ((1, 2), "28/10\nTipo Material, tabela\nde valores, menu lateral"),
        ((2, 2), "29/10\nPlanilha só com\n\"Estoque indisponível\""),
    ]
    for i, ((c, l), t) in enumerate(marcos):
        f.no(f"t{i}", c, l, t, "marca" if i in (4, 7) else "processo")
        if i:
            f.seta(f"t{i - 1}", f"t{i}")
    f.no("hoje", 3, 2, "Situação atual\n(29/10/2026)\nem uso operacional", "resultado")
    f.seta("t10", "hoje")
    return f


def visao_geral():
    f = Fluxo("Fluxo principal de uma análise", 3, 7, cel_l=235, cel_a=84, caixa_l=178, caixa_a=56)
    f.no("inicio", 1, 0, "Usuário informa as WOs\n(campo ou planilha .xlsx)", "inicio")
    f.no("api", 1, 1, "POST /api/executar\n(servidor.py → agente.enviar)")
    f.no("fonte", 1, 2, "Fonte de dados\nconfirmada?", "decisao")
    f.no("prep", 0, 2, "Preparação automática\n+ conferência da amostra")
    f.no("motor", 1, 3, "Motor: grupos de WOs\nem paralelo (motor.py)")
    f.no("proc", 1, 4, "Extração + auditorias\n(processador.py)")
    f.no("btp", 2, 4, "Aba do BTP →\nServiço OData", "externo")
    f.no("sessao", 0, 4, "Sessão caiu: pausa\ne retoma sozinho", "erro")
    f.no("loja", 1, 5, "Cada WO gravada ao\nterminar (loja.py, SQLite)", "dado")
    f.no("fim", 1, 6, "Excel conferido + histórico\n+ diagnóstico", "resultado")
    f.seta("inicio", "api")
    f.seta("api", "fonte")
    f.seta("fonte", "prep", "não")
    f.seta("prep", "motor", "pronta", lado_de="s", lado_para="o")
    f.seta("fonte", "motor", "sim")
    f.seta("motor", "proc")
    f.seta("proc", "btp", "GET", dupla=True)
    f.seta("proc", "sessao", "sessão", tracejada=True)
    f.seta("proc", "loja")
    f.seta("loja", "fim", "fila vazia")
    return f.legenda("inicio", "processo", "decisao", "externo", "dado", "erro", "resultado")


def arquitetura():
    f = Fluxo("Arquitetura: componentes e ligações", 4, 5, cel_l=182, cel_a=92, caixa_l=146, caixa_a=60)
    f.no("tela", 0, 0, "Tela do agente\nweb/index.html", "externo")
    f.no("servidor", 1, 0, "servidor.py\nFlask · rotas /api")
    f.no("agente", 2, 0, "agente.py\norquestrador + fila", "marca")
    f.no("prep", 3, 0, "Preparação da fonte\nautoconfig · exploracao\nsondagem · analisador")
    f.no("prefs", 1, 1, "preferencias.py\nconfig.py · materiais.py", "dado")
    f.no("motor", 2, 1, "motor.py\nparalelismo + sessão")
    f.no("diag", 3, 1, "diagnostico.py\nmotivo + evidência")
    f.no("cliente", 1, 2, "cliente.py + odata.py\nGET OData + erros")
    f.no("proc", 2, 2, "processador.py\nextração + auditorias")
    f.no("aud", 3, 2, "auditoria.py\ncompara leituras")
    f.no("ponte", 1, 3, "ponte.py\nfila de tarefas")
    f.no("js", 0, 3, "agente_btp.js\nna aba do BTP", "externo")
    f.no("odata", 0, 4, "Serviço OData\ndo Monitor (BTP)", "externo")
    f.no("loja", 2, 3, "loja.py\nSQLite por WO", "dado")
    f.no("exp", 3, 3, "exportacao.py\nExcel + conferência")
    f.no("hist", 3, 4, "historico.py\nhistorico.json", "dado")
    f.seta("tela", "servidor", "HTTP", dupla=True)
    f.seta("servidor", "agente")
    f.seta("agente", "prep")
    f.seta("servidor", "prefs", "lê/grava")
    f.seta("agente", "motor")
    f.seta("motor", "diag")
    f.seta("motor", "proc")
    f.seta("proc", "aud")
    f.seta("proc", "cliente")
    f.seta("cliente", "ponte")
    f.seta("ponte", "js", dupla=True)
    f.seta("js", "odata", "GET", dupla=True)
    f.seta("proc", "loja", "resultado")
    f.seta("loja", "exp")
    f.seta("exp", "hist", "arquivo")
    return f.legenda("processo", "externo", "dado", "marca")


def camadas():
    f = Fluxo("Camadas e dependências entre módulos", 1, 6, cel_l=560, cel_a=70, caixa_l=520, caixa_a=50)
    nomes = [
        ("Apresentação — web/index.html · servidor.py", "externo"),
        ("Orquestração — agente.py · motor.py", "marca"),
        ("Regras do domínio — processador.py · auditoria.py · exportacao.py · materiais.py", "processo"),
        ("Acesso aos dados — cliente.py · odata.py · ponte.py · web/agente_btp.js", "processo"),
        ("Persistência — loja.py · historico.py · config.py · preferencias.py", "dado"),
        ("Base comum — modelos.py · diagnostico.py · campos.py · entrada.py", "dado"),
    ]
    for i, (t, tipo) in enumerate(nomes):
        f.no(f"c{i}", 0, i, t, tipo)
        if i:
            f.seta(f"c{i - 1}", f"c{i}", "usa")
    return f


def inicializacao():
    f = Fluxo("Inicialização do programa", 2, 9, cel_l=280, cel_a=72, caixa_l=220, caixa_a=48)
    f.no("bat", 0, 0, "Duplo clique em\nAbrir Agente BTP.bat", "inicio")
    f.no("mata", 0, 1, "Encerra versão antiga\nescutando a porta 8767")
    f.no("venv", 0, 2, ".venv existe?", "decisao")
    f.no("cria", 1, 2, "Cria o ambiente\n(py -3 -m venv .venv)")
    f.no("pip", 0, 3, "pip install -r requirements.txt\n(flask, openpyxl)")
    f.no("main", 0, 4, "python -m agente_btp\n(__main__.main)")
    f.no("log", 0, 5, "Desliga QuickEdit e grava\nlog em logs/agente_btp.log")
    f.no("agente", 0, 6, "Agente(): Loja SQLite, PonteAba,\nthread de comandos (_laco)")
    f.no("app", 0, 7, "criar_app(): rotas, versão,\nsincroniza a extensão")
    f.no("pronto", 0, 8, "Servidor em 127.0.0.1:8767\n+ navegador abre a tela", "resultado")
    f.no("antiga", 1, 6, "config/api.json de versão\nanterior? → descartada", "dado")
    f.seta("bat", "mata")
    f.seta("mata", "venv")
    f.seta("venv", "cria", "não")
    f.seta("cria", "pip", lado_de="s", lado_para="l")
    f.seta("venv", "pip", "sim")
    f.seta("pip", "main")
    f.seta("main", "log")
    f.seta("log", "agente")
    f.seta("agente", "antiga", tracejada=True)
    f.seta("agente", "app")
    f.seta("app", "pronto")
    return f


def conexao_aba():
    f = Fluxo("Como a aba do BTP se conecta ao programa", 3, 5, cel_l=210, cel_a=86, caixa_l=182, caixa_a=58)
    f.no("login", 0, 0, "Usuário abre o BTP\n(login + MFA manuais)", "externo")
    f.no("fav", 1, 0, "Alternativa: favorito\nbusca /agente_btp.js", "nota")
    f.no("nobtp", 0, 1, "no_btp.js (extensão)\npede a conexão")
    f.no("fundo", 1, 1, "fundo.js injeta\nagente_btp.js (MAIN)")
    f.no("ola", 2, 1, "agente_btp.js\nPOST /api/agente/ola")
    f.no("resp", 2, 2, "Programa local\nrespondeu?", "decisao")
    f.no("espera", 1, 2, "Aguarda 5 s e tenta\nde novo (pela extensão)")
    f.no("trab", 2, 3, "Abre N trabalhadores\n(N = paralelismo, até 6)")
    f.no("ok", 2, 4, "Selo na aba:\n\"Agente BTP: conectado\"", "resultado")
    f.seta("login", "nobtp")
    f.seta("nobtp", "fundo")
    f.seta("fundo", "ola")
    f.seta("fav", "ola", lado_de="l", lado_para="n", tracejada=False)
    f.seta("ola", "resp")
    f.seta("resp", "espera", "não")
    f.seta("espera", "fundo", "repete", lado_de="n", lado_para="s")
    f.seta("resp", "trab", "sim")
    f.seta("trab", "ok")
    return f


def seq_consulta():
    s = Sequencia("Uma consulta ao BTP passando pela aba", [
        ("cliente", "ClienteBTP\ncliente.py", "processo"),
        ("ponte", "PonteAba\nponte.py", "processo"),
        ("flask", "Servidor Flask\n/api/agente/ciclo", "processo"),
        ("aba", "Trabalhador na aba\nagente_btp.js", "externo"),
        ("btp", "Serviço OData\n(BTP)", "externo")], larg_col=150, passo=36)
    s.msg("aba", "flask", "ciclo: pede tarefa")
    s.msg("flask", "ponte", "proxima_tarefa()")
    s.nota("ponte", "fila vazia: o pedido espera até 15 s (long-poll)", ate="aba")
    s.msg("cliente", "ponte", "get(url): tarefa na fila")
    s.msg("ponte", "flask", "{id, url, timeout}")
    s.msg("flask", "aba", "resposta do ciclo")
    s.msg("aba", "btp", "fetch GET (same-origin)")
    s.msg("btp", "aba", "200 + JSON OData", tracejada=True)
    s.msg("aba", "flask", "próximo ciclo leva a resposta")
    s.msg("flask", "ponte", "entregar_resposta()")
    s.msg("ponte", "cliente", "Resposta(status, tipo, corpo)", tracejada=True)
    s.msg("cliente", "cliente", "classificar() + extrair_pagina()")
    return s


def comunicacao():
    f = Fluxo("Canais de comunicação e o que passa por eles", 2, 4, cel_l=330, cel_a=104, caixa_l=210, caixa_a=58)
    f.no("tela", 0, 0, "Tela do agente\n(navegador do usuário)", "externo")
    f.no("prog", 0, 1, "Programa local\nFlask · 127.0.0.1:8767", "marca")
    f.no("aba", 0, 2, "Aba do BTP\n(agente_btp.js)", "externo")
    f.no("btp", 0, 3, "Serviço OData\ndo Monitor de alertas", "externo")
    f.no("disco", 1, 1, "Disco local\nSQLite · JSON · XLSX", "dado")
    f.no("nota", 1, 3, "Nenhum dado sai do\ncomputador além das\nconsultas feitas pela aba", "nota")
    f.seta("tela", "prog", "HTTP + JSON\n/api/estado a cada 1 s", dupla=True)
    f.seta("aba", "prog", "HTTP loopback (127.0.0.1)\nCORS: só a origem do BTP", dupla=True)
    f.seta("aba", "btp", "HTTPS · só GET\ncookie da sessão", dupla=True)
    f.seta("prog", "disco", "grava/lê")
    return f


def preparacao():
    f = Fluxo("Preparação da fonte de dados (primeira vez)", 3, 8, cel_l=205, cel_a=76, caixa_l=182, caixa_a=52)
    f.no("inicio", 1, 0, "Análise pedida", "inicio")
    f.no("tem", 1, 1, "config/api.json\nexiste?", "decisao")
    f.no("confere", 2, 1, "Conferência da amostra\n(se não confirmada)")
    f.no("r0", 1, 2, "0º Receita confirmada:\nPendencias → UltimoRetornoView")
    f.no("grava", 2, 2, "Grava config/api.json\n(origem + campos)", "dado")
    f.no("r1", 1, 3, "1º Exploração pelos dados\n(exploracao.py)")
    f.no("r2", 1, 4, "2º Candidatos do $metadata\n(autoconfig.py)")
    f.no("r3", 1, 5, "3º Sondagem na linha\ndo alerta (sondagem.py)")
    f.no("r4", 1, 6, "4º Observa a tela do Monitor\n(analisador.py, até 5 min)")
    f.no("falha", 1, 7, "Não preparou: avisa e\noferece o diagnóstico", "erro")
    f.no("nota", 2, 4.5, "Cada caminho só serve se\ntrouxer os itens da janela\nMensagens da própria WO\n(_validar = 2); o primeiro\nque servir é gravado", "nota", alt=1.6)
    f.no("nota0", 0, 3.5, "Testes com as 5\nprimeiras WOs válidas\nda própria lista", "nota", alt=1.3)
    f.seta("inicio", "tem")
    f.seta("tem", "confere", "sim")
    f.seta("tem", "r0", "não")
    f.seta("r0", "grava", "serviu")
    f.seta("grava", "confere")
    for a, b in (("r0", "r1"), ("r1", "r2"), ("r2", "r3"), ("r3", "r4")):
        f.seta(a, b, "não serviu")
    f.seta("r4", "falha", "tempo esgotado")
    return f


def conferencia_amostra():
    f = Fluxo("Conferência da amostra pelo usuário", 3, 4, cel_l=210, cel_a=88, caixa_l=186, caixa_a=58)
    f.no("mostra", 1, 0, "Mostra uma amostra real\n(até 6 itens de 1 WO)")
    f.no("resp", 1, 1, "Resposta do usuário", "decisao")
    f.no("sim", 0, 2, "Sim: fonte confirmada\n+ mensagem escolhida", "resultado")
    f.no("outra", 1, 2, "Não — outra fonte:\nesta é recusada para sempre", "erro")
    f.no("mostrar", 2, 2, "Não — vou mostrar:\naprende pela tela do BTP")
    f.no("nova", 1, 3, "Nova preparação e nova\namostra (no máx. 6 fontes)")
    f.no("lote", 0, 3, "A análise começa", "inicio")
    f.seta("mostra", "resp")
    f.seta("resp", "sim", "sim", lado_de="o", lado_para="n")
    f.seta("resp", "outra", "outra")
    f.seta("resp", "mostrar", "mostrar", lado_de="l", lado_para="n")
    f.seta("outra", "nova")
    f.seta("mostrar", "nova", lado_de="s", lado_para="l")
    f.seta("sim", "lote")
    return f


def extracao():
    f = Fluxo("Extração de uma WO (consulta individual)", 3, 7, cel_l=210, cel_a=80, caixa_l=186, caixa_a=54)
    f.no("wo", 1, 0, "WO 12345|678901234\n(exemplo fictício)", "inicio")
    f.no("lista", 1, 1, "Lista: Pendencias\n$filter=workOrderID eq 'WO'")
    f.no("linhas", 2, 1, "Linhas do Monitor\n(1 por consolidado)", "dado")
    f.no("tem", 1, 2, "Alguma linha\nda própria WO?", "decisao")
    f.no("nao", 0, 2, "Sem alerta no Monitor\n(NAO_ENCONTRADA)", "nota")
    f.no("agrupa", 1, 3, "Junta linhas que levam à\nmesma consulta de itens")
    f.no("itens", 1, 4, "Itens: UltimoRetornoView\nconsolidID + WO + status 'E'")
    f.no("odata", 2, 4, "Serviço OData\n(JSON V4: value[])", "externo")
    f.no("ocorr", 1, 5, "ocorrencia(): campos →\nvalores + chave do item")
    f.no("leitura", 1, 6, "Leitura (itens por chave)\n→ auditoria", "resultado")
    f.seta("wo", "lista")
    f.seta("lista", "linhas", "resposta")
    f.seta("lista", "tem")
    f.seta("tem", "nao", "não")
    f.seta("tem", "agrupa", "sim")
    f.seta("agrupa", "itens")
    f.seta("itens", "odata", "GET", dupla=True)
    f.seta("itens", "ocorr")
    f.seta("ocorr", "leitura")
    return f


def lote():
    f = Fluxo("Leitura em lote com verificação de equivalência", 3, 6, cel_l=205, cel_a=80, caixa_l=182, caixa_a=54)
    f.no("grupo", 1, 0, "Grupo de até 20 WOs\n(tamanho_lote)", "inicio")
    f.no("lista", 1, 1, "1 consulta da lista:\nfiltro OR das 20 WOs")
    f.no("itens", 1, 2, "Itens de todas as linhas\nem poucas consultas (OR)")
    f.no("reparte", 1, 3, "Reparte por WO pelas\nmesmas regras (_montar)")
    f.no("igual", 1, 4, "Igual à leitura\nindividual?", "decisao")
    f.no("desliga", 0, 4, "Desativa o lote na análise\n→ grupo refeito WO a WO", "erro")
    f.no("segue", 1, 5, "Segue em lote\n(auditorias também)", "resultado")
    f.no("nota", 2, 4, "3 primeiros grupos: todas\nas WOs; depois, 1 com\nitens + 1 vazia por grupo", "nota", alt=1.2)
    f.seta("grupo", "lista")
    f.seta("lista", "itens")
    f.seta("itens", "reparte")
    f.seta("reparte", "igual")
    f.seta("igual", "desliga", "não")
    f.seta("igual", "segue", "sim")
    return f


def auditoria():
    f = Fluxo("Decisão da auditoria para cada item", 3, 6, cel_l=210, cel_a=82, caixa_l=186, caixa_a=54)
    f.no("leituras", 1, 0, "Leitura 0 (extração)\n+ auditorias 1..N", "inicio")
    f.no("compara", 1, 1, "Compara por chave do\nitem, campo a campo")
    f.no("muda", 1, 2, "Sumiu, apareceu ou\nalgum campo mudou?", "decisao")
    f.no("extras", 2, 2, "+3 leituras extras\nindividuais")
    f.no("resolve", 2, 3, "BTP registra alteração\ne leituras posteriores\nconcordam?", "decisao", alt=1.15)
    f.no("div", 2, 4.4, "DIVERGENTE: campo \"-\"\n+ \"conferir no BTP\"", "erro")
    f.no("vazio", 1, 3, "Campo vazio ou\nquantidade ambígua?", "decisao")
    f.no("inc", 0, 3, "INCOMPLETO", "erro")
    f.no("todas", 1, 4, "Todas as auditorias\nprevistas feitas?", "decisao")
    f.no("nc", 0, 4, "NÃO CONCLUÍDO", "erro")
    f.no("val", 1, 5, "VALIDADO", "resultado")
    f.seta("leituras", "compara")
    f.seta("compara", "muda")
    f.seta("muda", "extras", "sim")
    f.seta("extras", "resolve")
    f.seta("resolve", "div", "não")
    f.seta("resolve", "vazio", "sim")
    f.seta("muda", "vazio", "não")
    f.seta("vazio", "inc", "sim")
    f.seta("vazio", "todas", "não")
    f.seta("todas", "nc", "não")
    f.seta("todas", "val", "sim")
    return f.legenda("decisao", "erro", "resultado")


def transformacao():
    f = Fluxo("Do JSON do BTP à linha da planilha", 2, 6, cel_l=290, cel_a=72, caixa_l=260, caixa_a=50)
    f.no("json", 0, 0, "Corpo da resposta (texto JSON)\n{\"value\": [ {...}, {...} ]}", "dado")
    f.no("pag", 0, 1, "odata.extrair_pagina()\n→ lista de registros (dicts)")
    f.no("campos", 0, 2, "obter_campo() pelos nomes\nmapeados em config/api.json")
    f.no("conv", 0, 3, "como_texto() / como_quantidade()\n→ valores + chave do item")
    f.no("cons", 0, 4, "auditoria.consolidar()\n→ LinhaMensagem (valores finais)")
    f.no("linha", 0, 5, "Linha da aba Dados\n(com \"-\" onde não há valor)", "resultado")
    f.no("n1", 1, 1, "V2: {\"d\":{\"results\":[...]}}\nV4: {\"value\":[...]}", "nota")
    f.no("n2", 1, 2, "material ← \"material\"\nquantidade ← \"quantidade\"", "nota")
    f.no("n3", 1, 3, "\"000000000022026169\" fica texto\n\"1e+0\" → 1 (Decimal exato)", "nota")
    f.no("n4", 1, 4, "notas por campo: divergente,\nnão obtido, ambígua", "nota")
    for a, b in (("json", "pag"), ("pag", "campos"), ("campos", "conv"), ("conv", "cons"), ("cons", "linha")):
        f.seta(a, b)
    for a, b in (("pag", "n1"), ("campos", "n2"), ("conv", "n3"), ("cons", "n4")):
        f.seta(a, b, tracejada=False)
    return f


def estados_wo():
    f = Fluxo("Estados de uma WO na base (loja.py)", 3, 4.3, cel_l=250, cel_a=82, caixa_l=150, caixa_a=52)
    f.no("pend", 0, 1.5, "pendente", "inicio")
    f.no("and", 1, 1.5, "em_andamento")
    f.no("val", 2, 0, "validada", "resultado")
    f.no("div", 2, 1, "divergente", "alerta")
    f.no("inc", 2, 2, "incompleta", "alerta")
    f.no("fal", 2, 3, "falha", "erro")
    f.seta("pend", "and", "trabalhador\npega a WO")
    for d in ("val", "div", "inc", "fal"):
        f.seta("and", d, lado_de="l", lado_para="o")
    f.seta("and", "pend", "sessão caiu /\ninterrupção", lado_de="n", lado_para="n", via=[(1, 0.55), (0, 0.55)],
           tracejada=True)
    f.seta("fal", "and", "Retomar", lado_de="s", lado_para="s", via=[(2, 3.62), (1, 3.62)])
    return f


def geracao_planilha():
    f = Fluxo("Geração e conferência do Excel (exportacao.py)", 2, 9, cel_l=290, cel_a=66, caixa_l=262, caixa_a=46)
    passos = [
        ("dados", "Loja: resultados(run_id, leve=True)", "dado"),
        ("unicos", "itens_unicos(): junta itens idênticos\nque o BTP traz mais de uma vez", "processo"),
        ("filtro", "entra_na_planilha(): só a mensagem\nconfigurada (\"Estoque indisponível\")", "processo"),
        ("aba1", "Aba Dados: 1 linha por item\n(WO sem item: 1 linha com \"-\")", "processo"),
        ("aba2", "Aba Materiais: soma por material\n× valor unitário da tabela", "processo"),
        ("aba3", "Abas Análise, Rastreio (oculta)\ne Tabela de valores (oculta)", "processo"),
        ("grava", "Grava em escrita contínua\n(openpyxl write-only)", "processo"),
        ("confere", "Reabre e confere\ncélula a célula: ok?", "decisao"),
        ("fim", "Análise N - X WOs.xlsx\n+ registro no histórico", "resultado"),
    ]
    for i, (ident, t, tipo) in enumerate(passos):
        f.no(ident, 0, i, t, tipo)
        if i:
            f.seta(passos[i - 1][0], ident, "sim" if ident == "fim" else "")
    f.no("outros", 1, 2, "Outros itens da WO →\nObservação da 1ª linha", "nota")
    f.no("tabela", 1, 4, "Tabela de valores importada\n(config/tabela_materiais.json)", "dado")
    f.no("erro", 1, 7, "Erro na planilha: aviso na\ntela e no histórico", "erro")
    f.seta("filtro", "outros")
    f.seta("tabela", "aba2")
    f.seta("confere", "erro", "não")
    return f


def abas_excel():
    f = Fluxo("Estrutura do arquivo Análise N - X WOs.xlsx", 3, 2, cel_l=208, cel_a=150, caixa_l=190, caixa_a=126)
    f.grupo(0, 0, 2, 1, "Análise N - X WOs.xlsx")
    f.no("dados", 0, 0, "Dados (visível)\n· WO · Material SAP\n· Tipo Material · Fornecedor\n· Quantidade · Mensagem\n· Observação · Status", "processo")
    f.no("mat", 1, 0, "Materiais (visível)\n· Material SAP · Tipo\n· Quantidade total\n· Valor total · Valor unitário\n· Observação · Total", "processo")
    f.no("ana", 2, 0, "Análise (visível)\n· Números da análise\n· Materiais recorrentes\n· Mensagens\n· Situação de cada WO", "processo")
    f.no("ras", 0, 1, "Rastreio (oculta)\n· cada leitura feita ao BTP\n· rodada, tipo, horário\n· comparação com a 1ª", "dado")
    f.no("tab", 1, 1, "Tabela de valores (oculta)\n· Material SAP (18 dígitos)\n· Tipo Material\n· Valor unitário (R$)", "dado")
    f.no("ver", 2, 1, "Conferência automática\nDados, Materiais e Tabela\nrelidas após gravar", "resultado")
    f.seta("dados", "mat", "soma")
    f.seta("mat", "ana")
    return f


def erros():
    f = Fluxo("Classificação e tratamento de falhas", 4, 6, cel_l=178, cel_a=84, caixa_l=160, caixa_a=56)
    f.no("raiz", 1.5, 0, "Resposta do GET\n(cliente.classificar)", "inicio", larg=1.2)
    f.no("s1", 0, 1, "Redirecionado, 401, 403,\nHTML de login\n→ SessaoExpirada", "erro")
    f.no("x1", 1, 1, "Exceção do programa\n→ excecao_app", "erro")
    f.no("c1", 2, 1, "429, 5xx, rede, tempo\n→ FalhaComunicacao", "erro")
    f.no("e1", 3, 1, "404, outro 4xx,\nJSON inválido\n→ ErroConsulta", "erro")
    f.no("s2", 0, 2, "Motor faz a sonda\nda sessão")
    f.no("s3", 0, 3, "Sonda passa?", "decisao")
    f.no("s4", 0, 4, "Pausa: WOs em andamento\nvoltam a pendente")
    f.no("s5", 0, 5, "Login na aba →\nretoma sozinho", "resultado")
    f.no("s6", 1, 3, "Só esta WO falha;\nanálise continua")
    f.no("x2", 1, 2, "Grupo vira falha com\ntraceback; segue")
    f.no("c2", 2, 2, "Repete até 2×\n+ ritmo adaptativo")
    f.no("c3", 2, 3, "Persistiu?", "decisao")
    f.no("c4", 2, 4, "WO = falha\n(Retomar refaz)", "erro")
    f.no("e2", 3, 2, "Em lote: grupo\nrefeito WO a WO")
    f.no("e3", 3, 3, "Individual: WO = falha\ncom motivo", "erro")
    for d in ("s1", "x1", "c1", "e1"):
        f.seta("raiz", d, lado_de="s", lado_para="n")
    f.seta("s1", "s2")
    f.seta("s2", "s3")
    f.seta("s3", "s4", "não")
    f.seta("s4", "s5")
    f.seta("s3", "s6", "sim")
    f.seta("x1", "x2")
    f.seta("c1", "c2")
    f.seta("c2", "c3")
    f.seta("c3", "c4", "sim")
    f.seta("e1", "e2")
    f.seta("e2", "e3")
    return f


def motor_laco():
    f = Fluxo("Laço do motor: passadas, sondas e pausas (motor.py)", 3, 7, cel_l=210, cel_a=78, caixa_l=188, caixa_a=52)
    f.no("ini", 1, 0, "executar(): em andamento\nvoltam para a fila", "inicio")
    f.no("sonda", 1, 1, "Sonda antes de começar\n(_verificar_sessao_inicial)")
    f.no("fila", 1, 2, "Há WOs a processar?", "decisao")
    f.no("fim", 0, 2, "Encerra: concluída", "resultado")
    f.no("passada", 1, 3, "_passada(): grupos em fila,\nN trabalhadores em paralelo")
    f.no("periodica", 2, 3, "Sonda a cada 60 s\nou 100 WOs", "nota")
    f.no("res", 1, 4, "Como terminou\na passada?", "decisao")
    f.no("pausa", 0, 5, "Pausa do usuário: aguarda\nRetomar e volta ao início")
    f.no("sess", 2, 5, "Sessão caiu: espera aba/sonda\n(a cada 30 s) e volta ao início", "erro")
    f.no("parar", 1, 6, "Parar ou tempo máximo:\ninterrompida (retomável)", "erro")
    f.seta("ini", "sonda")
    f.seta("sonda", "fila")
    f.seta("fila", "fim", "não")
    f.seta("fila", "passada", "sim")
    f.seta("passada", "periodica", tracejada=False)
    f.seta("passada", "res")
    f.seta("res", "pausa", "pausa", lado_de="o", lado_para="n")
    f.seta("res", "sess", "sessão", lado_de="l", lado_para="n")
    f.seta("res", "parar", "fim")
    return f


def testes():
    f = Fluxo("Como o projeto é verificado", 4, 2, cel_l=178, cel_a=130, caixa_l=160, caixa_a=104)
    f.no("unit", 0, 0, "pytest (unidade e\nintegração)\n164 testes\n8 arquivos test_*.py", "processo")
    f.no("falsos", 1, 0, "Serviços falsos\nservico_real_falso\nbtp_lote_falso\nsap_falso (HTTP)", "dado")
    f.no("e2e", 2, 0, "Ponta a ponta\n(Chromium)\nrodar_tela · rodar_sessao\nrodar_aba · rodar_extensao", "processo")
    f.no("med", 3, 0, "Medição\nmedir_desempenho.py\n(antes x depois)", "processo")
    f.no("real", 1, 1, "Validação no BTP real\nconferência da amostra\n+ diagnóstico .json", "externo", larg=1.9)
    f.seta("unit", "falsos", "usa")
    f.seta("falsos", "e2e", "usa")
    f.seta("e2e", "med")
    return f


def implantacao():
    f = Fluxo("Distribuição e implantação no computador do usuário", 3, 3, cel_l=262, cel_a=96, caixa_l=150, caixa_a=60)
    f.no("zip", 0, 0, "Pacote Agente-BTP.zip\n(git archive)", "dado")
    f.no("pasta", 0, 1, "Extrair por cima da pasta\n(mantém dados/ e config/)")
    f.no("bat", 0, 2, "Abrir Agente BTP.bat\n(.venv + pip install)")
    f.no("ext", 1, 0, "Chrome: extensão carregada\nsem compactação (extensao/)", "externo")
    f.no("btp", 2, 0, "BTP no Chrome\nlogin manual + MFA", "externo")
    f.no("ag", 1, 2, "Agente em execução\n127.0.0.1:8767", "marca")
    f.no("con", 2, 2, "Aba conectada\n(selo na página)", "resultado")
    f.seta("zip", "pasta")
    f.seta("pasta", "bat")
    f.seta("bat", "ag")
    f.seta("ext", "btp", "injeta o agente")
    f.seta("btp", "con")
    f.seta("ag", "con", "long-poll", dupla=True)
    return f


def mapa_final():
    f = Fluxo("Mapa integrado do sistema (com os capítulos de referência)", 4, 5, cel_l=178, cel_a=92, caixa_l=160, caixa_a=62)
    f.grupo(0, 0, 0, 4, "Usuário, tela e relatório")
    f.grupo(1, 1, 2, 4, "Programa local (Python)")
    f.grupo(3, 1, 3, 4, "Diagnóstico e BTP")
    f.no("user", 0, 0, "Usuário\n(cap. 1, 23)", "externo")
    f.no("tela", 0, 1, "Tela: Análise,\nHistórico, Config.\n(cap. 23, 25)")
    f.no("ajuda", 0, 2, "Ajuda guiada (F1)\n(cap. 23)", "nota")
    f.no("xlsx", 0, 4, "Excel conferido\n(cap. 15, 25)", "resultado")
    f.no("serv", 1, 1, "servidor.py\n(cap. 10, 11)")
    f.no("ag", 1, 2, "agente.py: preparo,\nconferência, roteiro\n(cap. 12, 16)", "marca")
    f.no("loja", 1, 3, "loja.py (SQLite)\n(cap. 14, 26)", "dado")
    f.no("exp", 1, 4, "exportacao.py\n(cap. 15)")
    f.no("motor", 2, 2, "motor.py: paralelismo\ne sessão (cap. 16, 26)")
    f.no("proc", 2, 3, "processador.py +\nauditoria.py\n(cap. 13, 14)")
    f.no("cli", 2, 4, "cliente.py + ponte.py\n(cap. 11, 12)")
    f.no("diag", 3, 2, "Diagnóstico e\nhistórico (cap. 26, 27)", "dado")
    f.no("btp", 3, 4, "Aba do BTP →\nOData (cap. 11)", "externo")
    f.seta("user", "tela")
    f.seta("tela", "serv", dupla=True)
    f.seta("serv", "ag")
    f.seta("ag", "motor")
    f.seta("motor", "diag")
    f.seta("motor", "proc")
    f.seta("proc", "loja", "grava")
    f.seta("proc", "cli")
    f.seta("cli", "btp", dupla=True)
    f.seta("loja", "exp")
    f.seta("exp", "xlsx")
    return f

def seq_execucao():
    s = Sequencia("Uma análise completa, do clique ao Excel", [
        ("user", "Usuário", "externo"),
        ("tela", "Tela\nindex.html", "processo"),
        ("ag", "Agente\nservidor · agente", "marca"),
        ("mot", "Motor\nmotor · processador", "processo"),
        ("aba", "Aba do BTP\nponte · agente_btp.js", "externo"),
        ("btp", "Serviço OData\n(BTP)", "externo"),
        ("loja", "Base e Excel\nloja · exportacao", "dado")], larg_col=134, passo=34)
    s.msg("user", "tela", "cola 3 WOs, Executar")
    s.msg("tela", "ag", "POST /api/executar")
    s.msg("ag", "loja", "criar(run_id): 3 pendentes")
    s.separador("preparo já feito e fonte confirmada")
    s.msg("ag", "mot", "Motor.executar()")
    s.msg("mot", "aba", "sonda: lista de 1 WO")
    s.msg("aba", "btp", "GET Pendencias")
    s.msg("btp", "aba", "200 JSON", tracejada=True)
    s.msg("mot", "aba", "lote: lista + itens (OR)")
    s.msg("aba", "btp", "GETs")
    s.msg("btp", "aba", "registros", tracejada=True)
    s.msg("mot", "mot", "2 auditorias + consolidar")
    s.msg("mot", "loja", "concluir(): grava cada WO")
    s.msg("tela", "ag", "GET /api/estado (1 s)")
    s.msg("ag", "tela", "progresso, indicadores", tracejada=True)
    s.separador("fila vazia")
    s.msg("ag", "loja", "gerar_planilha + verificar")
    s.msg("tela", "user", "Baixar relatório auditado")
    return s


def ciclo_mudanca():
    f = Fluxo("Roteiro seguro para criar ou alterar uma funcionalidade", 3, 3, cel_l=210, cel_a=96, caixa_l=186, caixa_a=62)
    passos = [((0, 0), "1. Descrever o\ncomportamento desejado"), ((1, 0), "2. Localizar o componente\nresponsável"),
              ((2, 0), "3. Mapear quem depende\ndele (grep, testes)"), ((2, 1), "4. Escrever o teste\nque falha"),
              ((1, 1), "5. Implementar a mudança\nmínima"), ((0, 1), "6. Rodar pytest +\nponta a ponta"),
              ((0, 2), "7. Comparar planilha\nantes x depois"), ((1, 2), "8. Revisar o diff\n(nada além do pedido)"),
              ((2, 2), "9. Registrar em docs/\ne fazer o commit")]
    for i, ((c, l), t) in enumerate(passos):
        f.no(f"p{i}", c, l, t, "resultado" if i == 8 else "processo")
        if i:
            f.seta(f"p{i - 1}", f"p{i}")
    return f


CATALOGO = {
    "contexto": ("O Agente BTP e os sistemas com que ele conversa", contexto),
    "antes_depois": ("O trabalho manual por WO e o mesmo trabalho com o agente", antes_depois),
    "linha_tempo": ("Marcos do desenvolvimento reconstruídos a partir do histórico Git", linha_tempo),
    "visao_geral": ("Fluxo principal de uma análise, do pedido ao relatório", visao_geral),
    "arquitetura": ("Arquitetura: componentes reais e suas ligações", arquitetura),
    "camadas": ("Camadas do código e direção das dependências", camadas),
    "inicializacao": ("Fluxograma da inicialização", inicializacao),
    "conexao_aba": ("Como a aba do BTP se conecta ao programa local", conexao_aba),
    "seq_consulta": ("Sequência de uma consulta ao BTP passando pela aba", seq_consulta),
    "comunicacao": ("Canais de comunicação e o que trafega em cada um", comunicacao),
    "preparacao": ("Preparação da fonte de dados na primeira execução", preparacao),
    "conferencia_amostra": ("Conferência da amostra: as três respostas possíveis", conferencia_amostra),
    "extracao": ("Extração de uma WO pela consulta individual", extracao),
    "lote": ("Leitura em lote e a verificação de equivalência", lote),
    "auditoria": ("Como a auditoria decide o estado de cada item", auditoria),
    "transformacao": ("Do JSON devolvido pelo BTP à linha da planilha", transformacao),
    "estados_wo": ("Estados de uma WO na base de execução", estados_wo),
    "geracao_planilha": ("Geração e conferência do relatório Excel", geracao_planilha),
    "abas_excel": ("Estrutura do arquivo Excel gerado", abas_excel),
    "erros": ("Classificação das falhas e o que acontece com cada uma", erros),
    "motor_laco": ("Laço do motor: passadas, sondas e pausas", motor_laco),
    "testes": ("Camadas de verificação do projeto", testes),
    "implantacao": ("Distribuição e implantação no computador do usuário", implantacao),
    "mapa_final": ("Mapa integrado do sistema, com os capítulos de referência", mapa_final),
    "seq_execucao": ("Sequência completa de uma análise", seq_execucao),
    "ciclo_mudanca": ("Roteiro seguro para criar ou alterar uma funcionalidade", ciclo_mudanca),
}
