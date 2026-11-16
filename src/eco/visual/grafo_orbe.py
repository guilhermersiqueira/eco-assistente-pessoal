"""Grafo como orbe 3D: as ideias formam uma esfera que gira, como a própria Eco.

A hierarquia vira camadas (`grafo/esfera.py`): troncos perto do núcleo, galhos no meio, folhas na casca. Arrastar gira
a orbe (com inércia), a roda dá zoom e as setas do teclado também giram. Parada, ela gira devagar sozinha; para de
girar quando o mouse está numa ideia ou há uma selecionada, para o cartão não fugir.

Tudo o que não é desenho (cartão, menu do botão direito, ligar, pôr dentro, apagar, lembranças) vem de `VistaGrafo`:
esta classe só troca a geometria (`_ponto`, `_raio_tela`, `_no_em`, `_ligacao_em`), o posicionamento e a pintura.

Documento de design, seção 7: fitas de luz em órbitas, feixes finos saindo do centro e fragmentos em suspensão; nó
redondo com tamanho pelo nível e brilho pela importância; arestas curvas, fortes em `VERDE_LUZ`, fracas quase
transparentes; seleção escurece o resto para ~30%; uma região suave de fundo por espaço; arrastar com inércia;
"Reorganizar" anima a transição; três níveis de qualidade (médio é o padrão), que caem sozinhos se a tela ficar lenta
(seção 9: abaixo de ~45 quadros por segundo).
"""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient

from eco.chat.armazenamento import Banco
from eco.config import Config
from eco.grafo import esfera
from eco.grafo.dados import Grafo
from eco.visual import tema
from eco.visual.componentes import Cartao, _movimento_reduzido
from eco.visual.grafo import No, VistaGrafo

QUALIDADES = ("baixo", "medio", "alto")
FRAGMENTOS = {"baixo": 0, "medio": 70, "alto": 160}
FRACAO_FPS_MINIMA = 0.75  # abaixo de 75% da taxa pedida (45 de 60, como no documento) a qualidade cai um nível
INTERVALO_RAPIDO_MS = 16  # ~60 quadros/s enquanto alguém mexe
INTERVALO_CALMO_MS = 33  # ~30 quadros/s girando sozinha
GIRO_SOZINHO = 0.07  # rad/s quando ninguém mexe
SENSIBILIDADE = 0.0075  # rad por pixel arrastado
CURVA = 0.6  # o ponto de controle da aresta fica no meio do caminho, puxado para o núcleo (arco por dentro da orbe)
CURVA_HIERARQUIA = 0.94  # pai e filho: quase reto, o filho "brota" do pai
TAMANHO = {"tronco": 12.5, "galho": 8.5, "folha": 5.5}
ORBITAS = ((0.30, 0.35, 0.10), (0.64, 1.05, 0.45), (1.0, 0.20, 1.15))  # raio, inclinação x, inclinação z


def _matriz_orbita(tx: float, tz: float) -> np.ndarray:
    cx, sx, cz, sz = math.cos(tx), math.sin(tx), math.cos(tz), math.sin(tz)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rx @ rz


class VistaOrbe(VistaGrafo):
    def __init__(self, banco: Banco, grafo: Grafo, cfg: Config) -> None:
        super().__init__(banco, grafo, cfg)
        self.reduzido = _movimento_reduzido()
        self.qualidade = cfg.grafo_qualidade if cfg.grafo_qualidade in QUALIDADES else "medio"
        self.qualidade_caiu = False
        self.guinada, self.arfagem = 0.6, -0.3
        self.vg = self.va = 0.0  # inércia (rad/s)
        self.zoom = 1.0
        self.alvo: dict[str, np.ndarray] = {}
        self.atual: dict[str, np.ndarray] = {}
        self._sal = 0
        self._giro: QPointF | None = None
        self._t_giro = 0.0
        self._ultimo = time.perf_counter()
        self._janela_fps: tuple[float, int] = (self._ultimo, 0)
        self._t = 0.0
        self._imp_max = 1.0
        self._versao_pos = 0
        self._chave_cena: tuple | None = None
        self._imagem_cena: QPixmap | None = None
        rng = np.random.default_rng(11)
        u, a = rng.random(240) * 2 - 1, rng.random(240) * 2 * math.pi
        raio = 0.15 + rng.random(240) ** 0.7 * 1.15
        self._fragmentos = np.stack([np.sqrt(1 - u * u) * np.cos(a), u, np.sqrt(1 - u * u) * np.sin(a)], axis=1) * raio[:, None]
        self._fragmentos_tam = rng.random(240)
        dirs = esfera.fibonacci(28) + rng.normal(0, 0.12, (28, 3))
        self._feixes = dirs / np.linalg.norm(dirs, axis=1, keepdims=True)
        self._feixes_comp = 0.75 + rng.random(28) * 0.5
        ang = np.linspace(0, 2 * math.pi, 73)
        self._circulo = np.stack([np.cos(ang), np.sin(ang), np.zeros_like(ang)], axis=1)
        self._orbitas = [(r, _matriz_orbita(tx, tz)) for r, tx, tz in ORBITAS]
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        # opaca: pinta ela mesma a cor do cartão em volta, e o Qt não precisa repintar o cartão e a janela atrás dela a
        # cada quadro (isso custava mais que a orbe inteira)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._projetar()

    # --- posições ----------------------------------------------------------------------------------------------
    def _posicionar(self, nos: dict[str, No], reorganizar: bool) -> None:
        if reorganizar:
            self._sal += 1  # outro sorteio: a orbe se rearruma (animado)
        self.nos = nos
        itens = [esfera.Item(k, n.nivel, n.ideia.pai if n.ideia.pai in nos else None, n.grupo) for k, n in nos.items()]
        self.alvo = esfera.posicoes(itens, self._sal)
        # ideia nova nasce no núcleo e sai para a camada dela; as que já estavam continuam de onde estavam
        self.atual = {k: (self.atual[k].copy() if k in self.atual else (self.alvo[k] if self.reduzido else self.alvo[k] * 0.08)) for k in nos}
        if self.reduzido:
            self.assentar()
        self._imp_max = max([n.importancia for n in nos.values()] + [1.0])
        self._versao_pos += 1
        self._projetar()
        self._acordar()

    def assentar(self) -> None:
        """Pula a animação: cada ideia vai direto para a camada dela."""
        self.atual = {k: v.copy() for k, v in self.alvo.items()}
        self._versao_pos += 1
        self._projetar()

    def ajustar(self) -> None:
        self.zoom = 1.0
        self._projetar()
        self.update()

    def resizeEvent(self, e) -> None:
        super(VistaGrafo, self).resizeEvent(e)  # pula o ajuste da vista 2D (que zeraria o zoom)
        self._projetar()

    def _projetar(self) -> None:
        """Calcula, para o quadro atual, onde cada ideia e cada aresta aparecem na tela."""
        self._R = esfera.rotacao(self.guinada, self.arfagem)
        self._raio_px = 0.40 * max(60.0, min(self.width(), self.height())) * self.zoom
        self._cx, self._cy = self.width() / 2, self.height() / 2 + 8
        ids = [k for k in self.atual if k in self.nos]
        self._ids, self._idx = ids, {k: i for i, k in enumerate(ids)}
        P = np.array([self.atual[k] for k in ids]).reshape(-1, 3)
        self._sx, self._sy, self._sz, self._sp = esfera.projetar(P, self._R, self._raio_px, self._cx, self._cy)
        escala = min(2.0, max(0.7, self.zoom)) ** 0.6
        self._sr = np.array(
            [(TAMANHO.get(self.nos[k].nivel, 5.5) + 2.5 * min(1.0, math.sqrt(self.nos[k].importancia) / 4)) for k in ids]
        ).reshape(-1) * self._sp * escala
        # arestas: ponta a, controle (meio puxado para o núcleo) e ponta b
        validas = [l for l in self.ligacoes if l.a in self._idx and l.b in self._idx]
        self._arestas = validas
        if validas:
            ia = np.array([self._idx[l.a] for l in validas])
            ib = np.array([self._idx[l.b] for l in validas])
            curva = np.array([CURVA_HIERARQUIA if l.hierarquia else CURVA for l in validas])[:, None]
            C = (P[ia] + P[ib]) / 2 * curva
            self._cx_, self._cy_, _, _ = esfera.projetar(C, self._R, self._raio_px, self._cx, self._cy)
            self._ea, self._eb = ia, ib
        else:
            self._ea = self._eb = np.zeros(0, dtype=int)
            self._cx_ = self._cy_ = np.zeros(0)

    def _ponto(self, id_: str) -> QPointF:
        i = self._idx.get(id_)
        if i is None or i >= len(self._sx):
            return QPointF(self._cx, self._cy)
        return QPointF(float(self._sx[i]), float(self._sy[i]))

    def _raio_tela(self, id_: str) -> float:
        i = self._idx.get(id_)
        return float(self._sr[i]) if i is not None and i < len(self._sr) else 6.0

    def _no_em(self, p: QPointF) -> str | None:
        if not len(self._ids):
            return None
        d = np.hypot(self._sx - p.x(), self._sy - p.y())
        ok = np.nonzero(d <= self._sr + 5)[0]
        if not len(ok):
            return None
        return self._ids[int(ok[np.argmax(self._sz[ok])])]  # o da frente ganha

    def _ligacao_em(self, p: QPointF):
        if not len(self._ea):
            return None
        t = np.linspace(0, 1, 24)[None, :]
        ax, ay, bx, by = self._sx[self._ea][:, None], self._sy[self._ea][:, None], self._sx[self._eb][:, None], self._sy[self._eb][:, None]
        cx, cy = self._cx_[:, None], self._cy_[:, None]
        x = (1 - t) ** 2 * ax + 2 * (1 - t) * t * cx + t**2 * bx
        y = (1 - t) ** 2 * ay + 2 * (1 - t) * t * cy + t**2 * by
        d = np.hypot(x - p.x(), y - p.y()).min(axis=1)
        i = int(np.argmin(d))
        return self._arestas[i] if d[i] <= 6 else None

    # --- animação ----------------------------------------------------------------------------------------------
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._ultimo = time.perf_counter()
        self._janela_fps = (self._ultimo, 0)
        self._acordar()

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def _parada(self) -> bool:
        """Ninguém mexendo: pode girar sozinha."""
        return (
            self.hover is None and self.selecionado is None and self.modo_ligar is None and self.modo_pai is None
            and not self.sobre_cartao and self._giro is None
        )

    def _tick(self) -> None:
        agora = time.perf_counter()
        dt = min(0.05, max(0.0, agora - self._ultimo))
        self._ultimo = agora
        self._t += dt
        self._medir(agora)
        k = 1 - math.exp(-6 * dt)
        animando = False
        interagindo = self._giro is not None  # arrastando, com inércia ou ideias se mexendo: 60 quadros/s
        for chave, alvo in self.alvo.items():
            atual = self.atual.get(chave)
            if atual is None:
                continue
            delta = alvo - atual
            if float(np.abs(delta).max()) > 1e-4:
                self.atual[chave] = atual + delta * k
                animando = interagindo = True
                self._versao_pos += 1
        if self._giro is None and (abs(self.vg) > 1e-3 or abs(self.va) > 1e-3):
            self.guinada += self.vg * dt
            self.arfagem = max(-1.3, min(1.3, self.arfagem + self.va * dt))
            amortece = math.exp(-2.5 * dt)
            self.vg *= amortece
            self.va *= amortece
            animando = interagindo = True
        elif not self.reduzido and self._parada():
            self.guinada += GIRO_SOZINHO * dt
            animando = True
        self._fase = (self._fase + dt * 1.1) % 1.0
        self._projetar()
        if self.cartao.isVisible() and (self.hover or self.selecionado) and animando:
            self._posicionar_cartao(self.hover or self.selecionado)
        self.update()
        pulsando = (self.hover or self.selecionado) is not None and not self.reduzido
        if not (animando or pulsando or self.modo_ligar or self.modo_pai):
            self._timer.stop()  # parada de verdade (movimento reduzido): não gasta processador
            return
        # girando sozinha, devagar, 30 quadros/s bastam (metade do custo); mexendo, 60
        intervalo = INTERVALO_RAPIDO_MS if (interagindo or pulsando or self._giro is not None) else INTERVALO_CALMO_MS
        if self._timer.interval() != intervalo:
            self._timer.setInterval(intervalo)
            self._janela_fps = (agora, 0)

    def _medir(self, agora: float) -> None:
        """Seção 9 do documento: se a taxa cair abaixo de ~45 quadros/s (75% da pedida), desce um nível de qualidade."""
        inicio, quadros = self._janela_fps
        quadros += 1
        if agora - inicio >= 2.0:
            fps = quadros / (agora - inicio)
            minimo = FRACAO_FPS_MINIMA * 1000 / max(1, self._timer.interval())
            if fps < minimo and self.qualidade != "baixo" and self.isVisible() and not self.reduzido:
                self.qualidade = QUALIDADES[QUALIDADES.index(self.qualidade) - 1]
                self.qualidade_caiu = True
            inicio, quadros = agora, 0
        self._janela_fps = (inicio, quadros)

    # --- mouse e teclado ---------------------------------------------------------------------------------------
    def _acordar(self) -> None:
        self._timer.setInterval(INTERVALO_RAPIDO_MS)
        super()._acordar()

    def mousePressEvent(self, e) -> None:
        self.setFocus()
        if e.button() == Qt.MouseButton.RightButton:
            self._menu(e.position(), e.globalPosition().toPoint())
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._moveu = False
        self._giro = self._inicio = e.position()
        self._t_giro = time.perf_counter()
        self.vg = self.va = 0.0
        self._acordar()

    def mouseMoveEvent(self, e) -> None:
        p = e.position()
        self._cursor = p
        if self._giro is not None:
            if not self._moveu and math.hypot(p.x() - self._inicio.x(), p.y() - self._inicio.y()) > 4:
                self._moveu = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._moveu:
                agora = time.perf_counter()
                dt = max(1e-3, agora - self._t_giro)
                dg, da = (p.x() - self._giro.x()) * SENSIBILIDADE, (p.y() - self._giro.y()) * SENSIBILIDADE
                self.guinada += dg
                self.arfagem = max(-1.3, min(1.3, self.arfagem + da))
                self.vg = 0.6 * self.vg + 0.4 * (dg / dt)  # média curta: a inércia segue o fim do gesto
                self.va = 0.6 * self.va + 0.4 * (da / dt)
                self._giro, self._t_giro = p, agora
                self._projetar()
                self.update()
            return
        super().mouseMoveEvent(e)  # passar o mouse: destaque, cartão e dica das arestas (como no 2D)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._giro = None
        self.unsetCursor()
        if self._moveu:
            if self.reduzido or time.perf_counter() - self._t_giro > 0.12:
                self.vg = self.va = 0.0  # soltou parado: sem inércia
            self._acordar()
            return
        alvo = self._no_em(e.position())
        if alvo is not None:
            self._clique_no(alvo)
        else:
            self.selecionado = None
            if self.modo_ligar is None and self.modo_pai is None:
                self.cartao.hide()
        self._acordar()
        self.update()

    def wheelEvent(self, e) -> None:
        self.zoom = max(0.55, min(3.0, self.zoom * (1.12 if e.angleDelta().y() > 0 else 1 / 1.12)))
        self._projetar()
        self.update()

    def keyPressEvent(self, e) -> None:
        passo = 0.18
        teclas = {
            Qt.Key.Key_Left: (-passo, 0.0), Qt.Key.Key_Right: (passo, 0.0),
            Qt.Key.Key_Up: (0.0, -passo), Qt.Key.Key_Down: (0.0, passo),
        }
        if e.key() in teclas:
            dg, da = teclas[e.key()]
            self.guinada += dg
            self.arfagem = max(-1.3, min(1.3, self.arfagem + da))
        elif e.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom = min(3.0, self.zoom * 1.12)
        elif e.key() == Qt.Key.Key_Minus:
            self.zoom = max(0.55, self.zoom / 1.12)
        else:
            super().keyPressEvent(e)
            return
        self._projetar()
        self.update()

    def recarregar_preservando(self) -> None:
        zoom, g, a = self.zoom, self.guinada, self.arfagem
        self.recarregar()
        self.zoom, self.guinada, self.arfagem = zoom, g, a
        self._projetar()

    # --- pintura -----------------------------------------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self._cor_de_fundo())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        foco = self.hover or self.selecionado
        p.drawPixmap(0, 0, self._cena(foco is not None))
        if not self.nos:
            p.setPen(QColor(tema.TEXTO_2))
            p.setFont(tema.fonte_texto(tema.CORPO))
            p.drawText(self.rect().adjusted(0, int(self._raio_px * 0.5), 0, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       "Ainda não há ideias.\nGuarde conversas e clique em \"Atualizar grafo\" para a Eco extraí-las.")
            p.end()
            return
        perto = (self.vizinhos.get(foco, set()) | {foco}) if foco else set()
        self._pintar_arestas(p, foco)
        self._pintar_nos(p, foco, perto)
        if (self.modo_ligar or self.modo_pai) in self.nos:
            p.setPen(QPen(QColor(tema.VERDE_LUZ), 1.4, Qt.PenStyle.DashLine))
            p.drawLine(self._ponto(self.modo_ligar or self.modo_pai), self._cursor)
        self._pintar_legenda(p)
        if not (self.modo_ligar or self.modo_pai):
            p.setPen(QColor(tema.TEXTO_APAGADO))
            p.setFont(tema.fonte_mono(tema.ROTULO))
            p.drawText(self.rect().adjusted(12, 0, -12, -8), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                       "arraste para girar · roda ou +/− para zoom · setas giram")
        p.end()

    def _cor_de_fundo(self) -> QColor:
        """A cor que o cartão em volta tem neste ponto (cartão quase opaco sobre o fundo da janela)."""
        w = self.parentWidget()
        while w is not None and not isinstance(w, Cartao):
            w = w.parentWidget()
        base = QColor(tema.FUNDO)
        if w is None:
            return base
        cartao = tema.misturar(tema.SUPERFICIE, tema.ELEVADO, w.foco)
        a = tema.OPACIDADE_CARTAO + (1 - tema.OPACIDADE_CARTAO) * w.foco
        return QColor.fromRgbF(*(a * x + (1 - a) * y for x, y in zip(cartao.getRgbF()[:3], base.getRgbF()[:3])))

    def _cena(self, apagar: bool) -> QPixmap:
        """Núcleo, silhueta, órbitas, feixes, fragmentos e regiões numa imagem guardada.

        É a parte cara de rasterizar (linhas longas com antialias e degradês grandes) e muda pouco de um quadro para o
        outro: só é refeita quando a rotação anda o bastante para mexer um pixel, o núcleo respira um passo ou as
        ideias se mexem. Girando sozinha, isso dá uns 15 redesenhos por segundo em vez de 60."""
        passo = 0.004 / max(0.5, self.zoom)
        chave = (
            round(self.guinada / passo), round(self.arfagem / passo), round(self.zoom, 3), self.width(), self.height(),
            self.qualidade, apagar, 0 if self.reduzido else int(self._t * 8), self._versao_pos, len(self._ids),
        )
        if chave == self._chave_cena and self._imagem_cena is not None:
            return self._imagem_cena
        dpr = self.devicePixelRatioF()
        img = QPixmap(max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr)))
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.GlobalColor.transparent)
        q = QPainter(img)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._pintar_nucleo(q)
        if self.nos:
            if self.qualidade != "baixo":
                self._pintar_regioes(q, apagar)
                self._pintar_silhueta(q)
            self._pintar_orbitas(q)
            self._pintar_feixes(q, apagar)
            self._pintar_fragmentos(q)
        q.end()
        self._imagem_cena, self._chave_cena = img, chave
        return img

    def _frente(self, z: np.ndarray | float):
        return (np.asarray(z) + 1) / 2  # 0 no fundo, 1 na frente

    def _pintar_nucleo(self, p: QPainter) -> None:
        """O centro da orbe é a própria Eco: brilho no tom da orbe, respirando devagar."""
        h = self.cfg.orbe_tom / 360
        respira = 1.0 if self.reduzido else 1 + 0.06 * math.sin(self._t * 1.6)
        r = self._raio_px * 0.24 * respira
        c = QPointF(self._cx, self._cy)
        g = QRadialGradient(c, r)
        g.setColorAt(0.0, QColor.fromHslF(h, 0.85, 0.72, 0.55))
        g.setColorAt(0.25, QColor.fromHslF(h, 0.8, 0.55, 0.22))
        g.setColorAt(1.0, QColor.fromHslF(h, 0.8, 0.45, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, r, r)

    def _pintar_regioes(self, p: QPainter, apagar: bool) -> None:
        """Uma mancha suave de fundo por espaço, sem linhas (documento, seção 7)."""
        grupos: dict[str, list[int]] = {}
        for i, k in enumerate(self._ids):
            grupos.setdefault(self.nos[k].grupo, []).append(i)
        if len(grupos) < 2:
            return
        for idx in grupos.values():
            if len(idx) < 2:
                continue
            ix = np.array(idx)
            mx, my = float(self._sx[ix].mean()), float(self._sy[ix].mean())
            espalho = float(np.hypot(self._sx[ix] - mx, self._sy[ix] - my).mean())
            r = max(50.0, espalho * 1.5 + 40)
            cor = QColor(self.nos[self._ids[idx[0]]].cor)
            frente = float(self._frente(self._sz[ix].mean()))
            g = QRadialGradient(QPointF(mx, my), r)
            g.setColorAt(0.0, QColor(cor.red(), cor.green(), cor.blue(), int((10 + 18 * frente) * (0.4 if apagar else 1))))
            g.setColorAt(1.0, QColor(cor.red(), cor.green(), cor.blue(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(QPointF(mx, my), r, r)

    def _pintar_orbitas(self, p: QPainter) -> None:
        """Fitas de luz nas três camadas; a metade da frente é mais clara que a de trás."""
        p.setBrush(Qt.BrushStyle.NoBrush)
        tras = QPen(tema.cor(tema.VERDE_LUZ, 0.06), 1.0)
        frente_caneta = QPen(tema.cor(tema.VERDE_LUZ, 0.17), 1.2)
        fita = QPen(tema.cor(tema.VERDE_LUZ, 0.07), 3.0)
        for raio, M in self._orbitas:
            x, y, z, _ = esfera.projetar((self._circulo * raio) @ M.T, self._R, self._raio_px, self._cx, self._cy)
            pts = [QPointF(a, b) for a, b in zip(x.tolist(), y.tolist())]
            p.setPen(tras)
            p.drawPolyline(QPolygonF(pts))
            # metade da frente: o trecho contínuo com z > 0 (o círculo fecha, então gira o índice até o começo dele)
            na_frente = z[:-1] > 0
            if not na_frente.any():
                continue
            ini = int(np.argmax(na_frente & ~np.roll(na_frente, 1))) if not na_frente.all() else 0
            ordem = [(ini + i) % (len(pts) - 1) for i in range(len(pts) - 1)]
            trecho = [pts[i] for i in ordem if na_frente[i]]
            if len(trecho) > 1:
                p.setPen(frente_caneta)
                p.drawPolyline(QPolygonF(trecho))
                if self.qualidade == "alto":  # fita: um segundo fio, largo e fraco, colado ao primeiro
                    p.setPen(fita)
                    p.drawPolyline(QPolygonF(trecho))

    def _pintar_feixes(self, p: QPainter, apagar: bool) -> None:
        """Feixes finos irradiando do centro: um até cada tronco e alguns decorativos."""
        c = QPointF(self._cx, self._cy)
        for k in self._ids:
            if self.nos[k].nivel == "tronco":
                i = self._idx[k]
                p.setPen(QPen(tema.cor(tema.VERDE_LUZ, (0.10 if apagar else 0.28) * (0.5 + 0.5 * float(self._frente(self._sz[i])))), 1.0))
                p.drawLine(c, QPointF(float(self._sx[i]), float(self._sy[i])))
        if self.qualidade == "baixo":
            return
        x0, y0, _, _ = esfera.projetar(self._feixes * 0.12, self._R, self._raio_px, self._cx, self._cy)
        x1, y1, z1, _ = esfera.projetar(self._feixes * self._feixes_comp[:, None], self._R, self._raio_px, self._cx, self._cy)
        de_frente = z1 > 0
        for grupo, alfa in ((~de_frente, 0.03), (de_frente, 0.07)):
            linhas = [QLineF(a, b, c2, d) for a, b, c2, d in zip(x0[grupo].tolist(), y0[grupo].tolist(), x1[grupo].tolist(), y1[grupo].tolist())]
            if linhas:
                p.setPen(QPen(tema.cor(tema.VERDE_LUZ, alfa), 1.0))
                p.drawLines(linhas)

    def _pintar_silhueta(self, p: QPainter) -> None:
        """Contorno e "atmosfera" da casca externa: é o que faz o conjunto se ler como uma orbe."""
        r = self._raio_px * esfera.FOCO / math.sqrt(esfera.FOCO**2 - 1)
        c = QPointF(self._cx, self._cy)
        g = QRadialGradient(c, r * 1.04)
        g.setColorAt(0.70, tema.cor(tema.VERDE_LUZ, 0.0))
        g.setColorAt(0.96, tema.cor(tema.VERDE_LUZ, 0.045))
        g.setColorAt(1.0, tema.cor(tema.VERDE_LUZ, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, r * 1.04, r * 1.04)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(tema.cor(tema.VERDE_LUZ, 0.10), 1.0))
        p.drawEllipse(c, r, r)

    def _pintar_fragmentos(self, p: QPainter) -> None:
        """Fragmentos em suspensão: pontinhos que giram um pouco mais rápido que a orbe."""
        n = FRAGMENTOS[self.qualidade]
        if not n:
            return
        extra = esfera.rotacao(self.guinada + self._t * 0.04, self.arfagem)
        x, y, z, _ = esfera.projetar(self._fragmentos[:n], extra, self._raio_px, self._cx, self._cy)
        frente = self._frente(z)
        for limite, alfa, largura in ((0.5, 0.18, 1.0), (1.01, 0.42, 1.8)):
            sel = (frente < limite) & (frente >= limite - 0.5)
            if sel.any():
                p.setPen(QPen(tema.cor(tema.TEXTO_2, alfa), largura, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawPoints(QPolygonF([QPointF(float(a), float(b)) for a, b in zip(x[sel], y[sel])]))

    def _pintar_arestas(self, p: QPainter, foco: str | None) -> None:
        if not len(self._ea):
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        profundidade = (self._sz[self._ea] + self._sz[self._eb]) / 2
        for j in np.argsort(profundidade):
            l = self._arestas[j]
            a = QPointF(float(self._sx[self._ea[j]]), float(self._sy[self._ea[j]]))
            b = QPointF(float(self._sx[self._eb[j]]), float(self._sy[self._eb[j]]))
            c = QPointF(float(self._cx_[j]), float(self._cy_[j]))
            frente = 0.45 + 0.55 * float(self._frente(profundidade[j]))
            destaque = foco is not None and (l.a == foco or l.b == foco)
            apagar = 0.3 if foco is not None and not destaque else 1.0
            if l.hierarquia:
                cor = tema.cor(tema.VERDE_LUZ, min(1.0, (0.85 if destaque else 0.5) * frente * apagar))
                largura = 2.2 if destaque else 1.4
            elif l.peso >= 0.5:  # forte: verde-luz intenso
                cor = tema.cor(tema.VERDE_LUZ, min(1.0, (0.2 + 0.6 * l.peso + (0.2 if destaque else 0)) * frente * apagar))
                largura = 0.8 + 1.8 * l.peso
            else:  # fraca: quase transparente
                cor = QColor(self.nos[l.a].cor)
                cor.setAlphaF(min(1.0, (0.05 + 0.3 * l.peso + (0.35 if destaque else 0)) * frente * apagar))
                largura = 0.6 + 1.2 * l.peso
            caminho = QPainterPath(a)
            caminho.quadTo(c, b)
            p.setPen(QPen(cor, largura))
            p.drawPath(caminho)
            if destaque and not self.reduzido:  # pulso de luz saindo da ideia em foco
                t = self._fase if l.a == foco else 1 - self._fase
                q = QPointF(
                    (1 - t) ** 2 * a.x() + 2 * (1 - t) * t * c.x() + t**2 * b.x(),
                    (1 - t) ** 2 * a.y() + 2 * (1 - t) * t * c.y() + t**2 * b.y(),
                )
                halo = QRadialGradient(q, 8)
                halo.setColorAt(0, tema.cor(tema.VERDE_LUZ, 0.95))
                halo.setColorAt(1, tema.cor(tema.VERDE_LUZ, 0.0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(halo)
                p.drawEllipse(q, 8, 8)
                p.setBrush(Qt.BrushStyle.NoBrush)

    def _pintar_nos(self, p: QPainter, foco: str | None, perto: set[str]) -> None:
        for i in np.argsort(self._sz):
            k = self._ids[i]
            n = self.nos[k]
            c = QPointF(float(self._sx[i]), float(self._sy[i]))
            r = float(self._sr[i])
            frente = float(self._frente(self._sz[i]))
            apagar = foco is not None and k not in perto
            alfa = (0.4 + 0.6 * frente) * (0.3 if apagar else 1.0)
            imp = min(1.0, math.sqrt(n.importancia / self._imp_max))
            cor = QColor(n.cor)
            if self.qualidade != "baixo":  # brilho pela importância
                rh = r * (2.2 + 1.6 * imp)
                halo = QRadialGradient(c, rh)
                halo.setColorAt(0.2, QColor(cor.red(), cor.green(), cor.blue(), int((35 + 110 * imp) * alfa)))
                halo.setColorAt(1.0, QColor(cor.red(), cor.green(), cor.blue(), 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(halo)
                p.drawEllipse(c, rh, rh)
            nucleo = QRadialGradient(QPointF(c.x() - r * 0.3, c.y() - r * 0.35), r * 1.5)
            claro, escuro = cor.lighter(170), cor.darker(170)
            for cc in (claro, cor, escuro):
                cc.setAlphaF(min(1.0, alfa + 0.15))
            nucleo.setColorAt(0.0, claro)
            nucleo.setColorAt(0.5, cor)
            nucleo.setColorAt(1.0, escuro)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(nucleo)
            p.drawEllipse(c, r, r)
            if n.ideia.tipo == "entidade":
                anel = QColor(cor.lighter(150))
                anel.setAlphaF(alfa * 0.8)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(anel, 1.2, Qt.PenStyle.DashLine))
                p.drawEllipse(c, r + 2.5, r + 2.5)
            if k in (self.selecionado, self.modo_ligar, self.modo_pai):
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(tema.VERDE_LUZ), 1.5))
                p.drawEllipse(c, r + 4, r + 4)
            rotulo = (n.nivel == "tronco" and not apagar) or k in perto or (self.zoom >= 1.4 and frente > 0.6 and not apagar)
            if rotulo:
                p.setPen(tema.cor(tema.TEXTO, (0.3 if apagar else 0.92) * (0.55 + 0.45 * frente)))
                p.setFont(tema.fonte_texto(tema.CORPO if n.nivel == "tronco" else tema.ROTULO + 1, negrito=n.nivel == "tronco"))
                p.drawText(QRectF(c.x() - 90, c.y() + r + 4, 180, 30),
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, n.ideia.nome)
