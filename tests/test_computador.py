"""Ações no computador: apps, sites, volume/brilho/mídia e arquivos (só na pasta pessoal, com confirmação para alterar)."""

from __future__ import annotations

import pytest

from eco.ferramentas import computador


@pytest.fixture
def casa(tmp_path, monkeypatch):
    c = tmp_path / "casa"
    for pasta in ("OneDrive/Documentos/Faculdade", "Downloads", "Imagens", ".ssh", "AppData/Local"):
        (c / pasta).mkdir(parents=True)
    (c / "Downloads" / "relatorio final.pdf").write_text("pdf", encoding="utf-8")
    (c / "Downloads" / "foto.png").write_text("png", encoding="utf-8")
    (c / ".ssh" / "id_rsa").write_text("segredo", encoding="utf-8")
    monkeypatch.setattr(computador, "HOME", c)
    return c.resolve()


# --- apps e sites ----------------------------------------------------------------------------------------------
APPS = [("Spotify", "SpotifyAB.Spotify_x!Spotify"), ("Microsoft Edge", "edge!App"), ("Microsoft Word", "word!App"), ("Microsoft Excel", "excel!App")]


def test_escolher_app_exato_comeca_contem_e_ambiguo():
    assert computador.escolher_app("spotify", APPS)[0] == "Spotify"
    assert computador.escolher_app("edge", APPS)[0] == "Microsoft Edge"  # só um contém
    assert computador.escolher_app("Word", APPS)[0] == "Microsoft Word"
    with pytest.raises(ValueError, match="Mais de um app"):
        computador.escolher_app("microsoft", APPS)
    assert computador.escolher_app("spotify", [*APPS, ("Spotify", "outro!Id")])[0] == "Spotify"  # entrada duplicada não é ambiguidade
    with pytest.raises(ValueError, match="Não achei"):
        computador.escolher_app("photoshop", APPS)
    with pytest.raises(ValueError, match="nome do app"):
        computador.escolher_app("  ", APPS)


def test_abrir_app_chama_o_explorer_com_o_appid_e_recarrega_a_lista_se_nao_achar(monkeypatch):
    lancados, cargas = [], []

    def apps(recarregar=False):
        cargas.append(recarregar)
        return APPS if recarregar else [("Spotify", "s!S")]

    monkeypatch.setattr(computador, "_apps_instalados", apps)
    monkeypatch.setattr(computador.subprocess, "Popen", lambda args, **kw: lancados.append(args))
    monkeypatch.setattr(computador, "_exigir_windows", lambda: None)
    assert computador.abrir_app("spotify") == "Abrindo Spotify."
    assert lancados[-1] == ["explorer.exe", "shell:AppsFolder\\s!S"]
    assert computador.abrir_app("excel") == "Abrindo Microsoft Excel." and cargas == [False, False, True]
    with pytest.raises(ValueError, match="Mais de um"):
        computador.abrir_app("microsoft")  # erro de ambiguidade não recarrega nem abre
    assert len(lancados) == 2


def test_abrir_site_so_http_e_https(monkeypatch):
    abertos = []
    monkeypatch.setattr(computador.webbrowser, "open", abertos.append)
    assert computador.abrir_site("usp.edu.br") == "Abrindo usp.edu.br."
    assert computador.abrir_site("http://exemplo.com/a?b=1") == "Abrindo exemplo.com."
    assert abertos == ["https://usp.edu.br", "http://exemplo.com/a?b=1"]
    for ruim in ("file:///C:/Windows/System32/cmd.exe", "javascript:alert(1)", "ms-settings:network", "https://banco.com@malicioso.ru", "dois sites.com", "", "localhost", "ftp://x.com"):
        with pytest.raises(ValueError):
            computador.abrir_site(ruim)
    assert len(abertos) == 2


# --- volume, mídia, brilho -------------------------------------------------------------------------------------
@pytest.fixture
def teclas(monkeypatch):
    apertadas = []
    monkeypatch.setattr(computador, "_tecla", apertadas.append)
    return apertadas


def test_volume_mais_menos_e_definir(teclas):
    assert "10%" in computador.ajustar("volume_mais")
    assert teclas == [computador.TECLAS["volume_mais"]] * 5
    teclas.clear()
    computador.ajustar("volume_menos", 20)
    assert teclas == [computador.TECLAS["volume_menos"]] * 10
    teclas.clear()
    assert computador.ajustar("volume_definir", 30) == "Volume em cerca de 30%."
    assert teclas == [computador.TECLAS["volume_menos"]] * 50 + [computador.TECLAS["volume_mais"]] * 15
    teclas.clear()
    computador.ajustar("volume_definir", 500)  # limita a 100
    assert teclas.count(computador.TECLAS["volume_mais"]) == 50
    with pytest.raises(ValueError):
        computador.ajustar("volume_definir")


def test_midia(teclas):
    for acao, msg in (("mudo", "mudo"), ("proxima", "Próxima"), ("anterior", "anterior"), ("pausar_tocar", "Pausei")):
        assert msg in computador.ajustar(acao)
    assert teclas == [computador.TECLAS[a] for a in ("mudo", "proxima", "anterior", "pausar_tocar")]
    with pytest.raises(ValueError, match="desconhecida"):
        computador.ajustar("explodir")


def test_brilho_relativo_absoluto_e_nunca_zera(monkeypatch):
    estado = {"v": 50}
    monkeypatch.setattr(computador, "brilho_atual", lambda: estado["v"])
    monkeypatch.setattr(computador, "_definir_brilho", lambda v: estado.update(v=v))
    assert computador.ajustar("brilho_mais") == "Brilho em 60%."
    assert computador.ajustar("brilho_menos", 30) == "Brilho em 30%."
    assert computador.ajustar("brilho_definir", 0) == "Brilho em 5%."
    assert computador.ajustar("brilho_definir", 250) == "Brilho em 100%."


def test_brilho_em_monitor_externo_da_erro_claro(monkeypatch):
    monkeypatch.setattr(computador, "_powershell", lambda c, limite=30.0: "")
    with pytest.raises(ValueError, match="monitor externo"):
        computador.brilho_atual()


# --- caminhos --------------------------------------------------------------------------------------------------
def test_resolver_apelidos_relativos_e_absolutos(casa):
    assert computador.resolver("Documentos/Faculdade") == casa / "OneDrive" / "Documentos" / "Faculdade"
    assert computador.resolver("downloads") == casa / "Downloads"
    assert computador.resolver("Imagens") == casa / "Imagens"
    assert computador.resolver("~/Downloads/foto.png") == casa / "Downloads" / "foto.png"
    assert computador.resolver(str(casa / "Downloads")) == casa / "Downloads"
    assert computador.resolver('"Downloads/relatorio final.pdf"') == casa / "Downloads" / "relatorio final.pdf"


@pytest.mark.parametrize("ruim", ["C:/Windows/System32", "../fora", "Downloads/../../fora", ".ssh", ".ssh/id_rsa", "AppData/Local", "appdata", "", "   "])
def test_resolver_recusa_fora_da_casa_e_pastas_de_configuracao(casa, ruim):
    with pytest.raises(ValueError):
        computador.resolver(ruim)


def test_resolver_recusa_ntuser(casa):
    (casa / "NTUSER.DAT").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="configurações"):
        computador.resolver("NTUSER.DAT")


def test_listar_e_buscar(casa):
    saida = computador.listar("Downloads")
    assert saida.startswith("Pasta Downloads (2 itens):") and "foto.png" in saida and "relatorio final.pdf" in saida
    assert "Faculdade/" in computador.listar("Documentos") and ".ssh" not in computador.listar("~")
    assert "A pasta Imagens está vazia." == computador.listar("Imagens")
    with pytest.raises(ValueError, match="não é uma pasta"):
        computador.listar("Downloads/foto.png")
    achou = computador.buscar("RELATÓRIO final")
    assert achou.replace("\\", "/") == "Downloads/relatorio final.pdf"
    assert computador.buscar("segredo") .startswith("Não achei") and computador.buscar("id_rsa").startswith("Não achei")  # .ssh fica de fora
    assert computador.buscar("foto", "Downloads").replace("\\", "/") == "Downloads/foto.png"


# --- alterar arquivos: sempre um Plano (frase para confirmar + execução) ---------------------------------------
def test_criar_pasta(casa):
    plano = computador.planejar("criar_pasta", "Documentos/Faculdade/Cálculo")
    assert plano.frase == "Criar a pasta Cálculo dentro de Faculdade."
    assert not (casa / "OneDrive/Documentos/Faculdade/Cálculo").exists()  # planejar não mexe em nada
    assert plano.executar() == "Pasta Cálculo criada." and (casa / "OneDrive/Documentos/Faculdade/Cálculo").is_dir()
    with pytest.raises(ValueError, match="já existe"):
        computador.planejar("criar_pasta", "Documentos/Faculdade")


def test_copiar_nao_sobrescreve(casa):
    copiar = computador.planejar("copiar", "Downloads/foto.png", "Imagens")
    assert copiar.frase == "Copiar foto.png, de Downloads, para Imagens."
    copiar.executar()
    assert (casa / "Downloads/foto.png").exists() and (casa / "Imagens/foto.png").exists()
    with pytest.raises(ValueError, match="não sobrescrevo"):
        computador.planejar("copiar", "Downloads/foto.png", "Imagens")
    with pytest.raises(ValueError, match="destino"):
        computador.planejar("copiar", "Downloads/foto.png", "Documentos/NaoExiste")
    with pytest.raises(ValueError, match="Diga para qual pasta"):
        computador.planejar("copiar", "Downloads/foto.png")


@pytest.mark.parametrize("acao", ["mover", "apagar"])
def test_mover_e_apagar_foram_retirados(casa, acao):
    # requisito 19.5 do dono (decisão D1): as ferramentas saíram; nada é tocado
    with pytest.raises(ValueError, match="não fazem mais parte"):
        computador.planejar(acao, "Downloads/foto.png", "Imagens")
    assert (casa / "Downloads/foto.png").exists()
    assert acao not in computador.ACOES_ARQUIVO and not hasattr(computador, "_lixeira")


def test_copiar_pasta_inteira_e_nao_para_dentro_dela_mesma(casa):
    (casa / "Downloads" / "projeto").mkdir()
    (casa / "Downloads" / "projeto" / "a.txt").write_text("a", encoding="utf-8")
    computador.planejar("copiar", "Downloads/projeto", "Imagens").executar()
    assert (casa / "Imagens/projeto/a.txt").read_text(encoding="utf-8") == "a"
    (casa / "Downloads/projeto/sub").mkdir()
    with pytest.raises(ValueError, match="dentro dela mesma"):
        computador.planejar("copiar", "Downloads/projeto", "Downloads/projeto/sub")


def test_renomear(casa):
    plano = computador.planejar("renomear", "Downloads/foto.png", "viagem.png")
    assert plano.frase == "Renomear foto.png para viagem.png."
    plano.executar()
    assert (casa / "Downloads/viagem.png").exists() and not (casa / "Downloads/foto.png").exists()
    for ruim in ("", "..", "a/b.png", "x:y", ".oculto", "relatorio final.pdf"):
        with pytest.raises(ValueError):
            computador.planejar("renomear", "Downloads/viagem.png", ruim)


@pytest.mark.parametrize("item", ["Downloads", "Documentos", "~", "Imagens", "OneDrive"])
def test_pastas_principais_nao_podem_ser_renomeadas(casa, item):
    with pytest.raises(ValueError):
        computador.planejar("renomear", item, "outro")


def test_arquivo_inexistente(casa):
    with pytest.raises(ValueError, match="Não encontrei"):
        computador.planejar("renomear", "Downloads/nada.txt", "outro.txt")


# --- ferramentas (confirmação antes de alterar) ------------------------------------------------------------------
def _ferramentas(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    from eco.config import carregar_config
    from eco.ferramentas import construir_ferramentas

    return {f.nome: f for f in construir_ferramentas(carregar_config())}


def test_mexer_em_arquivo_exige_confirmacao_e_age_no_mesmo_plano(casa, tmp_path, monkeypatch):
    f = _ferramentas(tmp_path, monkeypatch)["mexer_em_arquivo"]
    assert f.confirmar is not None
    a = {"acao": "copiar", "caminho": "Downloads/foto.png", "destino": "Imagens"}
    assert f.confirmar(a) == "Copiar foto.png, de Downloads, para Imagens."
    assert not (casa / "Imagens/foto.png").exists()  # confirmar não mexe
    assert f.executar(a) == "Copiei foto.png para Imagens." and (casa / "Imagens/foto.png").exists()
    with pytest.raises(ValueError):  # erro na preparação vira mensagem ao Claude, sem pedir o "sim"
        f.confirmar({"acao": "renomear", "caminho": "Downloads", "destino": "x"})
    assert f.definicao()["input_schema"]["properties"]["acao"]["enum"] == ["criar_pasta", "copiar", "renomear"]


def test_ferramentas_de_abrir_e_ajustar_nao_pedem_confirmacao(tmp_path, monkeypatch):
    ferramentas = _ferramentas(tmp_path, monkeypatch)
    for nome in ("abrir_no_computador", "ajustar_som_e_tela", "ver_arquivos"):
        assert nome in ferramentas and ferramentas[nome].confirmar is None
    abertos = []
    monkeypatch.setattr(computador.webbrowser, "open", abertos.append)
    assert ferramentas["abrir_no_computador"].executar({"tipo": "site", "alvo": "usp.edu.br"}) == "Abrindo usp.edu.br."
    assert abertos == ["https://usp.edu.br"]
