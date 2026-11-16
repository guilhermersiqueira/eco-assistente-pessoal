"""Filtro local de ruído com Silero VAD (melhoria autorizada pelo dono junto da E2)."""

from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from eco.voz import filtro as f


def _wav(amostras: np.ndarray, taxa: int = 16000) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes(amostras.astype(np.int16).tobytes())
    return b.getvalue()


class VadFalso:
    """'Voz' onde o trecho é alto (só para testar a contagem, sem o modelo de verdade)."""

    def __init__(self):
        self.resets = 0

    def reset_states(self):
        self.resets += 1

    def predict(self, x, frame_size):
        return 0.9 if np.abs(x).mean() > 1000 else 0.1


def _audio(segundos_de_voz: float, total: float = 2.0) -> bytes:
    a = np.zeros(int(16000 * total))
    a[: int(16000 * segundos_de_voz)] = 5000
    return _wav(a)


def test_conta_os_segundos_de_voz_e_decide_pelo_minimo():
    filtro = f.FiltroDeVoz(VadFalso)
    assert filtro.segundos_de_fala(_audio(0.6)) == pytest.approx(0.6, abs=0.03)
    assert filtro.tem_fala(_audio(0.6)) and filtro.tem_fala(_audio(0.18))  # um "sim" curto passa
    assert not filtro.tem_fala(_audio(0.06)) and not filtro.tem_fala(_audio(0.0))


def test_cada_gravacao_comeca_do_zero():
    vad = VadFalso()
    filtro = f.FiltroDeVoz(lambda: vad)
    filtro.segundos_de_fala(_audio(0.3))
    filtro.segundos_de_fala(_audio(0.3))
    assert vad.resets == 2  # o estado do modelo não passa de uma gravação para outra


def test_sem_modelo_deixa_tudo_passar(capsys):
    def quebra():
        raise FileNotFoundError("silero_vad.onnx")

    filtro = f.FiltroDeVoz(quebra)
    assert filtro.tem_fala(_audio(0.0))  # nunca deixa a Eco surda
    assert filtro.tem_fala(_audio(0.0))
    assert capsys.readouterr().out.count("filtro de voz desligado") == 1  # avisa uma vez só


def test_audio_que_nao_da_para_analisar_passa():
    filtro = f.FiltroDeVoz(VadFalso)
    assert filtro.tem_fala(b"isso nao e wav")
    assert filtro.tem_fala(_wav(np.zeros(8000), taxa=8000))  # taxa diferente: não arrisca descartar


def test_modelo_de_verdade_descarta_silencio_ruido_e_tom():
    pytest.importorskip("openwakeword")
    filtro = f.FiltroDeVoz()
    rng = np.random.default_rng(1)
    t = np.arange(48000) / 16000
    for amostras in (np.zeros(48000), rng.normal(0, 1500, 48000), 3000 * np.sin(2 * np.pi * 440 * t)):
        assert filtro.segundos_de_fala(_wav(amostras)) < f.MIN_FALA_S


def test_configuracao(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    from eco.config import carregar_config

    assert carregar_config().filtro_voz
    monkeypatch.setenv("ECO_FILTRO_VOZ", "nao")
    assert not carregar_config().filtro_voz
