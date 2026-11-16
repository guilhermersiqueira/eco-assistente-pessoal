"""Janela do destinatário ao enviar um rascunho (requisito 19.4 do dono, decisão D7)."""

from __future__ import annotations

import base64
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default

import pytest

from eco.ferramentas import emails, google


# --- validação ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "texto,arrumado",
    [
        ("ana@x.com", "ana@x.com"),
        (" ana@x.com ; bia@y.com.br ", "ana@x.com, bia@y.com.br"),
        ("Ana Souza <ana@x.com>", "Ana Souza <ana@x.com>"),
    ],
)
def test_destinatarios_validos(texto, arrumado):
    assert emails.validar_destinatarios(texto) == arrumado


@pytest.mark.parametrize("texto", ["", "ana", "ana@x", "ana@@x.com", "ana@x.com, joao"])
def test_destinatarios_invalidos(texto):
    with pytest.raises(ValueError):
        emails.validar_destinatarios(texto)


# --- Gmail falso com formato raw ------------------------------------------------------------------------------------
class Pedido:
    def __init__(self, r=None, efeito=None):
        self.r, self.efeito = r, efeito

    def execute(self):
        if self.efeito:
            self.efeito()
        return self.r


class Gmail:
    def __init__(self):
        m = EmailMessage()
        m["To"] = "ana@x.com"
        m["Subject"] = "Re: Prova"
        m["In-Reply-To"] = "<abc@x.com>"
        m.set_content("Confirmo presença.")
        self.raw = m.as_bytes()
        self.chamadas = []

    def users(self):
        return self

    def drafts(self):
        return self

    def get(self, userId, id, format):
        if format == "raw":
            return Pedido({"id": id, "message": {"raw": base64.urlsafe_b64encode(self.raw).decode(), "threadId": "th1"}})
        return Pedido({"id": id, "message": {"payload": {"headers": [{"name": "To", "value": "ana@x.com"},
                                                                   {"name": "Subject", "value": "Re: Prova"}],
                                                       "mimeType": "text/plain",
                                                       "body": {"data": base64.urlsafe_b64encode(b"Confirmo presenca.").decode()}}}})

    def update(self, userId, id, body):
        return Pedido({"id": id}, lambda: self.chamadas.append(("atualizar", id, body)))

    def send(self, userId, body):
        return Pedido({}, lambda: self.chamadas.append(("enviar", body)))


@pytest.fixture
def gmail(monkeypatch):
    g = Gmail()
    monkeypatch.setattr(google, "_servico", lambda nome, versao: g)
    monkeypatch.setattr(google, "exige_escopo", lambda escopo: None)
    return g


def _ferramenta(pedir):
    from eco.ferramentas import _enviador_de_rascunho

    return _enviador_de_rascunho(pedir)


def test_enviar_sem_id_usa_o_ultimo_rascunho_criado(gmail, monkeypatch):
    from eco.ferramentas import emails

    monkeypatch.setattr(emails, "_ultimo_rascunho", None)
    assert emails.ultimo_rascunho() is None
    confirmar, executar = _ferramenta(lambda t, r, v: None)
    with pytest.raises(ValueError, match="rascunho recente"):
        confirmar({})
    monkeypatch.setattr(emails, "_ultimo_rascunho", "r1")
    lidos = []
    original = emails.localizar_rascunho
    monkeypatch.setattr(emails, "localizar_rascunho", lambda i: lidos.append(i) or original(i))
    assert "ana@x.com" in confirmar({})  # leu o rascunho r1 (o destinatário do fake)
    executar({})
    assert lidos and set(lidos) == {"r1"}  # sempre esse rascunho, sem o dono informar o id


def test_janela_abre_com_o_destinatario_atual_e_trocar_atualiza_o_rascunho(gmail):
    pedidos = []
    confirmar, executar = _ferramenta(lambda t, r, v: pedidos.append((t, r, v)) or ("bia@y.com", True))
    frase = confirmar({"rascunho_id": "r1"})
    assert pedidos == [("Enviar e-mail", "Assunto: Re: Prova\nEnviar para:", "ana@x.com")]
    assert frase.startswith("Enviar o rascunho para bia@y.com, assunto Re: Prova.")  # a confirmação já fala o novo
    assert executar({"rascunho_id": "r1"}) == "E-mail enviado para bia@y.com, assunto Re: Prova."
    tipo, id_, corpo = gmail.chamadas[0]
    assert (tipo, id_) == ("atualizar", "r1") and corpo["message"]["threadId"] == "th1"  # continua na mesma conversa
    novo = message_from_bytes(base64.urlsafe_b64decode(corpo["message"]["raw"]), policy=default)
    assert novo["To"] == "bia@y.com" and novo["Subject"] == "Re: Prova" and novo["In-Reply-To"] == "<abc@x.com>"
    assert gmail.chamadas[1] == ("enviar", {"id": "r1"})


def test_mesmo_destinatario_nao_mexe_no_rascunho(gmail):
    confirmar, executar = _ferramenta(lambda t, r, v: (v, True))
    confirmar({"rascunho_id": "r1"})
    executar({"rascunho_id": "r1"})
    assert [c[0] for c in gmail.chamadas] == ["enviar"]


def test_cancelar_a_janela_nao_envia_nem_pergunta(gmail):
    from eco.ferramentas import EnvioCancelado

    confirmar, _ = _ferramenta(lambda t, r, v: ("", False))
    with pytest.raises(EnvioCancelado):
        confirmar({"rascunho_id": "r1"})
    assert gmail.chamadas == []


def test_endereco_invalido_na_janela_vira_erro_e_nada_e_enviado(gmail):
    confirmar, _ = _ferramenta(lambda t, r, v: ("bia@", True))
    with pytest.raises(ValueError, match="inválido"):
        confirmar({"rascunho_id": "r1"})
    assert gmail.chamadas == []


def test_sem_tela_segue_como_antes(gmail):
    confirmar, executar = _ferramenta(lambda t, r, v: None)  # painel sem janela (ex.: console)
    assert confirmar({"rascunho_id": "r1"}).startswith("Enviar o rascunho para ana@x.com")
    executar({"rascunho_id": "r1"})
    assert [c[0] for c in gmail.chamadas] == ["enviar"]


def test_painel_qt_pede_o_texto_pela_janela(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from eco.visual.janela import Barramento, PainelQt

    QApplication.instance() or QApplication([])
    b = Barramento()

    def responder(titulo, rotulo, valor, resposta, pronto):
        resposta["r"] = (valor.upper(), True)
        pronto.set()

    b.pedir_texto.connect(responder)
    assert PainelQt(b).pedir_texto("Enviar e-mail", "Para:", "ana@x.com") == ("ANA@X.COM", True)


def test_painel_sem_tela_nao_tem_janela():
    from eco.painel import Painel, PainelMisto

    assert Painel().pedir_texto("t", "r") is None and PainelMisto(Painel()).pedir_texto("t", "r") is None
