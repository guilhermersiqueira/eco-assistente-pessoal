"""Caixas de diálogo no estilo HUD do sistema de design (substituem QMessageBox e QInputDialog).

A API imita a do Qt para trocar sem mexer na lógica:
- `perguntar(pai, titulo, texto)` -> bool (sim/não). `destrutivo=True` deixa o "Sim" vermelho e o foco em "Não",
  para um Enter sem querer não apagar nada.
- `avisar(pai, titulo, texto)` -> None (uma mensagem com "OK").
- `texto(pai, titulo, rotulo, valor="")` -> (texto, ok), uma linha.
- `texto_longo(pai, titulo, rotulo, valor="")` -> (texto, ok), várias linhas.
- `escolher(pai, titulo, texto, opcoes)` -> valor escolhido ou None; `opcoes` é [(rótulo, valor)].

Cores, fontes e botões vêm de `tema.py` e `componentes.py`. Nos testes, troque `_executar` para responder sem abrir
janela.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from eco.visual import tema
from eco.visual.componentes import Botao


class CaixaHud(QDialog):
    """Janela sem moldura do sistema: contorno grafite, marca HUD ao lado do título e botões com canto cortado."""

    def __init__(self, pai: QWidget | None, titulo: str, texto: str = "", largura: int = 440) -> None:
        super().__init__(pai)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setWindowTitle(titulo)
        self.setStyleSheet(tema.folha())
        self.setMinimumWidth(largura)
        self.raiz = QVBoxLayout(self)
        self.raiz.setContentsMargins(22, 18, 22, 18)
        self.raiz.setSpacing(12)
        cab = QLabel(titulo)
        cab.setFont(tema.fonte_mono(tema.ROTULO, negrito=True, maiusculas=True))
        cab.setStyleSheet(f"color: {tema.TEXTO_2}; background: transparent; padding-left: 14px;")
        self.raiz.addWidget(cab)
        if texto:
            corpo = QLabel(texto)
            corpo.setWordWrap(True)
            corpo.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            corpo.setStyleSheet("background: transparent;")
            self.raiz.addWidget(corpo)
        self.botoes = QHBoxLayout()
        self.botoes.setSpacing(8)
        self.botoes.addStretch(1)
        self._cab = cab

    def adicionar(self, rotulo: str, tipo: str = "normal", ao_clicar=None, padrao: bool = False) -> Botao:
        b = Botao(rotulo, tipo)
        if tipo == "parar":  # vermelho: ação destrutiva (o Botao reconhece pelo nome de estilo)
            b.setObjectName("parar")
        b.setAutoDefault(padrao)
        b.setDefault(padrao)
        b.clicked.connect(ao_clicar or self.accept)
        self.botoes.addWidget(b)
        return b

    def fechar_botoes(self) -> None:
        self.raiz.addLayout(self.botoes)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        forma = QPainterPath()
        forma.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), tema.RAIO_CARTAO, tema.RAIO_CARTAO)
        p.fillPath(forma, tema.cor(tema.ELEVADO))
        p.setPen(QPen(tema.cor(tema.BORDA_FORTE), 1))
        p.drawPath(forma)
        g = self._cab.geometry()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tema.cor(tema.VERDE_LUZ))
        p.drawRect(QRectF(22, g.center().y() - 2, 6, 6))  # marca HUD, como nos cartões
        p.end()


def _executar(d: QDialog) -> bool:
    """Ponto único de execução (os testes trocam esta função para não abrir janela)."""
    return d.exec() == QDialog.DialogCode.Accepted


def perguntar(pai: QWidget | None, titulo: str, texto: str, sim: str = "Sim", nao: str = "Não", destrutivo: bool = False) -> bool:
    d = CaixaHud(pai, titulo, texto)
    d.adicionar(nao, "normal", d.reject, padrao=destrutivo)
    d.adicionar(sim, "parar" if destrutivo else "principal", d.accept, padrao=not destrutivo)
    d.fechar_botoes()
    return _executar(d)


def avisar(pai: QWidget | None, titulo: str, texto: str) -> None:
    d = CaixaHud(pai, titulo, texto)
    d.adicionar("OK", "principal", d.accept, padrao=True)
    d.fechar_botoes()
    _executar(d)


def _entrada(pai: QWidget | None, titulo: str, rotulo: str, campo: QWidget, ler, por_cima: bool = False) -> tuple[str, bool]:
    d = CaixaHud(pai, titulo, rotulo, largura=480)
    if por_cima:  # pedido vindo da voz: aparece na frente de tudo, mesmo com a janela da Eco fechada
        d.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    d.raiz.addWidget(campo)
    d.adicionar("Cancelar", "normal", d.reject)
    d.adicionar("OK", "principal", d.accept, padrao=not isinstance(campo, QPlainTextEdit))
    d.fechar_botoes()
    campo.setFocus()
    ok = _executar(d)
    return (ler(campo) if ok else ""), ok


def texto(pai: QWidget | None, titulo: str, rotulo: str, valor: str = "", por_cima: bool = False) -> tuple[str, bool]:
    campo = QLineEdit(valor)
    campo.selectAll()
    return _entrada(pai, titulo, rotulo, campo, lambda c: c.text(), por_cima)


def texto_longo(pai: QWidget | None, titulo: str, rotulo: str, valor: str = "") -> tuple[str, bool]:
    campo = QPlainTextEdit(valor)
    campo.setFixedHeight(140)
    return _entrada(pai, titulo, rotulo, campo, lambda c: c.toPlainText())


def escolher(pai: QWidget | None, titulo: str, texto: str, opcoes: list[tuple[str, str]], cancelar: str = "Cancelar") -> str | None:
    d = CaixaHud(pai, titulo, texto, largura=520)
    escolhido: list[str] = []
    d.adicionar(cancelar, "normal", d.reject)
    for rotulo, valor in opcoes:
        d.adicionar(rotulo, "principal" if valor == opcoes[0][1] else "normal", lambda _=False, v=valor: (escolhido.append(v), d.accept()))
    d.fechar_botoes()
    return escolhido[0] if _executar(d) and escolhido else None
