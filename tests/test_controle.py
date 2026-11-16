"""Passo 5 do escopo: painel de controle (tarefas, eventos, e-mails com estrela) e as falas de pendências."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from eco import controle, revisao
from eco.ferramentas import google, tarefas
from eco.lembretes import Evento, Lembretes, Pendencia, montar_noite, montar_resumo

SP = ZoneInfo("America/Sao_Paulo")
HOJE = date(2026, 11, 11)


def sp(texto: str) -> datetime:
    return datetime.fromisoformat(texto).replace(tzinfo=SP)


# --- importante nas notas -------------------------------------------------------------------------------------------
def test_marca_de_importante_nas_notas():
    assert tarefas.motivo_importante("") is None
    assert tarefas.motivo_importante("Importante: meta de Estudos\ncomprar caderno") == "meta de Estudos"
    assert tarefas.motivo_importante("comprar\nIMPORTANTE") == ""
    assert tarefas.notas_com_importante("comprar caderno", "prova") == "Importante: prova\ncomprar caderno"
    assert tarefas.notas_com_importante("Importante: prova\ncomprar caderno", "outra") == "Importante: outra\ncomprar caderno"
    assert tarefas.notas_com_importante("Importante: prova\ncomprar caderno", None) == "comprar caderno"
    assert tarefas.notas_com_importante(None, "") == "Importante"
    assert tarefas.motivo_importante("isso é importante pra mim") is None  # só a linha que começa com a marca


def test_marcar_importante_grava_nas_notas(monkeypatch):
    chamadas = []

    class Tasks:
        def tasks(self):
            return self

        def patch(self, **kw):
            chamadas.append(kw)
            return type("P", (), {"execute": lambda s: {}})()

    monkeypatch.setattr(google, "_servico", lambda *a: Tasks())
    t = {"id": "1", "lista_id": "L", "titulo": "Prova", "prazo": None, "notas": "levar calculadora"}
    assert "importante" in tarefas.marcar_importante(t, "Cálculo")
    assert chamadas[0]["body"] == {"notes": "Importante: Cálculo\nlevar calculadora"}


# --- textos falados -------------------------------------------------------------------------------------------------
PEND = [
    Pendencia("Lista de exercícios", HOJE),
    Pendencia("Entrevista na X", HOJE, "carreira"),
    Pendencia("Pagar boleto", HOJE - timedelta(days=1)),
    Pendencia("Declarar imposto", HOJE - timedelta(days=9)),
]
ESTRELAS = [{"id": "e1", "de": "Ana", "assunto": "Contrato", "quando": "10/11 09:00"}]


def test_resumo_do_amanhecer_separa_hoje_ontem_e_antigas_com_importantes_primeiro():
    t = montar_resumo([Evento("a", "Aula", sp("2026-11-11T10:00"))], PEND, sp("2026-11-11T07:00"), estrelas=ESTRELAS)
    assert t.startswith("Bom dia!")
    assert "Na sua lista pra hoje: Entrevista na X, importante (carreira); Lista de exercícios." in t
    assert "Ficou de ontem: Pagar boleto." in t
    assert "1 coisa atrasada de antes: Declarar imposto." in t
    assert "1 e-mail pendente: Ana, Contrato." in t


def test_fechamento_da_noite():
    t = montar_noite(PEND, sp("2026-11-11T21:00"), ESTRELAS)
    assert t.startswith("Fechamento do dia.") and "Ainda aberto de hoje: Entrevista na X" in t
    assert "Atrasadas: Declarar imposto; Pagar boleto." in t and "Contrato" in t
    assert montar_noite([], sp("2026-11-11T21:00")) == "Fechamento do dia: nada pendente. Tudo em dia."


class Fonte:
    def __init__(self):
        self.leituras = 0

    def eventos(self, inicio, fim):
        return []

    def pendencias(self):
        self.leituras += 1
        return PEND

    def estrelas(self):
        return ESTRELAS


def _falas(lem):
    saida = []
    while (f := lem.proxima_fala()) is not None:
        saida.append(f)
    return saida


def test_resumo_so_depois_do_amanhecer(tmp_path):
    lem = Lembretes(Fonte(), tmp_path / "l.json")
    lem.ciclo(sp("2026-11-11T00:05"))
    lem.ciclo(sp("2026-11-11T05:59"))
    assert _falas(lem) == []
    lem.ciclo(sp("2026-11-11T06:00"))
    (fala,) = _falas(lem)
    assert "Ficou de ontem: Pagar boleto." in fala and "Contrato" in fala


def test_fechamento_no_primeiro_contato_depois_das_20h30_uma_vez_por_dia(tmp_path):
    fonte = Fonte()
    lem = Lembretes(fonte, tmp_path / "l.json")
    assert not lem.no_contato(sp("2026-11-11T20:29"))
    assert fonte.leituras == 0  # antes da hora nem consulta o Google
    assert lem.no_contato(sp("2026-11-11T20:30"))
    assert _falas(lem)[0].startswith("Fechamento do dia.")
    assert not lem.no_contato(sp("2026-11-11T22:00"))  # já falou hoje
    lem2 = Lembretes(fonte, tmp_path / "l.json")  # reiniciou: o estado ficou no arquivo
    assert not lem2.no_contato(sp("2026-11-11T23:00"))
    assert lem2.no_contato(sp("2026-11-12T20:45"))  # outro dia


def test_fechamento_com_google_fora_tenta_no_proximo_contato(tmp_path):
    class Quebra(Fonte):
        def pendencias(self):
            raise ConnectionError("sem rede")

    lem = Lembretes(Quebra(), tmp_path / "l.json")
    assert not lem.no_contato(sp("2026-11-11T21:00")) and lem.estado.ultima_noite is None


# --- painel (Qt) ----------------------------------------------------------------------------------------------------
@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    from eco.config import carregar_config

    return carregar_config()


@pytest.fixture
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _esperar(qapp, condicao, segundos=5):
    fim = time.time() + segundos
    while time.time() < fim:
        qapp.processEvents()
        if condicao():
            return True
        time.sleep(0.01)
    return False


def tarefa(titulo, prazo=None, notas=""):
    return {"id": titulo, "titulo": titulo, "prazo": prazo, "notas": notas, "lista_id": "L", "lista": "Minhas", "lista_visivel": False, "concluida_em": None}


class Acoes:
    def __init__(self):
        self.feitos = []

    def __getattr__(self, nome):
        if nome == "listas":
            return lambda: ["Minhas", "USP"]
        return lambda *a: self.feitos.append((nome, *a))


def _painel(cfg, qapp, acoes, confirmar=lambda f: True, escopo=lambda f: "este"):
    from eco.visual.dashboard import PainelDashboard

    pend = [tarefa("Comum", HOJE), tarefa("Entrevista", HOJE, "Importante: carreira")]
    eventos = [
        {"id": "e1", "titulo": "Aula", "inicio": sp("2026-11-11T10:00"), "fim": sp("2026-11-11T11:00"), "serie": None},
        {"id": "e2", "titulo": "Academia", "inicio": sp("2026-11-12T07:00"), "fim": sp("2026-11-12T08:00"), "serie": "s1"},
    ]
    dados = controle.Controle(revisao.coletar(cfg.pasta_dados, pend, [], HOJE), eventos, ESTRELAS)
    p = PainelDashboard(cfg, ler=lambda: dados, acoes=acoes, confirmar=confirmar, escolher_escopo=escopo)
    p.ao_abrir()
    assert _esperar(qapp, lambda: p.dados is not None and not p.ocupado)
    return p, eventos, pend


def _textos(p):
    from PySide6.QtWidgets import QCheckBox, QLabel, QWidget

    return [w.text() for w in p.corpo.findChildren(QWidget) if isinstance(w, (QLabel, QCheckBox))]


def test_painel_mostra_agenda_tarefas_importantes_e_emails(cfg, qapp):
    p, _, _ = _painel(cfg, qapp, Acoes())
    t = _textos(p)
    assert "Hoje" in t and "Amanhã" in t
    assert any(x.startswith("10:00") and "Aula" in x for x in t)
    assert any("Academia" in x and "↻" in x for x in t)  # série marcada
    assert any(x.startswith("Entrevista") and "★ importante: carreira" in x for x in t)
    assert any("Ana" in x and "Contrato" in x for x in t)
    caixas = [x for x in t if x.startswith(("Entrevista", "Comum"))]
    assert caixas[0].startswith("Entrevista")  # importante primeiro


def test_painel_acoes_de_tarefa_evento_e_email(cfg, qapp):
    a = Acoes()
    p, eventos, pend = _painel(cfg, qapp, a)
    p.salvar_tarefa(None, {"titulo": "Nova", "data": "2026-11-12", "lista": "USP", "notas": "", "importante": "prova"})
    p.alternar_importante(pend[0], "Estudos")  # marca com motivo
    p.alternar_importante(pend[1])  # já importante: tira
    p.apagar_tarefa(pend[0])
    p.salvar_evento(None, {"titulo": "Dentista", "inicio": "2026-11-13T15:00", "duracao_min": 30, "local": ""})
    p.salvar_evento(eventos[1], {"titulo": "Academia", "inicio": "2026-11-12T08:00", "duracao_min": 60, "local": ""})
    p.apagar_evento(eventos[0])
    p.resolver_email(ESTRELAS[0])
    assert _esperar(qapp, lambda: len(a.feitos) == 8 and not p.ocupado)
    nomes = [f[0] for f in a.feitos]
    assert nomes == ["criar_tarefa", "importante", "importante", "apagar_tarefa", "criar_evento", "editar_evento", "apagar_evento", "resolver_email"]
    assert a.feitos[1][2] == "Estudos" and a.feitos[2][2] is None
    assert a.feitos[5][2] == "este"  # série: o escopo escolhido vai junto
    assert a.feitos[6][2] is None  # evento comum


def test_painel_cancelar_nao_faz_nada(cfg, qapp):
    a = Acoes()
    p, eventos, pend = _painel(cfg, qapp, a, confirmar=lambda f: False, escopo=lambda f: None)
    p.apagar_tarefa(pend[0])
    p.apagar_evento(eventos[0])
    p.apagar_evento(eventos[1])
    p.salvar_evento(eventos[1], {"titulo": "x", "inicio": "2026-11-12T08:00", "duracao_min": 60, "local": ""})
    qapp.processEvents()
    assert a.feitos == []


def test_editar_tarefa_manda_so_o_que_mudou(monkeypatch):
    pedidos = []
    monkeypatch.setattr(tarefas, "aplicar_edicao", lambda t, m: pedidos.append(m))
    t = tarefa("Prova", date(2026, 11, 12), "levar lápis")
    ac = controle.AcoesGoogle()
    ac.editar_tarefa(t, {"titulo": "Prova", "data": "2026-11-12", "lista": "Minhas", "notas": "levar lápis", "importante": None})
    assert pedidos == []
    ac.editar_tarefa(t, {"titulo": "Prova de cálculo", "data": None, "lista": "Minhas", "notas": "levar lápis", "importante": "Cálculo"})
    assert pedidos == [{"novo_titulo": "Prova de cálculo", "remover_data": True, "notas": "Importante: Cálculo\nlevar lápis"}]


def test_carregar_junta_tudo_e_avisa_o_que_falhou(cfg, monkeypatch):
    from eco.ferramentas import emails

    monkeypatch.setattr(google, "tarefas_pendentes", lambda: [tarefa("A", HOJE)])
    monkeypatch.setattr(google, "tarefas_concluidas", lambda desde: [])
    monkeypatch.setattr(google, "eventos_entre", lambda i, f, limite=50: [{"id": "e", "titulo": "Aula", "inicio": sp("2026-11-11T10:00"), "fim": None, "serie": None}])

    def quebra(*a):
        raise ConnectionError("sem rede")

    monkeypatch.setattr(emails, "com_estrela", quebra)
    c = controle.carregar(cfg.pasta_dados, HOJE)
    assert [t["titulo"] for t in c.rev.proximas] == ["A"] and len(c.eventos) == 1 and c.estrelas == []
    assert c.avisos == ["Gmail indisponível (ConnectionError)"]
