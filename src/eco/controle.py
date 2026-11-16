"""Dados e ações do Painel de controle (passo 5 do escopo, 11/11/2026): tarefas, eventos e pendências de e-mail.

- `carregar` junta metas e tarefas (`revisao`), os eventos de hoje e dos próximos 7 dias e os e-mails com estrela. Cada
  parte falha sozinha: sem Gmail, o resto aparece; o aviso diz o que faltou.
- `AcoesGoogle` é o que os botões do Painel fazem, reaproveitando as funções das ferramentas de voz (as mesmas regras de
  séries recorrentes, listas e notas). A tela recebe um objeto com esses métodos; os testes passam um falso.
- Tarefa importante = linha "Importante: motivo" nas notas da tarefa (`tarefas.motivo_importante`).
- Pendência de e-mail = e-mail com estrela no Gmail; "Resolvido" tira a estrela.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from eco import revisao
from eco.lembretes import FUSO

DIAS_AGENDA = 7


@dataclass
class Controle:
    rev: revisao.Revisao
    eventos: list[dict] = field(default_factory=list)  # {id, titulo, inicio, fim, serie}, de hoje até +7 dias
    estrelas: list[dict] = field(default_factory=list)  # {id, de, assunto, quando}
    avisos: list[str] = field(default_factory=list)


def carregar(pasta: Path, hoje: date | None = None) -> Controle:
    from eco.ferramentas import emails, google

    hoje = hoje or datetime.now(FUSO).date()
    avisos: list[str] = []
    try:
        rev = revisao.carregar(pasta, hoje)
    except Exception as erro:  # sem rede, sem autorização: ao menos as metas
        rev = revisao.coletar(pasta, [], [], hoje)
        avisos.append(f"tarefas indisponíveis ({type(erro).__name__})")
    eventos: list[dict] = []
    try:
        comeco = datetime(hoje.year, hoje.month, hoje.day, tzinfo=FUSO)
        eventos = google.eventos_entre(comeco, comeco + timedelta(days=DIAS_AGENDA + 1), limite=100)
    except Exception as erro:
        avisos.append(f"agenda indisponível ({type(erro).__name__})")
    estrelas: list[dict] = []
    try:
        estrelas = emails.com_estrela()
    except Exception as erro:
        avisos.append(f"Gmail indisponível ({type(erro).__name__})")
    return Controle(rev, eventos, estrelas, avisos)


class AcoesGoogle:
    """O que cada botão do Painel faz. Tudo roda fora da thread da tela."""

    # --- tarefas -----------------------------------------------------------------------------------------
    def concluir_tarefa(self, t: dict) -> None:
        from eco.ferramentas import google

        google.concluir_tarefa(t["id"], t["lista_id"])

    def reabrir_tarefa(self, t: dict) -> None:
        from eco.ferramentas import tarefas

        tarefas.reabrir(t)

    def criar_tarefa(self, d: dict) -> None:
        """d: titulo, data (AAAA-MM-DD ou None), lista (nome ou None), notas, importante (None ou motivo)."""
        from eco.ferramentas import tarefas

        notas = tarefas.notas_com_importante(d.get("notas"), d.get("importante"))
        tarefas.criar_tarefa(d["titulo"], d.get("data"), d.get("lista"), notas or None)

    def editar_tarefa(self, t: dict, d: dict) -> None:
        """Só manda o que mudou; a marca de importante vai junto com as notas."""
        from eco.ferramentas import tarefas

        m: dict = {}
        if d["titulo"] != t["titulo"]:
            m["novo_titulo"] = d["titulo"]
        prazo = t["prazo"].isoformat() if t.get("prazo") else None
        if d.get("data") != prazo:
            m["nova_data" if d.get("data") else "remover_data"] = d.get("data") or True
        notas = tarefas.notas_com_importante(d.get("notas"), d.get("importante"))
        if notas != (t.get("notas") or ""):
            m["notas"] = notas or " "  # o Google não aceita apagar as notas com vazio pela edição: um espaço limpa
        if d.get("lista") and d["lista"] != t.get("lista"):
            m["mover_para_lista"] = d["lista"]
        if m:
            tarefas.aplicar_edicao(t, m)

    def apagar_tarefa(self, t: dict) -> None:
        from eco.ferramentas import tarefas

        tarefas.apagar(t)

    def importante(self, t: dict, motivo: str | None) -> None:
        from eco.ferramentas import tarefas

        tarefas.marcar_importante(t, motivo)

    def listas(self) -> list[str]:
        from eco.ferramentas import google

        return [l["titulo"] for l in google.listas_de_tarefas()]

    # --- eventos -----------------------------------------------------------------------------------------
    def criar_evento(self, d: dict) -> None:
        """d: titulo, inicio (AAAA-MM-DDTHH:MM), duracao_min, local."""
        from eco.ferramentas import agenda

        agenda.criar_evento(d["titulo"], d["inicio"], int(d.get("duracao_min") or 60), d.get("local") or None)

    def editar_evento(self, ev: dict, escopo: str | None, d: dict) -> None:
        from eco.ferramentas import agenda

        m: dict = {}
        if d["titulo"] != ev["titulo"]:
            m["novo_titulo"] = d["titulo"]
        if isinstance(ev["inicio"], datetime):
            atual = ev["inicio"].astimezone(FUSO)
            if d["inicio"] != f"{atual:%Y-%m-%dT%H:%M}":
                m["novo_inicio"] = d["inicio"]
            duracao = int((ev["fim"] - ev["inicio"]).total_seconds() // 60) if ev.get("fim") else 60
            if int(d.get("duracao_min") or duracao) != duracao:
                m["nova_duracao_min"] = int(d["duracao_min"])
        if d.get("local"):
            m["local"] = d["local"]
        if m:
            agenda.aplicar_edicao(ev, escopo, m)

    def apagar_evento(self, ev: dict, escopo: str | None) -> None:
        from eco.ferramentas import agenda

        agenda.apagar_com_escopo(ev, escopo)

    # --- e-mails -----------------------------------------------------------------------------------------
    def resolver_email(self, e: dict) -> None:
        from eco.ferramentas import emails

        emails.marcar_estrela(e["id"], False)
