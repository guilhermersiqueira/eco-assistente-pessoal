"""E0 (rede de segurança): backup do banco, saída à prova de caracteres, custo por categoria e supervisor."""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime

import pytest

from eco import backup
from eco.saida import ComHora, ajustar_codificacao, girar
from eco.supervisor import MAX_QUEDAS, vigiar
from eco.uso import FALA, GRAFO_ATUALIZACAO, SEM_CATEGORIA, TRANSCRICAO, RegistroUso, Uso, calcular_custo


# --- backup ---------------------------------------------------------------------------------------------------------
def _banco(arquivo):
    con = sqlite3.connect(str(arquivo))
    con.executescript(
        """
        CREATE TABLE ideias (id TEXT PRIMARY KEY, nome TEXT);
        CREATE TABLE ligacoes (a TEXT, b TEXT);
        CREATE VIRTUAL TABLE busca USING fts5(texto);
        INSERT INTO ideias VALUES ('1', 'Python'), ('2', 'Estudos ç ã ş');
        INSERT INTO ligacoes VALUES ('1', '2');
        INSERT INTO busca VALUES ('aprender python');
        """
    )
    con.commit()
    con.close()
    return arquivo


def test_backup_confere_e_restaura_sem_tocar_no_original(tmp_path):
    original = _banco(tmp_path / "eco.db")
    antes = original.read_bytes()
    copia = backup.fazer_backup(original, tmp_path / "backups", agora=datetime(2026, 11, 11, 23, 0))
    assert copia.name == "eco-20261111-230000-manual.db"
    c = backup.conferir(copia, original)
    assert c.ok and c.tabelas == {"busca": 1, "ideias": 2, "ligacoes": 1}
    restaurado = backup.restaurar(copia, tmp_path / "restaurado" / "eco.db")
    assert backup.conferir(restaurado, original).ok
    assert original.read_bytes() == antes  # o original não muda
    with pytest.raises(FileExistsError):  # nunca sobrescreve
        backup.restaurar(copia, original)


def test_conferencia_acusa_diferenca(tmp_path):
    original = _banco(tmp_path / "eco.db")
    copia = backup.fazer_backup(original, tmp_path / "b")
    con = sqlite3.connect(str(original))
    con.execute("UPDATE ideias SET nome = 'Outro' WHERE id = '1'")
    con.execute("INSERT INTO ligacoes VALUES ('2', '1')")
    con.commit()
    con.close()
    c = backup.conferir(copia, original)
    assert not c.ok
    assert any("ideias" in p and "conteúdo diferente" in p for p in c.problemas)
    assert any(p.startswith("ligacoes: cópia 1 linhas, original 2") for p in c.problemas)


def test_backups_no_mesmo_segundo_nao_se_sobrescrevem(tmp_path):
    original = _banco(tmp_path / "eco.db")
    t = datetime(2026, 1, 1)
    a = backup.fazer_backup(original, tmp_path / "b", agora=t)
    b = backup.fazer_backup(original, tmp_path / "b", agora=t)
    assert a != b and len(backup.backups(tmp_path / "b")) == 2


def test_backup_de_banco_inexistente_avisa(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup.fazer_backup(tmp_path / "nao-existe.db", tmp_path / "b")
    assert not (tmp_path / "nao-existe.db").exists()  # e não cria o arquivo


# --- saída ----------------------------------------------------------------------------------------------------------
def test_caractere_fora_do_cp1252_nao_derruba_o_print():
    bruto = io.BytesIO()
    fluxo = io.TextIOWrapper(bruto, encoding="cp1252")
    with pytest.raises(UnicodeEncodeError):  # o problema antigo
        fluxo.write("Você: nasıl sadeceşe\n")
        fluxo.flush()
    bruto2 = io.BytesIO()
    fluxo2 = io.TextIOWrapper(bruto2, encoding="cp1252")
    ajustar_codificacao(fluxo2)  # não é terminal: vira UTF-8
    fluxo2.write("Você: nasıl sadeceşe\n")
    fluxo2.flush()
    assert bruto2.getvalue().decode("utf-8").replace("\r\n", "\n") == "Você: nasıl sadeceşe\n"  # \r\n no Windows


def test_log_com_hora_no_comeco_de_cada_linha():
    destino = io.StringIO()
    log = ComHora(destino, relogio=lambda: datetime(2026, 11, 11, 23, 5, 9))
    log.write("Você: oi\nEco: ")
    log.write("olá\n\n")
    assert destino.getvalue() == "2026-11-11 23:05:09 Você: oi\n2026-11-11 23:05:09 Eco: olá\n\n"


def test_girar_log_guarda_tres_antigos(tmp_path):
    log = tmp_path / "eco.log"
    for i in range(5):
        log.write_text(f"rodada {i}" + "x" * 50, encoding="utf-8")
        assert girar(log, tamanho_maximo=10, guardar=3)
    assert not log.exists()
    assert (tmp_path / "eco.log.1").read_text(encoding="utf-8").startswith("rodada 4")
    assert (tmp_path / "eco.log.3").read_text(encoding="utf-8").startswith("rodada 2")
    assert not (tmp_path / "eco.log.4").exists()
    log.write_text("pouco", encoding="utf-8")
    assert not girar(log, tamanho_maximo=10)


# --- custo por categoria --------------------------------------------------------------------------------------------
def test_custo_por_categoria_e_volume_da_openai(tmp_path):
    r = RegistroUso(tmp_path / "uso.jsonl")
    t = datetime(2026, 11, 11, 10)
    r.registrar(Uso("claude-haiku-5-5", custo=0.002), t, categoria="voz")
    r.registrar(Uso("claude-haiku-5-5", custo=0.001), t, categoria=GRAFO_ATUALIZACAO)
    r.registrar(Uso("claude-sonnet-5-5", custo=0.03), t)  # antiga, sem categoria
    r.registrar_audio(TRANSCRICAO, "gpt-4o-mini-transcribe", segundos=12.34, preco_min=0.003, agora=t)
    r.registrar_audio(FALA, "gpt-4o-mini-tts", segundos=30.0, caracteres=120, preco_min=0.015, agora=t)
    cat = r.resumo_categorias(t)
    assert cat["voz"]["custo"] == pytest.approx(0.002) and cat[GRAFO_ATUALIZACAO]["chamadas"] == 1
    assert cat[SEM_CATEGORIA]["custo"] == pytest.approx(0.03)
    assert cat[TRANSCRICAO]["segundos"] == pytest.approx(12.3) and cat[FALA]["caracteres"] == 120
    assert cat[TRANSCRICAO]["custo"] == pytest.approx(12.34 / 60 * 0.003, abs=1e-6)
    assert cat[FALA]["custo"] == pytest.approx(0.0075)  # meio minuto a US$ 0,015/min
    assert set(r.resumo_mes(t)) == {"claude-haiku-5-5", "claude-sonnet-5-5"}  # áudio não entra como modelo do Claude
    openai = 12.34 / 60 * 0.003 + 0.0075
    assert r.total_openai_mes(t) == pytest.approx(openai, abs=1e-6)
    assert r.total_mes(t) == pytest.approx(0.033 + openai, abs=1e-6)  # o orçamento soma Claude e OpenAI
    ultima = json.loads((tmp_path / "uso.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert ultima["provedor"] == "openai" and ultima["segundos"] == 30.0


def test_fala_conta_os_segundos_de_voz_gerados(cfg_voz, monkeypatch):
    import wave

    from eco.voz import stt_tts

    def wav(segundos):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\0\0" * int(8000 * segundos))
        return buf.getvalue()

    duracoes = iter([1.5, 2.0])
    from types import SimpleNamespace

    cliente = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(
        create=lambda **kw: SimpleNamespace(read=lambda d=wav(next(duracoes)): d))))
    monkeypatch.setattr(stt_tts, "tocar_wav", lambda *a, **k: 1.0)
    texto = "Primeira frase bem comprida para não juntar. Segunda frase também comprida o bastante."
    fala = stt_tts.falar(cliente, texto, cfg_voz)
    assert fala.segundos_gerados == pytest.approx(3.5) and not fala.interrompida
    assert stt_tts.segundos_wav(b"") == 0.0 and stt_tts.segundos_wav(wav(0.25)) == pytest.approx(0.25)


def test_duracao_de_wav_de_transmissao_da_openai():
    # Regressão (11/11/2026): a voz da OpenAI vem com o tamanho "desconhecido" (0xFFFFFFFF) no cabeçalho; ler esse
    # campo dava ~50 horas por frase e US$ 45 por resposta na estimativa.
    import struct
    import wave

    from eco.voz.stt_tts import segundos_wav

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\0\0" * 24000 * 3)
    dados = bytearray(buf.getvalue())
    dados[4:8] = struct.pack("<I", 0xFFFFFFFF)
    i = dados.find(b"data")
    dados[i + 4 : i + 8] = struct.pack("<I", 0xFFFFFFFF)
    assert segundos_wav(bytes(dados)) == pytest.approx(3.0)


@pytest.fixture
def cfg_voz(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    from eco.config import carregar_config

    return carregar_config()


def test_precos_da_openai_vem_da_configuracao(cfg_voz, monkeypatch):
    assert cfg_voz.preco_transcricao_min == 0.003 and cfg_voz.preco_fala_min == 0.015
    monkeypatch.setenv("ECO_PRECO_FALA_MIN", "0.02")
    from eco.config import carregar_config

    assert carregar_config().preco_fala_min == 0.02


def test_leitura_do_cache_por_modelo():
    # Sonnet 5.5: leitura a 0,05 da entrada; Haiku 5.5: 0,1
    assert calcular_custo("claude-sonnet-5-5", 0, 0, leitura_cache=1_000_000) == pytest.approx(2.00 * 0.05)
    assert calcular_custo("claude-haiku-5-5", 0, 0, leitura_cache=1_000_000) == pytest.approx(0.10 * 0.1)


# --- supervisor -----------------------------------------------------------------------------------------------------
def test_supervisor_reabre_depois_de_queda_e_para_quando_fecham():
    codigos, esperas, rodou = iter([3221225477, 1, 0]), [], []  # access violation, erro, saída normal
    t = iter(range(0, 10_000, 100))
    fim = vigiar(["eco"], rodar=lambda c: rodou.append(c) or next(codigos), dormir=esperas.append,
                 relogio=lambda: next(t), avisar=lambda m: None)
    assert fim == 0 and len(rodou) == 3 and esperas == [3, 10]


def test_supervisor_desiste_depois_de_muitas_quedas_seguidas():
    avisos, esperas = [], []
    fim = vigiar(["eco"], rodar=lambda c: 1, dormir=esperas.append, relogio=lambda: 0.0, avisar=avisos.append)
    assert fim == 1 and len(esperas) == MAX_QUEDAS - 1 and "desisti" in avisos[-1]


def test_supervisor_esquece_quedas_antigas():
    codigos = iter([1] * 8 + [0])
    t = iter(range(0, 100_000, 1000))  # uma queda a cada ~17 min: nunca junta 5 na janela
    fim = vigiar(["eco"], rodar=lambda c: next(codigos), dormir=lambda s: None, relogio=lambda: next(t), avisar=lambda m: None)
    assert fim == 0
