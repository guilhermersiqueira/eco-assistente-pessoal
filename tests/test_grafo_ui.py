"""Testes da aba Grafo (Qt offscreen): análise ao abrir, cartão ao passar o mouse, clique na conversa, interação."""

import json
import time

import pytest

pytest.importorskip("PySide6")

from eco.grafo.analise import Resposta  # noqa: E402


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    from eco.config import carregar_config

    return carregar_config()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class Falso:
    def __init__(self, *respostas):
        self.respostas, self.chamadas = list(respostas), 0

    def completar(self, sistema, usuario):
        self.chamadas += 1
        return Resposta(json.dumps(self.respostas.pop(0)), 0.0005)


RESP = {
    "ideias": [
        {"nome": "Reserva de emergência", "resumo": "Seis meses de gastos."},
        {"nome": "Tesouro Selic", "resumo": "Onde guardar."},
        {"nome": "Renda fixa", "resumo": "Depois da reserva."},
    ],
    "ligacoes": [
        {"de": "Reserva de emergência", "para": "Tesouro Selic", "motivo": "fica no Selic"},
        {"de": "Reserva de emergência", "para": "Renda fixa", "motivo": "vem antes"},
    ],
}


def _esperar(qapp, cond, s=5):
    fim = time.time() + s
    while time.time() < fim:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def _montar(cfg, tmp_path, qapp, *respostas):
    from eco.chat.armazenamento import Banco
    from eco.visual.grafo import PainelGrafo

    banco = Banco(tmp_path / "eco.db")
    esp = banco.criar_espaco("Investimentos", "#5b9dff")
    conv = banco.criar_conversa(esp.id, "Minha reserva")
    banco.adicionar_mensagem(conv.id, "user", "Quero montar uma reserva de emergência e depois investir em renda fixa.")
    banco.adicionar_mensagem(conv.id, "assistant", "Comece com seis meses de gastos em Tesouro Selic e depois diversifique.")
    falso = Falso(*respostas)
    p = PainelGrafo(cfg, banco, falso, confirmar=lambda t: True)
    p.resize(900, 600)
    p.show()
    qapp.processEvents()
    return p, banco, esp, conv, falso


def _no(p, nome):
    return next(i for i, n in p.vista.nos.items() if n.ideia.nome == nome)


def _evento(v, tipo, ponto, botao=None):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    b = botao or Qt.MouseButton.NoButton
    tipos = {"move": QEvent.Type.MouseMove, "press": QEvent.Type.MouseButtonPress, "release": QEvent.Type.MouseButtonRelease}
    lp = QPointF(ponto)
    return QMouseEvent(tipos[tipo], lp, v.mapToGlobal(lp), b, b if tipo == "press" else Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)


def _mover(v, ponto):
    v.mouseMoveEvent(_evento(v, "move", ponto))


def _clicar(v, ponto):
    from PySide6.QtCore import Qt

    v.mousePressEvent(_evento(v, "press", ponto, Qt.MouseButton.LeftButton))
    v.mouseReleaseEvent(_evento(v, "release", ponto, Qt.MouseButton.LeftButton))


def _ponto(p, id_):
    from PySide6.QtCore import QPoint

    if hasattr(p.vista, "assentar"):
        p.vista.assentar()  # a orbe anima as ideias saindo do núcleo: pula para a posição final
    t = p.vista._ponto(id_)
    return QPoint(round(t.x()), round(t.y()))


def test_abrir_a_aba_analisa_so_o_que_mudou_e_mostra_as_ideias(cfg, qapp, tmp_path):
    p, banco, esp, conv, falso = _montar(cfg, tmp_path, qapp, RESP)
    assert p.vista.nos == {} and "Ainda não" not in p.status.text()
    p.ao_abrir()
    assert _esperar(qapp, lambda: not p.rodando and len(p.vista.nos) == 3)
    assert len(p.vista.ligacoes) == 3 and falso.chamadas == 1 and "3 ideias" in p.status.text()  # 2 da análise + 1 par só da mesma sessão
    p.ao_abrir()  # abrir de novo: nada mudou, não chama o modelo
    assert falso.chamadas == 1 and "Em dia" in p.status.text()


def test_passar_o_mouse_mostra_titulo_e_conversa_e_clicar_nela_emite_o_sinal(cfg, qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    p, banco, esp, conv, _ = _montar(cfg, tmp_path, qapp, RESP)
    p.ao_abrir()
    assert _esperar(qapp, lambda: len(p.vista.nos) == 3 and not p.rodando)
    escolhidas = []
    p.abrir_conversa.connect(escolhidas.append)
    alvo = _no(p, "Tesouro Selic")
    _mover(p.vista, _ponto(p, alvo))
    qapp.processEvents()
    assert p.vista.hover == alvo and p.vista.cartao.isVisibleTo(p.vista)
    textos = [w.text() for w in p.vista.cartao.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)]
    assert "Tesouro Selic" in textos and any(t.startswith("Espaço: Investimentos") for t in textos)
    botoes = p.vista.cartao.findChildren(__import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton)
    conversas = [b for b in botoes if "Minha reserva" in b.text()]  # os outros são as ligações (E8: clicáveis)
    assert len(conversas) == 1 and "Investimentos" in conversas[0].text()
    conversas[0].click()
    assert escolhidas == [conv.id]
    assert Qt  # (import usado só para garantir o módulo)


def test_clique_na_conversa_do_cartao_abre_a_aba_chat_direto_nela(cfg, qapp, tmp_path):
    from eco.chat.armazenamento import Banco
    from eco.visual.chat import PainelChat
    from eco.visual.grafo import PainelGrafo
    from eco.visual.principal import JanelaPrincipal

    banco = Banco(tmp_path / "eco.db")
    a, b = banco.criar_espaco("Investimentos"), banco.criar_espaco("Estudos")
    c1, c2 = banco.criar_conversa(a.id, "Reserva"), banco.criar_conversa(b.id, "Prova")
    banco.adicionar_mensagem(c1.id, "user", "pergunta sobre reserva")
    banco.adicionar_mensagem(c2.id, "user", "pergunta sobre a prova")
    chat = PainelChat(cfg, None, banco, object(), openai=object())
    grafo = PainelGrafo(cfg, banco, Falso(), confirmar=lambda t: True)
    j = JanelaPrincipal(cfg, chat=chat, grafo=grafo)
    chat.recarregar_espacos(b.id)  # o chat está em outro espaço quando o clique acontece
    j.mostrar_aba(1)
    assert j.corpo.currentWidget() is grafo and j.abas[1].isChecked()
    grafo.abrir_conversa.emit(c1.id)  # o que o clique no cartão faz
    assert j.corpo.currentWidget() is chat and j.abas[0].isChecked()
    assert chat.conversa_id == c1.id and chat.espaco_id == a.id
    assert "pergunta sobre reserva" in chat.vista.toHtml()


def test_ligar_arrastar_zoom_e_apagar(cfg, qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    p, banco, esp, conv, _ = _montar(cfg, tmp_path, qapp, RESP)
    p.ao_abrir()
    assert _esperar(qapp, lambda: len(p.vista.nos) == 3 and not p.rodando)
    p.alternar_vista("2d")  # arrastar nó e o zoom com centro são da vista 2D
    v = p.vista
    selic, renda = _no(p, "Tesouro Selic"), _no(p, "Renda fixa")
    assert "manual" not in next(l for l in v.ligacoes if {l.a, l.b} == {selic, renda}).regras  # só mesma sessão
    v.modo_ligar = selic  # o que "Ligar a outra ideia…" do menu faz
    _clicar(v, _ponto(p, renda))
    qapp.processEvents()
    assert renda in v.vizinhos[selic] and v.modo_ligar is None
    manual = [l for l in v.ligacoes if {l.a, l.b} == {selic, renda}][0]
    assert "manual" in manual.regras and manual.peso >= 0.7
    # arrastar um nó muda a posição dele
    antes = v.fisica.posicao(renda)
    ini = _ponto(p, renda)
    from PySide6.QtCore import QPoint

    fim = ini + QPoint(60, 40)
    v.mousePressEvent(_evento(v, "press", ini, Qt.MouseButton.LeftButton))
    _mover(v, fim)
    destino = v._mundo(__import__("PySide6.QtCore", fromlist=["QPointF"]).QPointF(fim))
    assert v.fisica.posicao(renda) == pytest.approx(destino)  # o nó seguiu o mouse
    v.mouseReleaseEvent(_evento(v, "release", fim, Qt.MouseButton.LeftButton))
    assert v.fisica.fixo is None and v.fisica.posicao(renda) != antes
    # zoom pela roda
    z = v.zoom
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    ev = QWheelEvent(QPointF(450, 300), QPointF(450, 300), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    v.wheelEvent(ev)
    assert v.zoom > z
    # desligar e apagar pela API que o menu usa
    p.grafo.desligar(selic, renda)
    v.recarregar_preservando()
    assert renda not in v.vizinhos[selic]
    p.grafo.apagar_ideia(renda)
    v.recarregar_preservando()
    assert renda not in v.nos and len(v.nos) == 2


def test_grafo_vazio_e_renderiza_sem_quebrar(cfg, qapp, tmp_path):
    from eco.chat.armazenamento import Banco
    from eco.visual.grafo import PainelGrafo

    p = PainelGrafo(cfg, Banco(tmp_path / "eco.db"), Falso(), confirmar=lambda t: True)
    p.resize(800, 500)
    assert not p.grab().toImage().isNull()
    p.atualizar()
    assert "Em dia" in p.status.text()


def test_estimativa_alta_pede_confirmacao_e_recusar_nao_chama_o_modelo(cfg, qapp, tmp_path):
    from eco.chat.armazenamento import Banco
    from eco.visual.grafo import PainelGrafo

    banco = Banco(tmp_path / "eco.db")
    for i in range(300):  # muitas conversas: a estimativa passa de US$ 0,05
        c = banco.criar_conversa(None, f"C{i}")
        banco.adicionar_mensagem(c.id, "user", "uma pergunta longa o bastante para ser analisada pelo grafo " * 8)
    perguntas, falso = [], Falso()
    p = PainelGrafo(cfg, banco, falso, confirmar=lambda t: perguntas.append(t) or False)
    p.atualizar()
    assert len(perguntas) == 1 and "300" in perguntas[0] and "US$" in perguntas[0]
    assert falso.chamadas == 0 and "adiada" in p.status.text() and not p.rodando


def test_sem_orcamento_do_grafo_nao_analisa(cfg, qapp, tmp_path):
    p, banco, esp, conv, falso = _montar(cfg, tmp_path, qapp, RESP)
    p.grafo.somar_gasto(p.cfg.teto_grafo_usd + 0.01)
    p.atualizar()
    assert falso.chamadas == 0 and "Teto mensal" in p.status.text()


def test_renderiza_o_grafo_com_nos_e_cartao(cfg, qapp, tmp_path):
    p, banco, esp, conv, _ = _montar(cfg, tmp_path, qapp, RESP)
    p.ao_abrir()
    assert _esperar(qapp, lambda: len(p.vista.nos) == 3 and not p.rodando)
    p.vista._mostrar_cartao(_no(p, "Renda fixa"))
    assert not p.grab().toImage().isNull() and p.vista.cartao.isVisibleTo(p.vista)
