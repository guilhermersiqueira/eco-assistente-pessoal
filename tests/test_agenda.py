"""Agenda avançada: repetição, edição (inclusive de séries) e exclusão em massa, com um Google Agenda simulado."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from types import SimpleNamespace

import pytest

from eco.agente import Agente
from eco.config import carregar_config
from eco.ferramentas import agenda, construir_ferramentas, google

TZ = ZoneInfo("America/Sao_Paulo")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_DADOS_DIR", str(tmp_path))
    monkeypatch.setenv("ECO_ENV_FILE", str(tmp_path / "nao-existe.env"))
    return carregar_config()


class ClienteFalso:
    def __init__(self, respostas):
        self.chamadas, self._respostas = [], list(respostas)
        self.messages = self

    def create(self, **kw):
        self.chamadas.append(kw)
        return self._respostas.pop(0)


def texto(t):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=t)])


def uso(nome, entrada):
    return SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(type="tool_use", id="t1", name=nome, input=entrada)])


def sp(texto: str) -> datetime:
    return datetime.fromisoformat(texto).replace(tzinfo=TZ)


class Pedido:
    def __init__(self, valor=None, efeito=None):
        self.valor, self.efeito = valor, efeito

    def execute(self):
        if self.efeito:
            self.efeito()
        return self.valor if self.valor is not None else {}


class Lote:
    def __init__(self, servico):
        self.servico, self.itens = servico, []

    def add(self, pedido, callback=None, request_id=None):
        self.itens.append((pedido, callback, request_id))

    def execute(self):
        for pedido, cb, rid in self.itens:
            pedido.execute()
            cb(rid, {}, None)


class Eventos:
    """events() falso: guarda as chamadas e devolve o que o teste armou."""

    def __init__(self, servico):
        self.s = servico

    def list(self, **kw):
        self.s.chamadas.append(("list", kw))
        return Pedido({"items": self.s.instancias})

    def get(self, calendarId, eventId):
        return Pedido(self.s.recursos[eventId])

    def patch(self, calendarId, eventId, body, sendUpdates="none"):
        self.s.chamadas.append(("patch", eventId, body, sendUpdates))
        return Pedido()

    def insert(self, calendarId, body, sendUpdates="none"):
        self.s.chamadas.append(("insert", body, sendUpdates))
        return Pedido()

    def delete(self, calendarId, eventId):
        return Pedido(efeito=lambda: self.s.chamadas.append(("delete", eventId)))

    def instances(self, calendarId, eventId, **kw):
        n = self.s.restantes
        return Pedido({"items": [{"status": "confirmed"}] * n})


class Servico:
    def __init__(self):
        self.chamadas: list = []
        self.recursos: dict = {}
        self.instancias: list = []
        self.restantes = 0

    def events(self):
        return Eventos(self)

    def new_batch_http_request(self):
        return Lote(self)

    def apagados(self):
        return [c[1] for c in self.chamadas if c[0] == "delete"]

    def com(self, tipo):
        return [c for c in self.chamadas if c[0] == tipo]


@pytest.fixture
def agenda_falsa(monkeypatch):
    s = Servico()
    monkeypatch.setattr(google, "_servico", lambda nome, versao: s)
    return s


def mestre_leitura(inicio="2026-11-10T21:00:00-03:00", regra="RRULE:FREQ=DAILY;COUNT=30"):
    return {
        "id": "serie1",
        "summary": "Leitura diária",
        "start": {"dateTime": inicio},
        "end": {"dateTime": (datetime.fromisoformat(inicio) + timedelta(minutes=30)).isoformat()},
        "recurrence": [regra],
    }


def ocorrencia(dia: int, titulo="Leitura diária", serie="serie1"):
    ini = sp(f"2026-11-{dia:02d}T21:00")
    return {"id": f"serie1_{dia}", "titulo": titulo, "inicio": ini, "fim": ini + timedelta(minutes=30), "serie": serie}


# --- repetição -----------------------------------------------------------------------------------------------
def test_montar_regra_diaria_com_numero_de_vezes():
    assert agenda.montar_regra({"frequencia": "diaria", "vezes": 10}) == "RRULE:FREQ=DAILY;COUNT=10"


def test_montar_regra_dias_da_semana_com_data_final_em_utc():
    regra = agenda.montar_regra({"frequencia": "semanal", "dias_semana": ["ter", "qui"], "ate": "2026-12-15"})
    # 15/12 às 23:59:59 em São Paulo (UTC-3) = 16/12 02:59:59 UTC
    assert regra == "RRULE:FREQ=WEEKLY;BYDAY=TU,TH;UNTIL=20261216T025959Z"


def test_segunda_a_sexta_vira_semanal_e_quinzenal_vira_intervalo():
    assert agenda.montar_regra({"frequencia": "diaria", "dias_semana": ["seg", "sex"], "vezes": 4}) == "RRULE:FREQ=WEEKLY;BYDAY=MO,FR;COUNT=4"
    assert agenda.montar_regra({"frequencia": "semanal", "intervalo": 2, "vezes": 3}) == "RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=3"


def test_repeticao_sem_fim_manda_perguntar_ate_quando():
    with pytest.raises(ValueError, match="até quando"):
        agenda.montar_regra({"frequencia": "diaria"})
    with pytest.raises(ValueError, match="só a data final ou só o número"):
        agenda.montar_regra({"frequencia": "diaria", "vezes": 3, "ate": "2026-12-01"})
    with pytest.raises(ValueError, match="Frequência"):
        agenda.montar_regra({"frequencia": "anual", "vezes": 3})
    with pytest.raises(ValueError, match="Dia da semana"):
        agenda.montar_regra({"frequencia": "semanal", "dias_semana": ["xyz"], "vezes": 3})


def test_descrever_recorrencia_fala_o_que_vai_repetir():
    assert agenda.descrever_recorrencia({"frequencia": "diaria", "ate": "2026-12-15"}) == "repetindo todo dia, até 15/12/2026"
    assert agenda.descrever_recorrencia({"frequencia": "semanal", "dias_semana": ["ter", "qui"], "vezes": 8}) == "repetindo toda semana (terça e quinta), 8 vezes"
    assert agenda.descrever_recorrencia({"frequencia": "semanal", "intervalo": 2, "vezes": 4}) == "repetindo a cada 2 semanas, 4 vezes"


def test_criar_evento_recorrente_manda_regra_e_convidados(agenda_falsa):
    agenda.criar_evento("Leitura", "2026-11-11T21:00:00", 30, "Sala", "Livro", {"frequencia": "diaria", "vezes": 5}, ["ana@x.com"])
    (_, corpo, envio), = agenda_falsa.com("insert")
    assert corpo["recurrence"] == ["RRULE:FREQ=DAILY;COUNT=5"] and corpo["location"] == "Sala"
    assert corpo["attendees"] == [{"email": "ana@x.com"}] and envio == "all"
    assert corpo["end"]["dateTime"].startswith("2026-11-11T21:30:00")


def test_criar_evento_simples_nao_manda_convite(agenda_falsa):
    agenda.criar_evento("Dentista", "2026-11-12T15:00:00")
    (_, corpo, envio), = agenda_falsa.com("insert")
    assert "recurrence" not in corpo and "attendees" not in corpo and envio == "none"


# --- escopo --------------------------------------------------------------------------------------------------
def test_evento_de_serie_sem_escopo_manda_perguntar():
    with pytest.raises(ValueError, match="série"):
        agenda.escopo_de(ocorrencia(10), None)
    assert agenda.escopo_de({"serie": None}, None) is None
    assert agenda.escopo_de(ocorrencia(10), "Próximos") == "proximos"


def test_titulo_de_uma_serie_escolhe_a_proxima_ocorrencia():
    ev = google.escolher_evento([ocorrencia(10), ocorrencia(11), ocorrencia(12)], "leitura")
    assert ev["id"] == "serie1_10"
    outros = [ocorrencia(10), {**ocorrencia(11), "serie": "outra"}]
    with pytest.raises(ValueError, match="Mais de um evento"):
        google.escolher_evento(outros, "leitura")


# --- apagar um evento ----------------------------------------------------------------------------------------
def test_apagar_so_esta_ocorrencia(agenda_falsa):
    agenda.apagar_com_escopo(ocorrencia(12), "este")
    assert agenda_falsa.apagados() == ["serie1_12"]


def test_apagar_serie_inteira_apaga_o_mestre(agenda_falsa):
    agenda.apagar_com_escopo(ocorrencia(12), "todos")
    assert agenda_falsa.apagados() == ["serie1"]


def test_apagar_proximos_corta_a_serie_antes_da_ocorrencia(agenda_falsa):
    agenda_falsa.recursos["serie1"] = mestre_leitura()
    agenda.apagar_com_escopo(ocorrencia(12), "proximos")
    (_, alvo, corpo, _), = agenda_falsa.com("patch")
    assert alvo == "serie1"
    # 12/11 21:00 -03 = 13/11 00:00 UTC; um segundo antes
    assert corpo["recurrence"] == ["RRULE:FREQ=DAILY;UNTIL=20261112T235959Z"]
    assert agenda_falsa.apagados() == []


def test_apagar_proximos_da_primeira_ocorrencia_apaga_a_serie(agenda_falsa):
    agenda_falsa.recursos["serie1"] = mestre_leitura()
    agenda.apagar_com_escopo(ocorrencia(10), "proximos")
    assert agenda_falsa.apagados() == ["serie1"] and not agenda_falsa.com("patch")


# --- editar --------------------------------------------------------------------------------------------------
def test_editar_exige_alguma_mudanca_e_escopo():
    with pytest.raises(ValueError, match="Nenhuma mudança"):
        agenda.validar_edicao(ocorrencia(10), "este", {})
    with pytest.raises(ValueError, match="série"):
        agenda.validar_edicao(ocorrencia(10), None, {"novo_titulo": "X"})


def test_editar_serie_nao_muda_o_dia():
    with pytest.raises(ValueError, match="só dá para mudar o horário"):
        agenda.validar_edicao(ocorrencia(10), "todos", {"novo_inicio": "2026-11-11T21:00:00"})
    assert agenda.validar_edicao(ocorrencia(10), "este", {"novo_inicio": "2026-11-11T21:00:00"}) == "este"  # só este: pode mudar o dia


def test_editar_dia_inteiro_nao_aceita_horario():
    ev = {"id": "a", "titulo": "Aniversário", "inicio": date(2026, 11, 15), "fim": date(2026, 11, 16), "serie": None}
    with pytest.raises(ValueError, match="dia inteiro"):
        agenda.validar_edicao(ev, None, {"novo_inicio": "2026-11-15T10:00:00"})
    assert agenda.validar_edicao(ev, None, {"novo_titulo": "Aniversário do João"}) is None


def test_editar_evento_simples_remarca_mantendo_a_duracao(agenda_falsa):
    ini = sp("2026-11-12T15:00")
    ev = {"id": "e1", "titulo": "Dentista", "inicio": ini, "fim": ini + timedelta(minutes=45), "serie": None}
    agenda.aplicar_edicao(ev, None, {"novo_inicio": "2026-11-12T16:30:00"})
    (_, alvo, corpo, envio), = agenda_falsa.com("patch")
    assert alvo == "e1" and envio == "none"
    assert corpo["start"]["dateTime"].startswith("2026-11-12T16:30:00") and corpo["end"]["dateTime"].startswith("2026-11-12T17:15:00")


def test_editar_so_esta_ocorrencia_mexe_na_instancia(agenda_falsa):
    agenda.aplicar_edicao(ocorrencia(12), "este", {"nova_duracao_min": 60, "novo_titulo": "Leitura longa"})
    (_, alvo, corpo, _), = agenda_falsa.com("patch")
    assert alvo == "serie1_12" and corpo["summary"] == "Leitura longa"
    assert corpo["end"]["dateTime"].startswith("2026-11-12T22:00:00")


def test_editar_serie_inteira_desloca_o_horario_do_mestre(agenda_falsa):
    agenda_falsa.recursos["serie1"] = mestre_leitura()
    agenda.aplicar_edicao(ocorrencia(12), "todos", {"novo_inicio": "2026-11-12T20:00:00"})
    (_, alvo, corpo, _), = agenda_falsa.com("patch")
    assert alvo == "serie1"
    assert corpo["start"]["dateTime"].startswith("2026-11-10T20:00:00")  # o mestre (dia 10) vai para as 20h
    assert corpo["end"]["dateTime"].startswith("2026-11-10T20:30:00")


def test_editar_esta_e_as_proximas_divide_a_serie_preservando_o_numero_de_vezes(agenda_falsa):
    agenda_falsa.recursos["serie1"] = mestre_leitura(regra="RRULE:FREQ=DAILY;COUNT=30")
    agenda_falsa.restantes = 28  # 12/11 até o fim
    agenda.aplicar_edicao(ocorrencia(12), "proximos", {"novo_inicio": "2026-11-12T20:00:00"})
    (_, alvo, cortado, _), = agenda_falsa.com("patch")
    assert alvo == "serie1" and cortado["recurrence"][0].startswith("RRULE:FREQ=DAILY;UNTIL=20261112T")
    (_, novo, _), = agenda_falsa.com("insert")
    assert novo["recurrence"] == ["RRULE:FREQ=DAILY;COUNT=28"] and novo["summary"] == "Leitura diária"
    assert novo["start"]["dateTime"].startswith("2026-11-12T20:00:00")


def test_editar_proximos_mantem_a_data_final_da_serie(agenda_falsa):
    agenda_falsa.recursos["serie1"] = mestre_leitura(regra="RRULE:FREQ=DAILY;UNTIL=20261231T025959Z")
    agenda.aplicar_edicao(ocorrencia(12), "proximos", {"novo_titulo": "Leitura noturna"})
    (_, novo, _), = agenda_falsa.com("insert")
    assert novo["recurrence"] == ["RRULE:FREQ=DAILY;UNTIL=20261231T025959Z"] and novo["summary"] == "Leitura noturna"
    assert novo["start"]["dateTime"].startswith("2026-11-12T21:00:00")  # sem mudar horário, mantém o da ocorrência


def test_convidados_sao_acrescentados_sem_duplicar_e_avisam_por_email(agenda_falsa):
    agenda_falsa.recursos["e1"] = {"id": "e1", "attendees": [{"email": "ana@x.com"}, {"email": "bia@x.com"}]}
    ini = sp("2026-11-12T15:00")
    ev = {"id": "e1", "titulo": "Reunião", "inicio": ini, "fim": ini + timedelta(hours=1), "serie": None}
    m = {"adicionar_convidados": ["Ana@x.com", "caio@x.com"], "remover_convidados": ["bia@x.com"]}
    frase = agenda.pergunta_editar(ev, None, m)
    assert "Isso envia e-mail de aviso para Ana@x.com, caio@x.com, bia@x.com" in frase
    agenda.aplicar_edicao(ev, None, m)
    (_, _, corpo, envio), = agenda_falsa.com("patch")
    assert corpo["attendees"] == [{"email": "ana@x.com"}, {"email": "caio@x.com"}] and envio == "all"


# --- apagar em massa -----------------------------------------------------------------------------------------
def fixar_hoje(monkeypatch, dia="2026-11-10"):
    monkeypatch.setattr(agenda, "_hoje", lambda: date.fromisoformat(dia))


def test_apagar_em_massa_exige_titulo_ou_periodo():
    with pytest.raises(ValueError, match="amplo demais"):
        agenda.planejar_apagar(None)
    with pytest.raises(ValueError, match="amplo demais"):
        agenda.planejar_apagar("", None, None)


def test_apagar_intervalo_apaga_so_as_ocorrencias_do_periodo(monkeypatch, agenda_falsa):
    fixar_hoje(monkeypatch)
    pedidos = []

    def eventos(inicio, fim, limite=50):
        pedidos.append((inicio.date(), fim.date()))
        return [ocorrencia(d) for d in (12, 13, 14)] + [ocorrencia(13, titulo="Dentista", serie=None)]

    monkeypatch.setattr(google, "eventos_entre", eventos)
    plano = agenda.planejar_apagar("leitura", "2026-11-12", "2026-11-14")
    assert pedidos == [(date(2026, 11, 12), date(2026, 11, 15))]  # a data final entra inteira
    assert plano.total == 3 and plano.instancias == ["serie1_12", "serie1_13", "serie1_14"]
    assert agenda.pergunta_apagar_varios(plano) == "Apagar 3 eventos 'leitura', de 12/11 a 14/11."
    agenda.apagar_plano(plano)
    assert agenda_falsa.apagados() == ["serie1_12", "serie1_13", "serie1_14"] and not agenda_falsa.com("patch")


def test_apagar_todos_de_um_titulo_apaga_a_serie_que_comeca_no_futuro(monkeypatch, agenda_falsa):
    fixar_hoje(monkeypatch, "2026-11-09")
    agenda_falsa.recursos["serie1"] = mestre_leitura()  # começa em 10/11
    monkeypatch.setattr(google, "eventos_entre", lambda i, f, limite=50: [ocorrencia(d) for d in range(10, 15)])
    plano = agenda.planejar_apagar("leitura")
    assert plano.series_inteiras == ["serie1"] and plano.instancias == [] and plano.total == 5
    assert "inclui 1 série" in agenda.pergunta_apagar_varios(plano)
    agenda.apagar_plano(plano)
    assert agenda_falsa.apagados() == ["serie1"]


def test_apagar_todos_de_um_titulo_preserva_o_passado_da_serie(monkeypatch, agenda_falsa):
    fixar_hoje(monkeypatch, "2026-11-12")
    agenda_falsa.recursos["serie1"] = mestre_leitura()  # começou em 10/11: já tem passado
    monkeypatch.setattr(google, "eventos_entre", lambda i, f, limite=50: [ocorrencia(d) for d in range(12, 15)])
    plano = agenda.planejar_apagar("leitura")
    assert plano.series_inteiras == [] and len(plano.series_cortadas) == 1
    agenda.apagar_plano(plano)
    (_, alvo, corpo, _), = agenda_falsa.com("patch")
    assert alvo == "serie1" and corpo["recurrence"][0].startswith("RRULE:FREQ=DAILY;UNTIL=20261112T")
    assert agenda_falsa.apagados() == []


def test_apagar_em_massa_sem_achados_avisa(monkeypatch):
    fixar_hoje(monkeypatch)
    monkeypatch.setattr(google, "eventos_entre", lambda i, f, limite=50: [ocorrencia(12, titulo="Dentista", serie=None)])
    with pytest.raises(ValueError, match="Não achei nenhum evento"):
        agenda.planejar_apagar("leitura")
    with pytest.raises(ValueError, match="anterior"):
        agenda.planejar_apagar("x", "2026-11-14", "2026-11-12")


def test_apagar_em_lotes_de_50(agenda_falsa):
    agenda._apagar_ids([f"id{i}" for i in range(120)])
    assert len(agenda_falsa.apagados()) == 120


# --- ligação com o agente ------------------------------------------------------------------------------------
def test_ferramentas_da_agenda_registradas_e_apagar_em_massa_confirma(cfg, monkeypatch, agenda_falsa):

    nomes = {f.nome for f in construir_ferramentas(cfg)}
    assert {"criar_evento", "editar_evento", "apagar_evento", "apagar_eventos_em_massa"} <= nomes

    fixar_hoje(monkeypatch)
    monkeypatch.setattr(google, "eventos_entre", lambda i, f, limite=50: [ocorrencia(d) for d in (12, 13)])
    perguntas = []
    cliente = ClienteFalso([uso("apagar_eventos_em_massa", {"titulo": "leitura", "de": "2026-11-12", "ate": "2026-11-13"}), texto("Feito.")])
    agente = Agente(cfg, construir_ferramentas(cfg), lambda d: perguntas.append(d) or True, cliente)
    agente.responder("apague as leituras de 12 a 13")
    assert perguntas == ["Apagar 2 eventos 'leitura', de 12/11 a 13/11."]
    assert agenda_falsa.apagados() == ["serie1_12", "serie1_13"]


def test_apagar_em_massa_cancelado_nao_apaga(cfg, monkeypatch, agenda_falsa):

    fixar_hoje(monkeypatch)
    monkeypatch.setattr(google, "eventos_entre", lambda i, f, limite=50: [ocorrencia(12)])
    cliente = ClienteFalso([uso("apagar_eventos_em_massa", {"titulo": "leitura", "ate": "2026-11-20"}), texto("Ok.")])
    Agente(cfg, construir_ferramentas(cfg), lambda d: False, cliente).responder("apaga")
    assert agenda_falsa.apagados() == []


def test_criar_evento_recorrente_sem_fim_nao_pede_sim(cfg, monkeypatch, agenda_falsa):

    perguntas = []
    entrada = {"titulo": "Leitura", "inicio": "2026-11-11T21:00:00", "recorrencia": {"frequencia": "diaria"}}
    cliente = ClienteFalso([uso("criar_evento", entrada), texto("Até quando?")])
    Agente(cfg, construir_ferramentas(cfg), lambda d: perguntas.append(d) or True, cliente).responder("leitura todo dia")
    resultado = cliente.chamadas[1]["messages"][-1]["content"][0]
    assert perguntas == [] and resultado["is_error"] and "até quando" in resultado["content"]
    assert agenda_falsa.com("insert") == []
