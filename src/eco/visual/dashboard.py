"""Aba Painel (Qt): o painel de controle. Agenda, tarefas, e-mails pendentes e metas num só lugar.

Passo 5 do escopo (11/11/2026). Tudo é administrado aqui, sem precisar da voz:
- Agenda de hoje e dos próximos 7 dias: criar, editar (título, dia, hora, duração, local) e apagar. Evento de série
  pergunta se é só este, este e os próximos, ou todos.
- Tarefas do Google Tasks: marcar feita/reabrir, criar, editar (título, data, lista, notas), apagar e marcar como
  importante com o motivo ou tópico (vai para as notas da tarefa: "Importante: motivo").
- E-mails pendentes = e-mails com estrela no Gmail; "Resolvido" tira a estrela, "Abrir" abre no navegador.
- Metas por tópico, como antes.
Toda leitura e ação roda em thread; a tela nunca trava esperando o Google. Recarrega sozinha a cada minuto aberta.
Os dados vêm de `eco.controle` (a mesma base dos lembretes falados).
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Any, Callable

from PySide6.QtCore import QDate, QObject, Qt, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from eco import controle, revisao
from eco.config import Config
from eco.ferramentas import notas
from eco.ferramentas.tarefas import motivo_importante
from eco.lembretes import FUSO
from eco.visual import dialogos, tema
from eco.visual.componentes import SEPARADOR, Botao, Caixa, Cartao, Linha, rotulo

INTERVALO_MS = 60_000
DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
ESCOPOS = (("Só este", "este"), ("Este e os próximos", "proximos"), ("Todos da série", "todos"))
ESTILO = tema.folha() + "QWidget#painel { background: transparent; }"  # deixa ver as partículas de fundo


class Sinais(QObject):
    carregado = Signal(object, str)  # Controle, aviso (vazio se deu tudo certo)


def _como_controle(dados: Any) -> controle.Controle:
    return dados if isinstance(dados, controle.Controle) else controle.Controle(dados)


class _AcoesComTrocas(controle.AcoesGoogle):
    """Mantém os parâmetros antigos (concluir_tarefa/reabrir_tarefa) por cima das ações do Google."""

    def __init__(self, concluir: Callable[[dict], None] | None, reabrir: Callable[[dict], None] | None) -> None:
        self._c, self._r = concluir, reabrir

    def concluir_tarefa(self, t: dict) -> None:
        (self._c or super().concluir_tarefa)(t)

    def reabrir_tarefa(self, t: dict) -> None:
        (self._r or super().reabrir_tarefa)(t)


# --- diálogos ------------------------------------------------------------------------------------------------------
class DialogoTarefa(QDialog):
    def __init__(self, parent: QWidget | None, t: dict | None, listas: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Editar tarefa" if t else "Nova tarefa")
        self.setStyleSheet(tema.folha())
        self.setMinimumWidth(420)
        form = QFormLayout(self)
        self.titulo = QLineEdit(t["titulo"] if t else "")
        form.addRow("Título", self.titulo)
        self.com_data = QCheckBox("Com data")
        self.data = QDateEdit()
        self.data.setCalendarPopup(True)
        self.data.setDisplayFormat("dd/MM/yyyy")
        prazo = t.get("prazo") if t else None
        self.com_data.setChecked(prazo is not None)
        self.data.setDate(QDate(prazo.year, prazo.month, prazo.day) if prazo else QDate.currentDate())
        self.data.setEnabled(prazo is not None)
        self.com_data.toggled.connect(self.data.setEnabled)
        linha = QHBoxLayout()
        linha.addWidget(self.com_data)
        linha.addWidget(self.data, 1)
        form.addRow("Data", linha)
        self.lista = QComboBox()
        self.lista.addItems(listas or ([t["lista"]] if t and t.get("lista") else []))
        if t and t.get("lista") in listas:
            self.lista.setCurrentText(t["lista"])
        self.lista.setEnabled(self.lista.count() > 1)
        form.addRow("Lista", self.lista)
        motivo = motivo_importante(t.get("notas")) if t else None
        self.importante = QCheckBox("Importante")
        self.importante.setChecked(motivo is not None)
        self.motivo = QLineEdit(motivo or "")
        self.motivo.setPlaceholderText("Motivo ou tópico (ex.: meta de Estudos)")
        self.motivo.setEnabled(motivo is not None)
        self.importante.toggled.connect(self.motivo.setEnabled)
        form.addRow(self.importante, self.motivo)
        from eco.ferramentas.tarefas import notas_com_importante

        self.notas = QPlainTextEdit(notas_com_importante(t.get("notas"), None) if t else "")
        self.notas.setFixedHeight(80)
        form.addRow("Notas", self.notas)
        bt = QHBoxLayout()
        bt.addStretch()
        cancelar = Botao("Cancelar")
        cancelar.clicked.connect(self.reject)
        salvar = Botao("Salvar", "principal")
        salvar.clicked.connect(self._salvar)
        bt.addWidget(cancelar)
        bt.addWidget(salvar)
        form.addRow(bt)

    def _salvar(self) -> None:
        if not self.titulo.text().strip():
            dialogos.avisar(self, "Tarefa", "Dê um título à tarefa.")
            return
        self.accept()

    def dados(self) -> dict:
        return {
            "titulo": " ".join(self.titulo.text().split()),
            "data": self.data.date().toString("yyyy-MM-dd") if self.com_data.isChecked() else None,
            "lista": self.lista.currentText() or None,
            "notas": self.notas.toPlainText().strip(),
            "importante": self.motivo.text().strip() if self.importante.isChecked() else None,
        }


class DialogoEvento(QDialog):
    def __init__(self, parent: QWidget | None, ev: dict | None, dia: date) -> None:
        super().__init__(parent)
        self.setWindowTitle("Editar evento" if ev else "Novo evento")
        self.setStyleSheet(tema.folha())
        self.setMinimumWidth(400)
        form = QFormLayout(self)
        self.titulo = QLineEdit(ev["titulo"] if ev else "")
        form.addRow("Título", self.titulo)
        inicio = ev["inicio"].astimezone(FUSO) if ev and isinstance(ev["inicio"], datetime) else None
        base = inicio.date() if inicio else (ev["inicio"] if ev else dia)
        self.data = QDateEdit(QDate(base.year, base.month, base.day))
        self.data.setCalendarPopup(True)
        self.data.setDisplayFormat("dd/MM/yyyy")
        form.addRow("Dia", self.data)
        self.hora = QTimeEdit(QTime(inicio.hour, inicio.minute) if inicio else QTime(9, 0))
        self.hora.setDisplayFormat("HH:mm")
        form.addRow("Hora", self.hora)
        self.duracao = QSpinBox()
        self.duracao.setRange(5, 24 * 60)
        self.duracao.setSingleStep(15)
        self.duracao.setSuffix(" min")
        fim = ev.get("fim") if ev else None
        self.duracao.setValue(int((fim - ev["inicio"]).total_seconds() // 60) if inicio and fim else 60)
        form.addRow("Duração", self.duracao)
        self.local = QLineEdit()
        self.local.setPlaceholderText("Opcional")
        form.addRow("Local", self.local)
        if ev and inicio is None:  # dia inteiro: aqui só o título muda
            for w in (self.data, self.hora, self.duracao):
                w.setEnabled(False)
        bt = QHBoxLayout()
        bt.addStretch()
        cancelar = Botao("Cancelar")
        cancelar.clicked.connect(self.reject)
        salvar = Botao("Salvar", "principal")
        salvar.clicked.connect(self._salvar)
        bt.addWidget(cancelar)
        bt.addWidget(salvar)
        form.addRow(bt)

    def _salvar(self) -> None:
        if not self.titulo.text().strip():
            dialogos.avisar(self, "Evento", "Dê um título ao evento.")
            return
        self.accept()

    def dados(self) -> dict:
        return {
            "titulo": " ".join(self.titulo.text().split()),
            "inicio": f"{self.data.date().toString('yyyy-MM-dd')}T{self.hora.time().toString('HH:mm')}",
            "duracao_min": self.duracao.value(),
            "local": self.local.text().strip(),
        }


# --- painel --------------------------------------------------------------------------------------------------------
class PainelDashboard(QWidget):
    def __init__(
        self,
        cfg: Config,
        ler: Callable[[], Any] | None = None,
        concluir_tarefa: Callable[[dict], None] | None = None,
        reabrir_tarefa: Callable[[dict], None] | None = None,
        acoes: Any | None = None,
        confirmar: Callable[[str], bool] | None = None,
        escolher_escopo: Callable[[str], str | None] | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self._ler = ler or (lambda: controle.carregar(cfg.pasta_dados))
        self.acoes = acoes or _AcoesComTrocas(concluir_tarefa, reabrir_tarefa)
        self._confirmar = confirmar or self._confirmar_padrao
        self._escolher_escopo = escolher_escopo or self._escopo_padrao
        self.sinais = Sinais()
        self.sinais.carregado.connect(self._mostrar)
        self.ocupado = False
        self._fila: list[Callable[[], None]] = []
        self.dados: controle.Controle | None = None
        self._listas: list[str] = []
        self.setObjectName("painel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(ESTILO)

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 16, 20, 12)
        raiz.setSpacing(12)
        barra = QHBoxLayout()
        barra.setSpacing(8)
        titulo = QLabel("Painel")
        titulo.setObjectName("titulo")
        barra.addWidget(titulo)
        self.status = QLabel("")
        self.status.setObjectName("rotulo")
        barra.addWidget(self.status, 1)
        self.btn_evento = Botao("+ Evento")
        self.btn_evento.clicked.connect(lambda: self.novo_evento())
        barra.addWidget(self.btn_evento)
        self.btn_tarefa = Botao("+ Tarefa")
        self.btn_tarefa.clicked.connect(lambda: self.nova_tarefa())
        barra.addWidget(self.btn_tarefa)
        self.btn_atualizar = Botao("Atualizar")
        self.btn_atualizar.clicked.connect(lambda: self.atualizar())
        barra.addWidget(self.btn_atualizar)
        raiz.addLayout(barra)

        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        raiz.addWidget(self.area, 1)
        self.corpo: QWidget | None = None

        self.timer = QTimer(self)
        self.timer.setInterval(INTERVALO_MS)
        self.timer.timeout.connect(self._tique)
        self.timer.start()

    # --- carregar --------------------------------------------------------------------------------------------
    def ao_abrir(self) -> None:
        self.atualizar()

    def _tique(self) -> None:
        if self.isVisible():
            self.atualizar()

    def atualizar(self, antes: Callable[[], None] | None = None) -> None:
        """Lê tudo de novo em thread. `antes` (uma ação de botão) roda na mesma thread, antes da leitura."""
        if self.ocupado:
            if antes is not None:
                self._fila.append(antes)  # clicou enquanto lia: faz logo depois, sem perder o clique
            return
        self.ocupado = True
        self.btn_atualizar.setEnabled(False)
        self.status.setText("Atualizando…")

        def trabalho() -> None:
            aviso = ""
            try:
                if antes is not None:
                    antes()
            except Exception as erro:
                aviso = f"Não consegui: {erro}"
            try:
                dados = _como_controle(self._ler())
                if dados.avisos:
                    aviso = aviso or "Faltou: " + "; ".join(dados.avisos) + "."
            except Exception as erro:  # sem rede, sem autorização: mostra ao menos as metas
                dados = controle.Controle(revisao.coletar(self.cfg.pasta_dados, [], [], datetime.now(FUSO).date()))
                aviso = aviso or f"Google indisponível ({type(erro).__name__}): mostrando só as metas."
            if not self._listas:
                try:
                    self._listas = self.acoes.listas()
                except Exception:
                    pass
            self.sinais.carregado.emit(dados, aviso)

        threading.Thread(target=trabalho, daemon=True, name="eco-painel").start()

    def _mostrar(self, dados: controle.Controle, aviso: str) -> None:
        self.ocupado = False
        self.btn_atualizar.setEnabled(True)
        self.dados = dados
        self.status.setText(aviso or f"Atualizado às {datetime.now():%H:%M}")
        self._montar(dados)
        if self._fila:
            lote, self._fila = self._fila, []

            def rodar_lote() -> None:
                for f in lote:
                    f()

            self.atualizar(antes=rodar_lote)

    # --- desenho ---------------------------------------------------------------------------------------------
    def _montar(self, dados: controle.Controle) -> None:
        """Duas colunas de cartões: agenda e e-mails à esquerda, tarefas e metas à direita."""
        rev = dados.rev
        corpo = QWidget()
        grade = QHBoxLayout(corpo)
        grade.setContentsMargins(0, 0, 8, 8)
        grade.setSpacing(12)
        esquerda, direita = QVBoxLayout(), QVBoxLayout()
        for coluna in (esquerda, direita):
            coluna.setSpacing(12)
        agenda, emails = Cartao("Agenda"), Cartao("E-mails pendentes")
        tarefas_, metas = Cartao("Tarefas"), Cartao("Metas por tópico")
        self._agenda(agenda, dados.eventos, rev.hoje)
        self._emails(emails, dados.estrelas)
        self._tarefas(tarefas_, rev)
        self._metas(metas, rev)
        esquerda.addWidget(agenda)
        esquerda.addWidget(emails)
        esquerda.addStretch(1)
        direita.addWidget(tarefas_)
        direita.addWidget(metas)
        direita.addStretch(1)
        grade.addLayout(esquerda, 1)
        grade.addLayout(direita, 1)
        self.corpo = corpo
        self.area.setWidget(corpo)

    def _agenda(self, cartao: Cartao, eventos: list[dict], hoje: date) -> None:
        col = cartao.corpo
        cartao.definir_contador(f"hoje + 7 dias · {len(eventos)}")
        if not eventos:
            col.addWidget(self._texto("Nenhum evento. Use “+ Evento” para criar."))
            return
        dia_atual = None
        for ev in eventos:
            d = ev["inicio"].astimezone(FUSO).date() if isinstance(ev["inicio"], datetime) else ev["inicio"]
            if d != dia_atual:
                dia_atual = d
                nome = "Hoje" if d == hoje else "Amanhã" if d == hoje + timedelta(days=1) else f"{DIAS[d.weekday()]}, {d:%d/%m}"
                col.addWidget(rotulo(nome))
            hora = f"{ev['inicio'].astimezone(FUSO):%H:%M}" if isinstance(ev["inicio"], datetime) else "dia todo"
            serie = "  ↻" if ev.get("serie") else ""
            linha = Linha([(hora, "hora"), (ev["titulo"], "texto"), ("↻ repete" if serie else "", "discreto")], f"{hora}   {ev['titulo']}{serie}")
            col.addLayout(self._linha(linha, [("Editar", lambda _=False, ev=ev: self.editar_evento(ev)),
                                              ("Apagar", lambda _=False, ev=ev: self.apagar_evento(ev))]))

    def _tarefas(self, cartao: Cartao, rev: revisao.Revisao) -> None:
        col = cartao.corpo
        pendentes = len(rev.atrasadas) + len(rev.proximas) + len(rev.sem_data)
        cartao.definir_contador(f"{pendentes} pendente(s)" + (f" · {len(rev.atrasadas)} atrasada(s)" if rev.atrasadas else ""))
        for titulo, lista, atrasado in (
            ("Atrasadas", rev.atrasadas, True),
            ("Próximos 7 dias", rev.proximas, False),
            ("Sem data", rev.sem_data, False),
        ):
            if not lista:
                continue
            grupo = rotulo(f"{titulo} ({len(lista)})")
            if atrasado:
                grupo.setStyleSheet(grupo.styleSheet() + f"color: {tema.ALERTA};")
            col.addWidget(grupo)
            for t in sorted(lista, key=lambda t: motivo_importante(t.get("notas")) is None):  # importantes primeiro
                col.addLayout(self._linha_tarefa(t, False, atrasado))
        if not pendentes:
            col.addWidget(self._texto("Nenhuma tarefa pendente."))
        if rev.concluidas:
            col.addWidget(rotulo(f"Concluídas nos últimos 7 dias ({len(rev.concluidas)})"))
            for t in rev.concluidas:
                col.addLayout(self._linha_tarefa(t, True, False))

    def _linha_tarefa(self, t: dict, feita: bool, atrasada: bool) -> QHBoxLayout:
        motivo = motivo_importante(t.get("notas"))
        texto = self._descricao(t)
        if motivo is not None:
            texto += SEPARADOR + "★ importante" + (f": {motivo}" if motivo else "")
        caixa = self._caixa(texto, feita, lambda marcado, t=t: self._tarefa(t, marcado), atrasada)
        botoes = [] if feita else [
            ("Editar", lambda _=False, t=t: self.editar_tarefa(t)),
            ("Tirar ★" if motivo is not None else "★", lambda _=False, t=t: self.alternar_importante(t)),
            ("Apagar", lambda _=False, t=t: self.apagar_tarefa(t)),
        ]
        return self._linha(caixa, botoes)

    def _emails(self, cartao: Cartao, estrelas: list[dict]) -> None:
        col = cartao.corpo
        cartao.definir_contador(f"com estrela · {len(estrelas)}")
        if not estrelas:
            col.addWidget(self._texto("Nenhum e-mail com estrela. Ponha estrela no Gmail (ou peça à Eco) no que precisa resolver."))
            return
        for e in estrelas:
            quando = e.get("quando", "")
            linha = Linha([(e["de"], "texto"), (e["assunto"], "discreto"), (quando, "direita")], f"{e['de']}  —  {e['assunto']}  ·  {quando}")
            col.addLayout(self._linha(linha, [("Abrir", lambda _=False, e=e: self.abrir_email(e)),
                                              ("Resolvido", lambda _=False, e=e: self.resolver_email(e))]))

    def _metas(self, cartao: Cartao, rev: revisao.Revisao) -> None:
        col = cartao.corpo
        cartao.definir_contador(f"{len(rev.metas_abertas)} aberta(s)")
        if not rev.topicos:
            col.addWidget(self._texto("Nenhuma meta ainda. Peça à Eco: “defina uma meta…”."))
        for topico, (feitas, total) in sorted(rev.topicos.items()):
            linha = QHBoxLayout()
            linha.setContentsMargins(0, 8, 0, 2)
            linha.setSpacing(10)
            nome = QLabel(topico)
            nome.setObjectName("subtitulo")
            nome.setStyleSheet("background: transparent;")
            nome.setMinimumWidth(150)
            barra = QProgressBar()
            barra.setRange(0, max(1, total))
            barra.setValue(feitas)
            barra.setTextVisible(False)
            barra.setFixedHeight(8)
            conta = QLabel(f"{feitas} de {total}")
            conta.setObjectName("contador")
            conta.setStyleSheet("background: transparent;")
            linha.addWidget(nome)
            linha.addWidget(barra, 1)
            linha.addWidget(conta)
            col.addLayout(linha)
            for m in rev.metas_abertas:
                if (m.topico or "Sem tópico") == topico:
                    atrasada = bool(m.prazo and m.prazo < rev.hoje)
                    prazo = f"{SEPARADOR}prazo {m.prazo:%d/%m}" if m.prazo else ""
                    aviso = f"{SEPARADOR}atrasada" if atrasada else ""  # a cor não é o único sinal
                    col.addWidget(self._caixa(m.titulo + prazo + aviso, False, lambda marcado, m=m: self._meta(m.titulo, marcado), atrasada))
            for m in rev.metas_concluidas:
                if (m.topico or "Sem tópico") == topico:
                    col.addWidget(self._caixa(m.titulo, True, lambda marcado, m=m: self._meta(m.titulo, marcado)))

    @staticmethod
    def _descricao(t: dict) -> str:
        extra = f"{SEPARADOR}{t['lista']}" if t.get("lista_visivel") else ""
        prazo = f"{SEPARADOR}{t['prazo']:%d/%m}" if t.get("prazo") else ""
        return t["titulo"] + prazo + extra

    @staticmethod
    def _linha(principal: QWidget, botoes: list[tuple[str, Callable]]) -> QHBoxLayout:
        linha = QHBoxLayout()
        linha.setSpacing(2)
        linha.addWidget(principal, 1)
        for texto, acao in botoes:
            b = Botao(texto, "perigo" if texto == "Apagar" else "mini")
            b.clicked.connect(acao)
            linha.addWidget(b)
        return linha

    @staticmethod
    def _texto(texto: str, discreto: bool = True) -> QLabel:
        r = QLabel(texto)
        r.setWordWrap(True)
        if discreto:
            r.setObjectName("discreto")
        r.setStyleSheet("background: transparent; padding: 4px 0;")
        return r

    @staticmethod
    def _caixa(texto: str, marcada: bool, ao_mudar: Callable[[bool], None], atrasada: bool = False) -> Caixa:
        c = Caixa(texto, atrasada)
        c.setChecked(marcada)
        c.clicked.connect(ao_mudar)  # `clicked` só dispara com clique de verdade, não com setChecked
        return c

    # --- perguntas (trocáveis nos testes) ------------------------------------------------------------------------
    def _confirmar_padrao(self, frase: str) -> bool:
        return dialogos.perguntar(self, "Confirmar", frase, sim="Apagar" if frase.startswith("Apagar") else "Sim", destrutivo=frase.startswith("Apagar"))

    def _escopo_padrao(self, frase: str) -> str | None:
        return dialogos.escolher(self, "Evento que repete", frase, [(nome, valor) for nome, valor in ESCOPOS])

    def _escopo(self, ev: dict, verbo: str) -> tuple[bool, str | None]:
        """(seguir, escopo). Evento comum: (True, None). Série: pergunta só este / próximos / todos."""
        if not ev.get("serie"):
            return True, None
        escopo = self._escolher_escopo(f"“{ev['titulo']}” faz parte de uma série. {verbo} qual?")
        return escopo is not None, escopo

    # --- ações: tarefas e metas ----------------------------------------------------------------------------------
    def _tarefa(self, t: dict, marcado: bool) -> None:
        acao = self.acoes.concluir_tarefa if marcado else self.acoes.reabrir_tarefa
        self.atualizar(antes=lambda: acao(t))

    def _meta(self, titulo: str, marcado: bool) -> None:
        self.atualizar(antes=lambda: notas.marcar_meta(self.cfg.pasta_dados, titulo, marcado))

    def nova_tarefa(self) -> None:
        d = DialogoTarefa(self, None, self._listas)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.salvar_tarefa(None, d.dados())

    def editar_tarefa(self, t: dict) -> None:
        d = DialogoTarefa(self, t, self._listas)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.salvar_tarefa(t, d.dados())

    def salvar_tarefa(self, t: dict | None, dados: dict) -> None:
        if t is None:
            self.atualizar(antes=lambda: self.acoes.criar_tarefa(dados))
        else:
            self.atualizar(antes=lambda: self.acoes.editar_tarefa(t, dados))

    def alternar_importante(self, t: dict, motivo: str | None = None) -> None:
        """★ marca (pede o motivo); "Tirar ★" desmarca."""
        if motivo_importante(t.get("notas")) is not None:
            self.atualizar(antes=lambda: self.acoes.importante(t, None))
            return
        if motivo is None:
            motivo, ok = dialogos.texto(self, "Tarefa importante", f"Por que “{t['titulo']}” é importante? (motivo ou tópico, opcional)")
            if not ok:
                return
        self.atualizar(antes=lambda: self.acoes.importante(t, motivo.strip()))

    def apagar_tarefa(self, t: dict) -> None:
        if self._confirmar(f"Apagar a tarefa “{t['titulo']}”?"):
            self.atualizar(antes=lambda: self.acoes.apagar_tarefa(t))

    # --- ações: eventos ------------------------------------------------------------------------------------------
    def novo_evento(self) -> None:
        d = DialogoEvento(self, None, datetime.now(FUSO).date())
        if d.exec() == QDialog.DialogCode.Accepted:
            self.salvar_evento(None, d.dados())

    def editar_evento(self, ev: dict) -> None:
        d = DialogoEvento(self, ev, datetime.now(FUSO).date())
        if d.exec() == QDialog.DialogCode.Accepted:
            self.salvar_evento(ev, d.dados())

    def salvar_evento(self, ev: dict | None, dados: dict) -> None:
        if ev is None:
            self.atualizar(antes=lambda: self.acoes.criar_evento(dados))
            return
        seguir, escopo = self._escopo(ev, "Alterar")
        if seguir:
            self.atualizar(antes=lambda: self.acoes.editar_evento(ev, escopo, dados))

    def apagar_evento(self, ev: dict) -> None:
        if ev.get("serie"):
            seguir, escopo = self._escopo(ev, "Apagar")
        else:
            seguir, escopo = self._confirmar(f"Apagar o evento “{ev['titulo']}”?"), None
        if seguir:
            self.atualizar(antes=lambda: self.acoes.apagar_evento(ev, escopo))

    # --- ações: e-mails ------------------------------------------------------------------------------------------
    def resolver_email(self, e: dict) -> None:
        self.atualizar(antes=lambda: self.acoes.resolver_email(e))

    @staticmethod
    def abrir_email(e: dict) -> None:
        QDesktopServices.openUrl(QUrl(f"https://mail.google.com/mail/u/0/#all/{e['id']}"))
