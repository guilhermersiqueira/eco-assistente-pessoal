"""Supervisor: reabre a Eco se o processo dela cair.

`eco vigiar` roda a Eco (`eco voz`) como processo filho e espera. Se ela termina com código 0, foi de propósito (menu
Sair, Ctrl+C, ou já havia outra Eco aberta) e o supervisor também termina. Qualquer outro código é queda (erro nativo
do Qt ou do áudio, exceção não tratada): o supervisor espera um pouco e abre de novo, com espera crescente. Se cair
muitas vezes seguidas em pouco tempo, ele desiste e registra no log, para não ficar num ciclo sem fim.

O supervisor não segura a trava de instância única (`eco.lock`); quem segura é a Eco filha, como sempre.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from typing import Callable

ESPERAS = (3, 10, 30, 60)  # segundos antes de reabrir, conforme o número de quedas recentes
MAX_QUEDAS = 5  # quedas dentro de JANELA_S: o supervisor desiste
JANELA_S = 15 * 60


def comando_eco(executavel: str | None = None) -> list[str]:
    """A Eco no modo voz, com o mesmo Python (pythonw continua sem janela de terminal)."""
    return [executavel or sys.executable, "-c", "import sys; from eco.principal import main; sys.argv = ['eco', 'voz']; main()"]


def _hora() -> str:
    return datetime.now().strftime("%H:%M:%S")


def vigiar(
    comando: list[str] | None = None,
    rodar: Callable[[list[str]], int] | None = None,
    dormir: Callable[[float], None] = time.sleep,
    relogio: Callable[[], float] = time.monotonic,
    avisar: Callable[[str], None] = print,
) -> int:
    """Roda a Eco até ela sair de propósito. Devolve o último código de saída."""
    comando = comando or comando_eco()
    rodar = rodar or (lambda c: subprocess.call(c))
    quedas: list[float] = []
    while True:
        avisar(f"[supervisor] abrindo a Eco ({_hora()})")
        codigo = rodar(comando)
        if codigo == 0:
            avisar(f"[supervisor] a Eco fechou normalmente ({_hora()}); supervisor encerrado")
            return 0
        agora = relogio()
        quedas = [t for t in quedas if agora - t < JANELA_S] + [agora]
        if len(quedas) >= MAX_QUEDAS:
            avisar(f"[supervisor] a Eco caiu {len(quedas)} vezes em {JANELA_S // 60} min (último código {codigo}); desisti de reabrir. Veja o eco.log.")
            return codigo
        espera = ESPERAS[min(len(quedas) - 1, len(ESPERAS) - 1)]
        avisar(f"[supervisor] a Eco caiu (código {codigo}) às {_hora()}; reabrindo em {espera} s")
        dormir(espera)


def comando_vigiar() -> int:
    """`eco vigiar`. Ctrl+C no terminal fecha a Eco e o supervisor."""
    try:
        return vigiar()
    except KeyboardInterrupt:
        avisar = print
        avisar("[supervisor] interrompido pelo terminal")
        return 0
