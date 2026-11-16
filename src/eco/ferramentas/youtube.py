"""Transcrição de vídeos do YouTube (biblioteca não oficial: pode quebrar se o YouTube mudar)."""

from __future__ import annotations

import re

LIMITE_CARACTERES = 60_000
_PADRAO_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")


def extrair_id(url_ou_id: str) -> str:
    texto = url_ou_id.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", texto):
        return texto
    achado = _PADRAO_ID.search(texto)
    if not achado:
        raise ValueError("Não consegui identificar o vídeo nesse link.")
    return achado.group(1)


def obter_transcricao(url_ou_id: str, limite: int = LIMITE_CARACTERES) -> str:
    from youtube_transcript_api import YouTubeTranscriptApi

    video_id = extrair_id(url_ou_id)
    try:
        trechos = YouTubeTranscriptApi().fetch(video_id, languages=["pt", "pt-BR", "en"])
    except Exception as erro:  # a biblioteca tem várias classes de erro; a mensagem basta
        raise ValueError(
            "Não consegui obter a transcrição: o vídeo pode não ter legenda, estar privado ou indisponível. "
            f"({type(erro).__name__})"
        ) from erro
    texto = " ".join(t.text.replace("\n", " ") for t in trechos).strip()
    if not texto:
        raise ValueError("A transcrição do vídeo veio vazia.")
    if len(texto) > limite:
        return texto[:limite] + f"\n[Transcrição cortada: só os primeiros {limite} caracteres de {len(texto)}.]"
    return texto
