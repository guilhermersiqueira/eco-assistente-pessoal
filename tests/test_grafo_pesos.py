"""Grafo refeito (passo 4 do escopo): hierarquia por campo pai, tipos de ligação e regras de peso."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from eco.chat.armazenamento import Banco
from eco.config import carregar_config
from eco.grafo import pesos
from eco.grafo.analise import Analisador, Resposta
from eco.grafo.dados import Grafo


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    return carregar_config()


@pytest.fixture
def banco(tmp_path):
    b = Banco(tmp_path / "eco.db")
    yield b
    b.fechar()


@pytest.fixture
def grafo(banco):
    return Grafo(banco)


# --- regras puras ---------------------------------------------------------------------------------------------------
def test_valores_de_cada_regra_e_teto():
    assert pesos.combinar({"hierarquia": 0.9, "sessao": 0.5}) == 0.9  # hierarquia fica em 0,9, fora do teto
    assert pesos.combinar({"causal": 0.8}) == 0.8  # o teto não rebaixa uma regra sozinha
    assert pesos.combinar({"causal": 0.8, "sessao": 0.5}) == 0.8
    assert pesos.combinar({"sessao": 0.5, "semantica": 0.4}) == 0.7  # soma 0,9 limitada a 0,7
    assert pesos.combinar({"sessao": 0.5}) == 0.5 and pesos.combinar({"lateral": 0.2}) == 0.2
    assert pesos.combinar({}) == 0.0
    assert pesos.valor_do_tipo("semantica", 0.9) == 0.4 and pesos.valor_do_tipo("semantica", 0.25) == 0.25


def test_decaimento_temporal_enfraquece_sem_sumir():
    assert pesos.fator_tempo(0) == 1.0
    assert pesos.fator_tempo(pesos.MEIA_VIDA_DIAS) == pytest.approx(0.5)
    assert pesos.fator_tempo(10_000) == pesos.PISO_DECAIMENTO
    assert pesos.combinar({"causal": 0.8}, dias=pesos.MEIA_VIDA_DIAS) == pytest.approx(0.4)
    assert pesos.combinar({"hierarquia": 0.9}, dias=10_000) == 0.9  # a estrutura não decai


def _ideia(id_, pai=None, quando="2026-11-10T12:00:00+00:00"):
    return SimpleNamespace(id=id_, pai=pai, atualizado_em=quando)


def _lig(a, b, tipo, forca=0.4, ativa=True):
    return SimpleNamespace(id=f"{a}{b}", a=min(a, b), b=max(a, b), tipo=tipo, forca=forca, ativa=ativa, motivo="m")


def test_calcular_junta_hierarquia_ligacoes_e_sessao():
    ideias = [_ideia("a"), _ideia("b", pai="a"), _ideia("c"), _ideia("d", quando="2026-03-14T12:00:00+00:00")]
    ligs = [_lig("b", "c", "causal"), _lig("a", "c", "semantica", 0.3), _lig("c", "d", "lateral", ativa=False)]
    arestas = {(x.a, x.b): x for x in pesos.calcular(ideias, ligs, [("a", "c"), ("c", "d"), ("a", "d")])}
    assert arestas[("a", "b")].peso == 0.9 and arestas[("a", "b")].hierarquia
    assert arestas[("b", "c")].peso == 0.8
    assert arestas[("a", "c")].peso == 0.7 and set(arestas[("a", "c")].regras) == {"semantica", "sessao"}
    assert ("c", "d") not in arestas  # o dono desligou: nem a mesma sessão religa
    assert arestas[("a", "d")].peso < 0.5  # ~8 meses de distância: decaiu
    assert "mesma sessão" in arestas[("a", "c")].descricao() and "0,70" in arestas[("a", "c")].descricao()


# --- banco ------------------------------------------------------------------------------------------------------------
def test_pai_filhos_profundidade_e_ciclo(grafo):
    inv, acoes, bova = (grafo.criar_ideia(n, None, "manual") for n in ("Investimentos", "Ações", "BOVA11"))
    grafo.definir_pai(acoes.id, inv.id)
    grafo.definir_pai(bova.id, acoes.id)
    assert [f.nome for f in grafo.filhos(inv.id)] == ["Ações"]
    assert grafo.profundidade(bova.id) == 2 and grafo.profundidade(inv.id) == 0
    with pytest.raises(ValueError, match="círculo"):
        grafo.definir_pai(inv.id, bova.id)
    with pytest.raises(ValueError):
        grafo.definir_pai(inv.id, inv.id)
    grafo.apagar_ideia(acoes.id)
    assert grafo.ideia(bova.id).pai is None  # o pai sumiu: a folha fica solta, não some


def test_ligar_mantem_o_tipo_mais_forte_e_desligar_par_so_de_sessao(grafo):
    a, b = grafo.criar_ideia("A", None, "manual"), grafo.criar_ideia("B", None, "manual")
    grafo.ligar(a.id, b.id, "parecido", "auto", "semantica", 0.3)
    grafo.ligar(a.id, b.id, "uma causa a outra", "auto", "causal")
    grafo.ligar(a.id, b.id, "", "auto", "lateral")
    (l,) = grafo.listar_ligacoes()
    assert l.tipo == "causal" and l.motivo == "uma causa a outra"
    c = grafo.criar_ideia("C", None, "manual")
    grafo.desligar(a.id, c.id)  # par sem linha gravada: grava desligada
    assert [x.ativa for x in grafo.listar_ligacoes(so_ativas=False) if {x.a, x.b} == {a.id, c.id}] == [False]
    assert grafo.ligar(a.id, c.id, "", "auto", "causal") is None  # a análise não religa


def test_banco_antigo_ganha_as_colunas_novas(tmp_path):
    caminho = tmp_path / "velho.db"
    b = Banco(caminho)
    con = b._con
    with b._lock, con:
        con.executescript(
            "CREATE TABLE ideias (id TEXT PRIMARY KEY, nome TEXT NOT NULL, resumo TEXT NOT NULL DEFAULT '', espaco_id TEXT, "
            "origem TEXT NOT NULL DEFAULT 'chat', topico TEXT NOT NULL DEFAULT '', criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL);"
            "CREATE TABLE ligacoes (id TEXT PRIMARY KEY, a TEXT NOT NULL, b TEXT NOT NULL, motivo TEXT NOT NULL DEFAULT '', "
            "origem TEXT NOT NULL DEFAULT 'auto', ativa INTEGER NOT NULL DEFAULT 1, criado_em TEXT NOT NULL, UNIQUE (a, b), CHECK (a < b));"
            "INSERT INTO ideias VALUES ('1','A','','','manual','','2026-11-01','2026-11-01');"
            "INSERT INTO ideias VALUES ('2','B','','','manual','','2026-11-01','2026-11-01');"
            "INSERT INTO ligacoes VALUES ('l','1','2','feita por mim','manual',1,'2026-11-01');"
        )
    g = Grafo(b)
    assert g.ideia("1").pai is None and g.ideia("1").tipo == "ideia"
    assert g.listar_ligacoes()[0].tipo == "manual"
    b.fechar()


# --- análise ----------------------------------------------------------------------------------------------------------
class Falso:
    def __init__(self, *respostas):
        self.respostas, self.chamadas = list(respostas), []

    def completar(self, sistema, usuario):
        self.chamadas.append((sistema, usuario))
        return Resposta(json.dumps(self.respostas.pop(0)), 0.001)


def test_analise_monta_hierarquia_tipos_e_ignora_semelhanca_fraca(cfg, banco, grafo):
    esp = banco.criar_espaco("Investimentos")
    c = banco.criar_conversa(esp.id, "Carteira")
    banco.adicionar_mensagem(c.id, "user", "Quero investir em ações, começando por BOVA11, e falar com o assessor João sobre isso.")
    banco.adicionar_mensagem(c.id, "assistant", "Boa: BOVA11 replica o Ibovespa. Leve as dúvidas ao João.")
    resp = {
        "ideias": [
            {"nome": "Investimentos", "resumo": "x", "nivel": "tronco", "pai": "", "tipo": "ideia"},
            {"nome": "Ações", "resumo": "x", "nivel": "galho", "pai": "Investimentos", "tipo": "ideia"},
            {"nome": "BOVA11", "resumo": "x", "nivel": "folha", "pai": "Ações", "tipo": "ideia"},
            {"nome": "Assessor João", "resumo": "x", "nivel": "folha", "pai": "Inexistente", "tipo": "entidade"},
        ],
        "ligacoes": [
            {"de": "Assessor João", "para": "BOVA11", "tipo": "causal", "motivo": "ele orienta a compra"},
            {"de": "Assessor João", "para": "Investimentos", "tipo": "semelhante", "forca": 0.1, "motivo": "fraca"},
        ],
    }
    falso = Falso(resp)
    an = Analisador(banco, grafo, falso, cfg)
    an.executar([p for p in an.pendentes() if p.tipo == "conversa"])
    por_nome = {i.nome: i for i in grafo.listar_ideias()}
    assert por_nome["Ações"].pai == por_nome["Investimentos"].id and por_nome["BOVA11"].pai == por_nome["Ações"].id
    assert por_nome["Investimentos"].nivel == "tronco" and por_nome["Assessor João"].tipo == "entidade"
    assert por_nome["Assessor João"].pai is None  # pai que não existe é ignorado
    (l,) = grafo.listar_ligacoes()  # a semelhança 0,1 ficou abaixo do limiar
    assert l.tipo == "causal"
    assert "tronco" in falso.chamadas[0][0] and "rotineiras" in falso.chamadas[0][0]


def test_analise_nao_troca_o_pai_que_o_dono_escolheu(cfg, banco, grafo):
    esp = banco.criar_espaco("Estudos")
    a = grafo.criar_ideia("Cálculo", esp.id, "chat", nivel="galho")
    b = grafo.criar_ideia("Matemática", esp.id, "chat", nivel="tronco")
    outro = grafo.criar_ideia("Faculdade", esp.id, "chat", nivel="tronco")
    grafo.definir_pai(a.id, outro.id)
    antiga = banco.criar_conversa(esp.id, "Antiga")
    for i in (a, b, outro):
        grafo.adicionar_fonte(i.id, antiga.id)  # ideia de chat sem conversa de origem é limpa como órfã
    c = banco.criar_conversa(esp.id, "Prova")
    banco.adicionar_mensagem(c.id, "user", "Preciso estudar cálculo para a prova de quinta, principalmente derivadas e integrais.")
    resp = {"ideias": [{"nome": "Cálculo", "resumo": "", "nivel": "galho", "pai": "Matemática"}], "ligacoes": []}
    an = Analisador(banco, grafo, Falso(resp), cfg)
    an.executar([p for p in an.pendentes() if p.tipo == "conversa"])
    assert grafo.ideia(a.id).pai == outro.id and b.id != outro.id
