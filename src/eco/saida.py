"""Saída de texto da Eco: terminal ou `~/.eco/eco.log`, sem quebrar com caracteres fora do padrão do Windows.

Antes, uma transcrição com letra de outro alfabeto (ex.: "ş") fazia o `print` estourar `'charmap' codec can't encode`
quando a saída ia para um arquivo ou cano em cp1252, e o pedido inteiro falhava. Agora:
- terminal de verdade: mantém a codificação dele e troca só o caractere impossível por "?";
- arquivo ou cano: UTF-8.
No log (sem terminal), cada linha ganha a data e a hora, e o arquivo é girado quando passa de `TAMANHO_MAXIMO`.
"""

from __future__ import annotations

import io
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO

TAMANHO_MAXIMO = 5 * 1024 * 1024  # 5 MB
GUARDAR = 3  # eco.log.1 a eco.log.3


def ajustar_codificacao(fluxo: TextIO | None) -> None:
    """Deixa `print` à prova de qualquer caractere neste fluxo (terminal: troca o impossível; arquivo/cano: UTF-8)."""
    if fluxo is None or not hasattr(fluxo, "reconfigure"):
        return
    try:
        if fluxo.isatty():
            fluxo.reconfigure(errors="replace")
        else:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError, io.UnsupportedOperation):
        pass


def girar(arquivo: Path, tamanho_maximo: int = TAMANHO_MAXIMO, guardar: int = GUARDAR) -> bool:
    """Se o log passou do tamanho, vira eco.log.1 (o .1 vira .2, ...; o mais antigo some). True se girou."""
    if not arquivo.exists() or arquivo.stat().st_size < tamanho_maximo:
        return False
    for i in range(guardar, 0, -1):
        velho = arquivo.with_name(f"{arquivo.name}.{i}")
        if i == guardar and velho.exists():
            velho.unlink()
        anterior = arquivo if i == 1 else arquivo.with_name(f"{arquivo.name}.{i - 1}")
        if anterior.exists():
            anterior.replace(velho)
    return True


class ComHora(io.TextIOBase):
    """Escreve no arquivo pondo "AAAA-MM-DD HH:MM:SS " no começo de cada linha (para saber quando cada coisa aconteceu)."""

    def __init__(self, destino: TextIO, relogio=datetime.now) -> None:
        self._destino = destino
        self._relogio = relogio
        self._inicio_de_linha = True

    def write(self, texto: str) -> int:
        partes = []
        for pedaco in texto.splitlines(keepends=True):
            if self._inicio_de_linha and pedaco not in ("\n", "\r\n"):
                partes.append(self._relogio().strftime("%Y-%m-%d %H:%M:%S "))
            partes.append(pedaco)
            self._inicio_de_linha = pedaco.endswith("\n")
        self._destino.write("".join(partes))
        return len(texto)

    def flush(self) -> None:
        self._destino.flush()

    def writable(self) -> bool:
        return True

    @property
    def encoding(self) -> str:
        return "utf-8"

    def fileno(self) -> int:  # o faulthandler escreve direto no descritor do arquivo
        return self._destino.fileno()


def abrir_log(arquivo: Path) -> ComHora:
    """Gira o log se estiver grande e abre para acrescentar, em UTF-8, com hora em cada linha."""
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    try:
        girar(arquivo)
    except OSError:  # outro processo (o supervisor, ou a Eco) está com o log aberto: gira na próxima vez
        pass
    return ComHora(open(arquivo, "a", encoding="utf-8", errors="replace", buffering=1))


def preparar_terminal() -> None:
    ajustar_codificacao(sys.stdout)
    ajustar_codificacao(sys.stderr)
