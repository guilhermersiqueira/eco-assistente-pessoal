"""Bloco de notas: uma página por dia (arquivo notas/AAAA-MM-DD.md), editável, e assuntos que se repetem ligados no grafo."""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta

import pytest

from eco.chat.armazenamento import Banco
from eco.config import carregar_config
from eco.ferramentas import notas
from eco.grafo.analise import Analisador, Resposta
from eco.grafo.dados import Grafo

HOJE = date.today()
ONTEM = HOJE - timedelta(days=1)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    return carregar_config()


@pytest.fixture
def pasta(cfg):
    cfg.pasta_dados.mkdir(parents=True, exist_ok=True)
    return cfg.pasta_dados


@pytest.fixture
def banco(tmp_path):
    b = Banco(tmp_path / "eco.db")
    yield b
    b.fechar()


# --- arquivo ---------------------------------------------------------------------------------------------------
def test_anotar_por_voz_cria_a_pagina_do_dia_e_acrescenta(pasta):
    msg = notas.salvar_anotacao(pasta, "ideia: app de receitas", datetime(2026, 11, 9, 10, 5))
    assert "bloco de notas de 09/11/2026" in msg
    notas.salvar_anotacao(pasta, "comprar café", datetime(2026, 11, 9, 11, 0))
    assert notas.ler_dia(pasta, date(2026, 11, 9)) == "# Notas de 09/11/2026\n\n- 10:05 ideia: app de receitas\n- 11:00 comprar café\n"
    assert notas.dias_com_nota(pasta) == [date(2026, 11, 9)]


def test_dias_com_nota_do_mais_novo_ao_mais_antigo_e_ignora_arquivo_estranho(pasta):
    for nome in ("2026-11-01", "2026-11-09", "rascunho"):
        (pasta / "notas").mkdir(exist_ok=True)
        (pasta / "notas" / f"{nome}.md").write_text("x", encoding="utf-8")
    assert notas.dias_com_nota(pasta) == [date(2026, 11, 9), date(2026, 11, 1)]
    assert notas.dias_com_nota(pasta / "nao-existe") == []


def test_salvar_dia_poe_cabecalho_edita_e_apaga_quando_vazio(pasta):
    dia = date(2026, 11, 9)
    assert notas.salvar_dia(pasta, dia, "minha ideia") == "# Notas de 09/11/2026\n\nminha ideia\n"
    assert notas.salvar_dia(pasta, dia, "# Notas de 09/11/2026\n\nminha ideia melhor") .endswith("minha ideia melhor\n")
    assert notas.salvar_dia(pasta, dia, "  \n ") == "" and notas.dias_com_nota(pasta) == []


def test_salvar_dia_mantem_o_que_a_eco_anotou_enquanto_o_dono_digitava(pasta):
    notas.salvar_anotacao(pasta, "primeira", datetime(2026, 11, 9, 9, 0))
    base = notas.ler_dia(pasta, date(2026, 11, 9))  # a página foi aberta aqui
    notas.salvar_anotacao(pasta, "dita por voz", datetime(2026, 11, 9, 9, 30))  # chega no arquivo
    final = notas.salvar_dia(pasta, date(2026, 11, 9), base + "\numa frase que o dono digitou", base)
    assert "uma frase que o dono digitou" in final and final.rstrip().endswith("- 09:30 dita por voz")
    assert final.count("primeira") == 1


# --- grafo: dias por ideia e análise ---------------------------------------------------------------------------
class Falso:
    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.usuarios = []

    def completar(self, sistema, usuario):
        self.usuarios.append(usuario)
        return Resposta(json.dumps(self.respostas.pop(0)), 0.0004)


def test_analise_das_notas_liga_o_assunto_aos_dias_em_que_apareceu(banco, cfg, pasta):
    notas.salvar_anotacao(pasta, "ideia de app de receitas para a vó", datetime.combine(ONTEM, datetime.min.time()).replace(hour=9))
    notas.salvar_anotacao(pasta, "voltei a pensar no app de receitas, talvez com voz", datetime.now().replace(hour=15))
    notas.salvar_anotacao(pasta, "marcar dentista", datetime.now().replace(hour=16))
    grafo = Grafo(banco)
    falso = Falso(
        {
            "ideias": [
                {"nome": "App de receitas", "resumo": "Para a vó.", "dias": [f"{ONTEM:%Y-%m-%d}", f"{HOJE:%Y-%m-%d}", "2001-01-01"]},
                {"nome": "Dentista", "resumo": "Marcar consulta.", "dias": [f"{HOJE:%Y-%m-%d}"]},
            ],
            "ligacoes": [],
        }
    )
    an = Analisador(banco, grafo, falso, cfg)
    pend = [p for p in an.pendentes() if p.fonte == "notas"]
    assert len(pend) == 1
    assert f"## {HOJE:%Y-%m-%d}" in pend[0].texto and f"## {ONTEM:%Y-%m-%d}" in pend[0].texto and "# Notas de" not in pend[0].texto
    an.executar(pend)
    app = next(i for i in grafo.ideias_da_origem("notas") if i.nome == "App de receitas")
    assert grafo.dias(app.id) == [f"{HOJE:%Y-%m-%d}", f"{ONTEM:%Y-%m-%d}"]  # a data inventada ("2001") é descartada
    assert grafo.contagem_dias()[app.id] == 2
    assert [p for p in an.pendentes() if p.fonte == "notas"] == []  # nada mudou: sem nova chamada
    notas.salvar_dia(pasta, HOJE, notas.ler_dia(pasta, HOJE) + "\n- 17:00 mais uma coisa")  # o dono editou a página
    assert len([p for p in an.pendentes() if p.fonte == "notas"]) == 1  # mudou: reanalisa só as notas


def test_dias_somem_junto_com_a_ideia(banco):
    g = Grafo(banco)
    i = g.criar_ideia("Assunto", None, "notas", "x")
    g.adicionar_dia(i.id, "2026-11-09")
    g.adicionar_dia(i.id, "2026-11-09")  # repetido não duplica
    assert g.dias(i.id) == ["2026-11-09"]
    g.remover(i.id)
    assert g.dias(i.id) == []


# --- aba Bloco -------------------------------------------------------------------------------------------------
pytest.importorskip("PySide6")


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


def test_aba_bloco_abre_hoje_salva_sozinha_e_mostra_anotacao_de_voz(cfg, pasta, qapp):
    from eco.visual.bloco import PainelBloco

    p = PainelBloco(cfg)
    p.show()
    p.ao_abrir()
    assert p.dia == HOJE and p.lista.count() == 1 and "Hoje" in p.lista.item(0).text()
    p.editor.setPlainText("ideia solta sobre o TCC")
    assert p.sujo and _esperar(qapp, lambda: not p.sujo)  # autosave
    assert "ideia solta sobre o TCC" in notas.ler_dia(pasta, HOJE) and "Salvo" in p.status.text()
    notas.salvar_anotacao(pasta, "dita por voz")  # a Eco anota enquanto a aba está aberta
    p._sondar()
    assert "dita por voz" in p.editor.toPlainText()
    p.hide()


def test_aba_bloco_troca_de_dia_busca_e_nao_perde_o_que_foi_digitado(cfg, pasta, qapp):
    from eco.visual.bloco import PainelBloco

    notas.salvar_dia(pasta, ONTEM, "reunião sobre o orçamento da reforma")
    notas.salvar_dia(pasta, HOJE - timedelta(days=5), "comprar presente")
    p = PainelBloco(cfg)
    p.show()
    p.ao_abrir()
    assert p.lista.count() == 3
    p.editor.setPlainText("texto de hoje ainda não salvo")
    p.abrir_dia(ONTEM)  # trocar de dia salva antes
    assert "ainda não salvo" in notas.ler_dia(pasta, HOJE)
    assert "orçamento da reforma" in p.editor.toPlainText() and p.dia == ONTEM
    p.busca.setText("ORCAMENTO")  # sem acento e sem diferenciar maiúsculas
    assert p.lista.count() == 1 and "Hoje" not in p.lista.item(0).text()
    p.busca.setText("")
    p.lista.setCurrentRow(0)  # clicar em hoje na lista
    assert p.dia == HOJE
    p.hide()


def test_aba_bloco_pagina_apagada_some_da_lista(cfg, pasta, qapp):
    from eco.visual.bloco import PainelBloco

    notas.salvar_dia(pasta, ONTEM, "algo")
    p = PainelBloco(cfg)
    p.show()
    p.ao_abrir()
    p.abrir_dia(ONTEM)
    p.editor.setPlainText("")
    p.salvar_agora()
    assert notas.dias_com_nota(pasta) == [] or ONTEM not in notas.dias_com_nota(pasta)
    p.hide()


def test_clique_no_dia_do_cartao_abre_o_bloco_nessa_pagina(cfg, pasta, qapp, tmp_path):
    from eco.visual.bloco import PainelBloco
    from eco.visual.chat import PainelChat
    from eco.visual.grafo import PainelGrafo
    from eco.visual.principal import JanelaPrincipal

    banco = Banco(tmp_path / "eco2.db")
    notas.salvar_dia(pasta, ONTEM, "ideia do app de receitas")
    chat = PainelChat(cfg, None, banco, object(), openai=object())
    grafo = PainelGrafo(cfg, banco, object(), confirmar=lambda t: True)
    bloco = PainelBloco(cfg)
    j = JanelaPrincipal(cfg, chat=chat, grafo=grafo, bloco=bloco)
    assert [b.text() for b in j.abas] == ["Conversas", "Grafo", "Bloco"]
    grafo.abrir_nota.emit(f"{ONTEM:%Y-%m-%d}")  # o que o clique no cartão faz
    assert j.corpo.currentWidget() is bloco and j.abas[2].isChecked()
    assert bloco.dia == ONTEM and "app de receitas" in bloco.editor.toPlainText()


def test_cartao_do_grafo_mostra_os_dias_da_ideia(cfg, pasta, qapp, banco):
    from eco.visual.grafo import PainelGrafo

    g = Grafo(banco)
    i = g.criar_ideia("App de receitas", None, "notas", "Para a vó.")
    g.adicionar_dia(i.id, f"{ONTEM:%Y-%m-%d}")
    g.adicionar_dia(i.id, f"{HOJE:%Y-%m-%d}")
    p = PainelGrafo(cfg, banco, object(), confirmar=lambda t: True)
    p.vista.recarregar()
    p.vista._mostrar_cartao(i.id)
    from PySide6.QtWidgets import QLabel, QPushButton

    textos = [w.text() for w in p.vista.cartao.findChildren(QLabel)]
    assert any("assunto recorrente" in t for t in textos) and not any("Sem conversa de origem" in t for t in textos)
    recebidos = []
    p.abrir_nota.connect(recebidos.append)
    botoes = [b for b in p.vista.cartao.findChildren(QPushButton) if b.objectName() == "fonte"]
    assert len(botoes) == 2
    botoes[0].click()
    assert recebidos == [f"{HOJE:%Y-%m-%d}"]  # o mais recente primeiro
