import pytest


@pytest.fixture(autouse=True)
def sem_ligacao_automatica_real(monkeypatch):
    """A ligação automática (E6) chama o Haiku: nos testes ela fica desligada, a menos que o teste a ligue com um
    completador falso."""
    monkeypatch.setenv("ECO_LIGACAO_AUTO", "nao")
    monkeypatch.setenv("ECO_MEMORIA_CONFERIR", "nao")  # E7: a conferência ao guardar também chama o Haiku
    monkeypatch.setenv("ECO_HIGIENE_DIAS", "0")


@pytest.fixture(autouse=True)
def sem_google_real(tmp_path, monkeypatch):
    """Nenhum teste fala com a conta Google de verdade: sem token, qualquer chamada não simulada falha na hora."""
    from eco.ferramentas import google

    monkeypatch.setattr(google, "TOKEN", tmp_path / "sem-token.json")


# Mesma regra da Eco (`visual/janela.instalar_coletor`): o coletor de lixo automático do Python, disparado numa thread
# de trabalho (carga do Painel, análise do grafo), pode destruir objetos do Qt fora da thread deles e derrubar o
# processo ("Windows fatal exception: access violation" em "Garbage-collecting"). Nos testes, a coleta roda só aqui,
# na thread principal, entre um teste e outro.
def pytest_configure(config):
    import gc

    gc.disable()


@pytest.fixture(autouse=True)
def _coleta_na_thread_principal():
    yield
    import gc

    gc.collect(1)  # geração jovem: barata; o resto é liberado no fim da rodada


# Nenhum teste toca a pasta real ~/.eco (banco, uso, lembretes, log, backups). Em 12/11/2026 telas criadas sem banco
# abriram o eco.db real por padrão (Banco(PASTA_ECO / "eco.db")) e gravaram nele. Aqui, todo módulo que guardou o
# caminho na importação passa a apontar para uma pasta temporária do próprio teste.
@pytest.fixture(autouse=True)
def pasta_eco_de_teste(tmp_path_factory, monkeypatch):
    import importlib

    falsa = tmp_path_factory.mktemp("eco-home") / ".eco"
    falsa.mkdir()
    trocas = {
        "eco.config": {"PASTA_ECO": falsa},
        "eco.backup": {"BANCO": falsa / "eco.db", "PASTA_BACKUPS": falsa / "backups"},
        "eco.principal": {"PASTA_ECO": falsa, "ARQUIVO_USO": falsa / "uso.jsonl", "ARQUIVO_LEMBRETES": falsa / "lembretes.json"},
        "eco.visual.chat": {"PASTA_ECO": falsa},
        "eco.visual.grafo": {"PASTA_ECO": falsa},
        "eco.visual.janela": {"PASTA_ECO": falsa},
        "eco.ferramentas.google": {"CREDENCIAIS": falsa / "google_credentials.json"},
        "eco.grafo.semelhanca": {"PASTA_MODELOS": falsa / "modelos"},  # testes com o modelo real o pedem explicitamente
    }
    for modulo, valores in trocas.items():
        try:
            m = importlib.import_module(modulo)
        except ImportError:
            continue
        for nome, valor in valores.items():
            monkeypatch.setattr(m, nome, valor, raising=False)
    return falsa
