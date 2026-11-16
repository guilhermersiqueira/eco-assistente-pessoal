"""Manual da Eco (`ajuda.md`): a Eco consulta para explicar a si mesma.

- "Eco, explique-me como abrir a interface": o pedido já vai ao modelo com os trechos certos do manual (sem chamada
  extra à IA); ver `comandos.eh_pedido_de_explicacao` e `trechos_para`.
- Outras formas de perguntar ("como eu faço para...?"): o modelo chama a ferramenta `manual_da_eco`.
A busca é local e por palavras (sem acento, sem custo): título e linha "palavras:" de cada seção valem mais.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ARQUIVO = Path(__file__).with_name("ajuda.md")
# palavras comuns que não ajudam a achar a seção
_VAZIAS = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas", "um", "uma", "para", "pra",
    "por", "com", "como", "que", "eu", "me", "mim", "voce", "eco", "ei", "explique", "explica", "explicar", "faco", "fazer",
    "funciona", "usar", "uso", "se", "sua", "seu", "minha", "meu", "posso", "pode", "qual", "quais", "isso", "esse", "essa",
}


@dataclass(frozen=True)
class Secao:
    titulo: str
    palavras: frozenset[str]
    texto: str


def _norm(texto: str) -> list[str]:
    s = unicodedata.normalize("NFD", texto.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return [p for p in re.findall(r"[a-z0-9]+", s) if p not in _VAZIAS and len(p) > 1]


def _radical(p: str) -> str:
    """Corta finais comuns para "lembrança" achar "lembrar", "tarefas" achar "tarefa"."""
    for fim in ("coes", "cao", "ancas", "anca", "ar", "er", "ir", "as", "es", "os", "s", "a", "o", "e"):
        if len(p) > len(fim) + 3 and p.endswith(fim):
            return p[: -len(fim)]
    return p


@lru_cache(maxsize=1)
def secoes() -> tuple[Secao, ...]:
    blocos = re.split(r"^## ", ARQUIVO.read_text(encoding="utf-8"), flags=re.M)[1:]
    saida = []
    for b in blocos:
        linhas = b.strip().splitlines()
        titulo = linhas[0].strip()
        palavras, corpo = "", []
        for l in linhas[1:]:
            if l.startswith("palavras:"):
                palavras = l[len("palavras:"):]
            else:
                corpo.append(l)
        termos = {_radical(p) for p in _norm(titulo + " " + palavras)}
        saida.append(Secao(titulo, frozenset(termos), " ".join(x.strip() for x in corpo if x.strip())))
    return tuple(saida)


def topicos() -> list[str]:
    return [s.titulo for s in secoes()]


def buscar(pergunta: str, limite: int = 2) -> list[Secao]:
    """As seções que mais combinam com a pergunta (vazia se nenhuma combina)."""
    termos = [_radical(p) for p in _norm(pergunta)]
    if not termos:
        return []
    pontos = []
    for i, s in enumerate(secoes()):
        corpo = {_radical(p) for p in _norm(s.texto)}
        n = sum(3 if t in s.palavras else 1 if t in corpo else 0 for t in termos)
        if n:
            pontos.append((n, -i, s))
    pontos.sort(reverse=True)
    if not pontos:
        return []
    melhor = pontos[0][0]
    return [s for n, _, s in pontos[:limite] if n >= melhor / 2]  # a segunda só entra se for quase tão boa


def trechos_para(pergunta: str) -> str:
    """Texto para o modelo: as seções certas, ou a lista de tópicos se nada combinar."""
    achadas = buscar(pergunta)
    if not achadas:
        return "Nenhuma seção do manual combina com o pedido. Tópicos do manual: " + "; ".join(topicos()) + "."
    return "\n\n".join(f"{s.titulo}: {s.texto}" for s in achadas)


def contexto_de_explicacao(pergunta: str) -> str:
    """O que vai junto com um pedido "explique-me ...": os trechos do manual e como usá-los."""
    return (
        f"{pergunta}\n\n"
        "(Trechos do manual da Eco para este pedido de explicação; se o pedido for sobre a própria Eco, explique com base "
        "neles, do jeito do canal, sem inventar funções que não estão aqui. Se for sobre outra coisa, ignore os trechos.)\n"
        + trechos_para(pergunta)
    )
