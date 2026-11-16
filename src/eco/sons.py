"""Sons suaves de começar e terminar de ouvir."""

from __future__ import annotations

import numpy as np

from eco.config import Config

TAXA = 24_000


def gerar(frequencias: list[float], duracao: float = 0.07, volume: float = 0.12) -> np.ndarray:
    """Notas em sequência, com subida e descida suaves para não estalar."""
    notas = []
    n = int(TAXA * duracao)
    janela = np.hanning(n)
    t = np.arange(n) / TAXA
    for f in frequencias:
        notas.append(np.sin(2 * np.pi * f * t) * janela * volume)
    return np.concatenate(notas).astype(np.float32)


def _tocar(som: np.ndarray, esperar: bool) -> None:
    try:
        import sounddevice as sd

        sd.play(som, TAXA)
        if esperar:
            sd.wait()
    except Exception:  # sem som não é motivo para parar o assistente
        pass


def inicio(cfg: Config) -> None:
    """Toca e espera acabar, para o microfone não captar o próprio som."""
    if cfg.sons:
        _tocar(gerar([660, 880]), esperar=True)


def fim(cfg: Config) -> None:
    if cfg.sons:
        _tocar(gerar([880, 660]), esperar=False)
