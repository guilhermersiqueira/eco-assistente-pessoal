"""Testes do chat: espaços, troca de espaço, histórico salvo, interrupção da resposta, busca e fala."""

import threading
import time
from types import SimpleNamespace

import pytest

from eco.chat.armazenamento import GERAL, TODOS, Banco
from eco.chat.fala import AVISO_CODIGO, DivisorDeFala, Narrador
from eco.chat.render import renderizar_conversa
from eco.chat.servico import MARCA_INTERROMPIDA, ServicoChat, ler_arquivo_contexto
from eco.config import carregar_config


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    return carregar_config()


@pytest.fixture
def banco(tmp_path):
    b = Banco(tmp_path / "eco.db")
    yield b
    b.fechar()


class ProvedorFalso:
    def __init__(self, pedacos=("Olá, ", "tudo bem?"), atraso=0.0, falhar_apos=None):
        self.pedacos, self.atraso, self.falhar_apos = list(pedacos), atraso, falhar_apos
        self.chamadas = []
        self.primeiro = threading.Event()

    def transmitir(self, sistema, mensagens, parar):
        self.chamadas.append((sistema, [dict(m) for m in mensagens]))
        for i, p in enumerate(self.pedacos):
            if parar.is_set():
                return
            if self.falhar_apos is not None and i >= self.falhar_apos:
                raise ConnectionError("a conexão caiu")
            yield p
            self.primeiro.set()
            time.sleep(self.atraso)


def _conversar(servico, conversa_id, texto, **kw):
    g = servico.iniciar(conversa_id, texto, **kw)
    assert g.concluida.wait(5)
    return g


# --- espaços --------------------------------------------------------------------------------
def test_criar_espaco_com_nome_cor_e_instrucoes(banco):
    e = banco.criar_espaco("  Investimentos ", "#ffaa00", "  Aja como analista conservador.  ")
    assert (e.nome, e.cor, e.instrucoes) == ("Investimentos", "#ffaa00", "Aja como analista conservador.")
    assert banco.espaco(e.id) == e
    banco.criar_espaco("Estudos")
    assert [x.nome for x in banco.listar_espacos()] == ["Estudos", "Investimentos"]
    banco.atualizar_espaco(e.id, nome="Finanças", instrucoes="Outra instrução")
    assert banco.espaco(e.id).nome == "Finanças" and banco.espaco(e.id).cor == "#ffaa00"


def test_espaco_exige_nome_unico_sem_diferenciar_acento_ou_caixa(banco):
    banco.criar_espaco("Leitura")
    with pytest.raises(ValueError, match="nome"):
        banco.criar_espaco("   ")
    with pytest.raises(ValueError, match="Já existe"):
        banco.criar_espaco("leitura")
    outro = banco.criar_espaco("Reunião")
    with pytest.raises(ValueError, match="Já existe"):
        banco.atualizar_espaco(outro.id, nome="LEITURA")
    with pytest.raises(ValueError, match="Já existe"):
        banco.criar_espaco("reuniao")


def test_conversas_ficam_no_espaco_ou_na_area_geral(banco):
    a = banco.criar_espaco("Trabalho")
    c1, c2 = banco.criar_conversa(a.id), banco.criar_conversa(GERAL)
    assert [c.id for c in banco.listar_conversas(a.id)] == [c1.id]
    assert [c.id for c in banco.listar_conversas(GERAL)] == [c2.id]
    with pytest.raises(KeyError):
        banco.criar_conversa("espaco-que-nao-existe")


def test_mover_conversa_entre_espacos_sem_perder_o_historico(banco):
    a, b = banco.criar_espaco("A"), banco.criar_espaco("B")
    c = banco.criar_conversa(a.id)
    banco.adicionar_mensagem(c.id, "user", "primeira")
    banco.adicionar_mensagem(c.id, "assistant", "segunda", interrompida=True)
    antes = banco.mensagens(c.id)
    banco.mover_conversa(c.id, b.id)
    assert banco.listar_conversas(a.id) == [] and [x.id for x in banco.listar_conversas(b.id)] == [c.id]
    assert banco.mensagens(c.id) == antes  # mesmas mensagens, mesmos ids, mesma marca de interrompida
    banco.mover_conversa(c.id, GERAL)
    assert banco.conversa(c.id).espaco_id is None and banco.mensagens(c.id) == antes
    with pytest.raises(KeyError):
        banco.mover_conversa(c.id, "nao-existe")


def test_trocar_de_espaco_mostra_so_as_conversas_dele(banco):
    a, b = banco.criar_espaco("A"), banco.criar_espaco("B")
    ca1, ca2, cb = banco.criar_conversa(a.id, "a1"), banco.criar_conversa(a.id, "a2"), banco.criar_conversa(b.id, "b1")
    assert {c.titulo for c in banco.listar_conversas(a.id)} == {"a1", "a2"}
    assert [c.titulo for c in banco.listar_conversas(b.id)] == ["b1"]
    banco.adicionar_mensagem(ca1.id, "user", "oi")  # a mais recente sobe na lista
    assert banco.listar_conversas(a.id)[0].id == ca1.id
    assert cb.id not in [c.id for c in banco.listar_conversas(a.id)] and ca2


def test_excluir_espaco_nao_perde_conversas_e_apaga_seus_arquivos(banco):
    a = banco.criar_espaco("Temporário")
    banco.adicionar_arquivo(a.id, "nota.md", "conteúdo")
    c = banco.criar_conversa(a.id)
    banco.adicionar_mensagem(c.id, "user", "guarda isso")
    banco.excluir_espaco(a.id)
    assert banco.conversa(c.id).espaco_id is None  # foi para a área geral
    assert [m.conteudo for m in banco.mensagens(c.id)] == ["guarda isso"]
    assert banco.listar_espacos() == []


def test_excluir_conversa_apaga_mensagens_e_some_da_busca(banco):
    c = banco.criar_conversa()
    banco.adicionar_mensagem(c.id, "user", "palavra rara zumbido")
    assert banco.buscar("zumbido")
    banco.excluir_conversa(c.id)
    assert banco.mensagens(c.id) == [] and banco.buscar("zumbido") == []


# --- histórico salvo e streaming ------------------------------------------------------------------
def test_resposta_em_streaming_e_historico_salvo_no_disco(tmp_path, cfg):
    arquivo = tmp_path / "eco.db"
    banco = Banco(arquivo)
    esp = banco.criar_espaco("Estudos")
    conversa = banco.criar_conversa(esp.id)
    pedacos, fim = [], []
    servico = ServicoChat(banco, ProvedorFalso(["Olá, ", "tudo ", "bem?"]), cfg)
    g = _conversar(servico, conversa.id, "Oi, Eco!", ao_pedaco=pedacos.append, ao_fim=fim.append)
    assert pedacos == ["Olá, ", "tudo ", "bem?"] and g.mensagem is not None and fim == [g.mensagem]
    assert banco.conversa(conversa.id).titulo == "Oi, Eco!"  # título automático
    banco.fechar()
    reaberto = Banco(arquivo)  # fechou e abriu o app: está tudo lá
    ms = reaberto.mensagens(conversa.id)
    assert [(m.papel, m.conteudo, m.interrompida) for m in ms] == [("user", "Oi, Eco!", False), ("assistant", "Olá, tudo bem?", False)]
    assert reaberto.espaco(esp.id).nome == "Estudos"
    reaberto.fechar()


def test_segunda_pergunta_leva_o_historico_para_o_modelo(banco, cfg):
    prov = ProvedorFalso(["ok"])
    servico = ServicoChat(banco, prov, cfg)
    c = banco.criar_conversa()
    _conversar(servico, c.id, "primeira pergunta")
    _conversar(servico, c.id, "segunda pergunta")
    enviado = prov.chamadas[1][1]
    assert [(m["role"], m["content"]) for m in enviado] == [
        ("user", "primeira pergunta"), ("assistant", "ok"), ("user", "segunda pergunta"),
    ]


def test_prompt_do_espaco_leva_instrucoes_arquivos_e_memoria(banco, cfg):
    from eco.ferramentas import memoria

    memoria.lembrar(cfg.pasta_dados, "O dono se chama Vitor.")
    e = banco.criar_espaco("Investimentos", instrucoes="Assuma o papel de analista conservador.")
    banco.adicionar_arquivo(e.id, "carteira.md", "PETR4: 10 ações")
    prov = ProvedorFalso(["ok"])
    servico = ServicoChat(banco, prov, cfg)
    _conversar(servico, banco.criar_conversa(e.id).id, "como está minha carteira?")
    sistema = prov.chamadas[0][0]
    assert 'espaço "Investimentos"' in sistema and "analista conservador" in sistema
    assert "carteira.md" in sistema and "PETR4: 10 ações" in sistema and "Vitor" in sistema
    # área geral: sem bloco de espaço
    prov2 = ProvedorFalso(["ok"])
    _conversar(ServicoChat(banco, prov2, cfg), banco.criar_conversa(GERAL).id, "oi")
    assert "analista conservador" not in prov2.chamadas[0][0] and "Vitor" in prov2.chamadas[0][0]


def test_so_um_trecho_dos_arquivos_vai_para_o_prompt(banco, cfg, monkeypatch):
    from eco.chat import servico as mod

    monkeypatch.setattr(mod, "LIMITE_CONTEXTO", 50)
    e = banco.criar_espaco("Grande")
    banco.adicionar_arquivo(e.id, "grande.txt", "A" * 40 + "B" * 5000)
    sistema = ServicoChat(banco, ProvedorFalso(), cfg).montar_sistema(e)
    assert "A" * 40 in sistema and "B" * 10 in sistema and "B" * 11 not in sistema


# --- interrupção da resposta ----------------------------------------------------------------------
def test_parar_a_geracao_salva_so_o_que_chegou_e_marca_interrompida(banco, cfg):
    prov = ProvedorFalso(["x"] * 200, atraso=0.01)
    servico = ServicoChat(banco, prov, cfg)
    c = banco.criar_conversa()
    g = servico.iniciar(c.id, "conta uma história longa")
    assert prov.primeiro.wait(2)
    g.parar()
    assert g.concluida.wait(3)
    msg = g.mensagem
    assert msg is not None and msg.interrompida and 0 < len(msg.conteudo) < 200
    salvo = banco.mensagens(c.id)[-1]
    assert salvo.papel == "assistant" and salvo.interrompida and salvo.conteudo == msg.conteudo
    # na próxima pergunta o modelo é avisado de que foi cortado
    prov2 = ProvedorFalso(["certo"])
    _conversar(ServicoChat(banco, prov2, cfg), c.id, "pode continuar de onde parou?")
    historico = prov2.chamadas[0][1]
    assert historico[1]["role"] == "assistant" and historico[1]["content"].endswith(MARCA_INTERROMPIDA)


def test_parar_antes_de_chegar_qualquer_texto_nao_salva_resposta_vazia(banco, cfg):
    prov = ProvedorFalso(["a", "b"])
    servico = ServicoChat(banco, prov, cfg)
    c = banco.criar_conversa()
    parar_cedo = threading.Event()
    parar_cedo.set()

    class Cedo(ProvedorFalso):
        def transmitir(self, sistema, mensagens, parar):
            parar.set()
            return super().transmitir(sistema, mensagens, parar)

    g = _conversar(ServicoChat(banco, Cedo(), cfg), c.id, "oi")
    assert g.mensagem is None
    assert [m.papel for m in banco.mensagens(c.id)] == ["user"]
    # e a pergunta seguinte ainda funciona (mensagens do mesmo lado seguidas são unidas)
    prov2 = ProvedorFalso(["ok"])
    _conversar(ServicoChat(banco, prov2, cfg), c.id, "estou aí?")
    assert [m["role"] for m in prov2.chamadas[0][1]] == ["user"] and "oi" in prov2.chamadas[0][1][0]["content"]
    assert servico and prov and parar_cedo


def test_erro_do_provedor_no_meio_guarda_o_que_chegou(banco, cfg):
    erros = []
    servico = ServicoChat(banco, ProvedorFalso(["parte um ", "parte dois"], falhar_apos=1), cfg)
    c = banco.criar_conversa()
    g = _conversar(servico, c.id, "oi", ao_erro=erros.append)
    assert len(erros) == 1 and isinstance(erros[0], ConnectionError)
    assert g.mensagem.conteudo == "parte um " and g.mensagem.interrompida
    # erro logo de cara: nada salvo além da pergunta
    g2 = _conversar(ServicoChat(banco, ProvedorFalso(["a"], falhar_apos=0), cfg), c.id, "de novo", ao_erro=erros.append)
    assert g2.mensagem is None and len(erros) == 2


def test_mensagem_vazia_e_recusada(banco, cfg):
    with pytest.raises(ValueError):
        ServicoChat(banco, ProvedorFalso(), cfg).iniciar(banco.criar_conversa().id, "   ")


# --- busca --------------------------------------------------------------------------------------
def test_busca_no_espaco_atual_ou_em_todos(banco):
    a, b = banco.criar_espaco("A"), banco.criar_espaco("B")
    ca, cb, cg = banco.criar_conversa(a.id, "Planejamento"), banco.criar_conversa(b.id, "Outro"), banco.criar_conversa(GERAL, "Solta")
    banco.adicionar_mensagem(ca.id, "user", "Reunião sobre orçamento do projeto")
    banco.adicionar_mensagem(cb.id, "assistant", "A reunião de equipe foi remarcada")
    banco.adicionar_mensagem(cg.id, "user", "preciso marcar uma reuniao")
    todos = {r.conversa_id for r in banco.buscar("reuniao", TODOS)}  # sem acento acha "Reunião"
    assert todos == {ca.id, cb.id, cg.id}
    assert [r.conversa_id for r in banco.buscar("REUNIÃO", a.id)] == [ca.id]
    assert [r.conversa_id for r in banco.buscar("reunião", GERAL)] == [cg.id]
    r = banco.buscar("orçamento", TODOS)[0]
    assert r.titulo == "Planejamento" and "[" in r.trecho and "orçamento" in r.trecho.lower()
    assert [x.conversa_id for x in banco.buscar("planej", TODOS)] == [ca.id]  # acha pelo título (início de palavra)
    assert banco.buscar("", TODOS) == [] and banco.buscar("nada-por-aqui", TODOS) == []
    assert banco.buscar('"; DROP TABLE mensagens; --', TODOS) == []  # texto estranho não quebra a consulta


def test_busca_acha_uma_conversa_so_uma_vez(banco):
    c = banco.criar_conversa(GERAL, "Café")
    for i in range(3):
        banco.adicionar_mensagem(c.id, "user", f"café número {i}")
    assert len(banco.buscar("café", TODOS)) == 1


# --- arquivos de contexto -------------------------------------------------------------------------
def test_ler_arquivo_de_contexto(tmp_path, monkeypatch):
    from eco.chat import servico as mod

    ok = tmp_path / "ideias.md"
    ok.write_text("# Ideias\nalgo", encoding="utf-8")
    assert ler_arquivo_contexto(ok) == ("ideias.md", "# Ideias\nalgo")
    exe = tmp_path / "programa.exe"
    exe.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="não suportado"):
        ler_arquivo_contexto(exe)
    monkeypatch.setattr(mod, "LIMITE_ARQUIVO_BYTES", 5)
    with pytest.raises(ValueError, match="grande demais"):
        ler_arquivo_contexto(ok)
    assert mod.__dict__["LIMITE_ARQUIVO_BYTES"] == 5


def test_limite_de_arquivo_e_200_mb():
    from eco.chat.servico import LIMITE_ARQUIVO_BYTES

    assert LIMITE_ARQUIVO_BYTES == 200 * 1024 * 1024


# --- fala interrompível ---------------------------------------------------------------------------
def test_divisor_de_fala_pula_codigo_e_limpa_markdown():
    d = DivisorDeFala()
    texto = "# Título\nAqui vai **uma dica** boa. Veja:\n```python\nprint('oi')\n```\n- item um\n| a | b |\nPronto!"
    falas = []
    for i in range(0, len(texto), 7):  # chega em pedacinhos, como no streaming
        falas += d.alimentar(texto[i : i + 7])
    falas += d.finalizar()
    assert falas == ["Título", "Aqui vai uma dica boa.", "Veja:", AVISO_CODIGO, "item um", "Pronto!"]
    assert not any("print" in f or "|" in f or "*" in f for f in falas)


class FalarFalso:
    def __init__(self, interromper=None, interrompe_na_chamada=None):
        self.ditos, self.interromper, self.na_chamada = [], interromper, interrompe_na_chamada

    def __call__(self, texto):
        self.ditos.append(texto)
        if self.na_chamada is not None and len(self.ditos) == self.na_chamada:
            self.interromper.set()  # o dono clicou na orbe no meio desta fala
            return SimpleNamespace(falado="", interrompida=True)
        return SimpleNamespace(falado=texto, interrompida=False)


def test_narrador_fala_a_resposta_toda_quando_ninguem_interrompe():
    ev, ao_calar, terminou = threading.Event(), [], []
    falar = FalarFalso(ev)
    n = Narrador(falar, ev, ao_calar=lambda: ao_calar.append(1), ao_terminar=lambda: terminou.append(1))
    n.alimentar("Primeira frase. Segunda frase. Terce")
    n.alimentar("ira frase.")
    n.fim_do_texto()
    assert n.terminou.wait(3)
    assert " ".join(falar.ditos).replace("  ", " ") == "Primeira frase. Segunda frase. Terceira frase."
    assert ao_calar == [] and terminou == [1] and not n.calada


def test_interromper_a_fala_cala_o_resto_e_para_a_geracao(banco, cfg):
    prov = ProvedorFalso(["Frase um. "] * 300, atraso=0.01)
    servico = ServicoChat(banco, prov, cfg)
    ev = threading.Event()
    falar = FalarFalso(ev, interrompe_na_chamada=1)
    c = banco.criar_conversa()
    geracao_ref = []
    pronta = threading.Event()

    def ao_calar():
        pronta.wait(2)
        geracao_ref[0].parar()

    n = Narrador(falar, ev, ao_calar=ao_calar)
    g = servico.iniciar(c.id, "fala muito", ao_pedaco=n.alimentar, ao_fim=lambda m: n.fim_do_texto())
    geracao_ref.append(g)
    pronta.set()
    assert g.concluida.wait(5) and n.terminou.wait(5)
    assert n.calada and not ev.is_set()  # o sinal foi limpo: a próxima fala não nasce calada
    assert len(falar.ditos) == 1  # nada do que veio depois foi falado
    assert g.mensagem.interrompida and len(g.mensagem.conteudo) < 300 * len("Frase um. ")  # a geração parou junto
    assert banco.mensagens(c.id)[-1].interrompida


def test_botao_parar_cala_a_fala_sem_chamar_ao_calar():
    ev, ao_calar = threading.Event(), []
    liberar = threading.Event()

    def falar(texto):
        liberar.wait(2)  # "falando"...
        return SimpleNamespace(falado=texto, interrompida=ev.is_set())

    n = Narrador(falar, ev, ao_calar=lambda: ao_calar.append(1))
    n.alimentar("Uma frase longa para falar. Outra frase depois.")
    time.sleep(0.05)
    n.cancelar()
    liberar.set()
    assert n.terminou.wait(3) and n.calada and ao_calar == [] and not ev.is_set()


# --- renderização ---------------------------------------------------------------------------------
def test_blocos_de_codigo_ganham_link_de_copiar_e_html_cru_e_escapado():
    ms = [
        SimpleNamespace(papel="user", conteudo="como somar? <b>oi</b>", interrompida=False),
        SimpleNamespace(papel="assistant", conteudo="Assim:\n\n```python\nprint(1 < 2)\n```\n\nE **isso** <script>x</script>.", interrompida=True),
    ]
    html, codigos = renderizar_conversa(ms, "#2fd9b0", rascunho="outro:\n\n```js\nlet a=1\n```")
    assert codigos == ["print(1 < 2)\n", "let a=1\n"]  # o texto puro de cada bloco, para copiar
    assert 'href="copiar:0"' in html and 'href="copiar:1"' in html and ">python<" in html
    assert "<script>" not in html and "&lt;script&gt;" in html and "&lt;b&gt;oi" in html
    assert "(interrompida)" in html and "escrevendo…" in html and "<strong>isso</strong>" in html
    html2, _ = renderizar_conversa(ms[:1], erro="sem internet")
    assert "sem internet" in html2
