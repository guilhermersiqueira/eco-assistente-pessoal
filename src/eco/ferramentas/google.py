"""Gmail, Google Agenda e Google Tasks.

Ainda NÃO testado contra a conta real: depende de o dono criar as credenciais OAuth no Google Cloud
(salvar como %USERPROFILE%\\.eco\\google_credentials.json) e rodar `eco autorizar-google` uma vez.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
from email.message import EmailMessage

from eco.config import PASTA_ECO

ESCOPOS = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",  # rascunhos (e enviar rascunho)
    "https://www.googleapis.com/auth/gmail.modify",  # marcar como lido
    "https://www.googleapis.com/auth/tasks",
]
CREDENCIAIS = PASTA_ECO / "google_credentials.json"
TOKEN = PASTA_ECO / "google_token.json"
FUSO = "America/Sao_Paulo"


class GoogleNaoConfigurado(RuntimeError):
    pass


def autorizar() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CREDENCIAIS.exists():
        raise GoogleNaoConfigurado(f"Falta o arquivo de credenciais do Google em {CREDENCIAIS}.")
    credenciais = InstalledAppFlow.from_client_secrets_file(str(CREDENCIAIS), ESCOPOS).run_local_server(port=0)
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(credenciais.to_json(), encoding="utf-8")


def _credenciais():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN.exists():
        raise GoogleNaoConfigurado("O Google ainda não foi autorizado. Rode 'eco autorizar-google' uma vez.")
    # sem passar ESCOPOS: usa os que o token já tem. Pedir um escopo que o dono ainda não autorizou faria o refresh
    # falhar (invalid_scope) e derrubaria Agenda e Tasks também; quem precisa de um escopo novo chama `exige_escopo`.
    cred = Credentials.from_authorized_user_file(str(TOKEN))
    if cred.expired and cred.refresh_token:
        cred.refresh(Request())
        TOKEN.write_text(cred.to_json(), encoding="utf-8")
    return cred


def escopos_concedidos() -> set[str]:
    import json

    try:
        return set(json.loads(TOKEN.read_text(encoding="utf-8")).get("scopes") or [])
    except (OSError, ValueError):
        return set()


def exige_escopo(escopo: str) -> None:
    """Erro claro (em vez de um 403 do Google) quando o token ainda não tem a permissão que a ferramenta precisa."""
    if escopo not in escopos_concedidos():
        raise GoogleNaoConfigurado(
            "Falta autorizar uma permissão nova do Gmail (rascunhos e marcar como lido). "
            "Rode 'py -m uv run eco autorizar-google' uma vez e entre com a conta de novo."
        )


def _servico(nome: str, versao: str):
    from googleapiclient.discovery import build

    return build(nome, versao, credentials=_credenciais(), cache_discovery=False)


def listar_eventos(dias: int = 1) -> str:
    agora = datetime.now().astimezone()
    resposta = (
        _servico("calendar", "v3")
        .events()
        .list(
            calendarId="primary",
            timeMin=agora.isoformat(),
            timeMax=(agora + timedelta(days=dias)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=15,
        )
        .execute()
    )
    itens = resposta.get("items", [])
    if not itens:
        return "Nenhum evento no período."
    return "\n".join(
        f"{e['start'].get('dateTime') or e['start'].get('date')}: {e.get('summary', '(sem título)')}" for e in itens
    )


def eventos_entre(inicio: datetime, fim: datetime, limite: int = 50) -> list[dict]:
    """Eventos da Agenda entre dois instantes (datetimes com fuso), com as ocorrências de séries já separadas.
    `inicio` vem como datetime com fuso nos eventos com horário e como `date` nos de dia inteiro; `fim` é
    igual (ou None); `serie` é o id do evento-mãe quando o evento repete."""
    from datetime import date

    def quando(marca: dict):
        return datetime.fromisoformat(marca["dateTime"]) if "dateTime" in marca else date.fromisoformat(marca["date"])

    eventos = _servico("calendar", "v3").events()
    saida: list[dict] = []
    pagina = None
    while len(saida) < limite:
        resposta = eventos.list(
            calendarId="primary",
            timeMin=inicio.isoformat(),
            timeMax=fim.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=min(250, limite - len(saida)),
            pageToken=pagina,
        ).execute()
        for e in resposta.get("items", []):
            if e.get("status") == "cancelled":
                continue
            saida.append(
                {
                    "id": e["id"],
                    "titulo": e.get("summary", "(sem título)"),
                    "inicio": quando(e["start"]),
                    "fim": quando(e["end"]) if e.get("end") else None,
                    "serie": e.get("recurringEventId"),
                }
            )
        pagina = resposta.get("nextPageToken")
        if not pagina:
            break
    return saida


def _sem_acento(texto: str) -> str:
    import unicodedata

    base = unicodedata.normalize("NFD", texto.lower())
    return " ".join("".join(c for c in base if unicodedata.category(c) != "Mn").split())


def descrever_evento(ev: dict) -> str:
    """Frase falada que identifica o evento: título, dia e hora."""
    from datetime import date

    from eco.lembretes import hora_falada

    quando = ev["inicio"]
    if isinstance(quando, datetime):
        return f"{ev['titulo']}, dia {quando:%d/%m} às {hora_falada(quando)}"
    assert isinstance(quando, date)
    return f"{ev['titulo']}, dia {quando:%d/%m}, o dia todo"


def escolher_evento(eventos: list[dict], trecho: str, data: str | None = None) -> dict:
    """Acha UM evento pelo trecho do título (sem diferenciar maiúsculas nem acentos) e, se vier, pela data.
    Se nenhum ou mais de um combinar, levanta ValueError com a explicação para o Claude repassar."""
    alvo = _sem_acento(trecho)
    if not alvo:
        raise ValueError("Preciso de um trecho do título do evento para achá-lo.")
    achados = [e for e in eventos if alvo in _sem_acento(e["titulo"])]
    if data:
        dia = datetime.fromisoformat(data).date()
        achados = [e for e in achados if (e["inicio"].date() if isinstance(e["inicio"], datetime) else e["inicio"]) == dia]
    if not achados:
        raise ValueError(f"Não achei nenhum evento com '{trecho}'" + (f" no dia {data[:10]}." if data else " nos próximos dias."))
    series = {e.get("serie") for e in achados}
    if len(achados) > 1 and len(series) == 1 and None not in series:
        return achados[0]  # todas são ocorrências da mesma série: vale a mais próxima (o escopo é perguntado depois)
    if len(achados) > 1:
        lista = "; ".join(descrever_evento(e) for e in achados[:5])
        raise ValueError(f"Mais de um evento combina com '{trecho}': {lista}. Pergunte ao dono qual, ou a data. Não apague nenhum ainda.")
    return achados[0]


def localizar_evento(trecho: str, data: str | None = None, dias: int = 60) -> dict:
    """Busca na Agenda (a partir de hoje, ou só no dia `data`) e devolve o único evento que combina."""
    from datetime import timedelta

    from eco.lembretes import FUSO, em_sp

    if data:
        d = datetime.fromisoformat(data).date()
        comeco = datetime(d.year, d.month, d.day, tzinfo=FUSO)
        fim = comeco + timedelta(days=1)
    else:
        h = em_sp(datetime.now().astimezone()).date()
        comeco = datetime(h.year, h.month, h.day, tzinfo=FUSO)
        fim = comeco + timedelta(days=dias)
    return escolher_evento(eventos_entre(comeco, fim), trecho, data)


def apagar_evento(evento_id: str) -> None:
    _servico("calendar", "v3").events().delete(calendarId="primary", eventId=evento_id).execute()


def listas_de_tarefas() -> list[dict]:
    """Listas do Google Tasks: {id, titulo}."""
    itens = _servico("tasks", "v1").tasklists().list(maxResults=100).execute().get("items", [])
    return [{"id": l["id"], "titulo": l.get("title", "(sem nome)")} for l in itens]


def _tarefas_das_listas(concluidas: bool = False, desde: str | None = None) -> list[dict]:
    """Tarefas de TODAS as listas (pendentes, ou concluídas desde `desde`, um instante ISO em UTC):
    {id, titulo, prazo (date ou None), notas, lista_id, lista, lista_visivel, concluida_em (date ou None)}."""
    from datetime import date

    servico = _servico("tasks", "v1")
    listas = listas_de_tarefas()
    saida: list[dict] = []
    for lista in listas:
        pagina = None
        while True:
            pedido = {"tasklist": lista["id"], "maxResults": 100, "pageToken": pagina}
            if concluidas:
                pedido.update(showCompleted=True, showHidden=True, **({"completedMin": desde} if desde else {}))
            else:
                pedido.update(showCompleted=False)
            r = servico.tasks().list(**pedido).execute()
            for t in r.get("items", []):
                if not t.get("title") or (t.get("status") == "completed") != concluidas:
                    continue
                saida.append(
                    {
                        "id": t["id"],
                        "titulo": t["title"],
                        "prazo": date.fromisoformat(t["due"][:10]) if t.get("due") else None,
                        "notas": t.get("notes", ""),
                        "lista_id": lista["id"],
                        "lista": lista["titulo"],
                        "lista_visivel": len(listas) > 1,
                        "concluida_em": date.fromisoformat(t["completed"][:10]) if t.get("completed") else None,
                    }
                )
            pagina = r.get("nextPageToken")
            if not pagina:
                break
    return saida


def tarefas_pendentes() -> list[dict]:
    """Todas as tarefas pendentes de todas as listas do Google Tasks."""
    return _tarefas_das_listas()


def tarefas_concluidas(desde: str) -> list[dict]:
    """Tarefas concluídas desde um instante ISO em UTC, de todas as listas."""
    return _tarefas_das_listas(concluidas=True, desde=desde)


def descrever_tarefa(t: dict) -> str:
    return t["titulo"] + (f" (para {t['prazo']:%d/%m})" if t.get("prazo") else "") + (f", na lista {t['lista']}" if t.get("lista_visivel") else "")


def _ordem_tarefa(t: dict) -> tuple:
    from datetime import date

    return (t.get("lista", ""), t["prazo"] or date.max, t["titulo"], t["id"])


def escolher_tarefa(tarefas: list[dict], trecho: str, lista: str | None = None, data: str | None = None, numero: int | None = None, situacao: str = "pendente") -> dict:
    """Acha UMA tarefa pelo trecho do título (e, se vierem, pela lista e pelo vencimento). Nenhuma ou mais de uma vira
    ValueError para o Claude repassar; com várias, as candidatas saem numeradas e `numero` (1, 2...) escolhe uma."""
    alvo = _sem_acento(trecho)
    if not alvo:
        raise ValueError("Preciso de um trecho do título da tarefa para achá-la.")
    achadas = sorted((t for t in tarefas if alvo in _sem_acento(t["titulo"])), key=_ordem_tarefa)
    if lista:
        achadas = [t for t in achadas if _sem_acento(lista) == _sem_acento(t.get("lista", ""))]
    if data:
        dia = datetime.fromisoformat(data).date()
        achadas = [t for t in achadas if t["prazo"] == dia]
    if not achadas:
        raise ValueError(f"Não achei nenhuma tarefa {situacao} com '{trecho}'" + (f" na lista {lista}" if lista else "") + (f" para {data[:10]}." if data else "."))
    if numero is not None:
        if not 1 <= int(numero) <= len(achadas):
            raise ValueError(f"Só há {len(achadas)} tarefa(s) com '{trecho}'; o número vai de 1 a {len(achadas)}.")
        return achadas[int(numero) - 1]
    if len(achadas) > 1:
        lista_texto = "; ".join(f"{i}) {descrever_tarefa(t)}" for i, t in enumerate(achadas[:8], 1))
        raise ValueError(
            f"Mais de uma tarefa combina com '{trecho}': {lista_texto}. Pergunte ao dono qual e chame de novo com `numero`. Não mexa em nenhuma ainda."
        )
    return achadas[0]


def localizar_tarefa(trecho: str, lista: str | None = None, data: str | None = None, numero: int | None = None) -> dict:
    return escolher_tarefa(tarefas_pendentes(), trecho, lista, data, numero)


def apagar_tarefa(tarefa_id: str, lista_id: str = "@default") -> None:
    _servico("tasks", "v1").tasks().delete(tasklist=lista_id, task=tarefa_id).execute()


def concluir_tarefa(tarefa_id: str, lista_id: str = "@default") -> None:
    _servico("tasks", "v1").tasks().patch(tasklist=lista_id, task=tarefa_id, body={"status": "completed"}).execute()


def tarefas_com_prazo() -> list[dict]:
    """Tarefas pendentes, de todas as listas, que têm data (o Google guarda só a data, sem hora)."""
    return [{"titulo": t["titulo"], "prazo": t["prazo"], "notas": t.get("notas", "")} for t in tarefas_pendentes() if t["prazo"]]


def criar_evento(titulo: str, inicio: str, duracao_min: int = 60) -> str:
    comeco = datetime.fromisoformat(inicio)
    fim = comeco + timedelta(minutes=duracao_min)
    corpo = {
        "summary": titulo,
        "start": {"dateTime": comeco.isoformat(), "timeZone": FUSO},
        "end": {"dateTime": fim.isoformat(), "timeZone": FUSO},
    }
    _servico("calendar", "v3").events().insert(calendarId="primary", body=corpo).execute()
    return f"Evento criado: {titulo}, em {comeco:%d/%m/%Y às %H:%M}."


def ler_emails(quantidade: int = 5, apenas_nao_lidos: bool = True) -> str:
    gmail = _servico("gmail", "v1")
    filtro = {"userId": "me", "maxResults": quantidade, "labelIds": ["INBOX"]}
    if apenas_nao_lidos:
        filtro["q"] = "is:unread"
    mensagens = gmail.users().messages().list(**filtro).execute().get("messages", [])
    if not mensagens:
        return "Nenhum e-mail encontrado."
    linhas = []
    for m in mensagens:
        d = (
            gmail.users()
            .messages()
            .get(userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject"])
            .execute()
        )
        cab = {h["name"]: h["value"] for h in d["payload"]["headers"]}
        linhas.append(f"[{m['id']}] De {cab.get('From', '?')} | Assunto: {cab.get('Subject', '(sem assunto)')} | {d.get('snippet', '')}")
    return "\n".join(linhas)


def enviar_email(para: str, assunto: str, corpo: str) -> str:
    msg = EmailMessage()  # recusa quebras de linha em cabeçalhos
    msg["To"] = para
    msg["Subject"] = assunto
    msg.set_content(corpo)
    bruto = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    _servico("gmail", "v1").users().messages().send(userId="me", body={"raw": bruto}).execute()
    return f"E-mail enviado para {para}."
