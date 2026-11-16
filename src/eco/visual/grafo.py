"""Aba Grafo (Qt): as ideias como pequenas orbes ligadas por fios de luz, agrupadas por espaço.

Só interface. Os dados estão em `eco.grafo.dados`, a análise pela Claude em `eco.grafo.analise` e a posição física
em `eco.grafo.layout`. Passar o mouse numa ideia abre um cartão com o título e as conversas citadas; clicar numa
conversa do cartão emite `abrir_conversa` e a janela volta para a aba Conversas nela.

Hierarquia e pesos (escopo de 10/11/2026): tronco e galho são orbes maiores; o fio entre pai e filho é contínuo e
claro; os outros fios ficam mais grossos e visíveis quanto maior o peso (regras em `eco.grafo.pesos`). Entidade
(pessoa, projeto, nicho) ganha um anel tracejado.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QStackedWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from eco.chat.armazenamento import Banco
from eco.config import PASTA_ECO, Config
from eco.ferramentas import memoria
from eco.grafo.analise import Analisador, Completador, Resumo
from eco.grafo.dados import Grafo, Ideia, Ligacao
from eco.grafo.fatos import ServicoMemoria
from eco.grafo.layout import Layout
from eco.grafo.pesos import Aresta, calcular
from eco.visual import dialogos, tema
from eco.visual.chat import ESTILO
from eco.visual.componentes import Botao, Cartao

COR_GERAL = tema.COR_GERAL
CORES_ORIGEM = dict(tema.COR_ORIGEM)
NOMES_ORIGEM = {"memoria": "Memória", "fato": "Memória", "notas": "Notas", "metas": "Metas", "manual": "Criada por você", "chat": "Conversas"}
LIMITE_CONFIRMAR_USD = 0.05  # estimativa acima disso pede confirmação antes de analisar


@dataclass
class No:
    ideia: Ideia
    cor: str
    grupo: str
    raio: float = 8.0
    nivel: str = "folha"  # tronco | galho | folha, já considerando o pai
    importancia: float = 0.0  # ligações + vezes que o assunto apareceu (a orbe usa no brilho)


def _dia_do_bloco(dia: str) -> str:
    """AAAA-MM-DD como 'ter, 10/11/2026'."""
    try:
        d = datetime.fromisoformat(dia)
    except ValueError:
        return dia
    return f"{('seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom')[d.weekday()]}, {d:%d/%m/%Y}"


def _data(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d/%m/%Y")
    except ValueError:
        return iso[:10]


def _nivel(ideia: Ideia, por_id: dict, tem_filhos: set) -> str:
    """Nível na tela: pela profundidade quando há pai (filho de tronco é galho; abaixo disso, folha); sem pai, o que a
    análise disse, e quem tem filhos é tronco."""
    n, atual, vistos = 0, ideia.pai, set()
    while atual in por_id and atual not in vistos:
        vistos.add(atual)
        n += 1
        atual = por_id[atual].pai
    if n == 0:
        return "tronco" if ideia.id in tem_filhos or ideia.nivel == "tronco" else (ideia.nivel or "folha")
    if n == 1:
        return "galho" if ideia.id in tem_filhos or ideia.nivel != "folha" else "folha"
    return "folha"


def _limpar(lay) -> None:
    """Esvazia um layout, inclusive as linhas de botões (layouts dentro dele), sem deixar widget órfão na tela."""
    while lay.count():
        item = lay.takeAt(0)
        if item.widget() is not None:
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout() is not None:
            _limpar(item.layout())
            item.layout().deleteLater()


class Sinais(QObject):
    progresso = Signal(int, int, str)
    terminou = Signal(object)  # Resumo ou Exception


class CartaoIdeia(QFrame):
    """Cartão que aparece ao lado da ideia. Fica aberto enquanto o mouse estiver na ideia ou nele."""

    conversa_escolhida = Signal(str)
    nota_escolhida = Signal(str)  # AAAA-MM-DD: abre essa página no Bloco
    ideia_escolhida = Signal(str)  # E8: navegar para uma ideia ligada, o pai ou um filho
    acao = Signal(str, str)  # E8: (arquivar | proteger | desproteger | historico, id da ideia)

    def __init__(self, vista: "VistaGrafo") -> None:
        super().__init__(vista)
        self.vista = vista
        self.setObjectName("cartao")
        self.setStyleSheet(
            ESTILO + f"QFrame#cartao {{ background: {tema.ELEVADO}; border: 1px solid {tema.BORDA_FORTE}; border-radius: {tema.RAIO_CARTAO}px; }}"
            f"QPushButton#fonte {{ text-align: left; background: transparent; border: none; color: {tema.VERDE_LUZ}; padding: 3px 0; }}"
            f"QPushButton#fonte:hover, QPushButton#fonte:focus {{ color: {tema.TEXTO}; text-decoration: underline; }}"
        )
        self.setFixedWidth(280)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 12, 14, 12)
        self.lay.setSpacing(4)
        self.hide()

    def _ir(self, texto: str, id_: str, dica: str = "") -> None:
        curto = texto if len(texto) <= 36 else texto[:35].rstrip() + "…"  # botão não quebra linha: o resto vai na dica
        b = QPushButton(curto)
        b.setObjectName("fonte")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFlat(True)
        b.setToolTip((dica or texto) + "\n(clique para ir até ela)")
        b.clicked.connect(lambda _=False, i=id_: self.ideia_escolhida.emit(i))
        self.lay.addWidget(b)

    def mostrar(
        self, no: No, fontes: list, ligacoes: list[tuple[str, str]], detalhes: str = "", aviso: str = "", dias: list[str] | None = None,
        pai: tuple[str, str] | None = None, filhos: list[tuple[str, str]] | None = None, origem: str = "", com_acoes: bool = False,
    ) -> None:
        """`ligacoes`: (id da outra ideia, texto com a relação e o motivo), clicáveis. `pai` e `filhos`: (id, nome) para
        navegar pela hierarquia. `com_acoes`: a ideia está selecionada (clique) e o cartão mostra arquivar e proteger."""
        _limpar(self.lay)
        t = QLabel(no.ideia.nome)
        t.setWordWrap(True)
        t.setStyleSheet(f"font-size:{tema.SUBTITULO}px; font-weight:600; color:{no.cor};")
        self.lay.addWidget(t)
        origem = NOMES_ORIGEM.get(no.ideia.origem, no.ideia.origem)
        if no.ideia.origem == "chat" and fontes:
            origem = f"Espaço: {fontes[0].espaco_nome}"
        sub = QLabel(origem + (f"  ·  {detalhes}" if detalhes else ""))
        sub.setObjectName("discreto")
        sub.setWordWrap(True)
        self.lay.addWidget(sub)
        if aviso:
            av = QLabel(aviso)
            av.setWordWrap(True)
            av.setStyleSheet(f"color:{tema.ALERTA};")
            self.lay.addWidget(av)
        if no.ideia.resumo:
            r = QLabel(no.ideia.resumo)
            r.setWordWrap(True)
            self.lay.addWidget(r)
        if fontes:
            cab = QLabel("Conversas citadas:")
            cab.setObjectName("discreto")
            self.lay.addWidget(cab)
            for f in fontes[:6]:
                b = QPushButton(f"{f.titulo}\n{f.espaco_nome}  ·  {_data(f.atualizado_em)}")
                b.setObjectName("fonte")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setFlat(True)
                b.clicked.connect(lambda _=False, cid=f.conversa_id: self.conversa_escolhida.emit(cid))
                self.lay.addWidget(b)
            if len(fontes) > 6:
                mais = QLabel(f"+ {len(fontes) - 6} conversas")
                mais.setObjectName("discreto")
                self.lay.addWidget(mais)
        elif no.ideia.origem != "fato" and not dias:
            vazio = QLabel("Sem conversa de origem.")
            vazio.setObjectName("discreto")
            self.lay.addWidget(vazio)
        if dias:
            cab = QLabel("Anotado no bloco em:" + ("  (assunto recorrente)" if len(dias) > 1 else ""))
            cab.setObjectName("discreto")
            self.lay.addWidget(cab)
            for dia in dias[:6]:
                b = QPushButton(_dia_do_bloco(dia))
                b.setObjectName("fonte")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setFlat(True)
                b.clicked.connect(lambda _=False, d=dia: self.nota_escolhida.emit(d))
                self.lay.addWidget(b)
            if len(dias) > 6:
                mais = QLabel(f"+ {len(dias) - 6} dias")
                mais.setObjectName("discreto")
                self.lay.addWidget(mais)
        if pai is not None:
            cab = QLabel("Dentro de:")
            cab.setObjectName("discreto")
            self.lay.addWidget(cab)
            self._ir(f"↑ {pai[1]}", pai[0])
        if filhos:
            cab = QLabel(f"Abaixo dela ({len(filhos)}):")
            cab.setObjectName("discreto")
            self.lay.addWidget(cab)
            for id_, nome in filhos[:6]:
                self._ir(f"↓ {nome}", id_)
        if ligacoes:
            cab = QLabel("Ligada a:")
            cab.setObjectName("discreto")
            self.lay.addWidget(cab)
            for id_, texto in ligacoes[:6]:
                principal, _, motivo = texto.partition(" — ")
                self._ir(f"→ {principal}", id_, texto)
            if len(ligacoes) > 6:
                mais = QLabel(f"+ {len(ligacoes) - 6} ligações")
                mais.setObjectName("discreto")
                self.lay.addWidget(mais)
        if origem:
            o = QLabel(origem)
            o.setObjectName("discreto")
            o.setWordWrap(True)
            self.lay.addWidget(o)
        if com_acoes:
            botoes = [("Arquivar", "arquivar"), ("Desproteger" if no.ideia.protegido else "Proteger",
                                                  "desproteger" if no.ideia.protegido else "proteger"), ("Histórico", "historico")]
            for fatia in (botoes[:2], botoes[2:]):  # duas linhas: no cartão estreito, três botões não cabem
                linha = QHBoxLayout()
                linha.setSpacing(6)
                for texto, acao in fatia:
                    b = Botao(texto)
                    b.clicked.connect(lambda _=False, a=acao, i=no.ideia.id: self.acao.emit(a, i))
                    linha.addWidget(b)
                linha.addStretch(1)
                self.lay.addLayout(linha)
        elif not no.ideia.arquivada:
            dica = QLabel("Clique na ideia para arquivar, proteger ou ver o histórico.")
            dica.setObjectName("discreto")
            dica.setWordWrap(True)
            self.lay.addWidget(dica)
        self.style().unpolish(self)
        self.style().polish(self)
        self._ajustar_altura()
        self.show()
        self.raise_()
        QTimer.singleShot(0, self._ajustar_altura)  # de novo quando o layout já tiver medido tudo (cartão trocado às pressas)

    def _ajustar_altura(self) -> None:
        self.lay.invalidate()
        self.lay.activate()
        altura = max(self.lay.sizeHint().height(), self.lay.totalHeightForWidth(self.width()))  # rótulos com quebra de linha
        if altura > 0 and altura != self.height():
            self.setFixedHeight(altura)
            if self.isVisible():
                foco = self.vista.hover or self.vista.selecionado
                if foco in self.vista.nos:
                    self.vista._posicionar_cartao(foco)

    def enterEvent(self, e) -> None:
        self.vista.sobre_cartao = True
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self.vista.sobre_cartao = False
        self.vista.agendar_esconder()
        super().leaveEvent(e)


class VistaGrafo(QWidget):
    abrir_conversa = Signal(str)
    abrir_nota = Signal(str)

    def __init__(self, banco: Banco, grafo: Grafo, cfg: Config) -> None:
        super().__init__()
        self.banco, self.grafo, self.cfg = banco, grafo, cfg
        self.memoria = ServicoMemoria(grafo, cfg.pasta_dados)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.nos: dict[str, No] = {}
        self.ligacoes: list[Aresta] = []  # hierarquia, ligações gravadas e mesma sessão, com peso final
        self.vizinhos: dict[str, set[str]] = {}
        self.fisica = Layout([], {}, [])
        self.zoom, self.cx, self.cy = 1.0, 0.0, 0.0
        self.hover: str | None = None
        self.selecionado: str | None = None
        self.sobre_cartao = False
        self.modo_ligar: str | None = None  # id da ideia de origem enquanto escolhe a de destino
        self.modo_pai: str | None = None  # id da ideia que vai ficar dentro da próxima clicada
        self._arrasto: tuple[str, QPointF] | None = None
        self._pan: QPointF | None = None
        self._moveu = False
        self._fase = 0.0
        self._cursor = QPointF()
        self.cartao = CartaoIdeia(self)
        self.cartao.conversa_escolhida.connect(self.abrir_conversa)
        self.cartao.nota_escolhida.connect(self.abrir_nota)
        self.cartao.ideia_escolhida.connect(self.navegar)
        self.cartao.acao.connect(self._acao_do_cartao)
        self.ao_mudar: Callable[[], None] | None = None  # E8: o painel Gerenciar acompanha o que muda no mapa
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._esconder = QTimer(self)
        self._esconder.setSingleShot(True)
        self._esconder.setInterval(260)
        self._esconder.timeout.connect(self._esconder_se_solto)

    # --- dados --------------------------------------------------------------------------------------
    def recarregar(self, reorganizar: bool = False) -> None:
        try:
            self.memoria.memorias.inicializar()  # a memória mora no banco (D2); na primeira vez, sai do memoria.md
        except OSError:
            pass
        cores = {e.id: e.cor for e in self.banco.listar_espacos()}
        ideias = self.grafo.listar_ideias()
        self.ligacoes = calcular(ideias, self.grafo.listar_ligacoes(so_ativas=False), self.grafo.mesma_sessao())
        fontes = {l["ideia_id"]: l["n"] for l in self.banco._consultar("SELECT ideia_id, COUNT(*) AS n FROM ideia_fontes GROUP BY ideia_id")}
        for id_, n in self.grafo.contagem_dias().items():  # assunto que volta em vários dias do bloco também engorda o nó
            fontes[id_] = fontes.get(id_, 0) + n
        self.vizinhos = {i.id: set() for i in ideias}
        for l in self.ligacoes:
            self.vizinhos.setdefault(l.a, set()).add(l.b)
            self.vizinhos.setdefault(l.b, set()).add(l.a)
        nos: dict[str, No] = {}
        por_id = {i.id: i for i in ideias}
        tem_filhos = {i.pai for i in ideias if i.pai}
        for i in ideias:
            if i.origem == "chat":
                cor, grupo = (cores.get(i.espaco_id, COR_GERAL), i.espaco_id or "geral")
            else:
                cor, grupo = CORES_ORIGEM.get(i.origem, COR_GERAL), i.origem
            nivel = _nivel(i, por_id, tem_filhos)
            raio = 7.0 + 2.4 * math.sqrt(len(self.vizinhos.get(i.id, ()))) + 1.3 * math.sqrt(fontes.get(i.id, 0))
            raio += {"tronco": 6.0, "galho": 2.5}.get(nivel, 0.0)
            nos[i.id] = No(i, cor, grupo, raio, nivel, len(self.vizinhos.get(i.id, ())) + fontes.get(i.id, 0))
        self._posicionar(nos, reorganizar)
        if self.hover not in nos:
            self.hover = None
        if self.selecionado not in nos:
            self.selecionado = None
        self.cartao.hide()
        self.ajustar()
        self.update()

    def _posicionar(self, nos: dict[str, No], reorganizar: bool) -> None:
        """Física 2D (a orbe sobrescreve com as camadas da esfera)."""
        anteriores = {} if reorganizar else {id_: self.fisica.posicao(id_) for id_ in self.fisica.ids}
        self.nos = nos
        self.fisica = Layout(list(nos), {k: n.grupo for k, n in nos.items()}, [(l.a, l.b, l.peso) for l in self.ligacoes])
        for id_, (x, y) in anteriores.items():
            if id_ in self.fisica.indice:
                self.fisica.pos[self.fisica.indice[id_]] = (x, y)
        self.fisica.assentar(500)

    def ajustar(self) -> None:
        if not self.nos or self.width() < 50:
            self.zoom, self.cx, self.cy = 1.0, 0.0, 0.0
            return
        xs, ys = self.fisica.pos[:, 0], self.fisica.pos[:, 1]
        bw, bh = max(1.0, float(xs.max() - xs.min())) + 140, max(1.0, float(ys.max() - ys.min())) + 140
        self.zoom = max(0.25, min(self.width() / bw, self.height() / bh, 1.6))
        self.cx, self.cy = float((xs.max() + xs.min()) / 2), float((ys.max() + ys.min()) / 2)
        self.update()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        foco = self.hover or self.selecionado
        if self.cartao.isVisible() and foco in self.nos:  # o painel Gerenciar abriu ou fechou: o cartão acompanha
            self._posicionar_cartao(foco)
        if self.nos and self._pan is None:
            self.ajustar()

    # --- geometria ---------------------------------------------------------------------------------
    def _tela(self, x: float, y: float) -> QPointF:
        return QPointF((x - self.cx) * self.zoom + self.width() / 2, (y - self.cy) * self.zoom + self.height() / 2)

    def _ponto(self, id_: str) -> QPointF:
        """Onde a ideia está na tela agora (a orbe sobrescreve com a projeção 3D)."""
        return self._tela(*self.fisica.posicao(id_))

    def _raio_tela(self, id_: str) -> float:
        return self.nos[id_].raio * max(self.zoom, 0.6)

    def _mundo(self, p: QPointF) -> tuple[float, float]:
        return (p.x() - self.width() / 2) / self.zoom + self.cx, (p.y() - self.height() / 2) / self.zoom + self.cy

    def _no_em(self, p: QPointF) -> str | None:
        melhor, dmin = None, 1e9
        for id_, n in self.nos.items():
            c = self._ponto(id_)
            d = math.hypot(c.x() - p.x(), c.y() - p.y())
            if d <= self._raio_tela(id_) + 5 and d < dmin:
                melhor, dmin = id_, d
        return melhor

    def _ligacao_em(self, p: QPointF) -> Ligacao | None:
        for l in self.ligacoes:
            a, b = self._ponto(l.a), self._ponto(l.b)
            vx, vy = b.x() - a.x(), b.y() - a.y()
            comp2 = vx * vx + vy * vy
            if comp2 < 1:
                continue
            t = max(0.0, min(1.0, ((p.x() - a.x()) * vx + (p.y() - a.y()) * vy) / comp2))
            if math.hypot(a.x() + t * vx - p.x(), a.y() + t * vy - p.y()) <= 6:
                return l
        return None

    # --- animação ----------------------------------------------------------------------------------
    def _acordar(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        if not self.fisica.assentado:
            self.fisica.passo()
        self._fase = (self._fase + 0.018) % 1.0
        self.update()
        if self.fisica.assentado and self.hover is None and self.modo_ligar is None and self.modo_pai is None and self._arrasto is None:
            self._timer.stop()

    # --- pintura -----------------------------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.nos:
            p.setPen(QColor(tema.TEXTO_2))
            p.setFont(tema.fonte_texto(tema.CORPO))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       "Ainda não há ideias.\nGuarde conversas e clique em \"Atualizar grafo\" para a Eco extraí-las.")
            return
        foco = self.hover or self.selecionado
        perto = (self.vizinhos.get(foco, set()) | {foco}) if foco else set()
        for l in self.ligacoes:
            a, b = self._ponto(l.a), self._ponto(l.b)
            destaque = foco is not None and (l.a == foco or l.b == foco)
            if l.hierarquia:  # pai e filho: fio contínuo e claro
                cor = QColor(tema.TEXTO)
                cor.setAlpha(210 if destaque else (40 if foco else 120))
                p.setPen(QPen(cor, 2.6 if destaque else 1.8))
            else:
                cor = QColor(self.nos[l.a].cor)
                base = 25 + 170 * l.peso
                cor.setAlpha(int(min(230, base + 60)) if destaque else int(base * (0.35 if foco else 1.0)))
                p.setPen(QPen(cor, (0.5 + 2.4 * l.peso) * (1.3 if destaque else 1.0)))
            p.drawLine(a, b)
            if destaque:  # pulso de luz saindo da ideia em foco
                ini, fim = (a, b) if l.a == foco else (b, a)
                q = QPointF(ini.x() + (fim.x() - ini.x()) * self._fase, ini.y() + (fim.y() - ini.y()) * self._fase)
                halo = QRadialGradient(q, 9)
                halo.setColorAt(0, tema.cor(tema.VERDE_LUZ, 0.95))
                halo.setColorAt(1, tema.cor(tema.VERDE_LUZ, 0.0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(halo)
                p.drawEllipse(q, 9, 9)
        if (self.modo_ligar or self.modo_pai) in self.nos:
            o = self._ponto(self.modo_ligar or self.modo_pai)
            p.setPen(QPen(QColor(tema.VERDE_LUZ), 1.4, Qt.PenStyle.DashLine))
            p.drawLine(o, self._cursor)
        zoom_texto = self.zoom >= 1.2
        for id_, n in self.nos.items():
            c = self._ponto(id_)
            r = n.raio * max(self.zoom, 0.6)
            esmaecer = foco is not None and id_ not in perto
            cor = QColor(n.cor)
            halo = QRadialGradient(c, r * 3.2)
            halo.setColorAt(0.25, QColor(cor.red(), cor.green(), cor.blue(), 25 if esmaecer else 110))
            halo.setColorAt(1.0, QColor(cor.red(), cor.green(), cor.blue(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawEllipse(c, r * 3.2, r * 3.2)
            nucleo = QRadialGradient(QPointF(c.x() - r * 0.3, c.y() - r * 0.35), r * 1.5)
            claro = cor.lighter(170)
            for cc in (cor, claro):
                cc.setAlpha(70 if esmaecer else 255)
            nucleo.setColorAt(0.0, claro)
            nucleo.setColorAt(0.5, cor)
            escuro = cor.darker(170)
            escuro.setAlpha(70 if esmaecer else 255)
            nucleo.setColorAt(1.0, escuro)
            p.setBrush(nucleo)
            p.drawEllipse(c, r, r)
            if n.ideia.tipo == "entidade":
                p.setBrush(Qt.BrushStyle.NoBrush)
                anel = QColor(cor.lighter(150))
                anel.setAlpha(60 if esmaecer else 200)
                p.setPen(QPen(anel, 1.2, Qt.PenStyle.DashLine))
                p.drawEllipse(c, r + 2.5, r + 2.5)
            if id_ == self.selecionado or id_ == self.modo_ligar or id_ == self.modo_pai:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(tema.VERDE_LUZ), 1.5))
                p.drawEllipse(c, r + 4, r + 4)
            if (zoom_texto and not esmaecer) or id_ in perto or (n.nivel == "tronco" and not esmaecer):
                p.setPen(tema.cor(tema.TEXTO, 0.3 if esmaecer else 0.92))  # resto escurece para ~30% (documento, seção 7)
                fonte = tema.fonte_texto(tema.CORPO if n.nivel == "tronco" else tema.ROTULO + 1, negrito=n.nivel == "tronco")
                p.setFont(fonte)
                p.drawText(QRectF(c.x() - 90, c.y() + r + 4, 180, 30), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, n.ideia.nome)
        self._pintar_legenda(p)
        p.end()

    def _pintar_legenda(self, p: QPainter) -> None:
        """Grupos (espaços e origens) no canto e, durante "ligar"/"pôr dentro", a instrução embaixo."""
        grupos: dict[str, str] = {}
        nomes_espaco = {e.id: e.nome for e in self.banco.listar_espacos()}
        for n in self.nos.values():
            grupos.setdefault(NOMES_ORIGEM.get(n.grupo) or nomes_espaco.get(n.grupo, "Geral"), n.cor)
        y = 12
        p.setFont(tema.fonte_mono(tema.ROTULO))
        for nome, cor in sorted(grupos.items()):
            p.setBrush(QColor(cor))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(18, y + 6), 5, 5)
            p.setPen(QColor(tema.TEXTO_2))
            p.drawText(30, y + 11, nome)
            y += 18
        if self.modo_ligar or self.modo_pai:
            p.setPen(QColor(tema.TEXTO))
            texto = "Clique na ideia que quer ligar (Esc cancela)" if self.modo_ligar else "Clique na ideia que fica acima desta (Esc cancela)"
            p.drawText(self.rect().adjusted(0, 0, -12, -10), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight, texto)

    # --- cartão ------------------------------------------------------------------------------------
    def _mostrar_cartao(self, id_: str) -> None:
        n = self.nos.get(id_)
        if n is None:
            return
        fontes = self.grafo.fontes(id_)
        liga = []
        for l in sorted(self.ligacoes, key=lambda x: -x.peso):
            outro = l.b if l.a == id_ else l.a if l.b == id_ else None
            if outro in self.nos and not l.hierarquia:
                relacao = l.relacao_legivel() or "mesma conversa ou dia"
                nome = self.nos[outro].ideia.nome
                revisar = " · para revisar" if l.status == "revisar" else ""
                if l.de in (l.a, l.b) and l.relacao:  # relação com sentido: "depende de X" ou "X depende desta"
                    if l.de == id_:
                        frase = f"{relacao} {nome}"
                    else:
                        frase = f"{nome} {relacao[:-3] + ' desta' if relacao.endswith(' de') else relacao + ' esta'}"
                else:
                    frase = f"{nome}: {relacao}"
                liga.append((outro, frase + revisar + (f" — {l.motivo}" if l.motivo else "")))
        detalhes, aviso = "", ""
        if n.ideia.origem == "fato":
            f = next((f for f in self.memoria.memorias.fatos() if f.texto == n.ideia.resumo), None)
            importada = n.ideia.topico == "importada"
            detalhes = " · ".join(x for x in (("fixa" if n.ideia.topico == "fixo" else ""),
                                              "importada do claude.ai, só aparece pela busca" if importada else "",
                                              f.data if f else "") if x)
            if not importada and n.ideia.resumo in self.memoria.memorias.fora_do_prompt():
                aviso = "Memória cheia: esta lembrança não está indo para a conversa. Fixe-a ou esqueça outras."
        hierarquia = [{"tronco": "Tronco", "galho": "Galho", "folha": "Folha"}[n.nivel]]
        if n.ideia.pai in self.nos:
            hierarquia.append(f"dentro de {self.nos[n.ideia.pai].ideia.nome}")
        filhos = [x for x in self.nos.values() if x.ideia.pai == id_]
        if filhos:
            hierarquia.append(f"{len(filhos)} abaixo")
        if n.ideia.tipo == "entidade":
            hierarquia.append("entidade")
        detalhes = " · ".join(x for x in (", ".join(hierarquia), detalhes) if x)
        pai = (n.ideia.pai, self.nos[n.ideia.pai].ideia.nome) if n.ideia.pai in self.nos else None
        filhos_ids = [(x.ideia.id, x.ideia.nome) for x in filhos]
        quem = n.ideia.origem_detalhe or "sistema"
        origem = f"Criada por {quem} em {_data(n.ideia.criado_em)}" + (" · protegida" if n.ideia.protegido else "")
        self.cartao.mostrar(n, fontes, liga, detalhes, aviso, self.grafo.dias(id_), pai, filhos_ids, origem,
                            com_acoes=id_ == self.selecionado)
        self._posicionar_cartao(id_)

    def _posicionar_cartao(self, id_: str) -> None:
        """Cartão ao lado da ideia (do outro lado se não couber)."""
        c = self._ponto(id_)
        r = self._raio_tela(id_)
        x = c.x() + r + 16
        if x + self.cartao.width() > self.width() - 8:
            x = c.x() - r - 16 - self.cartao.width()
        y = max(8, min(c.y() - 24, self.height() - self.cartao.height() - 8))
        self.cartao.move(int(max(8, x)), int(y))

    def agendar_esconder(self) -> None:
        self._esconder.start()

    def _esconder_se_solto(self) -> None:
        if self.hover is None and not self.sobre_cartao and self.selecionado is None:
            self.cartao.hide()
        elif self.hover is None and not self.sobre_cartao and self.selecionado is not None:
            self._mostrar_cartao(self.selecionado)  # volta ao cartão da ideia selecionada

    # --- mouse --------------------------------------------------------------------------------------
    def mousePressEvent(self, e) -> None:
        self.setFocus()
        p = e.position()
        if e.button() == Qt.MouseButton.LeftButton:
            self._moveu = False
            alvo = self._no_em(p)
            if alvo is not None:
                self._arrasto = (alvo, p)
                self.fisica.fixo = self.fisica.indice[alvo]
            else:
                self._pan = p
        elif e.button() == Qt.MouseButton.RightButton:
            self._menu(p, e.globalPosition().toPoint())

    def mouseMoveEvent(self, e) -> None:
        p = e.position()
        self._cursor = p
        if self._arrasto is not None:
            id_, ini = self._arrasto
            if math.hypot(p.x() - ini.x(), p.y() - ini.y()) > 4:
                self._moveu = True
            if self._moveu and self.modo_ligar is None and self.modo_pai is None:
                self.fisica.pos[self.fisica.indice[id_]] = self._mundo(p)
                self.fisica.reaquecer()
                self._acordar()
            return
        if self._pan is not None:
            self.cx -= (p.x() - self._pan.x()) / self.zoom
            self.cy -= (p.y() - self._pan.y()) / self.zoom
            self._pan = p
            self.update()
            return
        alvo = self._no_em(p)
        if alvo != self.hover:
            self.hover = alvo
            self.setCursor(Qt.CursorShape.PointingHandCursor if alvo else Qt.CursorShape.ArrowCursor)
            if alvo:
                self._esconder.stop()
                self._mostrar_cartao(alvo)
                self._acordar()
            else:
                self.agendar_esconder()
            self.update()
        if alvo is None:
            l = self._ligacao_em(p)
            if l:
                relacao = l.relacao_legivel()
                if relacao and l.de in (l.a, l.b) and l.relacao:
                    outro = l.b if l.de == l.a else l.a
                    relacao = f"{self.nos[l.de].ideia.nome} {relacao} {self.nos[outro].ideia.nome}"
                texto = (f"{self.nos[l.a].ideia.nome} ↔ {self.nos[l.b].ideia.nome}\n" + (f"{relacao}\n" if relacao else "")
                         + l.descricao() + (" · para revisar" if l.status == "revisar" else "") + (f"\n{l.motivo}" if l.motivo else ""))
                QToolTip.showText(e.globalPosition().toPoint(), texto, self)
        if self.modo_ligar or self.modo_pai:
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        arrasto, self._arrasto, self._pan = self._arrasto, None, None
        self.fisica.fixo = None
        if arrasto is None:
            if not self._moveu and self._no_em(e.position()) is None:
                self.selecionado = None
                if self.modo_ligar is None and self.modo_pai is None:
                    self.cartao.hide()
                self.update()
            return
        if not self._moveu:
            self._clique_no(arrasto[0])

    def _clique_no(self, id_: str) -> None:
        """Clique (sem arrastar) numa ideia: termina "pôr dentro" ou "ligar", ou seleciona e abre o cartão."""
        if self.modo_pai is not None:
            filho, self.modo_pai = self.modo_pai, None
            if filho != id_:
                try:
                    self.grafo.definir_pai(filho, id_)
                except ValueError as erro:
                    dialogos.avisar(self, "Hierarquia", str(erro))
                self.recarregar_preservando()
            self.update()
            return
        if self.modo_ligar is not None:
            origem, self.modo_ligar = self.modo_ligar, None
            if origem != id_:
                self.grafo.ligar(origem, id_, "Ligação feita por você", "manual")
                self.recarregar_preservando()
            self.update()
            return
        self.selecionado = id_
        self._mostrar_cartao(id_)
        self.update()

    def navegar(self, id_: str) -> None:
        """E8 (19.6.10, 19.6.22): seleciona a ideia, traz para a vista e abre o cartão com as ligações dela."""
        if id_ not in self.nos:
            return
        self.selecionado = id_
        self._centralizar(id_)
        self._mostrar_cartao(id_)
        self._acordar()
        self.update()

    def _centralizar(self, id_: str) -> None:
        if id_ in self.fisica.indice:
            x, y = self.fisica.posicao(id_)
            self.cx, self.cy = float(x), float(y)

    def _acao_do_cartao(self, acao: str, id_: str) -> None:
        if id_ not in self.nos:
            return
        i = self.nos[id_].ideia
        if acao == "historico":
            from eco.grafo import consulta

            dialogos.avisar(self, "Histórico", consulta.historico(self.grafo, i.nome) if i.nome else "")
            return
        if acao == "arquivar":
            aviso = " Ela é protegida: só você pode arquivá-la, e é o que está fazendo." if i.protegido else ""
            if not dialogos.perguntar(self, "Arquivar ideia", f"Arquivar \"{i.nome}\"? Ela some do mapa e das conversas, mas "
                                      f"fica guardada e volta pelo painel Gerenciar.{aviso}", sim="Arquivar"):
                return
            self.grafo.arquivar(id_, "arquivada pelo dono")
            if i.origem == "fato":
                self.memoria.memorias.espelhar()
            self.selecionado = None
        elif acao in ("proteger", "desproteger"):
            if i.origem == "fato":
                self.memoria.fixar(id_, acao == "proteger")
            else:
                self.grafo.proteger(id_, acao == "proteger")
        self.recarregar_preservando()
        if id_ in self.nos and acao != "arquivar":
            self.navegar(id_)
        if self.ao_mudar is not None:
            self.ao_mudar()

    def recarregar_preservando(self) -> None:
        tinha_nos = bool(self.nos)
        zoom, cx, cy = self.zoom, self.cx, self.cy
        self.recarregar()
        if tinha_nos:  # o dono já arrumou a vista: não joga o zoom e a posição fora a cada atualização
            self.zoom, self.cx, self.cy = zoom, cx, cy

    def wheelEvent(self, e) -> None:
        antes = self._mundo(e.position())
        self.zoom = max(0.15, min(4.0, self.zoom * (1.12 if e.angleDelta().y() > 0 else 1 / 1.12)))
        depois = self._mundo(e.position())
        self.cx += antes[0] - depois[0]
        self.cy += antes[1] - depois[1]
        self.update()

    def leaveEvent(self, e) -> None:
        if self.hover is not None:
            self.hover = None
            self.agendar_esconder()
            self.update()
        super().leaveEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape and (self.modo_ligar or self.modo_pai):
            self.modo_ligar = self.modo_pai = None
            self.update()
        else:
            super().keyPressEvent(e)

    # --- menu de contexto --------------------------------------------------------------------------
    def _menu(self, p: QPointF, global_pos) -> None:
        alvo = self._no_em(p)
        menu = QMenu(self)
        if alvo is not None:
            n = self.nos[alvo]
            eh_fato = n.ideia.origem == "fato"
            a_editar = menu.addAction("Editar lembrança…") if eh_fato else None
            a_fixar = menu.addAction("Desafixar lembrança" if n.ideia.topico == "fixo" else "Fixar lembrança (nunca cortar)") if eh_fato else None
            a_renomear = menu.addAction("Renomear título…" if eh_fato else "Renomear…")
            a_ligar = menu.addAction("Ligar a outra ideia…")
            a_pai = menu.addAction("Pôr dentro de outra ideia…")
            a_soltar = menu.addAction(f"Tirar de dentro de {self.nos[n.ideia.pai].ideia.nome}") if n.ideia.pai in self.nos else None
            sub = menu.addMenu("Desligar de")
            desligar = {}
            for outro in sorted(self.vizinhos.get(alvo, ()), key=lambda o: self.nos[o].ideia.nome if o in self.nos else ""):
                if outro in self.nos:
                    desligar[sub.addAction(self.nos[outro].ideia.nome)] = outro
            sub.setEnabled(bool(desligar))
            menu.addSeparator()
            a_arquivar = menu.addAction("Arquivar (sai do mapa, fica guardada)")
            a_proteger = menu.addAction("Desproteger" if n.ideia.protegido else "Proteger (nada automático mexe nela)")
            a_historico = menu.addAction("Ver histórico")
            menu.addSeparator()
            a_apagar = menu.addAction("Esquecer esta lembrança" if eh_fato else "Apagar ideia")
            escolha = menu.exec(global_pos)
            if escolha is None:
                return
            if escolha in (a_arquivar, a_proteger, a_historico):
                self._acao_do_cartao({a_arquivar: "arquivar", a_historico: "historico"}.get(
                    escolha, "desproteger" if n.ideia.protegido else "proteger"), alvo)
                return
            if eh_fato and escolha is a_editar:
                texto, ok = dialogos.texto_longo(self, "Editar lembrança", "Texto da lembrança:", n.ideia.resumo)
                if ok and texto.strip():
                    self._aplicar_memoria(lambda: self.memoria.editar(alvo, texto))
            elif eh_fato and escolha is a_fixar:
                self._aplicar_memoria(lambda: self.memoria.fixar(alvo, n.ideia.topico != "fixo"))
            elif eh_fato and escolha is a_apagar:
                if dialogos.perguntar(
                    self, "Esquecer lembrança", f"Esquecer esta lembrança?\n\n{n.ideia.resumo}\n\nEla sai do memoria.md e do grafo.",
                    sim="Esquecer", destrutivo=True,
                ):
                    self._aplicar_memoria(lambda: self.memoria.apagar(alvo))
            elif escolha is a_renomear:
                novo, ok = dialogos.texto(self, "Renomear ideia", "Novo nome:", n.ideia.nome)
                if ok and novo.strip():
                    self.grafo.renomear_ideia(alvo, novo)
                    self.recarregar_preservando()
            elif escolha is a_ligar:
                self.modo_ligar = alvo
                self._acordar()
                self.update()
            elif escolha is a_pai:
                self.modo_pai = alvo
                self._acordar()
                self.update()
            elif a_soltar is not None and escolha is a_soltar:
                self.grafo.definir_pai(alvo, None)
                self.recarregar_preservando()
            elif escolha in desligar:
                self._desfazer(alvo, desligar[escolha])
            elif escolha is a_apagar and not eh_fato:
                if dialogos.perguntar(self, "Apagar ideia", f"Apagar a ideia \"{n.ideia.nome}\" e as ligações dela?\nA análise não a recria depois.", sim="Apagar", destrutivo=True):
                    self.grafo.apagar_ideia(alvo)
                    self.recarregar_preservando()
            return
        l = self._ligacao_em(p)
        if l is not None:
            titulo = menu.addAction(f"{self.nos[l.a].ideia.nome} ↔ {self.nos[l.b].ideia.nome}")
            titulo.setEnabled(False)
            for linha in (l.descricao(), l.motivo):
                if linha:
                    m = menu.addAction(linha)
                    m.setEnabled(False)
            if l.relacao_legivel():
                info = menu.addAction(f"Relação: {l.relacao_legivel()}" + (" (para revisar)" if l.status == "revisar" else ""))
                info.setEnabled(False)
            a_aceitar = menu.addAction("Aceitar esta ligação") if l.status == "revisar" and l.ligacao_id else None
            tipos = {}
            if l.ligacao_id and not l.hierarquia:
                from eco.grafo.ligacao import RELACOES

                sub = menu.addMenu("Trocar o tipo da relação")
                for codigo, (rot, com_sentido, _t) in RELACOES.items():
                    if com_sentido:
                        tipos[sub.addAction(f"{self.nos[l.a].ideia.nome} {rot} {self.nos[l.b].ideia.nome}")] = (codigo, l.a)
                        tipos[sub.addAction(f"{self.nos[l.b].ideia.nome} {rot} {self.nos[l.a].ideia.nome}")] = (codigo, l.b)
                    else:
                        tipos[sub.addAction(rot)] = (codigo, "")
            a_desligar = menu.addAction("Separar (tirar de dentro)" if l.hierarquia else "Desligar estas ideias")
            escolha = menu.exec(global_pos)
            if escolha is None:
                return
            if a_aceitar is not None and escolha is a_aceitar:
                self.grafo.aceitar_ligacao(l.ligacao_id)
            elif escolha in tipos:
                self.grafo.mudar_relacao(l.ligacao_id, *tipos[escolha])
            elif escolha is a_desligar:
                self._desfazer(l.a, l.b)
                if self.ao_mudar is not None:
                    self.ao_mudar()
                return
            self.recarregar_preservando()
            if self.ao_mudar is not None:
                self.ao_mudar()
            return
        a_nova = menu.addAction("Nova ideia aqui…")
        a_lembranca = menu.addAction("Nova lembrança na memória…")
        a_ajustar = menu.addAction("Ajustar à tela")
        a_reorg = menu.addAction("Reorganizar")
        escolha = menu.exec(global_pos)
        if escolha is a_nova:
            nome, ok = dialogos.texto(self, "Nova ideia", "Nome da ideia:")
            if ok and nome.strip():
                ideia = self.grafo.criar_ideia(nome, None, "manual")
                self.recarregar_preservando()
                if ideia.id in self.fisica.indice:
                    self.fisica.pos[self.fisica.indice[ideia.id]] = self._mundo(p)
                    self.update()
        elif escolha is a_lembranca:
            texto, ok = dialogos.texto_longo(self, "Nova lembrança", "O que a Eco deve lembrar:")
            if ok and texto.strip():
                self._aplicar_memoria(lambda: self.memoria.adicionar(texto), posicao=self._mundo(p))
        elif escolha is a_ajustar:
            self.ajustar()
        elif escolha is a_reorg:
            self.recarregar(reorganizar=True)


    def _desfazer(self, a: str, b: str) -> None:
        """Desliga o par. Se um é pai do outro, separa a hierarquia; a ligação gravada (se houver) também cai."""
        for filho, pai in ((a, b), (b, a)):
            if filho in self.nos and self.nos[filho].ideia.pai == pai:
                self.grafo.definir_pai(filho, None)
        self.grafo.desligar(a, b)
        self.recarregar_preservando()

    def _aplicar_memoria(self, acao: Callable[[], Any], posicao: tuple[float, float] | None = None) -> None:
        """Roda uma operação sobre a memória e mostra o erro (arquivo editado à mão, por exemplo) em vez de quebrar."""
        try:
            resultado = acao()
        except (ValueError, OSError) as e:
            dialogos.avisar(self, "Memória", str(e))
            self.recarregar_preservando()
            return
        self.recarregar_preservando()
        if posicao is not None and isinstance(resultado, Ideia) and resultado.id in self.fisica.indice:
            self.fisica.pos[self.fisica.indice[resultado.id]] = posicao
            self.update()


class PainelGrafo(QWidget):
    abrir_conversa = Signal(str)
    abrir_nota = Signal(str)

    def __init__(
        self,
        cfg: Config,
        banco: Banco | None = None,
        completador: Completador | None = None,
        confirmar: Callable[[str], bool] | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.banco = banco or Banco(PASTA_ECO / "eco.db")
        self.grafo = Grafo(self.banco)
        self.grafo._local.autor = "clique na tela do grafo"  # o que for feito na tela (thread principal) fica assim no histórico
        self._completador = completador
        self._analisador: Analisador | None = None
        self.confirmar = confirmar or self._confirmar_padrao
        self.sinais = Sinais()
        self.sinais.progresso.connect(self._ao_progresso)
        self.sinais.terminou.connect(self._ao_terminar)
        self._parar = threading.Event()
        self.rodando = False
        self.setStyleSheet(ESTILO)

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 16, 20, 12)
        barra = QHBoxLayout()
        barra.setSpacing(8)
        titulo = QLabel("Grafo de ideias")
        titulo.setObjectName("titulo")
        barra.addWidget(titulo)
        self.status = QLabel("")
        self.status.setObjectName("discreto")
        barra.addWidget(self.status, 1)
        self.busca = QLineEdit()
        self.busca.setPlaceholderText("Buscar ideia…")
        self.busca.setToolTip("Nome ou assunto (Enter): leva até a ideia no mapa")
        self.busca.setMaximumWidth(220)
        self.busca.returnPressed.connect(self.buscar)
        barra.addWidget(self.busca)
        self.btn_gerenciar = Botao("Gerenciar")
        self.btn_gerenciar.setToolTip("Painel com revisões, propostas, contradições, arquivadas, protegidas, histórico e custos")
        self.btn_gerenciar.clicked.connect(lambda: self.alternar_gerenciar())
        barra.addWidget(self.btn_gerenciar)
        self.btn_vista = Botao("")
        self.btn_vista.setToolTip("Alterna entre a orbe 3D e o mapa 2D")
        self.btn_vista.clicked.connect(lambda: self.alternar_vista())
        self.btn_ajustar = Botao("Ajustar à tela")
        self.btn_reorg = Botao("Reorganizar")
        self.btn_atualizar = Botao("Atualizar grafo", "principal")
        self.btn_ajustar.clicked.connect(lambda: self.vista.ajustar())
        self.btn_reorg.clicked.connect(lambda: self.vista.recarregar(reorganizar=True))
        self.btn_atualizar.clicked.connect(lambda: self.atualizar())
        for b in (self.btn_vista, self.btn_ajustar, self.btn_reorg, self.btn_atualizar):
            barra.addWidget(b)
        raiz.addLayout(barra)
        from eco.visual.grafo_orbe import VistaOrbe  # aqui: grafo_orbe importa este módulo

        self.vista_2d = VistaGrafo(self.banco, self.grafo, cfg)
        self.vista_orbe = VistaOrbe(self.banco, self.grafo, cfg)
        self.pilha = QStackedWidget()
        for v in (self.vista_orbe, self.vista_2d):
            v.abrir_conversa.connect(self.abrir_conversa)
            v.abrir_nota.connect(self.abrir_nota)
            self.pilha.addWidget(v)
        self.mapa = Cartao("")
        self.mapa.layout().setContentsMargins(10, 12, 10, 10)
        self.mapa.cabecalho.setContentsMargins(20, 0, 0, 0)  # a marca HUD fica fora do texto
        self.mapa.corpo.addWidget(self.pilha, 1)
        corpo = QHBoxLayout()
        corpo.setSpacing(12)
        corpo.addWidget(self.mapa, 1)
        from eco.visual.gerenciar import PainelGerenciar

        self.gerenciar = PainelGerenciar(self.grafo, cfg, self.vista_2d.memoria.memorias,
                                         higiene_fabrica=self._higiene_com_ia, ligador_fabrica=self._ligador,
                                         importador_fabrica=self._importador)
        self.gerenciar.ideia_escolhida.connect(lambda i: self.vista.navegar(i))
        self.gerenciar.mudou.connect(self._depois_de_mudar)
        self.gerenciar.hide()
        corpo.addWidget(self.gerenciar)
        raiz.addLayout(corpo, 1)
        for v in (self.vista_orbe, self.vista_2d):
            v.ao_mudar = self._mapa_mudou
        self.vista: VistaGrafo = self.vista_2d if cfg.grafo_vista == "2d" else self.vista_orbe
        self.alternar_vista("2d" if cfg.grafo_vista == "2d" else "orbe", recarregar=False)
        self.vista.recarregar()
        self._resumo_status()

    def alternar_vista(self, qual: str | None = None, recarregar: bool = True) -> None:
        """"orbe" (esfera 3D, padrão) ou "2d"; sem argumento, troca para a outra."""
        if qual is None:
            qual = "2d" if self.vista is self.vista_orbe else "orbe"
        self.vista = self.vista_orbe if qual == "orbe" else self.vista_2d
        self.pilha.setCurrentWidget(self.vista)
        self.mapa.titulo.setText("Orbe de ideias" if qual == "orbe" else "Mapa de ideias")
        self.btn_vista.setText("Ver em 2D" if qual == "orbe" else "Ver orbe 3D")
        if recarregar:
            self.vista.recarregar_preservando()
            self._resumo_status()

    # --- E8: busca e painel Gerenciar ---------------------------------------------------------------------
    def buscar(self) -> None:
        texto = self.busca.text().strip()
        if not texto:
            return
        from eco.grafo import consulta

        try:
            sem = None
            from eco.grafo.semelhanca import Semelhanca

            sem = Semelhanca(self.grafo)
            ideia = consulta.achar(self.grafo, texto, sem)
        except ValueError as erro:
            self._resumo_status(str(erro))
            return
        self.vista.navegar(ideia.id)
        self._resumo_status(f"Achei: {ideia.nome}.")

    def alternar_gerenciar(self, mostrar: bool | None = None) -> None:
        mostrar = not self.gerenciar.isVisible() if mostrar is None else mostrar
        if mostrar:
            self.gerenciar.recarregar()
        self.gerenciar.setVisible(mostrar)
        self.btn_gerenciar.setText("Fechar painel" if mostrar else "Gerenciar")
        QTimer.singleShot(0, self._reajustar_mapa)  # depois que o layout der a nova largura ao mapa

    def _reajustar_mapa(self) -> None:
        sel = self.vista.selecionado
        self.vista.ajustar()
        if sel in self.vista.nos:
            self.vista.navegar(sel)

    def _depois_de_mudar(self) -> None:
        self.vista.recarregar_preservando()
        self._resumo_status()

    def _mapa_mudou(self) -> None:
        if self.gerenciar.isVisible():
            self.gerenciar.recarregar()
        self._resumo_status()

    def _higiene_com_ia(self):
        from eco.grafo.higiene import higiene_padrao
        from eco.uso import RegistroUso

        return higiene_padrao(self.cfg, self.banco, self.vista_2d.memoria.memorias, RegistroUso(PASTA_ECO / "uso.jsonl"))

    def _importador(self):
        from eco.importacao.servico import Importador
        from eco.uso import RegistroUso

        return Importador(self.cfg, self.banco, self.grafo, self.vista_2d.memoria.memorias, RegistroUso(PASTA_ECO / "uso.jsonl"))

    def _ligador(self):
        from eco.grafo.ligacao import ligador_padrao
        from eco.uso import RegistroUso

        return ligador_padrao(self.cfg, self.banco, RegistroUso(PASTA_ECO / "uso.jsonl"))

    # --- análise ----------------------------------------------------------------------------------
    def analisador(self) -> Analisador:
        from eco.grafo.analise import CompletadorClaude

        if self._analisador is None:
            if self._completador is None:
                from eco.uso import RegistroUso

                self._completador = CompletadorClaude(self.cfg, registro=RegistroUso(PASTA_ECO / "uso.jsonl"))
            self._analisador = Analisador(self.banco, self.grafo, self._completador, self.cfg)
            if self.cfg.ligacao_automatica and isinstance(self._completador, CompletadorClaude):
                from eco.grafo.ligacao import ligador_padrao

                self._analisador.ligador = ligador_padrao(self.cfg, self.banco, getattr(self._completador, "registro", None))
        return self._analisador

    def _resumo_status(self, extra: str = "") -> None:
        n, m = len(self.vista.nos), len(self.vista.ligacoes)
        from eco.grafo.memorias import Memorias

        usado = Memorias(self.grafo, self.cfg.pasta_dados).usado()
        mem = f"Memória: {usado}/{memoria.LIMITE_CARACTERES} caracteres. " if usado else ""
        self.status.setText((f"{n} ideias, {m} ligações. " if n else "") + mem + extra)

    def _confirmar_padrao(self, texto: str) -> bool:
        return dialogos.perguntar(self, "Atualizar grafo", texto, sim="Atualizar")

    def atualizar(self, auto: bool = False) -> None:
        """Analisa só o que mudou desde a última vez. Com `auto` (ao abrir a aba) não incomoda se não houver nada."""
        if self.rodando:
            if not auto:
                self._parar.set()
                self.status.setText("Parando…")
            return
        try:
            an = self.analisador()
            pend = an.pendentes()
        except Exception as e:  # sem chave da Anthropic, por exemplo
            self._resumo_status(f"Não consegui preparar a análise: {e}")
            return
        if not pend:
            self.vista.recarregar_preservando()
            self._resumo_status("Em dia: nada novo para analisar.")
            return
        restante = self.cfg.teto_grafo_usd - self.grafo.gasto_do_mes()
        if restante <= 0:
            self._resumo_status(f"Teto mensal do grafo atingido (US$ {self.cfg.teto_grafo_usd:.2f}). Ajuste ECO_GRAFO_TETO_USD para continuar.")
            return
        est = an.estimar(pend)
        if est.custo_usd > LIMITE_CONFIRMAR_USD and not self.confirmar(
            f"Vou analisar {est.chamadas} itens novos ou alterados com {self.cfg.modelo_grafo}.\n"
            f"Custo estimado: cerca de US$ {est.custo_usd:.3f} (teto restante do mês: US$ {restante:.2f}).\nContinuar?"
        ):
            self._resumo_status("Análise adiada.")
            return
        self.rodando = True
        self._parar.clear()
        self.btn_atualizar.setText("Parar")
        self.btn_atualizar.setObjectName("parar")
        self.btn_atualizar.update()

        def trabalho() -> None:
            try:
                r = an.executar(pend, lambda n, t, titulo: self.sinais.progresso.emit(n, t, titulo), self._parar)
                self.sinais.terminou.emit(r)
            except Exception as e:
                self.sinais.terminou.emit(e)

        threading.Thread(target=trabalho, daemon=True, name="eco-grafo").start()

    def _ao_progresso(self, n: int, total: int, titulo: str) -> None:
        self.status.setText(f"Analisando {n}/{total}: {titulo}")

    def _ao_terminar(self, resultado: Any) -> None:
        self.rodando = False
        self.btn_atualizar.setText("Atualizar grafo")
        self.btn_atualizar.setObjectName("principal")
        self.btn_atualizar.update()
        self.vista.recarregar_preservando()
        if isinstance(resultado, Exception):
            self._resumo_status(f"A análise falhou: {resultado}")
            return
        r: Resumo = resultado
        partes = [f"{r.analisadas} analisadas, {r.ideias_novas} ideias novas, {r.ligacoes_novas} ligações, ~US$ {r.custo:.4f}."]
        if r.parou_por_teto:
            partes.append("Parei no teto mensal do grafo.")
        if r.interrompido:
            partes.append("Interrompida.")
        if r.erros:
            partes.append(f"{len(r.erros)} com erro (tento de novo na próxima).")
        self._resumo_status(" ".join(partes))

    def ao_abrir(self) -> None:
        """A aba foi aberta: mostra o que há e analisa só o que mudou."""
        self.vista.recarregar_preservando()
        self.atualizar(auto=True)
