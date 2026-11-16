"""Filtro, divisão e estimativa de custo da importação (M3): tudo local, sem chamar a API."""

from __future__ import annotations

import pytest

from eco.chat.armazenamento import Banco
from eco.importacao import filtro
from eco.importacao.estado import Importacoes
from eco.importacao.modelo import ASSISTENTE, DONO, Conversa, Mensagem
from eco.uso import PRECOS, calcular_custo

MODELO = "claude-haiku-5-5"


def _conversa(uuid="c1", n_dono=5, tamanho=120, atualizada="2026-10-01", titulo="Projeto", resposta="Resposta da assistente."):
    msgs = []
    for i in range(n_dono):
        msgs.append(Mensagem(DONO, f"mensagem {i} " + "x" * tamanho))
        msgs.append(Mensagem(ASSISTENTE, resposta))
    return Conversa(uuid, titulo, "2026-09-01T10:00:00Z", atualizada, tuple(msgs))


@pytest.fixture
def estado(tmp_path):
    return Importacoes(Banco(tmp_path / "eco.db"))


# --- ignorar -----------------------------------------------------------------------------------------------------------
def test_conversa_com_poucas_mensagens_do_dono_e_ignorada():
    assert "curta" in filtro.motivo_para_ignorar(_conversa(n_dono=3, tamanho=500))
    assert filtro.motivo_para_ignorar(_conversa(n_dono=4, tamanho=100)) == ""


def test_conversa_quase_sem_texto_do_dono_e_ignorada():
    assert "sem texto" in filtro.motivo_para_ignorar(_conversa(n_dono=5, tamanho=10))


def test_respostas_da_assistente_nao_contam_como_mensagens_do_dono():
    so_assistente = Conversa("x", "t", "", "", tuple(Mensagem(ASSISTENTE, "texto longo " * 100) for _ in range(10)))
    assert filtro.motivo_para_ignorar(so_assistente)


# --- texto e divisão ---------------------------------------------------------------------------------------------------
def test_texto_traz_cabecalho_dono_inteiro_e_so_o_comeco_da_assistente():
    longa = "a" * 1000
    c = Conversa("c", "Landing da padaria", "2026-09-01T10:00:00Z", "", (Mensagem(DONO, "Quero uma  landing\npage"), Mensagem(ASSISTENTE, longa)))
    [texto] = filtro.dividir(c)
    linhas = texto.splitlines()
    assert linhas[0] == "Conversa: Landing da padaria (2026-09-01)"
    assert linhas[1] == "Dono: Quero uma landing page"  # espaços e quebras de linha normalizados
    assert linhas[2].startswith("Assistente: aaa") and len(linhas[2]) < 330 and linhas[2].endswith("…")


def test_conversa_pequena_vai_em_uma_parte_so():
    assert len(filtro.dividir(_conversa())) == 1


def test_conversa_media_divide_em_duas_partes_sem_cortar_mensagem():
    c = _conversa(n_dono=40, tamanho=500)  # ~20 mil letras
    partes = filtro.dividir(c)
    assert len(partes) == 2 and all(len(p) <= filtro.MAX_CARACTERES_PARTE for p in partes)
    juntas = "\n".join(partes)
    assert all(f"mensagem {i} " in juntas for i in range(40))  # nada se perdeu


def test_conversa_enorme_fica_com_a_primeira_e_a_ultima_parte():
    c = _conversa(n_dono=200, tamanho=500)
    partes = filtro.dividir(c)
    assert len(partes) == filtro.MAX_PARTES and all(len(p) <= filtro.MAX_CARACTERES_PARTE for p in partes)
    assert "mensagem 0 " in partes[0] and "mensagem 199 " in partes[-1]
    assert filtro.AVISO_OMITIDO in partes[-1] and "mensagem 100 " not in "\n".join(partes)


def test_mensagem_gigante_nao_estoura_a_parte():
    c = Conversa("c", "Texto colado", "", "", (Mensagem(DONO, "y" * 50_000),))
    [parte] = filtro.dividir(c)
    assert len(parte) <= filtro.MAX_CARACTERES_PARTE


def test_conversa_sem_texto_nao_gera_parte():
    assert filtro.dividir(Conversa("c", "Vazia", "", "", (Mensagem(DONO, "  "),))) == []


# --- estimativa --------------------------------------------------------------------------------------------------------
def test_estimativa_segue_a_formula_e_o_preco_do_modelo():
    partes = ["a" * 7000, "b" * 3500]
    e = filtro.estimar(partes, MODELO)
    entrada = int((7000 + filtro.TAMANHO_PROMPT) / 3.5) + int((3500 + filtro.TAMANHO_PROMPT) / 3.5)
    saida = 2 * filtro.SAIDA_POR_PARTE
    assert (e.chamadas, e.tokens_entrada, e.tokens_saida) == (2, entrada, saida)
    assert e.custo_usd == pytest.approx(calcular_custo(MODELO, entrada, saida)) and e.custo_usd > 0


def test_modelo_fora_da_tabela_tem_custo_zero():
    assert "modelo-desconhecido" not in PRECOS
    assert filtro.estimar(["a" * 1000], "modelo-desconhecido").custo_usd == 0.0


# --- plano -------------------------------------------------------------------------------------------------------------
def test_plano_separa_analisar_ignorar_e_ja_feitas(estado):
    nova, curta, feita, mudou = _conversa("nova"), _conversa("curta", n_dono=2), _conversa("feita"), _conversa("mudou")
    for c in (feita, mudou):
        estado.registrar_conversa(c)
        estado.definir_estado(c.uuid, "analisada")
    mudou_agora = Conversa("mudou", "Projeto", "", "2026-10-05", mudou.mensagens + (Mensagem(DONO, "mensagem nova " + "z" * 200),))
    p = filtro.planejar([nova, curta, feita, mudou_agora], estado, MODELO, teto_usd=2.0)
    assert {c.uuid for c in p.analisar} == {"nova", "mudou"}
    assert [(c.uuid, "curta" in m) for c, m in p.ignoradas] == [("curta", True)] and p.ja_feitas == 1
    assert p.estimativa.chamadas == 2 and not p.passa_do_teto and p.cabem == 2


def test_plano_retoma_o_que_ficou_pendente_ou_com_erro(estado):
    c = _conversa("a")
    estado.registrar_conversa(c)
    estado.definir_estado("a", "erro", erro="sem rede")
    p = filtro.planejar([c], estado, MODELO, 2.0)
    assert [x.uuid for x in p.analisar] == ["a"] and p.ja_feitas == 0


def test_plano_ordena_da_mais_recente_e_o_teto_corta_as_mais_antigas(estado):
    conversas = [_conversa(f"c{i}", atualizada=f"2026-0{i}-01", tamanho=3000) for i in range(1, 6)]
    custo_uma = filtro.estimar(filtro.dividir(conversas[0]), MODELO).custo_usd
    p = filtro.planejar(conversas, estado, MODELO, teto_usd=custo_uma * 2.5)
    assert [c.uuid for c in p.analisar] == ["c5", "c4", "c3", "c2", "c1"]
    assert p.cabem == 2 and p.passa_do_teto
    assert "cabem 2" in p.frase() and "passa do teto" in p.frase()


def test_plano_sem_conversas_ou_sem_novidade(estado):
    p = filtro.planejar([], estado, MODELO, 2.0)
    assert p.analisar == [] and p.estimativa.chamadas == 0 and "Nada novo" in p.frase()
    c = _conversa()
    estado.registrar_conversa(c)
    estado.definir_estado(c.uuid, "sem_fatos")
    p = filtro.planejar([c], estado, MODELO, 2.0)
    assert p.ja_feitas == 1 and "Nada novo" in p.frase()


def test_plano_com_modelo_sem_preco_avisa_que_nao_estima(estado):
    p = filtro.planejar([_conversa()], estado, "modelo-desconhecido", 2.0)
    assert not p.preco_conhecido and p.cabem == 1 and "não consigo estimar" in p.frase()


def test_planejar_nao_grava_nada_no_estado(estado):
    filtro.planejar([_conversa("a"), _conversa("b", n_dono=1)], estado, MODELO, 2.0)
    assert estado.contagens()["pendente"] == 0 and estado.conversa("a") is None
