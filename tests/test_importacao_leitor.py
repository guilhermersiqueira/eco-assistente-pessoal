"""Leitura do export de conversas do claude.ai (M1). O export de teste é montado aqui, com a mesma estrutura do real
(conversas com chat_messages, blocos de conteúdo, anexos e manifesto) e textos inventados."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from eco.chat.armazenamento import Banco
from eco.importacao import filtro, leitor
from eco.importacao.estado import Importacoes
from eco.importacao.leitor import ExportInvalido, ler_export, localizar_export
from eco.importacao.modelo import ASSISTENTE, DONO

MODELO = "claude-haiku-5-5"
AGORA = 1_800_000_000  # um instante qualquer para os mtimes


def _msg(sender, texto, quando, **extra):
    return {"uuid": f"m-{sender}-{quando}", "text": texto, "sender": sender, "created_at": quando, "updated_at": quando,
            "content": [{"type": "text", "text": texto, "citations": []}], "attachments": [], "files": [],
            "parent_message_uuid": "00000000-0000-4000-8000-000000000000", **extra}


def _conv(uuid="c1", name="Conversa de teste", summary="", msgs=None, criada="2026-10-13T10:00:00.000000Z",
          atualizada="2026-10-13T11:00:00.000000Z"):
    msgs = msgs if msgs is not None else [
        _msg("human", "Mensagem do dono número um", "2026-10-13T10:00:01.000000Z"),
        _msg("assistant", "Resposta da IA número um", "2026-10-13T10:00:02.000000Z"),
    ]
    return {"uuid": uuid, "name": name, "summary": summary, "created_at": criada, "updated_at": atualizada,
            "account": {"uuid": "conta"}, "chat_messages": msgs}


def _zip(caminho: Path, conversas, entrada="conversations.json"):
    with zipfile.ZipFile(caminho, "w") as z:
        z.writestr(entrada, json.dumps(conversas))
    return caminho


def _mtime(caminho: Path, segundos: float):
    os.utime(caminho, (segundos, segundos))
    return caminho


# --- ler: conversão ----------------------------------------------------------------------------------------------------
def test_converte_conversa_mensagens_e_datas(tmp_path):
    z = _zip(tmp_path / "conversations-000.zip", [_conv()])
    r = ler_export([z])
    [c] = r.conversas
    assert (c.uuid, c.titulo, c.criada_em, c.atualizada_em) == ("c1", "Conversa de teste", "2026-10-13T10:00:00.000000Z", "2026-10-13T11:00:00.000000Z")
    assert [(m.autor, m.texto) for m in c.mensagens] == [(DONO, "Mensagem do dono número um"), (ASSISTENTE, "Resposta da IA número um")]
    assert c.mensagens[0].quando == "2026-10-13T10:00:01.000000Z" and r.invalidas == 0 and r.avisos == []


def test_so_entram_mensagens_com_texto_e_nunca_raciocinio_ferramenta_ou_anexo(tmp_path):
    msgs = [
        _msg("human", "Veja o anexo", "2026-10-13T10:00:01Z",
             attachments=[{"file_name": "doc.pdf", "extracted_content": "CONTEUDO PRIVADO DE TERCEIRO"}]),
        _msg("assistant", "", "2026-10-13T10:00:02Z", content=[
            {"type": "thinking", "thinking": "RACIOCINIO INTERNO"}, {"type": "tool_use", "name": "busca", "input": {"q": "FERRAMENTA"}}]),
        _msg("human", "", "2026-10-13T10:00:03Z", content=[{"type": "tool_result", "content": [{"type": "text", "text": "RESULTADO"}]}]),
        _msg("assistant", "Resposta final", "2026-10-13T10:00:04Z"),
        _msg("system", "Mensagem de sistema", "2026-10-13T10:00:05Z"),
    ]
    [c] = ler_export([_zip(tmp_path / "conversations-000.zip", [_conv(msgs=msgs)])]).conversas
    textos = " | ".join(m.texto for m in c.mensagens)
    assert [m.texto for m in c.mensagens] == ["Veja o anexo", "Resposta final"]
    for proibido in ("PRIVADO", "RACIOCINIO", "FERRAMENTA", "RESULTADO", "sistema"):
        assert proibido not in textos


def test_mensagens_fora_de_ordem_sao_ordenadas_pela_data(tmp_path):
    msgs = [_msg("assistant", "segunda", "2026-10-13T10:00:02Z"), _msg("human", "primeira", "2026-10-13T10:00:01Z")]
    [c] = ler_export([_zip(tmp_path / "conversations-000.zip", [_conv(msgs=msgs)])]).conversas
    assert [m.texto for m in c.mensagens] == ["primeira", "segunda"]


def test_titulo_cai_para_resumo_depois_primeira_fala_do_dono_depois_data(tmp_path):
    msgs = [_msg("assistant", "Olá", "2026-10-13T10:00:00Z"), _msg("human", "Quero ajuda com a minha planilha de gastos mensais " * 3, "2026-10-13T10:00:01Z")]
    z = _zip(tmp_path / "conversations-000.zip", [
        _conv("a", name="", summary="Resumo da conversa"), _conv("b", name="", msgs=msgs),
        _conv("c", name="", msgs=[_msg("assistant", "só a IA", "2026-10-13T10:00:00Z")]), _conv("d", name="  Nome  ")])
    t = {c.uuid: c.titulo for c in ler_export([z]).conversas}
    assert t["a"] == "Resumo da conversa" and t["d"] == "Nome"
    assert t["b"].startswith("Quero ajuda com a minha planilha") and len(t["b"]) <= 60
    assert t["c"] == "Conversa de 2026-10-13"


def test_conversa_invalida_e_contada_e_as_validas_seguem(tmp_path):
    itens = [{"name": "sem uuid", "chat_messages": []}, {"uuid": "x", "chat_messages": "não é lista"}, "texto solto", _conv("ok")]
    r = ler_export([_zip(tmp_path / "conversations-000.zip", itens)])
    assert [c.uuid for c in r.conversas] == ["ok"] and r.invalidas == 3 and "3 conversa(s)" in r.avisos[0]


def test_lista_vazia_avisa_que_nao_ha_conversas(tmp_path):
    r = ler_export([_zip(tmp_path / "conversations-000.zip", [])])
    assert r.conversas == [] and any("nenhuma conversa" in a for a in r.avisos)


def test_partes_juntas_sem_repetir_e_vale_a_versao_mais_recente(tmp_path):
    p0 = _zip(tmp_path / "conversations-000.zip", [_conv("a", name="Antiga", atualizada="2026-10-01T00:00:00Z"), _conv("b")])
    p1 = _zip(tmp_path / "conversations-001.zip", [_conv("a", name="Nova", atualizada="2026-10-05T00:00:00Z"), _conv("c")])
    r = ler_export([p0, p1])
    assert {c.uuid: c.titulo for c in r.conversas} == {"a": "Nova", "b": "Conversa de teste", "c": "Conversa de teste"}


def test_le_conversations_json_solto_e_zip_do_formato_antigo(tmp_path):
    solto = tmp_path / "conversations.json"
    solto.write_text(json.dumps([_conv("solto")]), encoding="utf-8")
    antigo = _zip(tmp_path / "data-2026-10-13.zip", [_conv("antigo")], entrada="pasta/conversations.json")
    assert [c.uuid for c in ler_export([solto]).conversas] == ["solto"]
    assert [c.uuid for c in ler_export([antigo]).conversas] == ["antigo"]


def test_hash_identifica_o_conteudo_dos_arquivos(tmp_path):
    a = _zip(tmp_path / "conversations-000.zip", [_conv("a")])
    h1 = ler_export([a]).hash
    assert h1 == ler_export([a]).hash
    b = _zip(tmp_path / "outro" / "conversations-000.zip", [_conv("a", name="Outro")]) if (tmp_path / "outro").mkdir() is None else None
    assert ler_export([b]).hash != h1


# --- ler: arquivos ruins -----------------------------------------------------------------------------------------------
def test_zip_corrompido(tmp_path):
    ruim = tmp_path / "conversations-000.zip"
    ruim.write_bytes(b"isto nao e um zip")
    with pytest.raises(ExportInvalido, match="não é um .zip válido"):
        ler_export([ruim])


def test_zip_sem_conversations_json(tmp_path):
    z = _zip(tmp_path / "memories-000.zip", [], entrada="memories.json")
    with pytest.raises(ExportInvalido, match="não tem o arquivo conversations.json"):
        ler_export([z])


def test_json_quebrado_ou_com_formato_inesperado(tmp_path):
    quebrado = tmp_path / "conversations-000.zip"
    with zipfile.ZipFile(quebrado, "w") as z:
        z.writestr("conversations.json", '[{"uuid": "a", ')
    with pytest.raises(ExportInvalido, match="corrompido"):
        ler_export([quebrado])
    with pytest.raises(ExportInvalido, match="formato inesperado"):
        ler_export([_zip(tmp_path / "conversations-001.zip", {"conversas": []})])


def test_arquivo_que_nao_e_utf8(tmp_path):
    ruim = tmp_path / "conversations.json"
    ruim.write_bytes(b"\xff\xfe[\x00]\x00 \x80\x81")
    with pytest.raises(ExportInvalido):
        ler_export([ruim])


def test_arquivo_grande_demais_e_recusado(tmp_path, monkeypatch):
    monkeypatch.setattr(leitor, "MAX_BYTES_JSON", 50)
    with pytest.raises(ExportInvalido, match="grande demais"):
        ler_export([_zip(tmp_path / "conversations-000.zip", [_conv()])])
    solto = tmp_path / "conversations.json"
    solto.write_text(json.dumps([_conv()]), encoding="utf-8")
    with pytest.raises(ExportInvalido, match="grande demais"):
        ler_export([solto])


def test_nenhum_arquivo():
    with pytest.raises(ExportInvalido):
        ler_export([])


# --- localizar ---------------------------------------------------------------------------------------------------------
def test_acha_o_export_mais_recente_e_ignora_zips_de_outras_coisas(tmp_path):
    _mtime(_zip(tmp_path / "conversations-000.zip", [_conv()]), AGORA)
    _mtime(_zip(tmp_path / "programa.zip", []), AGORA + 100, )
    (tmp_path / "foto.png").write_bytes(b"x")
    e = localizar_export([tmp_path])
    assert [a.name for a in e.arquivos] == ["conversations-000.zip"] and e.manifesto is None and e.faltando == []
    assert e.baixado_em.timestamp() == pytest.approx(AGORA)


def test_nada_para_importar_devolve_none(tmp_path):
    (tmp_path / "outro.zip").write_bytes(b"x")
    assert localizar_export([tmp_path]) is None
    assert localizar_export([tmp_path / "nao-existe"]) is None


def test_junta_as_partes_da_mesma_janela_e_deixa_de_fora_export_antigo(tmp_path):
    _mtime(_zip(tmp_path / "conversations-000.zip", [_conv()]), AGORA - 3 * 24 * 3600)  # export de dias atrás
    _mtime(_zip(tmp_path / "conversations-001.zip", [_conv("b")]), AGORA - 60)
    _mtime(_zip(tmp_path / "conversations-002.zip", [_conv("c")]), AGORA)
    e = localizar_export([tmp_path])
    assert [a.name for a in e.arquivos] == ["conversations-001.zip", "conversations-002.zip"]


def test_acha_json_solto_e_zip_antigo_so_se_tiver_conversas(tmp_path):
    _mtime(_zip(tmp_path / "data-sem-conversas.zip", [], entrada="outra.json"), AGORA + 50)
    _mtime(_zip(tmp_path / "data-2026-10-13.zip", [_conv()]), AGORA)
    e = localizar_export([tmp_path])
    assert [a.name for a in e.arquivos] == ["data-2026-10-13.zip"]
    solto = tmp_path / "conversations.json"
    solto.write_text("[]", encoding="utf-8")
    _mtime(solto, AGORA + 200)
    assert [a.name for a in localizar_export([tmp_path]).arquivos] == ["conversations.json"]


def _manifesto(pasta: Path, *partes, quando=AGORA):
    dados = {"instructions": "...", "created_at": "2026-11-15T21:13:12+00:00", "total_files": len(partes), "version": "1.0",
             "data_files": [{"batch_index": i, "export_url": "https://exemplo.invalido/nao-usar", "category": cat, "part": 0, "filename": nome}
                            for i, (cat, nome) in enumerate(partes)]}
    caminho = pasta / "manifest-teste.json"
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    return _mtime(caminho, quando)


def test_manifesto_aponta_parte_que_nao_foi_baixada(tmp_path):
    _mtime(_zip(tmp_path / "conversations-000.zip", [_conv()]), AGORA)
    _manifesto(tmp_path, ("conversations", "conversations-000.zip"), ("conversations", "conversations-001.zip"),
               ("memories", "memories-000.zip"))
    e = localizar_export([tmp_path])
    assert e.manifesto.name == "manifest-teste.json" and e.faltando == ["conversations-001.zip"]  # memories não conta


def test_manifesto_completo_ou_ilegivel(tmp_path):
    _mtime(_zip(tmp_path / "conversations-000.zip", [_conv()]), AGORA)
    _manifesto(tmp_path, ("conversations", "conversations-000.zip"), ("frames", "frames-000.zip"))
    assert localizar_export([tmp_path]).faltando == []
    _mtime((tmp_path / "manifest-teste.json"), AGORA)
    (tmp_path / "manifest-teste.json").write_text("{ quebrado", encoding="utf-8")
    _mtime((tmp_path / "manifest-teste.json"), AGORA)  # regravar muda a data: devolve para dentro da janela
    e = localizar_export([tmp_path])
    assert e.faltando == [] and "manifesto" in e.avisos[0]


def test_manifesto_mais_novo_sem_os_arquivos_avisa_que_o_export_esta_desatualizado(tmp_path):
    _mtime(_zip(tmp_path / "conversations-000.zip", [_conv()]), AGORA - 5 * 24 * 3600)
    _manifesto(tmp_path, ("conversations", "conversations-000.zip"), quando=AGORA)
    e = localizar_export([tmp_path])
    assert e.desatualizado and "Baixe os arquivos do export novo" in e.avisos[0]
    assert [a.name for a in e.arquivos] == ["conversations-000.zip"]  # ainda dá para importar o que existe


def test_export_em_dia_nao_avisa_nada(tmp_path):
    _mtime(_zip(tmp_path / "conversations-000.zip", [_conv()]), AGORA)
    _manifesto(tmp_path, ("conversations", "conversations-000.zip"), quando=AGORA - 3600)
    e = localizar_export([tmp_path])
    assert not e.desatualizado and e.avisos == []


def test_manifesto_de_outro_export_nao_vale(tmp_path):
    _mtime(_zip(tmp_path / "conversations-000.zip", [_conv()]), AGORA)
    _manifesto(tmp_path, ("conversations", "conversations-000.zip"), ("conversations", "conversations-001.zip"), quando=AGORA - 5 * 24 * 3600)
    e = localizar_export([tmp_path])
    assert e.manifesto is None and e.faltando == []


# --- do arquivo até o plano ------------------------------------------------------------------------------------------------
def test_do_export_ao_plano_de_importacao(tmp_path):
    longa = [_msg("human", f"Mensagem {i} do dono com bastante texto para passar do mínimo " + "a" * 60, f"2026-10-13T10:00:{i:02d}Z") for i in range(6)]
    curta = [_msg("human", "oi", "2026-10-13T10:00:00Z"), _msg("assistant", "olá", "2026-10-13T10:00:01Z")]
    z = _zip(tmp_path / "conversations-000.zip", [_conv("longa", msgs=longa), _conv("curta", msgs=curta)])
    estado = Importacoes(Banco(tmp_path / "eco.db"))
    r = ler_export([z])
    id_ = estado.registrar_importacao(z.name, r.hash, len(r.conversas))
    plano = filtro.planejar(r.conversas, estado, MODELO, 2.0)
    assert [c.uuid for c in plano.analisar] == ["longa"] and [c.uuid for c, _ in plano.ignoradas] == ["curta"]
    assert estado.importacoes()[0]["id"] == id_ and plano.estimativa.chamadas == 1
