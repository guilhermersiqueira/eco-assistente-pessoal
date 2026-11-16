"""Manual da Eco (`ajuda.md`) e o pedido "explique-me" (requisito 19.1 do dono)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from eco import ajuda
from eco.comandos import eh_abrir_interface, eh_pedido_de_explicacao


@pytest.mark.parametrize(
    "frase",
    ["Eco, explique-me como abrir a interface.", "Explique-me o modo foco", "ei eco me explica como funciona a memória",
     "explica pra mim o grafo", "Ei Eco, por favor, explique me como guardar uma conversa"],
)
def test_explique_me_e_reconhecido(frase):
    assert eh_pedido_de_explicacao(frase)


@pytest.mark.parametrize("frase", ["abra a interface", "qual a explicação disso?", "vou te explicar uma coisa", "o que tenho hoje"])
def test_outras_frases_nao_sao_pedido_de_explicacao(frase):
    assert not eh_pedido_de_explicacao(frase)


def test_explique_me_nao_abre_a_interface():
    # é uma pergunta sobre como abrir, não o comando de abrir
    assert not eh_abrir_interface("Eco, explique-me como abrir a interface")


@pytest.mark.parametrize(
    "pergunta,secao",
    [
        ("explique-me como abrir a interface", "Abrir a interface"),
        ("explique-me o modo foco", "Modo foco"),
        ("como eu apago uma tarefa", "Tarefas"),
        ("como funciona a memória", "Memória"),
        ("explique-me como ver quanto gastei", "Custos e backup"),
        ("explique-me como calar a eco", "Silenciar, calar e repetir"),
        ("explique-me o grafo", "Grafo de ideias"),
        ("explique-me como confirmar uma ação", "Confirmações"),
    ],
)
def test_busca_acha_a_secao_certa_primeiro(pergunta, secao):
    achadas = ajuda.buscar(pergunta)
    assert achadas and achadas[0].titulo == secao


def test_pergunta_fora_do_manual_devolve_os_topicos():
    assert ajuda.buscar("explique-me como fazer bolo de cenoura") == []
    texto = ajuda.trechos_para("explique-me como fazer bolo de cenoura")
    assert texto.startswith("Nenhuma seção") and "Modo foco" in texto


def test_manual_tem_todas_as_secoes_com_palavras_e_texto():
    secoes = ajuda.secoes()
    assert len(secoes) >= 15
    for s in secoes:
        assert s.palavras and len(s.texto) > 80, s.titulo


def test_manual_nao_fala_do_foco_pela_agenda_retirado():
    texto = " ".join(s.texto for s in ajuda.secoes()).lower()
    assert "foco" in texto and "título" not in ajuda.buscar("modo foco")[0].texto.lower()


def _cliente(respostas):
    chamadas = []

    class Mensagens:
        def create(self, **kw):
            chamadas.append(kw)
            return respostas.pop(0)

    return SimpleNamespace(messages=Mensagens(), chamadas=chamadas)


def _texto(t):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=t)], usage=None)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    from eco.config import carregar_config

    return carregar_config()


def test_explique_me_leva_o_trecho_do_manual_junto_sem_chamada_extra(cfg):
    from eco.agente import Agente
    from eco.ferramentas import construir_ferramentas

    cliente = _cliente([_texto("É só dizer 'abra a interface'."), _texto("Claro.")])
    agente = Agente(cfg, construir_ferramentas(cfg), lambda d: True, cliente)
    agente.responder("Eco, explique-me como abrir a interface")
    assert len(cliente.chamadas) == 1  # uma chamada só
    enviado = cliente.chamadas[0]["messages"][-1]["content"]
    assert "Abrir a interface:" in enviado and "manual da Eco" in enviado
    agente.responder("obrigado")
    historico = cliente.chamadas[1]["messages"]
    assert historico[0]["content"] == "Eco, explique-me como abrir a interface"  # o histórico guarda só a fala do dono


def test_ferramenta_manual_da_eco(cfg):
    from eco.ferramentas import construir_ferramentas

    f = {x.nome: x for x in construir_ferramentas(cfg)}["manual_da_eco"]
    assert f.confirmar is None and "Modo foco:" in f.executar({"pergunta": "como entro em modo foco?"})
