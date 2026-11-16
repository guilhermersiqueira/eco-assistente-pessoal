"""Exportar e importar o grafo em JSON (requisitos 19.6.11 e 19.6.12)."""

from __future__ import annotations

import json

import pytest

from eco.chat.armazenamento import Banco
from eco.grafo import exportar as ex
from eco.grafo.dados import Grafo


@pytest.fixture
def cheio(tmp_path):
    banco = Banco(tmp_path / "a.db")
    esp = banco.criar_espaco("Estudos")
    conv = banco.criar_conversa(esp.id, "Python")
    g = Grafo(banco)
    tronco = g.criar_ideia("Programação", esp.id, nivel="tronco")
    py = g.criar_ideia("Python", esp.id, nivel="galho", resumo="Linguagem")
    g.definir_pai(py.id, tronco.id)
    curso = g.criar_ideia("Curso de Python", esp.id, nivel="folha")
    g.definir_pai(curso.id, py.id)
    g.ligar(py.id, curso.id, "o curso ensina", tipo="causal")
    g.adicionar_fonte(py.id, conv.id)
    g.adicionar_dia(curso.id, "2026-11-10")
    g.criar_fato("O dono estuda Python", "Estuda Python", fixo=True)
    return g


def test_exportar_tem_tudo_com_versao(cheio, tmp_path):
    arquivo = ex.exportar_para_arquivo(cheio, tmp_path / "exportacoes")
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    assert dados["formato"] == "eco-grafo" and dados["versao"] == ex.VERSAO
    assert dados["resumo"] == {"ideias": 4, "ligacoes": 1}
    assert {"pai", "nivel", "tipo", "topico"} <= set(dados["ideias"][0])  # colunas vêm do banco
    assert len(dados["ideia_fontes"]) == 1 and len(dados["ideia_dias"]) == 1
    assert ex.ultima_exportacao(tmp_path / "exportacoes") == arquivo


def test_restaurar_num_banco_vazio_preserva_hierarquia_ligacoes_e_metadados(cheio, tmp_path):
    arquivo = ex.exportar_para_arquivo(cheio, tmp_path / "exp")
    destino = Banco(tmp_path / "b.db")
    novo = Grafo(destino)
    plano = ex.planejar_importacao(novo, arquivo)
    assert plano.frase.startswith("Importar") and "4 ideias novas e 1 ligações novas" in plano.frase
    assert novo.listar_ideias() == []  # planejar não grava
    print(plano.executar())
    por_nome = {i.nome: i for i in novo.listar_ideias()}
    assert por_nome["Curso de Python"].pai == por_nome["Python"].id and por_nome["Python"].pai == por_nome["Programação"].id
    assert por_nome["Estuda Python"].topico == "fixo" and por_nome["Estuda Python"].origem == "fato"
    l = novo.listar_ligacoes()[0]
    assert l.tipo == "causal" and l.motivo == "o curso ensina"
    assert novo.dias(por_nome["Curso de Python"].id) == ["2026-11-10"]
    # espaços e conversas não vêm no grafo: a ideia vai para o Geral e a origem de conversa que não existe fica de fora
    assert por_nome["Python"].espaco_id is None and novo.fontes(por_nome["Python"].id) == []


def test_importar_de_novo_nao_duplica(cheio, tmp_path):
    arquivo = ex.exportar_para_arquivo(cheio, tmp_path / "exp")
    plano = ex.planejar_importacao(cheio, arquivo)
    assert "0 ideias novas e 0 ligações novas" in plano.frase and "4 já estão iguais" in plano.frase
    plano.executar()
    assert len(cheio.listar_ideias()) == 4 and len(cheio.listar_ligacoes()) == 1


def test_conflito_so_e_substituido_quando_pedido(cheio, tmp_path):
    arquivo = ex.exportar_para_arquivo(cheio, tmp_path / "exp")
    py = next(i for i in cheio.listar_ideias() if i.nome == "Python")
    cheio.atualizar_ideia(py.id, resumo="Mudou depois do backup")
    manter = ex.planejar_importacao(cheio, arquivo)
    assert "1 itens estão diferentes no grafo atual e ficam como estão" in manter.frase
    manter.executar()
    assert cheio.ideia(py.id).resumo == "Mudou depois do backup"
    trocar = ex.planejar_importacao(cheio, arquivo, substituir_conflitos=True)
    assert "SUBSTITUIR 1 itens" in trocar.frase
    trocar.executar()
    assert cheio.ideia(py.id).resumo == "Linguagem"


def test_mesmo_nome_com_outro_id_fica_de_fora(cheio, tmp_path):
    arquivo = ex.exportar_para_arquivo(cheio, tmp_path / "exp")
    outro = Grafo(Banco(tmp_path / "c.db"))
    outro.criar_ideia("Python", None, nivel="galho")  # mesma ideia, id diferente
    plano = ex.planejar_importacao(outro, arquivo)
    assert "1 repetem ideias que já existem e ficam de fora" in plano.frase
    plano.executar()
    assert sum(i.nome == "Python" for i in outro.listar_ideias()) == 1


def test_backup_antes_de_gravar(cheio, tmp_path):
    arquivo = ex.exportar_para_arquivo(cheio, tmp_path / "exp")
    ordem = []
    plano = ex.planejar_importacao(Grafo(Banco(tmp_path / "d.db")), arquivo, fazer_backup=lambda: ordem.append("backup"))
    assert ordem == []
    plano.executar()
    assert ordem == ["backup"]


@pytest.mark.parametrize(
    "estragar,mensagem",
    [
        (lambda d: d.update(formato="outro"), "não é uma exportação"),
        (lambda d: d.update(versao=99), "mais nova"),
        (lambda d: d["ideias"][0].pop("nome"), "sem id, nome ou origem"),
        (lambda d: d["ideias"].append(dict(d["ideias"][0])), "aparece duas vezes"),
        (lambda d: d["ligacoes"][0].update(a="nao-existe"), "não está no arquivo"),
        (lambda d: [i.update(pai=d["ideias"][2]["id"]) for i in d["ideias"][:1]], "círculo"),
    ],
)
def test_arquivo_invalido_e_recusado_antes_de_gravar(cheio, tmp_path, estragar, mensagem):
    dados = ex.exportar(cheio)
    estragar(dados)
    arquivo = tmp_path / "ruim.json"
    arquivo.write_text(json.dumps(dados), encoding="utf-8")
    vazio = Grafo(Banco(tmp_path / "e.db"))
    with pytest.raises(ValueError, match=mensagem):
        ex.planejar_importacao(vazio, arquivo)
    assert vazio.listar_ideias() == []


def test_json_quebrado(tmp_path, cheio):
    (tmp_path / "x.json").write_text("{nao e json", encoding="utf-8")
    with pytest.raises(ValueError, match="não é um JSON válido"):
        ex.planejar_importacao(cheio, tmp_path / "x.json")


def test_ferramentas_exportar_e_importar_pela_voz(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    from eco.config import carregar_config
    from eco.ferramentas import construir_ferramentas

    banco = Banco(tmp_path / "f.db")
    Grafo(banco).criar_ideia("Python")
    f = {x.nome: x for x in construir_ferramentas(carregar_config(), banco=banco)}
    assert f["exportar_grafo"].confirmar is None and f["importar_grafo"].confirmar is not None
    assert f["exportar_grafo"].executar({}).startswith("Exportei 1 ideias e 0 ligações")
    assert "0 ideias novas" in f["importar_grafo"].confirmar({})  # sem caminho: a exportação mais recente
    with pytest.raises(ValueError, match="pasta pessoal|não mexo"):  # fora da casa ou em pasta de configuração
        f["importar_grafo"].confirmar({"caminho": str(tmp_path / "fora.json")})
