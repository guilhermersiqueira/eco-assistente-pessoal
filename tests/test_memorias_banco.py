"""Memória no banco (decisão D2): passagem do memoria.md, prompt igual, cópia de leitura e edição à mão com confirmação."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from eco.chat.armazenamento import Banco
from eco.ferramentas import memoria
from eco.grafo.dados import Grafo
from eco.grafo.memorias import Memorias

ARQUIVO = """# Memória da Eco

- [fixo] O dono se chama Vitor. (09/11/2026)
- Vitor cursa a USP. (09/11/2026)
- Vitor gosta de café. (10/11/2026)
"""


@pytest.fixture
def pasta(tmp_path):
    p = tmp_path / "dados"
    p.mkdir()
    (p / "memoria.md").write_text(ARQUIVO, encoding="utf-8")
    return p


@pytest.fixture
def mem(tmp_path, pasta):
    return Memorias(Grafo(Banco(tmp_path / "eco.db")), pasta)


def test_primeira_vez_passa_tudo_para_o_banco_em_ordem_com_datas_e_fixas(mem, pasta):
    legado_prompt = memoria.ler(pasta)
    assert mem.inicializar() is True
    assert [(f.texto, f.data, f.fixo) for f in mem.fatos()] == [
        ("O dono se chama Vitor.", "09/11/2026", True),
        ("Vitor cursa a USP.", "09/11/2026", False),
        ("Vitor gosta de café.", "10/11/2026", False),
    ]
    assert mem.ler() == legado_prompt  # o texto do prompt é o mesmo de antes
    assert (pasta / "memoria.md.antes-do-banco").read_text(encoding="utf-8") == ARQUIVO  # o original fica guardado
    assert mem.grafo.ideias_da_origem("fato")[0].protegido in (0, 1)
    assert mem.inicializar() is False and len(mem.fatos()) == 3  # uma vez só


def test_memoria_md_vira_copia_no_mesmo_formato(mem, pasta):
    mem.inicializar()
    texto = (pasta / "memoria.md").read_text(encoding="utf-8")
    assert "Cópia gerada pela Eco" in texto
    assert [(f.texto, f.data, f.fixo) for f in memoria.fatos(pasta)] == [(f.texto, f.data, f.fixo) for f in mem.fatos()]
    assert mem.edicao_manual() is None


def test_lembrar_esquecer_editar_e_fixar_no_banco_com_historico(mem, pasta):
    mem.inicializar()
    with mem.grafo.como("voz"):
        assert mem.lembrar("Vitor trabalha às 8h.").startswith("Guardado.")
    assert "trabalha às 8h" in (pasta / "memoria.md").read_text(encoding="utf-8")
    assert mem.editar_por_trecho("8h", "Vitor trabalha às 9h.") == "Lembrança atualizada."
    no = next(i for i in mem.nos() if "9h" in i.resumo)
    h = mem.grafo.historico(no.id)
    assert [x["acao"] for x in h] == ["criar", "atualizar"] and h[0]["autor"] == "voz"
    assert h[1]["antes"]["resumo"] == "Vitor trabalha às 8h."  # o conteúdo anterior fica consultável
    assert mem.fixar_por_trecho("café") == "Lembrança fixada: nunca será cortada."
    assert next(f for f in mem.fatos() if "café" in f.texto).fixo
    assert mem.esquecer("USP") == "Esquecido."
    assert "USP" not in (pasta / "memoria.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Mais de uma"):
        mem.esquecer("Vitor")


def test_limite_e_fixas_como_antes(mem, tmp_path):
    mem.inicializar()
    for n in range(60):
        mem.lembrar(f"Fato número {n} com algumas palavras para ocupar espaço.")
    lido = mem.ler(limite=500)
    assert len(lido) <= 500 and "Vitor." in lido and "Fato número 59" in lido and "número 0 " not in lido
    assert "Vitor cursa a USP." in mem.fora_do_prompt(limite=500)


def test_edicao_a_mao_e_detectada_e_so_entra_com_confirmacao(mem, pasta):
    mem.inicializar()
    texto = (pasta / "memoria.md").read_text(encoding="utf-8")
    texto = texto.replace("- Vitor cursa a USP. (09/11/2026)\n", "").replace("- Vitor gosta de café.", "- [fixo] Vitor gosta de café.")
    (pasta / "memoria.md").write_text(texto + "- Vitor tem um gato chamado Mel. (12/11/2026)\n", encoding="utf-8")
    e = mem.edicao_manual()
    assert [f.texto for f in e.novas] == ["Vitor tem um gato chamado Mel."]
    assert [f.texto for f in e.removidas] == ["Vitor cursa a USP."] and [f.texto for f in e.fixadas] == ["Vitor gosta de café."]
    assert len(mem.fatos()) == 3  # nada entrou sozinho
    assert mem.importar_edicao().startswith("Importei a edição do memoria.md: 1 nova(s), 1 removida(s), 1 fixa(s)")
    textos = [f.texto for f in mem.fatos()]
    assert "Vitor tem um gato chamado Mel." in textos and "Vitor cursa a USP." not in textos
    assert mem.edicao_manual() is None


def test_desfazer_a_edicao_a_mao(mem, pasta):
    mem.inicializar()
    (pasta / "memoria.md").write_text("# Memória\n\n- Bagunça. (01/01/2026)\n", encoding="utf-8")
    assert mem.edicao_manual() is not None
    mem.descartar_edicao()
    assert mem.edicao_manual() is None and len(memoria.fatos(pasta)) == 3


def test_prompt_usa_o_banco_e_avisa_da_edicao_a_mao(mem, pasta, tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_DADOS_DIR", str(pasta))
    from eco.agente import Agente
    from eco.config import carregar_config
    from eco.ferramentas import construir_ferramentas

    mem.inicializar()
    chamadas = []

    class Cliente:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                chamadas.append(kw)
                return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Oi")], usage=None)

    cfg = carregar_config()
    agente = Agente(cfg, construir_ferramentas(cfg), lambda d: True, Cliente(), memorias=mem)
    agente.responder("oi")
    texto = "".join(b["text"] for b in chamadas[0]["system"])
    assert "Vitor gosta de café." in texto and "editado à mão" not in texto
    (pasta / "memoria.md").write_text(ARQUIVO + "- Novo fato. (12/11/2026)\n", encoding="utf-8")
    agente.responder("e aí")
    texto = "".join(b["text"] for b in chamadas[1]["system"])
    assert "O memoria.md foi editado à mão" in texto and "importar_edicao_da_memoria" in texto
    assert "Novo fato" not in texto.split("O que você já sabe sobre o dono")[1].split("O memoria.md")[0]  # não entrou sozinho


def test_ferramentas_com_o_banco_gravam_no_banco_e_pedem_confirmacao_para_importar(tmp_path, pasta, monkeypatch):
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_DADOS_DIR", str(pasta))
    from eco.config import carregar_config
    from eco.ferramentas import construir_ferramentas

    banco = Banco(tmp_path / "eco.db")
    f = {x.nome: x for x in construir_ferramentas(carregar_config(), banco=banco)}
    f["lembrar"].executar({"fato": "Vitor corre às terças."})
    assert any("corre às terças" in i.resumo for i in Grafo(banco).ideias_da_origem("fato"))
    assert f["importar_edicao_da_memoria"].confirmar is not None and f["desfazer_edicao_da_memoria"].confirmar is not None
    with pytest.raises(ValueError, match="não tem mudanças"):
        f["importar_edicao_da_memoria"].confirmar({})
    (pasta / "memoria.md").write_text((pasta / "memoria.md").read_text(encoding="utf-8") + "- Fato à mão. (12/11/2026)\n", encoding="utf-8")
    frase = f["importar_edicao_da_memoria"].confirmar({})
    assert "nova: Fato à mão." in frase
    f["importar_edicao_da_memoria"].executar({})
    assert any(i.resumo == "Fato à mão." for i in Grafo(banco).ideias_da_origem("fato"))
