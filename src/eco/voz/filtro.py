"""Filtro local de ruído (Silero VAD) antes da transcrição.

A gravação termina por volume (`gravacao.DetectorSilencio`), então barulho de TV, música ou alguém falando longe às
vezes vira "fala". Mandado para a transcrição, isso volta como frases soltas ("Сфера", "Thanks.", "nasıl sadecele") e
cada uma gera uma resposta paga do Claude e da voz. Aqui o próprio computador confere, em ~30 ms por trecho, se há voz
humana de verdade; sem pelo menos `MIN_FALA_S` de voz, o áudio é descartado sem custo.

O modelo (`silero_vad.onnx`) já vem com o openWakeWord e roda no `onnxruntime`, os dois já instalados. Se não carregar,
o filtro deixa tudo passar (nunca deixa a Eco surda) e avisa uma vez no log. Desliga com ECO_FILTRO_VOZ=nao.
"""

from __future__ import annotations

import io
import wave

import numpy as np

LIMIAR = 0.5  # probabilidade de voz de um trecho de 30 ms para contar como fala
# Menos que isso de fala na gravação inteira: ruído. Calibrado em 12/11/2026 com a voz do Windows: "Sim", "Não" e
# "Pode" sozinhos dão 0,33 a 0,51 s de voz detectada; silêncio, ruído branco e um tom contínuo dão 0,00 s.
MIN_FALA_S = 0.15
QUADRO = 480  # 30 ms a 16 kHz


class FiltroDeVoz:
    def __init__(self, criar_modelo=None) -> None:
        self._criar = criar_modelo
        self._modelo = None
        self._falhou = False

    def _vad(self):
        if self._modelo is None and not self._falhou:
            try:
                if self._criar is not None:
                    self._modelo = self._criar()
                else:
                    from openwakeword.vad import VAD

                    self._modelo = VAD()
            except Exception as erro:  # sem modelo: deixa passar tudo
                self._falhou = True
                print(f"  [ruído] filtro de voz desligado: {type(erro).__name__}: {erro}")
        return self._modelo

    def segundos_de_fala(self, wav: bytes) -> float | None:
        """Quanto do áudio é voz, em segundos (None se não deu para analisar)."""
        vad = self._vad()
        if vad is None:
            return None
        try:
            with wave.open(io.BytesIO(wav)) as w:
                if w.getframerate() != 16000 or w.getsampwidth() != 2:
                    return None
                dados = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                if w.getnchannels() > 1:
                    dados = dados.reshape(-1, w.getnchannels()).mean(axis=1).astype(np.int16)
        except (wave.Error, EOFError, ValueError):
            return None
        n = len(dados) // QUADRO * QUADRO
        if n == 0:
            return 0.0
        vad.reset_states()
        com_voz = 0
        for i in range(0, n, QUADRO):
            if float(vad.predict(dados[i : i + QUADRO], QUADRO)) >= LIMIAR:
                com_voz += 1
        return com_voz * QUADRO / 16000

    def tem_fala(self, wav: bytes) -> bool:
        segundos = self.segundos_de_fala(wav)
        return segundos is None or segundos >= MIN_FALA_S
