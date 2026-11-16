"""Grava do microfone até detectar silêncio."""

from __future__ import annotations

import io
import wave

from eco.config import Config

TAXA = 16_000
BLOCO_SEG = 0.03


class DetectorSilencio:
    """Decide quando parar de gravar, só com base no volume (RMS, de 0 a 1) de cada bloco."""

    def __init__(self, silencio_seg: float, max_seg: float, espera_fala_seg: float, limiar: float) -> None:
        self.silencio_seg, self.max_seg, self.espera_fala_seg, self.limiar = silencio_seg, max_seg, espera_fala_seg, limiar
        self.tempo = 0.0
        self.mudo = 0.0
        self.falou = False

    def processar(self, rms: float, dt: float) -> str:
        """Devolve 'continuar', 'fim' (gravou a fala) ou 'sem_fala' (ninguém falou)."""
        self.tempo += dt
        if rms >= self.limiar:
            self.falou, self.mudo = True, 0.0
        elif self.falou:
            self.mudo += dt
        if self.falou and self.mudo >= self.silencio_seg:
            return "fim"
        if self.tempo >= self.max_seg:
            return "fim" if self.falou else "sem_fala"
        if not self.falou and self.tempo >= self.espera_fala_seg:
            return "sem_fala"
        return "continuar"


def nivel_visual(rms: float) -> float:
    """Converte o RMS do microfone (0 a ~0,1) em 0 a 1 para o orbe, com curva suave."""
    return min(1.0, (rms / 0.06) ** 0.7)


def gravar_ate_silencio(cfg: Config, ao_nivel=None, espera_fala_seg: float | None = None) -> bytes | None:
    """Devolve um WAV com a fala, ou None se ninguém falou. `ao_nivel(0..1)` recebe o volume ao vivo.
    `espera_fala_seg` troca o quanto esperar pelo início da fala (a conversa usa um tempo maior)."""
    import numpy as np
    import sounddevice as sd

    bloco = int(TAXA * BLOCO_SEG)
    espera = cfg.espera_fala_seg if espera_fala_seg is None else espera_fala_seg
    detector = DetectorSilencio(cfg.silencio_seg, cfg.max_gravacao_seg, espera, cfg.limiar_voz)
    pedacos = []
    primeiro = ultimo = None  # índices dos blocos com voz: só a fala (com uma folga) vai para a transcrição
    with sd.InputStream(samplerate=TAXA, channels=1, dtype="int16", blocksize=bloco) as fluxo:
        while True:
            dados, _ = fluxo.read(bloco)
            pedacos.append(dados.copy())
            rms = float(np.sqrt(np.mean((dados.astype(np.float32) / 32768.0) ** 2)))
            if rms >= cfg.limiar_voz:
                primeiro = len(pedacos) - 1 if primeiro is None else primeiro
                ultimo = len(pedacos) - 1
            if ao_nivel:
                ao_nivel(nivel_visual(rms))
            estado = detector.processar(rms, BLOCO_SEG)
            if estado == "sem_fala":
                return None
            if estado == "fim":
                break
    if primeiro is not None:
        folga = int(0.3 / BLOCO_SEG)
        pedacos = pedacos[max(0, primeiro - folga) : ultimo + folga + 1]
    saida = io.BytesIO()
    with wave.open(saida, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TAXA)
        w.writeframes(np.concatenate(pedacos).tobytes())
    return saida.getvalue()
