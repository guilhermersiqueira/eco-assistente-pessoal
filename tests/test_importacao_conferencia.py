"""Conferência dos fatos propostos com a memória (M5): duplicado descarta, conflito anota, novo passa. O Haiku é simulado."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from eco.chat.armazenamento import Banco
from eco.grafo import semelhanca as sem
from eco.grafo.analise import Resposta
from eco.grafo.dados import Grafo
from eco.grafo.higiene import Higiene
from eco.grafo.memorias import Memorias
from eco.importacao.conferencia import MAX_ERROS_SEGUIDOS, Conferencia
from eco.importacao.estado import COLUNAS_PROPOSTAS, Importacoes
from eco.importacao.modelo import DONO, Conversa, Mensagem

sys.path.insert(0, str(Path(__file__).parent))
from test_semelhanca import CodificadorFalso  # noqa: E402

ANTIGA = "Vitor trabalha das 8h às 17h."
NOVA = "Vitor agora trabalha das 9h às 18h."


class Haiku:
    """Respostas prontas (dict ou exceção), na ordem; conta as chamadas."""

    def __init__(self, *respostas, custo=0.001):
        self.respostas, self.custo, self.chamadas = list(respostas), custo, []

    def completar(self, sistema, usuario):
        self.chamadas.append(usuario)
        r = self.respostas.pop(0) if self.respostas else {"itens": []}
        if isinstance(r, Exception):
            raise r
        return Resposta(json.dumps(r), self.custo)


def _item(relacao, confianca="alta", motivo="teste"):
    return {"itens": [{"n": 1, "relacao": relacao, "confianca": confianca, "motivo": motivo}]}


@pytest.fixture
def mem(tmp_path):
    pasta = tmp_path / "dados"
    pasta.mkdir()
    m = Memorias(Grafo(Banco(tmp_path / "eco.db")), pasta)
    m.inicializar()
    m.lembrar_no(ANTIGA)
    m.lembrar_no("Vitor gosta de café coado.")
    return m


@pytest.fixture
def estado(mem):
    e = Importacoes(mem.grafo.banco)
    e.registrar_conversa(Conversa("c1", "Conversa", "2026-09-30T09:00:00Z", "2026-10-01", (Mensagem(DONO, "x"),)))
    return e


def _conferencia(mem, estado, haiku=None, teto=0.0):
    hig = Higiene(mem.grafo, haiku, sem.Semelhanca(mem.grafo, CodificadorFalso()), memorias=mem)
    return Conferencia(estado, hig, teto)


# --- duplicado ---------------------------------------------------------------------------------------------------------
def test_texto_igual_a_uma_lembranca_e_descartado_sem_chamar_o_haiku_e_sem_tocar_a_memoria(mem, estado):
    antiga = next(i for i in mem.nos() if i.resumo == ANTIGA)
    id_ = estado.propor("c1", "vitor trabalha das 8h às 17h")  # só outras letras e sem o ponto
    haiku = Haiku()
    r = _conferencia(mem, estado, haiku).executar()
    assert (r.conferidas, r.duplicadas, r.conflitos, r.novas) == (1, 1, 0, 0) and haiku.chamadas == []
    p = estado.proposta(id_)
    assert p.estado == "duplicada" and ANTIGA in p.motivo and p.ideia_id == antiga.id and estado.propostas() == []
    depois = mem.grafo.ideia(antiga.id)
    assert depois.usos == antiga.usos and depois.resumo == ANTIGA  # a importação não reforça nem altera a memória
    assert len(mem.nos()) == 2


def test_haiku_diz_que_e_igual_com_outras_palavras(mem, estado):
    id_ = estado.propor("c1", "Vitor trabalha das 8h até as 17h.")
    r = _conferencia(mem, estado, Haiku(_item("igual"))).executar()
    assert r.duplicadas == 1 and estado.proposta(id_).estado == "duplicada" and ANTIGA in estado.proposta(id_).motivo


# --- conflito ----------------------------------------------------------------------------------------------------------
def test_contradicao_continua_pendente_com_a_lembranca_em_conflito(mem, estado):
    antiga = next(i for i in mem.nos() if i.resumo == ANTIGA)
    id_ = estado.propor("c1", NOVA, "agora entro às 9h")
    r = _conferencia(mem, estado, Haiku(_item("atualiza", "alta", "horário novo"))).executar()
    assert (r.conflitos, r.duplicadas, r.novas) == (1, 0, 0)
    p = estado.proposta(id_)
    assert p.estado == "pendente" and p.com_conflito and p.conferida_em
    assert (p.conflito_ideia_id, p.conflito_texto, p.conflito_relacao, p.conflito_motivo) == (antiga.id, ANTIGA, "atualiza", "horário novo")
    assert [x.id for x in estado.propostas()] == [id_]  # continua na revisão
    assert any(i.resumo == ANTIGA and not i.arquivada for i in mem.nos())  # a antiga não foi mexida


def test_contradicao_de_media_confianca_tambem_anota(mem, estado):
    id_ = estado.propor("c1", "Vitor trabalha das 10h.")
    _conferencia(mem, estado, Haiku(_item("contradiz", "media"))).executar()
    p = estado.proposta(id_)
    assert p.com_conflito and p.conflito_relacao == "contradiz"


# --- novo --------------------------------------------------------------------------------------------------------------
def test_fato_novo_segue_pendente_e_marcado_como_conferido(mem, estado):
    id_ = estado.propor("c1", "Vitor trabalha com Python.")
    r = _conferencia(mem, estado, Haiku({"itens": []})).executar()
    assert (r.novas, r.conflitos, r.duplicadas) == (1, 0, 0)
    p = estado.proposta(id_)
    assert p.estado == "pendente" and not p.com_conflito and p.conferida_em
    assert estado.sem_conferencia() == []
    assert "1 são novos" in r.frase()


def test_nao_confere_duas_vezes_a_mesma_proposta(mem, estado):
    estado.propor("c1", "Vitor trabalha com Python.")
    haiku = Haiku({"itens": []})
    _conferencia(mem, estado, haiku).executar()
    r = _conferencia(mem, estado, haiku).executar()
    assert r.conferidas == 0 and len(haiku.chamadas) == 1


def test_so_confere_o_que_esta_pendente(mem, estado):
    a, b = estado.propor("c1", "Fato qualquer A sobre o dono."), estado.propor("c1", "Fato qualquer B sobre o dono.")
    estado.decidir(b, "rejeitada")
    r = _conferencia(mem, estado, Haiku()).executar()
    assert r.conferidas == 1 and estado.proposta(b).conferida_em == ""


# --- falhas, teto e interrupção ------------------------------------------------------------------------------------------
def test_falha_de_rede_deixa_a_proposta_sem_conferir_e_tenta_de_novo_depois(mem, estado):
    id_ = estado.propor("c1", NOVA)
    r = _conferencia(mem, estado, Haiku(ConnectionError("sem rede"))).executar()
    assert r.conferidas == 0 and len(r.erros) == 1 and "sem rede" in r.erros[0] and "não consegui conferir" in r.frase()
    assert estado.proposta(id_).conferida_em == "" and [p.id for p in estado.sem_conferencia()] == [id_]
    r2 = _conferencia(mem, estado, Haiku(_item("contradiz"))).executar()
    assert r2.conflitos == 1 and estado.proposta(id_).com_conflito


def test_para_depois_de_tres_erros_seguidos(mem, estado):
    for i in range(6):
        estado.propor("c1", f"Vitor trabalha com a linguagem número {i}.")
    haiku = Haiku(*[ConnectionError("fora do ar")] * 10)
    r = _conferencia(mem, estado, haiku).executar()
    assert len(haiku.chamadas) == MAX_ERROS_SEGUIDOS and r.parou_por_erros and r.restantes == 3


def test_teto_de_custo_para_a_conferencia(mem, estado):
    for i in range(5):
        estado.propor("c1", f"Vitor trabalha com a linguagem número {i}.")
    haiku = Haiku(custo=0.01)
    r = _conferencia(mem, estado, haiku, teto=0.015).executar()
    assert len(haiku.chamadas) == 2 and r.parou_por_teto and r.restantes == 3 and len(estado.sem_conferencia()) == 3


def test_teto_considera_o_que_a_extracao_ja_gastou(mem, estado):
    estado.propor("c1", "Vitor trabalha com Python.")
    haiku = Haiku()
    r = _conferencia(mem, estado, haiku, teto=1.0).executar(gasto_antes=1.0)
    assert r.parou_por_teto and haiku.chamadas == [] and r.restantes == 1


def test_interromper_pelo_dono(mem, estado):
    for i in range(3):
        estado.propor("c1", f"Vitor trabalha com a linguagem número {i}.")
    pedidos = iter([False, True])
    r = _conferencia(mem, estado, Haiku()).executar(parar=lambda: next(pedidos))
    assert r.interrompido and r.conferidas == 1 and r.restantes == 2


def test_progresso_avisa_a_cada_fato_conferido(mem, estado):
    estado.propor("c1", "Vitor trabalha com Python.")
    estado.propor("c1", "Vitor trabalha com Excel.")
    avisos = []
    _conferencia(mem, estado, Haiku()).executar(ao_progresso=lambda n, total, r: avisos.append((n, total, r.conferidas)))
    assert avisos == [(1, 2, 1), (2, 2, 2)]


def test_sem_haiku_so_pega_o_texto_igual(mem, estado):
    igual, outra = estado.propor("c1", ANTIGA), estado.propor("c1", NOVA)
    r = _conferencia(mem, estado, None).executar()
    assert r.duplicadas == 1 and r.novas == 1 and estado.proposta(igual).estado == "duplicada"
    assert estado.proposta(outra).estado == "pendente" and not estado.proposta(outra).com_conflito


# --- estado: edição e colunas ------------------------------------------------------------------------------------------------
def test_editar_o_texto_pede_nova_conferencia_e_limpa_o_conflito(mem, estado):
    id_ = estado.propor("c1", NOVA)
    _conferencia(mem, estado, Haiku(_item("atualiza"))).executar()
    assert estado.proposta(id_).com_conflito
    estado.editar_texto(id_, "Vitor trabalha das 9h às 18h, três vezes por semana.")
    p = estado.proposta(id_)
    assert p.conferida_em == "" and not p.com_conflito and p.conflito_texto == "" and [x.id for x in estado.sem_conferencia()] == [id_]


def test_tabela_criada_antes_da_conferencia_ganha_as_colunas_sem_perder_dados(mem):
    banco = mem.grafo.banco
    e = Importacoes(banco)
    e.registrar_conversa(Conversa("c1", "Conversa", "", "", (Mensagem(DONO, "x"),)))
    id_ = e.propor("c1", "Um fato antigo sobre o dono.")
    for coluna in COLUNAS_PROPOSTAS:  # volta ao desenho de antes da M5
        banco._executar(f"ALTER TABLE importacao_propostas DROP COLUMN {coluna}")
    reaberto = Importacoes(banco)
    p = reaberto.proposta(id_)
    assert p.texto == "Um fato antigo sobre o dono." and p.conferida_em == "" and not p.com_conflito
    assert [x.id for x in reaberto.sem_conferencia()] == [id_]
