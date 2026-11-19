import os

import numpy as np
import pytest

from eco.config import carregar_config
from eco.erros import mensagem_de_erro
from eco.painel import Painel, PainelMisto
from eco.sons import gerar
from eco.visual.particulas import ESTADOS, Renderizador, Sistema, hsl_to_rgb
from eco.voz.gravacao import nivel_visual
from eco.voz.stt_tts import envelope


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    return carregar_config()


def test_hsl_verde_e_ciano():
    verde = hsl_to_rgb(np.array([120.0]), 1.0, 0.5)[0]
    ciano = hsl_to_rgb(np.array([180.0]), 1.0, 0.5)[0]
    assert np.allclose(verde, [0, 1, 0], atol=1e-6)
    assert np.allclose(ciano, [0, 1, 1], atol=1e-6)


@pytest.mark.parametrize("estado", list(ESTADOS))
def test_motor_gera_quadro_valido_em_todos_os_estados(estado):
    s, r = Sistema(0.3, 1), Renderizador(160)
    for _ in range(20):
        q = s.passo(1 / 60, estado, 0.7, 160, (10, 5), True)
        img = r.quadro(q)
    assert img.shape == (160, 160, 4) and img.dtype == np.uint8
    assert img[:, :, 3].max() > 0  # algo foi desenhado
    assert (img[:, :, :3] <= img[:, :, 3:4]).all()  # cor pré-multiplicada válida
    assert np.isfinite(q.x).all() and np.isfinite(q.y).all()


def test_cursor_atrai_particulas():
    s = Sistema(0.3, 2)
    s.passo(1 / 60, "listen", 0, 200)
    sem = np.hypot(s.ox, s.oy).sum()
    for _ in range(30):
        s.passo(1 / 60, "listen", 0, 200, (40, 0), True)
    assert np.hypot(s.ox, s.oy).sum() > sem + 1


def test_orbe_cresce_com_o_volume():
    a, b = Sistema(0.2, 3), Sistema(0.2, 3)
    for _ in range(40):
        qa = a.passo(1 / 60, "speak", 0.0, 300)
        qb = b.passo(1 / 60, "speak", 1.0, 300)
    assert qb.R > qa.R


def test_envelope_e_nivel_visual():
    fraco = (np.sin(np.arange(16000) / 5) * 2000).astype(np.int16)
    forte = (np.sin(np.arange(16000) / 5) * 20000).astype(np.int16)
    e = envelope(np.concatenate([fraco, forte]).reshape(-1, 1), 16000)
    assert e.max() <= 1 and e[:10].mean() < e[-10:].mean()
    assert nivel_visual(0.0) == 0.0 and nivel_visual(1.0) == 1.0


def test_sons_e_erros():
    assert gerar([660, 880]).dtype == np.float32 and abs(gerar([440])).max() < 0.2
    assert "crédito" in mensagem_de_erro(RuntimeError("insufficient_quota"))
    assert "internet" in mensagem_de_erro(ConnectionError("x"))
    assert "problema" in mensagem_de_erro(ValueError("???"))


def test_painel_misto_repassa_tudo():
    visto = []

    class Anota(Painel):
        def estado(self, nome):
            visto.append(nome)

    PainelMisto(Anota(), Anota()).estado("listen")
    assert visto == ["listen", "listen"]


def test_janela_offscreen_pinta_e_menu_de_mudo(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr("eco.visual.janela._arquivo_posicao", lambda: tmp_path / "orbe.json")
    from eco.visual.janela import abrir_janela

    app, janela, painel = abrir_janela(cfg)
    assert janela.windowOpacity() == 0.0  # dormindo: invisível
    painel.b.estado.emit("listen")
    painel.b.legenda.emit("Ouvindo…", True)
    for _ in range(40):
        janela.t_ultimo -= 0.03  # simula tempo passando, sem depender do relógio
        janela._tick()
        app.processEvents()
    assert janela.opac > 0.5 and janela.img_buf is not None
    img = janela.grab().toImage()
    assert img.width() == janela.width() and not img.isNull()
    janela.alternar_mudo()
    assert painel.mudo and janela.estado == "mute"
    janela.alternar_mudo()
    assert not painel.mudo and janela.estado == "idle"
    janela.definir_legenda("", False)
    for _ in range(300):
        janela.t_ultimo -= 0.03
        janela._tick()
    assert janela.opac == 0.0  # voltou a dormir


def test_tela_cheia_alterna(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr("eco.visual.janela._arquivo_posicao", lambda: tmp_path / "orbe.json")
    from eco.visual.janela import abrir_janela

    app, janela, painel = abrir_janela(cfg)
    janela.estado = "speak"
    janela.alternar_tela_cheia()
    assert janela.tela_cheia and janela.width() > janela.lado_normal and janela.k > 1
    for _ in range(5):
        janela.t_ultimo -= 0.03
        janela._tick()
    assert not janela.grab().toImage().isNull()
    janela.alternar_tela_cheia()
    assert not janela.tela_cheia and janela.width() == janela.lado_normal and janela.k == 2.0  # brilho em meia resolução


def test_orbe_so_muda_a_opacidade_quando_precisa_e_a_taxa_segue_o_estado(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr("eco.visual.janela._arquivo_posicao", lambda: tmp_path / "orbe.json")
    from eco.visual.janela import abrir_janela

    app, janela, painel = abrir_janela(cfg)
    chamadas = []
    original = janela.setWindowOpacity
    monkeypatch.setattr(janela, "setWindowOpacity", lambda v: (chamadas.append(v), original(v)))
    janela.estado = "listen"
    for _ in range(60):
        janela.t_ultimo -= 0.03
        janela._tick()
    assert janela.opac == 1.0
    depois_de_acender = len(chamadas)
    for _ in range(20):  # já está em 1: não chama o Windows de novo
        janela.t_ultimo -= 0.03
        janela._tick()
    assert len(chamadas) == depois_de_acender
    assert janela.timer.interval() == 17  # ouvindo: 60 quadros/s
    janela.estado = "think"
    janela._tick()
    assert janela.timer.interval() == 33  # pensando: 30 quadros/s
    janela.pausar(True)
    antes = janela.quadro
    janela._tick()
    assert janela.quadro is antes  # pausada: não desenha
    janela.pausar(False)
    janela._tick()
    assert janela.quadro is not antes


def test_orbe_fica_no_centro_embaixo_e_ignora_a_posicao_antiga_uma_vez(cfg, monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.delenv("ECO_ORBE_TAMANHO", raising=False)
    arquivo = tmp_path / "orbe.json"
    arquivo.write_text(json.dumps({"x": 7, "y": 9, "tamanho": "pequeno"}), encoding="utf-8")  # gravado antes do centro
    monkeypatch.setattr("eco.visual.janela._arquivo_posicao", lambda: arquivo)
    from PySide6.QtGui import QGuiApplication

    from eco.config import TAMANHOS_ORBE
    from eco.visual.janela import MARGEM_BAIXO, abrir_janela

    app, janela, painel = abrir_janela(cfg)
    tela = QGuiApplication.primaryScreen().availableGeometry()
    assert janela.lado_normal == TAMANHOS_ORBE["pequeno"]  # o tamanho salvo continua valendo
    assert janela.x() + janela.width() / 2 == pytest.approx(tela.x() + tela.width() / 2, abs=1)  # centro horizontal
    assert janela.y() + janela.height() == tela.y() + tela.height() - MARGEM_BAIXO  # embaixo
    assert janela.na_posicao_padrao()

    janela.definir_tamanho("grande")  # no centro embaixo, crescer mantém a base e o centro
    assert janela.na_posicao_padrao()

    janela.move(40, 50)  # o dono arrastou
    from eco.visual.janela import _gravar_posicao, _ler_posicao

    _gravar_posicao(40, 50)
    assert _ler_posicao() == (40, 50) and not janela.na_posicao_padrao()
    janela.voltar_ao_centro()
    assert janela.na_posicao_padrao()
    assert _ler_posicao() == (janela.x(), janela.y())
    assert json.loads(arquivo.read_text(encoding="utf-8"))["versao_posicao"] == 2


def test_tamanhos_do_menu(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.delenv("ECO_ORBE_TAMANHO", raising=False)
    monkeypatch.setattr("eco.visual.janela._arquivo_posicao", lambda: tmp_path / "orbe.json")
    from eco.config import TAMANHOS_ORBE
    from eco.visual.janela import abrir_janela

    app, janela, painel = abrir_janela(cfg)
    assert janela.lado_normal == TAMANHOS_ORBE["medio"]
    janela.definir_tamanho("grande")
    assert janela.width() == TAMANHOS_ORBE["grande"] and janela.render.lado == TAMANHOS_ORBE["grande"] // 2
    assert janela.nitidas.n == TAMANHOS_ORBE["grande"] and janela.k == 2.0
    janela.definir_tamanho("tela")
    assert janela.tela_cheia
    janela.definir_tamanho("pequeno")
    assert not janela.tela_cheia and janela.width() == 320
    for _ in range(3):
        janela.t_ultimo -= 0.03
        janela._tick()
    assert "pequeno" in (tmp_path / "orbe.json").read_text()


# --- janela principal e comando "abra a interface" ----------------------------
def test_comando_abrir_interface_reconhece_variacoes_e_rejeita_o_resto():
    from eco.comandos import eh_abrir_interface

    for frase in (
        "Abra a interface", "abrir interface", "Mostra a interface.", "Eco, abre a interface",
        "Ei Eco, abra a interface por favor", "pode abrir a interface", "Exiba a interface do Eco",
        "quero ver a interface", "abra a janela principal", "Abre a interface pra mim",
        "Ecco, mostra a interface.",  # grafia que o transcritor produziu num teste com áudio real
        "Hey Echo, abra a interface", "Eko abre a interface",
    ):
        assert eh_abrir_interface(frase), frase
    for frase in (
        "me explica como abrir a interface do docker", "abra o chrome", "o que é uma interface gráfica",
        "não abra a interface", "abra a interface e me diz as horas", "interface", "", "feche a interface",
    ):
        assert not eh_abrir_interface(frase), frase


def test_movimento_reduzido_pelo_ambiente_e_pelo_windows(monkeypatch):
    import ctypes
    from types import SimpleNamespace

    from eco.visual import principal

    monkeypatch.setenv("ECO_MOVIMENTO_REDUZIDO", "1")
    assert principal.movimento_reduzido()
    monkeypatch.setenv("ECO_MOVIMENTO_REDUZIDO", "nao")
    assert not principal.movimento_reduzido()
    monkeypatch.delenv("ECO_MOVIMENTO_REDUZIDO")

    def windows(animacao_ligada):
        def consulta(_acao, _n, ponteiro, _flags):
            ponteiro._obj.value = int(animacao_ligada)
            return 1

        return SimpleNamespace(user32=SimpleNamespace(SystemParametersInfoW=consulta))

    monkeypatch.setattr(ctypes, "windll", windows(False), raising=False)  # "efeitos de animação" desligados
    assert principal.movimento_reduzido()
    monkeypatch.setattr(ctypes, "windll", windows(True), raising=False)
    assert not principal.movimento_reduzido()


def test_interpolar():
    from eco.visual.principal import interpolar

    assert interpolar((0, 0, 10, 10), (100, 50, 200, 110), 0.0) == (0, 0, 10, 10)
    assert interpolar((0, 0, 10, 10), (100, 50, 200, 110), 1.0) == (100, 50, 200, 110)
    assert interpolar((0, 0, 10, 10), (100, 50, 200, 110), 0.5) == (50, 25, 105, 60)


def _orbe(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("ECO_MOVIMENTO_REDUZIDO", "0")
    monkeypatch.setattr("eco.visual.janela._arquivo_posicao", lambda: tmp_path / "orbe.json")
    from eco.visual.janela import abrir_janela

    return abrir_janela(cfg)


def _clique(janela, quando_apertou=0.0):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    def ev(tipo):
        return QMouseEvent(tipo, QPointF(50, 50), QPointF(50, 50), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    janela.mousePressEvent(ev(QEvent.Type.MouseButtonPress))
    janela._t_press -= quando_apertou  # simula quanto tempo o botão ficou apertado
    janela.mouseReleaseEvent(ev(QEvent.Type.MouseButtonRelease))


def test_janela_principal_cresce_a_partir_da_orbe_e_fechar_so_esconde(cfg, monkeypatch, tmp_path):
    app, janela, painel = _orbe(cfg, monkeypatch, tmp_path)
    assert janela.principal is None
    orbe = janela.retangulo_do_orbe()
    assert orbe.width() == janela.lado_normal
    janela.estado = "listen"  # a orbe estava à vista quando clicaram nela
    for _ in range(40):
        janela.t_ultimo -= 0.03
        janela._tick()
    assert janela.isVisible() and janela.opac == 1.0
    janela.abrir_principal()
    pr = janela.principal
    assert pr.isVisible()
    g = pr.geometry()
    alvo = tuple(int(v) for v in pr._alvo)
    assert (g.x(), g.y(), g.width(), g.height()) == alvo  # já no tamanho final: nada é redimensionado na animação
    assert pr.captura is not None and not pr.corpo.isVisible()  # durante a animação, só a captura é desenhada
    forma = pr._forma_animada()
    assert (round(forma.x() + g.x()), round(forma.y() + g.y()), round(forma.width())) == (orbe.x(), orbe.y(), orbe.width())  # começa na orbe
    assert pr.raio == pytest.approx(orbe.width() / 2)  # círculo
    assert janela.pausada and janela.windowOpacity() == pytest.approx(1.0)  # a orbe para de desenhar
    pr.anim.setCurrentTime(pr.anim.duration() // 2)
    assert orbe.width() < pr._forma_animada().width() < g.width()  # no meio do caminho
    assert janela.windowOpacity() == pytest.approx(0.0)  # a orbe já "virou" a janela
    assert not pr.grab().toImage().isNull()
    pr.anim.setCurrentTime(pr.anim.duration())
    fim = pr.geometry()
    assert (fim.x(), fim.y(), fim.width(), fim.height()) == alvo
    assert pr.windowOpacity() == 1.0 and pr.raio == pytest.approx(18.0) and pr.conteudo == 1.0
    assert pr.captura is None and pr.corpo.isVisible() and all(b.isVisible() for b in pr.abas)
    assert not janela.pausada  # a orbe volta a desenhar
    assert not pr.grab().toImage().isNull()
    pr.close()  # fechar encolhe de volta para a orbe e só esconde
    assert pr.isVisible() and pr.fechando and pr.captura is not None and janela.pausada
    pr.anim.setCurrentTime(pr.anim.duration())
    assert not pr.isVisible() and not pr.fechando and not janela.pausada
    assert app.quitOnLastWindowClosed() is False
    janela.abrir_principal()  # abre de novo, mesma janela
    assert janela.principal is pr and pr.isVisible()
    pr.hide()  # escondida no meio da animação: a orbe não fica pausada
    assert not janela.pausada and pr.captura is None


def test_janela_principal_e_preparada_escondida_sem_carregar_dados(cfg, monkeypatch, tmp_path):
    _, janela, _ = _orbe(cfg, monkeypatch, tmp_path)
    abertas = []
    from eco.visual.dashboard import PainelDashboard

    monkeypatch.setattr(PainelDashboard, "ao_abrir", lambda self: abertas.append(1))
    janela._preparar_principal()
    pr = janela.principal
    assert pr is not None and not pr.isVisible() and abertas == []
    janela._preparar_principal()  # de novo: não monta outra
    assert janela.principal is pr
    janela.abrir_principal()
    assert janela.principal is pr and pr.isVisible()


def test_captura_da_abertura_mostra_o_conteudo(cfg, monkeypatch, tmp_path):
    _, janela, _ = _orbe(cfg, monkeypatch, tmp_path)
    janela.abrir_principal()
    pr = janela.principal
    img = pr.captura.toImage()
    pr.anim.setCurrentTime(pr.anim.duration())
    real = pr.grab().toImage()
    assert img.size() == real.size()
    # a captura é a própria janela pronta: o mesmo desenho que aparece quando a animação acaba (sem pulo)
    iguais = sum(img.pixel(x, y) == real.pixel(x, y) for x in range(0, img.width(), 40) for y in range(0, img.height(), 40))
    total = len(range(0, img.width(), 40)) * len(range(0, img.height(), 40))
    assert iguais / total > 0.95


def test_janela_principal_ja_aberta_nao_repete_a_animacao(cfg, monkeypatch, tmp_path):
    _, janela, _ = _orbe(cfg, monkeypatch, tmp_path)
    janela.abrir_principal()
    pr = janela.principal
    pr.anim.setCurrentTime(pr.anim.duration())
    antes = pr.geometry()
    janela.abrir_principal()
    assert pr.geometry() == antes and pr.p == 1.0


def test_janela_principal_com_movimento_reduzido_so_faz_fade(cfg, monkeypatch, tmp_path):
    _, janela, _ = _orbe(cfg, monkeypatch, tmp_path)
    monkeypatch.setenv("ECO_MOVIMENTO_REDUZIDO", "1")
    janela.abrir_principal()
    pr = janela.principal
    alvo = tuple(int(v) for v in pr._alvo)
    g = pr.geometry()
    assert (g.x(), g.y(), g.width(), g.height()) == alvo  # já no tamanho final, sem crescer
    assert pr.windowOpacity() == 0.0 and pr.anim.duration() < 300
    pr.anim.setCurrentTime(pr.anim.duration() // 2)
    assert 0.0 < pr.windowOpacity() < 1.0
    g = pr.geometry()
    assert (g.x(), g.y(), g.width(), g.height()) == alvo
    pr.anim.setCurrentTime(pr.anim.duration())
    assert pr.windowOpacity() == 1.0


def test_clique_na_orbe_abre_a_janela_principal(cfg, monkeypatch, tmp_path):
    _, janela, _ = _orbe(cfg, monkeypatch, tmp_path)
    _clique(janela)
    assert janela.principal is not None and janela.principal.isVisible()
    janela.principal.hide()
    _clique(janela, quando_apertou=1.0)  # segurar o botão é brincar com as faíscas, não é clique
    assert not janela.principal.isVisible()


def test_clique_enquanto_ela_fala_so_interrompe(cfg, monkeypatch, tmp_path):
    _, janela, painel = _orbe(cfg, monkeypatch, tmp_path)
    janela.estado = "speak"
    _clique(janela)
    assert painel.interromper.is_set() and janela.principal is None


def test_painel_abrir_interface_da_thread_de_voz(cfg, monkeypatch, tmp_path):
    from eco.painel import Painel

    assert Painel().abrir_interface() is False  # sem janela (modo sem visual)
    _, janela, painel = _orbe(cfg, monkeypatch, tmp_path)
    assert painel.abrir_interface() is True
    assert janela.principal is not None and janela.principal.isVisible()


def test_sair_pede_confirmacao_na_orbe_e_na_bandeja(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr("eco.visual.janela._arquivo_posicao", lambda: tmp_path / "orbe.json")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from eco.visual.janela import abrir_janela

    app, janela, painel = abrir_janela(cfg)
    saidas = []
    escolha = {"texto": "Continuar aberta"}
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: saidas.append(1)))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: next(b for b in self.buttons() if b.text() == escolha["texto"]))
    assert janela.pedir_para_sair() is False and saidas == []  # "continuar aberta" não fecha
    if janela.icone is not None:  # sem bandeja do sistema (ex.: teste offscreen), só vale a orbe
        sair_bandeja = next(a for a in janela.icone.contextMenu().actions() if a.text() == "Sair")
        sair_bandeja.trigger()
        assert saidas == []  # o Sair da bandeja também pergunta antes
        escolha["texto"] = "Fechar a Eco"
        sair_bandeja.trigger()
        assert saidas == [1]
        saidas.clear()
    escolha["texto"] = "Fechar a Eco"
    assert janela.pedir_para_sair() is True and saidas == [1]


def test_coleta_de_lixo_so_na_thread_principal(monkeypatch):
    """O coletor automático do Python é desligado e um relógio do Qt coleta (evita queda nativa em thread de trabalho)."""
    import gc

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from eco.visual.janela import instalar_coletor

    app = QApplication.instance() or QApplication([])
    ligado = gc.isenabled()
    try:
        relogio = instalar_coletor(app)
        assert not gc.isenabled() and relogio.isActive() and relogio.interval() == 2000
        relogio.timeout.emit()  # uma coleta pelo relógio não pode dar erro
    finally:
        relogio.stop()
        if ligado:
            gc.enable()
