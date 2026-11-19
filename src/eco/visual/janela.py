"""Janela flutuante do orbe da Eco (PySide6).

Sem borda, sempre no topo, transparente. Fica invisível enquanto a Eco dorme e aparece com
fade quando ela é chamada. A thread de voz fala com a janela só por sinais (Barramento).
"""

from __future__ import annotations

import json
import math
import os
import signal
import threading
import time
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from eco.config import PASTA_ECO, TAMANHOS_ORBE, Config
from eco.painel import ESTADOS_VALIDOS, Painel
from eco.visual import tema
from eco.visual.particulas import FaiscasNitidas, Renderizador, Sistema

ALTURA_LEGENDA = 76
NIVEIS_BRILHO = [160, 200, 240, 280, 320, 400]  # resolução do brilho/rastro em tela cheia (adaptativa)
PESO_FAISCAS = 0.45  # o brilho leva só parte da energia das faíscas: elas vão nítidas por cima
QUADROS_S_REAGINDO = 60  # ouvindo e falando: a orbe segue o volume, precisa de 60
QUADROS_S_CALMO = 30  # pensando, confirmando, legenda, erro, mudo: o movimento é lento, 30 bastam
REAGE_AO_VOLUME = ("listen", "speak")
TEMPO_LEGENDA = 6.0
VERSAO_POSICAO = 2  # 2 = centro embaixo (18/11/2026): posições salvas antes disso são ignoradas uma vez
LIMIAR_ARRASTO = 6  # px que o mouse anda com o botão apertado até virar "arrastar a orbe"
MARGEM_BAIXO = 24  # distância da orbe (com a legenda) até a barra de tarefas
PREPARAR_INTERFACE_MS = 3000  # a janela principal é montada escondida logo depois de a Eco abrir


def tamanho_da_legenda(tela_cheia: bool) -> int:
    """Fonte da legenda: 2/3 do tamanho anterior (15 px -> 10 px; tela cheia 21 -> 14), para o texto longo caber
    na área fixa de ALTURA_LEGENDA."""
    return round((tema.SUBTITULO if not tela_cheia else 21) * 2 / 3)


class Barramento(QObject):
    """Sinais que cruzam threads com segurança (a fila de eventos do Qt faz a entrega)."""

    estado = Signal(str)
    nivel = Signal(float)
    legenda = Signal(str, bool)
    memoria = Signal()
    notificar = Signal(str, str)
    abrir_interface = Signal()
    flutuante = Signal(str)
    sair = Signal()
    pedir_texto = Signal(str, str, str, object, object)  # título, rótulo, valor, resposta (dict), pronto (Event)


class PainelQt(Painel):
    """Painel usado pela thread de voz: só emite sinais."""

    def __init__(self, barramento: Barramento) -> None:
        super().__init__()
        self.b = barramento

    def estado(self, nome: str) -> None:
        self.b.estado.emit(nome)

    def nivel(self, valor: float) -> None:
        self.b.nivel.emit(float(valor))

    def legenda(self, texto: str, fixa: bool = False) -> None:
        self.b.legenda.emit(texto, fixa)

    def memoria(self) -> None:
        self.b.memoria.emit()

    def notificar(self, titulo: str, texto: str) -> None:
        self.b.notificar.emit(titulo, texto)

    def flutuante(self, texto: str) -> None:
        self.b.flutuante.emit(texto)

    def abrir_interface(self) -> bool:
        self.b.abrir_interface.emit()  # a janela mora na thread principal; o sinal atravessa com segurança
        return True

    ESPERA_TEXTO_S = 300  # sem resposta em 5 minutos, vale como cancelado

    def pedir_texto(self, titulo: str, rotulo: str, valor: str = "") -> tuple[str, bool] | None:
        """A caixa abre na thread principal; esta thread (voz ou caixa de texto) espera a resposta."""
        import threading

        resposta: dict = {}
        pronto = threading.Event()
        self.b.pedir_texto.emit(titulo, rotulo, valor, resposta, pronto)
        if not pronto.wait(self.ESPERA_TEXTO_S):
            return ("", False)
        return resposta.get("r", ("", False))


def _arquivo_posicao():
    return PASTA_ECO / "orbe.json"


def _ler_estado() -> dict:
    try:
        return json.loads(_arquivo_posicao().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ler_posicao() -> tuple[int, int] | None:
    """Posição salva pelo dono. Uma gravada antes do centro embaixo (sem `versao_posicao`) não vale: a orbe vai uma
    vez para o novo lugar padrão e, dali em diante, onde ele a deixar."""
    d = _ler_estado()
    if d.get("versao_posicao") != VERSAO_POSICAO:
        return None
    try:
        return int(d["x"]), int(d["y"])
    except Exception:
        return None


def _gravar_posicao(x: int, y: int, tamanho: str | None = None) -> None:
    try:
        d = _ler_estado()
        d.update(x=x, y=y, versao_posicao=VERSAO_POSICAO)
        if tamanho:
            d["tamanho"] = tamanho
        _arquivo_posicao().parent.mkdir(parents=True, exist_ok=True)
        _arquivo_posicao().write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass


class JanelaOrbe(QWidget):
    def __init__(self, cfg: Config, painel: PainelQt | None = None, barramento: Barramento | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        salvo = _ler_estado().get("tamanho")  # o tamanho escolhido no menu vale, salvo ECO_ORBE_TAMANHO explícito
        self.lado_normal = TAMANHOS_ORBE[salvo] if salvo in TAMANHOS_ORBE and "ECO_ORBE_TAMANHO" not in os.environ else cfg.orbe_tamanho
        self.tela_cheia = False
        self.nitidas: FaiscasNitidas | None = None  # faíscas na resolução da janela, por cima do brilho
        self.ov: np.ndarray | None = None
        self.centro = (self.lado_normal / 2, self.lado_normal / 2)  # centro do orbe em coordenadas da janela
        self.geom_normal = None
        self.painel = painel
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setFixedSize(self.lado_normal, self.lado_normal + ALTURA_LEGENDA)
        self.setWindowTitle("Eco")

        self.sistema = Sistema(cfg.particulas)
        self._configurar_desenho(self.lado_normal, self.lado_normal // 2)
        self.opac_aplicada = -1.0  # última opacidade passada ao Windows (mudar a cada quadro custa caro)
        self.pausada = False  # a janela principal pede pausa enquanto anima a abertura
        self.estado = "idle"
        self.alvo_nivel = 0.0
        self.legenda = ""
        self.legenda_fixa = False
        self.legenda_ate = 0.0
        self.opac = 0.0
        self.cursor: tuple[float, float] | None = None
        self.pressionado = False
        self.principal = None  # janela principal, criada na primeira vez que é aberta
        self.janela_flutuante = None  # código e texto longo das respostas
        self._clique_valido = False  # o clique atual pode virar "abrir a interface"
        self._t_press = 0.0
        self._press_global: QPointF | None = None  # onde o botão foi apertado (para saber se virou arrasto)
        self.movendo: QPointF | None = None
        self.quadro = None
        self.img_buf: np.ndarray | None = None
        self.t_ultimo = time.perf_counter()

        pos = _ler_posicao() or self._posicao_padrao()
        self.move(*pos)
        self.setWindowOpacity(0.0)
        if cfg.orbe_tela_cheia:
            self.alternar_tela_cheia()

        if barramento is not None:
            barramento.estado.connect(self.definir_estado)
            barramento.nivel.connect(self.definir_nivel)
            barramento.legenda.connect(self.definir_legenda)
            barramento.memoria.connect(lambda: self.sistema.acender())
            barramento.abrir_interface.connect(self.abrir_principal)
            barramento.flutuante.connect(self.mostrar_flutuante)
            barramento.pedir_texto.connect(self._pedir_texto)

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)  # o padrão do Windows arredonda para ~15 ms e engasga
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)
        # monta a janela principal escondida, com a Eco já de pé: o primeiro "abrir" fica tão rápido quanto os outros
        QTimer.singleShot(PREPARAR_INTERFACE_MS, self._preparar_principal)

    def _configurar_desenho(self, orbe_px: float, lado_brilho: int) -> None:
        """Brilho e rastro em `lado_brilho` (borrados, baratos), ampliados até `orbe_px`; as faíscas vão nítidas por
        cima, na resolução da janela. `self.lado` é o sistema de coordenadas do motor (o do brilho)."""
        antigo = getattr(self, "lado", lado_brilho)
        self.lado = lado_brilho
        self.k = orbe_px / lado_brilho
        self.render = Renderizador(lado_brilho, peso_faiscas=PESO_FAISCAS)
        self.nitidas = FaiscasNitidas(int(round(orbe_px)))
        self.ov = None
        r = lado_brilho / antigo  # mantém o deslocamento das faíscas (cursor) proporcional
        s = self.sistema
        s.ox *= r; s.oy *= r; s.vx *= r; s.vy *= r

    def pausar(self, pausada: bool) -> None:
        """Para de desenhar (a janela principal anima a abertura e precisa do processador só para ela)."""
        self.pausada = pausada
        self.t_ultimo = time.perf_counter()

    def _transicao_principal(self, fator: float | None) -> None:
        """Chamada pela janela principal a cada quadro da animação: a orbe pausa e se apaga (`fator` de 1 a 0), e
        volta ao normal com None."""
        if fator is None:
            self.pausar(False)
            self.opac_aplicada = -1.0  # o próximo quadro reaplica a opacidade de verdade
            return
        if not self.pausada:
            self.pausar(True)
        if self.isVisible():
            self.setWindowOpacity(self.opac * max(0.0, min(1.0, fator)))
            self.opac_aplicada = -1.0

    def _aplicar_opacidade(self, valor: float) -> None:
        if abs(valor - self.opac_aplicada) > 0.002 or (valor in (0.0, 1.0) and valor != self.opac_aplicada):
            self.setWindowOpacity(valor)
            self.opac_aplicada = valor

    def _intervalo(self) -> int:
        """Milissegundos entre quadros: 60 por segundo quando a orbe segue o volume, 30 no resto."""
        alvo = QUADROS_S_REAGINDO if self.estado in REAGE_AO_VOLUME or self.pressionado or self.cursor else QUADROS_S_CALMO
        return round(1000 / alvo)

    # --- entradas -------------------------------------------------------------------------
    def _tela(self) -> QRect:
        """Área útil (sem a barra de tarefas) da tela onde a orbe está."""
        tela = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        return tela.availableGeometry()

    def _posicao_padrao(self, tela: QRect | None = None) -> tuple[int, int]:
        """Centro horizontal, embaixo: a orbe e a legenda logo acima da barra de tarefas."""
        tela = tela or QGuiApplication.primaryScreen().availableGeometry()
        return tela.x() + (tela.width() - self.width()) // 2, tela.y() + tela.height() - self.height() - MARGEM_BAIXO

    def na_posicao_padrao(self) -> bool:
        x, y = self._posicao_padrao(self._tela())
        return abs(self.x() - x) <= 2 and abs(self.y() - y) <= 2

    def voltar_ao_centro(self) -> None:
        """Menu "Voltar ao centro": centro embaixo da tela onde a orbe está, e grava."""
        if self.tela_cheia:
            return
        self.move(*self._posicao_padrao(self._tela()))
        _gravar_posicao(self.x(), self.y())

    def definir_estado(self, nome: str) -> None:
        if nome in ESTADOS_VALIDOS:
            self.estado = nome

    def definir_nivel(self, valor: float) -> None:
        self.alvo_nivel = valor

    def definir_legenda(self, texto: str, fixa: bool = False) -> None:
        self.legenda = texto
        self.legenda_fixa = fixa
        self.legenda_ate = time.perf_counter() + TEMPO_LEGENDA if texto else 0.0

    # --- ciclo ----------------------------------------------------------------------------
    def _legenda_visivel(self, agora: float) -> bool:
        return bool(self.legenda) and (self.legenda_fixa or agora < self.legenda_ate)

    def _tick(self) -> None:
        agora = time.perf_counter()
        dt = agora - self.t_ultimo
        self.t_ultimo = agora

        visivel_alvo = self.estado != "idle" or self._legenda_visivel(agora) or self.movendo is not None
        alvo = 1.0 if visivel_alvo else 0.0
        passo = dt * (5.0 if alvo > self.opac else 2.5)
        self.opac = min(alvo, self.opac + passo) if alvo > self.opac else max(alvo, self.opac - passo)

        if self.opac <= 0.005:
            if self.isVisible():
                self.hide()
            self.opac_aplicada = -1.0
            self.timer.setInterval(80)
            self.sistema.passo(dt, "idle", 0, self.lado)  # mantém a animação "viva" sem pintar
            return
        if self.pausada:
            self.timer.setInterval(80)
            return
        intervalo = self._intervalo()
        if self.timer.interval() != intervalo:
            self.timer.setInterval(intervalo)
            if self.tela_cheia:  # outra taxa: a medição de folga recomeça
                self.ema_dt, self.t_troca = intervalo / 1000, agora
        if not self.isVisible():
            self.show()
        self._aplicar_opacidade(self.opac)
        if self.tela_cheia and intervalo < 20:  # a adaptação mede folga a 60 quadros/s; a 30 sempre sobra
            self._adaptar_qualidade(min(dt, 0.1), agora)

        cur = None
        if self.cursor:  # da janela para o sistema de coordenadas do desenho interno
            cur = ((self.cursor[0] - self.centro[0]) / self.k, (self.cursor[1] - self.centro[1]) / self.k)
        q = self.sistema.passo(dt, self.estado, self.alvo_nivel, self.lado, cur, self.pressionado, self.cfg.orbe_tom)
        self.quadro = q
        self.img_buf = np.ascontiguousarray(self.render.quadro(q, dt))
        self.ov = self.nitidas.quadro(q, self.k) if self.nitidas is not None else None
        self.update()

    def paintEvent(self, _evento) -> None:
        if self.quadro is None or self.img_buf is None:
            return
        q = self.quadro
        L = self.lado
        c = L / 2
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        img = QImage(self.img_buf.data, L, L, L * 4, QImage.Format.Format_RGBA8888_Premultiplied)
        p.save()
        p.translate(self.centro[0], self.centro[1])
        p.scale(self.k, self.k)
        p.translate(-c, -c)
        p.drawImage(0, 0, img)
        if self.ov is not None:
            n = self.ov.shape[0]
            ov = QImage(self.ov.data, n, n, n * 4, QImage.Format.Format_RGBA8888_Premultiplied)
            p.save()
            p.resetTransform()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.drawImage(int(self.centro[0] - n / 2), int(self.centro[1] - n / 2), ov)
            p.restore()

        def cor(l: float, a: float, dh: float = 0.0) -> QColor:
            return QColor.fromHslF(((q.hue + dh) % 360) / 360, 0.8, l, max(0.0, min(1.0, a)))

        # raios finos saindo do centro
        for x0, y0, x1, y1, o in q.raios:
            p.setPen(QPen(cor(0.6, o), 1.0))
            p.drawLine(QPointF(c + x0, c + y0), QPointF(c + x1, c + y1))

        if q.estado == "think":  # anel tracejado girando
            pen = QPen(cor(0.62, 0.5 * min(1, q.br)), 1.4, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([2, 6])
            pen.setDashOffset(-q.t * 10)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(c, c), q.R * 1.08, q.R * 1.08)
        elif q.estado == "confirm":  # arco girando, esperando o "sim"
            p.setPen(QPen(cor(0.65, 0.8, 10), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            raio = q.R * 1.1
            ang = int(-q.t * 140 * 16)
            p.drawArc(QRectF(c - raio, c - raio, 2 * raio, 2 * raio), ang, 100 * 16)
        p.restore()

        if self._legenda_visivel(time.perf_counter()):
            self._pintar_legenda(p, self.height() - ALTURA_LEGENDA)
        p.end()

    def _pintar_legenda(self, p: QPainter, topo: int) -> None:
        fonte = tema.fonte_texto(tamanho_da_legenda(self.tela_cheia))
        p.setFont(fonte)
        area = QRectF(12, topo - 6, self.width() - 24, ALTURA_LEGENDA - 8)
        texto = self.legenda
        # sombra suave + texto
        p.setPen(QColor(0, 0, 0, 170))
        flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
        p.drawText(area.translated(1, 1), flags, texto)
        p.setPen(QColor(220, 245, 238, 235))
        p.drawText(area, flags, texto)

    # --- mouse ----------------------------------------------------------------------------
    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        if self.movendo is None and self._press_global is not None and not self.tela_cheia:
            andou = e.globalPosition() - self._press_global
            if abs(andou.x()) + abs(andou.y()) >= LIMIAR_ARRASTO:  # apertou e arrastou: move a orbe
                self.movendo = self._press_global - QPointF(self.pos())
                self.pressionado, self._clique_valido, self.cursor = False, False, None
        if self.movendo is not None and not self.tela_cheia:
            self.move((e.globalPosition() - self.movendo).toPoint())
            return
        self.cursor = (pos.x(), pos.y())

    def retangulo_do_orbe(self) -> QRect:
        """Onde o orbe está na tela agora (mesmo com a janela dele invisível): ponto de partida da janela principal."""
        lado = self.orbe_px if self.tela_cheia else self.lado_normal
        cx, cy = self.x() + self.centro[0], self.y() + self.centro[1]
        return QRect(int(cx - lado / 2), int(cy - lado / 2), int(lado), int(lado))

    def _criar_principal(self) -> None:
        """Monta a janela principal e as abas (escondidas). Não carrega nada: cada aba lê os dados ao ser aberta."""
        from eco.visual.bloco import PainelBloco
        from eco.visual.chat import PainelChat
        from eco.visual.dashboard import PainelDashboard
        from eco.visual.grafo import PainelGrafo
        from eco.visual.principal import JanelaPrincipal

        chat = PainelChat(self.cfg, self.painel)
        self.principal = JanelaPrincipal(self.cfg, chat=chat, grafo=PainelGrafo(self.cfg, chat.banco), dashboard=PainelDashboard(self.cfg), bloco=PainelBloco(self.cfg))  # mesmo banco
        self.principal.estado_eco = lambda: (self.estado, self.alvo_nivel)  # as partículas de fundo reagem à Eco
        self.principal.ao_animar = self._transicao_principal
        self.principal.origem_fechar = self.retangulo_do_orbe

    def _preparar_principal(self) -> None:
        if self.principal is not None:
            return
        try:
            self._criar_principal()
            self.principal.aquecer(self.retangulo_do_orbe())
        except Exception as e:  # se falhar aqui, o clique tenta de novo (e mostra o erro de verdade)
            self.principal = None
            print(f"[interface] não consegui preparar a janela principal: {e}")

    def abrir_principal(self) -> None:
        """Abre a janela principal crescendo a partir da orbe (clique, voz, menu ou ícone da bandeja)."""
        if self.principal is None:
            self._criar_principal()
        self.principal.abrir(self.retangulo_do_orbe())

    def _pedir_texto(self, titulo: str, rotulo: str, valor: str, resposta: dict, pronto) -> None:
        """Caixa HUD por cima de tudo (o pedido pode vir da voz, com a janela principal fechada)."""
        from eco.visual import dialogos

        try:
            resposta["r"] = dialogos.texto(None, titulo, rotulo, valor, por_cima=True)
        finally:
            pronto.set()

    def mostrar_flutuante(self, texto: str) -> None:
        """Código ou texto longo de uma resposta: janela à parte, por cima das outras."""
        from eco.visual.flutuante import JanelaFlutuante

        if self.janela_flutuante is None:
            self.janela_flutuante = JanelaFlutuante()
        self.janela_flutuante.mostrar(texto)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape and self.tela_cheia:
            self.alternar_tela_cheia()

    def definir_tamanho(self, nome: str) -> None:
        """Troca entre os tamanhos do menu: pequeno, medio, grande e tela cheia."""
        if nome == "tela":
            if not self.tela_cheia:
                self.alternar_tela_cheia()
            return
        if self.tela_cheia:
            self.alternar_tela_cheia()
        novo = TAMANHOS_ORBE[nome]
        if novo == self.lado_normal:
            return
        centro = self.geometry().center()  # cresce e encolhe em torno do mesmo ponto
        no_padrao = self.na_posicao_padrao()  # no centro embaixo: continua lá (base fixa), em vez de crescer para baixo
        self.lado_normal = novo
        self._configurar_desenho(novo, novo // 2)
        self.centro = (novo / 2, novo / 2)
        self.setFixedSize(novo, novo + ALTURA_LEGENDA)
        tela = (QGuiApplication.screenAt(centro) or QGuiApplication.primaryScreen()).availableGeometry()
        if no_padrao:
            x, y = self._posicao_padrao(tela)
        else:
            x = max(tela.left(), min(centro.x() - self.width() // 2, tela.right() - self.width()))
            y = max(tela.top(), min(centro.y() - self.height() // 2, tela.bottom() - self.height()))
        self.move(int(x), int(y))
        _gravar_posicao(self.x(), self.y(), nome)

    def _nivel_brilho(self, idx: int) -> None:
        """Escolhe a resolução do brilho/rastro em tela cheia. As faíscas são sempre nítidas, na tela."""
        self.nivel_brilho = idx
        self._configurar_desenho(self.orbe_px, NIVEIS_BRILHO[idx])

    def _adaptar_qualidade(self, dt: float, agora: float) -> None:
        """Sobe a nitidez enquanto o PC dá conta de ~60 quadros/s; desce se começar a engasgar."""
        self.ema_dt = 0.92 * self.ema_dt + 0.08 * dt
        if agora - self.t_troca < 2.5:
            return
        if self.ema_dt > 0.024 and self.nivel_brilho > 0:  # abaixo de ~42 quadros/s
            self.teto = min(self.teto, self.nivel_brilho - 1) if agora - self.t_troca < 12 else self.teto
            self._nivel_brilho(self.nivel_brilho - 1)
            self.t_troca = agora
        elif self.ema_dt < 0.0185 and self.nivel_brilho < self.teto:  # folga: tenta o nível acima
            self._nivel_brilho(self.nivel_brilho + 1)
            self.t_troca = agora

    def alternar_tela_cheia(self) -> None:
        """Orbe ocupando a tela inteira (fundo transparente). Duplo clique, Esc ou o menu voltam."""
        if not self.tela_cheia:
            self.geom_normal = self.geometry()
            tela = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
            g = tela.geometry()
            self.tela_tamanho = (g.width(), g.height())
            self.tela_cheia = True
            self.setFixedSize(g.width(), g.height())
            self.move(g.x(), g.y())
            area_h = g.height() - ALTURA_LEGENDA
            self.orbe_px = min(g.width(), area_h) * self.cfg.orbe_tela_escala  # tamanho do orbe na tela
            self.centro = (g.width() / 2, area_h / 2)
            self.sistema = Sistema(self.cfg.particulas * 1.6)  # mais faíscas: tela grande aguenta e fica mais rico
            self.ema_dt, self.t_troca, self.teto = 1 / 60, time.perf_counter(), len(NIVEIS_BRILHO) - 1
            self._nivel_brilho(2)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        else:
            self.tela_cheia = False
            self.sistema = Sistema(self.cfg.particulas)
            self.lado = self.lado_normal // 2
            self._configurar_desenho(self.lado_normal, self.lado_normal // 2)
            self.centro = (self.lado_normal / 2, self.lado_normal / 2)
            self.setFixedSize(self.lado_normal, self.lado_normal + ALTURA_LEGENDA)
            if self.geom_normal is not None:
                self.move(self.geom_normal.topLeft())

    def leaveEvent(self, _e) -> None:
        if not self.pressionado:
            self.cursor = None

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            if self.estado == "speak" and self.painel:  # clique no orbe enquanto ela fala = calar
                self.painel.interromper.set()
                self._clique_valido = False
                return
            if not self.tela_cheia and e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
                self.movendo = e.globalPosition() - QPointF(self.pos())  # com Ctrl/Shift já começa movendo
            else:
                # clique rápido parado abre a interface; segurar parado puxa as faíscas; apertar e arrastar move
                self.pressionado = True
                self.cursor = (e.position().x(), e.position().y())
                self._clique_valido, self._t_press = True, time.perf_counter()
                self._press_global = e.globalPosition()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_global = None
            if self.movendo is not None:
                self.movendo = None
                if QGuiApplication.screenAt(self.geometry().center()) is None:  # largou fora de qualquer tela
                    self.move(*self._posicao_padrao())
                _gravar_posicao(self.x(), self.y())
            elif self._clique_valido and time.perf_counter() - self._t_press < 0.35:  # clique rápido (segurar mexe nas faíscas)
                self.abrir_principal()
            self._clique_valido = False
            self.pressionado = False

    def contextMenuEvent(self, e) -> None:
        menu = QMenu(self)
        mudo = bool(self.painel and self.painel.mudo)
        a_mudo = menu.addAction("Ligar a voz" if mudo else "Silenciar a voz")
        a_interface = menu.addAction("Abrir interface")
        a_calar = menu.addAction("Parar de falar")
        a_rep = menu.addAction("Repetir última resposta")
        menu.addSeparator()
        sub = menu.addMenu("Tamanho")
        escolhas = {}
        for nome, rotulo in (("pequeno", "Pequeno"), ("medio", "Médio"), ("grande", "Grande")):
            a = sub.addAction(rotulo)
            a.setCheckable(True)
            a.setChecked(not self.tela_cheia and TAMANHOS_ORBE[nome] == self.lado_normal)
            escolhas[a] = nome
        a_tela = sub.addAction("Tela cheia")
        a_tela.setCheckable(True)
        a_tela.setChecked(self.tela_cheia)
        a_centro = None
        if not self.tela_cheia:
            a_centro = menu.addAction("Voltar ao centro")
            a_centro.setEnabled(not self.na_posicao_padrao())
            a_pos = menu.addAction("Mover: clique e arraste")
            a_pos.setEnabled(False)
        a_sair = menu.addAction("Sair")
        r = menu.exec(e.globalPos())
        if r is a_mudo and self.painel:
            self.alternar_mudo()
        elif r is a_interface:
            self.abrir_principal()
        elif r is a_calar and self.painel:
            self.painel.interromper.set()
        elif r is a_rep and self.painel:
            self.painel.pedido_repetir.set()
        elif r is a_tela:
            self.alternar_tela_cheia()
        elif a_centro is not None and r is a_centro:
            self.voltar_ao_centro()
        elif r in escolhas:
            self.definir_tamanho(escolhas[r])
        elif r is a_sair:
            self.pedir_para_sair()

    def pedir_para_sair(self) -> bool:
        """Pede confirmação antes de fechar a Eco (o Sair fica colado no Silenciar e fechar sem querer a deixa muda).
        Devolve True se saiu."""
        from PySide6.QtWidgets import QMessageBox

        caixa = QMessageBox()
        caixa.setWindowTitle("Eco")
        caixa.setIcon(QMessageBox.Icon.Question)
        caixa.setText("Fechar a Eco?")
        caixa.setInformativeText("Ela deixa de ouvir até você abri-la de novo. Para só calar a voz (ela continua ouvindo e responde na tela), use Silenciar a voz.")
        from eco.visual.componentes import Botao

        sair, ficar = Botao("Fechar a Eco", "parar"), Botao("Continuar aberta")
        sair.setObjectName("parar")
        caixa.addButton(sair, QMessageBox.ButtonRole.AcceptRole)
        caixa.addButton(ficar, QMessageBox.ButtonRole.RejectRole)
        caixa.setDefaultButton(ficar)  # Enter sem querer não fecha
        caixa.setEscapeButton(ficar)
        caixa.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        caixa.exec()
        if caixa.clickedButton() is sair:
            print("[saída] pedida pelo menu (Sair)")
            QApplication.quit()
            return True
        return False

    def alternar_mudo(self) -> None:
        if not self.painel:
            return
        self.painel.mudo = not self.painel.mudo
        self.estado = "mute" if self.painel.mudo else "idle"
        self.definir_legenda("Voz desligada: respondo na tela" if self.painel.mudo else "Voz ligada", False)


def instalar_coletor(app: QApplication) -> QTimer:
    """Faz o coletor de lixo do Python rodar só na thread principal.

    Com o coletor automático, qualquer thread (voz, lembretes, carga do Painel) pode disparar uma coleta e destruir
    objetos do Qt (QTimer, QPixmap...) fora da thread deles: queda nativa (`access violation` dentro de
    "Garbage-collecting"), sem nenhuma linha no log. Aqui o automático é desligado e um relógio do Qt coleta: a
    geração jovem a cada 2 s e tudo a cada 60 s."""
    import gc

    gc.disable()
    contagem = {"n": 0}

    def coletar() -> None:
        contagem["n"] += 1
        gc.collect(1 if contagem["n"] % 30 else 2)

    relogio = QTimer(app)
    relogio.setInterval(2000)
    relogio.timeout.connect(coletar)
    relogio.start()
    app.coletor = relogio  # mantém a referência viva
    return relogio


def abrir_janela(cfg: Config) -> tuple[QApplication, JanelaOrbe, PainelQt]:
    try:  # identidade própria na barra de tarefas (senão o Windows agrupa com o Python e mostra o ícone dele)
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Eco.Assistente")
    except Exception:
        pass
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    instalar_coletor(app)
    from eco.visual.principal import icone_eco

    app.setWindowIcon(icone_eco(cfg.orbe_tom))
    barramento = Barramento()
    painel = PainelQt(barramento)
    janela = JanelaOrbe(cfg, painel, barramento)
    janela.icone = _criar_icone_bandeja(app, janela)  # sem ele, uma Eco escondida não tem como ser fechada
    return app, janela, painel


def _criar_icone_bandeja(app: QApplication, janela: JanelaOrbe):
    """Ícone perto do relógio: a Eco roda escondida (orbe só aparece quando chamada), e o menu dele silencia ou fecha."""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QSystemTrayIcon

    from eco.visual.principal import icone_eco

    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    icone = QSystemTrayIcon(icone_eco(janela.cfg.orbe_tom), app)
    icone.setToolTip("Eco: diga “ei eco”")
    icone.activated.connect(
        lambda motivo: janela.abrir_principal() if motivo == QSystemTrayIcon.ActivationReason.Trigger else None
    )
    menu = QMenu()
    a_interface = QAction("Abrir interface", menu)
    a_interface.triggered.connect(janela.abrir_principal)
    a_mudo = QAction("Silenciar a voz", menu)
    a_mudo.triggered.connect(janela.alternar_mudo)
    menu.aboutToShow.connect(lambda: a_mudo.setText("Ligar a voz" if janela.painel and janela.painel.mudo else "Silenciar a voz"))
    a_calar = QAction("Parar de falar", menu)
    a_calar.triggered.connect(lambda: janela.painel and janela.painel.interromper.set())
    a_sair = QAction("Sair", menu)
    a_sair.triggered.connect(janela.pedir_para_sair)
    menu.addAction(a_interface)
    menu.addAction(a_calar)
    menu.addAction(a_mudo)
    menu.addAction(a_sair)
    icone.setContextMenu(menu)
    if janela.painel is not None:  # lembretes: balão do sistema na hora, mesmo que a Eco esteja falando
        janela.painel.b.notificar.connect(
            lambda titulo, texto: icone.showMessage(titulo, texto, QSystemTrayIcon.MessageIcon.Information, 15000)
        )
    icone.menu = menu  # mantém referências vivas
    icone.show()
    return icone


def rodar_com_trabalho(cfg: Config, trabalho: Callable[[PainelQt], None]) -> int:
    """Roda `trabalho(painel)` numa thread e o laço do Qt na principal. Fecha quando um acabar."""
    app, janela, painel = abrir_janela(cfg)

    def alvo() -> None:
        try:
            trabalho(painel)
            print("[saída] o laço de voz terminou sem erro; fechando a Eco")
        except BaseException:
            import traceback

            print("[saída] o laço de voz parou por um erro; fechando a Eco:")
            traceback.print_exc()
            raise
        finally:
            try:
                painel.b.sair.emit()
            except RuntimeError:  # a Eco já estava fechando (o Qt apagou o emissor)
                pass

    def ao_fechar() -> None:
        import datetime

        print(f"[saída] a Eco fechou em {datetime.datetime.now():%H:%M:%S}")

    painel.b.sair.connect(app.quit)
    app.aboutToQuit.connect(ao_fechar)

    def ctrl_c(*_) -> None:
        print("[saída] Ctrl+C no terminal")
        app.quit()

    signal.signal(signal.SIGINT, ctrl_c)  # Ctrl+C no terminal fecha a Eco
    respiro = QTimer()  # deixa o Python tratar sinais enquanto o Qt roda
    respiro.start(250)
    respiro.timeout.connect(lambda: None)
    t = threading.Thread(target=alvo, daemon=True, name="eco-voz")
    t.start()
    return app.exec()


def demo(cfg: Config) -> int:
    """Percorre os estados com volume simulado. Não precisa de nenhuma chave de API."""
    roteiro = [
        ("listen", "Ouvindo…", 4.0),
        ("think", "Pensando…", 3.0),
        ("speak", "Oi! Estou aqui, pode falar comigo.", 5.0),
        ("confirm", "Confirma enviar o e-mail?", 4.0),
        ("error", "Sem conexão com a internet.", 3.5),
        ("mute", "Silenciada", 3.0),
        ("idle", "", 3.0),
    ]

    def trabalho(painel: PainelQt) -> None:
        painel.legenda("Demo do orbe da Eco. Clique e arraste para mover; botão direito abre o menu; clique abre a interface; tela cheia pelo menu.")
        painel.estado("listen")
        time.sleep(2)
        t0 = time.perf_counter()
        for nome, texto, dur in roteiro:
            painel.estado(nome)
            painel.legenda(texto)
            if nome == "speak":
                painel.memoria()
            fim = time.perf_counter() + dur
            while time.perf_counter() < fim:
                t = time.perf_counter() - t0
                nivel = 0.0
                if nome in ("listen", "speak"):
                    nivel = max(0.0, min(1.0, 0.45 + 0.4 * math.sin(t * 7) * math.sin(t * 2.3) + 0.15 * math.sin(t * 17)))
                painel.nivel(nivel)
                time.sleep(0.03)
        time.sleep(1)

    return rodar_com_trabalho(cfg, trabalho)
