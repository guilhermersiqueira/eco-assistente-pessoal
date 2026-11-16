"""Revisão semanal (segunda-feira) e painel de metas + Google Tasks."""

from __future__ import annotations

import time
from datetime import date, datetime

import pytest

from eco import revisao
from eco.ferramentas import google, notas
from eco.lembretes import FUSO, Lembretes

SEGUNDA = date(2026, 11, 9)  # 09/11/2026 é segunda-feira


def tarefa(titulo, prazo=None, concluida_em=None, lista="Estudos", id=None):
    return {"id": id or titulo, "titulo": titulo, "prazo": prazo, "notas": "", "lista_id": "L1", "lista": lista, "lista_visivel": True, "concluida_em": concluida_em}


@pytest.fixture
def pasta(tmp_path):
    p = tmp_path / "dados"
    p.mkdir()
    notas.definir_meta(p, "Ler 12 livros", "2026-11-12", "Leitura")
    notas.definir_meta(p, "Ler artigo", None, "Leitura")
    notas.definir_meta(p, "Montar reserva", "2026-11-01", "Investimentos")
    notas.definir_meta(p, "Sem tópico aqui")
    notas.concluir_meta(p, "artigo")
    return p


def test_desde_semana_e_meia_noite_de_sp_sete_dias_atras_em_utc():
    assert revisao.desde_semana(SEGUNDA) == "2026-11-02T03:00:00Z"


def test_coletar_separa_atrasadas_proximas_sem_data_e_concluidas(pasta):
    pend = [tarefa("Prova", date(2026, 11, 8)), tarefa("Trabalho", date(2026, 11, 9)), tarefa("Longe", date(2026, 12, 1)), tarefa("Solta")]
    conc = [tarefa("A", concluida_em=date(2026, 11, 3)), tarefa("B", concluida_em=date(2026, 11, 7))]
    r = revisao.coletar(pasta, pend, conc, SEGUNDA)
    assert [t["titulo"] for t in r.atrasadas] == ["Prova"]
    assert [t["titulo"] for t in r.proximas] == ["Trabalho"]  # "Longe" passa de 7 dias
    assert [t["titulo"] for t in r.sem_data] == ["Solta"]
    assert [t["titulo"] for t in r.concluidas] == ["B", "A"]  # mais recente primeiro
    assert [m.titulo for m in r.metas_atrasadas] == ["Montar reserva"] and [m.titulo for m in r.metas_proximas] == ["Ler 12 livros"]
    assert r.topicos == {"Leitura": (1, 2), "Investimentos": (0, 1), "Sem tópico": (0, 1)}
    assert revisao.snapshot(r) == {"Leitura": 1, "Investimentos": 0, "Sem tópico": 0}


def test_texto_falado_traz_os_numeros_certos(pasta):
    r = revisao.coletar(pasta, [tarefa("Prova", date(2026, 11, 8))], [tarefa("A", concluida_em=date(2026, 11, 3))], SEGUNDA)
    t = revisao.texto_falado(r)
    assert t.startswith("Revisão da semana.")
    assert "Investimentos, nenhuma de 1" in t and "Leitura, 1 de 2" in t
    assert "Você concluiu 1 tarefa nos últimos 7 dias." in t
    assert "2 itens atrasados: Prova; Montar reserva." in t
    assert "Vencem nos próximos 7 dias: Ler 12 livros." in t
    assert "Desde a última revisão" not in t  # primeira revisão: sem comparação


def test_texto_falado_compara_com_a_revisao_anterior_e_cobre_vazio(pasta, tmp_path):
    r = revisao.coletar(pasta, [], [], SEGUNDA)
    assert "Desde a última revisão, 1 meta concluída." in revisao.texto_falado(r, {"Leitura": 0, "Investimentos": 0})
    assert "Desde a última revisão" not in revisao.texto_falado(r, revisao.snapshot(r))
    vazio = tmp_path / "vazio"
    vazio.mkdir()
    t = revisao.texto_falado(revisao.coletar(vazio, [], [], SEGUNDA))
    assert "ainda não tem metas" in t and "Nenhuma tarefa concluída" in t and "Nada atrasado" in t


def test_marcar_meta_exata_conclui_e_reabre(pasta):
    notas.marcar_meta(pasta, "Ler 12 livros", True)
    assert "- [x] Ler 12 livros (tópico: Leitura) (prazo: 2026-11-12)" in (pasta / "metas.md").read_text(encoding="utf-8")
    notas.marcar_meta(pasta, "ler 12 livros", False)
    assert "- [ ] Ler 12 livros" in (pasta / "metas.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Não encontrei"):
        notas.marcar_meta(pasta, "Ler", True)  # só título exato


# --- lembretes: toda segunda, depois do resumo, uma vez ---------------------------------------------------------
class Fonte:
    def __init__(self, pasta):
        self.pasta, self.revisoes = pasta, 0

    def eventos(self, inicio, fim):
        return []

    def pendencias(self):
        return []

    def revisao(self, hoje):
        self.revisoes += 1
        return revisao.coletar(self.pasta, [], [tarefa("A", concluida_em=date(2026, 11, 3))], hoje)


def _lembretes(tmp_path, fonte, avisos=None):
    return Lembretes(fonte, tmp_path / "lembretes.json", lambda t, m: avisos.append(t) if avisos is not None else None)


def _falas(l):
    saida = []
    while l.tem_fala():
        saida.append(l.proxima_fala())
    return saida


def _sp(texto):
    return datetime.fromisoformat(texto).replace(tzinfo=FUSO)


def test_revisao_fala_na_segunda_depois_do_resumo_e_so_uma_vez(tmp_path, pasta):
    fonte, avisos = Fonte(pasta), []
    l = _lembretes(tmp_path, fonte, avisos)
    l.ciclo(_sp("2026-11-09T08:00:00"))
    falas = _falas(l)
    assert len(falas) == 2 and falas[0].startswith("Bom dia") and falas[1].startswith("Revisão da semana.")
    assert "Resumo do dia" in avisos and "Revisão da semana" in avisos
    l.ciclo(_sp("2026-11-09T09:00:00"))
    assert _falas(l) == [] and fonte.revisoes == 1
    # e uma nova instância (app reaberto na mesma segunda) também não repete
    l2 = _lembretes(tmp_path, fonte)
    l2.ciclo(_sp("2026-11-09T20:00:00"))
    assert _falas(l2) == []


def test_revisao_nao_fala_fora_da_segunda_e_na_proxima_compara(tmp_path, pasta):
    fonte = Fonte(pasta)
    l = _lembretes(tmp_path, fonte)
    l.ciclo(_sp("2026-11-10T08:00:00"))  # terça
    assert [f for f in _falas(l) if f.startswith("Revisão")] == [] and fonte.revisoes == 0
    l.ciclo(_sp("2026-11-09T08:00:00"))
    _falas(l)
    notas.concluir_meta(pasta, "Ler 12")
    l.ciclo(_sp("2026-11-16T08:00:00"))  # segunda seguinte
    (resumo, rev) = _falas(l)
    assert "Desde a última revisão, 1 meta concluída." in rev


def test_revisao_com_google_fora_tenta_de_novo_no_proximo_ciclo(tmp_path, pasta):
    class Quebrada(Fonte):
        def revisao(self, hoje):
            raise ConnectionError("sem rede")

    fonte = Quebrada(pasta)
    l = _lembretes(tmp_path, fonte)
    l.ciclo(_sp("2026-11-09T08:00:00"))
    assert l.estado.ultima_revisao is None  # não marcou: tenta de novo
    fonte.revisao = lambda hoje: Fonte.revisao(fonte, hoje)
    l.ciclo(_sp("2026-11-09T08:01:00"))
    assert any(f.startswith("Revisão") for f in _falas(l))


def test_carregar_le_todas_as_listas_do_google(pasta, monkeypatch):
    pedidos = []
    monkeypatch.setattr(google, "tarefas_pendentes", lambda: [tarefa("Prova", date(2026, 11, 8))])
    monkeypatch.setattr(google, "tarefas_concluidas", lambda desde: pedidos.append(desde) or [tarefa("A", concluida_em=date(2026, 11, 3))])
    r = revisao.carregar(pasta, SEGUNDA)
    assert pedidos == ["2026-11-02T03:00:00Z"] and len(r.atrasadas) == 1 and len(r.concluidas) == 1


def test_ferramenta_revisao_semanal(tmp_path, pasta, monkeypatch):
    monkeypatch.setenv("ECO_DADOS_DIR", str(pasta))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setattr(google, "tarefas_pendentes", lambda: [])
    monkeypatch.setattr(google, "tarefas_concluidas", lambda desde: [])
    from eco.config import carregar_config
    from eco.ferramentas import construir_ferramentas

    ferramentas = {f.nome: f for f in construir_ferramentas(carregar_config())}
    texto = ferramentas["revisao_semanal"].executar({})
    assert texto.startswith("Revisão da semana.") and "Leitura, 1 de 2" in texto


# --- painel ----------------------------------------------------------------------------------------------------
pytest.importorskip("PySide6")


@pytest.fixture
def cfg(tmp_path, pasta, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("ECO_DADOS_DIR", str(pasta))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    from eco.config import carregar_config

    return carregar_config()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _esperar(qapp, condicao, limite=5.0):
    fim = time.time() + limite
    while time.time() < fim:
        qapp.processEvents()
        if condicao():
            return True
        time.sleep(0.01)
    return False


def _caixas(p):
    from PySide6.QtWidgets import QCheckBox

    return {c.text().split("  ·")[0]: c for c in p.findChildren(QCheckBox)}


def _painel(cfg, pasta, pendentes, concluidas, feitos):
    from eco.visual.dashboard import PainelDashboard

    estado = {"pend": pendentes, "conc": concluidas}

    def concluir(t):
        feitos.append(("concluir", t["titulo"]))
        estado["pend"] = [x for x in estado["pend"] if x is not t]
        estado["conc"] = estado["conc"] + [dict(t, concluida_em=SEGUNDA)]

    def reabrir(t):
        feitos.append(("reabrir", t["titulo"]))
        estado["conc"] = [x for x in estado["conc"] if x["titulo"] != t["titulo"]]
        estado["pend"] = estado["pend"] + [dict(t, concluida_em=None)]

    return PainelDashboard(cfg, ler=lambda: revisao.coletar(pasta, estado["pend"], estado["conc"], SEGUNDA), concluir_tarefa=concluir, reabrir_tarefa=reabrir)


def test_painel_mostra_metas_e_tarefas_e_conclui_pelo_clique(cfg, pasta, qapp):
    feitos = []
    p = _painel(cfg, pasta, [tarefa("Prova", date(2026, 11, 8)), tarefa("Solta")], [tarefa("Antiga", concluida_em=date(2026, 11, 3))], feitos)
    p.ao_abrir()
    assert _esperar(qapp, lambda: p.dados is not None and not p.ocupado)
    cx = _caixas(p)
    assert {"Ler 12 livros", "Montar reserva", "Prova", "Solta", "Antiga", "Ler artigo"} <= set(cx)
    assert cx["Antiga"].isChecked() and cx["Ler artigo"].isChecked() and not cx["Prova"].isChecked()
    assert "Atualizado" in p.status.text()
    cx["Prova"].click()  # marcar como feita
    assert _esperar(qapp, lambda: feitos == [("concluir", "Prova")] and not p.ocupado)
    assert _caixas(p)["Prova"].isChecked()  # já veio da releitura, agora entre as concluídas
    _caixas(p)["Antiga"].click()  # desmarcar reabre
    assert _esperar(qapp, lambda: ("reabrir", "Antiga") in feitos and not p.ocupado)
    assert not _caixas(p)["Antiga"].isChecked()


def test_painel_marca_meta_no_arquivo(cfg, pasta, qapp):
    p = _painel(cfg, pasta, [], [], [])
    p.ao_abrir()
    assert _esperar(qapp, lambda: p.dados is not None and not p.ocupado)
    _caixas(p)["Ler 12 livros"].click()
    assert _esperar(qapp, lambda: any(m.concluida and m.titulo == "Ler 12 livros" for m in notas.metas(pasta)) and not p.ocupado)
    assert _caixas(p)["Ler 12 livros"].isChecked()


def test_painel_sem_google_mostra_so_as_metas_e_avisa(cfg, pasta, qapp):
    from eco.visual.dashboard import PainelDashboard

    def quebra():
        raise ConnectionError("sem rede")

    p = PainelDashboard(cfg, ler=quebra)
    p.ao_abrir()
    assert _esperar(qapp, lambda: p.dados is not None and not p.ocupado)
    assert "Google indisponível" in p.status.text() and "Ler 12 livros" in _caixas(p)


def test_janela_principal_ganha_a_aba_painel(cfg, qapp, tmp_path):
    from eco.chat.armazenamento import Banco
    from eco.visual.chat import PainelChat
    from eco.visual.dashboard import PainelDashboard
    from eco.visual.grafo import PainelGrafo
    from eco.visual.principal import JanelaPrincipal

    banco = Banco(tmp_path / "eco.db")
    chat = PainelChat(cfg, None, banco, object(), openai=object())
    painel = PainelDashboard(cfg, ler=lambda: revisao.coletar(cfg.pasta_dados, [], [], SEGUNDA))
    j = JanelaPrincipal(cfg, chat=chat, grafo=PainelGrafo(cfg, banco, object(), confirmar=lambda t: True), dashboard=painel)
    assert [b.text() for b in j.abas] == ["Conversas", "Grafo", "Painel"]
    j.mostrar_aba(2)
    assert j.corpo.currentWidget() is painel and j.abas[2].isChecked()
    assert _esperar(qapp, lambda: painel.dados is not None)
