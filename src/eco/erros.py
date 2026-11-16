"""Traduz erros técnicos em frases curtas para a legenda."""

from __future__ import annotations


def mensagem_de_erro(erro: BaseException) -> str:
    msg = str(erro).lower()
    nome = type(erro).__name__.lower()
    if any(p in msg for p in ("credit", "insufficient_quota", "quota", "balance")):
        return "Sem crédito na API. Adicione crédito e tente de novo."
    if "connection" in nome or "timeout" in nome or "getaddrinfo" in msg:
        return "Sem conexão com a internet."
    if "authentication" in nome or "401" in msg or "api key" in msg or "api_key" in msg:
        return "Chave de API inválida ou ausente."
    if "ratelimit" in nome or "429" in msg:
        return "Muitas chamadas seguidas. Tente de novo em instantes."
    return "Tive um problema. Tente de novo."
