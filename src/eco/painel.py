"""O que o fluxo de voz usa para mostrar o que está acontecendo (janela, console ou nada)."""

from __future__ import annotations

import threading

ESTADOS_VALIDOS = ("idle", "listen", "think", "speak", "confirm", "error", "mute")


class Painel:
    """Versão que não faz nada. As outras herdam daqui e sobrescrevem o que precisam."""

    def __init__(self) -> None:
        self.mudo = False  # a janela liga/desliga (menu); a thread de voz só lê
        self.pedido_repetir = threading.Event()  # a janela liga; a thread de voz atende e limpa
        self.interromper = threading.Event()  # a janela liga para calar a Eco no meio da fala; a thread de voz limpa
        self.cerebro = None  # o Cerebro (agente único + conversa atual); a thread de voz preenche, a aba Conversas usa

    def estado(self, nome: str) -> None:
        pass

    def nivel(self, valor: float) -> None:
        """Volume de 0 a 1 (microfone ao ouvir, áudio da Eco ao falar)."""

    def legenda(self, texto: str, fixa: bool = False) -> None:
        pass

    def memoria(self) -> None:
        pass

    def notificar(self, titulo: str, texto: str) -> None:
        """Notificação do sistema (balão perto do relógio), usada pelos lembretes."""

    def flutuante(self, texto: str) -> None:
        """Mostra código ou texto longo numa janela à parte (sem janela, não faz nada)."""

    def pedir_texto(self, titulo: str, rotulo: str, valor: str = "") -> tuple[str, bool] | None:
        """Abre uma caixa para o dono digitar (ex.: o destinatário de um e-mail). None: não há tela para isso."""
        return None

    def abrir_interface(self) -> bool:
        """Abre a janela principal. False se não há janela (modo sem visual)."""
        return False


class PainelConsole(Painel):
    def legenda(self, texto: str, fixa: bool = False) -> None:
        if texto:
            print(texto, flush=True)


class PainelMisto(Painel):
    def __init__(self, *paineis: Painel) -> None:
        super().__init__()
        self._paineis = paineis

    def estado(self, nome: str) -> None:
        for p in self._paineis:
            p.estado(nome)

    def nivel(self, valor: float) -> None:
        for p in self._paineis:
            p.nivel(valor)

    def legenda(self, texto: str, fixa: bool = False) -> None:
        for p in self._paineis:
            p.legenda(texto, fixa)

    def memoria(self) -> None:
        for p in self._paineis:
            p.memoria()

    def notificar(self, titulo: str, texto: str) -> None:
        for p in self._paineis:
            p.notificar(titulo, texto)

    def pedir_texto(self, titulo: str, rotulo: str, valor: str = "") -> tuple[str, bool] | None:
        for p in self._paineis:
            r = p.pedir_texto(titulo, rotulo, valor)
            if r is not None:
                return r
        return None

    def abrir_interface(self) -> bool:
        return any([p.abrir_interface() for p in self._paineis])
