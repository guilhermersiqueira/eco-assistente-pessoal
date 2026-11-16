"""Posição física dos nós: ligados se atraem, todos se repelem e cada grupo (espaço) tem uma âncora própria.

Simulação simples com numpy, boa até algumas centenas de nós. Quando a energia cai, o desenho está assentado e a
tela para de animar (não gasta processador parado).
"""

from __future__ import annotations

import math

import numpy as np

ENERGIA_ASSENTADO = 0.02


class Layout:
    def __init__(self, ids: list[str], grupos: dict[str, str], arestas: list[tuple], semente: int = 7) -> None:
        """`arestas`: pares (a, b) ou trios (a, b, peso). Peso maior puxa mais forte e deixa a mola mais curta."""
        self.ids = list(ids)
        self.indice = {id_: i for i, id_ in enumerate(self.ids)}
        n = len(self.ids)
        rng = np.random.default_rng(semente)
        nomes = sorted({grupos.get(i, "") for i in self.ids})
        self.ancoras = {g: self._ancora(k, len(nomes)) for k, g in enumerate(nomes)}
        self.grupo_de = np.array([nomes.index(grupos.get(i, "")) for i in self.ids], dtype=int) if n else np.zeros(0, dtype=int)
        centros = np.array([self.ancoras[g] for g in nomes]) if nomes else np.zeros((0, 2))
        self.pos = (centros[self.grupo_de] + rng.normal(0, 60, (n, 2))) if n else np.zeros((0, 2))
        self.vel = np.zeros((n, 2))
        validas = [x for x in arestas if x[0] in self.indice and x[1] in self.indice]
        self.arestas = np.array([(self.indice[x[0]], self.indice[x[1]]) for x in validas], dtype=int).reshape(-1, 2)
        self.pesos = np.array([float(x[2]) if len(x) > 2 else 0.5 for x in validas]).reshape(-1, 1)
        self.fixo: int | None = None  # nó sendo arrastado
        self.energia = 1.0

    @staticmethod
    def _ancora(k: int, total: int) -> tuple[float, float]:
        if total <= 1:
            return (0.0, 0.0)
        raio = 170 + 45 * total
        ang = 2 * math.pi * k / total
        return (raio * math.cos(ang), raio * math.sin(ang))

    def reaquecer(self) -> None:
        self.energia = 1.0

    @property
    def assentado(self) -> bool:
        return self.energia < ENERGIA_ASSENTADO

    def passo(self, dt: float = 1.0) -> float:
        n = len(self.ids)
        if n == 0:
            self.energia = 0.0
            return 0.0
        d = self.pos[:, None, :] - self.pos[None, :, :]
        dist2 = (d**2).sum(-1) + 0.01
        np.fill_diagonal(dist2, np.inf)
        forca = (d / dist2[..., None] * 5200.0).sum(1)  # repulsão entre todos
        if len(self.arestas):  # mola nas ligações
            a, b = self.arestas[:, 0], self.arestas[:, 1]
            v = self.pos[b] - self.pos[a]
            dist = np.sqrt((v**2).sum(-1, keepdims=True)) + 0.01
            repouso = 140.0 - 70.0 * self.pesos  # peso 0,9 (pai e filho) fica perto; 0,2 fica mais solto
            f = (v / dist) * (dist - repouso) * (0.02 + 0.09 * self.pesos)
            np.add.at(forca, a, f)
            np.add.at(forca, b, -f)
        centros = np.array([self.ancoras[g] for g in sorted(self.ancoras)])
        forca += (centros[self.grupo_de] - self.pos) * 0.035  # puxa para a região do espaço
        self.vel = (self.vel + forca * dt) * 0.82
        limite = 40.0
        self.vel = np.clip(self.vel, -limite, limite)
        if self.fixo is not None:
            self.vel[self.fixo] = 0
        self.pos += self.vel * dt
        self.energia = float((self.vel**2).sum(-1).mean())
        return self.energia

    def assentar(self, passos: int = 400) -> None:
        for _ in range(passos):
            if self.passo() < ENERGIA_ASSENTADO:
                break

    def posicao(self, id_: str) -> tuple[float, float]:
        p = self.pos[self.indice[id_]]
        return float(p[0]), float(p[1])
