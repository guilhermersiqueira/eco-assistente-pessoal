"""Confirmação por voz: o Eco lê a ação de volta e só segue se ouvir "sim"."""

from __future__ import annotations

import re
import unicodedata
from typing import Callable

AFIRMATIVAS = {"sim", "confirmo", "confirmado", "pode", "positivo", "claro", "certo", "ok"}
NEGATIVAS = {"nao", "cancela", "cancelar", "cancele", "espera", "pare"}


def _normalizar(texto: str) -> list[str]:
    sem_acento = unicodedata.normalize("NFD", texto.lower())
    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z]+", sem_acento)


def eh_sim(texto: str) -> bool:
    """Só aceita resposta que comece com uma palavra afirmativa e não contenha negação."""
    palavras = _normalizar(texto)
    if not palavras or NEGATIVAS & set(palavras):
        return False
    return palavras[0] in AFIRMATIVAS


DESPEDIDAS = ("tchau", "ate logo", "ate mais", "so isso", "e so isso", "era so isso", "pode parar", "pode dormir", "obrigada eco", "obrigado eco", "desligar", "desliga", "fechar", "fecha", "encerrar", "encerra")


def eh_despedida(texto: str) -> bool:
    """Frases curtas que encerram a conversa (a Eco volta a esperar o 'ei eco')."""
    frase = " ".join(_normalizar(texto))
    return len(frase.split()) <= 6 and any(d in frase for d in DESPEDIDAS)


PERGUNTA_DE_NOVO = "Não ouvi. Posso confirmar?"


def confirmar_por_voz(descricao: str, falar: Callable[[str], None], ouvir: Callable[[], str]) -> bool:
    """Lê a ação de volta e só segue com um "sim" claro. Se não ouvir nada (o "sim" pode cair antes de o microfone
    abrir), pergunta de novo UMA vez; um segundo silêncio cancela. Silêncio nunca confirma."""
    falar(f"{descricao} Posso confirmar?")
    resposta = ouvir()
    if not resposta.strip():
        falar(PERGUNTA_DE_NOVO)
        resposta = ouvir()
    return eh_sim(resposta)
