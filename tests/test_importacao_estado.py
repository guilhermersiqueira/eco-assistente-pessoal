"""Estado da importação de conversas (M2): tabelas, situação de cada conversa, retomada e propostas sem repetição."""

from __future__ import annotations

import pytest

from eco.chat.armazenamento import Banco
from eco.importacao.estado import Importacoes, chave_do_fato
from eco.importacao.modelo import ASSISTENTE, DONO, Conversa, Mensagem


def _conversa(uuid="c1", titulo="Landing da padaria", texto="Quero fazer uma landing page", atualizada="2026-10-01T10:00:00Z"):
    return Conversa(uuid, titulo, "2026-09-30T09:00:00Z", atualizada,
                    (Mensagem(DONO, texto), Mensagem(ASSISTENTE, "Claro, vamos começar.")))


@pytest.fixture
def estado(tmp_path):
    return Importacoes(Banco(tmp_path / "eco.db"))


def test_criar_as_tabelas_duas_vezes_nao_apaga_nada(tmp_path):
    arquivo = tmp_path / "eco.db"
    e = Importacoes(Banco(arquivo))
    e.registrar_conversa(_conversa())
    e.banco.fechar()
    reaberto = Importacoes(Banco(arquivo))  # a Eco reabriu: o estado continua lá (retomada)
    assert reaberto.conversa("c1").estado == "pendente"
    assert [r.uuid for r in reaberto.pendentes()] == ["c1"]


def test_situacao_nova_feita_mudou_e_retomar(estado):
    c = _conversa()
    assert estado.situacao(c.uuid, c.hash) == "nova"
    assert estado.registrar_conversa(c) == "nova"
    assert estado.situacao(c.uuid, c.hash) == "retomar"  # registrada, mas ainda não analisada
    estado.definir_estado(c.uuid, "analisada", custo=0.002)
    assert estado.situacao(c.uuid, c.hash) == "feita"
    nova_versao = _conversa(texto="Quero fazer uma landing page e um cardápio")  # chegou mensagem ou mudou o texto
    assert estado.situacao(c.uuid, nova_versao.hash) == "mudou"


def test_registrar_de_novo_nao_desfaz_o_que_ja_foi_feito(estado):
    c = _conversa()
    estado.registrar_conversa(c)
    estado.definir_estado(c.uuid, "analisada")
    assert estado.registrar_conversa(c) == "feita"
    assert estado.conversa(c.uuid).estado == "analisada"


def test_conversa_que_mudou_volta_para_pendente_e_limpa_o_erro(estado):
    c = _conversa()
    estado.registrar_conversa(c)
    estado.definir_estado(c.uuid, "erro", erro="sem rede")
    assert estado.conversa(c.uuid).erro == "sem rede" and estado.situacao(c.uuid, c.hash) == "retomar"
    estado.definir_estado(c.uuid, "analisada")
    mudou = _conversa(texto="Outro texto agora", titulo="Título novo")
    assert estado.registrar_conversa(mudou) == "mudou"
    r = estado.conversa(c.uuid)
    assert (r.estado, r.erro, r.titulo, r.hash) == ("pendente", "", "Título novo", mudou.hash)


def test_pendentes_incluem_erro_da_mais_recente_para_a_mais_antiga(estado):
    estado.registrar_conversa(_conversa("a", atualizada="2026-01-01T00:00:00Z"))
    estado.registrar_conversa(_conversa("b", atualizada="2026-03-01T00:00:00Z", texto="b"))
    estado.registrar_conversa(_conversa("c", atualizada="2026-02-01T00:00:00Z", texto="c"))
    estado.definir_estado("c", "erro", erro="falhou")
    estado.definir_estado("a", "sem_fatos")
    assert [r.uuid for r in estado.pendentes()] == ["b", "c"]
    n = estado.contagens()
    assert (n["pendente"], n["erro"], n["sem_fatos"], n["analisada"]) == (1, 1, 1, 0)


def test_estado_invalido_e_conversa_desconhecida_dao_erro(estado):
    estado.registrar_conversa(_conversa())
    with pytest.raises(ValueError):
        estado.definir_estado("c1", "inventado")
    with pytest.raises(ValueError):
        estado.registrar_conversa(_conversa("x"), estado="inventado")
    with pytest.raises(ValueError):
        estado.definir_estado("nao-existe", "analisada")


def test_custo_soma_as_tentativas(estado):
    estado.registrar_conversa(_conversa())
    estado.definir_estado("c1", "erro", custo=0.001, erro="x")
    estado.definir_estado("c1", "analisada", custo=0.002)
    assert estado.conversa("c1").custo == pytest.approx(0.003)
    assert estado.custo_total() == pytest.approx(0.003)


def test_importacao_registrada_com_o_arquivo(estado):
    id_ = estado.registrar_importacao("data-2026.zip", "abc123", 42)
    estado.registrar_conversa(_conversa(), importacao_id=id_)
    [i] = estado.importacoes()
    assert (i["arquivo"], i["hash"], i["conversas"]) == ("data-2026.zip", "abc123", 42)


# --- propostas ---------------------------------------------------------------------------------------------------------
def test_fato_proposto_nao_se_repete_nem_com_outras_letras(estado):
    estado.registrar_conversa(_conversa())
    estado.registrar_conversa(_conversa("c2", texto="outra"))
    primeiro = estado.propor("c1", "Vitor está fazendo a landing page da padaria.", "Quero fazer uma landing page")
    assert primeiro
    assert estado.propor("c2", "  vitor está fazendo a LANDING page da padaria  ") is None  # outra conversa, mesmo fato
    assert chave_do_fato("Está à venda.") == chave_do_fato("esta a venda")


def test_fato_rejeitado_nao_volta(estado):
    estado.registrar_conversa(_conversa())
    id_ = estado.propor("c1", "Gosta de café.")
    estado.decidir(id_, "rejeitada", "não quero guardar isso")
    assert estado.propor("c1", "Gosta de café.") is None
    assert estado.propostas("pendente") == [] and [p.texto for p in estado.propostas("rejeitada")] == ["Gosta de café."]


def test_proposta_traz_a_conversa_de_origem(estado):
    estado.registrar_conversa(_conversa())
    id_ = estado.propor("c1", "Faz a landing page da padaria.", "Quero fazer uma landing page")
    [p] = estado.propostas()
    assert (p.id, p.conversa_titulo, p.conversa_data, p.trecho) == (id_, "Landing da padaria", "2026-09-30T09:00:00Z", "Quero fazer uma landing page")
    estado.decidir(id_, "aprovada", ideia_id="ideia-1")
    assert estado.proposta(id_).estado == "aprovada" and estado.proposta(id_).ideia_id == "ideia-1" and estado.proposta(id_).decidido_em


def test_aprovacao_parcial_so_muda_o_item_decidido(estado):
    estado.registrar_conversa(_conversa())
    a, b, c = (estado.propor("c1", t) for t in ("Fato A.", "Fato B.", "Fato C."))
    estado.decidir(a, "aprovada")
    estado.decidir(b, "rejeitada")
    assert [p.texto for p in estado.propostas()] == ["Fato C."]
    assert estado.propostas("aprovada")[0].texto == "Fato A."


def test_propor_vazio_ou_de_conversa_desconhecida_da_erro(estado):
    estado.registrar_conversa(_conversa())
    with pytest.raises(ValueError):
        estado.propor("c1", "   ")
    with pytest.raises(ValueError):
        estado.propor("nao-existe", "Um fato.")


def test_decidir_exige_decisao_valida_e_proposta_existente(estado):
    estado.registrar_conversa(_conversa())
    id_ = estado.propor("c1", "Um fato.")
    with pytest.raises(ValueError):
        estado.decidir(id_, "pendente")
    with pytest.raises(ValueError):
        estado.decidir("nao-existe", "aprovada")


def test_editar_texto_so_em_proposta_pendente_e_sem_colidir(estado):
    estado.registrar_conversa(_conversa())
    a, b = estado.propor("c1", "Fato A."), estado.propor("c1", "Fato B.")
    estado.editar_texto(a, "Fato A, corrigido.")
    assert estado.proposta(a).texto == "Fato A, corrigido."
    assert estado.propor("c1", "fato a, corrigido") is None  # a chave acompanhou a edição
    with pytest.raises(ValueError):
        estado.editar_texto(b, "Fato A, corrigido.")  # já existe outra igual
    with pytest.raises(ValueError):
        estado.editar_texto(b, " ")
    estado.decidir(b, "rejeitada")
    with pytest.raises(ValueError):
        estado.editar_texto(b, "Outro texto.")


def test_hash_da_conversa_ignora_o_titulo_mas_nao_o_texto():
    base = _conversa()
    assert base.hash == _conversa(titulo="Outro título").hash
    assert base.hash != _conversa(texto="Texto diferente").hash


def test_duracao_e_validade_ficam_na_proposta_e_valores_ruins_viram_vazio(estado):
    estado.registrar_conversa(_conversa())
    a = estado.propor("c1", "Fato temporário A.", duracao="temporaria", validade="2026-12-01")
    b = estado.propor("c1", "Fato B com valores ruins.", duracao="eterna", validade="amanhã")
    c = estado.propor("c1", "Fato C sem nada.")
    assert (estado.proposta(a).duracao, estado.proposta(a).validade) == ("temporaria", "2026-12-01")
    assert (estado.proposta(b).duracao, estado.proposta(b).validade) == ("", "")
    assert (estado.proposta(c).duracao, estado.proposta(c).validade) == ("", "")
