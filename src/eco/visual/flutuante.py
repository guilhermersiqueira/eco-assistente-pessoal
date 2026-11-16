"""Janela flutuante (Qt) para código e texto longo: fica por cima das outras, com "Copiar" em cada bloco de código.

Abre sozinha quando uma resposta tem código ou é longa (por voz ou pela caixa de texto). Não guarda nada: o que vai para o
histórico é decidido pela conversa (só entra se ela for guardada). Fechar só esconde; a próxima resposta reaproveita a janela.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from eco.chat.armazenamento import COR_PADRAO
from eco.chat.render import markdown_para_html
from eco.visual import tema
from eco.visual.chat import ESTILO
from eco.visual.componentes import Botao


class JanelaFlutuante(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Eco")
        self.setStyleSheet(ESTILO + f"QWidget#fundo {{ background: {tema.ELEVADO}; }}")
        self.setObjectName("fundo")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.resize(640, 520)
        self.texto = ""
        self._codigos: list[str] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self.vista = QTextBrowser()
        self.vista.setOpenLinks(False)
        self.vista.anchorClicked.connect(self._link)
        lay.addWidget(self.vista, 1)
        base = QHBoxLayout()
        self.aviso = QLabel("")
        self.aviso.setObjectName("discreto")
        base.addWidget(self.aviso, 1)
        self.btn_copiar = Botao("Copiar tudo")
        self.btn_copiar.clicked.connect(lambda: self._copiar(self.texto, "Texto copiado."))
        base.addWidget(self.btn_copiar)
        lay.addLayout(base)

    def mostrar(self, texto: str) -> None:
        self.texto = texto
        self._codigos = []
        self.vista.setHtml("<html><body>" + markdown_para_html(texto, self._codigos, COR_PADRAO) + "</body></html>")
        self.aviso.setText("")
        self.show()
        self.raise_()

    def _copiar(self, texto: str, aviso: str) -> None:
        QGuiApplication.clipboard().setText(texto)
        self.aviso.setText(aviso)
        QTimer.singleShot(2500, lambda: self.aviso.setText("") if self.aviso.text() == aviso else None)

    def _link(self, url) -> None:
        alvo = url.toString()
        if alvo.startswith("copiar:"):
            try:
                self._copiar(self._codigos[int(alvo.split(":", 1)[1])], "Código copiado.")
            except (ValueError, IndexError):
                pass

    def closeEvent(self, e) -> None:
        e.ignore()
        self.hide()
