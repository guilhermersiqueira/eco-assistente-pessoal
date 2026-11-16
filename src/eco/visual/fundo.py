"""Partículas de fundo da janela principal (documento de design, seção 8).

Uma camada só, atrás de todas as abas: trocar de aba parece mudar o cenário, não a tela. São partículas e fragmentos
(tracinhos que giram) em profundidade, com parallax leve pelo mouse. Reagem à Eco: ao ouvir, aproximam-se do centro e
aceleram; ao falar, pulsam com o volume da voz; em silêncio, voltam ao movimento lento.

`CampoFundo` é só numpy (como o `Sistema` da orbe em `particulas.py`, de quem reaproveita a conversão de cor); a janela
(`principal.py`) pinta o quadro com `pintar` e pede redesenho só dos retângulos em volta de cada partícula
(`regiao`), para não repintar as abas inteiras a cada quadro.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QRegion

from eco.visual.particulas import hsl_to_rgb

DENSIDADES = {"baixo": 55, "medio": 120, "alto": 220}
NIVEIS = ("baixo", "medio", "alto")
QUADROS_S = 30  # quando a Eco reage (ouvindo, pensando, falando): é quando o movimento aparece
QUADROS_S_CALMO = 15  # em repouso o movimento é lento: 15 bastam (cada quadro custa uns 5 ms de redesenho das abas)
QUADROS_S_SEM_FOCO = 6  # janela aberta mas atrás de outra e a Eco quieta: quase parado
REAGINDO = ("listen", "think", "speak", "confirm")


def quadros_alvo(estado: str, janela_ativa: bool) -> int:
    if estado in REAGINDO:
        return QUADROS_S
    return QUADROS_S_CALMO if janela_ativa else QUADROS_S_SEM_FOCO
FRACAO_FPS_MINIMA = 0.75  # abaixo de 75% da taxa alvo (a regra dos ~45 de 60 do documento), cai um nível
ATRACAO = {"listen": 1.0, "think": 0.5, "confirm": 0.3}  # quanto se aproximam do centro
VELOCIDADE = {"listen": 3.0, "think": 2.0, "speak": 1.4, "confirm": 1.4, "mute": 0.5}
BRILHO = {"mute": 0.45, "error": 0.85, "listen": 1.25, "speak": 1.1}


@dataclass
class QuadroFundo:
    x: np.ndarray  # -1 a 1 (centro = 0), já com parallax e atração
    y: np.ndarray
    alfa: np.ndarray  # 0 a 1
    tam: np.ndarray  # raio em pixels
    fragmento: np.ndarray  # bool: tracinho em vez de ponto
    angulo: np.ndarray  # dos fragmentos
    rgb: np.ndarray  # (n, 3) de 0 a 255


class CampoFundo:
    def __init__(self, densidade: str = "medio", tom: float = 148.0, semente: int | None = None) -> None:
        self.densidade = densidade if densidade in DENSIDADES else "medio"
        rng = np.random.default_rng(semente)
        n = DENSIDADES["alto"]  # sorteia o máximo; `ativas` diz quantas aparecem (trocar de nível não embaralha)
        self.pos = rng.random((n, 2)) * 2.2 - 1.1
        self.z = 0.12 + rng.random(n) ** 1.6 * 0.88  # 1 = perto (maior, mais clara, anda mais: parallax)
        ang = rng.random(n) * 2 * math.pi
        self.vel = np.stack([np.cos(ang), np.sin(ang)], axis=1) * (0.008 + 0.018 * rng.random(n))[:, None]
        self.fragmento = rng.random(n) < 0.14
        self.ang_frag = rng.random(n) * 2 * math.pi
        self.giro_frag = (rng.random(n) - 0.5) * 0.8
        self.pisca = rng.random(n) * 2 * math.pi
        matiz = tom + rng.normal(0, 9, n)
        self.rgb = (hsl_to_rgb(matiz, 0.5, 0.42 + 0.26 * self.z) * 255).astype(np.float32)
        self.t = 0.0
        self.atracao = 0.0
        self.vmul = 1.0
        self.brilho = 1.0
        self.voz = 0.0

    @property
    def ativas(self) -> int:
        return DENSIDADES[self.densidade]

    def reduzir(self) -> bool:
        """Desce um nível de densidade. False se já está no mínimo."""
        i = NIVEIS.index(self.densidade)
        if i == 0:
            return False
        self.densidade = NIVEIS[i - 1]
        return True

    def passo(self, dt: float, estado: str = "idle", nivel: float = 0.0, cursor: tuple[float, float] = (0.0, 0.0)) -> QuadroFundo:
        dt = min(max(dt, 0.0), 0.1)
        self.t += dt
        k = 1 - math.exp(-3 * dt)
        self.atracao += (ATRACAO.get(estado, 0.0) - self.atracao) * k
        self.vmul += (VELOCIDADE.get(estado, 1.0) - self.vmul) * k
        self.brilho += (BRILHO.get(estado, 1.0) - self.brilho) * k
        alvo_voz = float(np.clip(nivel, 0, 1)) if estado == "speak" else 0.0
        self.voz += (alvo_voz - self.voz) * min(1.0, dt * 12)

        n = self.ativas
        pos, z = self.pos[:n], self.z[:n]
        pos += self.vel[:n] * (dt * self.vmul) * (0.4 + z)[:, None]
        if self.atracao > 0.02:  # ao ouvir, o campo inteiro desce devagar para o centro (e reaparece na borda)
            pos -= pos * (0.10 * self.atracao * dt) * (0.4 + 0.6 * z)[:, None]
        np.copyto(pos, ((pos + 1.1) % 2.2) - 1.1)
        fora = np.abs(pos).max(axis=1) < 0.04  # quem chegou ao centro volta pela borda
        if fora.any():
            pos[fora] = np.sign(pos[fora] + 1e-6) * 1.08
        self.ang_frag[:n] += self.giro_frag[:n] * dt * self.vmul

        aperto = 1 - 0.25 * self.atracao * (0.4 + 0.6 * z)
        cx, cy = cursor
        x = pos[:, 0] * aperto - cx * 0.035 * z
        y = pos[:, 1] * aperto - cy * 0.035 * z
        pisca = 0.82 + 0.18 * np.sin(self.t * 1.3 + self.pisca[:n])
        alfa = np.clip((0.10 + 0.55 * z) * self.brilho * pisca * (1 + 0.9 * self.voz), 0, 1)
        tam = (0.7 + 2.0 * z) * (1 + 0.55 * self.voz)
        return QuadroFundo(x, y, alfa, tam, self.fragmento[:n], self.ang_frag[:n], self.rgb[:n])


def _na_tela(q: QuadroFundo, area: QRect) -> tuple[np.ndarray, np.ndarray]:
    return area.center().x() + q.x * (area.width() / 2), area.center().y() + q.y * (area.height() / 2)


def regiao(q: QuadroFundo, area: QRect) -> list[QRect]:
    """Retângulos que as partículas ocupam neste quadro (o que a janela precisa redesenhar)."""
    xs, ys = _na_tela(q, area)
    r = np.where(q.fragmento, q.tam * 4 + 2, q.tam * 3 + 2)  # halo e comprimento do tracinho
    return [QRect(int(x - m), int(y - m), int(2 * m) + 2, int(2 * m) + 2) for x, y, m in zip(xs.tolist(), ys.tolist(), r.tolist())]


def juntar(retangulos: list[QRect]) -> QRegion:
    reg = QRegion()
    for r in retangulos:
        reg = reg.united(r)
    return reg


def pintar(p: QPainter, q: QuadroFundo, area: QRect, opacidade: float = 1.0) -> None:
    """Desenha o quadro dentro de `area` (o corpo da janela, abaixo do cabeçalho)."""
    xs, ys = _na_tela(q, area)
    p.save()
    p.setClipRect(area, Qt.ClipOperation.IntersectClip)  # mantém o recorte que a janela já tiver (cantos)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for x, y, a, t, frag, ang, (r, g, b) in zip(
        xs.tolist(), ys.tolist(), q.alfa.tolist(), q.tam.tolist(), q.fragmento.tolist(), q.angulo.tolist(), q.rgb.tolist()
    ):
        a *= opacidade
        if a < 0.02:
            continue
        cor = QColor(int(r), int(g), int(b), int(255 * a))
        if frag:  # fragmento: tracinho que gira devagar
            dx, dy = math.cos(ang) * t * 3.2, math.sin(ang) * t * 3.2
            p.setPen(QPen(cor, max(1.0, t * 0.55), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QLineF(x - dx, y - dy, x + dx, y + dy))
            continue
        p.setPen(Qt.PenStyle.NoPen)
        if t > 1.9:  # as de perto ganham um halo
            halo = QColor(cor)
            halo.setAlpha(int(255 * a * 0.18))
            p.setBrush(halo)
            p.drawEllipse(QPointF(x, y), t * 2.6, t * 2.6)
        p.setBrush(cor)
        p.drawEllipse(QRectF(x - t / 2, y - t / 2, t, t))
    p.restore()
