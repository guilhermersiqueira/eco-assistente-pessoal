"""Importação de ponta a ponta (M7, sem Qt): export sintético em disco, Haiku simulado, memória e backup temporários."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from eco.chat.armazenamento import Banco
from eco.grafo import semelhanca as sem
from eco.grafo.analise import Resposta
from eco.grafo.dados import Grafo
from eco.grafo.memorias import Memorias
from eco.importacao.servico import Importador

sys.path.insert(0, str(Path(__file__).parent))
from test_semelhanca import CodificadorFalso  # noqa: E402

FATO = "Vitor está montando uma landing page para a padaria do tio."


def _conversa(uuid, titulo, dia):
    msgs = []
    for i in range(5):
        for sender, texto in (("human", f"Estou montando uma landing page para a padaria do meu tio, parte {i}. " + "x" * 80),
                              ("assistant", "Certo, vamos lá.")):
            msgs.append({"uuid": f"{uuid}-{i}-{sender}", "text": texto, "sender": sender, "created_at": f"2026-10-{dia:02d}T10:0{i}:00Z",
                         "updated_at": "", "content": [], "attachments": [], "files": []})
    return {"uuid": uuid, "name": titulo, "summary": "", "created_at": f"2026-10-{dia:02d}T09:00:00Z",
            "updated_at": f"2026-10-{dia:02d}T11:00:00Z", "chat_messages": msgs}


class Haiku:
    """Serve extração e conferência: devolve sempre os fatos da vez e nenhuma relação."""

    def __init__(self, fatos=None, custo=0.001, erro=None):
        self.fatos, self.custo, self.erro, self.chamadas = fatos if fatos is not None else [], custo, erro, 0

    def completar(self, sistema, usuario):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return Resposta(json.dumps({"fatos": [{"texto": t, "trecho": ""} for t in self.fatos], "itens": []}), self.custo)


@pytest.fixture
def downloads(tmp_path):
    pasta = tmp_path / "Downloads"
    pasta.mkdir()
    return pasta


def _export(pasta, n=3):
    with zipfile.ZipFile(pasta / "conversations-000.zip", "w") as z:
        z.writestr("conversations.json", json.dumps([_conversa(f"c{i}", f"Conversa {i}", i + 1) for i in range(n)]))


def _importador(tmp_path, downloads, haiku, copias=None, teto=2.0):
    pasta = tmp_path / "dados"
    pasta.mkdir(exist_ok=True)
    banco = Banco(tmp_path / "eco.db")
    grafo = Grafo(banco)
    mem = Memorias(grafo, pasta)
    mem.inicializar()
    cfg = SimpleNamespace(modelo_grafo="claude-haiku-5-5", importar_teto_usd=teto)
    return Importador(cfg, banco, grafo, mem, pastas=[downloads], completador_fabrica=lambda cat, tokens: haiku,
                      semelhanca_fabrica=lambda: sem.Semelhanca(grafo, CodificadorFalso()),
                      antes_de_gravar=(lambda: copias.append(1)) if copias is not None else None), mem


def test_sem_export_a_previa_explica_o_que_fazer(tmp_path, downloads):
    imp, _ = _importador(tmp_path, downloads, Haiku())
    p = imp.preparar()
    assert not p.pode_importar and "Exportar dados" in p.frase()


def test_preparar_nao_gasta_nem_grava(tmp_path, downloads):
    _export(downloads)
    haiku = Haiku([FATO])
    imp, mem = _importador(tmp_path, downloads, haiku)
    p = imp.preparar()
    assert p.pode_importar and len(p.plano.analisar) == 3 and "US$" in p.frase()
    assert haiku.chamadas == 0 and imp.estado.propostas() == [] and imp.estado.importacoes() == []


def test_importar_gera_propostas_para_revisao_sem_tocar_na_memoria(tmp_path, downloads):
    _export(downloads)
    imp, mem = _importador(tmp_path, downloads, Haiku([FATO]))
    antes = len(mem.nos())
    imp.preparar()
    avisos = []
    texto = imp.importar(ao_progresso=avisos.append)
    assert "fato(s)" in texto and any("Lendo conversa" in a for a in avisos) and any("Conferindo" in a for a in avisos)
    [p] = imp.propostas()  # o mesmo fato nas 3 conversas vira uma proposta só
    assert p.texto == FATO and len(mem.nos()) == antes


def test_ensaio_limita_as_conversas_mais_recentes(tmp_path, downloads):
    _export(downloads, 4)
    haiku = Haiku([])
    imp, _ = _importador(tmp_path, downloads, haiku)
    imp.preparar()
    imp.importar(max_conversas=2)
    assert imp.estado.contagens()["sem_fatos"] == 2
    assert imp.estado.conversa("c3").estado == "sem_fatos" and imp.estado.conversa("c0") is None  # as 2 mais recentes
    p = imp.preparar()  # o resto continua disponível
    assert len(p.plano.analisar) == 2 and p.plano.ja_feitas == 2


def test_importar_de_novo_nao_repete_o_que_ja_foi_feito(tmp_path, downloads):
    _export(downloads)
    haiku = Haiku([FATO])
    imp, _ = _importador(tmp_path, downloads, haiku)
    imp.preparar()
    imp.importar()
    chamadas = haiku.chamadas
    p = imp.preparar()
    assert not p.pode_importar and p.plano.ja_feitas == 3
    assert "Nada novo" in imp.importar() and haiku.chamadas == chamadas


def test_erro_de_rede_deixa_tudo_para_retomar(tmp_path, downloads):
    _export(downloads)
    imp, _ = _importador(tmp_path, downloads, Haiku(erro=ConnectionError("sem rede")))
    imp.preparar()
    texto = imp.importar()
    assert "erro" in texto and imp.propostas() == []
    assert imp.preparar().pode_importar  # as que deram erro voltam no plano


def test_parar_interrompe_e_nao_confere(tmp_path, downloads):
    _export(downloads)
    haiku = Haiku([FATO])
    imp, _ = _importador(tmp_path, downloads, haiku)
    imp.preparar()
    texto = imp.importar(parar=lambda: True)
    assert "nterromp" in texto and haiku.chamadas == 0


def test_aprovar_pelo_servico_guarda_com_copia_e_rejeitar_nao(tmp_path, downloads):
    _export(downloads)
    copias = []
    imp, mem = _importador(tmp_path, downloads, Haiku([FATO, "Vitor mora em Vila Mariana."]), copias)
    imp.preparar()
    imp.importar()
    a, b = imp.propostas()
    imp.aprovar(a.id)
    imp.rejeitar(b.id)
    assert [i.resumo for i in mem.importadas()] == [a.texto] and copias == [1]
    assert imp.propostas() == []
    assert imp.estado.proposta(b.id).estado == "rejeitada"


def test_arquivo_ilegivel_vira_mensagem_e_nao_excecao(tmp_path, downloads):
    (downloads / "conversations-000.zip").write_bytes(b"isto nao e um zip")
    imp, _ = _importador(tmp_path, downloads, Haiku())
    p = imp.preparar()
    assert p.erro and not p.pode_importar


def _memories(pasta, itens=("- [stated] Tem um gato chamado Mel [s:ab12]",)):
    conteudo = "---\nname: gato\ndescription: O gato do dono\n---\n" + "\n".join(itens)
    dados = {"memory_files": [{"path": "/areas/gato.md", "content": conteudo, "updated_at": "2026-11-15T10:00:00+00:00"}]}
    with zipfile.ZipFile(pasta / "memories-000.zip", "w") as z:
        z.writestr("memories/conta.json", json.dumps(dados))


def test_memoria_do_claude_entra_no_plano_e_vira_proposta_com_origem(tmp_path, downloads):
    _export(downloads, 1)
    _memories(downloads)
    imp, mem = _importador(tmp_path, downloads, Haiku(["Ana tem um gato chamado Mel."]), copias=[])
    p = imp.preparar()
    assert len(p.plano.analisar) == 2 and "memória do claude.ai" in p.frase()
    imp.importar()
    [prop] = imp.propostas()
    assert prop.conversa_uuid.startswith("memoria:") and prop.conversa_titulo == "O gato do dono"
    r = imp.aprovar(prop.id)
    assert mem.grafo.ideia(r.ideia_id).criado_em.startswith("2026-11-15")


def test_sem_memories_a_previa_avisa_para_baixar(tmp_path, downloads):
    _export(downloads, 1)
    imp, _ = _importador(tmp_path, downloads, Haiku())
    assert "memories-000.zip" in imp.preparar().frase()


def test_rotulo_curto_corta_a_descricao_longa_da_memoria():
    from eco.importacao.servico import rotulo_curto

    assert rotulo_curto("Investimentos do Vitor — aporte mensal de R$400, divisão entre reserva") == "Investimentos do Vitor"
    assert rotulo_curto("Dieta (TMB/TDEE)") == "Dieta"
    longo = rotulo_curto("Regras para decidir contestar ou descontar nos tickets de acareação da transportadora")
    assert len(longo) <= 46 and longo.endswith("…")
    assert rotulo_curto("") == "Sem título"


def test_grupos_juntam_por_origem_e_rejeitar_grupo_so_mexe_naquele(tmp_path, downloads):
    _export(downloads, 2)
    imp, mem = _importador(tmp_path, downloads, Haiku(), copias=[])
    imp.estado.registrar_conversa(imp_conversa("a", "Conversa A", "2026-10-02"))
    imp.estado.registrar_conversa(imp_conversa("b", "Investimentos do Vitor — aporte mensal", "2026-10-05"))
    a1 = imp.estado.propor("a", "Vitor tem um gato chamado Mel.")
    imp.estado.propor("b", "Vitor investe em renda fixa.")
    imp.estado.propor("b", "Vitor tem ações de bancos.")
    grupos = imp.grupos()
    assert [(g.rotulo, len(g.propostas)) for g in grupos] == [("Investimentos do Vitor", 2), ("Conversa A", 1)]
    assert imp.rejeitar_grupo("b") == 2
    assert [p.id for p in imp.propostas()] == [a1]
    assert [p.estado for p in imp.estado.propostas("rejeitada")] == ["rejeitada", "rejeitada"]


def imp_conversa(uuid, titulo, data):
    from eco.importacao.modelo import DONO, Conversa, Mensagem

    return Conversa(uuid, titulo, data + "T10:00:00Z", data + "T11:00:00Z", (Mensagem(DONO, "x"),))


def test_gasto_da_conferencia_nao_entra_no_teto_mensal_do_grafo(tmp_path, downloads):
    _export(downloads, 2)
    imp, mem = _importador(tmp_path, downloads, Haiku([FATO], custo=0.01), copias=[])
    imp.preparar()
    imp.importar()
    assert imp.estado.propostas() and mem.grafo.gasto_do_mes() == 0.0  # extração e conferência gastaram, mas fora do teto do grafo
