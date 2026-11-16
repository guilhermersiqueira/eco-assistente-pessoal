"""Comandos de voz que a Eco resolve sozinha, sem passar pelo Claude (rápidos, grátis e previsíveis).

O filtro é estrito de propósito: só reconhece a frase curta e inteira ("abra a interface"). Qualquer coisa
parecida, mas diferente ("me explica como abrir a interface do docker"), segue o caminho normal da conversa.
"""

from __future__ import annotations

import re

from eco.confirmacao import _normalizar

_ABRIR_INTERFACE = re.compile(
    r"^(?:(?:ei|hey|oi|ola)\s+)?(?:(?:eco|echo|ecco|eko|ekko|equo)\s+)?(?:por favor\s+)?(?:(?:pode|poderia)\s+)?"
    r"(?:abra|abre|abrir|mostra|mostre|mostrar|exibe|exiba|exibir|quero ver|me mostra|me mostre)\s+"
    r"(?:(?:a|essa|minha|sua)\s+)?(?:interface|janela principal)"
    r"(?:\s+(?:do|da|de)\s+(?:eco|echo|ecco|eko|ekko|equo))?(?:\s+(?:pra|para) mim)?(?:\s+por favor)?$"
)


def eh_abrir_interface(texto: str) -> bool:
    """True para "abra a interface", "abrir interface", "mostra a interface", "eco, abre a interface por favor"..."""
    return bool(_ABRIR_INTERFACE.match(" ".join(_normalizar(texto))))


_PREFIXO = (
    r"^(?:(?:ei|hey|oi|ola)\s+)?(?:(?:eco|echo|ecco|eko|ekko|equo)\s+)?(?:por favor\s+)?(?:(?:pode|poderia)\s+)?"
)
_SUFIXO = r"(?:\s+(?:pra|para) mim)?(?:\s+por favor)?$"
_MODO_FOCO = r"(?:(?:o|um)\s+)?modo (?:de )?foco"
_LIGAR_FOCO = re.compile(
    _PREFIXO + r"(?:(?:ative|ativa|ativar|ligue|liga|ligar|inicie|inicia|iniciar|comece|comeca|começar|comecar)\s+" + _MODO_FOCO
    + r"|(?:entre|entra|entrar)\s+(?:no|em)\s+modo (?:de )?foco)" + _SUFIXO
)
_DESLIGAR_FOCO = re.compile(
    _PREFIXO + r"(?:(?:desative|desativa|desativar|desligue|desliga|desligar|encerre|encerra|encerrar|pare|para|parar|termine|termina|terminar)\s+"
    + _MODO_FOCO + r"|(?:saia|sai|sair)\s+(?:do|de)\s+modo (?:de )?foco)" + _SUFIXO
)


_EXPLICACAO = re.compile(r"^(?:(?:ei|hey|oi|ola)\s+)?(?:(?:eco|echo|ecco|eko|ekko|equo)\s+)?(?:por favor\s+)?"
                         r"(?:explique me|explica me|me explique|me explica|explique|explica)\b")


def eh_pedido_de_explicacao(texto: str) -> bool:
    """True para "Eco, explique-me como abrir a interface", "me explica o modo foco"... (o pedido vai ao modelo com os
    trechos do manual da Eco). Diferente dos comandos locais, aqui o resto da frase é livre."""
    return bool(_EXPLICACAO.match(" ".join(_normalizar(texto))))


def comando_foco(texto: str) -> str | None:
    """'ligar' ou 'desligar' para "Eco, ative o modo foco" / "desative o modo foco" (e variações); None para o resto.
    Estrito como `eh_abrir_interface`: "estudo matéria em foco" ou "o que é modo foco?" seguem para a conversa."""
    frase = " ".join(_normalizar(texto))
    if _DESLIGAR_FOCO.match(frase):
        return "desligar"
    if _LIGAR_FOCO.match(frase):
        return "ligar"
    return None
