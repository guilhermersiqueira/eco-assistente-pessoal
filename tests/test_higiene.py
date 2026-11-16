"""Higiene da memória (E7): conferência ao guardar (igual, contradição, pendência), duração e validade, propostas da
revisão (arquivar, juntar, agrupar, contradição), decisões do dono, proteção, teto, custos e ferramentas."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eco.chat.armazenamento import Banco
from eco.grafo import consulta
from eco.grafo import semelhanca as sem
from eco.grafo.analise import Resposta
from eco.grafo.dados import Grafo
from eco.grafo.higiene import Higiene
from eco.grafo.memorias import Memorias

sys.path.insert(0, str(Path(__file__).parent))
from test_semelhanca import CodificadorFalso  # noqa: E402

AGORA = datetime(2026, 11, 13, tzinfo=timezone.utc)


class Haiku:
    """Devolve a resposta pronta (dict) e conta as chamadas."""

    def __init__(self, *respostas, custo=0.001):
        self.respostas, self.chamadas, self.custo = list(respostas), [], custo

    def completar(self, sistema, usuario):
        self.chamadas.append(usuario)
        return Resposta(json.dumps(self.respostas.pop(0) if self.respostas else {}), self.custo)


@pytest.fixture
def mem(tmp_path):
    pasta = tmp_path / "dados"
    pasta.mkdir()
    m = Memorias(Grafo(Banco(tmp_path / "eco.db")), pasta)
    m.inicializar()
    return m


def _hig(mem, haiku=None, **kw):
    return Higiene(mem.grafo, haiku, sem.Semelhanca(mem.grafo, CodificadorFalso()), memorias=mem, **kw)


# --- escrita -------------------------------------------------------------------------------------------------------
def test_texto_igual_reforca_sem_chamar_o_haiku(mem):
    velha = mem.lembrar_no("Vitor trabalha das 8h às 17h.")
    haiku = Haiku()
    v = _hig(mem, haiku).verificar_escrita("Vitor trabalha das 8h às 17h.")
    assert v.tipo == "igual" and v.existente.id == velha.id and haiku.chamadas == []


def test_haiku_decide_igual_conflito_pendencia_e_nova(mem):
    mem.lembrar_no("Vitor trabalha das 8h às 17h.")
    mem.lembrar_no("Vitor gosta de café coado.")
    item = lambda rel, conf: {"itens": [{"n": 1, "relacao": rel, "confianca": conf, "motivo": "teste"}]}  # noqa: E731
    h = _hig(mem, Haiku(item("igual", "alta"), item("atualiza", "alta"), item("contradiz", "media"), {"itens": []}))
    assert h.verificar_escrita("Vitor trabalha das 8h até as 17h.").tipo == "igual"
    v = h.verificar_escrita("Vitor agora trabalha das 9h às 18h.")
    assert v.tipo == "conflito" and v.relacao == "atualiza"
    assert "NÃO GUARDEI AINDA" in v.mensagem("x") and "substituir_lembranca" in v.mensagem("x")
    assert h.verificar_escrita("Vitor trabalha das 10h.").tipo == "pendencia"
    assert h.verificar_escrita("Vitor trabalha com Python.").tipo == "nova"


def test_lembranca_protegida_nunca_bloqueia_vira_pendencia(mem):
    velha = mem.lembrar_no("Vitor trabalha das 8h às 17h.")
    mem.fixar(velha.id, True)
    h = _hig(mem, Haiku({"itens": [{"n": 1, "relacao": "contradiz", "confianca": "alta", "motivo": "x"}]}))
    assert h.verificar_escrita("Vitor trabalha das 9h às 18h.").tipo == "pendencia"


def test_ferramenta_lembrar_com_cuidado(mem):
    from eco.ferramentas import _lembrar_com_cuidado

    mem.lembrar_no("Vitor trabalha das 8h às 17h.")
    conflito = {"itens": [{"n": 1, "relacao": "atualiza", "confianca": "alta", "motivo": "horário mudou"}]}
    h = _hig(mem, Haiku(conflito, {"itens": [{"n": 1, "relacao": "contradiz", "confianca": "media", "motivo": "?"}]}))
    assert _lembrar_com_cuidado(mem, h, {"fato": "Vitor trabalha das 9h às 18h."}).startswith("NÃO GUARDEI AINDA")
    assert len(mem.fatos()) == 1  # nada foi guardado antes do dono responder
    r = _lembrar_com_cuidado(mem, h, {"fato": "Vitor trabalha das 10h às 19h.", "duracao": "duradoura"})
    assert r.startswith("Guardado. Anotei uma possível contradição") and len(h.propostas()) == 1
    assert next(n for n in mem.nos() if "10h" in n.resumo).durabilidade == "duradoura"
    r = _lembrar_com_cuidado(mem, h, {"fato": "Vitor trabalha das 9h às 18h.", "mesmo_assim": True})
    assert r.startswith("Guardado.") and len(mem.fatos()) == 3
    assert _lembrar_com_cuidado(mem, h, {"fato": "Vitor trabalha das 9h às 18h."}).startswith("Isso já estava guardado")
    assert len(mem.fatos()) == 3


def test_duracao_e_validade(mem):
    no = mem.lembrar_no("Prova de cálculo na sexta.", "temporaria", "2026-11-20")
    assert (no.durabilidade, no.validade) == ("temporaria", "2026-11-20")
    assert mem.grafo.historico(no.id)[-1]["acao"] == "duracao"
    mem.grafo.definir_duracao(no.id, "eterna", "amanhã")  # inválidos viram vazio
    assert (mem.grafo.ideia(no.id).durabilidade, mem.grafo.ideia(no.id).validade) == ("", "")


# --- revisão -------------------------------------------------------------------------------------------------------
def test_propostas_locais_de_arquivar_e_contradicao(mem):
    g = mem.grafo
    prova = mem.lembrar_no("Prova de cálculo na sexta.", "temporaria", "2026-11-01")
    viagem = mem.lembrar_no("Viagem para Ouro Preto em julho.", "temporaria")
    g.banco._executar("UPDATE ideias SET atualizado_em = ? WHERE id = ?", ((AGORA - timedelta(days=90)).isoformat(), viagem.id))
    fixa = mem.lembrar_no("Aniversário da mãe em 3 de março.", "temporaria", "2026-03-03")
    mem.fixar(fixa.id, True)
    velha, nova = mem.lembrar_no("Mora em Campinas."), mem.lembrar_no("Mudou para Curitiba.")
    g.ligar_classificada(nova.id, velha.id, "substitui", 0.9, "mudou", "ativa", de=nova.id)
    a, b = mem.lembrar_no("Trabalha às 8h."), mem.lembrar_no("Trabalha às 9h.")
    g.ligar_classificada(a.id, b.id, "contradiz", 0.9, "horários diferentes", "ativa")
    h = _hig(mem)
    r = h.revisar(usar_ia=False, agora=AGORA)
    assert r.novas["arquivar"] == 3 and r.novas["contradicao"] == 1
    alvos = {p.ideias[0] for p in h.propostas() if p.tipo == "arquivar"}
    assert alvos == {prova.id, viagem.id, velha.id} and fixa.id not in alvos  # a protegida nunca entra
    assert h.revisar(usar_ia=False, agora=AGORA).novas == {"arquivar": 0, "contradicao": 0}  # não repete


def test_ligacao_cuja_ideia_mudou_vai_para_revisar(mem):
    g = mem.grafo
    a, b = g.criar_ideia("A", origem="manual", resumo="x"), g.criar_ideia("B", origem="manual", resumo="y")
    g.ligar_classificada(a.id, b.id, "relacionado_a", 0.9, "m", "ativa")
    h = _hig(mem)
    assert h.revisar(usar_ia=False).ligacoes_para_revisar == 0
    g.atualizar_ideia(a.id, resumo="mudou")
    assert h.revisar(usar_ia=False).ligacoes_para_revisar == 1
    assert g.ligacao_do_par(a.id, b.id).status == "revisar" and g.ligacao_do_par(a.id, b.id).ativa  # não sai


def test_duplicatas_e_grupos_com_o_haiku(mem):
    g = mem.grafo
    c1 = mem.lembrar_no("Vitor gosta de café coado sem açúcar.")
    c2 = mem.lembrar_no("Vitor gosta de café coado.")
    ids = [g.criar_ideia(n, origem="manual", resumo=r).id for n, r in
           [("Python básico", "aulas de python"), ("Projeto python", "script em python"), ("Curso python", "curso de python")]]
    g.ligar(ids[0], ids[1])
    g.ligar(ids[1], ids[2])
    haiku = Haiku({"pares": [{"n": 1, "mesma": True, "texto": "Vitor gosta de café coado sem açúcar.", "motivo": "repetida"}]},
                  {"grupos": [{"n": 1, "agrupar": True, "nome": "Aprendizado de Python", "motivo": "tudo sobre Python"}]})
    h = _hig(mem, haiku)
    r = h.revisar(agora=AGORA)
    assert r.novas["fundir"] == 1 and r.novas["agrupar"] == 1 and r.custo == pytest.approx(0.002)
    texto = h.listar()
    assert "juntar" in texto and "Aprendizado de Python" in texto
    fundir = next(p for p in h.propostas() if p.tipo == "fundir")
    assert set(fundir.ideias) == {c1.id, c2.id}


def test_recusada_nao_volta(mem):
    mem.lembrar_no("Prova na sexta.", "temporaria", "2026-11-01")
    h = _hig(mem)
    h.revisar(usar_ia=False, agora=AGORA)
    assert h.recusar(h.proposta_numero(1)).startswith("Combinado")
    h.revisar(usar_ia=False, agora=AGORA)
    assert h.propostas() == []


def test_aplicar_arquivar_e_contradicao(mem):
    g = mem.grafo
    prova = mem.lembrar_no("Prova na sexta.", "temporaria", "2026-11-01")
    a, b = mem.lembrar_no("Trabalha às 8h."), mem.lembrar_no("Trabalha às 9h.")
    g.ligar_classificada(a.id, b.id, "contradiz", 0.9, "horários", "ativa")
    h = _hig(mem)
    h.revisar(usar_ia=False, agora=AGORA)
    arq = next(p for p in h.propostas() if p.tipo == "arquivar")
    assert h.aplicar(arq).startswith("Arquivei") and g.ideia(prova.id).arquivada
    assert "Prova na sexta" not in (mem.pasta / "memoria.md").read_text(encoding="utf-8")  # a cópia acompanha
    con = next(p for p in h.propostas() if p.tipo == "contradicao")
    with pytest.raises(ValueError, match="Diga qual vale"):
        h.aplicar(con)
    h.aplicar(con, "segunda")  # "primeira" e "segunda" seguem a ordem em que a proposta lista as duas
    assert g.ideia(con.ideias[0]).arquivada and not g.ideia(con.ideias[1]).arquivada and h.propostas() == []


def test_juntar_preserva_origens_ligacoes_e_historico(mem):
    g = mem.grafo
    conv = g.banco.criar_conversa(None, "Conversa do café")
    a = g.criar_ideia("Café", origem="manual", resumo="gosta de café")
    b = g.criar_ideia("Café coado", origem="manual", resumo="café coado")
    outra = g.criar_ideia("Manhã", origem="manual", resumo="rotina da manhã")
    g.adicionar_fonte(b.id, conv.id)
    g.ligar_classificada(b.id, outra.id, "relacionado_a", 0.9, "de manhã", "ativa")
    h = _hig(mem)
    h._propor("fundir", [a.id, b.id], "repetidas", {"texto": "Gosta de café coado."})
    texto = h.aplicar(h.proposta_numero(1))
    assert texto.startswith("Juntei")
    assert g.ideia(a.id).resumo == "Gosta de café coado." and g.ideia(b.id).arquivada
    assert [f.titulo for f in g.fontes(a.id)] == ["Conversa do café"]
    assert g.ligacao_do_par(a.id, outra.id).motivo == "de manhã"
    assert any(e["acao"] == "fundir" and e["antes"]["resumo_antigo"] == "café coado" for e in g.historico(a.id))


def test_agrupar_cria_o_assunto_e_poe_dentro(mem):
    g = mem.grafo
    ids = [g.criar_ideia(n, origem="manual").id for n in ("P1", "P2", "P3")]
    h = _hig(mem)
    h._propor("agrupar", ids, "tudo Python", {"nome": "Aprendizado de Python"})
    h.aplicar(h.proposta_numero(1))
    pai = next(i for i in g.listar_ideias() if i.nome == "Aprendizado de Python")
    assert pai.nivel == "tronco" and {f.id for f in g.filhos(pai.id)} == set(ids)


def test_teto_e_agenda(mem):
    mem.lembrar_no("Gosta de café coado.")
    mem.lembrar_no("Gosta de café coado sem açúcar.")
    mem.grafo.somar_gasto(5.0)
    haiku = Haiku()
    h = _hig(mem, haiku, teto_usd=1.0)
    assert h.revisar(agora=AGORA).parou_por_teto and haiku.chamadas == []
    assert h.verificar_escrita("Gosta de café.").tipo == "nova" and haiku.chamadas == []  # sem gastar acima do teto
    assert not h.precisa_revisar(7, AGORA + timedelta(days=3)) and h.precisa_revisar(7, AGORA + timedelta(days=8))
    assert not h.precisa_revisar(0)


# --- ferramentas e consultas ---------------------------------------------------------------------------------------
def test_ferramentas_da_higiene(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    (tmp_path / "dados").mkdir()
    from eco.config import carregar_config
    from eco.ferramentas import construir_ferramentas

    banco = Banco(tmp_path / "eco.db")
    f = {x.nome: x for x in construir_ferramentas(carregar_config(), banco=banco)}
    f["lembrar"].executar({"fato": "Vitor trabalha das 8h às 17h.", "duracao": "duradoura"})
    assert f["lembrar"].executar({"fato": "Vitor trabalha das 8h às 17h."}).startswith("Isso já estava guardado")
    frase = f["substituir_lembranca"].confirmar({"trecho_antigo": "8h", "novo_fato": "Vitor trabalha das 9h às 18h."})
    assert "A antiga fica arquivada" in frase
    assert f["substituir_lembranca"].executar({"trecho_antigo": "8h", "novo_fato": "Vitor trabalha das 9h às 18h."}).startswith("Pronto")
    g = Grafo(banco)
    assert [i.resumo for i in g.arquivadas()] == ["Vitor trabalha das 8h às 17h."]
    assert "9h" in (tmp_path / "dados" / "memoria.md").read_text(encoding="utf-8")
    l = next(x for x in g.listar_ligacoes() if x.relacao == "substitui")
    assert l.de == next(i.id for i in g.listar_ideias() if "9h" in i.resumo)  # a nova substitui a antiga
    nova = g.criar_fato("Prova na sexta.", "Prova")
    g.definir_duracao(nova.id, "temporaria", "2020-01-01")
    assert "arquivar" in f["revisar_memoria"].executar({})
    assert "1. arquivar" in f["consultar_grafo"].executar({"tipo": "propostas"})
    assert f["aplicar_proposta"].confirmar({"numero": 1}).startswith("Aplicar a proposta: arquivar")
    with pytest.raises(ValueError, match="Não há proposta número 5"):
        f["aplicar_proposta"].confirmar({"numero": 5})
    assert f["recusar_proposta"].confirmar is None
    f["aplicar_proposta"].executar({"numero": 1})
    assert g.ideia(nova.id).arquivada
    custos = f["consultar_grafo"].executar({"tipo": "custos"})
    assert "Gasto do mês com o grafo" in custos and "limite físico" in custos and "US$ 0 (roda no computador)" in custos


def test_resumo_mostra_propostas_pendentes(mem):
    mem.lembrar_no("Prova na sexta.", "temporaria", "2026-11-01")
    _hig(mem).revisar(usar_ia=False, agora=AGORA)
    assert "1 proposta(s) de limpeza" in consulta.resumo(mem.grafo)
