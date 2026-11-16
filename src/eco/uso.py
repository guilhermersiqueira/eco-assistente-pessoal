"""Contador de gasto com a API do Claude: custo estimado de cada resposta e total do mês.

Os preços ficam na tabela abaixo (US$ por milhão de tokens) e são uma ESTIMATIVA a conferir no console da
Anthropic: a tabela vale para prompts de até 100 mil tokens e precisa ser atualizada se os preços mudarem.
A transcrição e a voz são cobradas pela OpenAI e não entram aqui.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PRECOS: dict[str, tuple[float, float]] = {  # modelo: (entrada, saída)
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
PRECO_BUSCA = 0.01  # US$ por pesquisa na web (US$ 10 por mil)
# Leitura do cache de prompt, em fração do preço de entrada (documentação da Anthropic, 11/2026): 0,05 no Sonnet 5.5 e
# no Opus 5.5; 0,1 nos demais. A escrita custa 1,25 vez (cache de 5 minutos).
LEITURA_CACHE: dict[str, float] = {"claude-sonnet-5-5": 0.05, "claude-opus-5-5": 0.05}

# Para que serviu cada gasto (campo "categoria" do uso.jsonl). Linhas antigas, sem o campo, contam como "sem categoria".
VOZ, TEXTO = "voz", "texto"  # respostas do agente pelo microfone e pela caixa de texto
GRAFO_ATUALIZACAO = "grafo-atualizacao"  # análise das conversas, notas, metas e memória para o grafo
GRAFO_LIGACAO = "grafo-ligacao"  # ligação automática entre ideias (E6)
GRAFO_ESCRITA = "grafo-escrita"  # conferir repetição e contradição ao guardar uma lembrança (E7)
GRAFO_HIGIENE = "grafo-higiene"  # revisão da memória: duplicatas e agrupamentos (E7)
GRAFO_CATEGORIAS = ("grafo-atualizacao", "grafo-ligacao", "grafo-escrita", "grafo-higiene")
IMPORTACAO = "importacao"  # extração de fatos das conversas exportadas do claude.ai (teto próprio, fora do teto do grafo)
TRANSCRICAO, FALA = "transcricao", "fala"  # OpenAI: microfone para texto e texto para voz
SEM_CATEGORIA = "sem categoria"


@dataclass(frozen=True)
class Uso:
    modelo: str
    entrada: int = 0
    saida: int = 0
    buscas: int = 0
    custo: float = 0.0
    cache_lido: int = 0  # parte da entrada que veio do cache de prompt (cobrada a ~0,1)
    cache_gravado: int = 0  # parte da entrada gravada no cache nesta chamada (cobrada a 1,25)


def calcular_custo(
    modelo: str, entrada: int, saida: int, buscas: int = 0, leitura_cache: int = 0, escrita_cache: int = 0
) -> float:
    preco_in, preco_out = PRECOS.get(modelo, (0.0, 0.0))  # modelo fora da tabela: só as buscas contam
    tokens = entrada + leitura_cache * LEITURA_CACHE.get(modelo, 0.1) + escrita_cache * 1.25
    return (tokens * preco_in + saida * preco_out) / 1_000_000 + buscas * PRECO_BUSCA


def extrair(modelo: str, resposta: Any) -> Uso:
    u = getattr(resposta, "usage", None)
    if u is None:
        return Uso(modelo)
    entrada = int(getattr(u, "input_tokens", 0) or 0)
    saida = int(getattr(u, "output_tokens", 0) or 0)
    buscas = int(getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0)
    leitura = int(getattr(u, "cache_read_input_tokens", 0) or 0)
    escrita = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
    return Uso(modelo, entrada + leitura + escrita, saida, buscas, calcular_custo(modelo, entrada, saida, buscas, leitura, escrita),
               leitura, escrita)


def somar(usos: list[Uso], modelo: str) -> Uso:
    return Uso(
        modelo,
        sum(u.entrada for u in usos),
        sum(u.saida for u in usos),
        sum(u.buscas for u in usos),
        sum(u.custo for u in usos),
        sum(u.cache_lido for u in usos),
        sum(u.cache_gravado for u in usos),
    )


class RegistroUso:
    """Uma linha JSON por chamada à API, em um arquivo local (fora do OneDrive)."""

    def __init__(self, arquivo: Path) -> None:
        self.arquivo = arquivo

    def registrar(self, uso: Uso, agora: datetime | None = None, categoria: str = "") -> None:
        linha = {"quando": (agora or datetime.now()).isoformat(timespec="seconds"), "modelo": uso.modelo,
                 "entrada": uso.entrada, "saida": uso.saida, "buscas": uso.buscas, "custo": round(uso.custo, 6)}
        if categoria:
            linha["categoria"] = categoria
        if uso.cache_lido:
            linha["cache_lido"] = uso.cache_lido
        if uso.cache_gravado:
            linha["cache_gravado"] = uso.cache_gravado
        self._gravar(linha)

    def registrar_audio(self, categoria: str, modelo: str, segundos: float = 0.0, caracteres: int = 0,
                        preco_min: float = 0.0, agora: datetime | None = None) -> None:
        """Uso da OpenAI: segundos de áudio (transcrito ou gerado) e, na fala, os caracteres do texto. O custo estimado
        é minutos × `preco_min` (US$ por minuto); ele entra no total do mês junto com o Claude."""
        linha: dict[str, Any] = {"quando": (agora or datetime.now()).isoformat(timespec="seconds"), "provedor": "openai",
                                 "categoria": categoria, "modelo": modelo, "custo": round(segundos / 60 * preco_min, 6)}
        if segundos:
            linha["segundos"] = round(segundos, 1)
        if caracteres:
            linha["caracteres"] = caracteres
        self._gravar(linha)

    def _gravar(self, linha: dict[str, Any]) -> None:
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        with self.arquivo.open("a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")

    def _linhas_do_mes(self, agora: datetime) -> list[dict[str, Any]]:
        if not self.arquivo.exists():
            return []
        mes = f"{agora:%Y-%m}"
        linhas = []
        for texto in self.arquivo.read_text(encoding="utf-8").splitlines():
            try:
                linha = json.loads(texto)
            except json.JSONDecodeError:
                continue  # linha quebrada não deve derrubar o contador
            if str(linha.get("quando", "")).startswith(mes):
                linhas.append(linha)
        return linhas

    def total_mes(self, agora: datetime | None = None) -> float:
        return sum(float(l.get("custo", 0)) for l in self._linhas_do_mes(agora or datetime.now()))

    def resumo_categorias(self, agora: datetime | None = None) -> dict[str, dict[str, float]]:
        """Por categoria: chamadas, custo e, no caso da OpenAI, segundos e caracteres."""
        resumo: dict[str, dict[str, float]] = {}
        for l in self._linhas_do_mes(agora or datetime.now()):
            r = resumo.setdefault(l.get("categoria") or SEM_CATEGORIA, {"chamadas": 0, "custo": 0.0, "segundos": 0.0, "caracteres": 0})
            r["chamadas"] += 1
            r["custo"] += float(l.get("custo", 0))
            r["segundos"] += float(l.get("segundos", 0))
            r["caracteres"] += int(l.get("caracteres", 0))
        return resumo

    def total_openai_mes(self, agora: datetime | None = None) -> float:
        return sum(float(l.get("custo", 0)) for l in self._linhas_do_mes(agora or datetime.now()) if l.get("provedor") == "openai")

    def resumo_mes(self, agora: datetime | None = None) -> dict[str, tuple[int, float]]:
        """Por modelo do Claude: (número de chamadas, custo). As linhas de áudio da OpenAI ficam de fora."""
        resumo: dict[str, tuple[int, float]] = {}
        for l in self._linhas_do_mes(agora or datetime.now()):
            if l.get("provedor") == "openai":
                continue
            chamadas, custo = resumo.get(l.get("modelo", "?"), (0, 0.0))
            resumo[l.get("modelo", "?")] = (chamadas + 1, custo + float(l.get("custo", 0)))
        return resumo
