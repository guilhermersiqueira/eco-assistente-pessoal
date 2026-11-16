"""Posições 3D das ideias na orbe do grafo: a hierarquia vira camadas de uma esfera.

Troncos ficam perto do núcleo, galhos numa camada do meio e folhas na casca externa. Cada filho nasce na direção do
pai (o galho "sai" do tronco), e quem não tem pai fica na região do seu grupo (espaço). Depois uma relaxação curta
afasta quem ficou colado na mesma camada. Só numpy, sem Qt; tudo é determinístico pelo id da ideia (recarregar não
embaralha a orbe), e `sal` muda o sorteio quando o dono pede "Reorganizar".
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import numpy as np

CAMADAS = {"tronco": 0.30, "galho": 0.64, "folha": 1.0}
ABERTURA = {"galho": 0.42, "folha": 0.30}  # quanto o filho pode se afastar da direção do pai (radianos, aprox.)
ABERTURA_GRUPO = 0.55  # idem, para quem não tem pai e fica perto do seu grupo
DISTANCIA_MINIMA = 0.15  # distância mínima (fração do raio da orbe) entre ideias da mesma camada


@dataclass(frozen=True)
class Item:
    id: str
    nivel: str  # tronco | galho | folha
    pai: str | None
    grupo: str


def _hash(texto: str, sal: int) -> np.ndarray:
    """Três números estáveis em [-1, 1) a partir do texto."""
    d = hashlib.md5(f"{sal}:{texto}".encode()).digest()
    return np.array([int.from_bytes(d[i : i + 4], "little") / 2**31 - 1.0 for i in (0, 4, 8)])


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-9)


def fibonacci(n: int) -> np.ndarray:
    """n direções bem espalhadas na esfera (espiral de Fibonacci)."""
    if n <= 0:
        return np.zeros((0, 3))
    if n == 1:
        return np.array([[0.0, -0.35, 0.94]])  # sozinho: um pouco acima e virado para a frente
    i = np.arange(n) + 0.5
    y = 1 - 2 * i / n
    r = np.sqrt(1 - y * y)
    phi = i * math.pi * (3 - math.sqrt(5))
    return np.stack([r * np.cos(phi), y, r * np.sin(phi)], axis=1)


def _perto(direcao: np.ndarray, ruido: np.ndarray, abertura: float) -> np.ndarray:
    """Direção perto de `direcao`, desviada por `ruido` (só a parte tangente) até ~`abertura`."""
    tangente = ruido - direcao * float(np.dot(ruido, direcao))
    return _unit(direcao + tangente * abertura)


_GUARDADAS: dict[tuple, dict[str, np.ndarray]] = {}


def posicoes(itens: list[Item], sal: int = 0) -> dict[str, np.ndarray]:
    """Posição 3D (x, y, z; raio até 1) de cada ideia.

    O resultado só depende das ideias (id, nível, pai, grupo) e do `sal`, então fica guardado: recarregar a aba sem
    mudar a hierarquia não recalcula (num grafo de ~300 ideias a relaxação leva meio segundo)."""
    chave = (sal, tuple(sorted(itens, key=lambda i: i.id)))
    if chave in _GUARDADAS:
        return {k: v.copy() for k, v in _GUARDADAS[chave].items()}
    saida = _calcular(itens, sal)
    if len(_GUARDADAS) >= 4:
        _GUARDADAS.pop(next(iter(_GUARDADAS)))
    _GUARDADAS[chave] = saida
    return {k: v.copy() for k, v in saida.items()}


def _calcular(itens: list[Item], sal: int) -> dict[str, np.ndarray]:
    if not itens:
        return {}
    por_id = {i.id: i for i in itens}
    grupos = sorted({i.grupo for i in itens})
    dir_grupo = dict(zip(grupos, fibonacci(len(grupos))))
    troncos = sorted((i for i in itens if i.nivel == "tronco"), key=lambda i: (i.grupo, i.id))
    direcao: dict[str, np.ndarray] = {}
    # troncos: espalhados, mas cada um puxado para o lado do seu grupo
    for i, base in zip(troncos, fibonacci(len(troncos))):
        direcao[i.id] = _unit(base * 0.6 + dir_grupo[i.grupo] * 0.4 + _hash(i.id, sal) * 0.05)

    def resolver(i: Item, caminho: set[str]) -> np.ndarray:
        if i.id in direcao:
            return direcao[i.id]
        ruido = _hash(i.id, sal)
        pai = por_id.get(i.pai) if i.pai else None
        if pai is not None and pai.id not in caminho:
            d = _perto(resolver(pai, caminho | {i.id}), ruido, ABERTURA.get(i.nivel, 0.3))
        else:
            d = _perto(dir_grupo[i.grupo], ruido, ABERTURA_GRUPO)
        direcao[i.id] = d
        return d

    for i in itens:
        resolver(i, set())

    saida: dict[str, np.ndarray] = {}
    for nivel, raio in CAMADAS.items():
        ids = [i.id for i in itens if i.nivel == nivel]
        if not ids:
            continue
        ancora = np.array([direcao[x] for x in ids])
        v = _relaxar(ancora.copy(), ancora, min(1.2, DISTANCIA_MINIMA / raio))
        for x, d in zip(ids, v):
            saida[x] = d * raio
    for i in itens:  # nível desconhecido: casca de fora
        if i.id not in saida:
            saida[i.id] = direcao[i.id] * CAMADAS["folha"]
    return saida


def _relaxar(v: np.ndarray, ancora: np.ndarray, separacao: float, voltas: int = 60) -> np.ndarray:
    """Afasta pontos mais próximos que `separacao` (ângulo), sem deixar cada um fugir muito de onde nasceu.

    O empurrão é proporcional ao que falta de distância; para quando o que falta é pequeno. Camada lotada aceita
    ficar mais perto (não cabe todo mundo na distância ideal)."""
    if len(v) < 2:
        return v
    separacao = min(separacao, 0.75 * math.sqrt(4 * math.pi / len(v)))
    corda = 2 * math.sin(separacao / 2)  # distância mínima entre pontos da esfera de raio 1
    v, ancora = v.astype(np.float32), ancora.astype(np.float32)
    for _ in range(voltas):
        dif = v[:, None, :] - v[None, :, :]
        d = np.linalg.norm(dif, axis=-1)
        np.fill_diagonal(d, np.inf)
        falta = np.clip(corda - d, 0, None)
        if falta.max() < 0.12 * corda:
            break
        empurrao = (dif / np.maximum(d, 1e-6)[..., None] * falta[..., None]).sum(axis=1)
        v = _unit(v + 0.5 * empurrao + 0.01 * (ancora - v))
    return v.astype(np.float64)


# --- projeção ----------------------------------------------------------------------------------------------------------
def rotacao(guinada: float, arfagem: float) -> np.ndarray:
    """Matriz que gira a orbe: `guinada` em torno do eixo vertical, `arfagem` em torno do horizontal."""
    cy, sy = math.cos(guinada), math.sin(guinada)
    cx, sx = math.cos(arfagem), math.sin(arfagem)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    return rx @ ry


FOCO = 3.2  # distância da câmera (em raios da orbe): perspectiva leve


def projetar(p: np.ndarray, R: np.ndarray, raio_px: float, cx: float, cy: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(N, 3) -> x e y na tela, profundidade z (-1 fundo, +1 frente) e fator de perspectiva."""
    if len(p) == 0:
        vazio = np.zeros(0)
        return vazio, vazio, vazio, vazio
    q = p @ R.T
    persp = FOCO / (FOCO - q[:, 2])
    return cx + q[:, 0] * raio_px * persp, cy + q[:, 1] * raio_px * persp, q[:, 2], persp
