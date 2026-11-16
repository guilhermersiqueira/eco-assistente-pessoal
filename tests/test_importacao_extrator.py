"""Extração de fatos das conversas importadas (M4), com o modelo simulado: nenhuma chamada real à API."""

from __future__ import annotations

import json

import pytest

from eco.chat.armazenamento import Banco
from eco.grafo.analise import Resposta
from eco.importacao import extrator, filtro
from eco.importacao.estado import Importacoes
from eco.importacao.extrator import Extrator, interpretar
from eco.importacao.modelo import ASSISTENTE, DONO, Conversa, Mensagem

MODELO = "claude-haiku-5-5"
FRASE = "Estou montando uma landing page para a padaria do meu tio em Vila Mariana"


class Modelo:
    """Devolve as respostas na ordem (texto, dict ou exceção) e guarda o que recebeu."""

    def __init__(self, *respostas, custo=0.001):
        self.respostas, self.custo, self.chamadas = list(respostas), custo, []

    def completar(self, sistema, usuario):
        self.chamadas.append((sistema, usuario))
        r = self.respostas.pop(0) if self.respostas else {"fatos": []}
        if isinstance(r, Exception):
            raise r
        return Resposta(r if isinstance(r, str) else json.dumps(r), self.custo)


def _fatos(*itens):
    return {"fatos": [{"texto": t, "trecho": tr} for t, tr in itens]}


def _conversa(uuid="c1", titulo="Landing da padaria", n=5, atualizada="2026-10-01", frase=FRASE, tamanho=100):
    msgs = [Mensagem(DONO, f"{frase}. Detalhe {i} " + "x" * tamanho) for i in range(n)]
    return Conversa(uuid, titulo, "2026-09-30T09:00:00Z", atualizada,
                    tuple(m for d in msgs for m in (d, Mensagem(ASSISTENTE, "Certo, vamos lá."))))


@pytest.fixture
def estado(tmp_path):
    return Importacoes(Banco(tmp_path / "eco.db"))


def _extrator(estado, modelo, teto=0.0, lembrancas=None):
    return Extrator(modelo, estado, MODELO, teto, lembrancas)


# --- interpretar -------------------------------------------------------------------------------------------------------
PARTE = f"Conversa: Landing (2026-09-30)\nDono: {FRASE}\nAssistente: Certo."


def test_interpretar_json_limpo_com_trecho_que_confere():
    [f] = interpretar(json.dumps(_fatos(("O dono faz a landing page da padaria do tio.", FRASE))), PARTE)
    assert f.texto == "O dono faz a landing page da padaria do tio." and f.trecho == FRASE


def test_interpretar_aceita_cerca_de_codigo_e_texto_em_volta():
    resposta = "Aqui está:\n```json\n" + json.dumps(_fatos(("O dono mora em Vila Mariana.", ""))) + "\n```\nPronto."
    assert [f.texto for f in interpretar(resposta)] == ["O dono mora em Vila Mariana."]


def test_trecho_que_nao_aparece_no_que_o_dono_escreveu_fica_vazio():
    [f] = interpretar(json.dumps(_fatos(("O dono faz a landing page da padaria.", "Eu amo viajar de trem pela Europa"))), PARTE)
    assert f.trecho == ""
    [g] = interpretar(json.dumps(_fatos(("O dono faz a landing page da padaria.", "Certo."))), PARTE)  # só a assistente disse
    assert g.trecho == ""


def test_trecho_quase_literal_ainda_confere():
    quase = "estou montando uma landing page pra padaria do meu tio em vila mariana"
    [f] = interpretar(json.dumps(_fatos(("O dono faz a landing page da padaria.", quase))), PARTE)
    assert f.trecho == quase


def test_interpretar_descarta_vazio_curto_longo_e_repetido():
    dados = _fatos(("", "x"), ("curto", ""), ("a" * 400, ""), ("O dono gosta de café.", ""), ("o dono gosta de CAFÉ", ""))
    assert [f.texto for f in interpretar(json.dumps(dados))] == ["O dono gosta de café."]


def test_interpretar_limita_a_quantidade_por_parte():
    dados = _fatos(*[(f"Fato número {i} sobre o dono.", "") for i in range(20)])
    assert len(interpretar(json.dumps(dados))) == extrator.MAX_FATOS_POR_PARTE


def test_interpretar_aceita_lista_de_textos_e_lista_vazia():
    assert [f.texto for f in interpretar(json.dumps({"fatos": ["O dono tem um gato chamado Tom."]}))] == ["O dono tem um gato chamado Tom."]
    assert interpretar('{"fatos": []}') == [] and interpretar('{"outra": 1}') == []


def test_interpretar_resposta_que_nao_e_json_da_erro():
    with pytest.raises(ValueError):
        interpretar("não encontrei nada")
    with pytest.raises(ValueError):
        interpretar("{ quebrado")


def test_prompt_exclui_faculdade_terceiros_e_o_que_ja_consta():
    p = extrator.PROMPT
    assert "faculdade" in p and "terceiros" in p and "LEMBRANÇAS ATUAIS" in p and "@MAX@" not in p
    assert str(extrator.MAX_FATOS_POR_PARTE) in p


# --- executar ----------------------------------------------------------------------------------------------------------
def test_fatos_viram_propostas_ligadas_a_conversa(estado):
    modelo = Modelo(_fatos(("O dono faz a landing page da padaria do tio.", FRASE), ("O dono mora em Vila Mariana.", "")))
    r = _extrator(estado, modelo).executar([_conversa()])
    assert (r.analisadas, r.com_fatos, r.sem_fatos, r.propostas, r.repetidas) == (1, 1, 0, 2, 0)
    assert r.custo == pytest.approx(0.001) and not r.erros
    ps = estado.propostas()
    assert [p.texto for p in ps] == ["O dono faz a landing page da padaria do tio.", "O dono mora em Vila Mariana."]
    assert ps[0].conversa_titulo == "Landing da padaria" and ps[0].trecho == FRASE and ps[1].trecho == ""
    assert estado.conversa("c1").estado == "analisada" and estado.conversa("c1").custo == pytest.approx(0.001)
    assert "2 fato(s)" in r.frase()


def test_conversa_sem_fatos_fica_marcada_e_nao_e_reanalisada(estado):
    modelo = Modelo({"fatos": []})
    r = _extrator(estado, modelo).executar([_conversa()])
    assert (r.sem_fatos, r.propostas) == (1, 0) and estado.conversa("c1").estado == "sem_fatos"
    assert filtro.planejar([_conversa()], estado, MODELO, 2.0).ja_feitas == 1


def test_o_modelo_recebe_as_lembrancas_atuais_e_o_texto_da_conversa(estado):
    modelo = Modelo()
    _extrator(estado, modelo, lembrancas=lambda: ["Vitor cursa a USP.", "Vitor mora em Curitiba."]).executar([_conversa()])
    [(sistema, usuario)] = modelo.chamadas
    assert sistema == extrator.PROMPT
    assert "- Vitor cursa a USP." in usuario and "CONVERSA:\nConversa: Landing da padaria (2026-09-30)" in usuario
    assert FRASE in usuario


def test_sem_lembrancas_o_prompt_diz_nenhuma(estado):
    modelo = Modelo()
    _extrator(estado, modelo).executar([_conversa()])
    assert "(nenhuma)" in modelo.chamadas[0][1]


def test_lembrancas_no_prompt_sao_limitadas(estado):
    modelo = Modelo()
    muitas = [f"Lembrança {i} " + "z" * 500 for i in range(200)]
    _extrator(estado, modelo, lembrancas=lambda: muitas).executar([_conversa()])
    usuario = modelo.chamadas[0][1]
    assert usuario.count("\n- Lembrança") + usuario.startswith("- Lembrança") <= extrator.MAX_LEMBRANCAS_NO_PROMPT
    assert "z" * (extrator.MAX_CARACTERES_LEMBRANCA + 20) not in usuario.split("CONVERSA:")[0]


def test_fato_repetido_em_outra_conversa_nao_duplica(estado):
    modelo = Modelo(_fatos(("O dono gosta de café.", "")), _fatos(("O dono gosta de café.", ""), ("O dono tem um gato.", "")))
    r = _extrator(estado, modelo).executar([_conversa("a", atualizada="2026-02-01"), _conversa("b", titulo="Outra", atualizada="2026-01-01")])
    assert r.propostas == 2 and r.repetidas == 1
    assert sorted(p.texto for p in estado.propostas()) == ["O dono gosta de café.", "O dono tem um gato."]


def test_fato_rejeitado_nao_volta_numa_nova_importacao(estado):
    _extrator(estado, Modelo(_fatos(("O dono gosta de café.", "")))).executar([_conversa("a")])
    estado.decidir(estado.propostas()[0].id, "rejeitada")
    r = _extrator(estado, Modelo(_fatos(("O dono gosta de café.", "")))).executar([_conversa("b", titulo="Outra")])
    assert r.propostas == 0 and r.repetidas == 1 and estado.propostas("pendente") == []


def test_erro_numa_conversa_nao_derruba_as_outras_e_fica_para_retomar(estado):
    modelo = Modelo(ConnectionError("sem rede"), _fatos(("O dono tem um gato chamado Tom.", "")))
    r = _extrator(estado, modelo).executar([_conversa("a", atualizada="2026-02-01"), _conversa("b", titulo="B", atualizada="2026-01-01")])
    assert r.analisadas == 1 and len(r.erros) == 1 and "sem rede" in r.erros[0] and "erro" in r.frase()
    assert estado.conversa("a").estado == "erro" and "ConnectionError" in estado.conversa("a").erro
    assert estado.conversa("b").estado == "analisada"
    # retomar: só a que deu erro volta a ser analisada
    plano = filtro.planejar([_conversa("a", atualizada="2026-02-01"), _conversa("b", titulo="B", atualizada="2026-01-01")], estado, MODELO, 2.0)
    assert [c.uuid for c in plano.analisar] == ["a"] and plano.ja_feitas == 1
    r2 = _extrator(estado, Modelo(_fatos(("O dono mora em Vila Mariana.", "")))).executar(plano.analisar)
    assert r2.propostas == 1 and estado.conversa("a").estado == "analisada"


def test_resposta_que_nao_e_json_duas_vezes_conta_como_erro_da_conversa(estado):
    modelo = Modelo("não sei", '{"fatos": [ quebrado')
    r = _extrator(estado, modelo).executar([_conversa()])
    assert r.analisadas == 0 and len(r.erros) == 1 and estado.conversa("c1").estado == "erro" and estado.propostas() == []
    assert len(modelo.chamadas) == 2 and r.custo == pytest.approx(0.002)  # as duas tentativas foram cobradas


def test_json_torto_na_primeira_vez_e_refeito_uma_vez(estado):
    torto = '{"fatos": [{"texto": "Vitor disse "oi" para o gato.", "trecho": ""}]}'  # aspas soltas dentro do texto
    modelo = Modelo(torto, _fatos(("Vitor tem um gato chamado Tom.", "")))
    r = _extrator(estado, modelo).executar([_conversa()])
    assert not r.erros and r.propostas == 1 and len(modelo.chamadas) == 2 and r.custo == pytest.approx(0.002)
    assert estado.conversa("c1").estado == "analisada"


def test_erro_de_rede_nao_e_repetido_dentro_da_mesma_conversa(estado):
    modelo = Modelo(ConnectionError("sem rede"), _fatos(("Vitor tem um gato.", "")))
    r = _extrator(estado, modelo).executar([_conversa()])
    assert len(modelo.chamadas) == 1 and len(r.erros) == 1  # só JSON ruim ganha segunda tentativa


def test_para_depois_de_tres_erros_seguidos(estado):
    modelo = Modelo(*[ConnectionError("fora do ar")] * 10)
    conversas = [_conversa(f"c{i}", titulo=f"C{i}", atualizada=f"2026-0{i}-01") for i in range(1, 6)]
    r = _extrator(estado, modelo).executar(conversas)
    assert len(modelo.chamadas) == extrator.MAX_ERROS_SEGUIDOS and r.parou_por_erros and r.restantes == 2
    assert estado.conversa("c4") is None  # as que não chegaram a ser tentadas nem foram registradas


def test_sucesso_zera_a_contagem_de_erros_seguidos(estado):
    modelo = Modelo(ConnectionError("x"), ConnectionError("x"), _fatos(("O dono tem um gato.", "")), ConnectionError("x"), ConnectionError("x"))
    conversas = [_conversa(f"c{i}", titulo=f"C{i}") for i in range(5)]
    r = _extrator(estado, modelo).executar(conversas)
    assert not r.parou_por_erros and len(modelo.chamadas) == 5


def test_teto_de_custo_para_e_deixa_o_resto_para_depois(estado):
    conversas = [_conversa(f"c{i}", titulo=f"C{i}", atualizada=f"2026-0{i}-01", tamanho=1500) for i in range(1, 5)]
    assert len(filtro.dividir(conversas[0])) == 1  # uma chamada por conversa, para a conta do teste ficar simples
    uma = filtro.estimar(filtro.dividir(conversas[0]), MODELO).custo_usd
    modelo = Modelo(custo=uma)
    r = _extrator(estado, modelo, teto=uma * 2.5).executar(conversas)
    assert len(modelo.chamadas) == 2 and r.parou_por_teto and r.restantes == 2 and "teto" in r.frase()
    assert estado.conversa("c2").estado == "sem_fatos"
    assert estado.conversa("c3") is None  # a primeira que não coube nem foi registrada: volta como "nova" na próxima vez


def test_interromper_pelo_dono_entre_uma_conversa_e_outra(estado):
    modelo = Modelo()
    pedidos = iter([False, True])
    r = _extrator(estado, modelo).executar([_conversa("a"), _conversa("b", titulo="B")], parar=lambda: next(pedidos))
    assert r.interrompido and r.restantes == 1 and len(modelo.chamadas) == 1 and estado.conversa("b") is None


def test_progresso_avisa_a_cada_conversa_inclusive_com_erro(estado):
    avisos = []
    modelo = Modelo(ConnectionError("x"), _fatos(("O dono tem um gato.", "")))
    _extrator(estado, modelo).executar([_conversa("a"), _conversa("b", titulo="B")],
                                       ao_progresso=lambda n, total, titulo, r: avisos.append((n, total, titulo)))
    assert avisos == [(1, 2, "Landing da padaria"), (2, 2, "B")]


def test_conversa_grande_manda_cada_parte_e_junta_os_fatos(estado):
    c = _conversa(n=40, tamanho=500)
    modelo = Modelo(_fatos(("O dono tem um gato.", "")), _fatos(("O dono tem um gato.", ""), ("O dono mora em Vila Mariana.", "")))
    r = _extrator(estado, modelo).executar([c])
    assert len(modelo.chamadas) == 2 and r.propostas == 2 and r.custo == pytest.approx(0.002)


def test_falha_numa_parte_nao_deixa_proposta_pela_metade(estado):
    c = _conversa(n=40, tamanho=500)
    modelo = Modelo(_fatos(("O dono tem um gato.", "")), ConnectionError("caiu"))
    r = _extrator(estado, modelo).executar([c])
    assert len(r.erros) == 1 and estado.propostas() == [] and estado.conversa("c1").estado == "erro"
    assert estado.conversa("c1").custo == pytest.approx(0.001)  # o que já foi gasto na primeira parte fica registrado


def test_conversa_sem_texto_nao_chama_o_modelo(estado):
    modelo = Modelo()
    vazia = Conversa("v", "Vazia", "", "", (Mensagem(DONO, "  "),))
    r = _extrator(estado, modelo).executar([vazia])
    assert modelo.chamadas == [] and estado.conversa("v").estado == "ignorada" and r.analisadas == 0


def test_lembrancas_da_memoria_lista_os_textos():
    class Mem:
        def fatos(self):
            return [type("F", (), {"texto": "A."})(), type("F", (), {"texto": "B."})()]

    assert extrator.lembrancas_da_memoria(Mem()) == ["A.", "B."]


def test_categoria_de_uso_da_importacao_existe_e_fica_fora_do_teto_do_grafo():
    from eco import uso

    assert uso.IMPORTACAO == "importacao" and uso.IMPORTACAO not in uso.GRAFO_CATEGORIAS


# --- duração, validade e contexto dos já propostos (ajustes depois do ensaio real de 15/11/2026) -------------------------------
def _com_duracao(texto, duracao, validade=""):
    return json.dumps({"fatos": [{"texto": texto, "trecho": "", "duracao": duracao, "validade": validade}]})


def test_duracao_e_validade_sao_lidas_e_validadas():
    [a] = interpretar(_com_duracao("Vitor tem prova na semana que vem.", "temporária", "2026-11-17"))
    assert (a.duracao, a.validade) == ("temporaria", "2026-11-17")  # aceita o acento
    [b] = interpretar(_com_duracao("Vitor mora em Curitiba.", "duradoura", "2026-11-17"))
    assert (b.duracao, b.validade) == ("duradoura", "")  # validade só vale para fato temporário
    [c] = interpretar(_com_duracao("Vitor tem um irmão chamado Rafael.", "para sempre"))
    assert (c.duracao, c.validade) == ("", "")  # duração inventada é descartada
    [d] = interpretar(_com_duracao("Vitor está num processo seletivo.", "temporaria", "17/11/2026"))
    assert (d.duracao, d.validade) == ("temporaria", "")  # data fora do formato é descartada, o fato fica


def test_prompt_pede_duracao_agrupar_e_o_mesmo_sujeito():
    p = extrator.PROMPT
    for trecho in ("duracao", "temporaria", "JUNTE", "MESMO sujeito", "Não deduza", "requisitos", "JÁ PROPOSTOS"):
        assert trecho in p


def test_duracao_e_validade_chegam_a_proposta(estado):
    modelo = Modelo({"fatos": [{"texto": "Vitor está num processo seletivo na Braskem.", "trecho": "", "duracao": "temporaria",
                                 "validade": "2026-12-01"}]})
    _extrator(estado, modelo).executar([_conversa()])
    [p] = estado.propostas()
    assert (p.duracao, p.validade) == ("temporaria", "2026-12-01")


def test_o_modelo_recebe_o_que_ja_foi_proposto_nesta_importacao(estado):
    modelo = Modelo(_fatos(("Vitor está num processo seletivo na Braskem.", "")), _fatos(("Vitor gosta de café.", "")))
    _extrator(estado, modelo).executar([_conversa("a"), _conversa("b", titulo="Outra")])
    primeira, segunda = modelo.chamadas[0][1], modelo.chamadas[1][1]
    assert "JÁ PROPOSTOS:\n(nenhuma)" in primeira
    assert "JÁ PROPOSTOS:\n- Vitor está num processo seletivo na Braskem." in segunda


def test_o_que_o_dono_rejeitou_ou_deixou_pendente_antes_tambem_vai_no_contexto(estado):
    _extrator(estado, Modelo(_fatos(("Vitor gosta de café.", ""), ("Vitor tem um gato.", "")))).executar([_conversa("a")])
    rejeitar = next(p for p in estado.propostas() if "café" in p.texto)
    estado.decidir(rejeitar.id, "rejeitada")
    modelo = Modelo()
    _extrator(estado, modelo).executar([_conversa("b", titulo="Outra")])
    contexto = modelo.chamadas[0][1].split("JÁ PROPOSTOS:")[1].split("CONVERSA:")[0]
    assert "Vitor gosta de café." in contexto and "Vitor tem um gato." in contexto
