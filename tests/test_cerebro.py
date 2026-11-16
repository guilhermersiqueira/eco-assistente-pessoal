"""Testes do Cerebro: conversa atual na memória, "Guarde essa, Eco", espaços, pausa e o que vai para a janela/voz."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from eco.agente import Agente
from eco.cerebro import PAUSA_NOVA_CONVERSA, Cerebro, texto_para_falar, vai_para_janela
from eco.chat.armazenamento import GERAL, Banco
from eco.config import carregar_config
from eco.ferramentas import construir_ferramentas


class AgenteFalso:
    def __init__(self):
        self.contexto = None
        self.ferramentas = []
        self.pedidos = []

    def adicionar_ferramenta(self, f):
        self.ferramentas.append(f)

    def responder(self, texto, canal="voz", confirmar=None):
        self.pedidos.append((texto, canal))
        return f"resposta de {texto}"


class Relogio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def banco(tmp_path):
    b = Banco(tmp_path / "eco.db")
    yield b
    b.fechar()


@pytest.fixture
def c(banco):
    return Cerebro(AgenteFalso(), banco, Relogio())


def test_conversa_fica_so_na_memoria_ate_guardar(c, banco):
    c.responder("oi", "voz")
    c.responder("faz um código", "texto")
    assert [(f.papel, f.canal) for f in c.sessao.falas] == [("user", "voz"), ("assistant", "voz"), ("user", "texto"), ("assistant", "texto")]
    assert banco.listar_conversas(GERAL) == [] and not c.sessao.guardada


def test_guardar_grava_tudo_e_o_resto_tambem(c, banco):
    estudos = banco.criar_espaco("Estudos")
    c.responder("monta um plano")
    assert "Estudos" in c.guardar("estudos", "Plano de cálculo")  # sem diferenciar maiúscula
    c.responder("e amanhã?")
    (conv,) = banco.listar_conversas(estudos.id)
    assert conv.titulo == "Plano de cálculo"
    assert [m.conteudo for m in banco.mensagens(conv.id)] == ["monta um plano", "resposta de monta um plano", "e amanhã?", "resposta de e amanhã?"]


def test_guardar_aceita_acento_diferente_e_geral(c, banco):
    banco.criar_espaco("Finanças")
    c.responder("x")
    assert "Finanças" in c.guardar("financas", "t")
    c.nova_conversa()
    c.responder("y")
    c.guardar("geral", "outra")
    assert [x.titulo for x in banco.listar_conversas(GERAL)] == ["outra"]


def test_guardar_em_espaco_inexistente_ou_vazio_falha(c, banco):
    banco.criar_espaco("Estudos")
    with pytest.raises(ValueError, match="Ainda não há nada"):
        c.guardar("Estudos", "t")
    c.responder("oi")
    with pytest.raises(ValueError, match="Pergunte ao dono"):
        c.guardar("Trabalho", "t")


def test_guardar_de_novo_em_outro_espaco_move(c, banco):
    a, b = banco.criar_espaco("A"), banco.criar_espaco("B")
    c.responder("oi")
    c.guardar("A", "t")
    assert "já está guardada" in c.guardar("A", "t")
    assert "Mudei" in c.guardar("B", "t")
    assert banco.listar_conversas(a.id) == [] and len(banco.listar_conversas(b.id)) == 1


def test_pausa_longa_comeca_outra_conversa(c):
    c.responder("um")
    c._relogio.t += PAUSA_NOVA_CONVERSA - 1
    c.responder("dois")
    assert len(c.sessao.falas) == 4
    c._relogio.t += PAUSA_NOVA_CONVERSA + 1
    c.responder("três")
    assert [f.texto for f in c.sessao.falas] == ["três", "resposta de três"]


def test_conversa_excluida_na_tela_volta_a_ser_so_memoria(c, banco):
    c.responder("oi")
    c.guardar("Geral", "t")
    banco.excluir_conversa(c.sessao.conversa_id)
    c.responder("de novo")
    assert not c.sessao.guardada


def test_ouvintes_sao_avisados(c):
    avisos = []
    c.ao_mudar(lambda: avisos.append(1))
    c.responder("oi")
    c.guardar("Geral", "t")
    c.nova_conversa()
    assert len(avisos) == 3


def test_contexto_do_prompt_lista_espacos_e_estado(c, banco):
    banco.criar_espaco("Estudos")
    assert "ainda não guardada" in c.contexto_para_o_prompt() and "Geral, Estudos" in c.contexto_para_o_prompt()
    c.responder("oi")
    c.guardar("Estudos", "t")
    assert "já guardada no espaço Estudos" in c.contexto_para_o_prompt()


def test_janela_e_voz():
    codigo = "Veja:\n\n```python\nprint(1)\n```\n\nPronto."
    assert vai_para_janela(codigo) and vai_para_janela("a" * 701) and not vai_para_janela("curto")
    assert texto_para_falar(codigo) == "Veja: Pronto."
    assert texto_para_falar("```js\nx()\n```") == "Pus o código na tela."


# --- com o Agente de verdade (cliente da API falso) ----------------------------------------------------------------
class ClienteFalso:
    def __init__(self, respostas):
        self.chamadas, self._respostas = [], list(respostas)
        self.messages = self

    def create(self, **kw):
        self.chamadas.append(kw)
        return self._respostas.pop(0)


def _texto(t):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=t)])


def _uso(nome, entrada):
    return SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(type="tool_use", id="t1", name=nome, input=entrada)])


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    return carregar_config()


def test_agente_usa_a_ferramenta_guardar_e_o_canal_muda_o_prompt(cfg, banco):
    banco.criar_espaco("Projetos")
    cliente = ClienteFalso([_texto("Plano: A, B."), _uso("guardar_conversa", {"espaco": "Projetos", "titulo": "Plano"}), _texto("Guardei.")])
    c = Cerebro(Agente(cfg, construir_ferramentas(cfg), lambda d: True, cliente), banco)
    c.responder("monta um plano", "texto")
    sistema = "".join(b["text"] for b in cliente.chamadas[0]["system"])
    assert "ESCREVENDO" in sistema and "Espaços existentes: Geral, Projetos" in sistema
    assert any(t["name"] == "guardar_conversa" for t in cliente.chamadas[0]["tools"])
    c.responder("guarde essa, Eco", "voz")
    assert "FALADAS" in "".join(b["text"] for b in cliente.chamadas[1]["system"])
    assert c.sessao.guardada and c.sessao.espaco_nome == "Projetos"
    conv = banco.conversa(c.sessao.conversa_id)
    assert conv.titulo == "Plano" and len(banco.mensagens(conv.id)) == 4


def test_nova_conversa_tambem_limpa_o_historico_do_agente(cfg, banco):
    # D-04 da auditoria: a tela ficava vazia, mas o modelo continuava vendo as últimas trocas
    cliente = ClienteFalso([_texto("Oi, Vitor!"), _texto("Pronto, começando do zero.")])
    c = Cerebro(Agente(cfg, construir_ferramentas(cfg), lambda d: True, cliente), banco)
    c.responder("meu assunto secreto é X", "texto")
    c.nova_conversa()
    c.responder("do que eu falava?", "texto")
    enviadas = cliente.chamadas[1]["messages"]
    assert [m["content"] for m in enviadas] == ["do que eu falava?"]
    assert c.sessao.falas and c.sessao.falas[0].texto == "do que eu falava?"
