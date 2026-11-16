"""Camada que fala com o modelo de IA. A interface do chat só conhece `Provedor`: para trocar de provedor
(OpenAI, Gemini, um modelo local), basta escrever outra classe com o método `transmitir`."""

from __future__ import annotations

import threading
from typing import Any, Iterator, Protocol

from eco.config import Config
from eco.uso import RegistroUso, extrair


class Provedor(Protocol):
    def transmitir(self, sistema: str, mensagens: list[dict[str, str]], parar: threading.Event) -> Iterator[str]:
        """Devolve a resposta aos poucos (pedaços de texto). Deve parar logo que `parar` for acionado.
        `mensagens` é a lista alternada [{"role": "user"|"assistant", "content": texto}, ...]."""
        ...


class ProvedorClaude:
    """Claude (Anthropic) com resposta em streaming."""

    def __init__(self, cfg: Config, cliente: Any | None = None, registro: RegistroUso | None = None) -> None:
        import anthropic

        self.cfg = cfg
        self.cliente = cliente or anthropic.Anthropic()
        self.registro = registro

    def transmitir(self, sistema: str, mensagens: list[dict[str, str]], parar: threading.Event) -> Iterator[str]:
        modelo = self.cfg.modelo_chat
        with self.cliente.messages.stream(
            model=modelo,
            max_tokens=4096,
            system=sistema,
            messages=mensagens,
            output_config={"effort": self.cfg.esforco_chat},
        ) as fluxo:
            for pedaco in fluxo.text_stream:
                if parar.is_set():
                    return  # sair do `with` fecha a conexão: o modelo para de gerar (e de cobrar) na hora
                yield pedaco
            final = fluxo.get_final_message()
        if self.registro is not None:
            self.registro.registrar(extrair(modelo, final))
        if final.stop_reason == "refusal":
            yield "Não posso ajudar com esse pedido."
        elif final.stop_reason == "max_tokens":
            yield "\n\n*(a resposta foi cortada)*"
