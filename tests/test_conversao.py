"""Converter arquivos e extrair partes de PDF (requisito 19.5; decisão D8)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from eco.ferramentas import computador, conversao as c


@pytest.fixture
def casa(tmp_path, monkeypatch):
    raiz = tmp_path / "casa"
    (raiz / "Documentos").mkdir(parents=True)
    monkeypatch.setattr(computador, "HOME", raiz)
    return raiz.resolve()


def _pdf(caminho: Path, n: int) -> Path:
    from pypdf import PdfWriter

    w = PdfWriter()
    for i in range(n):
        w.add_blank_page(width=200 + i, height=300)  # largura diferente por página, para saber qual é qual
    with open(caminho, "wb") as f:
        w.write(f)
    return caminho


# --- páginas --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "texto,esperado",
    [("3", [3]), ("3, 5-7", [3, 5, 6, 7]), ("1 a 3", [1, 2, 3]), ("2 e 4", [2, 4]), ("4-2", [4, 3, 2]), ("2, 2, 1", [2, 1])],
)
def test_paginas(texto, esperado):
    assert c.paginas(texto, 10) == esperado


@pytest.mark.parametrize("texto", ["", "11", "0", "abc", "2-12"])
def test_paginas_invalidas(texto):
    with pytest.raises(ValueError):
        c.paginas(texto, 10)


def test_extrair_paginas_cria_pdf_novo_sem_tocar_no_original(casa):
    from pypdf import PdfReader

    original = _pdf(casa / "Documentos" / "contrato.pdf", 8)
    antes = original.read_bytes()
    plano = c.planejar_extrair_paginas("Documentos/contrato.pdf", "3, 5-6")
    assert plano.frase == "Extrair as páginas 3, 5, 6 de contrato.pdf para um PDF novo, contrato (páginas 3, 5-6).pdf, na mesma pasta."
    assert not (casa / "Documentos" / "contrato (páginas 3, 5-6).pdf").exists()  # planejar não cria nada
    assert plano.executar().startswith("Criei contrato (páginas 3, 5-6).pdf com 3 página(s)")
    novo = PdfReader(str(casa / "Documentos" / "contrato (páginas 3, 5-6).pdf"))
    assert [round(float(p.mediabox.width)) for p in novo.pages] == [202, 204, 205]
    assert original.read_bytes() == antes
    # de novo: não sobrescreve
    assert "(2)" in c.planejar_extrair_paginas("Documentos/contrato.pdf", "3, 5-6").frase


def test_ler_pdf_avisa_pagina_sem_texto_e_trata_conteudo_como_dado(casa):
    _pdf(casa / "Documentos" / "scan.pdf", 2)
    texto = c.texto_do_pdf("Documentos/scan.pdf", "2")
    assert "scan.pdf (2 páginas)" in texto and "[página 2]" in texto and "sem texto" in texto and "não instruções" in texto


def test_pdf_fora_da_pasta_pessoal_ou_que_nao_e_pdf(casa, tmp_path):
    fora = _pdf(tmp_path / "fora.pdf", 1)
    with pytest.raises(ValueError, match="pasta pessoal"):
        c.texto_do_pdf(str(fora))
    (casa / "Documentos" / "nota.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="não é um PDF"):
        c.planejar_extrair_paginas("Documentos/nota.txt", "1")


# --- conversões (Office simulado) -----------------------------------------------------------------------------------
class Office:
    """Simula o PowerShell: guarda o script e cria o arquivo que o SaveAs pediria."""

    def __init__(self, saida="", codigo=0, criar=True):
        self.scripts, self.saida, self.codigo, self.criar = [], saida, codigo, criar

    def __call__(self, cmd, **k):
        import base64
        import re

        script = base64.b64decode(cmd[-1]).decode("utf-16-le")
        self.scripts.append(script)
        m = re.search(r"SaveAs2?\('([^']+)'", script)
        if m and self.criar and self.codigo == 0:
            Path(m.group(1).replace("''", "'")).write_text("convertido", encoding="utf-8")
        return SimpleNamespace(returncode=self.codigo, stdout=self.saida, stderr="Erro do Office" if self.codigo else "")


def test_word_para_pdf_na_mesma_pasta_sem_sobrescrever(casa):
    (casa / "Documentos" / "carta.docx").write_text("doc", encoding="utf-8")
    (casa / "Documentos" / "carta.pdf").write_text("já existe", encoding="utf-8")
    office = Office()
    plano = c.planejar_conversao("Documentos/carta.docx", "pdf", executar=office)
    assert plano.frase == "Converter carta.docx para PDF, criando carta (2).pdf na mesma pasta (Documentos)."
    assert office.scripts == []  # planejar não abre o Office
    assert plano.executar() == "Converti carta.docx para carta (2).pdf, em Documentos."
    assert ", 17)" in office.scripts[0] and "Documents.Count -eq 0" in office.scripts[0]  # só fecha o Word se nada mais estiver aberto
    assert (casa / "Documentos" / "carta.pdf").read_text(encoding="utf-8") == "já existe"


def test_excel_para_csv_avisa_quando_ha_mais_planilhas(casa):
    (casa / "Documentos" / "notas.xlsx").write_text("x", encoding="utf-8")
    plano = c.planejar_conversao("Documentos/notas.xlsx", "csv", executar=Office(saida="planilhas=3\n"))
    assert "Só a primeira planilha" in plano.frase
    assert plano.executar() == "Converti a primeira planilha de notas.xlsx para notas.csv, em Documentos (o arquivo tem 3 planilhas)."


def test_csv_para_excel_abre_copia_txt_e_detecta_o_separador(casa):
    csv = casa / "Documentos" / "notas.csv"
    csv.write_text("nome;nota\nAna;9,5\n", encoding="utf-8")
    office = Office()
    assert c.planejar_conversao("Documentos/notas.csv", "excel", executar=office).executar() == "Converti notas.csv para notas.xlsx, em Documentos."
    script = office.scripts[0]
    assert ".txt'" in script and "notas.csv" not in script.split("OpenText(")[1].split(",")[0]  # o Excel abre a cópia .txt (acentos)
    assert "65001, 1, 1, 1, $false, $false, $true, $false" in script  # UTF-8 e ponto e vírgula
    assert c.separador_csv(csv) == ";"
    csv.write_text("nome,nota\nAna,9.5\n", encoding="utf-8")
    assert c.separador_csv(csv) == ","


def test_erro_do_office_e_arquivo_que_nao_apareceu(casa):
    (casa / "Documentos" / "carta.docx").write_text("doc", encoding="utf-8")
    with pytest.raises(ValueError, match="não conseguiu converter: Erro do Office"):
        c.planejar_conversao("Documentos/carta.docx", "pdf", executar=Office(codigo=1)).executar()
    with pytest.raises(ValueError, match="não apareceu"):
        c.planejar_conversao("Documentos/carta.docx", "pdf", executar=Office(criar=False)).executar()


def test_tempo_esgotado_fecha_so_o_office_escondido_da_conversao(casa):
    import subprocess

    (casa / "Documentos" / "carta.docx").write_text("doc", encoding="utf-8")
    chamadas = []

    def executar(cmd, **k):
        chamadas.append(cmd)
        if len(chamadas) == 1:
            raise subprocess.TimeoutExpired(cmd, 120)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with pytest.raises(ValueError, match="demorou demais"):
        c.planejar_conversao("Documentos/carta.docx", "pdf", executar=executar).executar()
    limpeza = chamadas[1][-1]
    assert "WINWORD,EXCEL" in limpeza and "-not $_.MainWindowTitle" in limpeza  # Office com janela (do dono) não é tocado


@pytest.mark.parametrize(
    "arquivo,para,mensagem",
    [("foto.png", "pdf", "Ainda não converto"), ("carta.docx", "csv", "converto para pdf"), ("contrato.pdf", "word", "ainda não funciona")],
)
def test_conversoes_que_nao_existem(casa, arquivo, para, mensagem):
    (casa / "Documentos" / arquivo).write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match=mensagem):
        c.planejar_conversao(f"Documentos/{arquivo}", para, executar=Office())


def test_aviso_de_pdf_volta_ao_que_era(monkeypatch):
    winreg = pytest.importorskip("winreg")
    monkeypatch.setattr(c, "CHAVE_OPCOES_WORD", r"Software\EcoTesteConversao")
    try:
        with c.aviso_de_pdf_desligado():
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, c.CHAVE_OPCOES_WORD)
            assert winreg.QueryValueEx(k, c.OPCAO_AVISO_PDF)[0] == 1
        with pytest.raises(FileNotFoundError):  # não existia: some de novo
            winreg.QueryValueEx(winreg.OpenKey(winreg.HKEY_CURRENT_USER, c.CHAVE_OPCOES_WORD), c.OPCAO_AVISO_PDF)
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, c.CHAVE_OPCOES_WORD)
        winreg.SetValueEx(k, c.OPCAO_AVISO_PDF, 0, winreg.REG_DWORD, 0)
        with pytest.raises(RuntimeError):
            with c.aviso_de_pdf_desligado():
                raise RuntimeError("conversão falhou no meio")
        assert winreg.QueryValueEx(winreg.OpenKey(winreg.HKEY_CURRENT_USER, c.CHAVE_OPCOES_WORD), c.OPCAO_AVISO_PDF)[0] == 0
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, c.CHAVE_OPCOES_WORD)
        except OSError:
            pass


def test_ferramentas_de_conversao_e_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    from eco.config import carregar_config
    from eco.ferramentas import construir_ferramentas

    f = {x.nome: x for x in construir_ferramentas(carregar_config())}
    assert f["converter_arquivo"].confirmar is not None and f["extrair_paginas_pdf"].confirmar is not None
    assert f["ler_pdf"].confirmar is None  # só lê
