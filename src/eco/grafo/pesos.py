"""Regras de peso das ligações do grafo (escopo do dono, 10/11/2026). Funções puras: nada aqui grava no banco.

Regras:
- Hierarquia direta (pai e filho): 0,9, fora do teto e sem decaimento (é a estrutura do grafo).
- Causal explícita: 0,8. Manual (ligação feita pelo dono): 0,7.
- Mesma sessão (as duas saíram da mesma conversa ou do mesmo dia do bloco): 0,5.
- Similaridade semântica: a força que a análise deu, até 0,4; abaixo de `LIMIAR_SEMANTICO` não vira ligação.
- Menção lateral: 0,2.
- Soma: as regras de um par se somam e a soma para em `TETO` (0,7). Leitura adotada: o teto limita a soma, mas não
  rebaixa uma regra sozinha (uma causal continua 0,8). Assim o peso é max(maior regra, min(0,7, soma)).
- Decaimento temporal: multiplica o peso pela distância no tempo entre as duas ideias (última atividade de cada uma),
  com meia-vida de `MEIA_VIDA_DIAS` e piso `PISO_DECAIMENTO`: a ligação enfraquece, mas não some.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

HIERARQUIA = 0.9
PESOS = {"causal": 0.8, "manual": 0.7, "sessao": 0.5, "semantica": 0.4, "lateral": 0.2}
TETO = 0.7
LIMIAR_SEMANTICO = 0.2
MEIA_VIDA_DIAS = 120.0
PISO_DECAIMENTO = 0.35

NOMES = {
    "hierarquia": "hierarquia",
    "causal": "causal",
    "manual": "feita por você",
    "sessao": "mesma sessão",
    "semantica": "assunto parecido",
    "lateral": "menção",
}


def valor_do_tipo(tipo: str, forca: float = 0.4) -> float:
    if tipo == "semantica":
        return max(0.0, min(PESOS["semantica"], float(forca)))
    return PESOS.get(tipo, 0.0)


def fator_tempo(dias: float) -> float:
    """1,0 para ideias da mesma época; cai pela metade a cada `MEIA_VIDA_DIAS`, sem passar do piso."""
    return max(PISO_DECAIMENTO, 0.5 ** (abs(dias) / MEIA_VIDA_DIAS))


def combinar(regras: dict[str, float], dias: float = 0.0) -> float:
    """Peso final de um par a partir das regras que valem para ele ({tipo: valor})."""
    if "hierarquia" in regras:
        return HIERARQUIA
    valores = [v for v in regras.values() if v > 0]
    if not valores:
        return 0.0
    peso = max(max(valores), min(TETO, sum(valores)))
    return round(peso * fator_tempo(dias), 4)


def _dias_entre(a: str, b: str) -> float:
    try:
        return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds()) / 86400
    except (TypeError, ValueError):
        return 0.0


@dataclass
class Aresta:
    a: str
    b: str
    regras: dict[str, float] = field(default_factory=dict)
    peso: float = 0.0
    motivo: str = ""
    ligacao_id: str | None = None  # a linha de `ligacoes` (desligável), se houver
    relacao: str = ""  # E6/E8: código da relação (relacionado_a, depende_de...) e o resto da classificação
    status: str = ""
    confianca: float = 1.0
    de: str = ""
    tipo_gravado: str = ""

    @property
    def hierarquia(self) -> bool:
        return "hierarquia" in self.regras

    def descricao(self) -> str:
        nomes = " + ".join(NOMES[t] for t in sorted(self.regras, key=lambda t: -self.regras[t]))
        return f"peso {self.peso:.2f}".replace(".", ",") + f" · {nomes}"

    def relacao_legivel(self) -> str:
        """O tipo da relação para o dono ("depende de", "contradiz"...); vazio na hierarquia e na mesma sessão."""
        if self.ligacao_id is None:
            return ""
        from eco.grafo.ligacao import RELACAO_DO_TIPO, RELACOES

        codigo = self.relacao or RELACAO_DO_TIPO.get(self.tipo_gravado, "relacionado_a")
        return RELACOES.get(codigo, ("relacionado a",))[0]


def calcular(ideias: list, ligacoes: list, sessao: list[tuple[str, str]]) -> list[Aresta]:
    """Junta hierarquia (campo pai), ligações gravadas e pares da mesma sessão em arestas com peso final."""
    por_id = {i.id: i for i in ideias}
    arestas: dict[tuple[str, str], Aresta] = {}

    def aresta(a: str, b: str) -> Aresta | None:
        if a == b or a not in por_id or b not in por_id:
            return None
        k = (a, b) if a < b else (b, a)
        if k not in arestas:
            arestas[k] = Aresta(*k)
        return arestas[k]

    for i in ideias:
        if i.pai and (x := aresta(i.id, i.pai)):
            x.regras["hierarquia"] = HIERARQUIA
    for l in ligacoes:
        if l.ativa and (x := aresta(l.a, l.b)):
            v = valor_do_tipo(l.tipo, l.forca)
            if v > 0:
                x.regras[l.tipo] = max(v, x.regras.get(l.tipo, 0.0))
            x.motivo, x.ligacao_id = l.motivo, l.id
            x.relacao, x.status = getattr(l, "relacao", ""), getattr(l, "status", "ativa")
            x.confianca, x.de, x.tipo_gravado = getattr(l, "confianca", 1.0), getattr(l, "de", ""), l.tipo
    desligadas = {(l.a, l.b) for l in ligacoes if not l.ativa}
    for a, b in sessao:
        k = (a, b) if a < b else (b, a)
        if k not in desligadas and (x := aresta(*k)):  # o dono desligou: nem a sessão religa
            x.regras["sessao"] = PESOS["sessao"]
    saida = []
    for x in arestas.values():
        x.peso = combinar(x.regras, _dias_entre(por_id[x.a].atualizado_em, por_id[x.b].atualizado_em))
        if x.peso > 0:
            saida.append(x)
    return saida
