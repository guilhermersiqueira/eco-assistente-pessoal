"""Interface de gerenciamento do grafo (E8, Qt offscreen): cartão com conexões e hierarquia navegáveis, ações no cartão,
tipo da relação nas ligações, busca, painel "Gerenciar" (seções, aceitar, rejeitar, aplicar, restaurar, desproteger,
edição à mão, revisar agora) e as imagens de conferência em tests/resultados/imagens."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from eco.grafo.analise import Resposta  # noqa: E402

IMAGENS = Path(__file__).parent / "resultados" / "imagens"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_GRAFO_VISTA", "2d")
    (tmp_path / "dados").mkdir()
    from eco.config import carregar_config

    return carregar_config()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _esperar(qapp, cond, s=5):
    fim = time.time() + s
    while time.time() < fim:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def tela(cfg, qapp, tmp_path, monkeypatch):
    """Painel do grafo com um acervo pequeno: hierarquia, ligações classificadas, sugestão, arquivada, protegida."""
    from eco.chat.armazenamento import Banco
    from eco.visual import dialogos
    from eco.visual.grafo import PainelGrafo

    monkeypatch.setattr(dialogos, "perguntar", lambda *a, **k: True)
    monkeypatch.setattr(dialogos, "avisar", lambda *a, **k: None)
    banco = Banco(tmp_path / "eco.db")
    from eco.grafo.dados import Grafo

    g = Grafo(banco)
    ids = {n: g.criar_ideia(n, origem="manual", resumo=r).id for n, r in [
        ("Programação", "estudos de programação"), ("Python", "linguagem que o dono estuda"),
        ("Automação CSM", "scripts de fechamento"), ("Eco", "assistente de voz"), ("Viagem", "Ouro Preto em julho"),
        ("Rascunho antigo", "ideia velha")]}
    g.definir_pai(ids["Python"], ids["Programação"])
    g.ligar_classificada(ids["Automação CSM"], ids["Python"], "depende_de", 0.9, "os scripts são em Python", "ativa",
                         de=ids["Automação CSM"], tipo="causal")
    g.ligar_classificada(ids["Eco"], ids["Python"], "relacionado_a", 0.65, "a Eco é feita em Python", "revisar")
    g.ligar_classificada(ids["Viagem"], ids["Eco"], "relacionado_a", 0.35, "talvez", "sugestao")
    g.arquivar(ids["Rascunho antigo"], "não uso mais")
    g.proteger(ids["Viagem"])
    p = PainelGrafo(cfg, banco, object(), confirmar=lambda t: True)
    p.resize(1400, 900)
    p.show()
    qapp.processEvents()
    return p, g, ids


def _botoes(w, texto):
    from PySide6.QtWidgets import QPushButton

    return [b for b in w.findChildren(QPushButton) if texto in b.text() and b.isVisibleTo(w)]


# --- mapa e cartão -------------------------------------------------------------------------------------------------
def test_ligacao_mostra_o_tipo_da_relacao(tela):
    p, g, ids = tela
    l = next(x for x in p.vista.ligacoes if {x.a, x.b} == {ids["Automação CSM"], ids["Python"]})
    assert l.relacao_legivel() == "depende de" and l.de == ids["Automação CSM"]
    revisar = next(x for x in p.vista.ligacoes if {x.a, x.b} == {ids["Eco"], ids["Python"]})
    assert revisar.status == "revisar"
    assert not any({x.a, x.b} == {ids["Viagem"], ids["Eco"]} for x in p.vista.ligacoes)  # sugestão não aparece no mapa


def test_cartao_navega_pelas_ligacoes_e_pela_hierarquia(tela, qapp):
    p, g, ids = tela
    v = p.vista
    v.navegar(ids["Python"])
    qapp.processEvents()
    assert v.selecionado == ids["Python"] and v.cartao.isVisibleTo(v)
    assert _botoes(v.cartao, "↑ Programação")
    ligacao = _botoes(v.cartao, "→ Automação CSM depende desta")
    assert ligacao and _botoes(v.cartao, "para revisar")
    ligacao[0].click()
    qapp.processEvents()
    assert v.selecionado == ids["Automação CSM"]
    v.navegar(ids["Programação"])
    qapp.processEvents()
    _botoes(v.cartao, "↓ Python")[0].click()
    assert v.selecionado == ids["Python"]


def test_acoes_do_cartao_proteger_arquivar_e_historico(tela, qapp):
    p, g, ids = tela
    v = p.vista
    v.navegar(ids["Eco"])
    qapp.processEvents()
    _botoes(v.cartao, "Proteger")[0].click()
    assert g.ideia(ids["Eco"]).protegido
    v.navegar(ids["Eco"])
    qapp.processEvents()  # o Qt mostra os botões novos de um cartão já visível no próximo ciclo de eventos
    _botoes(v.cartao, "Arquivar")[0].click()
    qapp.processEvents()
    assert g.ideia(ids["Eco"]).arquivada and ids["Eco"] not in v.nos
    assert any(e["acao"] == "arquivar" and e["autor"] == "clique na tela do grafo" for e in g.historico(ids["Eco"]))


def test_busca_leva_ate_a_ideia(tela, qapp):
    p, g, ids = tela
    p.busca.setText("automação")
    p.buscar()
    assert p.vista.selecionado == ids["Automação CSM"] and "Achei" in p.status.text()
    p.busca.setText("astronomia")
    p.buscar()
    assert "Não achei" in p.status.text()


# --- painel Gerenciar ------------------------------------------------------------------------------------------------
def test_painel_tem_todas_as_secoes(tela, qapp):
    p, g, ids = tela
    p.alternar_gerenciar(True)
    qapp.processEvents()
    s = p.gerenciar.secoes
    assert set(s) >= {"resumo", "ligacoes", "contradicoes", "propostas", "arquivadas", "protegidas", "historico", "custos"}
    assert s["ligacoes"].contador.text() == "2" and s["arquivadas"].contador.text() == "1" and s["protegidas"].contador.text() == "1"
    assert p.btn_gerenciar.text() == "Fechar painel"
    p.alternar_gerenciar()
    assert not p.gerenciar.isVisible()


def test_aceitar_rejeitar_e_trocar_tipo_pelo_painel(tela, qapp):
    p, g, ids = tela
    p.alternar_gerenciar(True)
    _botoes(p.gerenciar.secoes["ligacoes"], "Aceitar")[0].click()  # Eco x Python (revisar)
    l = g.ligacao_do_par(ids["Eco"], ids["Python"])
    assert l.status == "ativa" and l.origem == "manual"
    assert p.gerenciar.secoes["ligacoes"].contador.text() == "1"
    _botoes(p.gerenciar.secoes["ligacoes"], "Rejeitar")[0].click()  # Viagem x Eco (sugestão)
    assert g.ligacao_do_par(ids["Viagem"], ids["Eco"]).status == "rejeitada"
    l = g.ligacao_do_par(ids["Automação CSM"], ids["Python"])
    p.gerenciar._fazer(lambda: p.gerenciar._mudar(l, "faz_parte_de"))
    assert g.ligacao_do_par(ids["Automação CSM"], ids["Python"]).relacao == "faz_parte_de"


def test_recusar_no_dialogo_nao_muda_nada(tela, qapp):
    p, g, ids = tela
    p.gerenciar.confirmar = lambda titulo, texto: False
    p.alternar_gerenciar(True)
    _botoes(p.gerenciar.secoes["ligacoes"], "Rejeitar")[0].click()
    assert g.ligacao_do_par(ids["Eco"], ids["Python"]).status == "revisar"
    _botoes(p.gerenciar.secoes["protegidas"], "Desproteger")[0].click()
    assert g.ideia(ids["Viagem"]).protegido


def test_restaurar_desproteger_e_ver(tela, qapp):
    p, g, ids = tela
    p.alternar_gerenciar(True)
    _botoes(p.gerenciar.secoes["arquivadas"], "Restaurar")[0].click()
    assert not g.ideia(ids["Rascunho antigo"]).arquivada and ids["Rascunho antigo"] in p.vista.nos
    _botoes(p.gerenciar.secoes["protegidas"], "Ver")[0].click()
    assert p.vista.selecionado == ids["Viagem"]
    _botoes(p.gerenciar.secoes["protegidas"], "Desproteger")[0].click()
    assert not g.ideia(ids["Viagem"]).protegido


def test_propostas_e_contradicoes_no_painel(tela, qapp):
    from eco.grafo.higiene import Higiene

    p, g, ids = tela
    h = Higiene(g, None)
    h._propor("arquivar", [ids["Viagem"]], "a validade passou", {})
    a = g.criar_fato("Trabalha às 8h.", "Horário")
    b = g.criar_fato("Trabalha às 9h.", "Horário novo")
    h._propor("contradicao", [a.id, b.id], "horários diferentes", {})
    p.vista.recarregar()
    p.alternar_gerenciar(True)
    assert p.gerenciar.secoes["contradicoes"].contador.text() == "1"
    _botoes(p.gerenciar.secoes["contradicoes"], "Vale \"Horário novo")[0].click()
    assert g.ideia(a.id).arquivada and not g.ideia(b.id).arquivada
    _botoes(p.gerenciar.secoes["propostas"], "Aplicar")[0].click()
    assert g.ideia(ids["Viagem"]).arquivada
    assert p.gerenciar.secoes["propostas"].contador.text() == "0"


def test_edicao_a_mao_aparece_e_importa(tela, qapp):
    p, g, ids = tela
    mem = p.vista_2d.memoria.memorias
    mem.inicializar()
    mem.lembrar("Vitor gosta de café.")
    mem.arquivo.write_text(mem.arquivo.read_text(encoding="utf-8") + "- Fato à mão. (13/11/2026)\n", encoding="utf-8")
    p.alternar_gerenciar(True)
    assert "edicao" in p.gerenciar.secoes
    _botoes(p.gerenciar.secoes["edicao"], "Importar")[0].click()
    assert any(f.texto == "Fato à mão." for f in mem.fatos()) and "edicao" not in p.gerenciar.secoes


def test_revisar_agora_roda_em_segundo_plano(tela, qapp):
    p, g, ids = tela

    class HigieneFalsa:
        def revisar(self):
            return type("R", (), {"frase": lambda self, n: f"Revisei: {n} proposta(s)."})()

        def propostas(self):
            return [1, 2]

    p.gerenciar._higiene_fabrica = HigieneFalsa
    p.alternar_gerenciar(True)
    p.gerenciar.btn_revisar.click()
    assert _esperar(qapp, lambda: not p.gerenciar.rodando)
    assert p.gerenciar.aviso.text() == "Revisei: 2 proposta(s)."


def test_imagens_de_conferencia(tela, qapp, tmp_path):
    """Sem tela (offscreen) as letras saem como quadrados: aqui só confere que desenha. As imagens com fontes reais
    ficam em tests/resultados/imagens (gerar com tests/imagens_e8.py)."""
    p, g, ids = tela
    IMAGENS = tmp_path
    p.vista.navegar(ids["Python"])
    p.alternar_gerenciar(True)
    for _ in range(30):
        qapp.processEvents()
        time.sleep(0.01)
    assert p.grab().save(str(IMAGENS / "e8_grafo_gerenciar.png"))
    p.alternar_vista("orbe")
    p.vista.navegar(ids["Python"])
    for _ in range(30):
        qapp.processEvents()
        time.sleep(0.01)
    assert p.grab().save(str(IMAGENS / "e8_orbe_cartao.png"))


# --- importação das conversas do claude.ai (M7) ----------------------------------------------------------------------------
def _painel_importacao(cfg, qapp, tmp_path, monkeypatch, fatos, conflito=False):
    """Painel do Gerenciar ligado a um Importador com export sintético e Haiku simulado."""
    import zipfile
    from types import SimpleNamespace

    from eco.chat.armazenamento import Banco
    from eco.grafo import semelhanca as sem
    from eco.grafo.dados import Grafo
    from eco.grafo.memorias import Memorias
    from eco.importacao.servico import Importador
    from eco.visual import dialogos
    from eco.visual.gerenciar import PainelGerenciar

    sys_path = str(Path(__file__).parent)
    import sys

    sys.path.insert(0, sys_path)
    from test_importacao_servico import FATO, Haiku, _export  # noqa: F401
    from test_semelhanca import CodificadorFalso

    monkeypatch.setattr(dialogos, "perguntar", lambda *a, **k: True)
    pasta = tmp_path / "Downloads"
    pasta.mkdir()
    _export(pasta, 2)
    banco = Banco(tmp_path / "eco.db")
    grafo = Grafo(banco)
    mem = Memorias(grafo, tmp_path / "dados")
    mem.inicializar()
    mem.lembrar_no("Vitor gosta de café coado.")
    c = SimpleNamespace(modelo_grafo="claude-haiku-5-5", importar_teto_usd=2.0)
    imp = Importador(c, banco, grafo, mem, pastas=[pasta], completador_fabrica=lambda cat, t: Haiku(fatos),
                     semelhanca_fabrica=lambda: sem.Semelhanca(grafo, CodificadorFalso()), antes_de_gravar=lambda: None)
    painel = PainelGerenciar(grafo, cfg, mem, confirmar=lambda t, x: True, importador_fabrica=lambda: imp)
    painel.show()
    painel.recarregar()
    return painel, imp, mem


def test_painel_importa_e_o_dono_revisa_item_a_item(cfg, qapp, tmp_path, monkeypatch):
    p, imp, mem = _painel_importacao(cfg, qapp, tmp_path, monkeypatch, ["Vitor faz uma landing page para a padaria do tio.",
                                                                          "Vitor mora em Vila Mariana."])
    assert "importacao" in p.secoes and "importados" not in p.secoes
    assert not _botoes(p.secoes["importacao"], "Importar tudo")  # só aparece depois de procurar o export
    _botoes(p.secoes["importacao"], "Procurar export")[0].click()
    assert _esperar(qapp, lambda: not p.rodando)
    assert _botoes(p.secoes["importacao"], "Importar tudo") and _botoes(p.secoes["importacao"], "Testar com")
    _botoes(p.secoes["importacao"], "Importar tudo")[0].click()
    assert _esperar(qapp, lambda: not p.rodando)
    assert p.secoes["importados"].contador.text() == "2"
    assert "fato(s)" in p.aviso.text() and len(imp.estado.propostas()) == 2
    antes = len(mem.nos())
    _botoes(p.secoes["importados"], "Aprovar")[0].click()
    assert p.secoes["importados"].contador.text() == "1" and len(mem.nos()) == antes + 1
    assert len(mem.importadas()) == 1
    _botoes(p.secoes["importados"], "Rejeitar")[0].click()
    assert "importados" not in p.secoes and len(mem.nos()) == antes + 1


def test_painel_recusar_a_confirmacao_nao_gasta(cfg, qapp, tmp_path, monkeypatch):
    p, imp, mem = _painel_importacao(cfg, qapp, tmp_path, monkeypatch, ["Vitor mora em Vila Mariana."])
    p.procurar_export()
    assert _esperar(qapp, lambda: not p.rodando)
    perguntas = []
    p.confirmar = lambda t, x: perguntas.append(x) or False
    p.importar_agora(None)
    assert len(perguntas) == 1 and "Anthropic" in perguntas[0] and "US$" in perguntas[0]
    assert imp.estado.propostas() == [] and not p.rodando


def test_painel_mostra_conflito_lado_a_lado_e_substitui(cfg, qapp, tmp_path, monkeypatch):
    p, imp, mem = _painel_importacao(cfg, qapp, tmp_path, monkeypatch, ["Vitor gosta de café com leite."])
    p.procurar_export()
    assert _esperar(qapp, lambda: not p.rodando)
    p.importar_agora(None)
    assert _esperar(qapp, lambda: not p.rodando)
    [prop] = imp.estado.propostas()
    antiga = next(i for i in mem.nos() if "coado" in i.resumo)
    imp.estado.marcar_conferida(prop.id, antiga.id, antiga.resumo, "contradiz", "gosto diferente")
    p.recarregar()
    assert not _botoes(p.secoes["importados"], "Aprovar")
    assert _botoes(p.secoes["importados"], "Manter as duas")
    _botoes(p.secoes["importados"], "Substituir a antiga")[0].click()
    assert mem.grafo.ideia(antiga.id).arquivada and "importados" not in p.secoes
    assert [i.resumo for i in mem.importadas()] == ["Vitor gosta de café com leite."]


def test_painel_editar_o_fato_antes_de_aprovar(cfg, qapp, tmp_path, monkeypatch):
    from eco.visual import dialogos

    p, imp, mem = _painel_importacao(cfg, qapp, tmp_path, monkeypatch, ["Vitor mora em Vila Mariana."])
    p.procurar_export()
    assert _esperar(qapp, lambda: not p.rodando)
    p.importar_agora(None)
    assert _esperar(qapp, lambda: not p.rodando)
    monkeypatch.setattr(dialogos, "texto_longo", lambda *a, **k: ("Vitor mora em Curitiba.", True))
    _botoes(p.secoes["importados"], "Editar")[0].click()
    assert imp.propostas()[0].texto == "Vitor mora em Curitiba."
    _botoes(p.secoes["importados"], "Aprovar")[0].click()
    assert [i.resumo for i in mem.importadas()] == ["Vitor mora em Curitiba."]


def test_painel_do_grafo_liga_a_secao_de_importacao(tela):
    p, g, ids = tela
    p.alternar_gerenciar(True)
    assert "importacao" in p.gerenciar.secoes  # o painel do grafo liga o importador real


def test_painel_agrupa_os_fatos_e_rejeita_um_assunto_inteiro(cfg, qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QLabel

    from eco.importacao.modelo import DONO, Conversa, Mensagem

    p, imp, mem = _painel_importacao(cfg, qapp, tmp_path, monkeypatch, [])
    for uuid, titulo, data in (("a", "Conversa A", "2026-10-02"), ("b", "Investimentos do Vitor — aporte mensal", "2026-10-05")):
        imp.estado.registrar_conversa(Conversa(uuid, titulo, data + "T10:00:00Z", data, (Mensagem(DONO, "x"),)))
    imp.estado.propor("a", "Vitor tem um gato chamado Mel.", "tenho um gato chamado Mel")
    imp.estado.propor("b", "Vitor investe em renda fixa.")
    imp.estado.propor("b", "Vitor tem ações de bancos.")
    p.recarregar()
    textos = [l.text() for l in p.secoes["importados"].findChildren(QLabel)]
    assert any(t.startswith("Investimentos do Vitor · 2 fato(s)") for t in textos)
    assert not any("aporte mensal" in t for t in textos)  # o grupo mostra o nome curto, não a descrição inteira
    assert any(t.startswith("Vitor tem um gato chamado Mel.\n\"tenho um gato") for t in textos)
    _botoes(p.secoes["importados"], "Rejeitar todos")[0].click()  # o primeiro grupo: o mais recente (investimentos)
    assert [x.texto for x in imp.propostas()] == ["Vitor tem um gato chamado Mel."]
    assert p.secoes["importados"].contador.text() == "1"
