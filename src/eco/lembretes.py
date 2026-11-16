"""Lembretes: resumo do dia na primeira abertura de cada dia e aviso antes de cada compromisso.

- Tudo é calculado no fuso America/Sao_Paulo, qualquer que seja o fuso do computador.
- `~/.eco/lembretes.json` guarda a data do último resumo e os avisos já dados (um por compromisso e horário).
- Aviso perdido (app fechado na hora) é ignorado: só dispara se estiver dentro da tolerância do momento certo.
- A thread de verificação nunca fala: ela põe o texto numa fila, e a thread de voz fala quando estiver livre.
- Modo foco (só por voz, desde 12/11/2026; o foco ligado por evento da Agenda com "foco" no título foi retirado a
  pedido do dono): enquanto ativo, a thread de voz não fala a fila; os avisos aparecem só no balão do sistema e
  ficam guardados até o dono sair do foco.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")
FALA_FOCO_LIGADO = "Modo foco ligado. Fico em silêncio até você dizer: Ei Eco, sair do modo foco."


@dataclass(frozen=True)
class Evento:
    id: str
    titulo: str
    inicio: datetime | date  # datetime com fuso (tem horário) ou date (dia inteiro)
    fim: datetime | date | None = None

    @property
    def dia_inteiro(self) -> bool:
        return not isinstance(self.inicio, datetime)


@dataclass(frozen=True)
class Pendencia:
    titulo: str
    prazo: date
    importante: str | None = None  # None = comum; senão o motivo/tópico ("" se não disse)

    def falada(self) -> str:
        if self.importante is None:
            return self.titulo
        return f"{self.titulo}, importante" + (f" ({self.importante})" if self.importante else "")


HORA_AMANHECER = 6  # o resumo do dia só sai a partir daqui (a Eco fica ligada a noite toda: não diz "bom dia" à meia-noite)
HORA_NOITE = (20, 30)  # a partir daqui, o primeiro contato do dia com a Eco traz as pendências do dia


class Fonte(Protocol):
    def eventos(self, inicio: datetime, fim: datetime) -> list[Evento]: ...

    def pendencias(self) -> list[Pendencia]: ...


def em_sp(agora: datetime) -> datetime:
    """Converte para o fuso de São Paulo. Um datetime sem fuso é tomado como hora local do computador."""
    if agora.tzinfo is None:
        agora = agora.astimezone()
    return agora.astimezone(FUSO)


def hora_falada(quando: datetime) -> str:
    q = em_sp(quando)
    if q.minute == 0:
        return f"{q.hour} hora" + ("" if q.hour == 1 else "s")
    return f"{q.hour} e {q.minute}"


def frase_de_emails(emails: dict | None, comeco: str = "E") -> str | None:
    """Uma frase curta sobre os e-mails não lidos da caixa principal (só quantidade e remetentes; nada do texto)."""
    if not emails or not emails.get("total"):
        return None
    n = emails["total"]
    quantidade = f"mais de {n}" if emails.get("mais_de") else str(n)
    plural = emails.get("mais_de") or n > 1
    nomes = emails.get("remetentes") or []
    quem = ""
    if nomes:
        quem = ", os mais recentes de " + (", ".join(nomes[:-1]) + " e " + nomes[-1] if len(nomes) > 1 else nomes[0])
    return f"{comeco} {quantidade} e-mail{'s' if plural else ''} não lido{'s' if plural else ''} na caixa principal{quem}."


def _lista(xs: list[str], limite: int = 5) -> str:
    extra = f" e mais {len(xs) - limite}" if len(xs) > limite else ""
    return "; ".join(xs[:limite]) + extra


def _ordenar(ps: list[Pendencia]) -> list[Pendencia]:
    return sorted(ps, key=lambda p: (p.importante is None, p.prazo))  # importantes primeiro


def frase_de_estrelas(estrelas: list[dict] | None, comeco: str = "E") -> str | None:
    """Os e-mails com estrela (pendências de e-mail): quantos e de quem / sobre o quê (só cabeçalho)."""
    if not estrelas:
        return None
    n = len(estrelas)
    itens = [f"{e['de']}, {e['assunto']}" for e in estrelas]
    return f"{comeco} {n} e-mail{'s' if n > 1 else ''} pendente{'s' if n > 1 else ''}: {_lista(itens, 3)}."


def montar_resumo(
    eventos: list[Evento], pendencias: list[Pendencia], agora: datetime, emails: dict | None = None, estrelas: list[dict] | None = None
) -> str:
    """Texto falado do resumo do dia (amanhecer). Montado por código (sem IA) para nunca inventar compromisso.
    Pendências: as de hoje, as que ficaram de ontem e as mais antigas, cada grupo numa frase; importantes primeiro."""
    a = em_sp(agora)
    hoje = a.date()
    ontem = hoje - timedelta(days=1)
    saudacao = "Bom dia" if a.hour < 12 else "Boa tarde" if a.hour < 18 else "Boa noite"

    com_hora = sorted((e for e in eventos if not e.dia_inteiro and em_sp(e.inicio) >= a), key=lambda e: e.inicio)
    o_dia_todo = [e for e in eventos if e.dia_inteiro]
    itens = [f"às {hora_falada(e.inicio)}, {e.titulo}" for e in com_hora] + [f"{e.titulo}, o dia todo" for e in o_dia_todo]
    de_hoje = [p.falada() for p in _ordenar([p for p in pendencias if p.prazo == hoje])]
    de_ontem = [p.falada() for p in _ordenar([p for p in pendencias if p.prazo == ontem])]
    antigas = [p.falada() for p in _ordenar([p for p in pendencias if p.prazo < ontem])]

    partes = [f"{saudacao}!"]
    if itens:
        n = len(itens)
        partes.append(f"Hoje você tem {n} compromisso{'s' if n > 1 else ''}: {_lista(itens)}.")
    if de_hoje:
        partes.append(f"Na sua lista pra hoje: {_lista(de_hoje)}.")
    if de_ontem:
        partes.append(f"Ficou de ontem: {_lista(de_ontem)}.")
    if antigas:
        n = len(antigas)
        partes.append(f"E {n} coisa{'s' if n > 1 else ''} atrasada{'s' if n > 1 else ''} de antes: {_lista(antigas)}.")
    if len(partes) == 1:
        partes.append("Seu dia está livre: nenhum compromisso nem lembrete pra hoje.")
    if frase := frase_de_estrelas(estrelas):
        partes.append(frase)
    if frase := frase_de_emails(emails):
        partes.append(frase)
    return " ".join(partes)


def montar_noite(pendencias: list[Pendencia], agora: datetime, estrelas: list[dict] | None = None) -> str:
    """Primeiro contato depois das 20h30: o que ficou pendente de hoje (e o atraso que segue aberto)."""
    hoje = em_sp(agora).date()
    de_hoje = [p.falada() for p in _ordenar([p for p in pendencias if p.prazo == hoje])]
    atrasadas = [p.falada() for p in _ordenar([p for p in pendencias if p.prazo < hoje])]
    partes = []
    if de_hoje:
        partes.append(f"Ainda aberto de hoje: {_lista(de_hoje)}.")
    if atrasadas:
        n = len(atrasadas)
        partes.append(f"Atrasada{'s' if n > 1 else ''}: {_lista(atrasadas)}.")
    if frase := frase_de_estrelas(estrelas):
        partes.append(frase)
    if not partes:
        return "Fechamento do dia: nada pendente. Tudo em dia."
    return "Fechamento do dia. " + " ".join(partes)


class Estado:
    """O que precisa sobreviver a fechar o app: data do último resumo e avisos já dados."""

    def __init__(self, arquivo: Path) -> None:
        self.arquivo = arquivo
        try:
            self.dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except Exception:
            self.dados = {}
        self.dados.setdefault("avisados", {})

    @property
    def ultimo_resumo(self) -> str | None:
        return self.dados.get("ultimo_resumo")

    @ultimo_resumo.setter
    def ultimo_resumo(self, dia: str) -> None:
        self.dados["ultimo_resumo"] = dia
        self.salvar()

    @property
    def ultima_revisao(self) -> str | None:
        return self.dados.get("ultima_revisao")

    @property
    def metas_da_revisao(self) -> dict | None:
        """Metas concluídas por tópico na última revisão (para dizer quanto avançou desde então)."""
        return self.dados.get("metas_da_revisao")

    def registrar_revisao(self, dia: str, metas: dict) -> None:
        self.dados["ultima_revisao"] = dia
        self.dados["metas_da_revisao"] = metas
        self.salvar()

    @property
    def foco_manual(self) -> bool:
        """O dono ligou o modo foco por voz (fica ligado, mesmo se a Eco reiniciar, até ele desligar)."""
        return bool(self.dados.get("foco_manual"))

    @foco_manual.setter
    def foco_manual(self, ligado: bool) -> None:
        self.dados["foco_manual"] = ligado
        self.salvar()

    @property
    def ultima_noite(self) -> str | None:
        """Dia (AAAA-MM-DD) em que as pendências da noite já foram faladas."""
        return self.dados.get("ultima_noite")

    @ultima_noite.setter
    def ultima_noite(self, dia: str) -> None:
        self.dados["ultima_noite"] = dia
        self.salvar()

    def ja_avisado(self, chave: str) -> bool:
        return chave in self.dados["avisados"]

    def marcar_avisado(self, chave: str, quando: datetime) -> None:
        self.dados["avisados"][chave] = quando.isoformat()
        corte = quando - timedelta(days=2)  # poda o que já passou há muito tempo
        self.dados["avisados"] = {
            k: v for k, v in self.dados["avisados"].items() if datetime.fromisoformat(v) >= corte
        }
        self.salvar()

    def salvar(self) -> None:
        try:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            self.arquivo.write_text(json.dumps(self.dados, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


class Lembretes:
    def __init__(
        self,
        fonte: Fonte,
        arquivo: Path,
        notificar: Callable[[str, str], None] | None = None,
        antecedencia_min: int = 10,
        tolerancia: timedelta = timedelta(minutes=2),
        relogio: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        intervalo: float = 30.0,
    ) -> None:
        self.fonte = fonte
        self.estado = Estado(arquivo)
        self._notificar = notificar or (lambda titulo, texto: None)
        self.antecedencia = timedelta(minutes=antecedencia_min)
        self.tolerancia = tolerancia
        self._relogio = relogio
        self._intervalo = intervalo
        self._falas: queue.Queue[str] = queue.Queue()
        self._erros_vistos: set[str] = set()
        self._trava = threading.RLock()  # a thread de verificação e a de voz (ligar/desligar foco) mexem no mesmo estado

    # --- fila de falas (a thread de voz consome) -----------------------------------------------
    def tem_fala(self) -> bool:
        return not self._falas.empty()

    def proxima_fala(self) -> str | None:
        try:
            return self._falas.get_nowait()
        except queue.Empty:
            return None

    def quantas_falas(self) -> int:
        """Avisos esperando para serem falados (os que chegaram durante o foco, por exemplo)."""
        return self._falas.qsize()

    def descartar_falas(self) -> int:
        """Esvazia a fila (o dono não quis ouvir o que chegou durante o foco). Devolve quantas eram."""
        n = 0
        while self.proxima_fala() is not None:
            n += 1
        return n

    # --- verificação ---------------------------------------------------------------------------
    def ciclo(self, agora: datetime | None = None) -> None:
        agora = em_sp(agora or self._relogio())
        with self._trava:
            for passo in (self._resumo_do_dia, self._revisao_semanal, self._avisos):
                try:
                    passo(agora)
                except Exception as erro:  # sem rede ou Google fora: tenta de novo no próximo ciclo
                    msg = f"{type(erro).__name__}: {erro}"
                    if msg not in self._erros_vistos:
                        self._erros_vistos.add(msg)
                        print(f"  [lembretes] {passo.__name__} falhou: {msg}")

    # --- modo foco -----------------------------------------------------------------------------
    def foco_ativo(self) -> bool:
        return self.estado.foco_manual

    def _resumo_do_foco(self) -> str:
        """O que ficou guardado durante o foco: hoje, só os e-mails não lidos, numa frase (vazio se não há ou se falhou)."""
        frase = frase_de_emails(self._emails(), "Você está com")
        return f" {frase}" if frase else ""

    def ligar_foco(self) -> str:
        """Comando de voz "entrar em modo foco": devolve o que a Eco fala (a última coisa até sair do foco)."""
        with self._trava:
            if self.foco_ativo():
                return "O modo foco já está ligado."
            self.estado.foco_manual = True
            return FALA_FOCO_LIGADO

    def desligar_foco(self) -> str:
        """Comando de voz "sair do modo foco": desliga e dá o resumo dos e-mails (os avisos guardados o laço de voz conta)."""
        with self._trava:
            if not self.foco_ativo():
                return "O modo foco não estava ligado."
            self.estado.foco_manual = False
            return "Modo foco desligado." + self._resumo_do_foco()

    def _resumo_do_dia(self, agora: datetime) -> None:
        hoje = em_sp(agora).date()
        if self.estado.ultimo_resumo == hoje.isoformat() or em_sp(agora).hour < HORA_AMANHECER:
            return
        comeco = datetime(hoje.year, hoje.month, hoje.day, tzinfo=FUSO)
        eventos = self.fonte.eventos(comeco, comeco + timedelta(days=1))
        pendencias = [p for p in self.fonte.pendencias() if p.prazo <= hoje]
        foco = self.foco_ativo()  # no foco, sem e-mails
        texto = montar_resumo(eventos, pendencias, agora, None if foco else self._emails(), None if foco else self._estrelas())
        self._falas.put(texto)
        self._notificar("Resumo do dia", texto[:250])
        self.estado.ultimo_resumo = hoje.isoformat()  # só marca depois de ter conseguido montar

    def _revisao_semanal(self, agora: datetime) -> None:
        """Toda segunda-feira, logo depois do resumo do dia: metas por tópico e tarefas da semana."""
        hoje = em_sp(agora).date()
        if hoje.weekday() != 0 or self.estado.ultima_revisao == hoje.isoformat():
            return
        carregar = getattr(self.fonte, "revisao", None)
        if carregar is None:
            return
        from eco import revisao

        rev = carregar(hoje)
        texto = revisao.texto_falado(rev, self.estado.metas_da_revisao)
        self._falas.put(texto)
        self._notificar("Revisão da semana", texto[:250])
        self.estado.registrar_revisao(hoje.isoformat(), revisao.snapshot(rev))  # só marca depois de ter conseguido montar

    def no_contato(self, agora: datetime | None = None) -> bool:
        """Chamado quando o dono fala com a Eco. No primeiro contato do dia a partir das 20h30, enfileira o fechamento
        do dia (o que ficou pendente). Devolve True se enfileirou. Lento (lê o Google): chame fora da thread de voz."""
        agora = em_sp(agora or self._relogio())
        hoje = agora.date()
        if (agora.hour, agora.minute) < HORA_NOITE or self.estado.ultima_noite == hoje.isoformat():
            return False
        with self._trava:
            if self.estado.ultima_noite == hoje.isoformat():
                return False
            try:
                pendencias = [p for p in self.fonte.pendencias() if p.prazo <= hoje]
            except Exception as erro:
                print(f"  [lembretes] fechamento do dia falhou: {type(erro).__name__}: {erro}")
                return False  # tenta de novo no próximo contato
            texto = montar_noite(pendencias, agora, None if self.foco_ativo() else self._estrelas())
            self.estado.ultima_noite = hoje.isoformat()
            self._falas.put(texto)
            self._notificar("Fechamento do dia", texto[:250])
            return True

    def _estrelas(self) -> list[dict] | None:
        """E-mails com estrela (pendências de e-mail). Falhou? O texto sai sem eles."""
        buscar = getattr(self.fonte, "estrelas", None)
        if buscar is None:
            return None
        try:
            return buscar()
        except Exception as erro:
            msg = f"{type(erro).__name__}: {erro}"
            if msg not in self._erros_vistos:
                self._erros_vistos.add(msg)
                print(f"  [lembretes] e-mails com estrela falharam: {msg}")
            return None

    def _emails(self) -> dict | None:
        """E-mails não lidos para o resumo. Falhou (sem rede, sem autorização)? O resumo sai igual, sem a frase."""
        buscar = getattr(self.fonte, "emails", None)
        if buscar is None:
            return None
        try:
            return buscar()
        except Exception as erro:
            msg = f"{type(erro).__name__}: {erro}"
            if msg not in self._erros_vistos:
                self._erros_vistos.add(msg)
                print(f"  [lembretes] e-mails do resumo falharam: {msg}")
            return None

    def _avisos(self, agora: datetime) -> None:
        for e in self.fonte.eventos(agora, agora + self.antecedencia + timedelta(minutes=1)):
            if e.dia_inteiro:
                continue
            momento_do_aviso = e.inicio - self.antecedencia
            if not (momento_do_aviso <= agora < min(momento_do_aviso + self.tolerancia, e.inicio)):
                continue  # cedo demais, ou o momento certo já passou (aviso perdido: ignorado)
            chave = f"{e.id}|{e.inicio.isoformat()}"  # mudou o horário? é outra ocorrência e avisa de novo
            if self.estado.ja_avisado(chave):
                continue
            self.estado.marcar_avisado(chave, agora)
            minutos = int(self.antecedencia.total_seconds() // 60)
            self._falas.put(f"Daqui a {minutos} minutos: {e.titulo}, às {hora_falada(e.inicio)}.")
            self._notificar(f"Em {minutos} minutos", f"{e.titulo}, às {em_sp(e.inicio):%H:%M}")

    def iniciar(self) -> threading.Thread:
        def laco() -> None:
            while True:
                self.ciclo()
                time.sleep(self._intervalo)

        t = threading.Thread(target=laco, daemon=True, name="eco-lembretes")
        t.start()
        return t


class FonteGoogle:
    """Agenda e Tasks do Google, mais as metas com prazo do metas.md."""

    def __init__(self, pasta_dados: Path) -> None:
        self.pasta_dados = pasta_dados

    def eventos(self, inicio: datetime, fim: datetime) -> list[Evento]:
        from eco.ferramentas import google

        return [Evento(e["id"], e["titulo"], e["inicio"], e.get("fim")) for e in google.eventos_entre(inicio, fim)]

    def pendencias(self) -> list[Pendencia]:
        from eco.ferramentas import google, notas
        from eco.ferramentas.tarefas import motivo_importante

        tarefas = [Pendencia(t["titulo"], t["prazo"], motivo_importante(t.get("notas"))) for t in google.tarefas_com_prazo()]
        metas = [Pendencia(t, d) for t, d in notas.metas_com_prazo(self.pasta_dados)]
        return tarefas + metas

    def revisao(self, hoje: date):
        from eco import revisao

        return revisao.carregar(self.pasta_dados, hoje)

    def emails(self) -> dict:
        from eco.ferramentas import emails

        return emails.nao_lidos_principal()

    def estrelas(self) -> list[dict]:
        from eco.ferramentas import emails

        return emails.com_estrela(10)
