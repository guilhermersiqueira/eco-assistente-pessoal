"""Aba Bloco (Qt): o bloco de notas do dono, uma página por dia, editável.

Cada página é o arquivo `notas/AAAA-MM-DD.md` da pasta de dados. A Eco acrescenta linhas a ele quando o dono diz "anota no bloco";
aqui o dono lê e edita a página inteira. Salva sozinho (logo depois de parar de digitar, ao trocar de dia e ao esconder a aba).
Se a Eco anotar por voz enquanto a página está aberta, a anotação aparece sozinha (ou é mantida no fim ao salvar, se o dono
estiver digitando). O grafo lê essas páginas e liga os assuntos que se repetem em vários dias.
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from eco.chat.armazenamento import _sem_acento
from eco.config import Config
from eco.ferramentas import notas
from eco.visual import tema
from eco.visual.chat import ESTILO
from eco.visual.componentes import Botao, Cartao

DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
SALVAR_APOS_MS = 800
SONDAR_A_CADA_MS = 2000


def rotulo_do_dia(dia: date, hoje: date) -> str:
    base = f"{DIAS[dia.weekday()]}, {dia:%d/%m/%Y}"
    return f"Hoje · {base}" if dia == hoje else base


class PainelBloco(QWidget):
    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.pasta = cfg.pasta_dados
        self.dia: date | None = None
        self.base = ""  # o que a página tinha no arquivo quando foi carregada ou salva pela última vez
        self.sujo = False  # há texto digitado ainda não salvo
        self._dias_vistos: tuple[date, ...] = ()
        # a página é texto longo: largura de leitura e entrelinha vêm do documento de design
        self.setStyleSheet(ESTILO + f"QPlainTextEdit {{ background: {tema.SUPERFICIE}; padding: 14px 18px; font-size: {tema.CORPO + 1}px; }}")

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 16, 20, 12)
        barra = QHBoxLayout()
        titulo = QLabel("Bloco de notas")
        titulo.setObjectName("titulo")
        barra.addWidget(titulo)
        self.status = QLabel("")
        self.status.setObjectName("discreto")
        barra.addWidget(self.status, 1)
        self.btn_hoje = Botao("Hoje")
        self.btn_hoje.clicked.connect(lambda: self.abrir_dia(date.today()))
        barra.addWidget(self.btn_hoje)
        raiz.addLayout(barra)

        corpo = QHBoxLayout()
        corpo.setSpacing(14)
        dias = Cartao("Dias")
        self.busca = QLineEdit()
        self.busca.setPlaceholderText("Buscar nas notas…")
        self.busca.textChanged.connect(lambda _: self._montar_lista())
        self.lista = QListWidget()
        self.lista.setFixedWidth(230)
        self.lista.currentItemChanged.connect(self._item_escolhido)
        dias.corpo.addWidget(self.busca)
        dias.corpo.addWidget(self.lista, 1)
        corpo.addWidget(dias)
        pagina = Cartao("Página")
        self.titulo_dia = QLabel("")
        self.titulo_dia.setObjectName("subtitulo")
        self.titulo_dia.setStyleSheet("background: transparent;")
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Escreva aqui. A Eco também anota nesta página quando você diz “anota no bloco”.")
        self.editor.textChanged.connect(self._alterado)
        pagina.corpo.addWidget(self.titulo_dia)
        pagina.corpo.addWidget(self.editor, 1)
        corpo.addWidget(pagina, 1)
        raiz.addLayout(corpo, 1)

        self._salvar = QTimer(self)
        self._salvar.setSingleShot(True)
        self._salvar.setInterval(SALVAR_APOS_MS)
        self._salvar.timeout.connect(self.salvar_agora)
        self._sondar_timer = QTimer(self)
        self._sondar_timer.setInterval(SONDAR_A_CADA_MS)
        self._sondar_timer.timeout.connect(self._sondar)
        self._sondar_timer.start()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.salvar_agora)

    # --- abrir ---------------------------------------------------------------------------------------------
    def ao_abrir(self) -> None:
        self._montar_lista()
        if self.dia is None:
            self.abrir_dia(date.today())
        else:
            self._sondar()

    def abrir_dia(self, dia: date) -> None:
        """Mostra a página do dia (salvando antes a que estava aberta)."""
        self.salvar_agora()
        self.dia = dia
        self._carregar(notas.ler_dia(self.pasta, dia))
        self.titulo_dia.setText(rotulo_do_dia(dia, date.today()))
        self._montar_lista()
        self.status.setText("")

    def _carregar(self, texto: str) -> None:
        """Põe o texto da página no editor sem contar como edição, mantendo cursor e rolagem."""
        cursor = self.editor.textCursor().position()
        rolagem = self.editor.verticalScrollBar().value()
        self.editor.blockSignals(True)
        self.editor.setPlainText(texto)
        self.editor.blockSignals(False)
        c = self.editor.textCursor()
        c.setPosition(min(cursor, len(texto)))
        self.editor.setTextCursor(c)
        self.editor.verticalScrollBar().setValue(rolagem)
        self.base = texto
        self.sujo = False

    # --- lista de dias -------------------------------------------------------------------------------------
    def _dias(self) -> list[date]:
        dias = notas.dias_com_nota(self.pasta)
        hoje = date.today()
        if hoje not in dias:
            dias.insert(0, hoje)  # hoje sempre existe, mesmo vazio
        if self.dia is not None and self.dia not in dias:
            dias.append(self.dia)
            dias.sort(reverse=True)
        return dias

    def _montar_lista(self) -> None:
        termo = _sem_acento(self.busca.text().strip())
        dias = self._dias()
        self._dias_vistos = tuple(notas.dias_com_nota(self.pasta))
        if termo:
            dias = [d for d in dias if termo in _sem_acento(notas.ler_dia(self.pasta, d))]
        self.lista.blockSignals(True)
        self.lista.clear()
        hoje = date.today()
        for d in dias:
            item = QListWidgetItem(rotulo_do_dia(d, hoje))
            item.setData(Qt.ItemDataRole.UserRole, d)
            self.lista.addItem(item)
            if d == self.dia:
                self.lista.setCurrentItem(item)
        self.lista.blockSignals(False)

    def _item_escolhido(self, item: QListWidgetItem | None, _anterior=None) -> None:
        if item is not None and item.data(Qt.ItemDataRole.UserRole) != self.dia:
            self.abrir_dia(item.data(Qt.ItemDataRole.UserRole))

    # --- salvar --------------------------------------------------------------------------------------------
    def _alterado(self) -> None:
        self.sujo = True
        self.status.setText("Editando…")
        self._salvar.start()

    def salvar_agora(self) -> None:
        self._salvar.stop()
        if not self.sujo or self.dia is None:
            return
        digitado = self.editor.toPlainText()
        try:
            final = notas.salvar_dia(self.pasta, self.dia, digitado, self.base)
        except OSError as erro:
            self.status.setText(f"Não consegui salvar: {erro}")
            return
        self.base, self.sujo = final, False
        if final.strip() != digitado.strip():  # a Eco acrescentou algo por voz enquanto digitava, ou o cabeçalho foi recolocado
            self._carregar(final)
        self.status.setText(f"Salvo às {datetime.now():%H:%M}")
        if tuple(notas.dias_com_nota(self.pasta)) != self._dias_vistos:
            self._montar_lista()

    def _sondar(self) -> None:
        """A cada 2 s com a aba visível: anotação nova (voz, outro aparelho pelo OneDrive) aparece sozinha."""
        if not self.isVisible() or self.dia is None:
            return
        if not self.sujo:
            atual = notas.ler_dia(self.pasta, self.dia)
            if atual != self.base:
                self._carregar(atual)
                self.status.setText(f"Atualizado às {datetime.now():%H:%M}")
        if tuple(notas.dias_com_nota(self.pasta)) != self._dias_vistos:
            self._montar_lista()

    def hideEvent(self, e) -> None:
        self.salvar_agora()
        super().hideEvent(e)
