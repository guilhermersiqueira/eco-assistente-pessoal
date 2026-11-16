"""Grafo versão 2 (E3): migração do banco, histórico (19.6.4), arquivar e proteger (19.6.9, 19.6.17), origem (19.6.15)."""

from __future__ import annotations

import sqlite3

import pytest

from eco.chat.armazenamento import Banco
from eco.grafo import dados
from eco.grafo.dados import Grafo

ESQUEMA_V1 = """
CREATE TABLE espacos (id TEXT PRIMARY KEY, nome TEXT NOT NULL, cor TEXT, instrucoes TEXT, criado_em TEXT);
CREATE TABLE ideias (id TEXT PRIMARY KEY, nome TEXT NOT NULL, resumo TEXT NOT NULL DEFAULT '', espaco_id TEXT,
    origem TEXT NOT NULL DEFAULT 'chat', topico TEXT NOT NULL DEFAULT '', criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL,
    pai TEXT, nivel TEXT NOT NULL DEFAULT '', tipo TEXT NOT NULL DEFAULT 'ideia');
CREATE TABLE ligacoes (id TEXT PRIMARY KEY, a TEXT NOT NULL, b TEXT NOT NULL, motivo TEXT NOT NULL DEFAULT '',
    origem TEXT NOT NULL DEFAULT 'auto', ativa INTEGER NOT NULL DEFAULT 1, criado_em TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'semantica', forca REAL NOT NULL DEFAULT 0.4, UNIQUE (a, b), CHECK (a < b));
INSERT INTO ideias VALUES ('a1', 'Python', 'linguagem', NULL, 'chat', '', 't', 't', NULL, 'galho', 'ideia');
INSERT INTO ideias VALUES ('b2', 'Nome do dono', 'O dono se chama Vitor', NULL, 'fato', 'fixo', 't', 't', NULL, '', 'ideia');
INSERT INTO ideias VALUES ('c3', 'Café', 'gosta de café', NULL, 'fato', '', 't', 't', NULL, '', 'ideia');
INSERT INTO ligacoes VALUES ('l1', 'a1', 'b2', 'x', 'auto', 1, 't', 'semantica', 0.3);
INSERT INTO ligacoes VALUES ('l2', 'a1', 'c3', '', 'manual', 0, 't', 'lateral', 0);
"""


@pytest.fixture
def banco_v1(tmp_path):
    arquivo = tmp_path / "eco.db"
    con = sqlite3.connect(str(arquivo))
    con.executescript(ESQUEMA_V1)
    con.commit()
    con.close()
    return arquivo


def test_migra_preservando_tudo_e_preenchendo_o_novo(banco_v1):
    chamadas = []
    b = Banco(banco_v1)
    assert dados.versao_do_esquema(b) == 1
    g = Grafo(b, backup_antes_de_migrar=lambda: chamadas.append("backup"))
    assert chamadas == ["backup"] and dados.versao_do_esquema(b) == 2
    por_id = {i.id: i for i in g.listar_ideias()}
    assert por_id["a1"].nome == "Python" and por_id["a1"].resumo == "linguagem" and por_id["a1"].nivel == "galho"
    assert por_id["b2"].protegido == 1 and por_id["c3"].protegido == 0  # lembrança fixa = protegida
    assert all(i.estado == "ativo" and i.relevancia == 0.5 for i in por_id.values())
    assert por_id["a1"].origem_detalhe == "chat"
    lig = {l.id: l for l in g.listar_ligacoes(so_ativas=False)}
    assert lig["l1"].status == "ativa" and lig["l2"].status == "rejeitada" and lig["l2"].criado_por == "manual"
    Grafo(b, backup_antes_de_migrar=lambda: chamadas.append("de novo"))
    assert chamadas == ["backup"]  # já migrado: nada de novo


def test_backup_padrao_antes_de_migrar_fica_ao_lado_do_banco(banco_v1):
    from eco import backup

    Grafo(Banco(banco_v1))
    feitos = backup.backups(banco_v1.parent / "backups")
    assert len(feitos) == 1 and "antes-migrar-grafo-v2" in feitos[0].name
    assert backup.registros(banco_v1.parent / "backups")[-1]["ok"]


def test_banco_novo_nasce_na_versao_2_sem_backup(tmp_path):
    chamadas = []
    b = Banco(tmp_path / "novo.db")
    Grafo(b, backup_antes_de_migrar=lambda: chamadas.append(1))
    assert dados.versao_do_esquema(b) == 2 and chamadas == []  # sem dados, nada a proteger


def test_migracao_que_falha_nao_deixa_nada_pela_metade(banco_v1, monkeypatch):
    b = Banco(banco_v1)
    monkeypatch.setattr(dados, "COLUNAS_V2", {**dados.COLUNAS_V2, "ideias": dados.COLUNAS_V2["ideias"] + [("ruim", "ISSO NAO E SQL (")]})
    with pytest.raises(sqlite3.Error):
        dados.migrar(b)
    assert dados.versao_do_esquema(b) == 1
    colunas = {l[1] for l in b._con.execute("PRAGMA table_info(ideias)")}
    assert "relevancia" not in colunas  # o ALTER anterior também voltou
    assert len(b._consultar("SELECT * FROM ideias")) == 3


# --- histórico ------------------------------------------------------------------------------------------------------
@pytest.fixture
def g(tmp_path):
    return Grafo(Banco(tmp_path / "g.db"))


def test_historico_registra_criacao_alteracoes_e_conteudo_anterior(g):
    with g.como("voz"):
        i = g.criar_ideia("Trabalho", resumo="Entro às 8h", origem_detalhe="")
    g.atualizar_ideia(i.id, resumo="Entro às 9h")
    with g.como("clique"):
        g.renomear_ideia(i.id, "Horário de trabalho")
    h = g.historico(i.id)
    assert [x["acao"] for x in h] == ["criar", "atualizar", "renomear"]
    assert h[0]["autor"] == "voz" and h[0]["depois"]["resumo"] == "Entro às 8h"
    assert h[1]["antes"] == {"resumo": "Entro às 8h"} and h[1]["depois"] == {"resumo": "Entro às 9h"}
    assert h[2]["autor"] == "clique" and h[2]["antes"] == {"nome": "Trabalho"}
    assert g.ideia(i.id).origem_detalhe == "voz"  # origem detalhada (19.6.15)


def test_apagar_guarda_o_conteudo_inteiro(g):
    i = g.criar_ideia("Projeto X", resumo="detalhes")
    g.apagar_ideia(i.id)
    h = g.historico(i.id)
    assert h[-1]["acao"] == "apagar" and h[-1]["antes"]["resumo"] == "detalhes" and h[-1]["antes"]["nome"] == "Projeto X"


def test_hierarquia_e_ligacoes_no_historico(g):
    pai, filho = g.criar_ideia("Programação"), g.criar_ideia("Python")
    g.definir_pai(filho.id, pai.id)
    l = g.ligar(pai.id, filho.id, "motivo")
    g.desligar(pai.id, filho.id)
    assert g.historico(filho.id)[-1] == {**g.historico(filho.id)[-1], "acao": "hierarquia", "antes": {"pai": None}, "depois": {"pai": pai.id}}
    acoes = [x["acao"] for x in g.historico_recente()]
    assert acoes[:2] == ["desligar", "ligar"] and g.listar_ligacoes(so_ativas=False)[0].status == "rejeitada"
    assert l.criado_por == "sistema"


# --- arquivar, restaurar e proteger ---------------------------------------------------------------------------------
def test_arquivada_some_da_tela_e_volta_quando_reaparece(g):
    i = g.criar_ideia("Viagem a Londres", None, "chat")
    assert g.arquivar(i.id, "passou")
    assert i.id not in {x.id for x in g.listar_ideias()} and [x.id for x in g.arquivadas()] == [i.id]
    assert i.id in {x.id for x in g.listar_ideias(incluir_arquivadas=True)}
    de_novo = g.criar_ideia("Viagem a Londres", None, "chat")  # a análise achou de novo numa conversa
    assert de_novo.id == i.id and not de_novo.arquivada
    assert [x["acao"] for x in g.historico(i.id)][-2:] == ["arquivar", "restaurar"]
    g.arquivar(i.id)
    g.restaurar(i.id)
    assert not g.ideia(i.id).arquivada


def test_processo_automatico_nao_mexe_em_protegida(g):
    i = g.criar_ideia("Preferência permanente")
    g.proteger(i.id)
    assert g.arquivar(i.id, automatico=True) is False and not g.ideia(i.id).arquivada
    assert g.arquivar(i.id, "o dono pediu") is True  # o dono pode
    g.restaurar(i.id)
    assert [x.nome for x in g.protegidas()] == ["Preferência permanente"]


def test_limpeza_de_orfas_respeita_protegidas(g):
    a, b = g.criar_ideia("Sem conversa A"), g.criar_ideia("Sem conversa B")
    g.proteger(b.id)
    assert g.limpar_orfas() == 1
    nomes = {x.nome for x in g.listar_ideias()}
    assert nomes == {"Sem conversa B"} and g.historico(a.id)[-1]["antes"]["motivo"].startswith("as conversas")


def test_proteger_lembranca_e_fixar(g):
    f = g.criar_fato("O dono se chama Vitor", "Nome")
    g.proteger(f.id)
    assert g.ideia(f.id).topico == "fixo"
    g.atualizar_ideia(f.id, topico="")
    assert g.ideia(f.id).protegido == 0
    assert g.criar_fato("Gosta de café", "Café", fixo=True).protegido == 1


def test_analise_e_importacao_ficam_com_autor_no_historico(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    import json

    from eco.grafo import exportar as ex
    from eco.grafo.analise import Analisador, Resposta

    origem = Grafo(Banco(tmp_path / "o.db"))
    origem.criar_ideia("Python")
    arquivo = ex.exportar_para_arquivo(origem, tmp_path / "exp")
    destino = Grafo(Banco(tmp_path / "d.db"))
    ex.planejar_importacao(destino, arquivo).executar()
    i = destino.listar_ideias()[0]
    h = destino.historico(i.id)[-1]
    assert h["acao"] == "importar" and h["autor"].startswith("importação de grafo-")

    class Falso:
        def completar(self, sistema, usuario):
            return Resposta(json.dumps({"ideias": [{"nome": "Cálculo", "resumo": "matéria"}, {"nome": "Integral", "resumo": "x"}],
                                        "ligacoes": []}), 0.0001)

    b = Banco(tmp_path / "a.db")
    conv = b.criar_conversa(None, "Estudos")
    b.adicionar_mensagem(conv.id, "user", "Preciso estudar cálculo, principalmente integrais, para a prova de sexta-feira.")
    g = Grafo(b)
    from eco.config import carregar_config

    an = Analisador(b, g, Falso(), carregar_config())
    an.executar(an.pendentes())
    calc = next(x for x in g.listar_ideias() if x.nome == "Cálculo")
    assert calc.origem_detalhe == "análise do grafo" and g.historico(calc.id)[0]["autor"] == "análise do grafo"
