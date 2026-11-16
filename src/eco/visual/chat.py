"""Aba Conversas da janela principal (Qt): a conversa atual com a Eco e as conversas guardadas, por espaço.

Substitui o antigo chat escrito (decisão de 10/11/2026). A conversa atual vem do `Cerebro` (o mesmo agente da voz):
o que foi falado e o que foi escrito na caixa simples aparecem juntos. Ela só vai para o banco, num espaço, quando o
dono diz "Guarde essa, Eco" ou aceita a sugestão da Eco; as guardadas aparecem na lista de cada espaço e ficam só para
leitura. Resposta com código ou longa abre também na janela flutuante. Threads falam com a tela só por sinais Qt.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from eco.chat.armazenamento import COR_PADRAO, GERAL, TODOS, Banco, Espaco
from eco.chat.render import renderizar_conversa
from eco.chat.servico import ler_arquivo_contexto
from eco.config import PASTA_ECO, Config
from eco.visual import dialogos, tema
from eco.visual.componentes import Botao, Cartao

CORES = list(tema.CORES_ESPACOS)
SEM_ESPACO = ""  # valor guardado nos itens da lista para a área "Geral"
ATUAL = "__atual__"  # item da lista que mostra a conversa atual (ainda na memória do Cerebro)

ESTILO = tema.folha()  # outras abas importam daqui; a fonte de verdade é visual/tema.py


class Sinais(QObject):
    """O que as threads (resposta da Eco, conversa atual mudou) avisam para a tela."""

    resposta = Signal(str, str)  # texto da resposta, erro ("" se deu certo)
    sessao = Signal()  # a conversa atual mudou (fala de voz, texto, guardou)
    confirmar = Signal(str)  # frase para o "sim" de uma ação pedida por escrito


def _bolinha(cor: str, lado: int = 12) -> QIcon:
    pm = QPixmap(lado, lado)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(cor))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(0, 0, lado, lado)
    p.end()
    return QIcon(pm)


class CaixaDeTexto(QPlainTextEdit):
    """Enter envia; Shift+Enter quebra a linha."""

    enviar = Signal()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.enviar.emit()
            return
        super().keyPressEvent(e)


class DialogoEspaco(QDialog):
    """Criar/editar espaço: nome, cor, instruções próprias e arquivos de contexto."""

    def __init__(self, parent: QWidget | None, espaco: Espaco | None = None, arquivos: list | None = None, cor_inicial: str = COR_PADRAO) -> None:
        super().__init__(parent)
        self.setWindowTitle("Editar espaço" if espaco else "Novo espaço")
        self.setStyleSheet(ESTILO)
        self.setMinimumWidth(520)
        self.cor = espaco.cor if espaco else cor_inicial
        self.novos: list[tuple[str, str]] = []
        self.removidos: list[str] = []
        self._existentes = list(arquivos or [])
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Nome"))
        self.nome = QLineEdit(espaco.nome if espaco else "")
        self.nome.setPlaceholderText("Ex.: Investimentos, Estudos, Leitura")
        lay.addWidget(self.nome)
        lay.addWidget(QLabel("Cor"))
        linha = QHBoxLayout()
        self._botoes_cor: list[QPushButton] = []
        for c in CORES:
            b = QPushButton()
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedSize(28, 28)
            b.clicked.connect(lambda _=False, c=c: self._escolher_cor(c))
            self._botoes_cor.append(b)
            linha.addWidget(b)
        linha.addStretch()
        lay.addLayout(linha)
        lay.addWidget(QLabel("Instruções (como a Eco deve agir neste espaço)"))
        self.instrucoes = QPlainTextEdit(espaco.instrucoes if espaco else "")
        self.instrucoes.setPlaceholderText("Ex.: Aja como analista de investimentos conservador e responda de forma objetiva.")
        self.instrucoes.setFixedHeight(110)
        lay.addWidget(self.instrucoes)
        lay.addWidget(QLabel("Arquivos de contexto (opcional; texto, Markdown, dados ou código, até 200 MB cada)"))
        self.lista = QListWidget()
        self.lista.setFixedHeight(90)
        lay.addWidget(self.lista)
        bt = QHBoxLayout()
        add = Botao("Adicionar arquivo…")
        add.clicked.connect(self._adicionar)
        rem = Botao("Remover selecionado")
        rem.clicked.connect(self._remover)
        bt.addWidget(add)
        bt.addWidget(rem)
        bt.addStretch()
        lay.addLayout(bt)
        fim = QHBoxLayout()
        fim.addStretch()
        cancelar = Botao("Cancelar")
        cancelar.clicked.connect(self.reject)
        salvar = Botao("Salvar", "principal")
        salvar.clicked.connect(self._salvar)
        fim.addWidget(cancelar)
        fim.addWidget(salvar)
        lay.addLayout(fim)
        self._escolher_cor(self.cor)
        self._atualizar_lista()

    def _escolher_cor(self, cor: str) -> None:
        self.cor = cor
        for b, c in zip(self._botoes_cor, CORES):
            borda = tema.TEXTO if c == cor else "transparent"  # a escolhida tem anel claro, não só cor
            b.setStyleSheet(f"background:{c}; border:2px solid {borda}; border-radius:14px;")

    def _atualizar_lista(self) -> None:
        self.lista.clear()
        for a in self._existentes:
            if a.id not in self.removidos:
                it = QListWidgetItem(a.nome)
                it.setData(Qt.ItemDataRole.UserRole, ("antigo", a.id))
                self.lista.addItem(it)
        for nome, _ in self.novos:
            it = QListWidgetItem(nome)
            it.setData(Qt.ItemDataRole.UserRole, ("novo", nome))
            self.lista.addItem(it)

    def _adicionar(self) -> None:
        caminhos, _ = QFileDialog.getOpenFileNames(self, "Escolha os arquivos de contexto")
        for c in caminhos:
            try:
                self.novos.append(ler_arquivo_contexto(c))
            except (ValueError, OSError) as e:
                dialogos.avisar(self, "Arquivo não adicionado", str(e))
        self._atualizar_lista()

    def _remover(self) -> None:
        it = self.lista.currentItem()
        if it is None:
            return
        tipo, valor = it.data(Qt.ItemDataRole.UserRole)
        if tipo == "antigo":
            self.removidos.append(valor)
        else:
            self.novos = [n for n in self.novos if n[0] != valor]
        self._atualizar_lista()

    def _salvar(self) -> None:
        if not self.nome.text().strip():
            dialogos.avisar(self, "Espaço", "Dê um nome ao espaço.")
            return
        self.accept()


class PainelChat(QWidget):
    def __init__(
        self,
        cfg: Config,
        painel: Any | None = None,
        banco: Banco | None = None,
        provedor: Any | None = None,  # não é mais usado (era o chat escrito); fica para não quebrar quem chama
        openai: Any | None = None,
        cerebro: Any | None = None,
    ) -> None:
        super().__init__()
        self.cfg, self.painel = cfg, painel
        self.banco = banco or Banco(PASTA_ECO / "eco.db")
        self._cerebro = cerebro
        self.sinais = Sinais()
        self.espaco_id: str | None = GERAL
        self.conversa_id: str | None = ATUAL
        self.pensando = False
        self._erro = ""
        self._mensagens: list = []
        self._codigos: list[str] = []
        self._resultados_busca: list = []
        self._resposta_confirmacao = False
        self._confirmado = threading.Event()
        self._janela_flutuante = None
        self._ouvindo_cerebro = False
        self.setStyleSheet(ESTILO)

        self.sinais.resposta.connect(self._ao_responder)
        self.sinais.sessao.connect(self._ao_mudar_sessao)
        self.sinais.confirmar.connect(self._perguntar)

        raiz = QHBoxLayout(self)
        raiz.setContentsMargins(20, 16, 20, 12)
        raiz.setSpacing(12)

        # coluna 1: espaços
        cartao1 = Cartao("Espaços")
        col1 = cartao1.corpo
        self.lista_espacos = QListWidget()
        self.lista_espacos.setFixedWidth(170)
        self.lista_espacos.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lista_espacos.currentItemChanged.connect(self._espaco_mudou)
        self.lista_espacos.customContextMenuRequested.connect(self._menu_espaco)
        col1.addWidget(self.lista_espacos, 1)
        self.btn_novo_espaco = Botao("+ Espaço")
        self.btn_novo_espaco.clicked.connect(self.novo_espaco)
        col1.addWidget(self.btn_novo_espaco)
        raiz.addWidget(cartao1)

        # coluna 2: busca e conversas
        cartao2 = Cartao("Conversas")
        col2 = cartao2.corpo
        self.busca = QLineEdit()
        self.busca.setPlaceholderText("Buscar por palavra…  (Ctrl+F)")
        self.busca.setClearButtonEnabled(True)
        self.busca.textChanged.connect(self._atualizar_conversas)
        col2.addWidget(self.busca)
        self.escopo = QComboBox()
        self.escopo.addItems(["Neste espaço", "Todos os espaços"])
        self.escopo.currentIndexChanged.connect(self._atualizar_conversas)
        col2.addWidget(self.escopo)
        self.lista_conversas = QListWidget()
        self.lista_conversas.setFixedWidth(230)
        self.lista_conversas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lista_conversas.itemClicked.connect(self._conversa_clicada)
        self.lista_conversas.customContextMenuRequested.connect(self._menu_conversa)
        col2.addWidget(self.lista_conversas, 1)
        self.btn_nova = Botao("+ Nova conversa")
        self.btn_nova.setToolTip("Começa outra conversa atual (a de agora, se não foi guardada, é descartada)")
        self.btn_nova.clicked.connect(self.nova_conversa)
        col2.addWidget(self.btn_nova)
        raiz.addWidget(cartao2)

        # coluna 3: conversa
        cartao3 = Cartao("Conversa")
        col3 = cartao3.corpo
        cab = QHBoxLayout()
        self.titulo = self._rotulo("", "titulo")
        cab.addWidget(self.titulo, 1)
        col3.addLayout(cab)
        self.vista = QTextBrowser()
        self.vista.setOpenLinks(False)
        self.vista.setOpenExternalLinks(False)
        self.vista.anchorClicked.connect(self._link_clicado)
        col3.addWidget(self.vista, 1)
        self.aviso = self._rotulo("", "discreto")
        col3.addWidget(self.aviso)
        base = QHBoxLayout()
        self.entrada = CaixaDeTexto()
        self.entrada.setPlaceholderText("Escreva para a Eco: comando rápido, código…  (Enter envia, Shift+Enter quebra a linha)")
        self.entrada.setFixedHeight(64)
        self.entrada.enviar.connect(self.enviar)
        base.addWidget(self.entrada, 1)
        self.btn_enviar = Botao("Enviar", "principal")
        self.btn_enviar.clicked.connect(self.enviar)
        base.addWidget(self.btn_enviar)
        col3.addLayout(base)
        raiz.addWidget(cartao3, 1)

        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.busca.setFocus)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.nova_conversa)
        self.recarregar_espacos()
        self.abrir_conversa(ATUAL)

    # --- utilidades -------------------------------------------------------------------------------
    @staticmethod
    def _rotulo(texto: str, nome: str) -> QLabel:
        r = QLabel(texto)
        r.setObjectName(nome)
        return r

    @property
    def cerebro(self) -> Any | None:
        """O Cerebro da thread de voz (fica pronto alguns segundos depois de a Eco abrir)."""
        c = self._cerebro or (getattr(self.painel, "cerebro", None) if self.painel is not None else None)
        if c is not None and not self._ouvindo_cerebro:
            c.ao_mudar(self.sinais.sessao.emit)  # chamado na thread de quem falou: o sinal traz para a tela
            self._ouvindo_cerebro = True
        return c

    def _cor_atual(self) -> str:
        if self.espaco_id:
            try:
                return self.banco.espaco(self.espaco_id).cor
            except KeyError:
                pass
        return COR_PADRAO

    def _estado_orbe(self, nome: str) -> None:
        if self.painel is not None:
            self.painel.estado(nome)

    @staticmethod
    def _id_do_item(item: QListWidgetItem | None) -> str | None:
        if item is None:
            return GERAL
        valor = item.data(Qt.ItemDataRole.UserRole)
        return GERAL if valor in (None, SEM_ESPACO) else valor

    # --- espaços ----------------------------------------------------------------------------------
    def recarregar_espacos(self, selecionar: str | None = ...) -> None:  # type: ignore[assignment]
        alvo = self.espaco_id if selecionar is ... else selecionar
        self.lista_espacos.blockSignals(True)
        self.lista_espacos.clear()
        geral = QListWidgetItem(_bolinha(tema.COR_GERAL), "Geral")
        geral.setData(Qt.ItemDataRole.UserRole, SEM_ESPACO)
        self.lista_espacos.addItem(geral)
        escolhido = geral
        for e in self.banco.listar_espacos():
            it = QListWidgetItem(_bolinha(e.cor), e.nome)
            it.setData(Qt.ItemDataRole.UserRole, e.id)
            self.lista_espacos.addItem(it)
            if e.id == alvo:
                escolhido = it
        self.lista_espacos.setCurrentItem(escolhido)
        self.lista_espacos.blockSignals(False)
        self.espaco_id = self._id_do_item(escolhido)
        self._atualizar_conversas()

    def selecionar_espaco(self, espaco_id: str | None) -> None:
        """Troca de espaço: mostra só as conversas dele e fecha a que estava aberta se for de outro espaço."""
        self.recarregar_espacos(espaco_id)
        self._fechar_se_de_outro_espaco()

    def _espaco_mudou(self, atual, _anterior) -> None:
        self.espaco_id = self._id_do_item(atual)
        self._atualizar_conversas()
        self._fechar_se_de_outro_espaco()

    def _fechar_se_de_outro_espaco(self) -> None:
        """A tela nunca mostra uma conversa guardada que não pertence ao espaço selecionado (a atual vale para todos)."""
        if self.conversa_id is None or self.conversa_id == ATUAL:
            return
        try:
            dono = self.banco.conversa(self.conversa_id).espaco_id
        except KeyError:
            dono = ...
        if dono != self.espaco_id:
            self.conversa_id = None
            self._mensagens = []
            self._erro = ""
            self.abrir_conversa(ATUAL)

    def novo_espaco(self) -> None:
        self._editar_espaco(None)

    def _editar_espaco(self, espaco: Espaco | None) -> None:
        arquivos = self.banco.listar_arquivos(espaco.id) if espaco else []
        usadas = {e.cor for e in self.banco.listar_espacos()}
        livre = next((c for c in CORES if c not in usadas), COR_PADRAO)  # cada espaço novo já nasce com uma cor diferente
        while True:
            d = DialogoEspaco(self, espaco, arquivos, livre)
            if d.exec() != QDialog.DialogCode.Accepted:
                return
            try:
                if espaco is None:
                    novo = self.banco.criar_espaco(d.nome.text(), d.cor, d.instrucoes.toPlainText())
                    alvo = novo.id
                else:
                    self.banco.atualizar_espaco(espaco.id, d.nome.text(), d.cor, d.instrucoes.toPlainText())
                    alvo = espaco.id
                for a_id in d.removidos:
                    self.banco.remover_arquivo(a_id)
                for nome, conteudo in d.novos:
                    self.banco.adicionar_arquivo(alvo, nome, conteudo)
            except ValueError as e:
                dialogos.avisar(self, "Espaço", str(e))
                continue
            self.recarregar_espacos(alvo)
            return

    def _menu_espaco(self, ponto) -> None:
        item = self.lista_espacos.itemAt(ponto)
        espaco_id = self._id_do_item(item)
        if item is None or espaco_id is None:
            return
        menu = QMenu(self)
        a_editar = menu.addAction("Editar…")
        a_excluir = menu.addAction("Excluir")
        escolha = menu.exec(self.lista_espacos.viewport().mapToGlobal(ponto))
        espaco = self.banco.espaco(espaco_id)
        if escolha is a_editar:
            self._editar_espaco(espaco)
        elif escolha is a_excluir:
            if dialogos.perguntar(
                self, "Excluir espaço",
                f"Excluir o espaço \"{espaco.nome}\"?\nAs conversas dele não são apagadas: vão para o Geral.",
                sim="Excluir", destrutivo=True,
            ):
                self.banco.excluir_espaco(espaco.id)
                self.recarregar_espacos(GERAL)
                self._fechar_se_de_outro_espaco()

    # --- conversas --------------------------------------------------------------------------------
    def _atualizar_conversas(self, *_args) -> None:
        texto = self.busca.text().strip()
        self.lista_conversas.clear()
        if texto:
            escopo = TODOS if self.escopo.currentIndex() == 1 else self.espaco_id
            self._resultados_busca = self.banco.buscar(texto, escopo)
            for r in self._resultados_busca:
                rotulo = r.titulo + (f"\n{r.trecho}" if r.trecho else "")
                it = QListWidgetItem(rotulo)
                it.setData(Qt.ItemDataRole.UserRole, r.conversa_id)
                it.setData(Qt.ItemDataRole.UserRole + 1, r.espaco_id or SEM_ESPACO)
                self.lista_conversas.addItem(it)
            if not self._resultados_busca:
                vazio = QListWidgetItem("Nada encontrado.")
                vazio.setFlags(Qt.ItemFlag.NoItemFlags)
                self.lista_conversas.addItem(vazio)
            return
        atual = QListWidgetItem("● Conversa atual")
        atual.setData(Qt.ItemDataRole.UserRole, ATUAL)
        atual.setData(Qt.ItemDataRole.UserRole + 1, SEM_ESPACO)
        self.lista_conversas.addItem(atual)
        if self.conversa_id == ATUAL:
            self.lista_conversas.setCurrentItem(atual)
        for c in self.banco.listar_conversas(self.espaco_id):
            it = QListWidgetItem(c.titulo)
            it.setData(Qt.ItemDataRole.UserRole, c.id)
            it.setData(Qt.ItemDataRole.UserRole + 1, c.espaco_id or SEM_ESPACO)
            self.lista_conversas.addItem(it)
            if c.id == self.conversa_id:
                self.lista_conversas.setCurrentItem(it)

    def _conversa_clicada(self, item: QListWidgetItem) -> None:
        conversa_id = item.data(Qt.ItemDataRole.UserRole)
        if not conversa_id:
            return
        if conversa_id == ATUAL:
            self.abrir_conversa(ATUAL)
            return
        espaco = item.data(Qt.ItemDataRole.UserRole + 1)
        espaco = GERAL if espaco in (None, SEM_ESPACO) else espaco
        if espaco != self.espaco_id and self.escopo.currentIndex() == 1:
            self.espaco_id = espaco
            self.lista_espacos.blockSignals(True)
            for i in range(self.lista_espacos.count()):
                if self._id_do_item(self.lista_espacos.item(i)) == espaco:
                    self.lista_espacos.setCurrentRow(i)
            self.lista_espacos.blockSignals(False)
        self.abrir_conversa(conversa_id)

    def mostrar_conversa(self, conversa_id: str) -> None:
        """Abre uma conversa vinda de fora (ex.: do cartão do grafo), trocando para o espaço dela."""
        try:
            c = self.banco.conversa(conversa_id)
        except KeyError:
            self.aviso.setText("Essa conversa não existe mais.")
            return
        self.busca.clear()
        self.recarregar_espacos(c.espaco_id)
        self.abrir_conversa(conversa_id)

    def nova_conversa(self) -> None:
        """Começa outra conversa atual (a anterior, se foi guardada, continua no espaço dela)."""
        if self.cerebro is not None:
            self.cerebro.nova_conversa()
        self.busca.clear()
        self.abrir_conversa(ATUAL)
        self.entrada.setFocus()

    def _falas_atuais(self) -> list:
        c = self.cerebro
        if c is None:
            return []
        return [SimpleNamespace(papel=f.papel, conteudo=f.texto, interrompida=False) for f in c.sessao.falas]

    def abrir_conversa(self, conversa_id: str) -> None:
        self.conversa_id = conversa_id
        self._erro = ""
        self._mensagens = self._falas_atuais() if conversa_id == ATUAL else self.banco.mensagens(conversa_id)
        self._atualizar_cabecalho()
        self._atualizar_botoes()
        self._renderizar()
        self.vista.verticalScrollBar().setValue(self.vista.verticalScrollBar().maximum())
        for i in range(self.lista_conversas.count()):
            if self.lista_conversas.item(i).data(Qt.ItemDataRole.UserRole) == conversa_id:
                self.lista_conversas.setCurrentRow(i)

    def _atualizar_cabecalho(self) -> None:
        if self.conversa_id is None:
            self.titulo.setText("")
            return
        if self.conversa_id == ATUAL:
            c = self.cerebro
            if c is None:
                self.titulo.setText("Conversa atual  ·  a Eco ainda está iniciando")
            elif c.sessao.guardada:
                self.titulo.setText(f"Conversa atual  ·  guardada em {c.sessao.espaco_nome}")
            else:
                self.titulo.setText("Conversa atual  ·  não guardada (diga \"Guarde essa, Eco\")")
            return
        try:
            self.titulo.setText(self.banco.conversa(self.conversa_id).titulo)
        except KeyError:
            self.titulo.setText("")

    def _menu_conversa(self, ponto) -> None:
        item = self.lista_conversas.itemAt(ponto)
        conversa_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not conversa_id or conversa_id == ATUAL:
            return
        menu = QMenu(self)
        a_renomear = menu.addAction("Renomear…")
        sub = menu.addMenu("Mover para")
        destinos = {sub.addAction("Geral"): GERAL}
        for e in self.banco.listar_espacos():
            destinos[sub.addAction(e.nome)] = e.id
        a_excluir = menu.addAction("Excluir")
        escolha = menu.exec(self.lista_conversas.viewport().mapToGlobal(ponto))
        if escolha is a_renomear:
            atual = self.banco.conversa(conversa_id).titulo
            novo, ok = dialogos.texto(self, "Renomear conversa", "Novo título:", atual)
            if ok and novo.strip():
                self.banco.renomear_conversa(conversa_id, novo.strip())
                self._atualizar_conversas()
                self._atualizar_cabecalho()
        elif escolha in destinos:
            self._mover_conversa(conversa_id, destinos[escolha])
        elif escolha is a_excluir:
            if dialogos.perguntar(self, "Excluir conversa", "Excluir esta conversa e todas as mensagens dela?", sim="Excluir", destrutivo=True):
                self.banco.excluir_conversa(conversa_id)
                if conversa_id == self.conversa_id:
                    self.abrir_conversa(ATUAL)
                self._atualizar_conversas()

    def _mover_conversa(self, conversa_id: str, destino: str | None) -> None:
        """Move mantendo todo o histórico (só muda o espaço da conversa)."""
        self.banco.mover_conversa(conversa_id, destino)
        if conversa_id == self.conversa_id:
            self.recarregar_espacos(destino)
        else:
            self._atualizar_conversas()

    # --- exibir -----------------------------------------------------------------------------------
    def _mostrar_vazio(self) -> None:
        self.titulo.setText("")
        self.vista.setHtml(
            f'<html><body><p style="color:{tema.TEXTO_2}">Fale com a Eco ou escreva abaixo. Para guardar uma conversa num espaço, '
            'diga "Guarde essa, Eco".</p></body></html>'
        )

    def _renderizar(self) -> None:
        if self.conversa_id is None:
            return
        if self.conversa_id == ATUAL and not self._mensagens and not self.pensando and not self._erro:
            self._mostrar_vazio()
            self._atualizar_cabecalho()
            return
        rascunho = "" if (self.pensando and self.conversa_id == ATUAL) else None
        html, self._codigos = renderizar_conversa(self._mensagens, self._cor_atual(), rascunho, self._erro or None)
        barra = self.vista.verticalScrollBar()
        no_fim = barra.value() >= barra.maximum() - 24
        self.vista.setHtml(html)
        if no_fim:
            barra.setValue(barra.maximum())

    def _link_clicado(self, url) -> None:
        texto = url.toString()
        if texto.startswith("copiar:"):
            try:
                QGuiApplication.clipboard().setText(self._codigos[int(texto.split(":", 1)[1])])
                self.aviso.setText("Código copiado.")
                QTimer.singleShot(2500, lambda: self.aviso.setText("") if self.aviso.text() == "Código copiado." else None)
            except (ValueError, IndexError):
                pass

    # --- enviar ------------------------------------------------------------------------------------
    @property
    def ocupado(self) -> bool:
        return self.pensando

    def _atualizar_botoes(self) -> None:
        self.btn_enviar.setEnabled(not self.pensando)

    def enviar(self) -> None:
        """Caixa simples: manda o texto para o mesmo agente da voz (com as ferramentas) e mostra na conversa atual."""
        texto = self.entrada.toPlainText().strip()
        if not texto or self.pensando:
            return
        cerebro = self.cerebro
        if cerebro is None:
            self.aviso.setText("A Eco ainda está iniciando. Tente de novo em alguns segundos.")
            return
        self.entrada.clear()
        self.busca.clear()
        self.aviso.setText("")
        self.pensando, self._erro = True, ""
        self.conversa_id = ATUAL
        self._mensagens = [*self._falas_atuais(), SimpleNamespace(papel="user", conteudo=texto, interrompida=False)]
        self._atualizar_conversas()
        self._atualizar_botoes()
        self._renderizar()

        def trabalho() -> None:
            try:
                resposta = cerebro.responder(texto, "texto", confirmar=self._confirmar_de_outra_thread)
                self.sinais.resposta.emit(resposta, "")
            except Exception as e:  # sem rede, sem chave: o texto volta para a caixa
                self.sinais.resposta.emit(texto, self._texto_do_erro(e))

        threading.Thread(target=trabalho, daemon=True, name="eco-caixa").start()

    @staticmethod
    def _texto_do_erro(e: Exception) -> str:
        try:
            from eco.erros import mensagem_de_erro

            return mensagem_de_erro(e)
        except Exception:
            return str(e)

    def _ao_responder(self, texto: str, erro: str) -> None:
        self.pensando = False
        self._atualizar_botoes()
        if erro:
            self._erro = erro
            if not self.entrada.toPlainText().strip():
                self.entrada.setPlainText(texto)  # não perde o que foi escrito
        if self.conversa_id == ATUAL:
            self._mensagens = self._falas_atuais()
            self._atualizar_cabecalho()
            self._renderizar()
        if not erro:
            from eco.cerebro import vai_para_janela

            if vai_para_janela(texto):
                self.mostrar_flutuante(texto)

    def mostrar_flutuante(self, texto: str) -> None:
        if self.painel is not None and hasattr(self.painel, "b"):
            self.painel.flutuante(texto)  # a janela da orbe cuida (uma só para voz e texto)
            return
        from eco.visual.flutuante import JanelaFlutuante

        if self._janela_flutuante is None:
            self._janela_flutuante = JanelaFlutuante()
        self._janela_flutuante.mostrar(texto)

    def _ao_mudar_sessao(self) -> None:
        """A conversa atual mudou (voz, texto ou guardou): redesenha se ela está aberta e atualiza a lista."""
        self._atualizar_conversas()
        if self.conversa_id == ATUAL and not self.pensando:
            self._mensagens = self._falas_atuais()
            self._atualizar_cabecalho()
            self._renderizar()

    # --- confirmação de ações pedidas por escrito -------------------------------------------------------
    def _confirmar_de_outra_thread(self, frase: str) -> bool:
        """O agente roda numa thread; a pergunta precisa da tela: pede pelo sinal e espera a resposta."""
        self._confirmado.clear()
        self.sinais.confirmar.emit(frase)
        self._confirmado.wait()
        return self._resposta_confirmacao

    def _perguntar(self, frase: str) -> None:
        self._resposta_confirmacao = dialogos.perguntar(self, "Confirmar", frase + "\n\nPosso fazer?", sim="Pode fazer")
        self._confirmado.set()
