"""Aviso de microfone no mudo (`voz/microfone.py`)."""

from __future__ import annotations

import threading

from eco.painel import Painel
from eco.voz.microfone import LEGENDA_VOLTOU, LEGENDAS, MUDO, OK, VOLUME_ZERO, VigiaDoMicrofone, estado_do_windows, vigiar


def test_vigia_avisa_uma_vez_ao_ficar_mudo_e_uma_ao_voltar():
    v = VigiaDoMicrofone()
    assert v.observar(OK) is None  # começou ligado: nada a dizer
    assert v.observar(MUDO) == ("problema", MUDO)
    assert v.observar(MUDO) is None  # continua mudo: não repete o aviso
    assert v.observar(None) is None  # não deu para saber: não muda nada
    assert v.observar(OK) == ("voltou", OK)
    assert v.observar(OK) is None


def test_vigia_mudo_desde_o_inicio_e_troca_de_problema():
    v = VigiaDoMicrofone()
    assert v.observar(MUDO) == ("problema", MUDO)  # já abriu com o microfone no mudo
    assert v.observar(VOLUME_ZERO) == ("problema", VOLUME_ZERO)  # tirou o mudo mas o volume está zerado
    assert v.observar(OK) == ("voltou", OK)


class _Anota(Painel):
    def __init__(self) -> None:
        super().__init__()
        self.legendas: list[str] = []
        self.baloes: list[str] = []
        self.mudou = threading.Event()

    def legenda(self, texto: str, fixa: bool = False) -> None:
        self.legendas.append(texto)
        self.mudou.set()

    def notificar(self, titulo: str, texto: str) -> None:
        self.baloes.append(texto)


def test_thread_avisa_pelo_painel():
    estados = iter([OK, MUDO, MUDO, OK])
    painel, parar = _Anota(), threading.Event()
    t = vigiar(painel, consultar=lambda: next(estados, OK), intervalo=0.01, parar=parar)
    import time

    limite = time.monotonic() + 5
    while LEGENDA_VOLTOU not in painel.legendas and time.monotonic() < limite:
        time.sleep(0.01)
    parar.set()
    t.join(2)
    assert painel.legendas == [LEGENDAS[MUDO], LEGENDA_VOLTOU]
    assert len(painel.baloes) == 1 and "mudo" in painel.baloes[0]


def test_consulta_do_windows_nunca_quebra():
    assert estado_do_windows() in (MUDO, VOLUME_ZERO, OK, None)  # sem microfone ou fora do Windows: None
