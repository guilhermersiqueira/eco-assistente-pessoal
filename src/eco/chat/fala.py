"""Fala da resposta do chat enquanto ela chega, e que pode ser interrompida.

`DivisorDeFala` transforma o texto que chega aos poucos (Markdown) em frases prontas para falar, pulando código e
símbolos. `Narrador` fala essas frases numa thread própria; se a fala for interrompida (clique na orbe, botão,
"Parar de falar"), ele avisa por `ao_calar`, que também pode parar a geração do texto.
"""

from __future__ import annotations

import queue
import re
import threading
from typing import Any, Callable

_FIM_DE_FRASE = re.compile(r"(?<=[.!?…])\s+|\n")
_FIM = object()
AVISO_CODIGO = "Deixei um trecho de código na tela."


def limpar_markdown(linha: str) -> str:
    s = re.sub(r"^\s*#{1,6}\s*", "", linha)
    s = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", s)
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = s.replace("`", "")
    s = re.sub(r"(\*\*|__)(.+?)\1", r"\2", s)
    s = s.replace("*", "").lstrip(">")
    return s.strip()


class DivisorDeFala:
    def __init__(self) -> None:
        self._buf = ""
        self._em_codigo = False

    def alimentar(self, pedaco: str) -> list[str]:
        """Recebe um pedaço do texto e devolve as frases que já ficaram completas (o resto espera o próximo pedaço)."""
        self._buf += pedaco
        *prontas, self._buf = _FIM_DE_FRASE.split(self._buf)
        return self._processar(prontas)

    def finalizar(self) -> list[str]:
        resto, self._buf = self._buf, ""
        return self._processar([resto]) if resto.strip() else []

    def _processar(self, pedacos: list[str]) -> list[str]:
        saida: list[str] = []
        for p in pedacos:
            t = p.strip()
            if t.startswith("```"):
                self._em_codigo = not self._em_codigo
                if self._em_codigo:
                    saida.append(AVISO_CODIGO)
                continue
            if self._em_codigo or t.startswith("|"):  # código e tabelas ficam só na tela
                continue
            limpo = limpar_markdown(t)
            if re.search(r"[A-Za-zÀ-ÿ]", limpo):
                saida.append(limpo)
        return saida


class Narrador:
    """Fala a resposta aos poucos. `falar(texto)` deve devolver algo com `.interrompida` (a `Fala` de voz/stt_tts)."""

    def __init__(
        self,
        falar: Callable[[str], Any],
        interromper: threading.Event,
        ao_calar: Callable[[], None] | None = None,
        ao_comecar: Callable[[], None] | None = None,
        ao_terminar: Callable[[], None] | None = None,
    ) -> None:
        self._falar, self._interromper = falar, interromper
        self._ao_calar, self._ao_comecar, self._ao_terminar = ao_calar, ao_comecar, ao_terminar
        self._divisor = DivisorDeFala()
        self._fila: queue.Queue = queue.Queue()
        self.calada = False
        self.terminou = threading.Event()
        interromper.clear()  # um clique antigo não pode calar a fala nova
        self._thread = threading.Thread(target=self._laco, daemon=True, name="eco-narrador")
        self._thread.start()

    def alimentar(self, pedaco: str) -> None:
        if not self.calada:
            for frase in self._divisor.alimentar(pedaco):
                self._fila.put(frase)

    def fim_do_texto(self) -> None:
        if not self.calada:
            for frase in self._divisor.finalizar():
                self._fila.put(frase)
        self._fila.put(_FIM)

    def cancelar(self) -> None:
        """Botão Parar: cala a fala já e descarta o que faltava dizer."""
        self.calada = True
        self._interromper.set()
        self._fila.put(_FIM)

    def _laco(self) -> None:
        comecou = False
        try:
            while True:
                item = self._fila.get()
                if item is _FIM:
                    break
                lote, acabou = [item], False
                while True:  # o que se acumulou enquanto falava vai junto (a voz gera em paralelo)
                    try:
                        outro = self._fila.get_nowait()
                    except queue.Empty:
                        break
                    if outro is _FIM:
                        acabou = True
                        break
                    lote.append(outro)
                if self.calada:
                    break
                if not comecou and self._ao_comecar:
                    self._ao_comecar()
                comecou = True
                fala = self._falar(" ".join(lote))
                if self.calada or getattr(fala, "interrompida", False) or self._interromper.is_set():
                    ja_calada, self.calada = self.calada, True
                    if not ja_calada and self._ao_calar:
                        self._ao_calar()  # foi o dono quem calou: pode parar a geração também
                    break
                if acabou:
                    break
        finally:
            if self.calada:
                self._interromper.clear()
            if self._ao_terminar:
                self._ao_terminar()
            self.terminou.set()
