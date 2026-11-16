"""Revisão semanal e dados do painel: metas por tópico + tarefas do Google Tasks, num só retrato.

- `coletar` é puro (recebe as tarefas já lidas) e por isso fácil de testar; `carregar` lê metas.md e o Google.
- `texto_falado` monta a revisão por código, sem IA, para nunca inventar número nem tarefa.
- A Eco sempre lê o Google na hora, então marcar uma tarefa como feita no celular, no painel ou por voz vale igual:
  na próxima leitura ela já aparece como concluída.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from eco.ferramentas import notas
from eco.lembretes import FUSO

DIAS_DA_SEMANA = 7


@dataclass
class Revisao:
    hoje: date
    topicos: dict[str, tuple[int, int]] = field(default_factory=dict)  # {tópico: (concluídas, total)}
    metas_abertas: list[notas.Meta] = field(default_factory=list)
    metas_concluidas: list[notas.Meta] = field(default_factory=list)
    atrasadas: list[dict] = field(default_factory=list)  # tarefas pendentes com prazo antes de hoje
    proximas: list[dict] = field(default_factory=list)  # prazo de hoje até daqui a 7 dias
    sem_data: list[dict] = field(default_factory=list)
    concluidas: list[dict] = field(default_factory=list)  # concluídas nos últimos 7 dias
    metas_atrasadas: list[notas.Meta] = field(default_factory=list)
    metas_proximas: list[notas.Meta] = field(default_factory=list)


def desde_semana(hoje: date) -> str:
    """Início da janela das tarefas concluídas (meia-noite de São Paulo, 7 dias atrás) como instante ISO em UTC."""
    inicio = datetime(hoje.year, hoje.month, hoje.day, tzinfo=FUSO) - timedelta(days=DIAS_DA_SEMANA)
    return inicio.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def coletar(pasta: Path, pendentes: list[dict], concluidas: list[dict], hoje: date) -> Revisao:
    limite = hoje + timedelta(days=DIAS_DA_SEMANA)
    todas = notas.metas(pasta)
    abertas = [m for m in todas if not m.concluida]
    rev = Revisao(
        hoje=hoje,
        topicos=notas.progresso_por_topico(pasta),
        metas_abertas=abertas,
        metas_concluidas=[m for m in todas if m.concluida],
        metas_atrasadas=[m for m in abertas if m.prazo and m.prazo < hoje],
        metas_proximas=[m for m in abertas if m.prazo and hoje <= m.prazo <= limite],
    )
    ordem = lambda t: (t["prazo"] or date.max, t["titulo"])  # noqa: E731
    rev.atrasadas = sorted((t for t in pendentes if t["prazo"] and t["prazo"] < hoje), key=ordem)
    rev.proximas = sorted((t for t in pendentes if t["prazo"] and hoje <= t["prazo"] <= limite), key=ordem)
    rev.sem_data = sorted((t for t in pendentes if not t["prazo"]), key=ordem)
    rev.concluidas = sorted(concluidas, key=lambda t: (t.get("concluida_em") or date.min, t["titulo"]), reverse=True)
    return rev


def carregar(pasta: Path, hoje: date | None = None) -> Revisao:
    """Lê as metas e as tarefas de todas as listas do Google (pendentes e concluídas na semana)."""
    from eco.ferramentas import google

    hoje = hoje or datetime.now(FUSO).date()
    return coletar(pasta, google.tarefas_pendentes(), google.tarefas_concluidas(desde_semana(hoje)), hoje)


def snapshot(rev: Revisao) -> dict[str, int]:
    """Quantas metas concluídas há por tópico: serve para dizer, na revisão seguinte, quanto avançou."""
    return {t: feitas for t, (feitas, _) in rev.topicos.items()}


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _lista(xs: list[str], limite: int = 5) -> str:
    extra = f" e mais {len(xs) - limite}" if len(xs) > limite else ""
    return "; ".join(xs[:limite]) + extra


def texto_falado(rev: Revisao, anterior: dict[str, int] | None = None) -> str:
    partes = ["Revisão da semana."]

    if rev.topicos:
        itens = []
        for topico, (feitas, total) in sorted(rev.topicos.items()):
            itens.append(f"{topico}, {'nenhuma' if feitas == 0 else feitas} de {total}")
        partes.append("Nas suas metas, concluídas por tópico: " + "; ".join(itens) + ".")
        if anterior is not None:
            novas = sum(max(0, feitas - anterior.get(t, 0)) for t, (feitas, _) in rev.topicos.items())
            if novas:
                partes.append(f"Desde a última revisão, {_plural(novas, 'meta concluída', 'metas concluídas')}.")
    else:
        partes.append("Você ainda não tem metas registradas.")

    n = len(rev.concluidas)
    partes.append(
        f"Você concluiu {_plural(n, 'tarefa', 'tarefas')} nos últimos 7 dias." if n else "Nenhuma tarefa concluída nos últimos 7 dias."
    )

    atrasadas = [t["titulo"] for t in rev.atrasadas] + [m.titulo for m in rev.metas_atrasadas]
    if atrasadas:
        partes.append(f"{_plural(len(atrasadas), 'item atrasado', 'itens atrasados')}: {_lista(atrasadas)}.")
    proximas = [t["titulo"] for t in rev.proximas] + [m.titulo for m in rev.metas_proximas]
    if proximas:
        partes.append(f"Vencem nos próximos 7 dias: {_lista(proximas)}.")
    if not atrasadas and not proximas:
        partes.append("Nada atrasado e nada vencendo nos próximos 7 dias.")
    return " ".join(partes)
