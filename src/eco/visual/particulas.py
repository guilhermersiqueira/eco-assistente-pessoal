"""Motor de partículas do orbe da Eco.

Só numpy, sem Qt: o `Sistema` calcula onde cada faísca está e o `Renderizador` pinta tudo num
buffer RGBA. A janela (visual/janela.py) só exibe esse buffer. Foi portado do protótipo
design/orbe-prototipo.html, então os números (raios, velocidades, mola) vêm de lá.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# raio (fração do orbe), giro, brilho, saturação, tremor, deslocamento de tom
ESTADOS: dict[str, dict[str, float]] = {
    "idle": dict(r=0.45, spin=0.25, br=0.35, sat=60, shk=0, hs=0),
    "listen": dict(r=0.72, spin=0.6, br=0.85, sat=85, shk=0, hs=0),
    "think": dict(r=0.6, spin=2.4, br=0.95, sat=90, shk=0, hs=6),
    "speak": dict(r=0.78, spin=1.0, br=1.05, sat=90, shk=0, hs=4),
    "confirm": dict(r=0.68, spin=0.5, br=1.0, sat=90, shk=0, hs=10),
    "error": dict(r=0.58, spin=0.7, br=0.7, sat=80, shk=3, hs=-150),
    "mute": dict(r=0.4, spin=0.12, br=0.25, sat=6, shk=0, hs=0),
}

# tipo, raio, inclinação x, inclinação z, giro, partículas
GRUPOS = [
    ("ring", 0.30, 0.4, 0.2, 1.6, 160),
    ("ring", 0.46, 1.1, 0.5, -1.1, 200),
    ("ring", 0.62, 0.2, 1.2, 0.9, 220),
    ("ring", 0.78, 1.4, -0.3, -0.7, 240),
    ("ring", 0.92, 0.6, 0.9, 0.5, 260),
    ("ring", 1.0, 1.0, -1.0, -0.35, 240),
    ("shell", 0.55, 0.3, 0.1, 0.8, 140),
    ("shell", 0.85, -0.4, 0.6, -0.5, 160),
]


def _rx(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _ry(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rz(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def hsl_to_rgb(h: np.ndarray, s: float | np.ndarray, l: float | np.ndarray) -> np.ndarray:
    """h em graus (qualquer faixa), s e l de 0 a 1. Devolve (..., 3) de 0 a 1."""
    h = np.asarray(h, dtype=np.float64) / 30.0  # 12 setores de 30 graus, como na fórmula do CSS

    def canal(n: float) -> np.ndarray:
        k = (n + h) % 12
        a = s * np.minimum(l, 1 - l)
        return l - a * np.maximum(-1, np.minimum(np.minimum(k - 3, 9 - k), 1))

    return np.stack([canal(0), canal(8), canal(4)], axis=-1)


@dataclass
class _Grupo:
    Rt: np.ndarray
    spin: float
    ang: float


@dataclass
class Quadro:
    x: np.ndarray  # posição em pixels, relativa ao centro do orbe
    y: np.ndarray
    cor: np.ndarray  # (N, 3), já multiplicada pela opacidade
    tam: np.ndarray
    R: float
    br: float
    hue: float
    raios: np.ndarray  # (M, 5): x0, y0, x1, y1, opacidade
    estado: str
    t: float


class Sistema:
    def __init__(self, escala: float = 1.0, semente: int | None = None) -> None:
        self.rng = np.random.default_rng(semente)
        rng = self.rng
        pontos, grupo, matiz = [], [], []
        self.grupos: list[_Grupo] = []
        for gi, (tipo, r, tx, tz, spin, n) in enumerate(GRUPOS):
            n = max(8, int(n * escala))
            rr = r * (1 + (rng.random(n) - 0.5) * (0.06 if tipo == "ring" else 0.1))
            if tipo == "ring":
                f = rng.random(n) * 2 * math.pi
                p = np.stack([rr * np.cos(f), rr * np.sin(f), (rng.random(n) - 0.5) * 0.05], axis=1)
            else:
                u = rng.random(n) * 2 - 1
                a = rng.random(n) * 2 * math.pi
                s = np.sqrt(1 - u * u)
                p = np.stack([rr * s * np.cos(a), rr * s * np.sin(a), rr * u], axis=1)
            pontos.append(p)
            grupo.append(np.full(n, gi))
            # anéis de dentro puxam para o ciano, os de fora para o verde floresta
            matiz.append(rng.random(n) * 14 - 7 + (0.65 - r) * 55)
            self.grupos.append(_Grupo(_rx(tx) @ _rz(tz), spin, rng.random() * 2 * math.pi))
        self.p0 = np.concatenate(pontos)
        self.g = np.concatenate(grupo)
        self.hoff = np.concatenate(matiz)
        n = len(self.p0)
        self.n = n
        self.size = 0.5 + rng.random(n) * 1.4
        self.tw = rng.random(n) * 2 * math.pi
        self.ox = np.zeros(n)
        self.oy = np.zeros(n)
        self.vx = np.zeros(n)
        self.vy = np.zeros(n)
        self.ray_a = rng.random(40) * 2 * math.pi
        self.ray_l = 0.9 + rng.random(40) * 0.3
        self.ray_o = 0.4 + rng.random(40) * 0.6
        self.ray_sp = (rng.random(40) - 0.5) * 1.2
        self.t = 0.0
        self.lv = 0.0
        self.flash = 0.0
        self.gy = 0.0
        self.gx = 0.35
        self.cur = dict(ESTADOS["idle"])

    def acender(self) -> None:
        """Brilho rápido de 'memória guardada'."""
        self.flash = 1.4

    def passo(
        self,
        dt: float,
        estado: str,
        nivel: float,
        lado: int,
        cursor: tuple[float, float] | None = None,
        pressionado: bool = False,
        tom: float = 148.0,
    ) -> Quadro:
        dt = min(max(dt, 0.0), 0.05)
        self.t += dt
        k = min(1.0, dt * 3)
        for canal, alvo in ESTADOS[estado].items():
            self.cur[canal] += (alvo - self.cur[canal]) * k
        self.lv += (float(np.clip(nivel, 0, 1)) - self.lv) * min(1.0, dt * 14)
        self.flash = max(0.0, self.flash - dt * 1.1)

        amp = 0.35 if estado == "listen" else 0.45 if estado == "speak" else 0.0
        pulso = max(0.0, math.sin(self.t * 4)) ** 3 * 0.12 if estado == "confirm" else 0.0
        S = lado * 0.42
        R = S * self.cur["r"] * (1 + amp * self.lv + pulso + self.flash * 0.12)
        br = self.cur["br"] + self.flash * 0.9
        hue = tom + self.cur["hs"]

        self.gy += dt * 0.12 * (self.cur["spin"] + 0.3)
        giro = _ry(self.gy) @ _rx(self.gx)
        Ms = np.empty((len(self.grupos), 3, 3))
        for i, gr in enumerate(self.grupos):
            gr.ang += dt * gr.spin * self.cur["spin"]
            Ms[i] = giro @ gr.Rt @ _rz(gr.ang)
        pos = np.einsum("nij,nj->ni", Ms[self.g], self.p0)
        z = pos[:, 2]
        persp = 1 / (1 - z * 0.22)

        tremor = self.cur["shk"]
        jx = (self.rng.random() - 0.5) * tremor * 2 if tremor > 0.05 else 0.0
        jy = (self.rng.random() - 0.5) * tremor * 2 if tremor > 0.05 else 0.0
        bx = pos[:, 0] * R * persp + jx
        by = pos[:, 1] * R * persp + jy

        if cursor is not None:  # o cursor atrai as faíscas; mais forte com o botão apertado
            dx, dy = cursor[0] - (bx + self.ox), cursor[1] - (by + self.oy)
            d = np.hypot(dx, dy)
            alcance = S * 0.8
            fo = np.where((d < alcance) & (d > 1), 1 - d / alcance, 0.0)
            forca = (2600.0 if pressionado else 900.0) * fo
            ds = np.maximum(d, 1.0)
            self.vx += dx / ds * forca * dt
            self.vy += dy / ds * forca * dt
        if estado == "speak" and self.lv > 0.6:  # faíscas lançadas para fora nos picos da fala
            sorteio = self.rng.random(self.n) < 0.004
            if sorteio.any():
                rl = np.maximum(np.hypot(bx, by), 1.0)
                self.vx += np.where(sorteio, bx / rl * 320, 0.0)
                self.vy += np.where(sorteio, by / rl * 320, 0.0)
        self.vx -= self.ox * 14 * dt  # mola que traz a faísca de volta
        self.vy -= self.oy * 14 * dt
        amortecimento = math.exp(-5 * dt)
        self.vx *= amortecimento
        self.vy *= amortecimento
        self.ox += self.vx * dt
        self.oy += self.vy * dt

        prof = (z + 1) / 2
        brilho_pisca = 0.75 + 0.25 * np.sin(self.t * 3 + self.tw)
        alfa = np.clip((0.18 + 0.62 * prof) * br * brilho_pisca, 0, 1)
        cor = hsl_to_rgb(hue + self.hoff, self.cur["sat"] / 100, (44 + prof * 24) / 100) * alfa[:, None]
        tam = self.size * (0.6 + 1.1 * prof)

        an = self.ray_a + self.gy * self.ray_sp
        c, s = np.cos(an), np.sin(an)
        raios = np.stack(
            [
                c * R * 0.35 + jx,
                s * R * 0.35 + jy,
                c * R * self.ray_l + jx,
                s * R * self.ray_l + jy,
                np.clip(0.10 * br * self.ray_o, 0, 1),
            ],
            axis=1,
        )
        return Quadro(bx + self.ox, by + self.oy, cor.astype(np.float32), tam, R, br, hue, raios, estado, self.t)


class Renderizador:
    """Pinta as faíscas num buffer com brilho aditivo e rastro, como o canvas do protótipo."""

    def __init__(self, lado: int, decaimento: float = 0.76, peso_faiscas: float = 1.0) -> None:
        self.peso_faiscas = peso_faiscas  # < 1 quando as faíscas também são desenhadas nítidas por cima
        self.lado = lado
        self.dec = decaimento
        self.buf = np.zeros((3, lado, lado), np.float32)  # planar: cada canal contíguo, bem mais rápido
        self.tmp = np.empty_like(self.buf)
        self.saida = np.zeros((lado, lado, 4), np.uint8)
        yy, xx = np.mgrid[0:lado, 0:lado].astype(np.float32)
        c = lado / 2
        self.rr = np.hypot(xx + 0.5 - c, yy + 0.5 - c)

    def quadro(self, q: Quadro, dt: float = 1 / 60) -> np.ndarray:
        """Devolve (lado, lado, 4) uint8 em RGBA com alfa pré-multiplicado (buffer reutilizado).

        O rastro some na mesma velocidade em qualquer taxa de quadros (dec é por 1/60 s)."""
        L = self.lado
        c = L // 2
        self.buf *= self.dec ** (min(max(dt, 0.004), 0.05) * 60)

        # brilho do núcleo, só na caixa onde ele existe
        raio = max(q.R * 0.6, 1.0)
        m = min(c, int(raio) + 2)
        sub = self.rr[c - m : c + m, c - m : c + m]
        brilho = np.clip(1 - sub / raio, 0, 1) * np.float32(0.22 * q.br)
        cor_nucleo = hsl_to_rgb(np.array([q.hue + 10.0]), 0.95, 0.62)[0].astype(np.float32)
        for ch in range(3):
            self.buf[ch, c - m : c + m, c - m : c + m] += brilho * cor_nucleo[ch]

        # Cada faísca vira uma mancha gaussiana; em resoluções maiores a mancha cresce junto,
        # para as faíscas manterem a mesma proporção em relação ao orbe.
        xs, ys = q.x + L / 2, q.y + L / 2
        escala = L / 320
        sigma = min(0.5 * escala, 0.95)  # kernel 3x3 fixo: barato, e o resto do tamanho vem da ampliação
        r = 1
        ix, iy = np.floor(xs).astype(np.int64), np.floor(ys).astype(np.int64)
        energia = np.clip(q.tam**2, 0.05, 4.0) * escala**0.7 / (2 * np.pi * sigma**2)
        idxs, pesos, cores = [], [], []
        for ddy in range(-r, r + 1):
            for ddx in range(-r, r + 1):
                jx, jy = ix + ddx, iy + ddy
                d2 = (jx + 0.5 - xs) ** 2 + (jy + 0.5 - ys) ** 2
                w = np.exp(-d2 / (2 * sigma**2)) * energia * self.peso_faiscas
                ok = (jx >= 0) & (jx < L) & (jy >= 0) & (jy < L) & (w > 1e-3)
                idxs.append((jy * L + jx)[ok])
                pesos.append(w[ok])
                cores.append(q.cor[ok])
        idx, w, cor = np.concatenate(idxs), np.concatenate(pesos), np.concatenate(cores)
        for ch in range(3):
            soma = np.bincount(idx, weights=w * cor[:, ch], minlength=L * L)
            self.buf[ch] += soma.reshape(L, L)

        np.clip(self.buf, 0, 1, out=self.buf)
        np.multiply(self.buf, 255, out=self.tmp)
        for ch in range(3):
            np.copyto(self.saida[:, :, ch], self.tmp[ch], casting="unsafe")
        # alfa = maior canal: a cor fica pré-multiplicada válida
        a = self.saida[:, :, 3]
        np.maximum(self.saida[:, :, 0], self.saida[:, :, 1], out=a)
        np.maximum(a, self.saida[:, :, 2], out=a)
        return self.saida


class FaiscasNitidas:
    """Faíscas desenhadas na resolução real da tela, por cima do brilho (que pode ser de baixa resolução).

    O brilho e o rastro são borrados por natureza e ficam num buffer pequeno e rápido; as faíscas, não:
    aqui cada uma vira 1 a 4 pixels de verdade, então ficam nítidas mesmo numa tela de 1920 px.
    """

    def __init__(self, lado_px: int) -> None:
        self.n = lado_px
        self.img = np.zeros((lado_px, lado_px, 4), np.uint8)
        self.plano = self.img.reshape(-1)

    def quadro(self, q: Quadro, k: float) -> np.ndarray:
        n = self.n
        self.img.fill(0)
        xs, ys = q.x * k + n / 2 - 0.5, q.y * k + n / 2 - 0.5
        ix, iy = np.floor(xs).astype(np.int64), np.floor(ys).astype(np.int64)
        fx, fy = xs - ix, ys - iy
        ganho = np.clip(q.tam * 0.9 * np.sqrt(k), 0.9, 2.6)  # faíscas maiores (e mais perto) brilham mais
        base = np.clip(q.cor * ganho[:, None], 0, 1) * 255.0
        idxs, vals = [], []
        for ddx, ddy, w in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
            jx, jy = ix + ddx, iy + ddy
            ok = (jx >= 0) & (jx < n) & (jy >= 0) & (jy < n)
            v = (base[ok] * (w[ok] ** 0.6)[:, None]).astype(np.uint8)  # ^0.6: o ponto não some entre pixels
            a = v.max(axis=1, keepdims=True)  # alfa = maior canal (cor pré-multiplicada válida)
            vals.append(np.concatenate([v, a], axis=1))
            idxs.append(((jy * n + jx)[ok] * 4)[:, None] + np.arange(4))
        np.maximum.at(self.plano, np.concatenate(idxs).ravel(), np.concatenate(vals).ravel())
        return self.img
