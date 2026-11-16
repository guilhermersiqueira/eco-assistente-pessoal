"""Componentes do sistema de design da Eco (documento "Eco — Design"), desenhados com os tokens de `tema.py`.

- `Cartao`: painel com contorno grafite. Em foco (mouse em cima ou teclado dentro), sobe para `ELEVADO` e um
  segmento de luz verde percorre a borda (uma volta a cada 6 s). Só UM cartão tem o fluxo por vez, e o
  relógio do fluxo para quando nenhum cartão está em foco ou a janela está escondida.
- `Caixa`: caixa de marcar de uma linha, com o título em destaque e os detalhes ("  ·  ") em tom secundário.
- `Botao` e `Aba`: estilo HUD, com um canto cortado (decisão do dono em 11/11/2026: canto cortado só em abas e
  botões; cartões arredondados em 12 px; campos e caixas com canto de 4 px).
- `Linha`: linha de lista com partes de estilos diferentes (hora em mono, título, detalhe), numa linha só.

Com "reduzir animações" ligado (ver `principal.movimento_reduzido`), nada se move: o cartão em foco só ganha
a borda acesa.
"""

from __future__ import annotations

import atexit
import time

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from eco.visual import tema

SEPARADOR = "  ·  "
ALTURA_LINHA = 28


def _movimento_reduzido() -> bool:
    from eco.visual.principal import movimento_reduzido  # import tardio: principal importa as abas

    return movimento_reduzido()


class _RelogioDoFluxo:
    """Um relógio só para o fluxo de luz. Anda apenas enquanto existe um cartão em foco e visível."""

    def __init__(self) -> None:
        self.timer: QTimer | None = None
        self.ativo: Cartao | None = None

    def fase(self) -> float:
        return (time.perf_counter() % tema.FLUXO_VOLTA_S) / tema.FLUXO_VOLTA_S

    def definir(self, cartao: "Cartao | None") -> None:
        anterior, self.ativo = self.ativo, cartao
        if anterior is not None and anterior is not cartao:
            try:
                anterior.update()
            except RuntimeError:  # o cartão antigo já foi destruído (o painel remontou a tela)
                pass
        if cartao is None or cartao.reduzido:
            if self.timer is not None:
                try:
                    self.timer.stop()
                except RuntimeError:  # fechando o programa: o Qt já destruiu o relógio
                    self.timer = None
            return
        if self.timer is None:
            self.timer = QTimer()
            self.timer.setInterval(round(1000 / tema.FLUXO_QUADROS_S))
            self.timer.timeout.connect(self._tique)
            # o relógio é global: se sobrar vivo quando o Qt é desmontado ao fechar o programa, derruba o processo
            atexit.register(self.encerrar)
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.encerrar)
        self.timer.start()

    def encerrar(self) -> None:
        """Para e solta o relógio antes de o Qt ser desmontado (fechar a Eco, fim dos testes)."""
        self.ativo = None
        timer, self.timer = self.timer, None
        if timer is not None:
            try:
                timer.stop()
                timer.timeout.disconnect()
                timer.deleteLater()
            except (RuntimeError, TypeError):  # já destruído pelo Qt
                pass

    def _tique(self) -> None:
        c = self.ativo
        try:
            if c is None or not c.isVisible() or not c.window().isVisible():
                self.definir(None)
                return
            c.update(c.regiao_da_borda())  # só a moldura: o conteúdo do cartão não é redesenhado
        except RuntimeError:
            self.ativo = None
            if self.timer is not None:
                self.timer.stop()


RELOGIO = _RelogioDoFluxo()


class Cartao(QFrame):
    """Painel com título em estilo HUD. `corpo` é o layout onde entra o conteúdo."""

    def __init__(self, titulo: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.reduzido = _movimento_reduzido()
        self.em_foco = False
        self.foco = 0.0  # 0 = normal, 1 = em foco (anima em HOVER_MS)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(tema.HOVER_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._ao_animar)

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(16, 14, 16, 16)
        raiz.setSpacing(8)
        self.cabecalho = QHBoxLayout()
        self.cabecalho.setContentsMargins(14, 0, 0, 0)  # espaço da marca HUD desenhada no paintEvent
        self.cabecalho.setSpacing(8)
        self.titulo = QLabel(titulo)
        self.titulo.setFont(tema.fonte_mono(tema.ROTULO, negrito=True, maiusculas=True))
        self.titulo.setStyleSheet(f"color: {tema.TEXTO_2}; background: transparent;")
        self.contador = QLabel("")
        self.contador.setObjectName("contador")
        self.contador.setStyleSheet("background: transparent;")
        self.cabecalho.addWidget(self.titulo)
        self.cabecalho.addWidget(self.contador)
        self.cabecalho.addStretch(1)
        raiz.addLayout(self.cabecalho)
        self.corpo = QVBoxLayout()
        self.corpo.setContentsMargins(0, 0, 0, 0)
        self.corpo.setSpacing(2)
        raiz.addLayout(self.corpo)
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._foco_do_teclado)

    # --- foco ---------------------------------------------------------------------------------------------
    def definir_contador(self, texto: str) -> None:
        self.contador.setText(texto)

    def _foco_do_teclado(self, _antigo, novo) -> None:
        try:
            dentro = novo is not None and (novo is self or self.isAncestorOf(novo))
            self._definir_foco(dentro or self.underMouse())
        except RuntimeError:  # sinal chegou depois de o cartão ser destruído
            pass

    def enterEvent(self, e) -> None:
        self._definir_foco(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        tem_teclado = QApplication.focusWidget() is not None and self.isAncestorOf(QApplication.focusWidget())
        self._definir_foco(tem_teclado)
        super().leaveEvent(e)

    def hideEvent(self, e) -> None:
        if RELOGIO.ativo is self:
            RELOGIO.definir(None)
        super().hideEvent(e)

    def _definir_foco(self, ligado: bool) -> None:
        if ligado == self.em_foco:
            return
        self.em_foco = ligado
        if ligado:
            RELOGIO.definir(self)
        elif RELOGIO.ativo is self:
            RELOGIO.definir(None)
        self._anim.stop()
        if self.reduzido:
            self._ao_animar(1.0 if ligado else 0.0)
            return
        self._anim.setStartValue(self.foco)
        self._anim.setEndValue(1.0 if ligado else 0.0)
        self._anim.start()

    def _ao_animar(self, valor) -> None:
        self.foco = float(valor)
        self.update()

    # --- pintura ------------------------------------------------------------------------------------------
    def regiao_da_borda(self) -> QRegion:
        """Moldura de 8 px: o que o fluxo de luz precisa redesenhar a cada quadro."""
        return QRegion(self.rect()) - QRegion(self.rect().adjusted(8, 8, -8, -8))

    def _forma(self) -> QPainterPath:
        forma = QPainterPath()
        forma.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), tema.RAIO_CARTAO, tema.RAIO_CARTAO)
        return forma

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        forma = self._forma()
        fundo = tema.misturar(tema.SUPERFICIE, tema.ELEVADO, self.foco)
        fundo.setAlphaF(tema.OPACIDADE_CARTAO + (1 - tema.OPACIDADE_CARTAO) * self.foco)
        p.fillPath(forma, fundo)
        p.setPen(QPen(tema.misturar(tema.BORDA, tema.BORDA_FORTE, self.foco * 0.6), 1))
        p.drawPath(forma)

        # marca HUD ao lado do título: quadradinho que acende com o cartão
        g = self.titulo.geometry()
        cy = g.center().y() + 1
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tema.misturar(tema.BORDA_FORTE, tema.VERDE_LUZ, self.foco))
        p.drawRect(QRectF(16, cy - 3, 6, 6))
        # marcações técnicas no canto superior direito
        p.setPen(QPen(tema.misturar(tema.BORDA, tema.BORDA_FORTE, self.foco), 1))
        x = self.width() - 18
        for i in range(3):
            p.drawLine(QPointF(x - i * 5, 14), QPointF(x - i * 5, 14 + (6 if i == 0 else 3)))

        # o fluxo só se a área a redesenhar toca a moldura (as partículas de fundo pedem pedacinhos no meio do cartão)
        if self.foco > 0.01 and RELOGIO.ativo is self and e.region().intersects(self.regiao_da_borda()):
            self._pintar_fluxo(p, forma)
        p.end()

    def _pintar_fluxo(self, p: QPainter, forma: QPainterPath) -> None:
        """Segmento de luz que dá a volta na borda: cauda apagada, cabeça acesa."""
        if self.reduzido:  # sem movimento: a borda inteira fica acesa
            p.setPen(QPen(tema.cor(tema.VERDE_LUZ, 0.7 * self.foco), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(forma)
            return
        fase, comprimento, passos = RELOGIO.fase(), 0.16, 22
        p.setBrush(Qt.BrushStyle.NoBrush)
        anterior = forma.pointAtPercent(fase % 1.0)
        for i in range(1, passos + 1):
            atual = forma.pointAtPercent((fase + comprimento * i / passos) % 1.0)
            forca = (i / passos) ** 2 * self.foco
            p.setPen(QPen(tema.cor(tema.VERDE_LUZ, 0.22 * forca), 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))  # halo
            p.drawLine(anterior, atual)
            p.setPen(QPen(tema.cor(tema.VERDE_LUZ, 0.95 * forca), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(anterior, atual)
            anterior = atual


class Caixa(QCheckBox):
    """Caixa de marcar de uma linha. O texto é "Título  ·  detalhe  ·  detalhe": o título vai em destaque e os
    detalhes em tom secundário. Estados (documento, seção 6): normal, concluído (riscado e apagado) e atrasado.

    A cor nunca é o único sinal: concluído também é riscado, importante leva "★" e atrasado traz a palavra ou
    fica sob o grupo "Atrasadas".
    """

    LADO = 16

    def __init__(self, texto: str, atrasada: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(texto, parent)
        self.atrasada = atrasada
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(texto)

    def sizeHint(self) -> QSize:
        return QSize(120, ALTURA_LINHA)

    def minimumSizeHint(self) -> QSize:
        return QSize(60, ALTURA_LINHA)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        feita = self.isChecked()
        sobre = self.underMouse()
        cy = self.height() / 2
        caixa = QRectF(1, cy - self.LADO / 2, self.LADO, self.LADO)
        borda = tema.VERDE_LUZ if (feita or sobre or self.hasFocus()) else tema.BORDA_FORTE
        p.setPen(QPen(QColor(borda), 2 if self.hasFocus() else 1))
        p.setBrush(QColor(tema.VERDE_LUZ if feita else tema.FUNDO))
        p.drawRoundedRect(caixa, tema.RAIO_HUD, tema.RAIO_HUD)
        if feita:  # o "visto": sinal que não depende da cor
            p.setPen(QPen(QColor(tema.FUNDO), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            visto = QPainterPath(QPointF(caixa.left() + 4, cy + 0.5))
            visto.lineTo(caixa.left() + 7, cy + 3.5)
            visto.lineTo(caixa.left() + 12, cy - 3)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(visto)

        partes = self.text().split(SEPARADOR)
        x, fim = self.LADO + 10, self.width() - 4
        for i, parte in enumerate(partes):
            if x >= fim - 12:
                break
            if i == 0:
                fonte = tema.fonte_texto(tema.CORPO)
                fonte.setStrikeOut(feita)
                cor = tema.TEXTO_APAGADO if feita else tema.ALERTA if self.atrasada else tema.TEXTO
            else:
                fonte = tema.fonte_mono(tema.ROTULO)
                destaque = parte.startswith("★") and not feita
                cor = tema.TEXTO_APAGADO if feita else tema.CIANO if destaque else tema.ALERTA if parte == "atrasada" else tema.TEXTO_2
            p.setFont(fonte)
            fm = QFontMetrics(fonte)
            texto = fm.elidedText(parte, Qt.TextElideMode.ElideRight, int(fim - x))
            p.setPen(QColor(cor))
            p.drawText(QRectF(x, 0, fim - x, self.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, texto)
            x += fm.horizontalAdvance(texto) + 14
        p.end()


class Linha(QLabel):
    """Linha de lista (evento, e-mail). `partes` é [(texto, estilo)]; estilos: "hora", "texto", "discreto",
    "direita" (mono, encostado à direita). `text()` devolve a frase inteira, para leitores de tela e testes."""

    LARGURA_HORA = 76

    def __init__(self, partes: list[tuple[str, str]], texto: str, parent: QWidget | None = None) -> None:
        super().__init__(texto, parent)
        self.partes = [(t, e) for t, e in partes if t]
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(texto)

    def sizeHint(self) -> QSize:
        return QSize(120, ALTURA_LINHA)

    def minimumSizeHint(self) -> QSize:
        return QSize(60, ALTURA_LINHA)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        alinhar = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        x, fim = 2.0, float(self.width() - 4)
        for texto, estilo in self.partes:
            if estilo == "direita":  # reserva o espaço antes de desenhar o resto
                fonte = tema.fonte_mono(tema.ROTULO)
                larg = QFontMetrics(fonte).horizontalAdvance(texto)
                p.setFont(fonte)
                p.setPen(QColor(tema.TEXTO_2))
                p.drawText(QRectF(fim - larg, 0, larg, self.height()), alinhar, texto)
                fim -= larg + 12
        for texto, estilo in self.partes:
            if estilo == "direita" or x >= fim - 12:
                continue
            if estilo == "hora":
                fonte, cor, fixa = tema.fonte_mono(12), tema.VERDE_LUZ, self.LARGURA_HORA
            elif estilo == "discreto":
                fonte, cor, fixa = tema.fonte_texto(tema.CORPO), tema.TEXTO_2, 0
            else:
                fonte, cor, fixa = tema.fonte_texto(tema.CORPO), tema.TEXTO, 0
            p.setFont(fonte)
            fm = QFontMetrics(fonte)
            mostrado = fm.elidedText(texto, Qt.TextElideMode.ElideRight, int(fim - x))
            p.setPen(QColor(cor))
            p.drawText(QRectF(x, 0, fim - x, self.height()), alinhar, mostrado)
            x += max(fixa, fm.horizontalAdvance(mostrado) + 12)
        p.end()


class Botao(QPushButton):
    """Botão HUD: canto inferior direito cortado. `tipo`: "normal", "principal" (ação da tela), "mini" (ação de
    linha, discreta) ou "perigo" (mini que fica vermelho no hover: apagar). Trocar o `objectName` para "principal"
    ou "parar" (vermelho, interromper) muda o visual na hora: basta chamar `update()`."""

    def __init__(self, texto: str, tipo: str = "normal", parent: QWidget | None = None) -> None:
        super().__init__(texto, parent)
        self.tipo = tipo
        self.setObjectName("principal" if tipo == "principal" else "mini" if tipo in ("mini", "perigo") else "")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def _mini(self) -> bool:
        return self.tipo in ("mini", "perigo")

    def _fonte(self):
        return tema.fonte_mono(tema.ROTULO) if self._mini() else tema.fonte_texto(tema.CORPO, negrito=self.objectName() in ("principal", "parar"))

    def sizeHint(self) -> QSize:
        larg = QFontMetrics(self._fonte()).horizontalAdvance(self.text())
        return QSize(larg + (18 if self._mini() else 28), 22 if self._mini() else 30)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        corte = 5.0 if self._mini() else 7.0
        forma = QPainterPath(r.topLeft())
        forma.lineTo(r.topRight())
        forma.lineTo(r.right(), r.bottom() - corte)
        forma.lineTo(r.right() - corte, r.bottom())
        forma.lineTo(r.bottomLeft())
        forma.closeSubpath()
        sobre, foco, ativo = self.underMouse() and self.isEnabled(), self.hasFocus(), self.isEnabled()
        if self._mini():
            fundo = tema.cor(tema.ELEVADO) if sobre else tema.cor(tema.ELEVADO, 0.0)
            borda = tema.VERDE_LUZ if foco else (tema.ALERTA if self.tipo == "perigo" else tema.BORDA_FORTE) if sobre else None
            texto = (tema.ALERTA if self.tipo == "perigo" else tema.TEXTO) if (sobre or foco) else tema.TEXTO_2
        elif self.objectName() == "principal":
            fundo = tema.cor("#248C58" if sobre else tema.VERDE)
            borda, texto = tema.VERDE_LUZ, tema.TEXTO
        elif self.objectName() == "parar":  # ação em andamento que pode ser interrompida
            fundo = tema.cor(tema.ALERTA_FUNDO)
            borda, texto = tema.ALERTA, tema.TEXTO
        else:
            fundo = tema.cor(tema.FUNDO if self.isDown() else tema.ELEVADO if sobre else tema.SUPERFICIE)
            borda, texto = tema.VERDE_LUZ if (sobre or foco) else tema.BORDA_FORTE, tema.TEXTO
        if not ativo:
            fundo, borda, texto = tema.cor(tema.SUPERFICIE, 0.0 if self._mini() else 1.0), None if self._mini() else tema.BORDA, tema.TEXTO_APAGADO
        p.fillPath(forma, fundo)
        if borda is not None:
            p.setPen(QPen(QColor(borda), 2 if (foco and ativo) else 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(forma)
        p.setFont(self._fonte())
        p.setPen(QColor(texto))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class Aba(QPushButton):
    """Aba do topo em estilo HUD: rótulo em mono; a ativa tem canto cortado, marcação técnica e o fluxo de luz."""

    def __init__(self, texto: str, parent: QWidget | None = None) -> None:
        super().__init__(texto, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.reduzido = _movimento_reduzido()
        self._timer = QTimer(self)
        self._timer.setInterval(round(1000 / tema.FLUXO_QUADROS_S))
        self._timer.timeout.connect(self.update)
        self.toggled.connect(self._ligar_fluxo)

    def _fonte(self):
        return tema.fonte_mono(12, negrito=True, maiusculas=True)

    def sizeHint(self) -> QSize:
        return QSize(QFontMetrics(self._fonte()).horizontalAdvance(self.text()) + 40, 30)

    def _ligar_fluxo(self, _=None) -> None:
        if self.isChecked() and self.isVisible() and not self.reduzido:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._ligar_fluxo()

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        ativa, sobre = self.isChecked(), self.underMouse()
        if ativa:
            corte = 8.0
            forma = QPainterPath(r.topLeft())
            forma.lineTo(r.right() - corte, r.top())
            forma.lineTo(r.right(), r.top() + corte)
            forma.lineTo(r.bottomRight())
            forma.lineTo(r.bottomLeft())
            forma.closeSubpath()
            p.fillPath(forma, tema.cor(tema.ELEVADO))
            p.setPen(QPen(QColor(tema.BORDA_FORTE), 1))
            p.drawPath(forma)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(tema.VERDE_LUZ))
            p.drawRect(QRectF(r.left() + 8, r.center().y() - 2, 4, 4))  # marcação: aba ativa
            p.setBrush(Qt.BrushStyle.NoBrush)
            if self.reduzido:
                p.setPen(QPen(tema.cor(tema.VERDE_LUZ, 0.7), 1.2))
                p.drawPath(forma)
            else:
                fase, passos = RELOGIO.fase(), 14
                anterior = forma.pointAtPercent(fase)
                for i in range(1, passos + 1):
                    atual = forma.pointAtPercent((fase + 0.22 * i / passos) % 1.0)
                    p.setPen(QPen(tema.cor(tema.VERDE_LUZ, 0.95 * (i / passos) ** 2), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                    p.drawLine(anterior, atual)
                    anterior = atual
        elif self.hasFocus():
            p.setPen(QPen(QColor(tema.VERDE_LUZ), 1))
            p.drawRect(r)
        p.setFont(self._fonte())
        p.setPen(QColor(tema.TEXTO if (ativa or sobre) else tema.TEXTO_2))
        p.drawText(self.rect().adjusted(8 if ativa else 0, 0, 0, 0), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


def rotulo(texto: str, nome: str = "rotulo") -> QLabel:
    """Rótulo pequeno em mono (grupo de uma lista, legenda)."""
    r = QLabel(texto)
    r.setObjectName(nome)
    r.setStyleSheet("background: transparent; margin-top: 6px;")
    return r


__all__ = ["Aba", "Botao", "Caixa", "Cartao", "Linha", "RELOGIO", "SEPARADOR", "rotulo"]
