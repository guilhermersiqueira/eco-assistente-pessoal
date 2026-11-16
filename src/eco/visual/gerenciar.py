"""Painel "Gerenciar" da aba Grafo (etapa E8, requisito 19.6.19; confirmações do 19.6.18).

Uma coluna ao lado do mapa com tudo o que pede atenção, e o que dá para fazer ali mesmo:
- números (ideias, conexões, protegidas, arquivadas) e edição à mão do memoria.md pendente (importar ou desfazer);
- ligações para revisar e sugestões (aceitar, rejeitar, trocar o tipo);
- propostas de limpeza: contradições, informações repetidas, arquivar, agrupar (aplicar ou recusar);
- arquivadas (restaurar) e protegidas (desproteger);
- histórico recente e custos do grafo;
- "Revisar memória agora" e "Procurar ligações agora" (gastam frações de centavo; pedem confirmação).
Só interface: as regras estão em `grafo/higiene.py`, `grafo/ligacao.py`, `grafo/consulta.py` e `grafo/dados.py`.
Toda ação que tira, troca ou junta informação pergunta antes (`confirmar`), e nada é apagado.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QScrollArea, QVBoxLayout, QWidget

from eco.grafo.dados import Grafo
from eco.visual import dialogos, tema
from eco.visual.componentes import Botao, Cartao

ACOES_HISTORICO = {"criar": "criada", "atualizar": "alterada", "renomear": "renomeada", "apagar": "apagada",
                   "arquivar": "arquivada", "restaurar": "restaurada", "proteger": "protegida", "desproteger": "desprotegida",
                   "ligar": "ligada", "desligar": "desligada", "classificar": "ligação classificada", "aceitar": "ligação aceita",
                   "mudar_relacao": "relação trocada", "revisar": "ligação para revisar", "fundir": "juntada",
                   "reforcar": "reforçada", "duracao": "duração definida", "religar": "religada", "importar": "importada"}


class _Sinais(QObject):
    terminou = Signal(str)
    progresso = Signal(str)


MAX_FATOS_NA_TELA = 8  # fatos importados mostrados por vez na revisão
ENSAIO_CONVERSAS = 10  # o "ensaio" da importação: só as 10 conversas mais recentes


class PainelGerenciar(QScrollArea):
    """Coluna rolável. `ideia_escolhida` leva o mapa até a ideia; `mudou` pede para o mapa recarregar."""

    ideia_escolhida = Signal(str)
    mudou = Signal()

    def __init__(self, grafo: Grafo, cfg, memorias=None, confirmar: Callable[[str, str], bool] | None = None,
                 higiene_fabrica: Callable[[], Any] | None = None, ligador_fabrica: Callable[[], Any] | None = None,
                 importador_fabrica: Callable[[], Any] | None = None) -> None:
        super().__init__()
        self.grafo, self.cfg, self.memorias = grafo, cfg, memorias
        self.confirmar = confirmar or (lambda titulo, texto: dialogos.perguntar(self, titulo, texto, sim="Confirmar"))
        self._higiene_fabrica, self._ligador_fabrica = higiene_fabrica, ligador_fabrica
        self._importador_fabrica, self._importador = importador_fabrica, None
        self.rodando = False
        self._parar_importacao = threading.Event()
        self.btn_parar = None
        self._sinais = _Sinais()
        self._sinais.terminou.connect(self._ao_terminar)
        self._sinais.progresso.connect(lambda t: self.aviso.setText(t))
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setMinimumWidth(340)
        self.setMaximumWidth(420)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.interior = QWidget()
        self.lay = QVBoxLayout(self.interior)
        self.lay.setContentsMargins(0, 0, 6, 0)
        self.lay.setSpacing(10)
        self.setWidget(self.interior)
        self.secoes: dict[str, Cartao] = {}
        self.aviso = QLabel("")
        self.aviso.setObjectName("discreto")
        self.aviso.setWordWrap(True)

    # --- motores (criados sob demanda: os testes injetam os seus) ------------------------------------------------------
    def higiene(self, com_ia: bool = False):
        if com_ia and self._higiene_fabrica is not None:
            return self._higiene_fabrica()
        from eco.grafo.higiene import Higiene

        return Higiene(self.grafo, None, memorias=self.memorias)

    # --- montagem ----------------------------------------------------------------------------------------------------
    def recarregar(self) -> None:
        while self.lay.count():
            item = self.lay.takeAt(0)
            if item.widget() is not None and item.widget() is not self.aviso:
                item.widget().deleteLater()
        self.secoes = {}
        self._numeros()
        self._importacao()
        self._revisao_importados()
        self._edicao_manual()
        self._ligacoes()
        self._propostas()
        self._arquivadas()
        self._protegidas()
        self._historico()
        self._custos()
        self.lay.addWidget(self.aviso)
        self.lay.addStretch(1)

    def _secao(self, chave: str, titulo: str, contador: int | None = None) -> Cartao:
        c = Cartao(titulo)
        if contador is not None:
            c.contador.setText(str(contador))
        self.secoes[chave] = c
        self.lay.addWidget(c)
        return c

    def _texto(self, c: Cartao, texto: str, discreto: bool = False) -> QLabel:
        l = QLabel(texto)
        l.setWordWrap(True)
        l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if discreto:
            l.setObjectName("discreto")
        c.corpo.addWidget(l)
        return l

    def _linha(self, c: Cartao, texto: str, botoes: list[tuple[str, Callable[[], None], str]], ideia: str | None = None) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 4, 0, 6)
        v.setSpacing(4)
        rotulo = QLabel(texto)
        rotulo.setWordWrap(True)
        v.addWidget(rotulo)
        widgets = []
        if ideia is not None:
            ver = Botao("Ver")
            ver.setToolTip("Mostrar no mapa")
            ver.clicked.connect(lambda _=False, i=ideia: self.ideia_escolhida.emit(i))
            widgets.append(ver)
        for nome, acao, tipo in botoes:
            b = Botao(nome, tipo)
            b.clicked.connect(lambda _=False, a=acao: self._fazer(a))
            widgets.append(b)
        w.linhas = []
        for k in range(0, max(len(widgets), 1), 3):  # no máximo 3 por linha: a coluna é estreita
            h = QHBoxLayout()
            h.setSpacing(6)
            for b in widgets[k:k + 3]:
                h.addWidget(b)
            h.addStretch(1)
            v.addLayout(h)
            w.linhas.append(h)
        c.corpo.addWidget(w)
        return w

    def _fazer(self, acao: Callable[[], Any]) -> None:
        try:
            resultado = acao()
        except (ValueError, KeyError, OSError) as erro:
            self.aviso.setText(str(erro))
            return
        if resultado is False:  # o dono disse não
            return
        self.aviso.setText(resultado if isinstance(resultado, str) else "")
        self.mudou.emit()
        self.recarregar()

    def _nome(self, id_: str) -> str:
        try:
            return self.grafo.ideia(id_).nome
        except KeyError:
            return "(apagada)"

    # --- seções ------------------------------------------------------------------------------------------------------
    def _numeros(self) -> None:
        from eco.grafo import consulta

        c = self._secao("resumo", "Gerenciar memória")
        self._texto(c, consulta.resumo(self.grafo))
        h = QHBoxLayout()
        self.btn_revisar = Botao("Revisar memória agora", "principal")
        self.btn_revisar.setToolTip("Procura o que arquivar, juntar ou agrupar e as contradições (frações de centavo)")
        self.btn_revisar.clicked.connect(self.revisar_agora)
        self.btn_ligar = Botao("Procurar ligações")
        self.btn_ligar.setToolTip("Liga as ideias novas ou alteradas às existentes (frações de centavo)")
        self.btn_ligar.clicked.connect(self.ligar_agora)
        for b in (self.btn_revisar, self.btn_ligar):
            b.setEnabled(not self.rodando)
            h.addWidget(b)
        h.addStretch(1)
        c.corpo.addLayout(h)

    # --- importação das conversas do claude.ai ------------------------------------------------------------------------------
    def importador(self):
        if self._importador is None and self._importador_fabrica is not None:
            self._importador = self._importador_fabrica()
        return self._importador

    def _importacao(self) -> None:
        if self._importador_fabrica is None:
            return
        imp = self.importador()
        c = self._secao("importacao", "Importar conversas do claude.ai")
        previa = imp.previa
        if previa is None:
            self._texto(c, "Traz para a memória fatos seus ditos nas conversas do claude.ai. Baixe o export no site (Configurações > "
                           "Privacidade > Exportar dados), deixe em Downloads e clique em Procurar. Procurar não gasta nada.", True)
        else:
            self._texto(c, previa.frase())
        h = QHBoxLayout()
        self.btn_procurar = Botao("Procurar export")
        self.btn_procurar.clicked.connect(self.procurar_export)
        self.btn_ensaio = Botao(f"Testar com {ENSAIO_CONVERSAS}")
        self.btn_ensaio.setToolTip("Analisa só as conversas mais recentes, para você ver a qualidade antes de importar tudo")
        self.btn_ensaio.clicked.connect(lambda: self.importar_agora(ENSAIO_CONVERSAS))
        self.btn_importar = Botao("Importar tudo", "principal")
        self.btn_importar.clicked.connect(lambda: self.importar_agora(None))
        pode = previa is not None and previa.pode_importar
        self.btn_ensaio.setVisible(pode)
        self.btn_importar.setVisible(pode)
        self.btn_parar = Botao("Parar")
        self.btn_parar.setVisible(False)
        self.btn_parar.clicked.connect(self._pedir_parada)
        for b in (self.btn_procurar, self.btn_ensaio, self.btn_importar, self.btn_parar):
            b.setEnabled(not self.rodando)
            h.addWidget(b)
        self.btn_parar.setEnabled(True)
        h.addStretch(1)
        c.corpo.addLayout(h)

    def _pedir_parada(self) -> None:
        self._parar_importacao.set()
        self.aviso.setText("Parando depois da conversa atual…")

    def procurar_export(self) -> None:
        if self.rodando:
            return
        self._em_segundo_plano(lambda: (self.importador().preparar(), "")[1], importacao=False)  # o resultado aparece no cartão

    def importar_agora(self, max_conversas: int | None) -> None:
        imp = self.importador()
        previa = imp.previa if imp is not None else None
        if self.rodando or previa is None or not previa.pode_importar:
            return
        n = len(previa.plano.analisar) if max_conversas is None else min(max_conversas, len(previa.plano.analisar))
        estimativa = previa.plano.estimativa.custo_usd if max_conversas is None else (
            previa.plano.estimativa.custo_usd * n / max(len(previa.plano.analisar), 1))
        texto = (f"Analisar {n} conversa(s)? O texto do que você escreveu nelas será enviado à Anthropic (modelo barato) para "
                 f"achar fatos sobre você; as respostas da assistente são cortadas. Estimativa: cerca de US$ {estimativa:.4f}, "
                 f"com teto de US$ {self.cfg.importar_teto_usd:.2f}. Nada vai para a memória sem o seu sim, item a item.")
        if not self.confirmar("Importar conversas", texto):
            return
        self._parar_importacao.clear()
        self._em_segundo_plano(lambda: imp.importar(
            max_conversas, ao_progresso=self._sinais.progresso.emit, parar=self._parar_importacao.is_set), importacao=True)

    def _revisao_importados(self) -> None:
        if self._importador_fabrica is None:
            return
        imp = self.importador()
        lista = imp.propostas()
        if not lista:
            return
        c = self._secao("importados", "Fatos importados para revisar", len(lista))
        self._texto(c, "Aprove só o que é verdade e vale lembrar. Aprovados ficam fora do prompt fixo: a Eco os traz quando o "
                       "assunto aparece.", True)
        for p in lista[:MAX_FATOS_NA_TELA]:
            data = p.conversa_data[:10]
            dura = {"permanente": "permanente", "duradoura": "duradouro", "temporaria": "temporário"}.get(p.duracao, "")
            if p.validade:
                dura += f" até {p.validade}"
            texto = f"{p.texto}\nConversa \"{p.conversa_titulo[:50]}\" ({data})" + (f" · {dura}" if dura else "")
            texto += f"\nVocê disse: \"{p.trecho}\"" if p.trecho else "\n(sem citação confirmada: confira com cuidado)"
            if p.com_conflito:
                verbo = "parece ser a versão nova de" if p.conflito_relacao == "atualiza" else "contradiz"
                texto += f"\nJÁ EXISTE na memória: \"{p.conflito_texto}\" — este fato {verbo} ela. {p.conflito_motivo}".rstrip()
            botoes = []
            if p.com_conflito:
                botoes += [("Substituir a antiga", lambda p=p: self._aprovar_importado(p, substituir=True), "principal"),
                           ("Manter as duas", lambda p=p: self._aprovar_importado(p, mesmo_assim=True), "normal")]
            else:
                botoes.append(("Aprovar", lambda p=p: self._aprovar_importado(p), "principal"))
            botoes += [("Editar", lambda p=p: self._editar_importado(p), "normal"),
                       ("Rejeitar", lambda p=p: self._rejeitar_importado(p), "normal")]
            self._linha(c, texto, botoes)
        if len(lista) > MAX_FATOS_NA_TELA:
            self._texto(c, f"E mais {len(lista) - MAX_FATOS_NA_TELA}. Eles aparecem conforme você decide estes.", True)

    def _aprovar_importado(self, p, texto: str | None = None, substituir: bool = False, mesmo_assim: bool = False) -> Any:
        from eco.importacao.aprovacao import ConflitoPendente

        imp = self.importador()
        try:
            r = imp.aprovar(p.id, texto, substituir, mesmo_assim)
        except ConflitoPendente as erro:  # a conferência achou conflito só agora: o painel mostra as opções ao recarregar
            return str(erro)
        if r.ja_existia:
            return "Esse fato já estava na memória: nada foi criado."
        return "Guardado." + (f" A lembrança antiga foi para o arquivo: \"{r.substituiu}\"." if r.substituiu else "")

    def _editar_importado(self, p) -> Any:
        novo, ok = dialogos.texto_longo(self, "Editar fato", "Corrija o fato como deve ficar na memória:", p.texto)
        if not ok or " ".join(novo.split()) == p.texto:
            return False
        self.importador().estado.editar_texto(p.id, novo)
        return "Texto alterado. Ele será conferido de novo com a memória ao aprovar."

    def _rejeitar_importado(self, p) -> str:
        self.importador().rejeitar(p.id)
        return "Rejeitado. A Eco não propõe esse fato de novo."

    def _edicao_manual(self) -> None:
        if self.memorias is None:
            return
        try:
            e = self.memorias.edicao_manual()
        except Exception:
            e = None
        if e is None:
            return
        c = self._secao("edicao", "Memória editada à mão")
        self._linha(c, e.frase() + " Nada entra sem o seu sim.", [
            ("Importar", lambda: self._confirmar_e("Importar a edição", e.frase(), self.memorias.importar_edicao), "principal"),
            ("Desfazer", lambda: self._confirmar_e("Desfazer a edição", "O memoria.md volta a ser a cópia da memória guardada.",
                                                   self.memorias.descartar_edicao), "normal"),
        ])

    def _confirmar_e(self, titulo: str, texto: str, acao: Callable[[], Any]) -> Any:
        if not self.confirmar(titulo, texto):
            return False
        return acao()

    def _ligacoes(self) -> None:
        from eco.grafo.ligacao import RELACOES, nivel_legivel, rotulo

        ativas = {i.id for i in self.grafo.listar_ideias()}
        lista = [(s, l) for s in ("revisar", "sugestao") for l in self.grafo.ligacoes_com_status(s) if l.a in ativas and l.b in ativas]
        c = self._secao("ligacoes", "Ligações para revisar", len(lista))
        if not lista:
            self._texto(c, "Nenhuma ligação esperando revisão.", True)
            return
        for status, l in lista[:15]:
            estado = "já vale, para revisar" if status == "revisar" else "sugestão, ainda não vale"
            texto = (f"{self._nome(l.a)} — {self._nome(l.b)}: {rotulo(l, self.grafo)}\n{l.motivo or 'sem motivo'} "
                     f"(confiança {nivel_legivel(l.confianca)}; {estado})")
            w = self._linha(c, texto, [
                ("Aceitar", lambda i=l.id: self._aceitar(i), "principal"),
                ("Rejeitar", lambda l=l: self._rejeitar(l), "normal"),
            ], ideia=l.a)
            trocar = Botao("Trocar tipo")
            menu = QMenu(trocar)
            for codigo, (rot, _sentido, _tipo) in RELACOES.items():
                menu.addAction(rot).triggered.connect(lambda _=False, l=l, c=codigo: self._fazer(lambda: self._mudar(l, c)))
            trocar.setMenu(menu)
            h = QHBoxLayout()  # numa linha própria: Ver, Aceitar e Rejeitar já ocupam a primeira
            h.addWidget(trocar)
            h.addStretch(1)
            w.layout().addLayout(h)

    def _aceitar(self, id_: str) -> str:
        with self.grafo.como("clique no painel Gerenciar"):
            self.grafo.aceitar_ligacao(id_)
        return "Ligação aceita."

    def _rejeitar(self, l) -> Any:
        if not self.confirmar("Rejeitar ligação", f"Desfazer a ligação entre \"{self._nome(l.a)}\" e \"{self._nome(l.b)}\"? "
                                                   "A Eco não liga esse par de novo sozinha."):
            return False
        with self.grafo.como("clique no painel Gerenciar"):
            self.grafo.desligar(l.a, l.b)
        return "Ligação rejeitada."

    def _mudar(self, l, codigo: str) -> str:
        from eco.grafo.ligacao import RELACOES, rotulo

        with self.grafo.como("clique no painel Gerenciar"):
            nova = self.grafo.mudar_relacao(l.id, codigo, l.a if RELACOES[codigo][1] else "")
        return f"Agora: {rotulo(nova, self.grafo)}."

    def _propostas(self) -> None:
        h = self.higiene()
        lista = h.propostas()
        contradicoes = [p for p in lista if p.tipo == "contradicao"]
        outras = [p for p in lista if p.tipo != "contradicao"]
        c = self._secao("contradicoes", "Possíveis contradições", len(contradicoes))
        if not contradicoes:
            self._texto(c, "Nenhuma contradição encontrada.", True)
        for p in contradicoes:
            a, b = p.ideias[0], p.ideias[1]
            self._linha(c, h.descrever(p), [
                (f"Vale \"{self._nome(a)[:16]}\"", lambda p=p: self._aplicar(p, "primeira"), "normal"),
                (f"Vale \"{self._nome(b)[:16]}\"", lambda p=p: self._aplicar(p, "segunda"), "normal"),
                ("As duas valem", lambda p=p: self._aplicar(p, "ambas"), "normal"),
            ])
        c = self._secao("propostas", "Propostas de limpeza", len(outras))
        if not outras:
            self._texto(c, "Nenhuma proposta. \"Revisar memória agora\" procura o que pode ser arquivado, juntado ou agrupado.", True)
        for p in outras:
            self._linha(c, h.descrever(p), [("Aplicar", lambda p=p: self._aplicar(p), "principal"),
                                            ("Recusar", lambda p=p: self.higiene().recusar(p), "normal")], ideia=p.ideias[0])

    def _aplicar(self, p, opcao: str = "") -> Any:
        h = self.higiene()
        if not self.confirmar("Aplicar proposta", "Aplicar: " + h.descrever(p) + "? Nada é apagado: o que sai vai para o arquivo."):
            return False
        return h.aplicar(p, opcao)

    def _arquivadas(self) -> None:
        lista = self.grafo.arquivadas()
        c = self._secao("arquivadas", "Arquivadas", len(lista))
        if not lista:
            self._texto(c, "Nada arquivado.", True)
        for i in lista[:20]:
            motivo = next((e["depois"].get("motivo", "") for e in reversed(self.grafo.historico(i.id))
                           if e["acao"] == "arquivar" and e["depois"]), "")
            self._linha(c, f"{i.nome}" + (f" ({motivo})" if motivo else ""), [("Restaurar", lambda i=i: self._restaurar(i), "normal")])

    def _restaurar(self, i) -> str:
        with self.grafo.como("clique no painel Gerenciar"):
            self.grafo.restaurar(i.id)
        if i.origem == "fato" and self.memorias is not None:
            self.memorias.espelhar()
        return f"\"{i.nome}\" voltou."

    def _protegidas(self) -> None:
        lista = self.grafo.protegidas()
        c = self._secao("protegidas", "Protegidas", len(lista))
        if not lista:
            self._texto(c, "Nenhuma ideia protegida. Proteja pelo cartão da ideia: processos automáticos nunca mexem nela.", True)
        for i in lista[:20]:
            self._linha(c, i.nome, [("Desproteger", lambda i=i: self._desproteger(i), "normal")], ideia=i.id)

    def _desproteger(self, i) -> Any:
        if not self.confirmar("Desproteger", f"Tirar a proteção de \"{i.nome}\"? Processos automáticos poderão propor arquivá-la."):
            return False
        with self.grafo.como("clique no painel Gerenciar"):
            if i.origem == "fato" and self.memorias is not None:
                self.memorias.fixar(i.id, False)
            else:
                self.grafo.proteger(i.id, False)
        return f"\"{i.nome}\" não está mais protegida."

    def _historico(self) -> None:
        eventos = self.grafo.historico_recente(15)
        c = self._secao("historico", "Histórico recente")
        if not eventos:
            self._texto(c, "Sem alterações registradas.", True)
        for e in eventos:
            alvo = self._nome(e["ideia_id"]) if e["ideia_id"] else "ligação"
            quando = e["quando"][:16].replace("T", " ")
            self._texto(c, f"{quando} · {alvo}: {ACOES_HISTORICO.get(e['acao'], e['acao'])} por {e['autor'] or 'sistema'}", True)

    def _custos(self) -> None:
        from eco import config
        from eco.grafo import consulta
        from eco.uso import RegistroUso

        c = self._secao("custos", "Custos e limites")
        try:
            self._texto(c, consulta.custos(self.grafo, self.cfg, RegistroUso(config.PASTA_ECO / "uso.jsonl")))
        except Exception as erro:
            self._texto(c, f"Não consegui ler os custos: {erro}", True)

    # --- ações que gastam (em segundo plano) ---------------------------------------------------------------------------
    def revisar_agora(self) -> None:
        if self.rodando or not self.confirmar("Revisar memória", "Revisar a memória agora? Custa frações de centavo (modelo "
                                                                 "barato), dentro do teto do grafo. Só cria propostas."):
            return
        self._em_segundo_plano(lambda: (lambda h: h.revisar().frase(len(h.propostas())))(self.higiene(com_ia=True)))

    def ligar_agora(self) -> None:
        if self.rodando or self._ligador_fabrica is None:
            return
        if not self.confirmar("Procurar ligações", "Procurar ligações para as ideias novas ou alteradas? Custa frações de "
                                                   "centavo por ideia, dentro do teto do grafo."):
            return
        self._em_segundo_plano(lambda: self._ligador_fabrica().executar().frase())

    def _em_segundo_plano(self, trabalho: Callable[[], str], importacao: bool | None = None) -> None:
        """`importacao`: None não mexe nos botões da importação; False os desliga; True os desliga e mostra o Parar."""
        self.rodando = True
        self.aviso.setText("Trabalhando…")
        botoes = [self.btn_revisar, self.btn_ligar]
        if importacao is not None and self._importador_fabrica is not None:
            botoes += [self.btn_procurar, self.btn_ensaio, self.btn_importar]
            if importacao and self.btn_parar is not None:
                self.btn_parar.setVisible(True)
        for b in botoes:
            b.setEnabled(False)

        def rodar() -> None:
            try:
                self._sinais.terminou.emit(trabalho())
            except Exception as erro:
                self._sinais.terminou.emit(f"Não deu certo: {type(erro).__name__}: {erro}")

        threading.Thread(target=rodar, daemon=True, name="eco-gerenciar").start()

    def _ao_terminar(self, texto: str) -> None:
        self.rodando = False
        self.mudou.emit()
        self.recarregar()
        self.aviso.setText(texto)
