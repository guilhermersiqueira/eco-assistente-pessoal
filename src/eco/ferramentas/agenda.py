"""Agenda avançada: eventos que repetem, edição (inclusive de séries) e exclusão em massa.

Tudo aqui é regra pura (montar a regra de repetição, escolher o escopo, planejar o que apagar) ou chamada à
API do Google Agenda feita sempre por `google._servico`, para os testes trocarem o serviço por um falso.
Quem pede o "sim" do dono são as ferramentas em `ferramentas/__init__.py`: aqui as funções `pergunta_*`
só montam a frase lida de volta e as demais agem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from eco.ferramentas import google
from eco.ferramentas.google import FUSO, _sem_acento, descrever_evento

TZ = ZoneInfo(FUSO)
ESCOPOS = ("este", "proximos", "todos")
CAMPOS_EDICAO = ("novo_titulo", "novo_inicio", "nova_duracao_min", "local", "descricao", "adicionar_convidados", "remover_convidados")
JANELA_DIAS = 730  # até onde olhar à frente ao apagar "tudo" de um título

_FREQUENCIAS = {"diaria": "DAILY", "diario": "DAILY", "semanal": "WEEKLY", "mensal": "MONTHLY"}
_DIAS = {"seg": "MO", "ter": "TU", "qua": "WE", "qui": "TH", "sex": "FR", "sab": "SA", "dom": "SU"}
_NOMES = {"MO": "segunda", "TU": "terça", "WE": "quarta", "TH": "quinta", "FR": "sexta", "SA": "sábado", "SU": "domingo"}
_UNIDADE = {"DAILY": ("dia", "dias"), "WEEKLY": ("semana", "semanas"), "MONTHLY": ("mês", "meses")}
PERGUNTA_ESCOPO = (
    "Esse evento faz parte de uma série que repete. Pergunte ao dono se é só este, este e os próximos, ou todos "
    "(escopo 'este', 'proximos' ou 'todos'). Não faça nada ainda."
)


def _calendario():
    return google._servico("calendar", "v3").events()


def _dt(texto: str) -> datetime:
    d = datetime.fromisoformat(texto)
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def _data(texto: str) -> date:
    return date.fromisoformat(str(texto)[:10])


def _inicio_do_recurso(recurso: dict):
    marca = recurso["start"]
    return datetime.fromisoformat(marca["dateTime"]) if "dateTime" in marca else date.fromisoformat(marca["date"])


def _hora(quando: datetime) -> str:
    from eco.lembretes import hora_falada

    return hora_falada(quando)


# --- repetição -----------------------------------------------------------------------------------------------
def _normalizar_recorrencia(rec: dict) -> dict:
    """Valida o pedido de repetição e devolve {freq, intervalo, dias (códigos), ate (date|None), vezes}."""
    freq = _FREQUENCIAS.get(_sem_acento(str(rec.get("frequencia", ""))))
    if not freq:
        raise ValueError("Frequência inválida: use diaria, semanal ou mensal.")
    intervalo = int(rec.get("intervalo") or 1)
    if intervalo < 1:
        raise ValueError("O intervalo da repetição precisa ser 1 ou mais.")
    dias = []
    for d in rec.get("dias_semana") or []:
        codigo = _DIAS.get(_sem_acento(str(d))[:3])
        if codigo is None:
            raise ValueError(f"Dia da semana desconhecido: {d}.")
        if codigo not in dias:
            dias.append(codigo)
    if dias and freq == "DAILY":
        freq = "WEEKLY"  # "de segunda a sexta" é semanal com dias
    if dias and freq == "MONTHLY":
        raise ValueError("Repetição mensal com dias da semana não é suportada; use o dia do mês do início.")
    ate = _data(rec["ate"]) if rec.get("ate") else None
    vezes = int(rec["vezes"]) if rec.get("vezes") else None
    if ate is None and vezes is None:
        raise ValueError("Pergunte ao dono até quando a série repete (data final ou número de vezes) antes de criar.")
    if ate is not None and vezes is not None:
        raise ValueError("Informe só a data final ou só o número de vezes, não os dois.")
    if vezes is not None and vezes < 1:
        raise ValueError("O número de vezes precisa ser 1 ou mais.")
    return {"freq": freq, "intervalo": intervalo, "dias": dias, "ate": ate, "vezes": vezes}


def _fim_do_dia_utc(dia: date) -> str:
    return datetime.combine(dia, time(23, 59, 59), TZ).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def montar_regra(rec: dict) -> str:
    """Linha RRULE do Google Agenda para o pedido de repetição."""
    n = _normalizar_recorrencia(rec)
    partes = {"FREQ": n["freq"]}
    if n["intervalo"] > 1:
        partes["INTERVAL"] = str(n["intervalo"])
    if n["dias"]:
        partes["BYDAY"] = ",".join(n["dias"])
    if n["vezes"]:
        partes["COUNT"] = str(n["vezes"])
    else:
        partes["UNTIL"] = _fim_do_dia_utc(n["ate"])
    return _regra(partes)


def descrever_recorrencia(rec: dict) -> str:
    n = _normalizar_recorrencia(rec)
    um, varios = _UNIDADE[n["freq"]]
    base = f"todo {um}" if n["intervalo"] == 1 and n["freq"] != "WEEKLY" else (
        "toda semana" if n["intervalo"] == 1 else f"a cada {n['intervalo']} {varios}"
    )
    if n["dias"]:
        base += " (" + " e ".join(_NOMES[d] for d in n["dias"]) + ")"
    fim = f"até {n['ate']:%d/%m/%Y}" if n["ate"] else f"{n['vezes']} vezes"
    return f"repetindo {base}, {fim}"


def _partes(regra: str) -> dict[str, str]:
    return dict(p.split("=", 1) for p in regra.removeprefix("RRULE:").split(";"))


def _regra(partes: dict[str, str]) -> str:
    return "RRULE:" + ";".join(f"{k}={v}" for k, v in partes.items())


def _regra_do(mestre: dict) -> str | None:
    return next((r for r in mestre.get("recurrence", []) if r.startswith("RRULE:")), None)


# --- criar ---------------------------------------------------------------------------------------------------
def pergunta_criar(titulo: str, inicio: str, recorrencia: dict | None, convidados: list[str] | None) -> str:
    quando = _dt(inicio)
    texto = f"Criar o evento {titulo}, dia {quando:%d/%m} às {_hora(quando)}"
    if recorrencia:
        texto += ", " + descrever_recorrencia(recorrencia)
    texto += "."
    if convidados:
        texto += f" Isso envia convite por e-mail para {', '.join(convidados)}."
    return texto


def criar_evento(
    titulo: str,
    inicio: str,
    duracao_min: int = 60,
    local: str | None = None,
    descricao: str | None = None,
    recorrencia: dict | None = None,
    convidados: list[str] | None = None,
) -> str:
    comeco = _dt(inicio)
    fim = comeco + timedelta(minutes=duracao_min)
    corpo: dict = {
        "summary": titulo,
        "start": {"dateTime": comeco.isoformat(), "timeZone": FUSO},
        "end": {"dateTime": fim.isoformat(), "timeZone": FUSO},
    }
    if local:
        corpo["location"] = local
    if descricao:
        corpo["description"] = descricao
    if recorrencia:
        corpo["recurrence"] = [montar_regra(recorrencia)]
    if convidados:
        corpo["attendees"] = [{"email": e} for e in convidados]
    _calendario().insert(calendarId="primary", body=corpo, sendUpdates="all" if convidados else "none").execute()
    texto = f"Evento criado: {titulo}, em {comeco:%d/%m/%Y às %H:%M}"
    return texto + (f", {descrever_recorrencia(recorrencia)}." if recorrencia else ".")


# --- escopo (este / próximos / todos) -------------------------------------------------------------------------
def escopo_de(ev: dict, escopo: str | None) -> str | None:
    """Escopo válido para o evento: None se ele não repete; erro para o Claude perguntar se repete e faltou."""
    if not ev.get("serie"):
        return None
    esc = _sem_acento(str(escopo or ""))
    if esc not in ESCOPOS:
        raise ValueError(PERGUNTA_ESCOPO)
    return esc


def _sufixo_escopo(esc: str | None) -> str:
    return {"este": " (só esta ocorrência)", "proximos": " (esta e as próximas)", "todos": " (a série inteira)"}.get(esc or "", "")


def _mestre(serie_id: str) -> dict:
    return _calendario().get(calendarId="primary", eventId=serie_id).execute()


def _eh_primeira(ev: dict, mestre: dict) -> bool:
    return ev["inicio"] == _inicio_do_recurso(mestre)


def _ate_antes(ocorrencia) -> str:
    """UNTIL que termina a série logo antes da ocorrência (UTC para evento com hora, data para dia inteiro)."""
    if isinstance(ocorrencia, datetime):
        return (ocorrencia.astimezone(timezone.utc) - timedelta(seconds=1)).strftime("%Y%m%dT%H%M%SZ")
    return (ocorrencia - timedelta(days=1)).strftime("%Y%m%d")


def _truncar_serie(eventos, mestre: dict, ocorrencia) -> dict[str, str]:
    """Faz a série acabar antes de `ocorrencia`. Devolve as partes da regra ORIGINAL (para recriar o resto)."""
    regra = _regra_do(mestre)
    if regra is None:
        raise ValueError("Não achei a regra de repetição desse evento.")
    original = _partes(regra)
    curta = {k: v for k, v in original.items() if k not in ("COUNT", "UNTIL")}
    curta["UNTIL"] = _ate_antes(ocorrencia)
    nova = [_regra(curta) if r == regra else r for r in mestre["recurrence"]]
    eventos.patch(calendarId="primary", eventId=mestre["id"], body={"recurrence": nova}).execute()
    return original


def _contar_a_partir(eventos, mestre_id: str, ocorrencia) -> int:
    """Quantas ocorrências da série (contando esta) restam; serve para recriar a regra com COUNT."""
    marca = ocorrencia.isoformat() if isinstance(ocorrencia, datetime) else f"{ocorrencia:%Y-%m-%d}T00:00:00Z"
    total, pagina = 0, None
    while True:
        r = eventos.instances(calendarId="primary", eventId=mestre_id, timeMin=marca, maxResults=250, pageToken=pagina).execute()
        total += sum(1 for e in r.get("items", []) if e.get("status") != "cancelled")
        pagina = r.get("nextPageToken")
        if not pagina:
            return total


# --- apagar um evento ----------------------------------------------------------------------------------------
def pergunta_apagar(ev: dict, escopo: str | None) -> str:
    return f"Apagar da agenda o evento {descrever_evento(ev)}{_sufixo_escopo(escopo)}."


def apagar_com_escopo(ev: dict, escopo: str | None) -> str:
    eventos = _calendario()
    esc = escopo_de(ev, escopo)
    if esc is None or esc == "este":
        eventos.delete(calendarId="primary", eventId=ev["id"]).execute()
    elif esc == "todos":
        eventos.delete(calendarId="primary", eventId=ev["serie"]).execute()
    else:  # próximos
        mestre = _mestre(ev["serie"])
        if _eh_primeira(ev, mestre):
            eventos.delete(calendarId="primary", eventId=ev["serie"]).execute()
        else:
            _truncar_serie(eventos, mestre, ev["inicio"])
    return f"Evento apagado: {descrever_evento(ev)}{_sufixo_escopo(esc)}."


# --- editar ---------------------------------------------------------------------------------------------------
def _tem(valor) -> bool:
    return valor not in (None, "", [])


def validar_edicao(ev: dict, escopo: str | None, m: dict) -> str | None:
    """Confere o pedido de edição ANTES de pedir o "sim". Devolve o escopo (None se o evento não repete)."""
    if not any(_tem(m.get(k)) for k in CAMPOS_EDICAO):
        raise ValueError("Nenhuma mudança informada: diga o que alterar (título, horário, duração, local, descrição ou convidados).")
    esc = escopo_de(ev, escopo)
    mexe_no_tempo = _tem(m.get("novo_inicio")) or _tem(m.get("nova_duracao_min"))
    if mexe_no_tempo and not isinstance(ev["inicio"], datetime):
        raise ValueError("Esse evento é de dia inteiro; só dá para mudar título, local, descrição ou convidados.")
    if _tem(m.get("novo_inicio")):
        novo = _dt(m["novo_inicio"])
        if esc in ("proximos", "todos") and novo.astimezone(TZ).date() != ev["inicio"].astimezone(TZ).date():
            raise ValueError("Numa série só dá para mudar o horário e a duração, não o dia. Para mudar o dia, é melhor apagar e criar de novo.")
    if _tem(m.get("nova_duracao_min")) and int(m["nova_duracao_min"]) < 1:
        raise ValueError("A duração precisa ser de 1 minuto ou mais.")
    return esc


def _descrever_mudancas(m: dict) -> str:
    itens = []
    if _tem(m.get("novo_titulo")):
        itens.append(f"novo título {m['novo_titulo']}")
    if _tem(m.get("novo_inicio")):
        d = _dt(m["novo_inicio"])
        itens.append(f"novo horário, dia {d:%d/%m} às {_hora(d)}")
    if _tem(m.get("nova_duracao_min")):
        itens.append(f"duração de {int(m['nova_duracao_min'])} minutos")
    if _tem(m.get("local")):
        itens.append(f"local {m['local']}")
    if _tem(m.get("descricao")):
        itens.append("nova descrição")
    if _tem(m.get("adicionar_convidados")):
        itens.append("convidar " + ", ".join(m["adicionar_convidados"]))
    if _tem(m.get("remover_convidados")):
        itens.append("tirar " + ", ".join(m["remover_convidados"]))
    return "; ".join(itens)


def pergunta_editar(ev: dict, escopo: str | None, m: dict) -> str:
    texto = f"Alterar o evento {descrever_evento(ev)}{_sufixo_escopo(escopo)}: {_descrever_mudancas(m)}."
    envolvidos = list(m.get("adicionar_convidados") or []) + list(m.get("remover_convidados") or [])
    if envolvidos:
        texto += f" Isso envia e-mail de aviso para {', '.join(envolvidos)}."
    return texto


def _convidados(atuais: list[dict], m: dict) -> list[dict]:
    tirar = {e.lower() for e in m.get("remover_convidados") or []}
    lista = [a for a in atuais if a.get("email", "").lower() not in tirar]
    ja = {a.get("email", "").lower() for a in lista}
    for e in m.get("adicionar_convidados") or []:
        if e.lower() not in ja:
            lista.append({"email": e})
            ja.add(e.lower())
    return lista


def aplicar_edicao(ev: dict, escopo: str | None, m: dict) -> str:
    esc = validar_edicao(ev, escopo, m)
    eventos = _calendario()
    campos: dict = {}
    if _tem(m.get("novo_titulo")):
        campos["summary"] = m["novo_titulo"]
    if _tem(m.get("local")):
        campos["location"] = m["local"]
    if _tem(m.get("descricao")):
        campos["description"] = m["descricao"]
    mexe_nos_convidados = _tem(m.get("adicionar_convidados")) or _tem(m.get("remover_convidados"))

    mexe_no_tempo = _tem(m.get("novo_inicio")) or _tem(m.get("nova_duracao_min"))
    atual = ev["inicio"]
    duracao = timedelta(minutes=int(m["nova_duracao_min"])) if _tem(m.get("nova_duracao_min")) else (
        (ev["fim"] - atual) if isinstance(atual, datetime) and ev.get("fim") else timedelta(hours=1)
    )
    novo_inicio = _dt(m["novo_inicio"]) if _tem(m.get("novo_inicio")) else atual
    deslocamento = (novo_inicio - atual) if isinstance(atual, datetime) else timedelta(0)

    def horario(inicio: datetime) -> dict:
        return {
            "start": {"dateTime": inicio.isoformat(), "timeZone": FUSO},
            "end": {"dateTime": (inicio + duracao).isoformat(), "timeZone": FUSO},
        }

    def enviar(alvo: str, extra: dict, base: dict) -> None:
        corpo = dict(extra)
        if mexe_nos_convidados:
            corpo["attendees"] = _convidados(base.get("attendees", []), m)
        eventos.patch(calendarId="primary", eventId=alvo, body=corpo, sendUpdates="all" if mexe_nos_convidados else "none").execute()

    if esc is None or esc == "este":
        extra = {**campos, **(horario(novo_inicio) if mexe_no_tempo else {})}
        base = eventos.get(calendarId="primary", eventId=ev["id"]).execute() if mexe_nos_convidados else {}
        enviar(ev["id"], extra, base)
    else:
        mestre = _mestre(ev["serie"])
        if esc == "todos" or _eh_primeira(ev, mestre):
            extra = dict(campos)
            if mexe_no_tempo:
                extra.update(horario(_inicio_do_recurso(mestre) + deslocamento))
            enviar(mestre["id"], extra, mestre)
        else:
            _dividir_serie(eventos, mestre, ev, campos, horario(novo_inicio) if mexe_no_tempo else None, m, mexe_nos_convidados)
    return f"Evento alterado: {descrever_evento(ev)}{_sufixo_escopo(esc)}: {_descrever_mudancas(m)}."


def _dividir_serie(eventos, mestre: dict, ev: dict, campos: dict, tempo: dict | None, m: dict, mexe_nos_convidados: bool) -> None:
    """Edita "esta e as próximas": a série antiga termina antes desta ocorrência e nasce outra com as mudanças."""
    restantes = _contar_a_partir(eventos, mestre["id"], ev["inicio"])
    original = _truncar_serie(eventos, mestre, ev["inicio"])
    novo = {k: mestre[k] for k in ("summary", "description", "location", "attendees", "reminders", "colorId") if k in mestre}
    novo.update(campos)
    if mexe_nos_convidados:
        novo["attendees"] = _convidados(mestre.get("attendees", []), m)
    duracao = (ev["fim"] - ev["inicio"]) if ev.get("fim") else timedelta(hours=1)
    novo.update(
        tempo
        or {
            "start": {"dateTime": ev["inicio"].isoformat(), "timeZone": FUSO},
            "end": {"dateTime": (ev["inicio"] + duracao).isoformat(), "timeZone": FUSO},
        }
    )
    partes = {k: v for k, v in original.items() if k not in ("COUNT", "UNTIL")}
    if "COUNT" in original:
        partes["COUNT"] = str(restantes)
    elif "UNTIL" in original:
        partes["UNTIL"] = original["UNTIL"]
    novo["recurrence"] = [_regra(partes)]
    eventos.insert(calendarId="primary", body=novo, sendUpdates="all" if mexe_nos_convidados else "none").execute()


# --- apagar em massa -------------------------------------------------------------------------------------------
@dataclass
class PlanoApagar:
    ocorrencias: list[dict]
    titulo: str | None
    de: date
    ate: date | None
    instancias: list[str] = field(default_factory=list)  # ids de ocorrências a apagar uma a uma
    series_inteiras: list[str] = field(default_factory=list)  # ids de mestres a apagar de uma vez
    series_cortadas: list[tuple[str, object]] = field(default_factory=list)  # (mestre, ocorrência onde a série termina)

    @property
    def total(self) -> int:
        return len(self.ocorrencias)


def _hoje() -> date:
    return datetime.now(TZ).date()


def planejar_apagar(titulo: str | None, de: str | None = None, ate: str | None = None) -> PlanoApagar:
    """Decide o que seria apagado, sem apagar nada.

    Com data final (`ate`): apaga só as ocorrências do intervalo, uma a uma. Sem data final: apaga dali em
    diante (de `de`, ou de hoje), e séries inteiras saem de uma vez ou são cortadas onde começa o intervalo."""
    alvo = _sem_acento(titulo or "")
    if not alvo and not de and not ate:
        raise ValueError("Comando amplo demais: pergunte ao dono o título dos eventos ou o período (de que data a que data). Não apague nada ainda.")
    inicio = _data(de) if de else _hoje()
    fim = _data(ate) if ate else None
    if fim is not None and fim < inicio:
        raise ValueError("A data final é anterior à inicial.")
    limite = (fim + timedelta(days=1)) if fim else (inicio + timedelta(days=JANELA_DIAS))
    achados = google.eventos_entre(datetime.combine(inicio, time(0), TZ), datetime.combine(limite, time(0), TZ), limite=3000)
    achados = [e for e in achados if not alvo or alvo in _sem_acento(e["titulo"])]
    if not achados:
        onde = f" de {inicio:%d/%m} a {fim:%d/%m}" if fim else f" a partir de {inicio:%d/%m}"
        raise ValueError(f"Não achei nenhum evento{' com ' + repr(titulo) if alvo else ''}{onde}.")
    plano = PlanoApagar(achados, titulo, inicio, fim)
    if fim is not None:
        plano.instancias = [e["id"] for e in achados]
        return plano
    vistas: dict[str, dict] = {}
    for e in achados:
        if e.get("serie") is None:
            plano.instancias.append(e["id"])
        elif e["serie"] not in vistas:
            vistas[e["serie"]] = e  # a primeira ocorrência achada de cada série (a lista vem em ordem de data)
    for serie, primeira in vistas.items():
        if _eh_primeira(primeira, _mestre(serie)):
            plano.series_inteiras.append(serie)
        else:
            plano.series_cortadas.append((serie, primeira["inicio"]))
    return plano


def pergunta_apagar_varios(plano: PlanoApagar) -> str:
    quem = f"eventos '{plano.titulo}'" if plano.titulo else "eventos"
    periodo = f"de {plano.de:%d/%m} a {plano.ate:%d/%m}" if plano.ate else f"a partir de {plano.de:%d/%m}"
    texto = f"Apagar {plano.total} {quem}, {periodo}"
    if plano.series_inteiras or plano.series_cortadas:
        texto += f" (inclui {len(plano.series_inteiras) + len(plano.series_cortadas)} série(s) que repetem, apagadas daqui para frente)"
    return texto + "."


def _apagar_ids(ids: list[str]) -> None:
    servico = google._servico("calendar", "v3")
    falhas: list[str] = []

    def retorno(id_pedido, _resposta, erro):
        status = getattr(getattr(erro, "resp", None), "status", None)
        if erro is not None and status not in (404, 410):  # já apagado conta como feito
            falhas.append(id_pedido)

    for i in range(0, len(ids), 50):
        lote = servico.new_batch_http_request()
        for id_evento in ids[i : i + 50]:
            lote.add(servico.events().delete(calendarId="primary", eventId=id_evento), callback=retorno, request_id=id_evento)
        lote.execute()
    if falhas:
        raise RuntimeError(f"Não consegui apagar {len(falhas)} de {len(ids)} eventos.")


def apagar_plano(plano: PlanoApagar) -> str:
    eventos = _calendario()
    for serie, ocorrencia in plano.series_cortadas:
        _truncar_serie(eventos, _mestre(serie), ocorrencia)
    _apagar_ids(plano.instancias + plano.series_inteiras)
    return f"Apagados {plano.total} eventos."
