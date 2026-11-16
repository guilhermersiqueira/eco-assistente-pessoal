"""Orbe 3D do grafo (`grafo/esfera.py` e `visual/grafo_orbe.py`) e partículas de fundo (`visual/fundo.py`)."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from eco.grafo import esfera
from eco.grafo.esfera import CAMADAS, Item


# --- geometria da esfera (sem Qt) -------------------------------------------------------------------------------------
def _arvore() -> list[Item]:
    itens = [Item("t1", "tronco", None, "inv"), Item("t2", "tronco", None, "est")]
    itens += [Item("g1", "galho", "t1", "inv"), Item("g2", "galho", "t2", "est")]
    itens += [Item(f"f{i}", "folha", "g1", "inv") for i in range(6)]
    itens += [Item(f"h{i}", "folha", "g2", "est") for i in range(6)]
    itens += [Item("solta", "folha", None, "inv")]
    return itens


def test_cada_nivel_fica_na_sua_camada():
    pos = esfera.posicoes(_arvore())
    for item in _arvore():
        assert np.linalg.norm(pos[item.id]) == pytest.approx(CAMADAS[item.nivel], abs=1e-6)


def test_filho_nasce_do_lado_do_pai():
    pos = esfera.posicoes(_arvore())
    d = {k: v / np.linalg.norm(v) for k, v in pos.items()}
    for folha in ("f0", "f1", "f2"):
        assert float(d[folha] @ d["g1"]) > float(d[folha] @ d["g2"])  # mais perto do próprio galho que do outro
    assert float(d["g1"] @ d["t1"]) > float(d["g1"] @ d["t2"])


def test_posicoes_sao_estaveis_e_reorganizar_muda():
    a, b = esfera.posicoes(_arvore()), esfera.posicoes(_arvore())
    assert all(np.allclose(a[k], b[k]) for k in a)
    c = esfera.posicoes(_arvore(), sal=1)
    assert any(not np.allclose(a[k], c[k]) for k in a)


def test_ideias_da_mesma_camada_nao_ficam_coladas():
    itens = [Item(f"f{i}", "folha", "g", "x") for i in range(30)] + [Item("g", "galho", None, "x")]
    pos = esfera.posicoes(itens)
    folhas = np.array([pos[f"f{i}"] for i in range(30)])
    dist = np.linalg.norm(folhas[:, None] - folhas[None, :], axis=-1) + np.eye(30) * 9
    assert dist.min() > 0.08


def test_ciclo_de_pais_nao_trava():
    pos = esfera.posicoes([Item("a", "galho", "b", "x"), Item("b", "galho", "a", "x")])
    assert set(pos) == {"a", "b"}


def test_projecao_poe_a_frente_maior_e_mais_perto():
    R = esfera.rotacao(0.0, 0.0)
    x, y, z, persp = esfera.projetar(np.array([[0, 0, 1.0], [0, 0, -1.0]]), R, 100, 0, 0)
    assert z[0] > z[1] and persp[0] > 1 > persp[1]
    assert esfera.projetar(np.zeros((0, 3)), R, 100, 0, 0)[0].shape == (0,)


# --- vista Orbe (Qt offscreen) ---------------------------------------------------------------------------------------
@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_MOVIMENTO_REDUZIDO", "0")
    from eco.config import carregar_config

    return carregar_config()


@pytest.fixture
def qapp(cfg):
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _painel(cfg, tmp_path):
    from eco.chat.armazenamento import Banco
    from eco.grafo.dados import Grafo
    from eco.visual.grafo import PainelGrafo

    banco = Banco(tmp_path / "eco.db")
    esp = banco.criar_espaco("Investimentos", "#7ED3D9")
    g = Grafo(banco)
    tronco = g.criar_ideia("Finanças", esp.id, nivel="tronco")
    galho = g.criar_ideia("Reserva", esp.id, nivel="galho")
    g.definir_pai(galho.id, tronco.id)
    folhas = [g.criar_ideia(n, esp.id, nivel="folha") for n in ("Selic", "CDB", "Liquidez")]
    for f in folhas:
        g.definir_pai(f.id, galho.id)
    p = PainelGrafo(cfg, banco, object(), confirmar=lambda t: False)
    p.resize(900, 640)
    p.show()
    p.vista.assentar()
    return p, g, tronco, galho, folhas


def _evento(v, tipo, ponto, botao=None):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    b = botao or Qt.MouseButton.NoButton
    tipos = {"move": QEvent.Type.MouseMove, "press": QEvent.Type.MouseButtonPress, "release": QEvent.Type.MouseButtonRelease}
    lp = QPointF(ponto)
    return QMouseEvent(tipos[tipo], lp, v.mapToGlobal(lp), b, b if tipo == "press" else Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)


def test_orbe_e_a_vista_padrao_e_o_botao_alterna(cfg, qapp, tmp_path):
    from eco.visual.grafo_orbe import VistaOrbe

    p, *_ = _painel(cfg, tmp_path)
    assert isinstance(p.vista, VistaOrbe) and p.btn_vista.text() == "Ver em 2D" and p.mapa.titulo.text() == "Orbe de ideias"
    p.alternar_vista()
    assert p.vista is p.vista_2d and p.pilha.currentWidget() is p.vista_2d and len(p.vista.nos) == 5
    p.alternar_vista()
    assert p.vista is p.vista_orbe


def test_vista_2d_pela_configuracao(cfg, qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_GRAFO_VISTA", "2d")
    from eco.config import carregar_config

    cfg2 = carregar_config()
    from eco.chat.armazenamento import Banco
    from eco.visual.grafo import PainelGrafo

    p = PainelGrafo(cfg2, Banco(tmp_path / "eco.db"), object(), confirmar=lambda t: False)
    assert p.vista is p.vista_2d


def test_camadas_na_tela_e_clique_seleciona(cfg, qapp, tmp_path):
    from PySide6.QtCore import QPoint, Qt

    p, g, tronco, galho, folhas = _painel(cfg, tmp_path)
    v = p.vista
    assert v.nos[tronco.id].nivel == "tronco" and v.nos[galho.id].nivel == "galho" and v.nos[folhas[0].id].nivel == "folha"
    assert np.linalg.norm(v.atual[tronco.id]) < np.linalg.norm(v.atual[galho.id]) < np.linalg.norm(v.atual[folhas[0].id])
    assert v._raio_tela(tronco.id) > v._raio_tela(folhas[0].id) * 0.9  # tronco é maior (fora a perspectiva)
    alvo = v._ids[int(np.argmax(v._sz))]  # a ideia mais à frente: nenhuma outra pode cobri-la no ponto do clique
    t = v._ponto(alvo)
    pt = QPoint(round(t.x()), round(t.y()))
    assert v._no_em(t) == alvo
    v.mousePressEvent(_evento(v, "press", pt, Qt.MouseButton.LeftButton))
    v.mouseReleaseEvent(_evento(v, "release", pt, Qt.MouseButton.LeftButton))
    assert v.selecionado == alvo and v.cartao.isVisibleTo(v)


def test_no_da_frente_ganha_no_clique(cfg, qapp, tmp_path):
    from PySide6.QtCore import QPointF

    p, *_ = _painel(cfg, tmp_path)
    v = p.vista
    a, b = list(v.atual)[:2]
    v.atual[a] = np.array([0.0, 0.0, 0.9])  # frente
    v.atual[b] = np.array([0.0, 0.0, -0.9])  # atrás, no mesmo ponto da tela
    v.guinada = v.arfagem = 0.0
    v._projetar()
    assert v._no_em(QPointF(v._cx, v._cy)) == a


def test_arrastar_gira_com_inercia_e_nao_seleciona(cfg, qapp, tmp_path):
    from PySide6.QtCore import QPoint, Qt

    p, *_ = _painel(cfg, tmp_path)
    v = p.vista
    g0, ini = v.guinada, QPoint(80, 80)
    v.mousePressEvent(_evento(v, "press", ini, Qt.MouseButton.LeftButton))
    for i in range(1, 6):
        v.mouseMoveEvent(_evento(v, "move", ini + QPoint(20 * i, 5 * i), Qt.MouseButton.LeftButton))
    assert v.guinada == pytest.approx(g0 + 100 * 0.0075) and v.vg > 0
    v._t_giro = __import__("time").perf_counter()  # soltou ainda em movimento: fica a inércia
    v.mouseReleaseEvent(_evento(v, "release", ini + QPoint(100, 25), Qt.MouseButton.LeftButton))
    assert v.selecionado is None and v.vg > 0
    antes = v.guinada
    v._ultimo -= 0.03
    v._tick()
    assert v.guinada > antes  # continua girando um pouco depois de soltar


def test_roda_e_teclado(cfg, qapp, tmp_path):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QKeyEvent, QWheelEvent

    p, *_ = _painel(cfg, tmp_path)
    v = p.vista
    z = v.zoom
    v.wheelEvent(QWheelEvent(QPointF(450, 300), QPointF(450, 300), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
    assert v.zoom > z
    g = v.guinada
    v.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier))
    assert v.guinada > g
    v.ajustar()
    assert v.zoom == 1.0


def test_gira_sozinha_so_quando_ninguem_mexe(cfg, qapp, tmp_path):
    p, g, tronco, *_ = _painel(cfg, tmp_path)
    v = p.vista
    antes = v.guinada
    v._ultimo -= 0.03
    v._tick()
    assert v.guinada > antes
    v.hover = tronco.id
    antes = v.guinada
    v._ultimo -= 0.03
    v._tick()
    assert v.guinada == antes  # mouse numa ideia: para, o cartão não foge


def test_qualidade_cai_se_a_tela_ficar_lenta(cfg, qapp, tmp_path):
    p, *_ = _painel(cfg, tmp_path)
    v = p.vista
    assert v.qualidade == "medio"
    v._janela_fps = (100.0, 20)  # 20 quadros em 2,5 s = 8 quadros/s
    v._medir(102.5)
    assert v.qualidade == "baixo" and v.qualidade_caiu
    v._janela_fps = (200.0, 20)
    v._medir(202.5)
    assert v.qualidade == "baixo"  # não desce abaixo do mínimo


@pytest.mark.parametrize("qualidade", ["baixo", "medio", "alto"])
def test_renderiza_nas_tres_qualidades_com_foco(cfg, qapp, tmp_path, qualidade):
    p, g, tronco, galho, folhas = _painel(cfg, tmp_path)
    v = p.vista
    g.ligar(folhas[0].id, folhas[1].id, "par", tipo="causal")
    v.recarregar_preservando()
    v.assentar()
    v.qualidade = qualidade
    v.hover = galho.id
    assert not v.grab().toImage().isNull()
    # o meio da curva da ligação causal acha essa ligação (o teste de clique segue a curva, não a reta)
    from PySide6.QtCore import QPointF

    j = next(i for i, l in enumerate(v._arestas) if {l.a, l.b} == {folhas[0].id, folhas[1].id} and not l.hierarquia)
    a = np.array([v._sx[v._ea[j]], v._sy[v._ea[j]]])
    b = np.array([v._sx[v._eb[j]], v._sy[v._eb[j]]])
    c = np.array([v._cx_[j], v._cy_[j]])
    meio = 0.25 * a + 0.5 * c + 0.25 * b
    achada = v._ligacao_em(QPointF(*meio))
    assert achada is not None  # (com ids sorteados, outra aresta pode passar pelo mesmo ponto: qual delas varia)


def test_ideia_nova_nasce_no_nucleo_e_sai_para_a_camada(cfg, qapp, tmp_path):
    p, g, tronco, *_ = _painel(cfg, tmp_path)
    v = p.vista
    nova = g.criar_ideia("Nova", None, "manual", nivel="folha")
    v.recarregar_preservando()
    assert np.linalg.norm(v.atual[nova.id]) < 0.2
    for _ in range(80):
        v._ultimo -= 0.03
        v._tick()
    assert np.linalg.norm(v.atual[nova.id]) == pytest.approx(1.0, abs=0.02)


def test_movimento_reduzido_nao_anima(cfg, qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_MOVIMENTO_REDUZIDO", "1")
    p, g, *_ = _painel(cfg, tmp_path)
    v = p.vista
    assert v.reduzido
    nova = g.criar_ideia("Nova", None, "manual", nivel="folha")
    v.recarregar_preservando()
    assert np.linalg.norm(v.atual[nova.id]) == pytest.approx(1.0)  # vai direto, sem sair do núcleo
    antes = v.guinada
    v._tick()
    assert v.guinada == antes and not v._timer.isActive()


def test_grafo_vazio_na_orbe(cfg, qapp, tmp_path):
    from eco.chat.armazenamento import Banco
    from eco.visual.grafo import PainelGrafo

    p = PainelGrafo(cfg, Banco(tmp_path / "eco.db"), object(), confirmar=lambda t: False)
    p.resize(700, 500)
    assert not p.grab().toImage().isNull() and p.vista.nos == {}


# --- partículas de fundo -----------------------------------------------------------------------------------------
def test_ao_ouvir_as_particulas_vao_para_o_centro_e_aceleram():
    from eco.visual.fundo import CampoFundo

    calmo, ouvindo = CampoFundo("medio", semente=3), CampoFundo("medio", semente=3)
    for _ in range(60):
        qc = calmo.passo(1 / 30, "idle")
        qo = ouvindo.passo(1 / 30, "listen")
    assert ouvindo.vmul > 2 and calmo.vmul == pytest.approx(1.0)
    assert float(np.hypot(qo.x, qo.y).mean()) < float(np.hypot(qc.x, qc.y).mean())


def test_ao_falar_pulsam_com_o_volume():
    from eco.visual.fundo import CampoFundo

    baixo, alto = CampoFundo(semente=1), CampoFundo(semente=1)
    for _ in range(20):
        qb = baixo.passo(1 / 30, "speak", 0.0)
        qa = alto.passo(1 / 30, "speak", 1.0)
    assert qa.alfa.mean() > qb.alfa.mean() * 1.3 and qa.tam.mean() > qb.tam.mean()


def test_parallax_move_mais_quem_esta_perto():
    from eco.visual.fundo import CampoFundo

    a, b = CampoFundo(semente=2), CampoFundo(semente=2)
    qa = a.passo(0.0, cursor=(0.0, 0.0))
    qb = b.passo(0.0, cursor=(1.0, 0.0))
    desloc = np.abs(qa.x - qb.x)
    perto = a.z[: a.ativas] > 0.7
    assert desloc[perto].mean() > desloc[~perto].mean()


def test_densidade_e_reducao():
    from eco.visual.fundo import DENSIDADES, CampoFundo, quadros_alvo

    c = CampoFundo("alto")
    assert len(c.passo(0.01).x) == DENSIDADES["alto"]
    assert c.reduzir() and c.densidade == "medio" and len(c.passo(0.01).x) == DENSIDADES["medio"]
    assert c.reduzir() and not c.reduzir() and c.densidade == "baixo"
    assert quadros_alvo("listen", False) > quadros_alvo("idle", True) > quadros_alvo("idle", False)


def test_config_do_fundo_e_do_grafo(cfg, monkeypatch):
    from eco.config import _nivel_qualidade

    assert cfg.fundo == "medio" and cfg.grafo_qualidade == "medio" and cfg.grafo_vista == "orbe"
    assert _nivel_qualidade("Médio", "baixo") == "medio" and _nivel_qualidade("xyz", "alto") == "alto"
    assert _nivel_qualidade("nao", "medio", desligavel=True) == "desligado"
    assert _nivel_qualidade("nao", "medio") == "medio"


def test_janela_pinta_o_fundo_e_le_o_estado_da_eco(cfg, qapp, monkeypatch):
    from PySide6.QtWidgets import QLabel

    from eco.visual.principal import JanelaPrincipal

    j = JanelaPrincipal(cfg, chat=QLabel("chat"))
    j.resize(900, 600)
    assert j.campo is not None and j.quadro_fundo is not None
    pedidos = []
    j.estado_eco = lambda: pedidos.append(1) or ("listen", 0.0)
    j.show()
    j._passo_fundo()
    assert pedidos and j.campo.atracao > 0
    assert not j.grab().toImage().isNull()
    j.hide()
    assert not j.relogio_fundo.isActive()


def test_fundo_desligado(cfg, qapp, monkeypatch):
    monkeypatch.setenv("ECO_FUNDO", "desligado")
    from PySide6.QtWidgets import QLabel

    from eco.config import carregar_config
    from eco.visual.principal import JanelaPrincipal

    j = JanelaPrincipal(carregar_config(), chat=QLabel("chat"))
    j.resize(800, 500)
    assert j.campo is None and not j.grab().toImage().isNull()
    j._ligar_fundo()
    assert not j.relogio_fundo.isActive()
