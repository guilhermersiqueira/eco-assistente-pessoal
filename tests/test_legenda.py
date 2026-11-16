"""Legenda da janela do orbe: tamanho da fonte em 2/3 do anterior (pedido do dono em 13/11/2026)."""

from eco.visual import tema
from eco.visual.janela import tamanho_da_legenda


def test_legenda_usa_dois_tercos_do_tamanho_anterior():
    assert tamanho_da_legenda(False) == round(tema.SUBTITULO * 2 / 3) == 10
    assert tamanho_da_legenda(True) == round(21 * 2 / 3) == 14
