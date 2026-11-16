"""Testes do grafo de ideias: dados, análise com um modelo falso e layout."""

import json
import threading

import numpy as np
import pytest

from eco.chat.armazenamento import Banco
from eco.config import carregar_config
from eco.grafo.analise import Analisador, Resposta, interpretar
from eco.grafo.dados import Grafo, chave_da_ideia
from eco.grafo.layout import Layout


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


class Falso:
    """Modelo falso: devolve respostas prontas, na ordem, e guarda o que recebeu."""

    def __init__(self, *respostas, custo=0.001):
        self.respostas, self.custo, self.chamadas = list(respostas), custo, []

    def completar(self, sistema, usuario):
        self.chamadas.append((sistema, usuario))
        r = self.respostas.pop(0) if self.respostas else json.dumps({"ideias": [], "ligacoes": []})
        if isinstance(r, Exception):
            raise r
        return Resposta(r if isinstance(r, str) else json.dumps(r), self.custo)


def _conversa(banco, espaco_id, titulo, pergunta, resposta):
    c = banco.criar_conversa(espaco_id, titulo)
    banco.adicionar_mensagem(c.id, "user", pergunta)
    banco.adicionar_mensagem(c.id, "assistant", resposta)
    return c


PERGUNTA = "Quero montar uma reserva de emergência e depois começar a investir em renda fixa."
RESPOSTA = "Comece com uma reserva de seis meses de gastos em Tesouro Selic, e só depois diversifique em renda fixa."


# --- dados ----------------------------------------------------------------------------------------
def test_ideia_com_mesmo_nome_no_mesmo_espaco_e_uma_so(grafo, banco):
    e = banco.criar_espaco("Investimentos")
    a = grafo.criar_ideia("Renda fixa", e.id)
    b = grafo.criar_ideia("  renda   FIXA ", e.id, resumo="Títulos de renda fixa")
    assert a.id == b.id and grafo.ideia(a.id).resumo == "Títulos de renda fixa"
    outro = grafo.criar_ideia("Renda fixa", None)
    assert outro.id != a.id  # espaço diferente, ideia diferente
    assert chave_da_ideia("Ações") == chave_da_ideia("acoes")
    with pytest.raises(ValueError):
        grafo.criar_ideia("   ")


def test_ligacao_nao_se_repete_e_nao_liga_ideia_a_si_mesma(grafo):
    a, b = grafo.criar_ideia("A"), grafo.criar_ideia("B")
    l1 = grafo.ligar(a.id, b.id, "motivo")
    l2 = grafo.ligar(b.id, a.id, "outro")  # ordem invertida: é a mesma ligação
    assert l1.id == l2.id and len(grafo.listar_ligacoes()) == 1
    with pytest.raises(ValueError):
        grafo.ligar(a.id, a.id)


def test_ligacao_desligada_pelo_dono_nao_volta_sozinha(grafo):
    a, b = grafo.criar_ideia("A"), grafo.criar_ideia("B")
    grafo.ligar(a.id, b.id, "auto", "auto")
    grafo.desligar(a.id, b.id)
    assert grafo.listar_ligacoes() == []
    assert grafo.ligar(a.id, b.id, "de novo", "auto") is None  # análise automática não recria
    assert grafo.listar_ligacoes() == []
    refeita = grafo.ligar(a.id, b.id, "eu quis", "manual")  # o dono refaz à mão
    assert refeita.ativa and refeita.origem == "manual" and len(grafo.listar_ligacoes()) == 1


def test_apagar_ideia_leva_as_ligacoes_e_nao_volta_na_analise(grafo, banco):
    e = banco.criar_espaco("X")
    a, b = grafo.criar_ideia("Ações", e.id), grafo.criar_ideia("Dividendos", e.id)
    grafo.ligar(a.id, b.id)
    grafo.apagar_ideia(a.id)
    assert [i.nome for i in grafo.listar_ideias()] == ["Dividendos"] and grafo.listar_ligacoes() == []
    assert grafo.ignorada("acoes", e.id) and not grafo.ignorada("acoes", None)


def test_fontes_listam_as_conversas_de_onde_a_ideia_saiu(grafo, banco):
    e = banco.criar_espaco("Investimentos")
    c1, c2 = banco.criar_conversa(e.id, "Carteira"), banco.criar_conversa(None, "Solta")
    i = grafo.criar_ideia("Renda fixa", e.id)
    grafo.adicionar_fonte(i.id, c1.id)
    grafo.adicionar_fonte(i.id, c2.id)
    grafo.adicionar_fonte(i.id, c2.id)  # repetida: ignorada
    fontes = grafo.fontes(i.id)
    assert {(f.titulo, f.espaco_nome) for f in fontes} == {("Carteira", "Investimentos"), ("Solta", "Geral")}
    banco.excluir_conversa(c1.id)
    assert [f.titulo for f in grafo.fontes(i.id)] == ["Solta"]
    banco.excluir_conversa(c2.id)
    assert grafo.limpar_orfas() == 1 and grafo.listar_ideias() == []


def test_mesmo_tema_em_espacos_diferentes_e_ligado_sem_custo(grafo, banco):
    a, b, c = banco.criar_espaco("Investimentos"), banco.criar_espaco("Leitura"), banco.criar_espaco("Estudos")
    x1, x2 = grafo.criar_ideia("Reserva de emergência", a.id), grafo.criar_ideia("reserva de emergencia", b.id)
    grafo.criar_ideia("Reserva de emergência", a.id)  # mesma ideia no mesmo espaço: não conta
    grafo.criar_ideia("Outra coisa", c.id)
    assert grafo.ligar_iguais_entre_espacos() == 1
    assert grafo.ligar_iguais_entre_espacos() == 0  # repetir não duplica
    assert {frozenset((l.a, l.b)) for l in grafo.listar_ligacoes()} == {frozenset((x1.id, x2.id))}
    grafo.desligar(x1.id, x2.id)
    assert grafo.ligar_iguais_entre_espacos() == 0  # desligada pelo dono: não volta


def test_excluir_espaco_mantem_as_ideias_no_geral(grafo, banco):
    e = banco.criar_espaco("Temp")
    i = grafo.criar_ideia("Algo", e.id)
    banco.excluir_espaco(e.id)
    assert grafo.ideia(i.id).espaco_id is None


def test_gasto_do_mes_acumula(grafo):
    assert grafo.gasto_do_mes() == 0.0
    grafo.somar_gasto(0.25)
    assert grafo.somar_gasto(0.5) == pytest.approx(0.75) and grafo.gasto_do_mes() == pytest.approx(0.75)


# --- interpretar a resposta do modelo -------------------------------------------------------------------
def test_interpretar_aceita_cerca_de_codigo_e_texto_em_volta():
    bruto = 'Aqui está:\n```json\n{"ideias":[{"nome":" Renda  fixa ","resumo":"x"},{"nome":""}],"ligacoes":[{"de":"A","para":"B","motivo":"m"},{"de":"A"}]}\n```'
    r = interpretar(bruto)
    assert r["ideias"] == [{"nome": "Renda fixa", "resumo": "x", "dias": [], "nivel": "", "pai": "", "tipo": "ideia"}]
    assert r["ligacoes"] == [{"de": "A", "para": "B", "motivo": "m", "tipo": "semantica", "forca": 0.4}]  # sem tipo: semelhante no limite
    with pytest.raises(ValueError):
        interpretar("não sei")
    with pytest.raises(ValueError):
        interpretar("{quebrado")


# --- análise --------------------------------------------------------------------------------------------
RESP_INVEST = {
    "ideias": [
        {"nome": "Reserva de emergência", "resumo": "Seis meses de gastos."},
        {"nome": "Tesouro Selic", "resumo": "Onde guardar a reserva."},
        {"nome": "Renda fixa", "resumo": "Diversificar depois."},
    ],
    "ligacoes": [
        {"de": "Reserva de emergência", "para": "Tesouro Selic", "motivo": "A reserva fica no Selic."},
        {"de": "Reserva de emergência", "para": "Renda fixa", "motivo": "Renda fixa vem depois da reserva."},
        {"de": "Reserva de emergência", "para": "Ideia que não existe", "motivo": "x"},
    ],
}


def _analisador(banco, grafo, cfg, *respostas, **kw):
    return Analisador(banco, grafo, Falso(*respostas, **kw), cfg)


def test_analisa_a_conversa_cria_ideias_ligacoes_e_fontes(banco, grafo, cfg):
    e = banco.criar_espaco("Investimentos")
    c = _conversa(banco, e.id, "Reserva", PERGUNTA, RESPOSTA)
    an = _analisador(banco, grafo, cfg, RESP_INVEST)
    pend = an.pendentes()
    assert [p.fonte for p in pend] == [f"conversa:{c.id}"]
    r = an.executar(pend)
    assert (r.analisadas, r.ideias_novas, r.ligacoes_novas) == (1, 3, 2) and r.erros == []
    nomes = {i.nome: i for i in grafo.listar_ideias()}
    assert set(nomes) == {"Reserva de emergência", "Tesouro Selic", "Renda fixa"}
    assert all(i.espaco_id == e.id and i.origem == "chat" for i in nomes.values())
    assert [f.conversa_id for f in grafo.fontes(nomes["Tesouro Selic"].id)] == [c.id]
    assert len(grafo.listar_ligacoes()) == 2  # a ligação para ideia inexistente foi descartada
    assert grafo.gasto_do_mes() > 0


def test_so_reanalisa_o_que_mudou(banco, grafo, cfg):
    e = banco.criar_espaco("Investimentos")
    c = _conversa(banco, e.id, "Reserva", PERGUNTA, RESPOSTA)
    an = _analisador(banco, grafo, cfg, RESP_INVEST, {"ideias": [{"nome": "CDB", "resumo": "x"}], "ligacoes": []})
    an.executar(an.pendentes())
    assert an.pendentes() == []  # nada mudou
    banco.adicionar_mensagem(c.id, "user", "E o CDB, vale a pena para completar a carteira de renda fixa?")
    pend = an.pendentes()
    assert len(pend) == 1
    an.executar(pend)
    assert "CDB" in {i.nome for i in grafo.listar_ideias()} and an.pendentes() == []


def test_a_analise_mostra_ao_modelo_as_ideias_que_o_espaco_ja_tem(banco, grafo, cfg):
    e = banco.criar_espaco("Investimentos")
    antiga = _conversa(banco, e.id, "Antiga", "conversa já analisada antes, sobre títulos de renda fixa", "Sim, títulos públicos e privados.")
    grafo.adicionar_fonte(grafo.criar_ideia("Renda fixa", e.id).id, antiga.id)
    grafo.definir_marca(f"conversa:{antiga.id}", str(banco.mensagens(antiga.id)[-1].id))
    _conversa(banco, e.id, "Outra", PERGUNTA, RESPOSTA)
    an = _analisador(banco, grafo, cfg, {"ideias": [{"nome": "renda FIXA", "resumo": "r"}, {"nome": "LCI", "resumo": "l"}], "ligacoes": [{"de": "LCI", "para": "Renda fixa", "motivo": "LCI é renda fixa"}]})
    an.executar(an.pendentes())
    assert "- Renda fixa" in an.completador.chamadas[0][1] and "Espaço da conversa: Investimentos" in an.completador.chamadas[0][1]
    assert sorted(i.nome for i in grafo.listar_ideias()) == ["LCI", "Renda fixa"]  # reaproveitou, não duplicou
    assert len(grafo.listar_ligacoes()) == 1


def test_ideia_apagada_e_ligacao_desligada_nao_voltam(banco, grafo, cfg):
    e = banco.criar_espaco("Investimentos")
    c = _conversa(banco, e.id, "Reserva", PERGUNTA, RESPOSTA)
    an = _analisador(banco, grafo, cfg, RESP_INVEST)
    an.executar(an.pendentes())
    ideias = {i.nome: i for i in grafo.listar_ideias()}
    grafo.apagar_ideia(ideias["Tesouro Selic"].id)
    grafo.desligar(ideias["Reserva de emergência"].id, ideias["Renda fixa"].id)
    banco.adicionar_mensagem(c.id, "user", "Mais uma coisa sobre a reserva de emergência e o Tesouro Selic.")
    an.completador.respostas.append(RESP_INVEST)
    an.executar(an.pendentes())
    assert "Tesouro Selic" not in {i.nome for i in grafo.listar_ideias()}
    assert grafo.listar_ligacoes() == []


def test_metas_viram_ideias_sem_conversa_e_so_reanalisa_o_que_mudou(banco, grafo, cfg):
    cfg.pasta_dados.mkdir(parents=True, exist_ok=True)
    (cfg.pasta_dados / "metas.md").write_text("# Metas\n\n- [ ] Ler 12 livros este ano\n- [ ] Montar reserva de emergência de seis meses\n", encoding="utf-8")
    an = _analisador(banco, grafo, cfg, {"ideias": [{"nome": "Leitura", "resumo": "12 livros"}, {"nome": "Investimentos", "resumo": "reserva"}], "ligacoes": []})
    pend = an.pendentes()
    assert [p.fonte for p in pend] == ["metas"]
    an.executar(pend)
    assert {(i.nome, i.origem) for i in grafo.listar_ideias()} == {("Leitura", "metas"), ("Investimentos", "metas")}
    assert all(grafo.fontes(i.id) == [] for i in grafo.listar_ideias())  # sem conversa de origem
    assert an.pendentes() == []
    (cfg.pasta_dados / "metas.md").write_text("# Metas\n\n- [ ] Ler 20 livros este ano e treinar todos os dias\n", encoding="utf-8")
    assert [p.fonte for p in an.pendentes()] == ["metas"]  # só o que mudou


def test_erro_em_uma_fonte_nao_derruba_as_outras(banco, grafo, cfg):
    _conversa(banco, None, "Uma", PERGUNTA, RESPOSTA)
    _conversa(banco, None, "Duas", "Preciso planejar minhas férias de julho e a viagem de carro.", "Vamos listar destinos, datas e orçamento para a viagem.")
    an = _analisador(banco, grafo, cfg, "isto não é json", {"ideias": [{"nome": "Férias", "resumo": "julho"}], "ligacoes": []})
    r = an.executar(an.pendentes())
    assert r.analisadas == 1 and len(r.erros) == 1 and [i.nome for i in grafo.listar_ideias()] == ["Férias"]
    assert len(an.pendentes()) == 1  # a que falhou continua pendente para a próxima vez


def test_teto_mensal_do_grafo_pausa_a_analise(banco, grafo, cfg):
    import dataclasses

    cfg2 = dataclasses.replace(cfg, teto_grafo_usd=0.0015)
    for i in range(4):
        _conversa(banco, None, f"C{i}", PERGUNTA + f" ({i})", RESPOSTA)
    an = Analisador(banco, grafo, Falso(*[{"ideias": [{"nome": f"Ideia {i}", "resumo": ""}], "ligacoes": []} for i in range(4)], custo=0.001), cfg2)
    r = an.executar(an.pendentes())
    assert r.parou_por_teto and r.analisadas == 2 and len(an.pendentes()) == 2


def test_parar_interrompe_entre_as_fontes(banco, grafo, cfg):
    for i in range(3):
        _conversa(banco, None, f"C{i}", PERGUNTA + f" ({i})", RESPOSTA)
    parar = threading.Event()
    an = _analisador(banco, grafo, cfg)
    feitos = []

    def progresso(n, total, titulo):
        feitos.append(n)
        if n == 1:
            parar.set()

    r = an.executar(an.pendentes(), progresso, parar)
    assert r.interrompido and feitos == [1] and r.analisadas == 1


def test_estimativa_de_custo_antes_de_rodar(banco, grafo, cfg):
    for i in range(10):
        _conversa(banco, None, f"C{i}", PERGUNTA + f" ({i})", RESPOSTA)
    an = _analisador(banco, grafo, cfg)
    est = an.estimar(an.pendentes())
    assert est.chamadas == 10 and est.tokens_saida == 6000 and 0 < est.custo_usd < 0.05


def test_conversa_curtinha_nao_e_analisada(banco, grafo, cfg):
    _conversa(banco, None, "Oi", "oi", "olá")
    assert _analisador(banco, grafo, cfg).pendentes() == []


# --- layout ---------------------------------------------------------------------------------------------
def _dist(l, a, b):
    return float(np.linalg.norm(l.pos[l.indice[a]] - l.pos[l.indice[b]]))


def test_layout_assenta_e_agrupa_por_espaco():
    ids = [f"a{i}" for i in range(6)] + [f"b{i}" for i in range(6)]
    grupos = {i: ("A" if i.startswith("a") else "B") for i in ids}
    arestas = [(f"a{i}", f"a{i + 1}") for i in range(5)] + [(f"b{i}", f"b{i + 1}") for i in range(5)]
    l = Layout(ids, grupos, arestas)
    l.assentar(800)
    assert l.assentado
    centro_a = np.mean([l.pos[l.indice[i]] for i in ids if i.startswith("a")], axis=0)
    centro_b = np.mean([l.pos[l.indice[i]] for i in ids if i.startswith("b")], axis=0)
    assert np.linalg.norm(centro_a - centro_b) > 150  # cada espaço numa região
    for i in ids:
        mesmo = centro_a if i.startswith("a") else centro_b
        outro = centro_b if i.startswith("a") else centro_a
        assert np.linalg.norm(l.pos[l.indice[i]] - mesmo) < np.linalg.norm(l.pos[l.indice[i]] - outro)


def test_layout_ligados_ficam_mais_perto_que_nao_ligados():
    ids = ["x", "y", "z", "w"]
    l = Layout(ids, {i: "G" for i in ids}, [("x", "y")])
    l.assentar(800)
    assert _dist(l, "x", "y") < _dist(l, "x", "z") and _dist(l, "x", "y") < _dist(l, "x", "w")


def test_layout_no_arrastado_fica_parado_e_vazio_nao_quebra():
    l = Layout(["a", "b"], {}, [("a", "b")])
    l.fixo = 0
    antes = l.pos[0].copy()
    for _ in range(20):
        l.passo()
    assert np.allclose(l.pos[0], antes)
    vazio = Layout([], {}, [])
    vazio.passo()
    assert vazio.assentado
