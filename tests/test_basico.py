from datetime import datetime
from types import SimpleNamespace

import pytest

from eco.agente import Agente, escolher_modelo
from eco.config import carregar_config
from eco.confirmacao import confirmar_por_voz, eh_sim
from eco.ferramentas import construir_ferramentas, notas
from eco.ferramentas.base import Ferramenta
from eco.ferramentas.youtube import extrair_id
from eco.voz.gravacao import DetectorSilencio


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    return carregar_config()


# --- notas e metas ---------------------------------------------------------
def test_anotacao_cria_arquivo_do_dia(tmp_path):
    notas.salvar_anotacao(tmp_path, "comprar pão", datetime(2026, 11, 8, 14, 30))
    texto = (tmp_path / "notas" / "2026-11-08.md").read_text(encoding="utf-8")
    assert "# Notas de 08/11/2026" in texto and "- 14:30 comprar pão" in texto


def test_metas_ciclo_completo(tmp_path):
    notas.definir_meta(tmp_path, "Ler um livro", "2026-12-31")
    notas.definir_meta(tmp_path, "Correr 5 km")
    assert "Ler um livro (prazo: 2026-12-31)" in notas.listar_metas(tmp_path)
    notas.concluir_meta(tmp_path, "livro")
    assert "livro" not in notas.listar_metas(tmp_path)
    with pytest.raises(ValueError):
        notas.definir_meta(tmp_path, "x", "31/12/2026")  # prazo fora do formato


# --- confirmação -----------------------------------------------------------
@pytest.mark.parametrize("fala", ["Sim", "sim, pode enviar", "Pode.", "Confirmo!"])
def test_eh_sim(fala):
    assert eh_sim(fala)


@pytest.mark.parametrize("fala", ["", "não", "sim, não", "talvez", "cancela isso", "hmm sim"])
def test_nao_eh_sim(fala):
    assert not eh_sim(fala)


def test_confirmar_por_voz_le_de_volta():
    ditas = []
    assert confirmar_por_voz("Enviar e-mail.", ditas.append, lambda: "sim")
    assert ditas == ["Enviar e-mail. Posso confirmar?"]


def test_confirmacao_pergunta_de_novo_uma_vez_se_nao_ouviu():
    # Regressão (11/11/2026): o "sim" dito logo depois da pergunta caía antes de o microfone abrir e virava "não".
    ditas, respostas = [], iter(["", "sim"])
    assert confirmar_por_voz("Criar o evento.", ditas.append, lambda: next(respostas))
    assert ditas == ["Criar o evento. Posso confirmar?", "Não ouvi. Posso confirmar?"]


def test_confirmacao_cancela_depois_de_dois_silencios_e_nao_repete_se_ouviu_um_nao():
    ditas, ouvidas = [], []
    assert not confirmar_por_voz("Apagar.", ditas.append, lambda: ouvidas.append(1) or "  ")
    assert len(ditas) == 2 and len(ouvidas) == 2  # silêncio nunca confirma, e só pergunta de novo uma vez
    ditas.clear()
    assert not confirmar_por_voz("Apagar.", ditas.append, lambda: "não")
    assert ditas == ["Apagar. Posso confirmar?"]  # ouviu uma resposta: não pergunta de novo


# --- silêncio --------------------------------------------------------------
def test_detector_termina_apos_pausa():
    d = DetectorSilencio(silencio_seg=1.0, max_seg=30, espera_fala_seg=5, limiar=0.02)
    assert d.processar(0.001, 0.5) == "continuar"  # ainda ninguém falou
    assert d.processar(0.1, 0.5) == "continuar"  # falou
    assert d.processar(0.001, 0.5) == "continuar"
    assert d.processar(0.001, 0.5) == "fim"  # 1 s de silêncio depois da fala


def test_detector_desiste_sem_fala():
    d = DetectorSilencio(silencio_seg=1.0, max_seg=30, espera_fala_seg=2, limiar=0.02)
    assert d.processar(0.0, 1.0) == "continuar"
    assert d.processar(0.0, 1.0) == "sem_fala"


# --- voz -------------------------------------------------------------------
def test_falar_envia_estilo_e_velocidade_so_quando_configurados(cfg, monkeypatch):
    from dataclasses import replace

    from eco.voz import stt_tts

    pedidos = []
    cliente = SimpleNamespace(
        audio=SimpleNamespace(speech=SimpleNamespace(create=lambda **kw: pedidos.append(kw) or SimpleNamespace(read=lambda: b"")))
    )
    monkeypatch.setattr(stt_tts, "tocar_wav", lambda wav, ao_nivel=None, interromper=None: None)
    stt_tts.falar(cliente, "oi", replace(cfg, estilo_voz=None, velocidade_tts=None))
    assert "instructions" not in pedidos[0] and "speed" not in pedidos[0]
    stt_tts.falar(cliente, "oi", replace(cfg, estilo_voz="rápido", velocidade_tts=1.25))
    assert pedidos[1]["instructions"] == "rápido" and pedidos[1]["speed"] == 1.25


def test_dividir_frases_junta_pedacos_curtos():
    from eco.voz.stt_tts import dividir_frases

    assert dividir_frases("   ") == []
    assert dividir_frases("Oi. Tudo bem?") == ["Oi. Tudo bem?"]
    longas = dividir_frases("Hoje você tem uma reunião às três da tarde. Amanhã o dia está livre pra descansar.")
    assert len(longas) == 2


def test_frase_detectada_vosk_e_selecao_do_ativador(cfg, monkeypatch):
    from dataclasses import replace

    from eco.voz import ativacao

    assert ativacao.frase_detectada("eco", "ei eco")
    assert ativacao.frase_detectada("[unk] eco", "ei eco")
    assert not ativacao.frase_detectada("[unk] [unk]", "ei eco")
    assert not ativacao.frase_detectada("ecologia", "ei eco")
    escolhido = []
    monkeypatch.setattr(ativacao, "AtivadorVosk", lambda c: escolhido.append(c) or "vosk")
    assert ativacao.criar_ativador(replace(cfg, modo="voz", wakeword_modelo="vosk")) == "vosk"
    assert ativacao.criar_ativador(replace(cfg, modo="teclado", wakeword_modelo="vosk")).__class__.__name__ == "AtivadorTeclado"


def test_despedida_encerra_a_conversa_e_espera_de_fala_e_ajustavel(cfg):
    from eco.confirmacao import eh_despedida

    assert eh_despedida("Tchau, Eco!") and eh_despedida("é só isso, obrigada")
    assert eh_despedida("Eco, pode desligar.") and eh_despedida("Pode fechar")
    assert not eh_despedida("me explica por que o céu é azul, só isso eu não entendi")
    assert not eh_despedida("quais são meus compromissos de amanhã")
    assert cfg.conversa_seg > 0
    d = DetectorSilencio(silencio_seg=1.0, max_seg=30, espera_fala_seg=12, limiar=0.02)
    assert d.processar(0.0, 11.0) == "continuar" and d.processar(0.0, 1.5) == "sem_fala"


# --- interrupção da fala -----------------------------------------------------
FRASES = (
    "Primeira frase bem comprida para passar do mínimo. Segunda frase também comprida para ficar separada. "
    "Terceira frase que nunca deveria ser dita. Quarta frase também comprida e nunca dita. Quinta e última frase comprida demais."
)


def _cliente_voz(chamadas, atraso=0.05):
    import time

    def create(**kw):
        time.sleep(atraso)
        chamadas.append(kw["input"])
        return SimpleNamespace(read=lambda: b"")

    return SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(create=create)))


def test_tocar_wav_para_na_hora_quando_interrompido(monkeypatch):
    import io
    import sys
    import threading
    import time
    import wave

    from eco.voz import stt_tts

    paradas = []
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(play=lambda d, t: None, stop=lambda: paradas.append(1), wait=lambda: None))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:  # 2 s de silêncio
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 32000)
    import numpy  # noqa: F401  (carregado antes do cronômetro: rodando sozinho, o import levava mais que os 0,2 s)

    ev = threading.Event()
    threading.Timer(0.2, ev.set).start()
    t0 = time.perf_counter()
    fracao = stt_tts.tocar_wav(buf.getvalue(), None, ev)
    assert time.perf_counter() - t0 < 1.0 and paradas == [1]
    assert 0.0 < fracao < 0.5
    ev2 = threading.Event()
    ev2.set()
    assert stt_tts.tocar_wav(buf.getvalue(), None, ev2) == 0.0  # já interrompido: nem começa


def test_falar_interrompido_no_meio_guarda_so_o_falado_e_para_de_gerar(cfg, monkeypatch):
    import threading
    import time

    from eco.voz import stt_tts

    chamadas = []
    ev = threading.Event()

    def toca(wav, ao_nivel=None, interromper=None):
        if len(tocados) == 1:  # interrompe no meio do 2º bloco
            ev.set()
            tocados.append("meio")
            return 0.5
        tocados.append("inteiro")
        return 1.0

    tocados = []
    monkeypatch.setattr(stt_tts, "tocar_wav", toca)
    blocos = stt_tts.dividir_frases(FRASES)
    assert len(blocos) == 5
    fala = stt_tts.falar(_cliente_voz(chamadas), FRASES, cfg, interromper=ev)
    assert fala.interrompida
    palavras2 = blocos[1].split()
    assert fala.falado == blocos[0] + " " + " ".join(palavras2[: len(palavras2) // 2])
    assert "Terceira" not in fala.falado
    time.sleep(0.4)
    assert len(chamadas) <= 3  # no máximo a frase que já estava em andamento; a 4ª e a 5ª nunca são geradas


def test_falar_sem_interrupcao_fala_tudo(cfg, monkeypatch):
    import threading

    from eco.voz import stt_tts

    monkeypatch.setattr(stt_tts, "tocar_wav", lambda wav, ao_nivel=None, interromper=None: 1.0)
    fala = stt_tts.falar(_cliente_voz([], 0), FRASES, cfg, interromper=threading.Event())
    assert not fala.interrompida and fala.falado == " ".join(stt_tts.dividir_frases(FRASES))


def test_historico_guarda_so_o_falado_com_a_marca(cfg):
    from eco.agente.nucleo import MARCA_INTERROMPIDA, MARCA_SEM_FALAR

    agente = Agente(cfg, construir_ferramentas(cfg), lambda d: True, ClienteFalso([texto("Uma resposta longa. Com várias frases.")]))
    agente.responder("conta algo")
    agente.marcar_interrompida("Uma resposta longa.")
    assert agente._historico[-1] == {"role": "assistant", "content": f"Uma resposta longa. {MARCA_INTERROMPIDA}"}
    agente.marcar_interrompida("")
    assert agente._historico[-1]["content"] == MARCA_SEM_FALAR
    assert "Com várias frases" not in str(agente._historico)


def test_painel_tem_sinal_de_interrupcao_e_prompt_explica_a_marca():
    from eco.agente.nucleo import SISTEMA
    from eco.painel import Painel

    p = Painel()
    assert not p.interromper.is_set()
    p.interromper.set()
    assert p.interromper.is_set()
    assert "interrompida pelo usuário" in SISTEMA


# --- lembretes: resumo do dia e aviso antes dos compromissos ----------------------
class FonteFalsa:
    def __init__(self, eventos=(), pendencias=()):
        self.lista, self.pend, self.falhar = list(eventos), list(pendencias), False
        self.consultas = 0

    def eventos(self, inicio, fim):
        self.consultas += 1
        if self.falhar:
            raise ConnectionError("sem rede")
        return [e for e in self.lista if e.dia_inteiro or inicio <= e.inicio < fim]

    def pendencias(self):
        return self.pend


def _sp(texto):
    from datetime import datetime

    from eco.lembretes import FUSO

    return datetime.fromisoformat(texto).replace(tzinfo=FUSO)


def _lembretes(tmp_path, fonte, avisos=None):
    from eco.lembretes import Lembretes

    return Lembretes(fonte, tmp_path / "lembretes.json", lambda t, m: avisos.append((t, m)) if avisos is not None else None)


def _falas(lem):
    saida = []
    while (f := lem.proxima_fala()) is not None:
        saida.append(f)
    return saida


def test_primeira_abertura_do_dia_fala_o_resumo_e_notifica(tmp_path):
    from datetime import date

    from eco.lembretes import Evento, Pendencia

    fonte = FonteFalsa(
        [Evento("a", "Dentista", _sp("2026-11-09T15:00")), Evento("b", "Aniversário da Ana", date(2026, 11, 9))],
        [Pendencia("pagar boleto", date(2026, 11, 9)), Pendencia("declarar imposto", date(2026, 11, 1))],
    )
    avisos = []
    lem = _lembretes(tmp_path, fonte, avisos)
    lem.ciclo(_sp("2026-11-09T08:00"))
    (fala,) = _falas(lem)
    assert fala.startswith("Bom dia!") and "às 15 horas, Dentista" in fala and "Aniversário da Ana, o dia todo" in fala
    assert "pagar boleto" in fala and "atrasada" in fala and "declarar imposto" in fala
    assert avisos and avisos[0][0] == "Resumo do dia"
    assert lem.estado.ultimo_resumo == "2026-11-09"


def test_segunda_abertura_no_mesmo_dia_nao_repete_o_resumo(tmp_path):
    from eco.lembretes import Evento

    fonte = FonteFalsa([Evento("a", "Dentista", _sp("2026-11-09T15:00"))])
    _lembretes(tmp_path, fonte).ciclo(_sp("2026-11-09T08:00"))
    segunda = _lembretes(tmp_path, fonte)  # o app foi fechado e aberto de novo: instância nova, mesmo arquivo
    segunda.ciclo(_sp("2026-11-09T13:00"))
    assert _falas(segunda) == []
    segunda.ciclo(_sp("2026-11-10T07:30"))  # no dia seguinte, volta a falar
    assert len(_falas(segunda)) == 1


def test_resumo_so_conta_como_dado_depois_de_conseguir_buscar(tmp_path):
    fonte = FonteFalsa()
    fonte.falhar = True  # o PC acabou de ligar e a rede ainda não subiu
    lem = _lembretes(tmp_path, fonte)
    lem.ciclo(_sp("2026-11-09T08:00"))
    assert _falas(lem) == [] and lem.estado.ultimo_resumo is None
    fonte.falhar = False
    lem.ciclo(_sp("2026-11-09T08:01"))
    (fala,) = _falas(lem)
    assert "dia está livre" in fala


def test_aviso_dez_minutos_antes_e_uma_vez_so(tmp_path):
    from datetime import timedelta

    from eco.lembretes import Evento

    fonte = FonteFalsa([Evento("a", "Reunião", _sp("2026-11-09T15:00"))])
    avisos = []
    lem = _lembretes(tmp_path, fonte, avisos)
    lem.estado.ultimo_resumo = "2026-11-09"  # só o aviso importa aqui
    lem.ciclo(_sp("2026-11-09T14:40"))  # cedo demais
    assert _falas(lem) == []
    t = _sp("2026-11-09T14:50")
    for i in range(5):  # o laço roda várias vezes dentro da janela
        lem.ciclo(t + timedelta(seconds=30 * i))
    (fala,) = _falas(lem)
    assert fala == "Daqui a 10 minutos: Reunião, às 15 horas."
    assert [a[0] for a in avisos] == ["Em 10 minutos"]
    _lembretes(tmp_path, fonte, avisos).ciclo(t + timedelta(seconds=60))  # reabriu dentro da janela: o registro impede repetir
    assert len(avisos) == 1


def test_aviso_perdido_e_ignorado_ao_abrir_o_app(tmp_path):
    from eco.lembretes import Evento

    fonte = FonteFalsa([Evento("a", "Reunião", _sp("2026-11-09T15:00")), Evento("b", "Outra", _sp("2026-11-09T15:05"))])
    lem = _lembretes(tmp_path, fonte)
    lem.estado.ultimo_resumo = "2026-11-09"
    lem.ciclo(_sp("2026-11-09T14:58"))  # app fechado na hora dos avisos (14:50 e 14:55): nada de disparar tudo de uma vez
    assert _falas(lem) == []
    lem.ciclo(_sp("2026-11-09T15:30"))  # depois do início também não
    assert _falas(lem) == []


def test_compromisso_de_dia_inteiro_nao_gera_aviso(tmp_path):
    from datetime import date

    from eco.lembretes import Evento

    lem = _lembretes(tmp_path, FonteFalsa([Evento("d", "Feriado", date(2026, 11, 9))]))
    lem.estado.ultimo_resumo = "2026-11-09"
    for h in ("00:00", "08:50", "23:50"):
        lem.ciclo(_sp(f"2026-11-09T{h}"))
    assert _falas(lem) == []


def test_mudanca_de_fuso_do_computador_nao_muda_o_dia_nem_o_aviso(tmp_path):
    from datetime import datetime, timezone

    from eco.lembretes import Evento

    # 22:30 do dia 9 em São Paulo já é dia 10 em UTC: o resumo do dia 9 não pode repetir
    lem = _lembretes(tmp_path, FonteFalsa())
    lem.estado.ultimo_resumo = "2026-11-09"
    lem.ciclo(datetime(2026, 11, 10, 1, 30, tzinfo=timezone.utc))
    assert _falas(lem) == []
    lem.ciclo(datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc))  # 01:00 do dia 10 em SP: outro dia, mas antes do amanhecer
    assert _falas(lem) == []
    lem.ciclo(datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc))  # 06:00 em SP: agora sim
    assert len(_falas(lem)) == 1
    # evento com horário vindo em UTC e relógio em UTC: a conta e a hora falada são as de São Paulo
    evento = Evento("u", "Aula", datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc))  # 14:00 em São Paulo
    lem2 = _lembretes(tmp_path, FonteFalsa([evento]))
    lem2.estado.ultimo_resumo = "2026-11-10"
    lem2.ciclo(datetime(2026, 11, 10, 16, 51, tzinfo=timezone.utc))
    assert _falas(lem2) == ["Daqui a 10 minutos: Aula, às 14 horas."]


def test_mudanca_de_horario_do_compromisso_avisa_de_novo(tmp_path):
    from eco.lembretes import Evento

    fonte = FonteFalsa([Evento("a", "Reunião", _sp("2026-11-09T15:00"))])
    lem = _lembretes(tmp_path, fonte)
    lem.estado.ultimo_resumo = "2026-11-09"
    lem.ciclo(_sp("2026-11-09T14:50"))
    assert len(_falas(lem)) == 1
    fonte.lista = [Evento("a", "Reunião", _sp("2026-11-09T16:30"))]  # remarcada
    lem.ciclo(_sp("2026-11-09T15:00"))
    assert _falas(lem) == []
    lem.ciclo(_sp("2026-11-09T16:20"))
    assert _falas(lem) == ["Daqui a 10 minutos: Reunião, às 16 e 30."]


def test_agente_registra_fala_automatica_no_historico(cfg):
    agente = Agente(cfg, construir_ferramentas(cfg), lambda d: True, ClienteFalso([]))
    agente.registrar_fala_automatica("Bom dia! Seu dia está livre.")
    assert agente._historico[-1] == {"role": "assistant", "content": "Bom dia! Seu dia está livre."}
    assert agente._historico[0]["role"] == "user"


# --- apagar evento da agenda --------------------------------------------------
def _evs():
    from datetime import date

    return [
        {"id": "e1", "titulo": "Dentista", "inicio": _sp("2026-11-12T15:00")},
        {"id": "e2", "titulo": "Reunião com a Ana", "inicio": _sp("2026-11-13T09:00")},
        {"id": "e3", "titulo": "Reunião de equipe", "inicio": _sp("2026-11-14T10:30")},
        {"id": "e4", "titulo": "Aniversário do João", "inicio": date(2026, 11, 15)},
    ]


def test_escolher_evento_ignora_acento_e_caixa_e_exige_um_so():
    from eco.ferramentas import google

    assert google.escolher_evento(_evs(), "DENTISTA")["id"] == "e1"
    assert google.escolher_evento(_evs(), "aniversario")["id"] == "e4"
    with pytest.raises(ValueError, match="Não achei"):
        google.escolher_evento(_evs(), "academia")
    with pytest.raises(ValueError, match="Mais de um evento") as erro:
        google.escolher_evento(_evs(), "reuniao")
    assert "Ana" in str(erro.value) and "equipe" in str(erro.value) and "Não apague" in str(erro.value)
    assert google.escolher_evento(_evs(), "reuniao", "2026-11-14")["id"] == "e3"  # a data desfaz a ambiguidade
    with pytest.raises(ValueError, match="no dia 2026-11-12"):
        google.escolher_evento(_evs(), "reuniao", "2026-11-12")
    with pytest.raises(ValueError):
        google.escolher_evento(_evs(), "   ")


def test_descrever_evento_fala_dia_e_hora():
    from eco.ferramentas import google

    assert google.descrever_evento(_evs()[0]) == "Dentista, dia 12/11 às 15 horas"
    assert google.descrever_evento(_evs()[3]) == "Aniversário do João, dia 15/11, o dia todo"


def _agente_agenda(cfg, monkeypatch, resposta_do_dono, entradas):
    from eco.ferramentas import google

    apagados, confirmacoes = [], []
    monkeypatch.setattr(google, "localizar_evento", lambda trecho, data=None, dias=60: google.escolher_evento(_evs(), trecho, data))
    from eco.ferramentas import agenda

    monkeypatch.setattr(
        agenda, "apagar_com_escopo", lambda ev, esc: apagados.append(ev["id"]) or f"Evento apagado: {google.descrever_evento(ev)}."
    )
    cliente = ClienteFalso([uso("apagar_evento", entradas), texto("Feito.")])
    agente = Agente(cfg, construir_ferramentas(cfg), lambda d: confirmacoes.append(d) or resposta_do_dono, cliente)
    agente.responder("apaga isso")
    return apagados, confirmacoes, cliente


def test_apagar_evento_le_de_volta_e_so_apaga_depois_do_sim(cfg, monkeypatch):
    apagados, confirmacoes, cliente = _agente_agenda(cfg, monkeypatch, True, {"trecho": "dentista"})
    assert confirmacoes == ["Apagar da agenda o evento Dentista, dia 12/11 às 15 horas."]
    assert apagados == ["e1"]
    assert "Evento apagado" in cliente.chamadas[1]["messages"][-1]["content"][0]["content"]


def test_apagar_evento_cancelado_nao_apaga_nada(cfg, monkeypatch):
    apagados, confirmacoes, cliente = _agente_agenda(cfg, monkeypatch, False, {"trecho": "dentista"})
    assert confirmacoes and apagados == []
    assert "cancelou" in cliente.chamadas[1]["messages"][-1]["content"][0]["content"]


def test_apagar_evento_ambiguo_ou_inexistente_nao_pergunta_nem_apaga(cfg, monkeypatch):
    for entrada, trecho_do_erro in (({"trecho": "reuniao"}, "Mais de um evento"), ({"trecho": "academia"}, "Não achei")):
        apagados, confirmacoes, cliente = _agente_agenda(cfg, monkeypatch, True, entrada)
        resultado = cliente.chamadas[1]["messages"][-1]["content"][0]
        assert confirmacoes == [] and apagados == []  # nem chega a pedir o "sim"
        assert resultado["is_error"] and trecho_do_erro in resultado["content"]


def test_apagar_evento_com_data_apaga_o_certo(cfg, monkeypatch):
    apagados, _, _ = _agente_agenda(cfg, monkeypatch, True, {"trecho": "reuniao", "data": "2026-11-13"})
    assert apagados == ["e2"]


# --- apagar e concluir tarefas (Google Tasks) ---------------------------------
def _tarefas():
    from datetime import date

    return [
        {"id": "t1", "titulo": "Pagar boleto", "prazo": date(2026, 11, 10), "lista_id": "L1"},
        {"id": "t2", "titulo": "Estudo de Cálculo", "prazo": None, "lista_id": "L1"},
        {"id": "t3", "titulo": "Estudo sobre investimentos", "prazo": date(2026, 11, 9), "lista_id": "L1"},
    ]


def test_escolher_tarefa_um_so_ignorando_acento():
    from eco.ferramentas import google

    assert google.escolher_tarefa(_tarefas(), "BOLETO")["id"] == "t1"
    assert google.escolher_tarefa(_tarefas(), "calculo")["id"] == "t2"
    assert google.descrever_tarefa(_tarefas()[0]) == "Pagar boleto (para 10/11)"
    assert google.descrever_tarefa(_tarefas()[1]) == "Estudo de Cálculo"
    with pytest.raises(ValueError, match="Não achei"):
        google.escolher_tarefa(_tarefas(), "academia")
    with pytest.raises(ValueError, match="Mais de uma tarefa") as erro:
        google.escolher_tarefa(_tarefas(), "estudo")
    assert "Cálculo" in str(erro.value) and "investimentos" in str(erro.value)
    with pytest.raises(ValueError):
        google.escolher_tarefa(_tarefas(), "")


def _agente_tarefas(cfg, monkeypatch, ferramenta, entrada, resposta_do_dono=True):
    from eco.ferramentas import google

    feitos, confirmacoes = [], []
    monkeypatch.setattr(google, "tarefas_pendentes", lambda: _tarefas())
    monkeypatch.setattr(google, "apagar_tarefa", lambda i, lista="@default": feitos.append(("apagar", i)))
    monkeypatch.setattr(google, "concluir_tarefa", lambda i, lista="@default": feitos.append(("concluir", i)))
    cliente = ClienteFalso([uso(ferramenta, entrada), texto("Feito.")])
    agente = Agente(cfg, construir_ferramentas(cfg), lambda d: confirmacoes.append(d) or resposta_do_dono, cliente)
    agente.responder("mexe na tarefa")
    return feitos, confirmacoes, cliente.chamadas[1]["messages"][-1]["content"][0]


def test_apagar_tarefa_le_de_volta_e_so_apaga_depois_do_sim(cfg, monkeypatch):
    feitos, confirmacoes, resultado = _agente_tarefas(cfg, monkeypatch, "apagar_tarefa", {"trecho": "boleto"})
    assert confirmacoes == ["Apagar da lista de tarefas: Pagar boleto (para 10/11)."]
    assert feitos == [("apagar", "t1")] and "Tarefa apagada" in resultado["content"]
    feitos, _, resultado = _agente_tarefas(cfg, monkeypatch, "apagar_tarefa", {"trecho": "boleto"}, resposta_do_dono=False)
    assert feitos == [] and "cancelou" in resultado["content"]


def test_apagar_tarefa_ambigua_ou_inexistente_nao_pergunta_nem_apaga(cfg, monkeypatch):
    for trecho, erro in (("estudo", "Mais de uma tarefa"), ("academia", "Não achei")):
        feitos, confirmacoes, resultado = _agente_tarefas(cfg, monkeypatch, "apagar_tarefa", {"trecho": trecho})
        assert feitos == [] and confirmacoes == []
        assert resultado["is_error"] and erro in resultado["content"]


def test_concluir_tarefa_marca_sem_apagar_e_sem_pedir_sim(cfg, monkeypatch):
    feitos, confirmacoes, resultado = _agente_tarefas(cfg, monkeypatch, "concluir_tarefa", {"trecho": "calculo"})
    assert feitos == [("concluir", "t2")] and confirmacoes == []
    assert "Tarefa concluída: Estudo de Cálculo" in resultado["content"]
    feitos, _, resultado = _agente_tarefas(cfg, monkeypatch, "concluir_tarefa", {"trecho": "estudo"})
    assert feitos == [] and resultado["is_error"]


def test_estilo_de_voz_padrao_e_desligavel(monkeypatch, cfg):
    assert cfg.estilo_voz and "natural" in cfg.estilo_voz
    monkeypatch.setenv("ECO_ESTILO_VOZ", "nenhum")
    assert carregar_config().estilo_voz is None


# --- memória ---------------------------------------------------------------
def test_memoria_lembra_esquece_e_limita(tmp_path):
    from eco.ferramentas import memoria

    assert memoria.ler(tmp_path) == ""
    memoria.lembrar(tmp_path, "O dono gosta de café sem açúcar", datetime(2026, 11, 9))
    memoria.lembrar(tmp_path, "A irmã se chama Ana")
    assert "café sem açúcar (09/11/2026)" in memoria.ler(tmp_path)
    memoria.esquecer(tmp_path, "irmã")
    assert "Ana" not in memoria.ler(tmp_path)
    for i in range(50):
        memoria.lembrar(tmp_path, f"fato número {i} " + "x" * 40)
    lido = memoria.ler(tmp_path, limite=500)
    assert len(lido) <= 500 and "fato número 49" in lido and "café" not in lido


def test_prompt_de_sistema_nao_muda_dentro_de_uma_resposta(cfg):
    # Regressão: `lembrar` altera a memória no meio do laço; o prompt tem de continuar idêntico.
    cliente = ClienteFalso([uso("lembrar", {"fato": "O dono gosta de café"}), texto("Anotado!")])
    Agente(cfg, construir_ferramentas(cfg), lambda d: True, cliente).responder("gosto de café")
    assert cliente.chamadas[0]["system"] == cliente.chamadas[1]["system"]
    assert "gosta de café" not in _texto_sistema(cliente.chamadas[1])


def test_agente_envia_memoria_no_prompt_do_sistema(cfg):
    from eco.ferramentas import memoria

    memoria.lembrar(cfg.pasta_dados, "O dono torce pelo Corinthians")
    cliente = ClienteFalso([texto("Opa, claro!")])
    Agente(cfg, construir_ferramentas(cfg), lambda d: True, cliente).responder("oi")
    assert "Corinthians" in _texto_sistema(cliente.chamadas[0])
    assert "lembrar" in {t.get("name") for t in cliente.chamadas[0]["tools"]}


# --- gasto -----------------------------------------------------------------
def test_calculo_de_custo():
    from eco.uso import calcular_custo

    assert calcular_custo("claude-haiku-5-5", 1000, 100) == pytest.approx(0.00015)
    assert calcular_custo("claude-sonnet-5-5", 1_000_000, 0) == pytest.approx(2.0)
    assert calcular_custo("claude-haiku-5-5", 0, 0, buscas=2) == pytest.approx(0.02)


def test_registro_soma_so_o_mes_atual(tmp_path):
    from eco.uso import RegistroUso, Uso

    registro = RegistroUso(tmp_path / "uso.jsonl")
    registro.registrar(Uso("claude-haiku-5-5", custo=0.5), datetime(2026, 10, 31))
    registro.registrar(Uso("claude-haiku-5-5", custo=0.25), datetime(2026, 11, 2))
    registro.registrar(Uso("claude-sonnet-5-5", custo=1.0), datetime(2026, 11, 3))
    hoje = datetime(2026, 11, 9)
    assert registro.total_mes(hoje) == pytest.approx(1.25)
    assert registro.resumo_mes(hoje)["claude-haiku-5-5"][0] == 1


def test_agente_mede_e_registra_o_gasto(cfg, tmp_path):
    from eco.uso import RegistroUso

    resposta = texto("Brasília.")
    resposta.usage = SimpleNamespace(
        input_tokens=2000, output_tokens=50, server_tool_use=SimpleNamespace(web_search_requests=1)
    )
    registro = RegistroUso(tmp_path / "uso.jsonl")
    agente = Agente(cfg, construir_ferramentas(cfg), lambda d: True, ClienteFalso([resposta]), registro)
    agente.responder("capital?")
    esperado = (2000 * 0.10 + 50 * 0.50) / 1_000_000 + 0.01
    assert agente.gasto_ultimo.custo == pytest.approx(esperado) and agente.gasto_ultimo.buscas == 1
    assert registro.total_mes() == pytest.approx(esperado)


# --- palavra de ativação ---------------------------------------------------
def test_resolver_modelo_wakeword_pronto_e_invalido():
    from eco.voz.ativacao import resolver_modelo_wakeword

    assert resolver_modelo_wakeword("hey_jarvis").name.startswith("hey_jarvis")
    with pytest.raises(ValueError, match="hey_jarvis"):  # a mensagem lista os nomes válidos
        resolver_modelo_wakeword("ei_eco_inexistente")


def test_criar_ativador_escolhe_openwakeword_e_respeita_modo_teclado(cfg):
    from dataclasses import replace

    from eco.voz.ativacao import AtivadorOpenWakeWord, AtivadorTeclado, criar_ativador

    assert isinstance(criar_ativador(replace(cfg, modo="voz", wakeword_modelo="hey_jarvis")), AtivadorOpenWakeWord)
    assert isinstance(criar_ativador(replace(cfg, modo="teclado", wakeword_modelo="hey_jarvis")), AtivadorTeclado)


# --- youtube ---------------------------------------------------------------
def test_extrair_id_de_links():
    assert extrair_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3") == "dQw4w9WgXcQ"
    assert extrair_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    with pytest.raises(ValueError):
        extrair_id("https://exemplo.com/video")


# --- agente (com cliente falso, sem rede) ----------------------------------
class ClienteFalso:
    def __init__(self, respostas):
        self.chamadas, self._respostas = [], list(respostas)
        self.messages = self

    def create(self, **kw):
        self.chamadas.append(kw)
        return self._respostas.pop(0)


def texto(t, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=t)])


def uso(nome, entrada):
    bloco = SimpleNamespace(type="tool_use", id="t1", name=nome, input=entrada)
    return SimpleNamespace(stop_reason="tool_use", content=[bloco])


def _texto_sistema(chamada) -> str:
    """O prompt de sistema vai em blocos (parte fixa com cache + parte variável); aqui, o texto inteiro."""
    return "".join(b["text"] for b in chamada["system"])


def test_roteamento_de_modelo(cfg):
    assert escolher_modelo("que horas são", cfg)[0] == cfg.modelo_padrao
    assert escolher_modelo("escreva um e-mail para o João", cfg)[0] == cfg.modelo_forte


@pytest.mark.parametrize("pedido", [
    "escreva um e-mail para o João", "responda o e-mail do Rafael dizendo que topo",
    "rascunhe uma resposta dizendo que recebi", "redija para a Ana que chego às 8",
    "manda uma mensagem pro chefe avisando do atraso", "envie um email pra Maria agradecendo",
])
def test_redigir_ou_responder_email_usa_o_modelo_forte(cfg, pedido):
    assert escolher_modelo(pedido, cfg)[0] == cfg.modelo_forte


@pytest.mark.parametrize("pedido", [
    "leia meus e-mails", "tenho e-mails não lidos?", "marque esse e-mail como pendente", "envie esse rascunho",
    "escreva uma nota no bloco", "responda que sim", "qual o último e-mail da Binance?", "abra o gmail",
])
def test_ler_listar_e_marcar_email_fica_no_modelo_padrao(cfg, pedido):
    # antes, qualquer frase com "e-mail" ou "escrev" ia para o Sonnet (62% do gasto medido em 11/2026)
    assert escolher_modelo(pedido, cfg)[0] == cfg.modelo_padrao


def test_prompt_tem_parte_fixa_com_cache_e_parte_variavel(cfg):
    from eco.agente.nucleo import SISTEMA, SISTEMA_FIXO, SISTEMA_VARIAVEL

    assert SISTEMA_FIXO + SISTEMA_VARIAVEL == SISTEMA  # o texto não mudou, só foi dividido
    assert "{memoria}" in SISTEMA_VARIAVEL and "{agora}" in SISTEMA_VARIAVEL and "{contexto}" in SISTEMA_VARIAVEL
    assert "{memoria}" not in SISTEMA_FIXO and "{agora}" not in SISTEMA_FIXO and "{canal}" in SISTEMA_FIXO
    cliente = ClienteFalso([texto("Oi!"), texto("Oi de novo!")])
    agente = Agente(cfg, construir_ferramentas(cfg), lambda d: True, cliente)
    agente.responder("oi")
    agente.responder("tudo bem?")
    fixo, variavel = cliente.chamadas[0]["system"]
    assert fixo["cache_control"] == {"type": "ephemeral"} and "cache_control" not in variavel
    assert "Agora:" in variavel["text"] and "Agora:" not in fixo["text"]
    # a parte fixa e as ferramentas são iguais entre pedidos (é o que deixa o cache valer)
    assert cliente.chamadas[1]["system"][0] == fixo and cliente.chamadas[1]["tools"] == cliente.chamadas[0]["tools"]


def test_uso_registra_leitura_e_gravacao_do_cache(cfg, tmp_path):
    import json

    from eco.uso import RegistroUso

    resposta = texto("Oi.")
    resposta.usage = SimpleNamespace(input_tokens=900, output_tokens=20, cache_read_input_tokens=8000,
                                     cache_creation_input_tokens=0, server_tool_use=None)
    registro = RegistroUso(tmp_path / "uso.jsonl")
    agente = Agente(cfg, construir_ferramentas(cfg), lambda d: True, ClienteFalso([resposta]), registro)
    agente.responder("oi")
    linha = json.loads((tmp_path / "uso.jsonl").read_text(encoding="utf-8"))
    assert linha["cache_lido"] == 8000 and linha["entrada"] == 8900 and linha["categoria"] == "voz"
    esperado = (900 * 0.10 + 8000 * 0.10 * 0.1 + 20 * 0.50) / 1_000_000
    assert agente.gasto_ultimo.custo == pytest.approx(esperado) and agente.gasto_ultimo.cache_lido == 8000


def test_pergunta_simples_usa_haiku(cfg):
    cliente = ClienteFalso([texto("Brasília.")])
    agente = Agente(cfg, construir_ferramentas(cfg), lambda d: True, cliente)
    assert agente.responder("capital do Brasil?") == "Brasília."
    assert cliente.chamadas[0]["model"] == cfg.modelo_padrao
    assert any(t.get("name") == "web_search" for t in cliente.chamadas[0]["tools"])


def test_ferramenta_local_roda_sem_confirmacao(cfg):
    cliente = ClienteFalso([uso("salvar_anotacao", {"texto": "ideia"}), texto("Anotei.")])
    agente = Agente(cfg, construir_ferramentas(cfg), lambda d: pytest.fail("não deveria confirmar"), cliente)
    assert agente.responder("anota ideia") == "Anotei."
    assert list((cfg.pasta_dados / "notas").glob("*.md"))
    resultado = cliente.chamadas[1]["messages"][-1]["content"][0]
    assert resultado["type"] == "tool_result" and "is_error" not in resultado


@pytest.mark.parametrize("resposta, executou", [(True, True), (False, False)])
def test_acao_externa_so_executa_se_confirmada(cfg, resposta, executou):
    feito = []
    envio = Ferramenta(
        "enviar_email", "x", {"type": "object", "properties": {}},
        lambda a: feito.append(a) or "enviado",
        confirmar=lambda a: "Enviar e-mail.",
    )
    cliente = ClienteFalso([uso("enviar_email", {}), texto("ok")])
    Agente(cfg, [envio], lambda d: resposta, cliente).responder("manda e-mail")
    assert bool(feito) is executou
    assert ("cancelou" in cliente.chamadas[1]["messages"][-1]["content"][0]["content"]) is (not executou)


def test_erro_de_ferramenta_vira_resultado_de_erro(cfg):
    cliente = ClienteFalso([uso("definir_meta", {"titulo": "x", "prazo": "amanhã"}), texto("Data inválida.")])
    Agente(cfg, construir_ferramentas(cfg), lambda d: True, cliente).responder("meta")
    assert cliente.chamadas[1]["messages"][-1]["content"][0]["is_error"] is True
