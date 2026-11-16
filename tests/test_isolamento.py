"""Os testes nunca usam a pasta real ~/.eco (lição de 12/11/2026: uma tela criada sem banco abriu o eco.db real)."""

from pathlib import Path


def test_pasta_da_eco_nos_testes_e_temporaria(pasta_eco_de_teste):
    from eco import backup, config, principal

    real = Path.home() / ".eco"
    for caminho in (config.PASTA_ECO, backup.BANCO.parent, backup.PASTA_BACKUPS.parent, principal.ARQUIVO_USO.parent):
        assert caminho != real and real not in caminho.parents


def test_tela_criada_sem_banco_nao_abre_o_banco_real(pasta_eco_de_teste, monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    from PySide6.QtWidgets import QApplication

    from eco.config import carregar_config
    from eco.visual.grafo import PainelGrafo

    QApplication.instance() or QApplication([])
    p = PainelGrafo(carregar_config(), confirmar=lambda t: False)
    assert p.banco.arquivo == pasta_eco_de_teste / "eco.db"
