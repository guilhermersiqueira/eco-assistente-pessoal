"""Ligação automática (E6): regras de gravação por confiança (D6), rejeição só do par, sem duplicar nem rebaixar,
candidatas locais, leitura do JSON do Haiku, marcas por ideia, teto, gatilhos (lembrar e análise) e ferramentas."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from eco.chat.armazenamento import Banco
from eco.grafo import consulta, ligacao
from eco.grafo import semelhanca as sem
from eco.grafo.analise import Resposta
from eco.grafo.dados import Grafo
from eco.grafo.ligacao import Ligador, interpretar, rotulo

sys.path.insert(0, str(Path(__file__).parent))
from test_semelhanca import CodificadorFalso  # noqa: E402


@pytest.fixture
def grafo(tmp_path):
    return Grafo(Banco(tmp_path / "eco.db"))


class HaikuFalso:
    """Responde com o que o teste mandar; guarda o que recebeu."""

    def __init__(self, respostas=None, custo=0.001):
        self.respostas, self.pedidos, self.custo = respostas or {}, [], custo

    def completar(self, sistema, usuario):
        self.pedidos.append(usuario)
        nomes = [linha.split(". ", 1)[1] for linha in usuario.split("EXISTENTES:\n")[1].splitlines()]
        ligacoes = []
        for n, texto in enumerate(nomes, 1):
            for chave, (relacao, confianca, sentido) in self.respostas.items():
                if chave in texto:
                    ligacoes.append({"n": n, "relacao": relacao, "confianca": confianca, "sentido": sentido, "motivo": f"por {chave}"})
        return Resposta(json.dumps({"ligacoes": ligacoes}), self.custo)


def _ligador(grafo, haiku, **kw):
    return Ligador(grafo, haiku, sem.Semelhanca(grafo, CodificadorFalso()), **kw)


# --- gravação ------------------------------------------------------------------------------------------------------
def test_confianca_decide_o_status(grafo):
    a, b, c, d = (grafo.criar_ideia(n) for n in "ABCD")
    assert grafo.ligar_classificada(a.id, b.id, "relacionado_a", 0.9, "x", "ativa").status == "ativa"
    assert grafo.ligar_classificada(a.id, c.id, "complementa", 0.65, "x", "revisar").status == "revisar"
    s = grafo.ligar_classificada(a.id, d.id, "depende_de", 0.35, "x", "sugestao", de=a.id)
    assert s.status == "sugestao" and not s.ativa
    ativas = {(l.a, l.b) for l in grafo.listar_ligacoes()}
    assert len(ativas) == 2  # a sugestão não vira ligação
    assert len(grafo.ligacoes_com_status("sugestao")) == 1


def test_um_par_uma_linha_sem_rebaixar_e_sugestao_pode_subir(grafo):
    a, b = grafo.criar_ideia("A"), grafo.criar_ideia("B")
    grafo.ligar_classificada(a.id, b.id, "relacionado_a", 0.35, "talvez", "sugestao")
    assert grafo.ligar_classificada(b.id, a.id, "relacionado_a", 0.35, "talvez", "sugestao") is None
    assert grafo.ligar_classificada(a.id, b.id, "complementa", 0.9, "certo", "ativa").status == "ativa"
    assert grafo.ligar_classificada(a.id, b.id, "contradiz", 0.65, "outra", "revisar") is None  # não rebaixa
    assert len(grafo.listar_ligacoes(so_ativas=False)) == 1


def test_rejeitada_nunca_volta_e_a_manual_nao_e_tocada(grafo):
    a, b, c = grafo.criar_ideia("A"), grafo.criar_ideia("B"), grafo.criar_ideia("C")
    grafo.ligar_classificada(a.id, b.id, "relacionado_a", 0.9, "x", "ativa")
    grafo.desligar(a.id, b.id)
    assert grafo.ligar_classificada(a.id, b.id, "relacionado_a", 0.9, "x", "ativa") is None
    assert grafo.ligar(a.id, b.id, "x", "auto") is None
    grafo.ligar(a.id, c.id, "do dono", "manual")
    assert grafo.ligar_classificada(a.id, c.id, "contradiz", 0.9, "x", "ativa") is None
    assert grafo.ligacao_do_par(a.id, c.id).relacao == ""


def test_ligacao_antiga_ganha_rotulo_sem_perder_o_status(grafo):
    a, b = grafo.criar_ideia("A"), grafo.criar_ideia("B")
    grafo.ligar(a.id, b.id, "da análise", "auto", "causal")
    assert rotulo(grafo.ligacao_do_par(a.id, b.id)) == "é consequência de"  # tipo antigo -> rótulo, sem mexer nos dados
    l = grafo.ligar_classificada(a.id, b.id, "depende_de", 0.65, "novo motivo", "revisar", de=a.id)
    assert l.status == "ativa" and l.relacao == "depende_de" and rotulo(l, grafo) == "A depende de B"
    assert grafo.historico_recente(1)[0]["acao"] == "classificar"


def test_aceitar_e_mudar_relacao_viram_decisao_do_dono(grafo):
    a, b = grafo.criar_ideia("A"), grafo.criar_ideia("B")
    s = grafo.ligar_classificada(a.id, b.id, "relacionado_a", 0.35, "x", "sugestao")
    l = grafo.aceitar_ligacao(s.id)
    assert l.ativa and l.status == "ativa" and l.origem == "manual"
    l = grafo.mudar_relacao(l.id, "substitui", b.id)
    assert rotulo(l, grafo) == "B substitui A"
    assert grafo.ligar_classificada(a.id, b.id, "contradiz", 0.9, "x", "ativa") is None  # é do dono agora


# --- leitura do Haiku ----------------------------------------------------------------------------------------------
def test_interpretar_descarta_o_que_nao_vale():
    texto = 'aqui: {"ligacoes": [' + ",".join([
        '{"n": 1, "relacao": "depende_de", "sentido": "nova", "confianca": "alta", "motivo": "m"}',
        '{"n": 1, "relacao": "complementa", "confianca": "alta"}',  # repetido
        '{"n": 9, "relacao": "complementa", "confianca": "alta"}',  # fora da lista
        '{"n": 2, "relacao": "parecido", "confianca": "alta"}',  # relação desconhecida
        '{"n": 3, "relacao": "contradiz", "sentido": "nova", "confianca": "média"}',  # sem sentido nessa relação
    ]) + "]} fim"
    r = interpretar(texto, 3)
    assert [(x["n"], x["relacao"], x["sentido"], x["confianca"]) for x in r] == [(1, "depende_de", "nova", "alta"),
                                                                                (3, "contradiz", "", "media")]
    assert interpretar("sem json", 3) == [] and interpretar("{quebrado", 3) == []


# --- o ligador -----------------------------------------------------------------------------------------------------
def _acervo(grafo):
    ids = {}
    for nome, resumo in [("Python", "estudar python para automação"), ("Projeto CSM", "automação de fechamentos com python"),
                         ("Café", "café coado de manhã"), ("Corrida", "corrida no parque de manhã"),
                         ("Viagem", "viagem para Ouro Preto em julho")]:
        ids[nome] = grafo.criar_ideia(nome, origem="manual", resumo=resumo).id  # "chat" sem conversa seria órfã
    return ids


def test_liga_por_confianca_e_nao_repete(grafo):
    ids = _acervo(grafo)
    haiku = HaikuFalso({"Projeto CSM": ("depende_de", "alta", "existente"), "Corrida": ("relacionado_a", "baixa", "")})
    lig = _ligador(grafo, haiku)
    r = lig.executar([ids["Python"]])
    assert r.analisadas == 1 and r.ativas == 1 and r.custo == pytest.approx(0.001)
    l = grafo.ligacao_do_par(ids["Python"], ids["Projeto CSM"])
    assert l.status == "ativa" and l.de == ids["Projeto CSM"] and l.motivo == "por Projeto CSM"
    assert grafo.historico_recente(1)[0]["autor"] == "ligação automática"
    assert "Viagem" not in haiku.pedidos[0]  # sem palavra nem sentido em comum: nem vai para o Haiku
    assert lig.executar([ids["Python"]]).custo == 0 and len(haiku.pedidos) == 1  # já ligada: sem candidatas, sem chamada


def test_pendentes_por_marca_do_texto(grafo):
    ids = _acervo(grafo)
    lig = _ligador(grafo, HaikuFalso())
    assert len(lig.pendentes()) == 5
    lig.executar()
    assert lig.pendentes() == []
    grafo.atualizar_ideia(ids["Café"], resumo="café coado sem açúcar")
    assert [i.nome for i in lig.pendentes()] == ["Café"]


def test_rejeicao_vale_so_para_o_par(grafo):
    ids = _acervo(grafo)
    grafo.desligar(ids["Python"], ids["Projeto CSM"])
    haiku = HaikuFalso({"Projeto CSM": ("relacionado_a", "alta", ""), "Python": ("relacionado_a", "alta", "")})
    lig = _ligador(grafo, haiku)
    lig.executar([ids["Python"]])
    assert grafo.ligacao_do_par(ids["Python"], ids["Projeto CSM"]).status == "rejeitada"
    lig.executar([ids["Café"]])  # outras ideias seguem sendo ligadas normalmente (Python com Café, se o Haiku quiser)
    assert grafo.ligacao_do_par(ids["Café"], ids["Python"]) is None or grafo.ligacao_do_par(ids["Café"], ids["Python"]).ativa


def test_media_com_aprovacao_exigida_vira_sugestao(grafo):
    ids = _acervo(grafo)
    lig = _ligador(grafo, HaikuFalso({"Projeto CSM": ("relacionado_a", "media", "")}), exigir_aprovacao=True)
    assert lig.executar([ids["Python"]]).sugestoes == 1
    assert not grafo.ligacao_do_par(ids["Python"], ids["Projeto CSM"]).ativa


def test_pai_e_filhos_nao_sao_candidatos(grafo):
    ids = _acervo(grafo)
    grafo.definir_pai(ids["Projeto CSM"], ids["Python"])
    haiku = HaikuFalso()
    _ligador(grafo, haiku).executar([ids["Python"]])
    assert not haiku.pedidos or "Projeto CSM" not in haiku.pedidos[0].split("EXISTENTES")[1]


def test_para_no_teto_e_sem_modelo_nao_gasta(grafo):
    _acervo(grafo)
    grafo.somar_gasto(5.0)
    haiku = HaikuFalso()
    assert _ligador(grafo, haiku, teto_usd=1.0).executar().parou_por_teto and haiku.pedidos == []

    class SemModelo:
        disponivel = False

    assert Ligador(grafo, haiku, sem.Semelhanca(grafo, SemModelo())).executar().analisadas == 0


# --- gatilhos e ferramentas ----------------------------------------------------------------------------------------
def test_lembrar_avisa_a_ligacao(tmp_path, grafo):
    from eco.grafo.memorias import Memorias

    pasta = tmp_path / "dados"
    pasta.mkdir()
    mem = Memorias(grafo, pasta)
    mem.inicializar()
    vistos = []
    mem.ao_mudar = vistos.append
    mem.lembrar("Vitor toca violão.")
    no = mem.nos()[-1]
    mem.editar(no.id, "Vitor toca violão e guitarra.")
    assert vistos == [no.id, no.id]
    mem.ao_mudar = lambda _id: 1 / 0
    assert mem.lembrar("Outra coisa.").startswith("Guardado.")  # erro na ligação nunca impede guardar


def test_analise_liga_no_fim_da_rodada(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path))
    from eco.config import carregar_config
    from eco.grafo.analise import Analisador

    banco = Banco(tmp_path / "eco.db")
    g = Grafo(banco)
    ids = _acervo(g)
    an = Analisador(banco, g, HaikuFalso(), carregar_config())
    an.ligador = _ligador(g, HaikuFalso({"Projeto CSM": ("relacionado_a", "alta", "")}))
    r = an.executar([])
    assert r.ligacoes_novas >= 1 and g.ligacao_do_par(ids["Python"], ids["Projeto CSM"]).status == "ativa"


def test_ferramentas_decidir_e_pendentes(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path))
    from eco.config import carregar_config
    from eco.ferramentas import construir_ferramentas

    banco = Banco(tmp_path / "eco.db")
    g = Grafo(banco)
    ids = _acervo(g)
    g.ligar_classificada(ids["Café"], ids["Corrida"], "relacionado_a", 0.65, "as duas de manhã", "revisar")
    g.ligar_classificada(ids["Python"], ids["Viagem"], "relacionado_a", 0.35, "talvez", "sugestao")
    f = {x.nome: x for x in construir_ferramentas(carregar_config(), banco=banco)}
    texto = f["consultar_grafo"].executar({"tipo": "pendentes"})
    assert "relacionado a (as duas de manhã; confiança média)" in texto and "Sugestões" in texto
    f["decidir_ligacao"].executar({"ideia_a": "café", "ideia_b": "corrida", "acao": "aceitar"})
    assert g.ligacao_do_par(ids["Café"], ids["Corrida"]).origem == "manual"
    f["decidir_ligacao"].executar({"ideia_a": "python", "ideia_b": "viagem", "acao": "rejeitar"})
    assert g.ligacao_do_par(ids["Python"], ids["Viagem"]).status == "rejeitada"
    r = f["decidir_ligacao"].executar({"ideia_a": "Projeto CSM", "ideia_b": "Python", "acao": "mudar", "relacao": "depende_de"})
    assert r == "Agora: Projeto CSM depende de Python."
    with pytest.raises(ValueError, match="tipo da relação"):
        f["decidir_ligacao"].executar({"ideia_a": "café", "ideia_b": "viagem", "acao": "mudar", "relacao": "parecido"})
    assert "ligação automática" not in [x.nome for x in f.values()]
    assert f["ligar_ideias_agora"].executar({}).startswith("Sem o modelo")  # pasta de modelos dos testes é vazia
    assert "Projeto CSM depende de Python" in consulta.relacoes(g, "Projeto CSM")


def test_rotulos_das_oito_categorias():
    assert len(ligacao.RELACOES) == 8
    assert {v[0] for v in ligacao.RELACOES.values()} == {"relacionado a", "depende de", "faz parte de", "contradiz",
                                                         "complementa", "substitui", "é consequência de", "foi originado por"}
