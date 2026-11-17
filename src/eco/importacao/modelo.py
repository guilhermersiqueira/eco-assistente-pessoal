"""Formato neutro das conversas importadas: o leitor do export (M1) converte o arquivo do claude.ai para isto, e o resto
da importação (filtro, extração, estado) só conhece estas classes, não o formato do arquivo."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

DONO = "dono"
ASSISTENTE = "assistente"
PREFIXO_MEMORIA = "memoria:"  # uuid das "conversas" que vêm da memória do claude.ai (um assunto da memória cada)


@dataclass(frozen=True)
class Mensagem:
    autor: str  # DONO ou ASSISTENTE
    texto: str
    quando: str = ""  # ISO 8601; vazio se o export não trouxe a hora


@dataclass(frozen=True)
class Conversa:
    uuid: str  # id da conversa no claude.ai
    titulo: str
    criada_em: str = ""
    atualizada_em: str = ""
    mensagens: tuple[Mensagem, ...] = ()

    @property
    def da_memoria(self) -> bool:
        """Veio de memories-NNN.zip: cada mensagem do dono é um item que o claude.ai resumiu de várias conversas."""
        return self.uuid.startswith(PREFIXO_MEMORIA)

    def do_dono(self) -> list[Mensagem]:
        return [m for m in self.mensagens if m.autor == DONO and m.texto.strip()]

    @property
    def hash(self) -> str:
        """Muda quando alguma mensagem muda ou chega mensagem nova (o título sozinho não conta)."""
        base = "\x1e".join(f"{m.autor}\x1f{m.texto}" for m in self.mensagens)
        return hashlib.sha1(base.encode("utf-8")).hexdigest()
