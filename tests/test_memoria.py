"""Memória estruturada (editar, fixar, limite), lembranças como nós do grafo, e metas com tópico."""

from __future__ import annotations

import json
import time
from datetime import date, datetime

import pytest

from eco.chat.armazenamento import Banco
from eco.config import carregar_config
from eco.ferramentas import memoria, notas
from eco.grafo import fatos as grafo_fatos
from eco.grafo.analise import Analisador, Resposta
from eco.grafo.dados import Grafo


@pytest.fixture
def cfg(tmp_path, monkeypatch):
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


@pytest.fixture
def grafo(banco):
    return Grafo(banco)


def escrever(pasta, *linhas):
    (pasta / "memoria.md").write_text("# Memória da Eco\n\n" + "\n".join(linhas) + "\n", encoding="utf-8")


# --- memória ---------------------------------------------------------------------------------------------------
def test_fatos_le_texto_data_e_fixo_e_ignora_o_resto(pasta):
    escrever(pasta, "- [fixo] O dono se chama Vitor. (09/11/2026)", "- Gosta de café", "texto solto", "- ")
    assert memoria.fatos(pasta) == [memoria.Fato("O dono se chama Vitor.", "09/11/2026", True), memoria.Fato("Gosta de café", "", False)]


def test_editar_mantem_data_e_se_e_fixo_e_preserva_o_resto_do_arquivo(pasta):
    escrever(pasta, "- [fixo] Mora em Campinas. (09/11/2026)", "- Gosta de café. (09/11/2026)")
    novo = memoria.editar(pasta, "Mora em Campinas.", "Mora em Curitiba.")
    assert novo == memoria.Fato("Mora em Curitiba.", "09/11/2026", True)
    texto = (pasta / "memoria.md").read_text(encoding="utf-8")
    assert texto.startswith("# Memória da Eco\n") and "- [fixo] Mora em Curitiba. (09/11/2026)" in texto and "Gosta de café" in texto
    with pytest.raises(ValueError, match="Não encontrei"):
        memoria.editar(pasta, "Mora em Campinas.", "x")
    with pytest.raises(ValueError, match="vazio"):
        memoria.editar(pasta, "Gosta de café.", "   ")


def test_fixar_apagar_e_por_trecho(pasta):
    escrever(pasta, "- A (01/01/2026)", "- B (01/01/2026)", "- Bola (01/01/2026)")
    assert memoria.fixar(pasta, "A").fixo is True and memoria.fatos(pasta)[0].fixo
    memoria.fixar(pasta, "A", False)
    assert not memoria.fatos(pasta)[0].fixo
    memoria.apagar(pasta, "B")
    assert [f.texto for f in memoria.fatos(pasta)] == ["A", "Bola"]
    with pytest.raises(ValueError, match="Mais de uma"):
        memoria.esquecer(pasta, "a")  # "A" e "Bola" combinam
    assert memoria.fixar_por_trecho(pasta, "bola") == "Lembrança fixada: nunca será cortada."
    assert memoria.editar_por_trecho(pasta, "bola", "Bola azul").startswith("Lembrança atualizada.")
    assert memoria.fatos(pasta)[1] == memoria.Fato("Bola azul", "01/01/2026", True)


def test_limite_corta_as_mais_antigas_menos_as_fixas(pasta):
    escrever(pasta, "- [fixo] Nome do dono (01/01/2026)", "- Antiga " + "a" * 50 + " (01/01/2026)", "- Média " + "m" * 50 + " (01/01/2026)", "- Nova " + "n" * 50 + " (01/01/2026)")
    dentro = memoria.ler(pasta, limite=200)
    assert "Nome do dono" in dentro and "[fixo]" not in dentro  # fixa entra, sem a marca no prompt
    assert "Nova" in dentro and "Média" in dentro and "Antiga" not in dentro
    assert memoria.fora_do_prompt(pasta, limite=200) == {"Antiga " + "a" * 50}
    assert "Antiga" in memoria.ler(pasta, limite=10_000)


def test_fixas_ficam_mesmo_que_sozinhas_passem_do_limite(pasta):
    escrever(pasta, "- [fixo] " + "x" * 300 + " (01/01/2026)", "- recente (01/01/2026)")
    assert memoria.ler(pasta, limite=100).count("\n") == 0 and "xxx" in memoria.ler(pasta, limite=100)


def test_aviso_de_limite(pasta):
    assert memoria.aviso_de_limite(pasta) == ""
    escrever(pasta, "- " + "z" * 3500 + " (01/01/2026)")
    assert "Aviso" in memoria.aviso_de_limite(pasta)
    escrever(pasta, "- " + "z" * 4100 + " (01/01/2026)")
    assert "passou do limite" in memoria.aviso_de_limite(pasta)
    msg = memoria.lembrar(pasta, "mais uma", datetime(2026, 11, 10))
    assert msg.startswith("Guardado.") and "passou do limite" in msg


def test_lembrar_sem_folga_continua_dizendo_so_guardado(pasta):
    assert memoria.lembrar(pasta, "gosta de café", datetime(2026, 11, 10)) == "Guardado."


# --- lembranças como nós ---------------------------------------------------------------------------------------
def test_sincronizar_cria_um_no_por_linha_e_remove_os_antigos_da_memoria(grafo, pasta):
    antigo = grafo.criar_ideia("Ideia abstrata antiga", None, "memoria")
    escrever(pasta, "- O dono estuda na USP. (09/11/2026)", "- [fixo] O dono se chama Vitor. (09/11/2026)")
    assert grafo_fatos.sincronizar(grafo, pasta) == (2, 0)
    nos = grafo.ideias_da_origem("fato")
    assert sorted(n.resumo for n in nos) == ["O dono estuda na USP.", "O dono se chama Vitor."]
    assert {n.resumo: n.topico for n in nos}["O dono se chama Vitor."] == "fixo"
    assert antigo.id not in {i.id for i in grafo.listar_ideias()}
    assert grafo_fatos.sincronizar(grafo, pasta) == (0, 0)  # idempotente


def test_sincronizar_acompanha_o_arquivo_editado_a_mao(grafo, pasta):
    escrever(pasta, "- A. (01/01/2026)", "- B. (01/01/2026)")
    grafo_fatos.sincronizar(grafo, pasta)
    a = next(n for n in grafo.ideias_da_origem("fato") if n.resumo == "A.")
    escrever(pasta, "- A. (01/01/2026)", "- [fixo] C. (01/01/2026)")  # B saiu, C entrou
    assert grafo_fatos.sincronizar(grafo, pasta) == (1, 1)
    assert sorted(n.resumo for n in grafo.ideias_da_origem("fato")) == ["A.", "C."]
    assert grafo.ideia(a.id).resumo == "A."  # o nó que continuou é o mesmo (mantém ligações)


def test_titulos_iguais_nao_viram_o_mesmo_no(grafo, pasta):
    longo = "Vitor está cursando " + "x" * 80
    escrever(pasta, f"- {longo} 1", f"- {longo} 2")
    grafo_fatos.sincronizar(grafo, pasta)
    nomes = [n.nome for n in grafo.ideias_da_origem("fato")]
    assert len(set(nomes)) == 2 and any("(2)" in n for n in nomes)


def test_servico_edita_fixa_apaga_e_adiciona_pelo_no(grafo, pasta):
    escrever(pasta, "- O dono mora em Campinas. (09/11/2026)")
    grafo_fatos.sincronizar(grafo, pasta)
    servico = grafo_fatos.ServicoMemoria(grafo, pasta)
    no = grafo.ideias_da_origem("fato")[0]
    servico.editar(no.id, "O dono mora em Curitiba.")
    assert memoria.fatos(pasta)[0].texto == "O dono mora em Curitiba." and grafo.ideia(no.id).resumo == "O dono mora em Curitiba."
    assert grafo.ideia(no.id).nome.startswith("O dono mora em Curitiba")  # título provisório acompanha o texto
    servico.fixar(no.id, True)
    assert memoria.fatos(pasta)[0].fixo and grafo.ideia(no.id).topico == "fixo"
    novo = servico.adicionar("Gosta de café")
    assert novo.resumo == "Gosta de café" and [f.texto for f in memoria.fatos(pasta)][-1] == "Gosta de café"
    servico.apagar(no.id)
    assert [f.texto for f in memoria.fatos(pasta)] == ["Gosta de café"] and no.id not in {i.id for i in grafo.listar_ideias()}


def test_titulo_dado_pela_analise_sobrevive_a_edicao_do_texto(grafo, pasta):
    escrever(pasta, "- O dono estuda na USP. (09/11/2026)")
    grafo_fatos.sincronizar(grafo, pasta)
    no = grafo.ideias_da_origem("fato")[0]
    grafo.atualizar_ideia(no.id, nome="Curso na USP")
    grafo_fatos.ServicoMemoria(grafo, pasta).editar(no.id, "O dono estuda Ciência e Tecnologia na USP.")
    assert grafo.ideia(no.id).nome == "Curso na USP"


def test_editar_depois_de_o_arquivo_mudar_a_mao_guarda_a_edicao_do_dono(grafo, pasta):
    # Desde a D2 a memória mora no banco: a edição pela tela vale, e o arquivo editado à mão (ainda não decidido)
    # é guardado ao lado antes de a cópia ser regravada. Antes (arquivo como fonte), isso dava erro.
    servico = grafo_fatos.ServicoMemoria(grafo, pasta)
    escrever(pasta, "- A. (01/01/2026)")
    servico.memorias.inicializar()
    no = grafo.ideias_da_origem("fato")[0]
    escrever(pasta, "- Outra coisa. (01/01/2026)")
    servico.editar(no.id, "novo")
    assert grafo.ideia(no.id).resumo == "novo"
    guardados = list(pasta.glob("memoria.md.editado-a-mao-*"))
    assert len(guardados) == 1 and "Outra coisa" in guardados[0].read_text(encoding="utf-8")


# --- análise: títulos e ligações entre lembranças ----------------------------------------------------------------
class Falso:
    def __init__(self, *respostas):
        self.respostas, self.chamadas = list(respostas), []

    def completar(self, sistema, usuario):
        self.chamadas.append((sistema, usuario))
        r = self.respostas.pop(0)
        return Resposta(r if isinstance(r, str) else json.dumps(r), 0.0004)


def analisador(banco, grafo, cfg, *respostas):
    return Analisador(banco, grafo, Falso(*respostas), cfg)


def test_analise_das_lembrancas_da_titulos_e_liga(banco, grafo, cfg, pasta):
    escrever(pasta, "- O dono estuda na USP. (09/11/2026)", "- O dono cursa Cálculo Numérico. (09/11/2026)", "- A mãe do dono se chama Lílian. (09/11/2026)")
    an = analisador(
        banco, grafo, cfg,
        {"titulos": [{"n": 1, "titulo": "Curso na USP"}, {"n": 2, "titulo": "Cálculo Numérico"}, {"n": 3, "titulo": "Mãe Lílian"}],
         "ligacoes": [{"de": 1, "para": 2, "motivo": "matéria do curso"}, {"de": 1, "para": 99, "motivo": "inexistente"}]},
    )
    pend = an.pendentes()
    assert [(p.fonte, p.titulo) for p in pend] == [("fatos", "Memória")]
    assert "1. O dono estuda na USP." in pend[0].texto and "(09/11" not in pend[0].texto  # só o texto, sem data
    r = an.executar(pend)
    assert r.analisadas == 1 and r.ligacoes_novas == 1 and not r.erros
    nos = {n.resumo: n for n in grafo.ideias_da_origem("fato")}
    assert nos["O dono estuda na USP."].nome == "Curso na USP" and nos["A mãe do dono se chama Lílian."].nome == "Mãe Lílian"
    (l,) = grafo.listar_ligacoes()
    assert {l.a, l.b} == {nos["O dono estuda na USP."].id, nos["O dono cursa Cálculo Numérico."].id} and l.motivo == "matéria do curso"
    assert an.pendentes() == []  # em dia: nada novo, nada gasto


def test_analise_nao_sobrescreve_titulo_que_o_dono_mudou_e_nao_refaz_ligacao_desligada(banco, grafo, cfg, pasta):
    escrever(pasta, "- A é uma coisa. (01/01/2026)", "- B é outra coisa. (01/01/2026)")
    an = analisador(
        banco, grafo, cfg,
        {"titulos": [{"n": 1, "titulo": "Título A"}, {"n": 2, "titulo": "Título B"}], "ligacoes": [{"de": 1, "para": 2, "motivo": "x"}]},
        {"titulos": [{"n": 1, "titulo": "Outro A"}, {"n": 2, "titulo": "Outro B"}], "ligacoes": [{"de": 1, "para": 2, "motivo": "x"}]},
    )
    an.executar(an.pendentes())
    nos = {n.resumo: n for n in grafo.ideias_da_origem("fato")}
    a, b = nos["A é uma coisa."], nos["B é outra coisa."]
    grafo.atualizar_ideia(a.id, nome="Meu nome para A")
    grafo.desligar(a.id, b.id)
    escrever(pasta, "- A é uma coisa. (01/01/2026)", "- B é outra coisa. (01/01/2026)", "- C chegou. (02/01/2026)")
    an.executar(an.pendentes())
    assert grafo.ideia(a.id).nome == "Meu nome para A"  # título do dono é respeitado
    assert grafo.listar_ligacoes() == []  # a ligação que o dono desfez não volta


def test_menos_de_duas_lembrancas_nao_gasta_nada(banco, grafo, cfg, pasta):
    escrever(pasta, "- Só uma. (01/01/2026)")
    assert analisador(banco, grafo, cfg).pendentes() == []
    assert len(grafo.ideias_da_origem("fato")) == 1  # mas o nó existe (sincronizar é local)


def test_resposta_ruim_nao_derruba_e_fica_pendente(banco, grafo, cfg, pasta):
    escrever(pasta, "- A. (01/01/2026)", "- B. (01/01/2026)")
    an = analisador(banco, grafo, cfg, "isto não é json")
    r = an.executar(an.pendentes())
    assert len(r.erros) == 1 and len(an.pendentes()) == 1


# --- metas com tópico ------------------------------------------------------------------------------------------
def test_meta_com_topico_e_prazo(pasta):
    notas.definir_meta(pasta, "Ler 12 livros", "2026-12-31", "Leitura")
    notas.definir_meta(pasta, "Montar reserva", topico="(Investimentos)")
    notas.definir_meta(pasta, "Sem nada")
    assert "- [ ] Ler 12 livros (tópico: Leitura) (prazo: 2026-12-31)" in (pasta / "metas.md").read_text(encoding="utf-8")
    assert notas.listar_metas(pasta).splitlines() == ["Ler 12 livros [Leitura] (prazo: 2026-12-31)", "Montar reserva [Investimentos]", "Sem nada"]
    assert notas.metas_com_prazo(pasta) == [("Ler 12 livros", date(2026, 12, 31))]


def test_metas_antigas_sem_topico_continuam_lidas_e_ganham_topico(pasta):
    (pasta / "metas.md").write_text("# Metas\n\n- [ ] Ler 12 livros (prazo: 2026-12-31)\n- [x] Estudar cálculo\n- [ ] Correr 5 km\n", encoding="utf-8")
    assert [(m.titulo, m.topico, m.concluida) for m in notas.metas(pasta)] == [("Ler 12 livros", "", False), ("Estudar cálculo", "", True), ("Correr 5 km", "", False)]
    assert notas.metas_com_prazo(pasta) == [("Ler 12 livros", date(2026, 12, 31))]
    assert "Leitura" in notas.definir_topico_meta(pasta, "livros", "Leitura")
    assert "- [ ] Ler 12 livros (tópico: Leitura) (prazo: 2026-12-31)" in (pasta / "metas.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Não encontrei"):
        notas.definir_topico_meta(pasta, "cálculo", "Estudos")  # concluída não entra
    with pytest.raises(ValueError, match="nome do tópico"):
        notas.definir_topico_meta(pasta, "correr", "  ")


def test_concluir_meta_com_topico_e_progresso_por_topico(pasta):
    notas.definir_meta(pasta, "Ler 12 livros", None, "Leitura")
    notas.definir_meta(pasta, "Ler artigo", None, "Leitura")
    notas.definir_meta(pasta, "Reserva", None, "Investimentos")
    notas.definir_meta(pasta, "Sem tópico aqui")
    notas.concluir_meta(pasta, "artigo")
    assert notas.progresso_por_topico(pasta) == {"Leitura": (1, 2), "Investimentos": (0, 1), "Sem tópico": (0, 1)}
    assert "[x] Ler artigo (tópico: Leitura)" in (pasta / "metas.md").read_text(encoding="utf-8")


# --- ferramentas -----------------------------------------------------------------------------------------------
def _agente(cfg, ferramenta, entrada):
    from types import SimpleNamespace

    from eco.agente import Agente
    from eco.ferramentas import construir_ferramentas

    class Cliente:
        def __init__(self):
            self.chamadas, self.messages = [], self
            self._r = [
                SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(type="tool_use", id="t1", name=ferramenta, input=entrada)]),
                SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Ok.")]),
            ]

        def create(self, **kw):
            self.chamadas.append(kw)
            return self._r.pop(0)

    c = Cliente()
    Agente(cfg, construir_ferramentas(cfg), lambda d: True, c).responder("faça")
    return c.chamadas[1]["messages"][-1]["content"][0]["content"]


def test_ferramentas_de_memoria_e_metas(cfg, pasta):
    escrever(pasta, "- Gosta de café. (01/01/2026)", "- Estuda na USP. (01/01/2026)")
    assert "atualizada" in _agente(cfg, "editar_lembranca", {"trecho": "café", "novo_texto": "Gosta de café sem açúcar."})
    assert "fixada" in _agente(cfg, "fixar_lembranca", {"trecho": "usp"})
    assert [f.fixo for f in memoria.fatos(pasta)] == [False, True]
    assert "tópico Estudos" in _agente(cfg, "definir_meta", {"titulo": "Passar em cálculo", "topico": "Estudos"})
    assert "Passar em cálculo [Estudos]" in _agente(cfg, "listar_metas", {})
    notas.definir_meta(pasta, "Correr")
    assert "agora está no tópico Saúde" in _agente(cfg, "definir_topico_meta", {"trecho": "correr", "topico": "Saúde"})
