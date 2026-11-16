"""As lembranças da memória (`memoria.md`) como nós do grafo.

O arquivo é a fonte da verdade: cada linha vira um nó (origem `fato`, o resumo guarda o texto inteiro). `sincronizar`
cria os nós que faltam e remove os de linhas que sumiram; `ServicoMemoria` edita, fixa, apaga e adiciona pelo nó,
gravando no arquivo e no grafo juntos. Os títulos curtos e as ligações entre lembranças vêm da análise (`analise.py`).
"""

from __future__ import annotations

from pathlib import Path

from eco.ferramentas import memoria
from eco.grafo.dados import Grafo, Ideia

ORIGEM = "fato"
TAMANHO_TITULO = 42


def titulo_provisorio(texto: str) -> str:
    """Título enquanto a análise não deu um melhor: o começo do texto."""
    t = " ".join(texto.split())
    return t if len(t) <= TAMANHO_TITULO else t[: TAMANHO_TITULO - 1].rstrip() + "…"


def sincronizar(grafo: Grafo, pasta: Path) -> tuple[int, int]:
    """Deixa os nós iguais ao arquivo. Devolve (nós criados, nós removidos). Também apaga os nós antigos da origem
    `memoria`, que eram ideias abstratas tiradas do texto da memória e foram trocadas pelas próprias lembranças."""
    for antigo in grafo.ideias_da_origem("memoria"):
        grafo.remover(antigo.id)
    por_texto: dict[str, list[Ideia]] = {}
    for i in grafo.ideias_da_origem(ORIGEM):
        por_texto.setdefault(i.resumo, []).append(i)
    criados = 0
    mantidos: set[str] = set()
    for f in memoria.fatos(pasta):
        existentes = por_texto.get(f.texto)
        if existentes:
            no = existentes.pop(0)
            if (no.topico == "fixo") != f.fixo:
                grafo.atualizar_ideia(no.id, topico="fixo" if f.fixo else "")
        else:
            no = grafo.criar_fato(f.texto, titulo_provisorio(f.texto), f.fixo)
            criados += 1
        mantidos.add(no.id)
    removidos = 0
    for lista in por_texto.values():
        for no in lista:
            if no.id not in mantidos:
                grafo.remover(no.id)
                removidos += 1
    return criados, removidos


class ServicoMemoria:
    """Operações do dono sobre uma lembrança, feitas pelo nó do grafo. Desde a D2 (12/11/2026) a memória mora no banco
    (`eco.grafo.memorias.Memorias`) e o memoria.md é a cópia regravada a cada mudança."""

    def __init__(self, grafo: Grafo, pasta: Path) -> None:
        from eco.grafo.memorias import Memorias

        self.grafo, self.pasta = grafo, pasta
        self.memorias = Memorias(grafo, pasta)

    def editar(self, ideia_id: str, novo_texto: str) -> None:
        self.memorias.editar(ideia_id, novo_texto)

    def fixar(self, ideia_id: str, fixo: bool) -> None:
        self.memorias.fixar(ideia_id, fixo)

    def apagar(self, ideia_id: str) -> None:
        self.memorias.apagar(ideia_id)

    def adicionar(self, texto: str) -> Ideia:
        return self.memorias.adicionar(texto)
