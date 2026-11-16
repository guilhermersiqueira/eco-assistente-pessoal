"""Transcrição (OpenAI) e fala (OpenAI TTS)."""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from typing import Any

from eco.config import Config


def segundos_wav(wav: bytes) -> float:
    """Duração de um WAV em segundos (0 se não der para ler).

    Conta pelos bytes de áudio que de fato vieram, não pelo tamanho escrito no cabeçalho: a voz da OpenAI vem em WAV
    "de transmissão", com o tamanho marcado como desconhecido (0xFFFFFFFF), o que daria ~50 horas por frase."""
    try:
        with wave.open(io.BytesIO(wav)) as w:
            por_quadro = w.getsampwidth() * w.getnchannels()
            dados = w.readframes(w.getnframes())  # lê até o fim do que existe, mesmo com o tamanho "desconhecido"
            return len(dados) / float(por_quadro or 1) / float(w.getframerate() or 1)
    except (wave.Error, EOFError):
        return 0.0


def transcrever(cliente: Any, wav: bytes, cfg: Config) -> str:
    r = cliente.audio.transcriptions.create(
        model=cfg.modelo_stt,
        file=("fala.wav", wav, "audio/wav"),
        language="pt",
    )
    return r.text.strip()


def envelope(dados, taxa: int, janela_seg: float = 0.02):
    """Volume (0 a 1) a cada `janela_seg`, normalizado pelo pico típico da própria fala."""
    import numpy as np

    mono = dados.astype(np.float32).reshape(len(dados), -1).mean(axis=1) / 32768.0
    n = max(1, int(taxa * janela_seg))
    k = len(mono) // n
    if k == 0:
        return np.zeros(1, np.float32)
    rms = np.sqrt((mono[: k * n].reshape(k, n) ** 2).mean(axis=1))
    ref = max(float(np.percentile(rms, 95)), 1e-4)
    return np.clip(rms / ref, 0, 1).astype(np.float32)


def tocar_wav(wav: bytes, ao_nivel=None, interromper=None) -> float:
    """Toca o WAV e devolve a fração tocada (1.0 = inteiro). Com `ao_nivel`, envia o volume do que
    está tocando para animar o orbe. Se `interromper` (threading.Event) for acionado, para na hora."""
    import time

    import numpy as np
    import sounddevice as sd

    if interromper is not None and interromper.is_set():
        return 0.0
    with wave.open(io.BytesIO(wav)) as w:
        taxa, canais = w.getframerate(), w.getnchannels()
        dados = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, canais)
    sd.play(dados, taxa)
    env = envelope(dados, taxa) if ao_nivel else None
    t0 = time.perf_counter()
    duracao = len(dados) / taxa
    while (t := time.perf_counter() - t0) < duracao:
        if interromper is not None and interromper.is_set():
            sd.stop()
            if ao_nivel:
                ao_nivel(0.0)
            return min(1.0, t / duracao)
        if ao_nivel:
            ao_nivel(float(env[min(int(t / 0.02), len(env) - 1)]))
        time.sleep(0.02)
    if ao_nivel:
        ao_nivel(0.0)
    sd.wait()
    return 1.0


def dividir_frases(texto: str, minimo: int = 40) -> list[str]:
    """Quebra em frases para gerar a voz aos poucos; junta pedaços curtos para não picotar a fala."""
    import re

    partes = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", texto.strip()) if p.strip()]
    blocos: list[str] = []
    for p in partes:
        if blocos and len(blocos[-1]) < minimo:
            blocos[-1] += " " + p
        else:
            blocos.append(p)
    return blocos


@dataclass(frozen=True)
class Fala:
    falado: str  # o que de fato saiu pelo alto-falante (o texto inteiro se ninguém interrompeu)
    interrompida: bool = False
    segundos_gerados: float = 0.0  # voz que a OpenAI gerou (é o que ela cobra), mesmo a que não chegou a tocar


def trecho_falado(texto: str, fracao: float) -> str:
    """Aproxima o que já foi dito de um bloco tocado só até `fracao`, cortando em palavra inteira."""
    palavras = texto.split()
    return " ".join(palavras[: int(len(palavras) * max(0.0, min(1.0, fracao)))])


def falar(cliente: Any, texto: str, cfg: Config, ao_nivel=None, interromper=None) -> Fala:
    """Gera a voz frase a frase: a primeira toca enquanto as próximas ainda são geradas.
    Se `interromper` (threading.Event) for acionado, a fala para na hora, as frases que faltam
    deixam de ser geradas e o resultado diz até onde ela chegou."""
    import queue
    import threading

    blocos = dividir_frases(texto)
    if not blocos:
        return Fala(texto)
    extras: dict[str, Any] = {}
    if cfg.estilo_voz:
        extras["instructions"] = cfg.estilo_voz
    if cfg.velocidade_tts:
        extras["speed"] = cfg.velocidade_tts

    fila: queue.Queue = queue.Queue()
    parar_geracao = threading.Event()
    gerado = [0.0]  # segundos de voz já gerados (para a estimativa de custo)

    def gerar() -> None:
        for bloco in blocos:
            if parar_geracao.is_set():
                return
            try:
                r = cliente.audio.speech.create(
                    model=cfg.modelo_tts, voice=cfg.voz_tts, input=bloco, response_format="wav", **extras
                )
                dados = r.read()
                gerado[0] += segundos_wav(dados)
                fila.put((bloco, dados))
            except Exception as erro:
                fila.put(erro)
                return
        fila.put(None)

    threading.Thread(target=gerar, daemon=True).start()
    ditos: list[str] = []
    try:
        while (item := fila.get()) is not None:
            if isinstance(item, Exception):
                raise item
            bloco, wav = item
            fracao = tocar_wav(wav, ao_nivel, interromper)
            fracao = 1.0 if fracao is None else fracao
            if fracao < 1.0 or (interromper is not None and interromper.is_set()):
                parte = trecho_falado(bloco, fracao)
                return Fala(" ".join([*ditos, *([parte] if parte else [])]), True, gerado[0])
            ditos.append(bloco)
    finally:
        parar_geracao.set()  # não gasta mais voz com o que não vai ser dito
    return Fala(" ".join(ditos), segundos_gerados=gerado[0])
