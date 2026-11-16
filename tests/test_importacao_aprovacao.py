"""Aprovação dos fatos importados (M6): lembrança importada fica fora do prompt, conflito exige escolha, tudo reversível."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from eco.chat.armazenamento import Banco
from eco.grafo import recuperacao
from eco.grafo import semelhanca as sem
from eco.grafo.analise import Resposta
from eco.grafo.dados import Grafo
from eco.grafo.higiene import Higiene
from eco.grafo.memorias import ARQUIVO_IMPORTADAS, Memorias
from eco.importacao.aprovacao import Aprovador, ConflitoPendente
from eco.importacao.estado import Importacoes
from eco.importacao.modelo import DONO, Conversa, Mensagem

sys.path.insert(0, str(Path(__file__).parent))
from test_semelhanca import CodificadorFalso  # noqa: E402

ANTIGA = "Vitor trabalha das 8h às 17h."
NOVA = "Vitor agora trabalha das 9h às 18h."
NOVO = "Vitor está montando uma landing page para a padaria do tio."
DATA = "2026-09-30T09:00:00Z"


class Haiku:
    def __init__(self, *respostas, custo=0.001):
        self.respostas, self.custo, self.chamadas = list(respostas), custo, []

    def completar(self, sistema, usuario):
        self.chamadas.append(usuario)
        r = self.respostas.pop(0) if self.respostas else {"itens": []}
        return Resposta(json.dumps(r), self.custo)


@pytest.fixture
def mem(tmp_path):
    pasta = tmp_path / "dados"
    pasta.mkdir()
    m = Memorias(Grafo(Banco(tmp_path / "eco.db")), pasta)
    m.inicializar()
    m.lembrar_no(ANTIGA)
    m.lembrar_no("Vitor gosta de café coado.")
    return m


@pytest.fixture
def estado(mem):
    e = Importacoes(mem.grafo.banco)
    e.registrar_conversa(Conversa("c1", "Landing da padaria", DATA, "2026-10-01", (Mensagem(DONO, "x"),)))
    return e


def _aprovador(mem, estado, haiku=None, copias=None):
    hig = Higiene(mem.grafo, haiku or Haiku(), sem.Semelhanca(mem.grafo, CodificadorFalso()), memorias=mem)
    return Aprovador(estado, mem, hig, antes_de_gravar=(lambda: copias.append(1)) if copias is not None else None)


def _propor(estado, texto=NOVO, **kw):
    return estado.propor("c1", texto, **kw)


# --- separação das importadas ------------------------------------------------------------------------------------------------
def test_importada_nao_entra_no_bloco_fixo_nem_no_memoria_md(mem):
    mem.lembrar_no(NOVO, importada=True, quando=DATA)
    assert NOVO not in [f.texto for f in mem.fatos()]
    assert NOVO in mem.fora_do_prompt()
    mem.espelhar()
    assert NOVO not in (mem.pasta / "memoria.md").read_text(encoding="utf-8")
    assert NOVO in (mem.pasta / ARQUIVO_IMPORTADAS).read_text(encoding="utf-8")
    assert [i.resumo for i in mem.importadas()] == [NOVO]


def test_importada_guarda_a_data_original(mem):
    no = mem.lembrar_no(NOVO, importada=True, quando=DATA)
    assert no.criado_em == DATA and no.topico == "importada" and not no.protegido


def test_lembranca_comum_continua_igual(mem):
    no = mem.lembrar_no("Vitor tem um gato.")
    assert no.topico != "importada" and "Vitor tem um gato." in [f.texto for f in mem.fatos()]


def test_origem_legivel_diz_que_veio_do_claude_ai(mem):
    no = mem.lembrar_no(NOVO, importada=True, quando=DATA)
    assert "claude.ai" in recuperacao.origem_legivel(mem.grafo, no)


# --- aprovar -----------------------------------------------------------------------------------------------------------------
def test_aprovar_cria_lembranca_importada_com_data_da_conversa(mem, estado):
    pid = _propor(estado, duracao="temporaria", validade="2026-12-01")
    r = _aprovador(mem, estado).aprovar(pid)
    assert not r.ja_existia and r.ideia_id
    no = mem.grafo.ideia(r.ideia_id)
    assert no.resumo == NOVO and no.topico == "importada" and no.criado_em == DATA
    p = estado.proposta(pid)
    assert p.estado == "aprovada" and p.ideia_id == r.ideia_id
    assert estado.origem_da_ideia(r.ideia_id).conversa_titulo == "Landing da padaria"
    assert any(h for h in mem.grafo.banco._consultar("SELECT * FROM historico WHERE autor = ?", ("importação do claude.ai",)))


def test_aprovar_mexe_no_espelho_so_do_arquivo_das_importadas(mem, estado):
    antes = (mem.pasta / "memoria.md").read_text(encoding="utf-8")
    _aprovador(mem, estado).aprovar(_propor(estado))
    assert (mem.pasta / "memoria.md").read_text(encoding="utf-8") == antes
    assert NOVO in (mem.pasta / ARQUIVO_IMPORTADAS).read_text(encoding="utf-8")


def test_editar_o_texto_antes_de_aprovar_guarda_a_versao_editada(mem, estado):
    pid = _propor(estado)
    r = _aprovador(mem, estado).aprovar(pid, texto="Vitor faz uma landing page para a padaria do tio.")
    assert mem.grafo.ideia(r.ideia_id).resumo == "Vitor faz uma landing page para a padaria do tio."


def test_aprovar_texto_que_ja_esta_na_memoria_nao_duplica(mem, estado):
    pid = _propor(estado, "vitor gosta de café coado")
    r = _aprovador(mem, estado).aprovar(pid)
    assert r.ja_existia and estado.proposta(pid).estado == "duplicada"
    assert len([i for i in mem.nos() if "café" in i.resumo]) == 1


def test_proposta_ja_decidida_nao_aprova_de_novo(mem, estado):
    ap = _aprovador(mem, estado)
    pid = _propor(estado)
    ap.aprovar(pid)
    with pytest.raises(ValueError):
        ap.aprovar(pid)
    with pytest.raises(ValueError):
        ap.rejeitar(pid)
    with pytest.raises(ValueError):
        ap.aprovar("inexistente")


def test_rejeitar_nao_cria_nada_e_fica_registrado(mem, estado):
    antes = len(mem.nos())
    pid = _propor(estado)
    _aprovador(mem, estado).rejeitar(pid, "não é verdade")
    assert len(mem.nos()) == antes and estado.proposta(pid).estado == "rejeitada"


# --- conflito ----------------------------------------------------------------------------------------------------------------
def _conflito(estado, pid, mem, relacao="atualiza"):
    antiga = next(i for i in mem.nos() if i.resumo == ANTIGA)
    estado.marcar_conferida(pid, antiga.id, ANTIGA, relacao, "horário mudou")
    return antiga


def test_conflito_sem_escolha_nao_grava_nada(mem, estado):
    pid = _propor(estado, NOVA)
    _conflito(estado, pid, mem)
    antes = len(mem.nos())
    with pytest.raises(ConflitoPendente) as e:
        _aprovador(mem, estado).aprovar(pid)
    assert ANTIGA in str(e.value) and len(mem.nos()) == antes and estado.proposta(pid).estado == "pendente"


def test_substituir_arquiva_a_antiga_e_liga_as_duas(mem, estado):
    pid = _propor(estado, NOVA)
    antiga = _conflito(estado, pid, mem)
    r = _aprovador(mem, estado).aprovar(pid, substituir=True)
    assert r.substituiu == ANTIGA
    assert mem.grafo.ideia(antiga.id).arquivada
    assert NOVA in [i.resumo for i in mem.importadas()] and ANTIGA not in [i.resumo for i in mem.nos()]


def test_mesmo_assim_mantem_as_duas(mem, estado):
    pid = _propor(estado, NOVA)
    antiga = _conflito(estado, pid, mem, "contradiz")
    r = _aprovador(mem, estado).aprovar(pid, mesmo_assim=True)
    assert not r.substituiu and not mem.grafo.ideia(antiga.id).arquivada
    assert {ANTIGA, NOVA} <= {i.resumo for i in mem.nos()}


def test_conflito_ja_resolvido_por_outro_caminho_nao_trava(mem, estado):
    pid = _propor(estado, NOVA)
    antiga = _conflito(estado, pid, mem)
    mem.grafo.arquivar(antiga.id, "removida à mão")
    r = _aprovador(mem, estado).aprovar(pid)
    assert r.ideia_id and estado.proposta(pid).estado == "aprovada"


def test_texto_editado_e_conferido_de_novo_na_hora_de_aprovar(mem, estado):
    pid = _propor(estado, NOVO)
    _conflito(estado, pid, mem)  # conflito da versão antiga do texto
    estado.editar_texto(pid, "Vitor tem um cachorro chamado Rex.")
    haiku = Haiku()
    r = _aprovador(mem, estado, haiku).aprovar(pid)  # texto novo, sem lembrança parecida: passa sem conflito
    assert r.ideia_id and not r.substituiu


# --- cópia de segurança ------------------------------------------------------------------------------------------------------
def test_copia_do_banco_so_na_primeira_gravacao_da_rodada(mem, estado):
    copias = []
    ap = _aprovador(mem, estado, copias=copias)
    ap.aprovar(_propor(estado, "Vitor tem um gato chamado Tom."))
    ap.aprovar(_propor(estado, "Vitor mora em Vila Mariana."))
    assert len(copias) == 1
    ap.nova_rodada()
    ap.aprovar(_propor(estado, "Vitor estuda na USP às terças."))
    assert len(copias) == 2


def test_rejeitar_e_duplicada_nao_fazem_copia(mem, estado):
    copias = []
    ap = _aprovador(mem, estado, copias=copias)
    ap.rejeitar(_propor(estado))
    ap.aprovar(_propor(estado, "Vitor gosta de café coado"))
    assert copias == []
