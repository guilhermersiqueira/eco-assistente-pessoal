"""Google Tasks avançado: várias listas, duplicadas, editar, mover, reabrir, concluídas e apagar em massa."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from eco.agente import Agente
from eco.config import carregar_config
from eco.ferramentas import construir_ferramentas, google, tarefas


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


class Pedido:
    def __init__(self, valor=None, efeito=None):
        self.valor, self.efeito = valor, efeito

    def execute(self):
        if self.efeito:
            self.efeito()
        return self.valor if self.valor is not None else {}


class Tarefas:
    def __init__(self, s):
        self.s = s

    def list(self, tasklist, **kw):
        self.s.chamadas.append(("list", tasklist, kw))
        return Pedido({"items": self.s.itens.get(tasklist, [])})

    def insert(self, tasklist, body):
        return Pedido(efeito=lambda: self.s.chamadas.append(("insert", tasklist, body)))

    def patch(self, tasklist, task, body):
        return Pedido(efeito=lambda: self.s.chamadas.append(("patch", tasklist, task, body)))

    def delete(self, tasklist, task):
        return Pedido(efeito=lambda: self.s.chamadas.append(("delete", tasklist, task)))


class Listas:
    def __init__(self, s):
        self.s = s

    def list(self, **kw):
        return Pedido({"items": self.s.listas})

    def insert(self, body):
        return Pedido(efeito=lambda: self.s.chamadas.append(("lista", body)))


class Servico:
    def __init__(self):
        self.chamadas: list = []
        self.listas = [{"id": "L1", "title": "Minhas tarefas"}, {"id": "L2", "title": "USP"}]
        self.itens: dict = {}

    def tasks(self):
        return Tarefas(self)

    def tasklists(self):
        return Listas(self)

    def com(self, tipo):
        return [c for c in self.chamadas if c[0] == tipo]


@pytest.fixture
def tasks_falso(monkeypatch):
    s = Servico()
    monkeypatch.setattr(google, "_servico", lambda nome, versao: s)
    return s


def tar(id, titulo, lista_id="L1", lista="Minhas tarefas", prazo=None, notas="", visivel=True):
    return {"id": id, "titulo": titulo, "prazo": prazo, "notas": notas, "lista_id": lista_id, "lista": lista, "lista_visivel": visivel, "concluida_em": None}


def duplicadas():
    return [
        tar("a", "Estudo de cálculo numérico", prazo=date(2026, 11, 12)),
        tar("b", "Estudo de cálculo numérico"),
        tar("c", "Estudo de cálculo numérico", "L2", "USP", date(2026, 11, 11)),
        tar("d", "Pagar boleto", prazo=date(2026, 11, 10)),
    ]


# --- leitura de todas as listas -------------------------------------------------------------------------------
def test_pendentes_vem_de_todas_as_listas_com_nome_da_lista(tasks_falso):
    tasks_falso.itens = {
        "L1": [{"id": "1", "title": "Pagar boleto", "due": "2026-11-10T00:00:00.000Z", "status": "needsAction"}],
        "L2": [{"id": "2", "title": "Lista de exercícios", "notes": "cap 3", "status": "needsAction"}, {"id": "3", "title": "", "status": "needsAction"}],
    }
    ts = google.tarefas_pendentes()
    assert [(t["titulo"], t["lista"], t["lista_id"]) for t in ts] == [("Pagar boleto", "Minhas tarefas", "L1"), ("Lista de exercícios", "USP", "L2")]
    assert ts[0]["prazo"] == date(2026, 11, 10) and ts[1]["notas"] == "cap 3" and ts[0]["lista_visivel"]
    assert google.tarefas_com_prazo() == [{"titulo": "Pagar boleto", "prazo": date(2026, 11, 10), "notas": ""}]  # o resumo do dia vê todas as listas


def test_concluidas_ignoram_pendentes_e_pedem_desde(tasks_falso):
    tasks_falso.itens = {
        "L1": [
            {"id": "1", "title": "Feita", "status": "completed", "completed": "2026-11-09T12:00:00.000Z"},
            {"id": "2", "title": "Aberta", "status": "needsAction"},
        ]
    }
    ts = google.tarefas_concluidas("2026-11-03T00:00:00Z")
    assert [t["titulo"] for t in ts] == ["Feita"] and ts[0]["concluida_em"] == date(2026, 11, 9)
    pedido = tasks_falso.com("list")[0][2]
    assert pedido["completedMin"] == "2026-11-03T00:00:00Z" and pedido["showCompleted"] and pedido["showHidden"]


# --- duplicadas ------------------------------------------------------------------------------------------------
def test_titulos_repetidos_saem_numerados_com_lista_e_data():
    with pytest.raises(ValueError, match="Mais de uma tarefa") as erro:
        google.escolher_tarefa(duplicadas(), "calculo")
    msg = str(erro.value)
    assert "1) Estudo de cálculo numérico (para 11/11), na lista USP" in msg or "1)" in msg
    assert msg.count(") Estudo") == 3 and "numero" in msg


def test_numero_lista_e_data_desfazem_a_duplicidade():
    ts = duplicadas()
    assert google.escolher_tarefa(ts, "calculo", lista="usp")["id"] == "c"
    assert google.escolher_tarefa(ts, "calculo", data="2026-11-12")["id"] == "a"
    ordenadas = [google.escolher_tarefa(ts, "calculo", numero=n)["id"] for n in (1, 2, 3)]
    assert sorted(ordenadas) == ["a", "b", "c"] and len(set(ordenadas)) == 3  # cada número é uma tarefa diferente
    with pytest.raises(ValueError, match="de 1 a 3"):
        google.escolher_tarefa(ts, "calculo", numero=4)
    with pytest.raises(ValueError, match="na lista Inexistente"):
        google.escolher_tarefa(ts, "calculo", lista="Inexistente")


def test_apagar_duplicada_pergunta_qual_e_apaga_a_escolhida(cfg, monkeypatch, tasks_falso):
    monkeypatch.setattr(google, "tarefas_pendentes", duplicadas)
    perguntas = []
    # 1ª tentativa: sem número, a ferramenta devolve as candidatas e nem pede o "sim"
    cliente = ClienteFalso([uso("apagar_tarefa", {"trecho": "calculo"}), texto("Qual?")])
    Agente(cfg, construir_ferramentas(cfg), lambda d: perguntas.append(d) or True, cliente).responder("apaga cálculo")
    resultado = cliente.chamadas[1]["messages"][-1]["content"][0]
    assert perguntas == [] and resultado["is_error"] and "1)" in resultado["content"] and tasks_falso.com("delete") == []
    # 2ª: com o número escolhido pelo dono
    cliente = ClienteFalso([uso("apagar_tarefa", {"trecho": "calculo", "numero": 1}), texto("Feito.")])
    Agente(cfg, construir_ferramentas(cfg), lambda d: perguntas.append(d) or True, cliente).responder("a primeira")
    assert len(perguntas) == 1 and perguntas[0].startswith("Apagar da lista de tarefas: Estudo de cálculo numérico")
    (_, lista_id, tarefa_id), = tasks_falso.com("delete")
    assert (tarefa_id, lista_id) in {("a", "L1"), ("b", "L1"), ("c", "L2")}


# --- criar e listas --------------------------------------------------------------------------------------------
def test_criar_tarefa_na_lista_citada_com_notas(tasks_falso):
    msg = tarefas.criar_tarefa("Lista 3", "2026-11-20", "usp", "capítulos 2 e 3")
    (_, lista_id, corpo), = tasks_falso.com("insert")
    assert lista_id == "L2" and corpo == {"title": "Lista 3", "due": "2026-11-20T00:00:00.000Z", "notes": "capítulos 2 e 3"}
    assert "na lista USP" in msg


def test_criar_tarefa_sem_lista_usa_a_padrao(tasks_falso):
    tarefas.criar_tarefa("Comprar pão")
    assert tasks_falso.com("insert")[0][1] == "@default"


def test_lista_inexistente_manda_perguntar_antes_de_criar(tasks_falso):
    with pytest.raises(ValueError, match="Pergunte ao dono se quer criá-la") as erro:
        tarefas.criar_tarefa("X", lista="Geek")
    assert "USP" in str(erro.value) and tasks_falso.com("insert") == []


def test_criar_lista_pede_sim_e_nao_duplica(cfg, tasks_falso):
    perguntas = []
    cliente = ClienteFalso([uso("criar_lista_tarefas", {"nome": "Geek"}), texto("Criei.")])
    Agente(cfg, construir_ferramentas(cfg), lambda d: perguntas.append(d) or True, cliente).responder("cria a lista Geek")
    assert perguntas == ["Criar a lista de tarefas Geek."] and tasks_falso.com("lista") == [("lista", {"title": "Geek"})]
    with pytest.raises(ValueError, match="já existe"):
        tarefas.criar_lista("usp")


def test_listar_agrupa_por_lista(monkeypatch):
    monkeypatch.setattr(google, "tarefas_pendentes", duplicadas)
    monkeypatch.setattr(google, "listas_de_tarefas", lambda: [{"id": "L1", "titulo": "Minhas tarefas"}, {"id": "L2", "titulo": "USP"}])
    saida = tarefas.listar()
    assert "[Minhas tarefas] Pagar boleto (para 10/11)" in saida and "[USP] Estudo de cálculo numérico (para 11/11)" in saida
    assert tarefas.listar("usp").count("\n") == 0 and "Pagar boleto" not in tarefas.listar("usp")


def test_concluidas_listam_com_data(monkeypatch):
    feitas = [{**tar("x", "Prova", visivel=False), "concluida_em": date(2026, 11, 9)}]
    monkeypatch.setattr(google, "tarefas_concluidas", lambda desde: feitas)
    assert tarefas.concluidas(7) == "Prova (concluída em 09/11)"
    monkeypatch.setattr(google, "tarefas_concluidas", lambda desde: [])
    assert "Nenhuma tarefa concluída nos últimos 7 dias" in tarefas.concluidas(7)


# --- reabrir ---------------------------------------------------------------------------------------------------
def test_reabrir_volta_a_pendente_e_pede_sim(cfg, monkeypatch, tasks_falso):
    feita = {**tar("x", "Prova de cálculo"), "concluida_em": date(2026, 11, 9)}
    monkeypatch.setattr(google, "tarefas_concluidas", lambda desde: [feita])
    perguntas = []
    cliente = ClienteFalso([uso("reabrir_tarefa", {"trecho": "prova"}), texto("Reaberta.")])
    Agente(cfg, construir_ferramentas(cfg), lambda d: perguntas.append(d) or True, cliente).responder("reabre a prova")
    assert perguntas[0].startswith("Reabrir a tarefa concluída: Prova de cálculo")
    assert tasks_falso.com("patch") == [("patch", "L1", "x", {"status": "needsAction", "completed": None})]


def test_reabrir_sem_achar_diz_concluida(monkeypatch):
    monkeypatch.setattr(google, "tarefas_concluidas", lambda desde: [])
    with pytest.raises(ValueError, match="tarefa concluída"):
        tarefas.localizar_concluida({"trecho": "x"})


# --- editar ----------------------------------------------------------------------------------------------------
def test_editar_exige_mudanca_e_coerencia():
    t = tar("a", "X")
    with pytest.raises(ValueError, match="Nenhuma mudança"):
        tarefas.validar_edicao(t, {})
    with pytest.raises(ValueError, match="só uma coisa"):
        tarefas.validar_edicao(t, {"nova_data": "2026-11-20", "remover_data": True})


def test_editar_titulo_data_e_notas_faz_um_patch(tasks_falso):
    tarefas.aplicar_edicao(tar("a", "Pagar boleto", prazo=date(2026, 11, 10)), {"novo_titulo": "Pagar boleto da faculdade", "nova_data": "2026-11-15", "notas": "vence sexta"})
    assert tasks_falso.com("patch") == [
        ("patch", "L1", "a", {"title": "Pagar boleto da faculdade", "due": "2026-11-15T00:00:00.000Z", "notes": "vence sexta"})
    ]


def test_remover_data_manda_null(tasks_falso):
    tarefas.aplicar_edicao(tar("a", "X", prazo=date(2026, 11, 10)), {"remover_data": True})
    assert tasks_falso.com("patch")[0][3] == {"due": None}


def test_mover_cria_no_destino_e_so_depois_apaga_a_original(tasks_falso):
    tarefas.aplicar_edicao(tar("a", "Estudo", prazo=date(2026, 11, 12), notas="cap 1"), {"mover_para_lista": "USP", "novo_titulo": "Estudo de cálculo"})
    assert [c[0] for c in tasks_falso.chamadas][-2:] == ["insert", "delete"]  # cria antes de apagar
    (_, destino, corpo), = tasks_falso.com("insert")
    assert destino == "L2" and corpo == {"title": "Estudo de cálculo", "due": "2026-11-12T00:00:00.000Z", "notes": "cap 1"}
    assert tasks_falso.com("delete") == [("delete", "L1", "a")]


def test_mover_para_a_mesma_lista_ou_inexistente_e_erro(tasks_falso):
    with pytest.raises(ValueError, match="já está"):
        tarefas.validar_edicao(tar("a", "X"), {"mover_para_lista": "Minhas tarefas"})
    with pytest.raises(ValueError, match="não existe"):
        tarefas.validar_edicao(tar("a", "X"), {"mover_para_lista": "Geek"})


def test_editar_le_de_volta_e_cancelar_nao_altera(cfg, monkeypatch, tasks_falso):
    monkeypatch.setattr(google, "tarefas_pendentes", duplicadas)
    perguntas = []
    cliente = ClienteFalso([uso("editar_tarefa", {"trecho": "boleto", "nova_data": "2026-11-20"}), texto("Ok.")])
    Agente(cfg, construir_ferramentas(cfg), lambda d: perguntas.append(d) or False, cliente).responder("muda o boleto")
    assert perguntas == ["Alterar a tarefa Pagar boleto (para 10/11), na lista Minhas tarefas: nova data 20/11."]
    assert tasks_falso.com("patch") == []


# --- apagar em massa -------------------------------------------------------------------------------------------
def test_massa_exige_titulo_ou_periodo():
    with pytest.raises(ValueError, match="amplo demais"):
        tarefas.planejar_apagar(None)
    with pytest.raises(ValueError, match="anterior"):
        tarefas.planejar_apagar("x", "2026-11-12", "2026-11-10")


def test_massa_por_titulo(monkeypatch, tasks_falso):
    monkeypatch.setattr(google, "tarefas_pendentes", duplicadas)
    plano = tarefas.planejar_apagar("calculo")
    assert len(plano.tarefas) == 3
    assert tarefas.pergunta_apagar_varios(plano) == "Apagar 3 tarefas com 'calculo' no título."
    assert tarefas.apagar_plano(plano) == "Apagadas 3 tarefas."
    assert {(c[1], c[2]) for c in tasks_falso.com("delete")} == {("L1", "a"), ("L1", "b"), ("L2", "c")}


def test_massa_por_periodo_ignora_tarefas_sem_data(monkeypatch):
    monkeypatch.setattr(google, "tarefas_pendentes", duplicadas)
    plano = tarefas.planejar_apagar(None, "2026-11-11", "2026-11-12")
    assert sorted(t["id"] for t in plano.tarefas) == ["a", "c"]
    assert tarefas.pergunta_apagar_varios(plano) == "Apagar 2 tarefas, com vencimento de 11/11 até 12/11."


def test_massa_titulo_mais_periodo_mais_lista(monkeypatch):
    monkeypatch.setattr(google, "tarefas_pendentes", duplicadas)
    monkeypatch.setattr(google, "listas_de_tarefas", lambda: [{"id": "L1", "titulo": "Minhas tarefas"}, {"id": "L2", "titulo": "USP"}])
    plano = tarefas.planejar_apagar("estudo", "2026-11-01", "2026-11-30", "USP")
    assert [t["id"] for t in plano.tarefas] == ["c"]
    with pytest.raises(ValueError, match="Não achei nenhuma"):
        tarefas.planejar_apagar("academia")


def test_massa_pede_sim_e_cancelar_nao_apaga(cfg, monkeypatch, tasks_falso):
    monkeypatch.setattr(google, "tarefas_pendentes", duplicadas)
    cliente = ClienteFalso([uso("apagar_tarefas_em_massa", {"titulo": "calculo"}), texto("Ok.")])
    Agente(cfg, construir_ferramentas(cfg), lambda d: False, cliente).responder("apaga todas de cálculo")
    assert tasks_falso.com("delete") == []


def test_massa_conta_falhas(monkeypatch):
    monkeypatch.setattr(google, "tarefas_pendentes", duplicadas)

    def apagar(i, lista="@default"):
        if i == "b":
            raise RuntimeError("falhou")

    monkeypatch.setattr(google, "apagar_tarefa", apagar)
    with pytest.raises(RuntimeError, match="Apaguei 2 de 3"):
        tarefas.apagar_plano(tarefas.planejar_apagar("calculo"))


def test_ferramentas_de_tarefas_registradas(cfg):
    nomes = {f.nome for f in construir_ferramentas(cfg)}
    assert {"criar_tarefa", "listar_tarefas", "listar_listas_tarefas", "criar_lista_tarefas", "tarefas_concluidas", "concluir_tarefa",
            "reabrir_tarefa", "editar_tarefa", "apagar_tarefa", "apagar_tarefas_em_massa"} <= nomes
