"""Janela principal da Eco (Qt). Por enquanto só o cabeçalho e um espaço vazio: o grafo e o chat entram depois.

Abre crescendo a partir da orbe (um círculo que vira painel). A janela já nasce no tamanho final e a animação é
pintada dentro dela, sobre uma captura do conteúdo pronto: nada é redimensionado nem montado a cada quadro, e no fim a
captura dá lugar aos widgets de verdade (a imagem é a mesma, então não há pulo). Fechar faz o caminho inverso, de volta
à orbe, e só esconde: a Eco continua rodando. Com a animação do Windows desligada (equivalente do
`prefers-reduced-motion`), abrir é só um fade e fechar é imediato.
"""

from __future__ import annotations

import ctypes
import os
import time
from typing import Callable

from PySide6.QtCore import QEasingCurve, QPoint, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QComboBox, QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QStackedWidget, QTextEdit, QWidget

from eco.config import Config
from eco.visual import fundo, tema
from eco.visual.componentes import Aba

LARGURA, ALTURA = 1360, 860
ALTURA_CABECALHO = 56
RAIO = 18.0
DURACAO_MS = 280
DURACAO_FECHAR_MS = 180
DURACAO_FADE_MS = 160
ESCALA_INICIAL = 0.96  # o conteúdo chega de 96% a 100% enquanto o painel cresce
ORBE_SOME_ATE = 0.4  # a orbe se apaga nos primeiros 40% da abertura: ela "vira" a janela


def movimento_reduzido() -> bool:
    """True se o usuário pediu menos movimento: ECO_MOVIMENTO_REDUZIDO, ou "efeitos de animação" desligados no Windows."""
    env = os.environ.get("ECO_MOVIMENTO_REDUZIDO", "").strip().lower()
    if env in ("1", "sim", "true"):
        return True
    if env in ("0", "nao", "não", "false"):
        return False
    try:
        ligado = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(ligado), 0)  # SPI_GETCLIENTAREAANIMATION
        return bool(ok) and not ligado.value
    except Exception:  # fora do Windows ou sem a API: movimento normal
        return False


def interpolar(a: tuple[float, ...], b: tuple[float, ...], p: float) -> tuple[float, ...]:
    return tuple(x + (y - x) * p for x, y in zip(a, b))


def icone_eco(tom: float) -> QIcon:
    """Bolinha na cor da orbe, para a janela e a bandeja."""
    icone = QIcon()
    h = tom / 360
    for lado in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(lado, lado)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = lado / 2
        halo = QRadialGradient(c, c, c)  # brilho suave em volta, como a orbe
        halo.setColorAt(0.55, QColor.fromHslF(h, 0.8, 0.5, 0.35))
        halo.setColorAt(1.0, QColor.fromHslF(h, 0.8, 0.5, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(0, 0, lado, lado)
        r = lado * 0.34
        nucleo = QRadialGradient(c - r * 0.3, c - r * 0.35, r * 1.4)  # núcleo claro no canto, escuro na borda
        nucleo.setColorAt(0.0, QColor.fromHslF(h, 0.75, 0.82))
        nucleo.setColorAt(0.45, QColor.fromHslF(h, 0.75, 0.52))
        nucleo.setColorAt(1.0, QColor.fromHslF(h, 0.8, 0.30))
        p.setBrush(nucleo)
        p.drawEllipse(QRectF(c - r, c - r, 2 * r, 2 * r))
        p.end()
        icone.addPixmap(pm)
    return icone


class _Esmaecer(QWidget):
    """Troca de aba: a captura da aba anterior fica por cima da nova e some (crossfade). Não recebe cliques."""

    def __init__(self, parent: QWidget, captura: QPixmap, area: QRect) -> None:
        super().__init__(parent)
        self.captura = captura
        self.alfa = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(area)
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(tema.TROCA_ABA_MS)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.valueChanged.connect(self._ao_animar)
        self.anim.finished.connect(self.deleteLater)
        self.show()
        self.raise_()
        self.anim.start()

    def _ao_animar(self, v) -> None:
        self.alfa = float(v)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setOpacity(self.alfa)
        p.drawPixmap(0, 0, self.captura)
        p.end()


class JanelaPrincipal(QWidget):
    def __init__(
        self,
        cfg: Config,
        chat: QWidget | None = None,
        grafo: QWidget | None = None,
        dashboard: QWidget | None = None,
        bloco: QWidget | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.chat, self.grafo, self.dashboard, self.bloco = chat, grafo, dashboard, bloco
        self.corpo: QStackedWidget | None = None
        self.abas: list[QPushButton] = []
        if chat is not None:
            self.corpo = QStackedWidget(self)
            self.corpo.addWidget(chat)
            self.corpo.hide()  # só aparece quando a animação de abertura termina
            if grafo is not None:
                self.corpo.addWidget(grafo)
                grafo.abrir_conversa.connect(self.abrir_conversa_no_chat)
                nomes = ["Conversas", "Grafo"]
                if dashboard is not None:
                    self.corpo.addWidget(dashboard)
                    nomes.append("Painel")
                if bloco is not None:
                    self.corpo.addWidget(bloco)
                    nomes.append("Bloco")
                    grafo.abrir_nota.connect(self.abrir_nota_no_bloco)
                x = 110
                for i, nome in enumerate(nomes):
                    b = Aba(nome, self)  # estilo HUD do sistema de design (visual/componentes.py)
                    b.clicked.connect(lambda _=False, i=i: self.mostrar_aba(i))
                    b.resize(b.sizeHint())
                    b.move(x, (ALTURA_CABECALHO - b.height()) // 2)
                    x += b.width() + 4
                    b.hide()
                    self.abas.append(b)
                self.abas[0].setChecked(True)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setWindowTitle("Eco")
        self.setWindowIcon(icone_eco(cfg.orbe_tom))
        self.p = 1.0  # progresso da abertura, 0 a 1
        self.conteudo = 1.0  # opacidade do cabeçalho durante a abertura
        self.raio = RAIO
        self.reduzido = False
        self.sobre_fechar = False
        self._origem = (0.0, 0.0, 0.0, 0.0)
        self._alvo = (0.0, 0.0, 0.0, 0.0)
        self.anim = QVariantAnimation(self)
        self.anim.valueChanged.connect(self._passo)
        self.anim.finished.connect(self._fim_da_animacao)
        self.captura: QPixmap | None = None  # o conteúdo pronto, pintado durante a animação
        self._esmaecer: _Esmaecer | None = None  # crossfade da troca de aba em andamento
        self.fechando = False
        # a orbe: recebe o quanto deve continuar visível durante a transição (None = acabou, pode voltar a desenhar)
        self.ao_animar: Callable[[float | None], None] | None = None
        self.origem_fechar: Callable[[], QRect | None] | None = None  # para onde a janela encolhe ao fechar
        # partículas de fundo (visual/fundo.py): o estado vem da orbe, por `estado_eco` -> (estado, volume da voz)
        self.estado_eco: Callable[[], tuple[str, float]] | None = None
        self.campo: fundo.CampoFundo | None = None if cfg.fundo == "desligado" else fundo.CampoFundo(cfg.fundo, cfg.orbe_tom)
        self.quadro_fundo: fundo.QuadroFundo | None = None
        self._degrade: QPixmap | None = None  # fundo fixo (sem partículas): refeito só ao mudar de tamanho
        self.densidade_caiu = False
        self._ret_fundo: list[QRect] = []
        self._cobertos: list[QWidget] | None = None  # campos opacos da aba aberta (escondem as partículas)
        self._tique = 0
        self._ultimo_fundo = time.perf_counter()
        self._janela_fps = (self._ultimo_fundo, 0)
        self.relogio_fundo = QTimer(self)
        self.relogio_fundo.setTimerType(Qt.TimerType.PreciseTimer)
        self.relogio_fundo.setInterval(round(1000 / fundo.QUADROS_S_CALMO))
        self.relogio_fundo.timeout.connect(self._passo_fundo)
        if self.campo is not None:
            self.quadro_fundo = self.campo.passo(0.0)

    def _mostrar_chat(self) -> None:
        if self.corpo is not None and self.isVisible():
            self._posicionar_chat()
            self.corpo.show()
            for b in self.abas:
                b.show()
        self._ligar_fundo()

    # --- partículas de fundo --------------------------------------------------------------------------------
    def area_fundo(self) -> QRect:
        """Onde as partículas passam: a janela toda (cabeçalho incluso) menos a faixa de baixo dos cantos arredondados."""
        return QRect(1, 1, max(1, self.width() - 2), max(1, self.height() - 1 - int(RAIO)))

    def _ligar_fundo(self) -> None:
        """Anda só com a janela aberta e sem "reduzir movimento" (aí as partículas ficam paradas, desenhadas uma vez)."""
        if self.campo is None or not self.isVisible() or self.reduzido or movimento_reduzido():
            self.relogio_fundo.stop()
            self.update(self.area_fundo())
            return
        self._ultimo_fundo = time.perf_counter()
        self._janela_fps = (self._ultimo_fundo, 0)
        self.relogio_fundo.start()

    def _passo_fundo(self) -> None:
        if self.campo is None or not self.isVisible() or self.isMinimized():
            self.relogio_fundo.stop()
            return
        agora = time.perf_counter()
        dt, self._ultimo_fundo = agora - self._ultimo_fundo, agora
        self._medir_fundo(agora)
        estado, nivel = ("idle", 0.0)
        if self.estado_eco is not None:
            try:
                estado, nivel = self.estado_eco()
            except Exception:  # a orbe é só a fonte do estado: se falhar, o fundo segue calmo
                pass
        alvo = fundo.quadros_alvo(estado, self.isActiveWindow())
        if self.relogio_fundo.interval() != round(1000 / alvo):
            self.relogio_fundo.setInterval(round(1000 / alvo))
            self._janela_fps = (agora, 0)  # mudou a taxa: começa a medir de novo
        area = self.area_fundo()
        c = self.mapFromGlobal(QCursor.pos())
        cursor = (
            max(-1.0, min(1.0, (c.x() - area.center().x()) / max(1, area.width() / 2))),
            max(-1.0, min(1.0, (c.y() - area.center().y()) / max(1, area.height() / 2))),
        )
        self.quadro_fundo = self.campo.passo(dt, estado, nivel, cursor)
        novos = fundo.regiao(self.quadro_fundo, area)
        # redesenha só onde as partículas estavam e onde estão agora, e não onde um campo opaco as esconde: assim
        # as abas não são repintadas inteiras a cada quadro (era o que mais pesava)
        sujo = fundo.juntar(self._ret_fundo + novos).intersected(area).subtracted(self._regiao_coberta())
        if not sujo.isEmpty():
            self.update(sujo)
        self._ret_fundo = novos

    def _regiao_coberta(self) -> QRegion:
        """Onde há widget opaco (listas, campos de texto, a orbe do grafo): ali as partículas não aparecem."""
        self._tique += 1
        if self._cobertos is None or self._tique % 15 == 0:  # a lista muda com a aba e o layout: relê a cada 0,5 s
            self._cobertos = []
            atual = self.corpo.currentWidget() if self.corpo is not None else None
            if atual is not None:
                for tipo in (QTextEdit, QPlainTextEdit, QListWidget):
                    self._cobertos += [w.viewport() for w in atual.findChildren(tipo)]
                for tipo in (QLineEdit, QComboBox):
                    self._cobertos += atual.findChildren(tipo)
                self._cobertos += [w for w in atual.findChildren(QWidget) if w.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)]
        reg = QRegion()
        for w in self._cobertos:
            try:
                if w.isVisible() and w.width() > 8 and w.height() > 8:
                    # 3 px para dentro: os cantos arredondados e a borda continuam sendo redesenhados
                    reg = reg.united(QRect(w.mapTo(self, QPoint(0, 0)), w.size()).adjusted(3, 3, -3, -3))
            except RuntimeError:  # o widget foi destruído (o painel remontou a tela)
                self._cobertos = None
                return QRegion()
        return reg

    def _medir_fundo(self, agora: float) -> None:
        """Documento, seção 9: se a taxa cair (abaixo de 75% da alvo, a regra dos ~45 de 60), menos partículas."""
        inicio, quadros = self._janela_fps
        quadros += 1
        if agora - inicio >= 3.0:
            alvo = 1000 / max(1, self.relogio_fundo.interval())
            if quadros / (agora - inicio) < alvo * fundo.FRACAO_FPS_MINIMA and self.campo.reduzir():
                self.densidade_caiu = True
                self._ret_fundo = fundo.regiao(self.quadro_fundo, self.area_fundo()) if self.quadro_fundo else []
                self.update(self.area_fundo())
            inicio, quadros = agora, 0
        self._janela_fps = (inicio, quadros)

    def _posicionar_chat(self) -> None:
        if self.corpo is not None:
            self.corpo.setGeometry(1, ALTURA_CABECALHO + 1, max(1, self.width() - 2), max(1, self.height() - ALTURA_CABECALHO - 10))

    def mostrar_aba(self, indice: int) -> None:
        """Abas na ordem: Chat, Grafo, Painel, Bloco. Ao abrir o grafo, ele analisa só o que mudou; o painel lê tudo de novo."""
        if self.corpo is None or indice >= self.corpo.count():
            return
        if indice != self.corpo.currentIndex() and self.corpo.isVisible() and self.captura is None and not movimento_reduzido():
            antiga = self.corpo.grab()  # a aba de agora, que vai sumir por cima da nova
            self.corpo.setCurrentIndex(indice)
            if self._esmaecer is not None:
                try:
                    self._esmaecer.deleteLater()
                except RuntimeError:  # já tinha terminado e se apagado
                    pass
            self._esmaecer = _Esmaecer(self, antiga, self.corpo.geometry())
        self.corpo.setCurrentIndex(indice)
        self._cobertos = None
        for i, b in enumerate(self.abas):
            b.setChecked(i == indice)
        aberta = self.corpo.widget(indice)
        if aberta is not None and aberta is not self.chat:
            aberta.ao_abrir()

    def abrir_nota_no_bloco(self, dia: str) -> None:
        """Clique num dia do cartão do grafo: abre o Bloco direto na página desse dia (AAAA-MM-DD)."""
        from datetime import date

        if self.bloco is None or self.corpo is None:
            return
        try:
            d = date.fromisoformat(dia)
        except ValueError:
            return
        self.mostrar_aba(self.corpo.indexOf(self.bloco))
        self.bloco.abrir_dia(d)

    def abrir_conversa_no_chat(self, conversa_id: str) -> None:
        """Clique numa conversa do cartão do grafo: volta ao Chat direto nela."""
        if self.chat is not None:
            self.mostrar_aba(0)
            self.chat.mostrar_conversa(conversa_id)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._degrade = None
        if self.corpo is not None and self.corpo.isVisible():
            self._posicionar_chat()

    # --- abrir ----------------------------------------------------------------------------
    def retangulo_alvo(self, origem: QRect | None) -> QRect:
        """Centralizado na tela onde está a orbe, sem passar das bordas."""
        ponto = origem.center() if origem else QGuiApplication.primaryScreen().availableGeometry().center()
        tela = (QGuiApplication.screenAt(ponto) or QGuiApplication.primaryScreen()).availableGeometry()
        w, h = min(LARGURA, tela.width() - 80), min(ALTURA, tela.height() - 80)
        return QRect(tela.center().x() - w // 2, tela.center().y() - h // 2, w, h)

    def abrir(self, origem: QRect | None = None) -> None:
        """Mostra a janela. Se já estiver aberta, só a traz para a frente (sem repetir a animação)."""
        if self.isVisible() and self.anim.state() != QVariantAnimation.State.Running:
            self.raise_()
            self.activateWindow()
            return
        alvo = self.retangulo_alvo(origem)
        self._alvo = (alvo.x(), alvo.y(), alvo.width(), alvo.height())
        o = origem or QRect(alvo.center().x() - 24, alvo.center().y() - 24, 48, 48)
        self._origem = (o.x(), o.y(), o.width(), o.height())
        self.reduzido = movimento_reduzido()
        self.fechando = False
        self.anim.stop()
        self.setGeometry(alvo)  # uma vez só: a animação acontece dentro da janela
        self._capturar()
        self.anim.setDuration(DURACAO_FADE_MS if self.reduzido else DURACAO_MS)
        self.anim.setEasingCurve(QEasingCurve.Type.Linear if self.reduzido else QEasingCurve.Type.OutQuart)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.setWindowOpacity(0.0 if self.reduzido else 1.0)
        self._passo(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.anim.start()

    def fechar(self) -> None:
        """Encolhe de volta para a orbe e esconde (X, Esc, fechar pelo sistema)."""
        if not self.isVisible() or self.fechando:
            return
        if movimento_reduzido():
            self.hide()
            return
        self.reduzido = False
        g = self.geometry()
        self._alvo = (g.x(), g.y(), g.width(), g.height())
        o = None
        if self.origem_fechar is not None:
            try:
                o = self.origem_fechar()
            except Exception:  # a orbe é só o destino: sem ela, encolhe para o centro
                o = None
        o = o or QRect(g.center().x() - 24, g.center().y() - 24, 48, 48)
        self._origem = (o.x(), o.y(), o.width(), o.height())
        self.anim.stop()
        self.relogio_fundo.stop()
        self.captura = self.grab()  # como está agora, com o que a pessoa estava vendo
        self._esconder_conteudo()
        self.fechando = True
        self.anim.setDuration(DURACAO_FECHAR_MS)
        self.anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.start()

    def aquecer(self, origem: QRect | None = None) -> None:
        """Com a janela escondida: põe no tamanho de abrir e pinta tudo uma vez, descartando a imagem. A 1ª pintura
        (o Qt polindo cada widget) custa ~100 ms; depois disso a captura do clique leva ~12 ms."""
        if self.isVisible():
            return
        self.setGeometry(self.retangulo_alvo(origem))
        self.winId()  # cria a janela nativa do Windows agora (no 1º show ela custava ~60 ms)
        self._capturar()
        self.captura = None

    def _esconder_conteudo(self) -> None:
        if self.corpo is not None:
            self.corpo.hide()
        for b in self.abas:
            b.hide()

    def _capturar(self) -> None:
        """Pinta a janela pronta (abas, cabeçalho, fundo) numa imagem e esconde os widgets: durante a animação só a
        imagem é desenhada. Funciona com a janela ainda escondida."""
        self.p, self.raio, self.conteudo = 1.0, RAIO, 1.0
        self.captura = None
        if self.corpo is not None:
            self._posicionar_chat()
            self.corpo.show()
            for b in self.abas:
                b.show()
        self.captura = self.grab()
        self._esconder_conteudo()

    def _forma_animada(self) -> QRectF:
        """Retângulo do painel neste ponto da animação, em coordenadas da janela (da orbe até a janela inteira)."""
        ox, oy, ow, oh = self._origem
        ax, ay = self._alvo[0], self._alvo[1]
        x, y, w, h = interpolar((ox - ax, oy - ay, ow, oh), (0.0, 0.0, float(self.width()), float(self.height())), self.p)
        return QRectF(x, y, max(1.0, w), max(1.0, h))

    def _passo(self, valor) -> None:
        self.p = float(valor)
        if self.reduzido:  # só fade: já no tamanho final
            self.conteudo, self.raio = 1.0, RAIO
            self.setWindowOpacity(self.p)
        else:  # círculo da orbe que cresce e vira painel
            self.raio = interpolar((min(self._origem[2], self._origem[3]) / 2,), (RAIO,), self.p)[0]
            self.conteudo = max(0.0, min(1.0, (self.p - 0.15) / 0.6))
        if self.ao_animar is not None:
            try:
                self.ao_animar(1.0 if self.fechando else max(0.0, 1.0 - self.p / ORBE_SOME_ATE))
            except Exception:
                pass
        self.update()

    def _fim_da_animacao(self) -> None:
        self.captura = None
        if self.fechando:
            self.fechando = False
            self.hide()
        else:
            self.p, self.raio, self.conteudo = 1.0, RAIO, 1.0
            self.setWindowOpacity(1.0)
            self._mostrar_chat()
        self._avisar_fim()

    def _avisar_fim(self) -> None:
        if self.ao_animar is not None:
            try:
                self.ao_animar(None)
            except Exception:
                pass

    # --- pintura --------------------------------------------------------------------------
    def _area_fechar(self) -> QRect:
        return QRect(self.width() - ALTURA_CABECALHO, 0, ALTURA_CABECALHO, ALTURA_CABECALHO)

    def paintEvent(self, evento) -> None:
        """Pinta só o que a área suja toca. As partículas pedem pedacinhos a 15-30 quadros/s; com a janela aberta,
        isso vira fundo liso (sem recorte pelo contorno arredondado), e cabeçalho e borda só entram se forem tocados."""
        if self.captura is not None:
            self._pintar_transicao()
            return
        reg = evento.region()
        w, h = self.width(), self.height()
        aberta = self.p >= 1.0 and self.raio == RAIO
        r = int(RAIO) + 2
        cantos = (QRect(0, 0, r, r), QRect(w - r, 0, r, r), QRect(0, h - r, r, r), QRect(w - r, h - r, r, r))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        forma = QPainterPath()
        forma.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.raio, self.raio)
        recortar = not aberta or any(reg.intersects(c) for c in cantos)
        if recortar:
            p.fillPath(forma, QColor(tema.FUNDO))
            p.setClipPath(forma)
        else:
            p.fillRect(self.rect(), QColor(tema.FUNDO))  # o Qt já recorta pela área suja: é barato
        if self.campo is None:
            p.drawPixmap(0, 0, self._fundo_fixo())
        elif self.quadro_fundo is not None and self.conteudo > 0.01:
            fundo.pintar(p, self.quadro_fundo, self.area_fundo(), self.conteudo)
        if not aberta or reg.intersects(QRect(0, 0, w, ALTURA_CABECALHO + 1)):
            self._pintar_cabecalho(p)
        bordas = (QRect(0, 0, w, 2), QRect(0, h - 2, w, 2), QRect(0, 0, 2, h), QRect(w - 2, 0, 2, h))
        if recortar or any(reg.intersects(b) for b in bordas):
            p.setClipping(False)
            p.setPen(QPen(QColor(tema.BORDA_FORTE), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(forma)
        p.end()

    def _pintar_transicao(self) -> None:
        """Durante a animação: o painel (fundo e borda) na forma atual, com a captura do conteúdo dentro."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.reduzido:  # fade: a janela inteira já pronta, a opacidade vem da janela
            p.drawPixmap(0, 0, self.captura)
            p.end()
            return
        ret = self._forma_animada()
        forma = QPainterPath()
        forma.addRoundedRect(ret.adjusted(0.5, 0.5, -0.5, -0.5), self.raio, self.raio)
        presenca = min(1.0, self.p * 3)
        p.setOpacity(presenca)
        p.fillPath(forma, QColor(tema.FUNDO))
        if self.conteudo > 0.0:
            p.save()
            p.setClipPath(forma)
            escala = ESCALA_INICIAL + (1 - ESCALA_INICIAL) * self.p
            p.translate(ret.center())
            p.scale(escala, escala)
            p.translate(-self.width() / 2, -self.height() / 2)
            p.setOpacity(self.conteudo)
            p.drawPixmap(0, 0, self.captura)
            p.restore()
        p.setOpacity(presenca)
        p.setPen(QPen(QColor(tema.BORDA_FORTE), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(forma)
        p.end()

    def _fundo_fixo(self) -> QPixmap:
        """Luz verde suave vinda do alto, ao centro, sobre `FUNDO`. Pintada uma vez por tamanho de janela."""
        if self._degrade is None or self._degrade.size() != self.size():
            pm = QPixmap(self.size())
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            luz = QRadialGradient(self.width() / 2, -self.height() * 0.15, max(self.width(), self.height()) * 0.75)
            luz.setColorAt(0.0, tema.cor(tema.VERDE, tema.BRILHO_FUNDO))
            luz.setColorAt(1.0, tema.cor(tema.VERDE, 0.0))
            q.fillRect(pm.rect(), luz)
            q.end()
            self._degrade = pm
        return self._degrade

    def _pintar_cabecalho(self, p: QPainter) -> None:
        """Bolinha da cor da orbe, nome, linha de baixo e botão de fechar."""
        p.save()
        p.setOpacity(self.conteudo)
        p.setBrush(QColor.fromHslF(self.cfg.orbe_tom / 360, 0.7, 0.5))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(24, ALTURA_CABECALHO // 2 - 6, 12, 12)
        p.setPen(QColor(tema.TEXTO))
        p.setFont(tema.fonte_mono(tema.SUBTITULO, negrito=True))
        p.drawText(QRect(46, 0, 300, ALTURA_CABECALHO), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "Eco")
        p.setPen(QPen(QColor(tema.BORDA), 1))
        p.drawLine(0, ALTURA_CABECALHO, self.width(), ALTURA_CABECALHO)
        cx, cy = self.width() - ALTURA_CABECALHO // 2, ALTURA_CABECALHO // 2
        p.setPen(QPen(QColor(255, 255, 255, 235 if self.sobre_fechar else 130), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx - 6, cy - 6, cx + 6, cy + 6)
        p.drawLine(cx - 6, cy + 6, cx + 6, cy - 6)
        p.restore()

    # --- interação ------------------------------------------------------------------------
    def mouseMoveEvent(self, e) -> None:
        sobre = self._area_fechar().contains(e.position().toPoint())
        if sobre != self.sobre_fechar:
            self.sobre_fechar = sobre
            self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        ponto = e.position().toPoint()
        if self._area_fechar().contains(ponto):
            self.fechar()
        elif ponto.y() < ALTURA_CABECALHO and self.windowHandle() is not None:
            self.windowHandle().startSystemMove()  # arrastar pelo cabeçalho

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.fechar()

    def closeEvent(self, e) -> None:
        e.ignore()  # fechar só esconde; a Eco continua rodando (para sair: menu da orbe ou da bandeja)
        self.fechar()

    def hideEvent(self, e) -> None:
        animando = self.anim.state() == QVariantAnimation.State.Running
        self.anim.stop()
        self.relogio_fundo.stop()
        if self.captura is not None or animando:  # escondida no meio da animação: a orbe volta a desenhar
            self.captura = None
            self.fechando = False
            self._avisar_fim()
        super().hideEvent(e)
