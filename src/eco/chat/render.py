"""Markdown -> HTML para a janela do chat (QTextBrowser), com blocos de código que têm um link "Copiar"."""

from __future__ import annotations

import html
import re
from typing import Any

from markdown_it import MarkdownIt

from eco.visual import tema

_md = MarkdownIt("commonmark", {"html": False}).enable("table")  # HTML cru da resposta nunca é renderizado
_BLOCO_CODIGO = re.compile(r'<pre><code(?: class="language-([^"]*)")?>(.*?)</code></pre>', re.DOTALL)

FUNDO_CODIGO = tema.FUNDO


def markdown_para_html(texto: str, codigos: list[str], cor: str) -> str:
    """Renderiza o Markdown. Cada bloco de código vira uma tabelinha com o link `copiar:N`; o texto puro do bloco
    é guardado em `codigos[N]` para a janela copiar para a área de transferência."""

    def bloco(m: re.Match) -> str:
        linguagem, corpo = m.group(1) or "", m.group(2)
        codigos.append(html.unescape(corpo))
        return (
            f'<table width="100%" cellspacing="0" cellpadding="6" bgcolor="{FUNDO_CODIGO}">'
            f'<tr><td><span style="color:{tema.TEXTO_2}; font-family:{tema.FONTE_MONO}">{html.escape(linguagem)}</span></td>'
            f'<td align="right"><a href="copiar:{len(codigos) - 1}" style="color:{cor}">Copiar</a></td></tr>'
            f'<tr><td colspan="2"><pre style="margin:0">{corpo}</pre></td></tr></table>'
        )

    return _BLOCO_CODIGO.sub(bloco, _md.render(texto))


def _bolha(rotulo: str, cor_rotulo: str, fundo: str, corpo: str, nota: str = "") -> str:
    return (
        '<table width="100%" cellspacing="0" cellpadding="10"><tr>'
        f'<td bgcolor="{fundo}"><span style="color:{cor_rotulo}; font-weight:600">{rotulo}</span>'
        f'<span style="color:{tema.TEXTO_2}"> {nota}</span><br>{corpo}</td></tr></table>'
        '<p style="margin:0; font-size:6px">&nbsp;</p>'
    )


def renderizar_conversa(
    mensagens: list[Any], cor: str = tema.CORES_ESPACOS[0], rascunho: str | None = None, erro: str | None = None
) -> tuple[str, list[str]]:
    """Página HTML da conversa inteira + a lista dos blocos de código (índice = número no link `copiar:N`).
    `mensagens` têm `.papel`, `.conteudo` e `.interrompida`. `rascunho` é a resposta que ainda está chegando."""
    codigos: list[str] = []
    partes: list[str] = []
    for m in mensagens:
        if m.papel == "user":
            corpo = html.escape(m.conteudo).replace("\n", "<br>")
            partes.append(_bolha("Você", tema.TEXTO_2, tema.ELEVADO, corpo))
        else:
            nota = "(interrompida)" if m.interrompida else ""
            partes.append(_bolha("Eco", cor, tema.SUPERFICIE, markdown_para_html(m.conteudo, codigos, cor), nota))
    if rascunho is not None:
        partes.append(_bolha("Eco", cor, tema.SUPERFICIE, markdown_para_html(rascunho, codigos, cor) if rascunho else "", "escrevendo…"))
    if erro:
        partes.append(_bolha("Erro", tema.ALERTA, tema.ALERTA_FUNDO, html.escape(erro)))
    return "<html><body>" + "".join(partes) + "</body></html>", codigos
