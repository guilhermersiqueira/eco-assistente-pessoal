"""Caixas de diálogo HUD de `visual/dialogos.py`."""

from __future__ import annotations

import pytest

from eco.visual import dialogos


@pytest.fixture
def qapp(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _clicar(rotulo):
    """Troca `_executar` por um clique no botão com o rótulo dado (ou rejeita se for None)."""

    def exec_(d):
        if rotulo is None:
            return False
        from eco.visual.componentes import Botao

        botao = next(b for b in d.findChildren(Botao) if b.text() == rotulo)
        botao.click()
        return d.result() == 1

    return exec_


def test_perguntar_sim_e_nao(qapp, monkeypatch):
    monkeypatch.setattr(dialogos, "_executar", _clicar("Sim"))
    assert dialogos.perguntar(None, "Confirmar", "Posso?") is True
    monkeypatch.setattr(dialogos, "_executar", _clicar("Não"))
    assert dialogos.perguntar(None, "Confirmar", "Posso?") is False


def test_perguntar_destrutivo_foca_o_nao(qapp, monkeypatch):
    achados = {}

    def exec_(d):
        from eco.visual.componentes import Botao

        achados.update({b.text(): b.isDefault() for b in d.findChildren(Botao)})
        return False

    monkeypatch.setattr(dialogos, "_executar", exec_)
    dialogos.perguntar(None, "Excluir", "Apagar?", sim="Excluir", destrutivo=True)
    assert achados == {"Não": True, "Excluir": False}


def test_texto_devolve_o_que_foi_digitado(qapp, monkeypatch):
    def exec_(d):
        from PySide6.QtWidgets import QLineEdit

        d.findChild(QLineEdit).setText("novo título")
        d.accept()
        return True

    monkeypatch.setattr(dialogos, "_executar", exec_)
    assert dialogos.texto(None, "Renomear", "Novo título:", "antigo") == ("novo título", True)


def test_texto_cancelado_volta_vazio(qapp, monkeypatch):
    monkeypatch.setattr(dialogos, "_executar", _clicar(None))
    assert dialogos.texto(None, "Renomear", "Novo título:", "antigo") == ("", False)
    assert dialogos.texto_longo(None, "Nova", "O que lembrar:") == ("", False)


def test_escolher_devolve_o_valor_ou_none(qapp, monkeypatch):
    opcoes = [("Só este", "this"), ("Este e os próximos", "following")]
    monkeypatch.setattr(dialogos, "_executar", _clicar("Este e os próximos"))
    assert dialogos.escolher(None, "Repete", "Alterar?", opcoes) == "following"
    monkeypatch.setattr(dialogos, "_executar", _clicar("Cancelar"))
    assert dialogos.escolher(None, "Repete", "Alterar?", opcoes) is None
