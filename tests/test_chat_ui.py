"""Testes da aba Conversas (Qt offscreen): conversa atual do Cerebro, caixa de texto, guardadas por espaço, busca."""

import time

import pytest

pytest.importorskip("PySide6")


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


class AgenteFalso:
    """Faz o papel do Agente: responde em fila e registra canal e se recebeu a função de confirmar."""

    def __init__(self, respostas, erro=None):
        self.respostas = list(respostas)
        self.erro = erro
        self.pedidos = []
        self.contexto = None
        self.ferramentas = []

    def adicionar_ferramenta(self, f):
        self.ferramentas.append(f)

    def responder(self, texto, canal="voz", confirmar=None):
        self.pedidos.append((texto, canal, confirmar is not None))
        if self.erro:
            raise self.erro
        return self.respostas.pop(0) if self.respostas else "ok"


def _esperar(qapp, condicao, segundos=5):
    fim = time.time() + segundos
    while time.time() < fim:
        qapp.processEvents()
        if condicao():
            return True
        time.sleep(0.01)
    return False


def _painel(cfg, tmp_path, agente=None, com_cerebro=True):
    from eco.cerebro import Cerebro
    from eco.chat.armazenamento import Banco
    from eco.visual.chat import PainelChat

    banco = Banco(tmp_path / "eco.db")
    cerebro = Cerebro(agente or AgenteFalso([]), banco) if com_cerebro else None
    return PainelChat(cfg, None, banco, cerebro=cerebro)


def _textos(lista):
    return [lista.item(i).text() for i in range(lista.count())]


def _ids(lista):
    return [lista.item(i).data(0x0100) for i in range(lista.count())]


def test_abre_na_conversa_atual_vazia(cfg, qapp, tmp_path):
    from eco.visual.chat import ATUAL

    p = _painel(cfg, tmp_path)
    assert p.conversa_id == ATUAL and _textos(p.lista_conversas) == ["● Conversa atual"]
    assert "Guarde essa" in p.vista.toPlainText() and "não guardada" in p.titulo.text()


def test_caixa_de_texto_usa_o_cerebro_e_nao_grava_sem_guardar(cfg, qapp, tmp_path):
    ag = AgenteFalso(["Olá! Aqui vai **negrito**."])
    p = _painel(cfg, tmp_path, ag)
    p.entrada.setPlainText("Oi Eco")
    p.enviar()
    assert _esperar(qapp, lambda: not p.ocupado and len(p._mensagens) == 2)
    assert ag.pedidos == [("Oi Eco", "texto", True)]
    html = p.vista.toHtml()
    assert "Oi Eco" in html and "negrito" in html
    assert p.banco.listar_conversas(None) == [] and p.entrada.toPlainText() == ""


def test_fala_de_voz_aparece_na_tela_pelo_sinal(cfg, qapp, tmp_path):
    p = _painel(cfg, tmp_path, AgenteFalso(["Choveu ontem."]))
    p.cerebro.responder("choveu?", "voz")  # o que a thread de voz faz
    assert _esperar(qapp, lambda: len(p._mensagens) == 2)
    assert "choveu?" in p.vista.toHtml()


def test_guardar_mostra_no_espaco_e_continua_gravando(cfg, qapp, tmp_path):
    p = _painel(cfg, tmp_path, AgenteFalso(["plano feito", "mais um passo"]))
    estudos = p.banco.criar_espaco("Estudos")
    p.recarregar_espacos(estudos.id)
    p.cerebro.responder("monta um plano", "voz")
    p.cerebro.guardar("estudos", "Plano de estudo")
    assert _esperar(qapp, lambda: "Plano de estudo" in _textos(p.lista_conversas))
    assert "guardada em Estudos" in p.titulo.text()
    p.cerebro.responder("e depois?", "voz")
    conversa = p.cerebro.sessao.conversa_id
    assert [m.conteudo for m in p.banco.mensagens(conversa)] == ["monta um plano", "plano feito", "e depois?", "mais um passo"]
    # abrir a guardada pela lista mostra só leitura do banco
    p.abrir_conversa(conversa)
    assert p.titulo.text() == "Plano de estudo" and len(p._mensagens) == 4


def test_trocar_de_espaco_fecha_a_guardada_de_outro_espaco(cfg, qapp, tmp_path):
    from eco.visual.chat import ATUAL

    p = _painel(cfg, tmp_path)
    a, b = p.banco.criar_espaco("A"), p.banco.criar_espaco("B")
    c = p.banco.criar_conversa(a.id, "Uma")
    p.recarregar_espacos(a.id)
    p.abrir_conversa(c.id)
    p.selecionar_espaco(b.id)
    assert p.conversa_id == ATUAL and _ids(p.lista_conversas) == [ATUAL]
    p.selecionar_espaco(a.id)
    assert _ids(p.lista_conversas) == [ATUAL, c.id]


def test_nova_conversa_descarta_a_atual_nao_guardada(cfg, qapp, tmp_path):
    p = _painel(cfg, tmp_path, AgenteFalso(["oi"]))
    p.cerebro.responder("olá", "voz")
    assert _esperar(qapp, lambda: len(p._mensagens) == 2)
    p.nova_conversa()
    assert _esperar(qapp, lambda: p._mensagens == []) and p.cerebro.sessao.falas == []


def test_mover_conversa_pela_tela_mantem_o_historico(cfg, qapp, tmp_path):
    p = _painel(cfg, tmp_path)
    a, b = p.banco.criar_espaco("A"), p.banco.criar_espaco("B")
    c = p.banco.criar_conversa(a.id, "Um")
    p.banco.adicionar_mensagem(c.id, "user", "pergunta")
    p.recarregar_espacos(a.id)
    p.abrir_conversa(c.id)
    p._mover_conversa(c.id, b.id)  # o que o menu "Mover para" do botão direito faz
    assert p.espaco_id == b.id and p.banco.conversa(c.id).espaco_id == b.id
    assert [m.conteudo for m in p.banco.mensagens(c.id)] == ["pergunta"]


def test_busca_neste_espaco_ou_em_todos(cfg, qapp, tmp_path):
    p = _painel(cfg, tmp_path)
    a, b = p.banco.criar_espaco("A"), p.banco.criar_espaco("B")
    ca, cb = p.banco.criar_conversa(a.id, "Um"), p.banco.criar_conversa(b.id, "Dois")
    p.banco.adicionar_mensagem(ca.id, "user", "falando de orçamento")
    p.banco.adicionar_mensagem(cb.id, "user", "outro orçamento aqui")
    p.recarregar_espacos(a.id)
    p.busca.setText("orcamento")
    assert p.lista_conversas.count() == 1
    p.escopo.setCurrentIndex(1)
    assert p.lista_conversas.count() == 2
    p._conversa_clicada(p.lista_conversas.item(0))
    assert p.conversa_id in (ca.id, cb.id) and p.espaco_id == p.banco.conversa(p.conversa_id).espaco_id
    p.busca.setText("inexistente")
    assert _textos(p.lista_conversas) == ["Nada encontrado."]


def test_nao_envia_vazio_nem_sem_cerebro_e_erro_devolve_o_texto(cfg, qapp, tmp_path):
    p = _painel(cfg, tmp_path, com_cerebro=False)
    p.entrada.setPlainText("   ")
    p.enviar()
    assert not p.ocupado
    p.entrada.setPlainText("oi")
    p.enviar()
    assert "iniciando" in p.aviso.text() and p.entrada.toPlainText() == "oi"

    p2 = _painel(cfg, tmp_path / "b", AgenteFalso([], erro=RuntimeError("sem rede")))
    p2.entrada.setPlainText("importante")
    p2.enviar()
    assert _esperar(qapp, lambda: not p2.ocupado)
    assert p2.entrada.toPlainText() == "importante" and "Erro" in p2.vista.toPlainText()


def test_resposta_com_codigo_abre_a_janela_flutuante(cfg, qapp, tmp_path):
    p = _painel(cfg, tmp_path, AgenteFalso(["Pronto:\n\n```python\nprint(1)\n```"]))
    p.entrada.setPlainText("faz um código")
    p.enviar()
    assert _esperar(qapp, lambda: p._janela_flutuante is not None and p._janela_flutuante.isVisible())
    assert "print(1)" in p._janela_flutuante.texto


def test_janela_principal_mostra_as_conversas_depois_de_abrir(cfg, qapp, tmp_path, monkeypatch):
    from eco.visual.principal import JanelaPrincipal

    monkeypatch.setenv("ECO_MOVIMENTO_REDUZIDO", "1")
    chat = _painel(cfg, tmp_path)
    j = JanelaPrincipal(cfg, chat=chat)
    j.abrir(None)
    assert _esperar(qapp, lambda: chat.isVisible(), 3)
    g = j.corpo.geometry()
    assert g.top() > 56 and g.width() == j.width() - 12  # 6 px livres de cada lado: é por ali que se redimensiona
    assert not j.grab().toImage().isNull()
    j.hide()
