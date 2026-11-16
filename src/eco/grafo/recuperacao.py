"""Recuperação contextual (etapa E5, requisitos 19.6.2 e 19.6.7): a cada pedido, a Eco escolhe sozinha as lembranças e
ideias do grafo que têm a ver com o assunto e as recebe junto do pedido, sem o dono pedir.

Como funciona (tudo local, sem custo):
1. O núcleo fixo continua indo no prompt de sistema como antes (`Memorias.ler()`: até 4.000 caracteres, fixas sempre
   dentro). Nada do que ia antes deixou de ir; o que é recuperado aqui é o que NÃO está no núcleo: lembranças que
   passaram do limite e as ideias do grafo (conversas guardadas, notas, metas), que antes nunca chegavam ao agente.
2. Semelhança (vetor + palavra, `grafo/semelhanca.py`) entre o pedido (e, com peso menor, o turno anterior) e cada ideia.
3. Expansão pelo grafo: vizinhos, pai e filhos das melhores entram se também tiverem alguma relação com o pedido.
   Não há expansão por "lembrança parecida": entre lembranças do dono, o e5 acha tudo parecido por causa do nome
   (medido em 12/11/2026: "mora em Campinas" fica mais perto de "se chama Vitor" que de "se mudou para São
   Bernardo"). Duas versões do mesmo fato chegam juntas quando a E7 (contradições) as liga.
4. Nota final = semelhança (0,6) + peso do nó (0,2: duração, proteção, origem, uso, atualidade) + atualidade (0,1) +
   proximidade no grafo (0,1). Até 8 itens e ~2.400 caracteres (~600 tokens).
5. O bloco vai como mensagem de sistema depois do pedido (`Agente`), fora do ponto de cache; cada item diz de onde veio.
6. Os itens enviados ganham um uso (`usos`, `ultimo_uso`, `relevancia` gravados sem histórico, sem custo).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from eco.grafo.dados import Grafo, Ideia
from eco.grafo.semelhanca import MINIMO_RELEVANTE, Semelhanca

MAX_ITENS = 8
MAX_CARACTERES = 2400  # ~600 tokens
MAX_POR_ITEM = 400
PESOS = {"semelhanca": 0.6, "no": 0.2, "atualidade": 0.1, "proximidade": 0.1}
ESCALA_SEMELHANCA = 0.08  # relevância (semelhança) que já conta como nota cheia
FOLGA_VIZINHO = 0.015  # um vizinho entra com um pouco menos de semelhança que uma semente
MEIA_VIDA_DIAS = 90

_DURACAO = {"permanente": 1.0, "duradoura": 0.75, "temporaria": 0.3, "": 0.5}
_ORIGEM = {"fato": 0.8, "metas": 0.7, "manual": 0.65, "chat": 0.5, "notas": 0.45}


def _quando(texto: str) -> datetime | None:
    if not texto:
        return None
    try:
        d = datetime.fromisoformat(texto)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def atualidade(i: Ideia, agora: datetime | None = None) -> float:
    """1 para o que mudou ou foi usado agora, 0,5 aos 90 dias, e assim por diante."""
    agora = agora or datetime.now(timezone.utc)
    datas = [d for d in (_quando(i.atualizado_em), _quando(i.ultimo_uso)) if d]
    if not datas:
        return 0.5
    dias = max(0.0, (agora - max(datas)).total_seconds() / 86400)
    return 0.5 ** (dias / MEIA_VIDA_DIAS)


def peso_do_no(i: Ideia, agora: datetime | None = None) -> float:
    """Relevância própria do nó, de 0 a 1 (19.6.2): o permanente e protegido pesa mais que o pontual; o uso e a
    atualidade sobem um pouco. Não depende do pedido."""
    base = 0.45 * _DURACAO.get(i.durabilidade, 0.5) + 0.35 * _ORIGEM.get(i.origem, 0.5)
    if i.protegido:
        base += 0.15
    base += 0.1 * min(i.usos, 10) / 10 + 0.05 * atualidade(i, agora)
    return round(max(0.0, min(1.0, base)), 3)


def origem_legivel(grafo: Grafo, i: Ideia) -> str:
    """De onde a Eco sabe isso, numa frase curta (19.6.15), para ela poder dizer ao dono."""
    if i.origem == "fato":
        from eco.grafo.memorias import _data_de

        data = _data_de(i)
        if i.topico == "importada":
            return f"lembrança importada das conversas do claude.ai{' (conversa de ' + data + ')' if data else ''}"
        return f"lembrança{' de ' + data if data else ''}" + (", fixa" if i.protegido else "")
    if i.origem == "chat":
        fontes = grafo.fontes(i.id)
        if fontes:
            f = fontes[0]
            return f"conversa guardada \"{f.titulo}\"" + (f" (espaço {f.espaco_nome})" if f.espaco_nome else "")
        return "conversa guardada"
    if i.origem == "notas":
        dias = grafo.dias(i.id)
        return "bloco de notas" + (f" ({dias[-1]})" if dias else "")
    if i.origem == "metas":
        return "metas"
    return "criada à mão no grafo"


@dataclass(frozen=True)
class Recuperado:
    ideia: Ideia
    nota: float
    semelhanca: float | None
    motivo: str  # "assunto" ou "ligada a <nome>" / "dentro de <nome>" / "contém <nome>"
    origem: str

    def linha(self) -> str:
        i = self.ideia
        texto = i.resumo if i.origem == "fato" else (f"{i.nome}: {i.resumo}" if i.resumo else i.nome)
        texto = " ".join(texto.split())
        if len(texto) > MAX_POR_ITEM:
            texto = texto[: MAX_POR_ITEM - 1].rstrip() + "…"
        extra = "" if self.motivo == "assunto" else f"; {self.motivo}"
        return f"- {texto} [{self.origem}{extra}]"


class Recuperacao:
    def __init__(self, grafo: Grafo, memorias=None, semelhanca: Semelhanca | None = None) -> None:
        self.grafo = grafo
        self.memorias = memorias  # para saber o que já está no núcleo do prompt
        self.sem = semelhanca if semelhanca is not None else Semelhanca(grafo)

    def _no_nucleo(self) -> set[str]:
        if self.memorias is None:
            return set()
        fora = self.memorias.fora_do_prompt()
        return {n.id for n in self.memorias.nos() if n.resumo not in fora}

    def recuperar(self, pedido: str, anterior: str = "", limite: int = MAX_ITENS, max_caracteres: int = MAX_CARACTERES,
                  agora: datetime | None = None) -> list[Recuperado]:
        pedido = (pedido or "").strip()
        if not pedido:
            return []
        excluir = self._no_nucleo()
        # semelhança com o pedido; o turno anterior ajuda em pedidos curtos ("e ela?"), com peso menor
        sim: dict[str, float] = {}
        palavra: set[str] = set()
        ideias: dict[str, Ideia] = {}
        for texto, fator in ((pedido, 1.0), (f"{anterior[-300:]}\n{pedido}" if anterior.strip() else "", 0.8)):
            if not texto:
                continue
            for a in self.sem.buscar(texto, limite=10_000, minimo=None, excluir=excluir):
                ideias[a.ideia.id] = a.ideia
                if a.por_palavra:
                    palavra.add(a.ideia.id)
                valor = a.nota if a.relevancia is None else a.relevancia * fator + (a.nota - a.relevancia)
                sim[a.ideia.id] = max(sim.get(a.ideia.id, -1.0), valor)
        com_modelo = self.sem.com_modelo
        minimo = MINIMO_RELEVANTE if com_modelo else 0.0
        sementes = sorted((k for k, v in sim.items() if v >= minimo and (com_modelo or k in palavra)), key=lambda k: -sim[k])
        candidatos: dict[str, tuple[float, str]] = {k: (0.0, "assunto") for k in sementes}
        # expansão: vizinhos, pai e filhos das 5 melhores, se também tiverem alguma relação com o pedido
        if com_modelo:
            ativas = {l for l in self.grafo.listar_ligacoes() if l.status != "rejeitada"}
            for k in sementes[:5]:
                nome = ideias[k].nome
                vizinhos = [(l.b if l.a == k else l.a, f"ligada a {nome}") for l in ativas if k in (l.a, l.b)]
                if ideias[k].pai:
                    vizinhos.append((ideias[k].pai, f"contém {nome}"))
                vizinhos += [(f.id, f"dentro de {nome}") for f in self.grafo.filhos(k)]
                for v, motivo in vizinhos:
                    if v in candidatos or v in excluir or v not in ideias:
                        continue
                    if sim.get(v, -1.0) >= minimo - FOLGA_VIZINHO:
                        candidatos[v] = (1.0, motivo)
        notas = []
        for k, (proximidade, motivo) in candidatos.items():
            i = ideias[k]
            s = sim[k] / ESCALA_SEMELHANCA if com_modelo else sim[k]
            nota = (PESOS["semelhanca"] * max(0.0, min(1.0, s)) + PESOS["no"] * peso_do_no(i, agora)
                    + PESOS["atualidade"] * atualidade(i, agora) + PESOS["proximidade"] * proximidade)
            notas.append(Recuperado(i, round(nota, 4), sim[k] if com_modelo else None, motivo, origem_legivel(self.grafo, i)))
        notas.sort(key=lambda r: -r.nota)
        escolhidos, total = [], 0
        for r in notas:
            tamanho = len(r.linha()) + 1
            if len(escolhidos) >= limite or total + tamanho > max_caracteres:
                continue
            escolhidos.append(r)
            total += tamanho
        return escolhidos

    def bloco(self, recuperados: list[Recuperado]) -> str:
        """O texto da mensagem de sistema que acompanha o pedido ("" quando nada tem a ver)."""
        if not recuperados:
            return ""
        return (
            "Memória recuperada para este pedido (uma amostra escolhida pela Eco no grafo, por semelhança com o assunto; não "
            "serve para contar nem para dizer que algo não existe; use só o que ajudar, sem listar nem dizer que recebeu isto; se o dono perguntar de onde você sabe, a origem está entre "
            "colchetes; se dois itens se contradizem, vale o mais recente pela data, e você pode confirmar com o dono; se "
            "precisar de mais, use consultar_memoria):\n" + "\n".join(r.linha() for r in recuperados)
        )

    def registrar_uso(self, recuperados: list[Recuperado], agora: datetime | None = None) -> None:
        """Retorno (19.6.2): o que foi enviado ganha um uso. Não vai para o histórico (é contagem, não mudança)."""
        if not recuperados:
            return
        quando = (agora or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        banco = self.grafo.banco
        with banco._lock, banco._con:
            for r in recuperados:
                i = r.ideia
                usado = Ideia(**{**i.__dict__, "usos": i.usos + 1, "ultimo_uso": quando})
                banco._con.execute(
                    "UPDATE ideias SET usos = usos + 1, ultimo_uso = ?, relevancia = ? WHERE id = ?",
                    (quando, peso_do_no(usado, agora), i.id),
                )
