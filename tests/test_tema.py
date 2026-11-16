"""Sistema de design (documento "Eco — Design"): tokens de `visual/tema.py` e componentes de `visual/componentes.py`."""

from __future__ import annotations

import pytest

from eco.visual import tema


@pytest.fixture
def qapp(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("ECO_MOVIMENTO_REDUZIDO", "0")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# --- tokens ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cor", tema.CORES_DE_TEXTO)
@pytest.mark.parametrize("fundo", tema.FUNDOS)
def test_cores_de_texto_passam_em_aa(cor, fundo):
    assert tema.contraste(cor, fundo) >= 4.5


def test_contraste_bate_com_valores_conhecidos():
    assert tema.contraste("#000000", "#ffffff") == pytest.approx(21.0)
    assert tema.contraste("#777777", "#777777") == pytest.approx(1.0)


def test_verde_e_so_preenchimento_e_borda_forte_serve_para_campos():
    assert tema.contraste(tema.VERDE, tema.SUPERFICIE) < 4.5  # por isso não está em CORES_DE_TEXTO
    assert tema.VERDE not in tema.CORES_DE_TEXTO
    assert tema.contraste(tema.TEXTO, tema.VERDE) >= 4.5  # texto claro sobre botão verde
    for fundo in tema.FUNDOS:
        assert tema.contraste(tema.BORDA_FORTE, fundo) >= 3.0  # contorno de campo, caixa e botão
    assert tema.contraste(tema.BORDA, tema.SUPERFICIE) < 3.0  # a borda comum é só decorativa


def test_folha_usa_os_tokens(qapp):
    qss = tema.folha()
    for token in (tema.TEXTO, tema.BORDA_FORTE, tema.VERDE_LUZ, tema.FONTE_MONO, tema.FONTE_TEXTO):
        assert token in qss
    assert tema.fonte_mono(11).pixelSize() == 11 and tema.fonte_texto().pixelSize() == tema.CORPO
    assert tema.misturar("#000000", "#ffffff", 0.5).red() == 128


# --- componentes ----------------------------------------------------------------------------------------------------
def test_cartao_em_foco_liga_o_fluxo_e_so_um_por_vez(qapp):
    from eco.visual.componentes import RELOGIO, Cartao

    a, b = Cartao("Agenda"), Cartao("Tarefas")
    a.resize(300, 200)
    b.resize(300, 200)
    assert RELOGIO.ativo is None and not a.em_foco
    a._definir_foco(True)
    assert RELOGIO.ativo is a and RELOGIO.timer.isActive()
    b._definir_foco(True)
    assert RELOGIO.ativo is b  # o fluxo muda de cartão: nunca dois ao mesmo tempo
    b.foco = 1.0
    assert not b.grab().toImage().isNull()  # pinta com o fluxo sem erro
    assert not b.regiao_da_borda().contains(b.rect().center())  # o fluxo redesenha só a moldura
    b._definir_foco(False)
    assert RELOGIO.ativo is None and not RELOGIO.timer.isActive()  # sem foco, o relógio para
    a._definir_foco(False)


def test_cartao_sem_movimento_nao_liga_relogio(qapp, monkeypatch):
    monkeypatch.setenv("ECO_MOVIMENTO_REDUZIDO", "1")
    from eco.visual.componentes import RELOGIO, Cartao

    c = Cartao("Metas")
    c.resize(300, 200)
    c._definir_foco(True)
    assert c.reduzido and c.foco == 1.0  # acende na hora, sem animar
    assert RELOGIO.timer is None or not RELOGIO.timer.isActive()
    assert not c.grab().toImage().isNull()
    c._definir_foco(False)


def test_caixa_clica_na_linha_toda_e_guarda_o_texto(qapp):
    from PySide6.QtCore import QPoint

    from eco.visual.componentes import SEPARADOR, Caixa

    c = Caixa("Entrevista" + SEPARADOR + "12/11" + SEPARADOR + "★ importante: carreira", atrasada=True)
    c.resize(400, 28)
    vistos = []
    c.clicked.connect(lambda marcado: vistos.append(marcado))
    assert c.text().split("  ·")[0] == "Entrevista"
    assert c.hitButton(QPoint(350, 14))  # o texto também marca, não só o quadradinho
    c.click()
    assert c.isChecked() and vistos == [True]
    assert not c.grab().toImage().isNull()


def test_botao_e_aba_hud(qapp):
    from eco.visual.componentes import Aba, Botao

    mini, normal, principal = Botao("Apagar", "perigo"), Botao("Atualizar"), Botao("Salvar", "principal")
    assert mini.sizeHint().height() < normal.sizeHint().height()
    assert principal.objectName() == "principal" and mini.objectName() == "mini"
    cliques = []
    normal.clicked.connect(lambda: cliques.append(1))
    normal.click()
    assert cliques == [1]
    normal.setEnabled(False)
    for b in (mini, normal, principal):
        b.resize(b.sizeHint())
        assert not b.grab().toImage().isNull()
    aba = Aba("Painel")
    aba.resize(aba.sizeHint())
    assert aba.isCheckable() and not aba._timer.isActive()
    aba.setChecked(True)
    assert not aba._timer.isActive()  # escondida: o fluxo não gasta processador
    assert not aba.grab().toImage().isNull()


def test_linha_guarda_a_frase_inteira(qapp):
    from eco.visual.componentes import Linha

    linha = Linha([("10:00", "hora"), ("Aula", "texto"), ("", "discreto"), ("10/11", "direita")], "10:00   Aula")
    linha.resize(300, 28)
    assert linha.text() == "10:00   Aula" and len(linha.partes) == 3  # parte vazia é descartada
    assert not linha.grab().toImage().isNull()
