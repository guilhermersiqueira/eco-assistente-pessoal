"""Google Tasks avançado: várias listas, editar, mover, reabrir, consultar concluídas e apagar em massa.

Mesma ideia de `agenda.py`: regra pura e chamadas à API por `google._servico` (os testes trocam por um falso).
O "sim" do dono é pedido pelas ferramentas em `ferramentas/__init__.py`; aqui as `pergunta_*` só montam a
frase lida de volta e as demais agem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from eco.ferramentas import google
from eco.ferramentas.google import _sem_acento, descrever_tarefa

CAMPOS_EDICAO = ("novo_titulo", "nova_data", "remover_data", "notas", "mover_para_lista")
DIAS_CONCLUIDAS_PARA_REABRIR = 90


def _tasks():
    return google._servico("tasks", "v1")


def _due(dia: str) -> str:
    datetime.fromisoformat(dia)  # valida
    return f"{dia[:10]}T00:00:00.000Z"


def _tem(valor) -> bool:
    return valor not in (None, "", False)


# --- listas ---------------------------------------------------------------------------------------------------
def achar_lista(nome: str) -> dict:
    """A lista pelo nome (sem acento nem caixa). Se não existir, erro para o Claude perguntar se o dono quer criá-la."""
    alvo = _sem_acento(nome)
    listas = google.listas_de_tarefas()
    achada = next((l for l in listas if _sem_acento(l["titulo"]) == alvo), None)
    if achada is None:
        existentes = ", ".join(l["titulo"] for l in listas) or "nenhuma"
        raise ValueError(
            f"A lista '{nome}' não existe (listas: {existentes}). Pergunte ao dono se quer criá-la; se sim, use criar_lista_tarefas. Não crie sozinha."
        )
    return achada


def listar_listas() -> str:
    listas = google.listas_de_tarefas()
    return ", ".join(l["titulo"] for l in listas) if listas else "Nenhuma lista de tarefas."


def criar_lista(nome: str) -> str:
    nome = nome.strip()
    if not nome:
        raise ValueError("Preciso do nome da lista.")
    if any(_sem_acento(l["titulo"]) == _sem_acento(nome) for l in google.listas_de_tarefas()):
        raise ValueError(f"A lista '{nome}' já existe.")
    _tasks().tasklists().insert(body={"title": nome}).execute()
    return f"Lista criada: {nome}."


# --- criar e consultar ----------------------------------------------------------------------------------------
def criar_tarefa(titulo: str, data: str | None = None, lista: str | None = None, notas: str | None = None) -> str:
    destino = achar_lista(lista) if lista else None
    corpo: dict = {"title": titulo}
    if data:
        corpo["due"] = _due(data)
    if notas:
        corpo["notes"] = notas
    _tasks().tasks().insert(tasklist=destino["id"] if destino else "@default", body=corpo).execute()
    return f"Tarefa criada: {titulo}" + (f", para {data[:10]}" if data else "") + (f", na lista {destino['titulo']}." if destino else ".")


def _linha(t: dict) -> str:
    return t["titulo"] + (f" (para {t['prazo']:%d/%m})" if t["prazo"] else "") + (f" — {t['notas']}" if t.get("notas") else "")


def listar(lista: str | None = None) -> str:
    """Pendentes de todas as listas, agrupadas por lista (ou só a citada)."""
    tarefas = google.tarefas_pendentes()
    if lista:
        achada = achar_lista(lista)
        tarefas = [t for t in tarefas if t["lista_id"] == achada["id"]]
    if not tarefas:
        return "Nenhuma tarefa pendente."
    grupos: dict[str, list[dict]] = {}
    for t in sorted(tarefas, key=google._ordem_tarefa):
        grupos.setdefault(t["lista"], []).append(t)
    if len(grupos) == 1 and not any(t.get("lista_visivel") for t in tarefas):
        return "\n".join(_linha(t) for t in tarefas)
    return "\n".join(f"[{nome}] {_linha(t)}" for nome, ts in grupos.items() for t in ts)


def _desde(dias: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")


def concluidas(dias: int = 7, lista: str | None = None) -> str:
    tarefas = google.tarefas_concluidas(_desde(dias))
    if lista:
        achada = achar_lista(lista)
        tarefas = [t for t in tarefas if t["lista_id"] == achada["id"]]
    if not tarefas:
        return f"Nenhuma tarefa concluída nos últimos {dias} dias."
    tarefas.sort(key=lambda t: (t["concluida_em"] or date.min, t["titulo"]), reverse=True)
    return "\n".join(
        f"{t['titulo']} (concluída em {t['concluida_em']:%d/%m})" + (f", lista {t['lista']}" if t.get("lista_visivel") else "")
        for t in tarefas
    )


# --- localizar ------------------------------------------------------------------------------------------------
def localizar(a: dict) -> dict:
    """A tarefa pendente pedida na entrada da ferramenta (trecho, lista, data, numero)."""
    return google.localizar_tarefa(a["trecho"], a.get("lista"), a.get("data"), a.get("numero"))


def localizar_concluida(a: dict) -> dict:
    tarefas = google.tarefas_concluidas(_desde(DIAS_CONCLUIDAS_PARA_REABRIR))
    return google.escolher_tarefa(tarefas, a["trecho"], a.get("lista"), a.get("data"), a.get("numero"), situacao="concluída")


# --- apagar e reabrir (uma) -----------------------------------------------------------------------------------
def pergunta_apagar(t: dict) -> str:
    return f"Apagar da lista de tarefas: {descrever_tarefa(t)}."


def apagar(t: dict) -> str:
    google.apagar_tarefa(t["id"], t["lista_id"])
    return f"Tarefa apagada: {descrever_tarefa(t)}."


def pergunta_reabrir(t: dict) -> str:
    return f"Reabrir a tarefa concluída: {descrever_tarefa(t)}."


def reabrir(t: dict) -> str:
    _tasks().tasks().patch(tasklist=t["lista_id"], task=t["id"], body={"status": "needsAction", "completed": None}).execute()
    return f"Tarefa reaberta: {descrever_tarefa(t)}."


# --- editar ---------------------------------------------------------------------------------------------------
def validar_edicao(t: dict, m: dict) -> None:
    if not any(_tem(m.get(k)) for k in CAMPOS_EDICAO):
        raise ValueError("Nenhuma mudança informada: diga o que alterar (título, data, anotações ou lista).")
    if _tem(m.get("nova_data")) and _tem(m.get("remover_data")):
        raise ValueError("Peça só uma coisa: nova data ou remover a data.")
    if _tem(m.get("nova_data")):
        _due(m["nova_data"])
    if _tem(m.get("mover_para_lista")):
        destino = achar_lista(m["mover_para_lista"])
        if destino["id"] == t["lista_id"]:
            raise ValueError(f"A tarefa já está na lista {destino['titulo']}.")


def _descrever_mudancas(m: dict) -> str:
    itens = []
    if _tem(m.get("novo_titulo")):
        itens.append(f"novo título {m['novo_titulo']}")
    if _tem(m.get("nova_data")):
        itens.append(f"nova data {_data(m['nova_data']):%d/%m}")
    if _tem(m.get("remover_data")):
        itens.append("sem data")
    if _tem(m.get("notas")):
        itens.append("novas anotações")
    if _tem(m.get("mover_para_lista")):
        itens.append(f"mover para a lista {m['mover_para_lista']}")
    return "; ".join(itens)


def _data(texto: str) -> date:
    return date.fromisoformat(str(texto)[:10])


def pergunta_editar(t: dict, m: dict) -> str:
    return f"Alterar a tarefa {descrever_tarefa(t)}: {_descrever_mudancas(m)}."


def aplicar_edicao(t: dict, m: dict) -> str:
    validar_edicao(t, m)
    campos: dict = {}
    if _tem(m.get("novo_titulo")):
        campos["title"] = m["novo_titulo"]
    if _tem(m.get("nova_data")):
        campos["due"] = _due(m["nova_data"])
    if _tem(m.get("remover_data")):
        campos["due"] = None
    if _tem(m.get("notas")):
        campos["notes"] = m["notas"]
    if _tem(m.get("mover_para_lista")):
        # o Google só move dentro da mesma lista: cria na de destino e apaga a original (nessa ordem, para não perder nada)
        destino = achar_lista(m["mover_para_lista"])
        corpo = {"title": t["titulo"]}
        if t["prazo"]:
            corpo["due"] = f"{t['prazo']:%Y-%m-%d}T00:00:00.000Z"
        if t.get("notas"):
            corpo["notes"] = t["notas"]
        corpo.update({k: v for k, v in campos.items() if v is not None})
        if "due" in campos and campos["due"] is None:
            corpo.pop("due", None)
        _tasks().tasks().insert(tasklist=destino["id"], body=corpo).execute()
        google.apagar_tarefa(t["id"], t["lista_id"])
    else:
        _tasks().tasks().patch(tasklist=t["lista_id"], task=t["id"], body=campos).execute()
    return f"Tarefa alterada: {descrever_tarefa(t)}: {_descrever_mudancas(m)}."


# --- apagar em massa -------------------------------------------------------------------------------------------
@dataclass
class PlanoApagar:
    tarefas: list[dict]
    titulo: str | None
    de: date | None
    ate: date | None
    lista: str | None


def planejar_apagar(titulo: str | None, de: str | None = None, ate: str | None = None, lista: str | None = None) -> PlanoApagar:
    """Decide o que seria apagado, sem apagar nada. Exige título e/ou período de vencimento."""
    alvo = _sem_acento(titulo or "")
    if not alvo and not de and not ate:
        raise ValueError("Comando amplo demais: pergunte ao dono o título das tarefas ou o período de vencimento (de que data a que data). Não apague nada ainda.")
    inicio, fim = (_data(de) if de else None), (_data(ate) if ate else None)
    if inicio and fim and fim < inicio:
        raise ValueError("A data final é anterior à inicial.")
    tarefas = google.tarefas_pendentes()
    if lista:
        achada = achar_lista(lista)
        tarefas = [t for t in tarefas if t["lista_id"] == achada["id"]]
    if alvo:
        tarefas = [t for t in tarefas if alvo in _sem_acento(t["titulo"])]
    if inicio or fim:  # com período, só entram tarefas que têm vencimento dentro dele
        tarefas = [t for t in tarefas if t["prazo"] and (not inicio or t["prazo"] >= inicio) and (not fim or t["prazo"] <= fim)]
    if not tarefas:
        raise ValueError("Não achei nenhuma tarefa pendente com esse título e período.")
    return PlanoApagar(sorted(tarefas, key=google._ordem_tarefa), titulo, inicio, fim, lista)


def pergunta_apagar_varios(plano: PlanoApagar) -> str:
    n = len(plano.tarefas)
    texto = f"Apagar {n} tarefa" + ("s" if n != 1 else "")
    if plano.titulo:
        texto += f" com '{plano.titulo}' no título"
    if plano.lista:
        texto += f", na lista {plano.lista}"
    if plano.de or plano.ate:
        de = f"de {plano.de:%d/%m} " if plano.de else ""
        ate = f"até {plano.ate:%d/%m}" if plano.ate else ""
        texto += f", com vencimento {de}{ate}".rstrip()
    return texto + "."


def apagar_plano(plano: PlanoApagar) -> str:
    falhas = 0
    for t in plano.tarefas:
        try:
            google.apagar_tarefa(t["id"], t["lista_id"])
        except Exception as erro:  # já apagada (404) conta como feita; o resto é contado
            if getattr(getattr(erro, "resp", None), "status", None) not in (404, 410):
                falhas += 1
    if falhas:
        raise RuntimeError(f"Apaguei {len(plano.tarefas) - falhas} de {len(plano.tarefas)} tarefas; {falhas} falharam.")
    return f"Apagadas {len(plano.tarefas)} tarefas."


# --- importante (decisão do dono, 11/11/2026: a marca fica nas notas da própria tarefa, visível no celular) ----------
_MARCA_IMPORTANTE = re.compile(r"^[ \t]*importante[ \t]*(?::[ \t]*(.*?))?[ \t]*$", re.IGNORECASE | re.MULTILINE)


def motivo_importante(notas: str | None) -> str | None:
    """None se a tarefa não é importante; senão o motivo ou tópico escrito depois de "Importante:" (pode ser "")."""
    m = _MARCA_IMPORTANTE.search(notas or "")
    return None if m is None else (m.group(1) or "").strip()


def notas_com_importante(notas: str | None, motivo: str | None) -> str:
    """As notas com a linha "Importante: motivo" no começo (motivo None tira a marca). O resto das notas fica igual."""
    resto = _MARCA_IMPORTANTE.sub("", notas or "").strip("\n")
    resto = re.sub(r"\n{3,}", "\n\n", resto).strip()
    if motivo is None:
        return resto
    linha = "Importante" + (f": {' '.join(motivo.split())}" if motivo.strip() else "")
    return linha + (f"\n{resto}" if resto else "")


def marcar_importante(t: dict, motivo: str | None) -> str:
    novas = notas_com_importante(t.get("notas"), motivo)
    _tasks().tasks().patch(tasklist=t["lista_id"], task=t["id"], body={"notes": novas}).execute()
    if motivo is None:
        return f"Tirei a marca de importante: {descrever_tarefa(t)}."
    return f"Marcada como importante: {descrever_tarefa(t)}" + (f" ({motivo.strip()})." if motivo.strip() else ".")
