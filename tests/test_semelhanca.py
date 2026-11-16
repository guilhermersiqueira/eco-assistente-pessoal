"""Motor de semelhança (E4): índice de palavras, vetores guardados por ideia, busca combinada, download conferido e,
com o modelo real instalado, pares que devem e que não devem ser parecidos (calibração dos limiares) e desempenho."""

from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path

import numpy as np
import pytest

from eco.chat.armazenamento import Banco
from eco.grafo import semelhanca as sem
from eco.grafo.dados import Grafo

MODELO_REAL = Path.home() / ".eco" / "modelos" / sem.NOME_MODELO  # só leitura; os testes que o usam pulam sem ele


class CodificadorFalso:
    """Vetor por saco de palavras (hash): parecido = mesmas palavras. Conta quantos textos codificou."""

    disponivel = True

    def __init__(self) -> None:
        self.codificados: list[str] = []
        self.posicao: dict[str, int] = {}

    def codificar(self, textos, consulta=False, lote=16):
        if not consulta and textos is not sem.ANCORAS:
            self.codificados += list(textos)
        saida = np.zeros((len(textos), sem.DIMENSAO * 4), np.float32)
        for n, t in enumerate(textos):
            for p in sem.palavras_da_busca(t):
                saida[n, 1 + self.posicao.setdefault(p.lower(), len(self.posicao))] += 1
            saida[n, 0] += 0.01
            saida[n] /= np.linalg.norm(saida[n])
        return saida


class SemModelo:
    disponivel = False


@pytest.fixture
def grafo(tmp_path):
    return Grafo(Banco(tmp_path / "eco.db"))


def _ideias(g: Grafo, *pares):
    return [g.criar_ideia(nome, resumo=resumo) for nome, resumo in pares]


# --- índice de palavras ------------------------------------------------------------------------------------------
def test_indice_de_palavras_acompanha_as_ideias(grafo):
    s = sem.Semelhanca(grafo, SemModelo())
    a, b = _ideias(grafo, ("Investimentos", "Carteira de ações e BOVA11"), ("Corrida", "Treino de corrida às terças"))
    assert [x.ideia.id for x in s.buscar("bova11")] == [a.id]
    assert [x.ideia.id for x in s.buscar("acoes")] == [a.id]  # sem acento acha com acento
    grafo.atualizar_ideia(b.id, resumo="Treino de natação às terças")
    assert s.buscar("corrida terças")[0].ideia.id == b.id  # o nome continua "Corrida"
    assert s.buscar("natacao")[0].ideia.id == b.id and not s.buscar("treino de corrida aos sabados")[1:]
    grafo.apagar_ideia(a.id)
    assert s.buscar("bova11") == []


def test_palavras_vazias_nao_contam(grafo):
    s = sem.Semelhanca(grafo, SemModelo())
    _ideias(grafo, ("Casa", "Reforma da casa de praia"))
    assert s.buscar("o que é que você sabe sobre isso") == []
    assert sem.palavras_da_busca("O que a Eco sabe da USP e do TCC 2026?") == ["USP", "TCC", "2026"]


def test_indice_e_reposto_se_nao_bate(grafo, tmp_path):
    _ideias(grafo, ("Antes", "Ideia criada antes do índice"))
    sem.Semelhanca(grafo, SemModelo())
    grafo.banco._executar("DELETE FROM ideias_fts")  # como num banco restaurado de backup antigo
    s = sem.Semelhanca(grafo, SemModelo())
    assert s.buscar("índice")[0].ideia.nome == "Antes"


def test_cria_o_indice_com_backup_antes_no_banco_com_dados(tmp_path):
    g = Grafo(Banco(tmp_path / "eco.db"))
    _ideias(g, ("X", "algo"))
    sem.Semelhanca(g, SemModelo())
    copias = list((tmp_path / "backups").glob("*antes-criar-indice-de-semelhanca*"))
    assert len(copias) == 1
    sem.Semelhanca(g, SemModelo())  # segunda vez: já existe, sem outra cópia
    assert len(list((tmp_path / "backups").glob("*antes-criar-indice-de-semelhanca*"))) == 1


def test_criar_o_indice_nao_muda_nenhum_dado(grafo):
    _ideias(grafo, ("A", "um"), ("B", "dois"))
    con = grafo.banco._con
    antes = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in ("ideias", "ligacoes", "historico")}
    sem.Semelhanca(grafo, SemModelo())
    assert {t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in antes} == antes


# --- vetores -----------------------------------------------------------------------------------------------------
def test_vetor_por_ideia_so_recalcula_o_que_mudou(grafo):
    cod = CodificadorFalso()
    s = sem.Semelhanca(grafo, cod)
    a, b = _ideias(grafo, ("Café", "Vitor gosta de café"), ("Gato", "Mel é o gato"))
    assert s.atualizar() == 2 and s.atualizar() == 0
    grafo.atualizar_ideia(b.id, resumo="Mel é a gata")
    assert s.atualizar() == 1 and cod.codificados[-1] == "Gato. Mel é a gata"
    grafo.apagar_ideia(a.id)
    assert grafo.banco._consultar("SELECT count(*) AS n FROM ideia_vetores")[0]["n"] == 1  # some junto com a ideia
    assert s.vetor(b.id) is not None and s.vetor(a.id) is None


def test_busca_combina_vetor_e_palavra_e_respeita_arquivadas(grafo):
    s = sem.Semelhanca(grafo, CodificadorFalso())
    a, b, c = _ideias(grafo, ("Viagem", "Viagem para Ouro Preto em julho"), ("Ouro", "Investir em ouro"),
                      ("Faculdade", "Matrícula na USP"))
    achados = s.buscar("viagem ouro preto", minimo=0.3)
    assert achados[0].ideia.id == a.id and achados[0].por_palavra and achados[0].relevancia is not None
    assert c.id not in [x.ideia.id for x in achados]
    grafo.arquivar(a.id, "teste")
    assert a.id not in [x.ideia.id for x in s.buscar("viagem ouro preto", minimo=0.3)]
    assert a.id in [x.ideia.id for x in s.buscar("viagem ouro preto", minimo=0.3, incluir_arquivadas=True)]
    assert a.id not in [x.ideia.id for x in s.buscar("viagem ouro preto", minimo=0.3, incluir_arquivadas=True, excluir=[a.id])]


def test_parecidas(grafo):
    s = sem.Semelhanca(grafo, CodificadorFalso())
    a, b, c = _ideias(grafo, ("Café", "café coado de manhã"), ("Café 2", "café coado de manhã cedo"), ("Banco", "conta no banco"))
    pares = s.parecidas(a.id, minimo=0.5)
    assert [p[0].id for p in pares] == [b.id]


def test_sem_modelo_nao_quebra(grafo):
    s = sem.Semelhanca(grafo, SemModelo())
    _ideias(grafo, ("Café", "café coado"))
    assert s.atualizar() == 0 and s.parecidas("x") == [] and s.buscar("café")[0].relevancia is None


def test_codificador_sem_arquivos_nao_esta_disponivel(tmp_path):
    assert sem.Codificador(tmp_path / "vazio").disponivel is False
    assert sem.modelo_presente(tmp_path / "vazio") is False


# --- download -----------------------------------------------------------------------------------------------------
def _abrir_falso(conteudos):
    def abrir(url, timeout=0):
        return io.BytesIO(conteudos[url.rsplit("/", 1)[1]])
    return abrir


def test_download_confere_sha_e_nao_deixa_arquivo_quebrado(tmp_path, monkeypatch):
    dados = {"model.onnx": b"m" * 10, "tokenizer.json": b"t" * 5}
    monkeypatch.setattr(sem, "ARQUIVOS", {n: (len(c), hashlib.sha256(c).hexdigest()) for n, c in dados.items()})
    pasta = sem.baixar(tmp_path / "m", progresso=lambda _: None, abrir=_abrir_falso(dados))
    assert sem.modelo_presente(pasta)
    errado = {"model.onnx": b"x" * 10, "tokenizer.json": b"t" * 5}
    with pytest.raises(ValueError, match="não confere"):
        sem.baixar(tmp_path / "n", progresso=lambda _: None, abrir=_abrir_falso(errado))
    assert not (tmp_path / "n" / "model.onnx").exists() and not list((tmp_path / "n").glob("*.parcial"))


def test_url_aponta_para_a_revisao_fixa(tmp_path, monkeypatch):
    urls = []
    monkeypatch.setattr(sem, "ARQUIVOS", {"tokenizer.json": (1, hashlib.sha256(b"a").hexdigest())})

    def abrir(url, timeout=0):
        urls.append(url)
        return io.BytesIO(b"a")

    sem.baixar(tmp_path, progresso=lambda _: None, abrir=abrir)
    assert urls == [f"https://huggingface.co/{sem.REPO}/resolve/{sem.REVISAO}/onnx/tokenizer.json"]


def test_testes_nao_usam_a_pasta_real_de_modelos():
    assert Path.home() / ".eco" not in sem.PASTA_MODELOS.parents


# --- modelo real (calibração e desempenho) -----------------------------------------------------------------------
real = pytest.mark.skipif(not sem.modelo_presente(MODELO_REAL), reason="modelo de semelhança não instalado")


@pytest.fixture(scope="module")
def cod_real():
    return sem.Codificador(MODELO_REAL)


# Pares que devem ser parecidos (mesmo sentido, palavras diferentes) e que não devem (só dividem palavras ou nada).
PARECIDOS = [
    ("Vitor gosta de café coado pela manhã.", "O dono prefere tomar café passado no coador logo cedo."),
    ("Vitor cursa Engenharia na USP.", "Ele estuda na Universidade Federal do ABC."),
    ("Reunião com o orientador do TCC toda quinta.", "Encontro semanal com o professor que orienta o trabalho de conclusão."),
    ("Quero começar a investir em ações.", "Planejo aplicar dinheiro na bolsa de valores."),
    ("O gato dele se chama Mel.", "Mel é o nome do gatinho do Vitor."),
]
DIFERENTES = [
    ("Vitor gosta de café coado pela manhã.", "A conta do banco vence dia 10."),
    ("Quero começar a investir em ações.", "Treino de corrida às terças e quintas."),
    ("O gato dele se chama Mel.", "Comprar pipoca para o cinema de sábado."),  # mesma palavra, outro sentido
    ("Vitor cursa Engenharia na USP.", "Receita de bolo de cenoura com cobertura."),
    ("Reunião com o orientador do TCC toda quinta.", "Viagem para a praia no feriado."),
]


@real
def test_pares_parecidos_e_diferentes(cod_real):
    s = sem.Semelhanca(Grafo(Banco(":memory:")), cod_real)
    sim = [s.comparar_textos(a, b) for a, b in PARECIDOS]
    nao = [s.comparar_textos(a, b) for a, b in DIFERENTES]
    assert min(sim) >= sem.PARECIDAS_MEDIA, list(zip(sim, PARECIDOS))  # nenhum par certo passa batido
    assert max(nao) < sem.PARECIDAS_ALTA, list(zip(nao, DIFERENTES))  # nenhum par errado sai como certeza
    assert sum(x >= sem.PARECIDAS_ALTA for x in sim) >= 3  # a maioria dos certos sai com certeza
    assert sum(sim) / len(sim) > sum(nao) / len(nao) + 0.04


MEMORIA = [
    ("Nome", "O dono se chama Vitor."),
    ("Faculdade", "Vitor cursa Engenharia na USP."),
    ("Café", "Vitor gosta de café coado pela manhã."),
    ("Gato", "O gato do Vitor se chama Mel."),
    ("Trabalho", "Vitor faz o fechamento quinzenal dos motoristas da Transportadora A."),
    ("Corrida", "Vitor corre às terças e quintas de manhã."),
    ("Investimentos", "Vitor quer montar uma carteira de ações com BOVA11."),
    ("TCC", "Reunião com o orientador do TCC toda quinta às 14h."),
    ("Landing page", "Vitor fez o site da Padaria Exemplo."),
    ("Mãe", "A mãe do Vitor faz aniversário em 3 de março."),
]
# pergunta -> nomes que precisam aparecer entre os 3 primeiros (vazio: nada deve passar do mínimo)
PERGUNTAS = {
    "o que eu bebo de manhã?": {"Café"},
    "como chama meu bichinho?": {"Gato"},
    "onde eu estudo?": {"Faculdade"},
    "quando é o encontro com meu professor orientador?": {"TCC"},
    "que dias eu faço exercício?": {"Corrida"},
    "tenho algo sobre BOVA11?": {"Investimentos"},
    "qual o aniversário da minha mãe?": {"Mãe"},
    "o site da padaria": {"Landing page"},
    "qual a capital da Austrália?": set(),
    "receita de lasanha": set(),
}


@real
def test_busca_por_pergunta_acha_a_lembranca_certa_e_nada_quando_nao_tem(tmp_path, cod_real):
    g = Grafo(Banco(tmp_path / "eco.db"))
    for nome, resumo in MEMORIA:
        g.criar_ideia(nome, resumo=resumo)
    s = sem.Semelhanca(g, cod_real)
    erros = []
    for pergunta, esperado in PERGUNTAS.items():
        achados = s.buscar(pergunta, limite=3)
        nomes = [a.ideia.nome for a in achados]
        if esperado and not esperado <= set(nomes):
            erros.append((pergunta, esperado, [(a.ideia.nome, round(a.relevancia, 3)) for a in s.buscar(pergunta, 3, None)]))
        if not esperado and nomes:
            erros.append((pergunta, "nada", [(a.ideia.nome, round(a.relevancia, 3)) for a in achados]))
        if esperado and esperado.isdisjoint(a.ideia.nome for a in achados[:2]):  # empate aceito ("chama" x nome)
            erros.append((pergunta, "entre os 2 primeiros", [a.ideia.nome for a in achados]))
    assert not erros, erros


@real
def test_desempenho(tmp_path, cod_real):
    g = Grafo(Banco(tmp_path / "eco.db"))
    with g.banco._lock, g.banco._con:
        for n in range(300):
            g.banco._con.execute(
                "INSERT INTO ideias (id, nome, resumo, origem, criado_em, atualizado_em) VALUES (?,?,?,'chat','x','x')",
                (f"i{n}", f"Ideia {n}", f"Assunto número {n} sobre projetos, estudos e rotina."),
            )
    s = sem.Semelhanca(g, cod_real)
    t = time.perf_counter()
    assert s.atualizar() == 300
    indexar = time.perf_counter() - t
    s.buscar("aquecimento")  # primeira consulta
    t = time.perf_counter()
    for _ in range(5):
        s.buscar("o que eu tenho sobre estudos?")
    consulta = (time.perf_counter() - t) / 5
    assert indexar < 60 and consulta < 0.3, (indexar, consulta)
