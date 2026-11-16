"""Ligação automática de ideias (etapa E6, requisitos 19.6.24, 19.6.26 e 19.6.27; decisão D6).

Para cada ideia nova ou cujo texto mudou:
1. Candidatas, local e sem custo (`Semelhanca.candidatas`): as até 6 ideias mais parecidas que o normal do acervo,
   fora as que já têm ligação, as do mesmo pai/filho e os pares que o dono rejeitou.
2. Classificação pelo Haiku (uma chamada por ideia, ~US$ 0,0002): para cada candidata, se há relação DE SENTIDO (não só
   palavras em comum), qual das 8 categorias, o sentido e a confiança (alta, média ou baixa), com o motivo.
3. Gravação (D6): alta -> ligação ativa; média -> ativa e marcada "revisar" (ou só sugestão, se o dono exigir aprovação:
   `ECO_LIGACAO_APROVAR=sim`); baixa -> sugestão, que não vira ligação. Par rejeitado pelo dono nunca volta, e só aquele
   par (a rejeição não se espalha para outros pares das mesmas ideias).
4. Marca da ideia (`analises`, fonte "ligacao:<id>" = hash do texto): não repete enquanto o texto não mudar.

O gasto entra no teto mensal do grafo e no uso.jsonl na categoria "grafo-ligacao" (19.6.20).
Relações indiretas não são gravadas: são calculadas na consulta (`grafo/consulta.relacoes`, até 2 saltos).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from eco.grafo.dados import Grafo, Ideia, Ligacao

# código: (rótulo para o dono, tem sentido?, tipo antigo para o peso do desenho)
RELACOES = {
    "relacionado_a": ("relacionado a", False, "semantica"),
    "depende_de": ("depende de", True, "causal"),
    "faz_parte_de": ("faz parte de", True, "causal"),
    "contradiz": ("contradiz", False, "lateral"),
    "complementa": ("complementa", False, "semantica"),
    "substitui": ("substitui", True, "lateral"),
    "consequencia_de": ("é consequência de", True, "causal"),
    "originado_por": ("foi originado por", True, "causal"),
}
# Ligações antigas (sem classificação) mostram um rótulo tirado do tipo (conversão do plano, sem mexer nos dados).
RELACAO_DO_TIPO = {"causal": "consequencia_de", "semantica": "relacionado_a", "lateral": "relacionado_a", "manual": "relacionado_a"}
CONFIANCA = {"alta": 0.9, "media": 0.65, "baixa": 0.35}
ALTA, MEDIA = 0.8, 0.5
MAX_CANDIDATAS = 6
MIN_CANDIDATA = 0.015  # semelhança acima do normal do acervo para valer a pena perguntar ao Haiku
MAX_POR_PALAVRA = 2  # mais estas, pela busca por palavra (termo raro em comum, como "Python"); o Haiku filtra
MAX_POR_RODADA = 25  # ideias processadas por rodada (o resto fica para a próxima)


def nivel(confianca: float) -> str:
    return "alta" if confianca >= ALTA else "media" if confianca >= MEDIA else "baixa"


def nivel_legivel(confianca: float) -> str:
    return {"alta": "alta", "media": "média", "baixa": "baixa"}[nivel(confianca)]


def rotulo(l: Ligacao, grafo: Grafo | None = None) -> str:
    """"A depende de B" quando a relação tem sentido e os nomes estão à mão; senão, só o rótulo."""
    codigo = l.relacao or RELACAO_DO_TIPO.get(l.tipo, "relacionado_a")
    texto, com_sentido, _ = RELACOES.get(codigo, ("relacionado a", False, ""))
    if grafo is not None and com_sentido and l.de in (l.a, l.b):
        try:
            outro = l.b if l.de == l.a else l.a
            return f"{grafo.ideia(l.de).nome} {texto} {grafo.ideia(outro).nome}"
        except KeyError:
            pass
    return texto


PROMPT = """Você liga ideias no grafo de memória pessoal do dono de uma assistente. Recebe uma ideia NOVA e algumas ideias EXISTENTES numeradas.
Para cada existente, diga se há uma relação real de SENTIDO entre ela e a nova. Palavras ou nomes em comum (inclusive o nome do dono, que aparece em quase tudo) NÃO são relação. Também NÃO é relação gostos ou hobbies diferentes que só têm em comum serem hobbies, interesses ou atividades ("gosta de xadrez" e "gosta de andar a cavalo" NÃO se ligam: são gostos independentes; só ligue se um depender do outro, por exemplo, um é parte do outro). Também NÃO é relação só pertencer à mesma categoria ampla: "as duas são metas", "as duas são da rotina", "as duas falam de estudos", "as duas são da família", "as duas são sobre a Eco". Tem que haver um vínculo concreto entre as duas coisas (uma usa, muda, explica, causa, depende, faz parte ou é outra versão da outra). Na dúvida, "nenhuma".

Relações possíveis:
- relacionado_a: mesmo assunto ou contexto, sem dependência
- depende_de: uma precisa da outra
- faz_parte_de: uma é parte da outra
- contradiz: dizem coisas incompatíveis sobre o mesmo fato
- complementa: uma acrescenta informação à outra
- substitui: uma é a versão nova da outra (a mais recente substitui a antiga)
- consequencia_de: uma acontece por causa da outra
- originado_por: uma surgiu a partir da outra
- nenhuma

"sentido": para depende_de, faz_parte_de, substitui, consequencia_de e originado_por, diga "nova" se a frase é "NOVA <relação> EXISTENTE", ou "existente" se é "EXISTENTE <relação> NOVA". Nas outras, "".
"confianca": "alta" quando o vínculo concreto está escrito nos textos; "media" quando o vínculo concreto é muito provável, mas falta um detalhe para confirmar; "baixa" quando é só uma possibilidade. Se o seu motivo precisaria dizer "mas não há dependência" ou "mas uma fala de X e a outra de Y", a resposta é "nenhuma".
"motivo": uma frase curta em português dizendo por que estão ligadas.

Responda só com JSON, sem texto em volta:
{"ligacoes": [{"n": 1, "relacao": "relacionado_a", "sentido": "", "confianca": "alta", "motivo": "..."}]}
Inclua só as existentes com relação diferente de "nenhuma"."""


def _descrever(i: Ideia, grafo: Grafo) -> str:
    from eco.grafo.recuperacao import origem_legivel

    texto = i.resumo if i.origem == "fato" else (f"{i.nome}: {i.resumo}" if i.resumo else i.nome)
    return f"{' '.join(texto.split())[:400]} [{origem_legivel(grafo, i)}]"


def interpretar(texto: str, n_candidatas: int) -> list[dict]:
    """Lê o JSON do Haiku; descarta o que não for válido (relação desconhecida, número fora da lista, repetido)."""
    m = re.search(r"\{.*\}", texto or "", re.S)
    if not m:
        return []
    try:
        dados = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    saida, vistos = [], set()
    for l in dados.get("ligacoes", []) if isinstance(dados, dict) else []:
        if not isinstance(l, dict):
            continue
        try:
            n = int(l.get("n"))
        except (TypeError, ValueError):
            continue
        relacao = str(l.get("relacao", "")).strip().lower()
        confianca = str(l.get("confianca", "")).strip().lower().replace("é", "e")
        if not 1 <= n <= n_candidatas or n in vistos or relacao not in RELACOES or confianca not in CONFIANCA:
            continue
        sentido = str(l.get("sentido", "")).strip().lower()
        vistos.add(n)
        saida.append({"n": n, "relacao": relacao, "confianca": confianca, "motivo": str(l.get("motivo", "")).strip()[:200],
                      "sentido": sentido if sentido in ("nova", "existente") and RELACOES[relacao][1] else ""})
    return saida


def _marca(i: Ideia) -> str:
    return hashlib.sha1(f"{i.nome}\n{i.resumo}".encode("utf-8")).hexdigest()


@dataclass
class ResumoLigacao:
    analisadas: int = 0
    ativas: int = 0
    revisar: int = 0
    sugestoes: int = 0
    custo: float = 0.0
    parou_por_teto: bool = False
    erros: list[str] = field(default_factory=list)

    def frase(self) -> str:
        if not self.analisadas:
            return "Nenhuma ideia nova para ligar."
        return (f"Analisei {self.analisadas} ideia(s): {self.ativas} ligação(ões) criada(s), {self.revisar} para revisar e "
                f"{self.sugestoes} sugestão(ões).") + (" Parei no teto de gasto do grafo." if self.parou_por_teto else "")


class Ligador:
    def __init__(self, grafo: Grafo, completador: Any, semelhanca=None, exigir_aprovacao: bool = False,
                 teto_usd: float | None = None) -> None:
        from eco.grafo.semelhanca import Semelhanca

        self.grafo = grafo
        self.completador = completador  # `completar(sistema, usuario) -> Resposta(texto, custo)` (analise.Completador)
        self.sem = semelhanca if semelhanca is not None else Semelhanca(grafo)
        self.exigir_aprovacao = exigir_aprovacao
        self.teto_usd = teto_usd

    # --- o que falta analisar --------------------------------------------------------------------------------------
    def pendentes(self) -> list[Ideia]:
        return [i for i in self.grafo.listar_ideias() if self.grafo.marca(f"ligacao:{i.id}") != _marca(i)]

    def _excluir(self, i: Ideia) -> set[str]:
        """Quem não precisa ser perguntado: já ligado (ou rejeitado), pai e filhos."""
        fora = set()
        for l in self.grafo.listar_ligacoes(so_ativas=False):
            if i.id in (l.a, l.b) and (l.status in ("rejeitada", "ativa", "revisar") or l.origem == "manual"):
                fora.add(l.b if l.a == i.id else l.a)
        if i.pai:
            fora.add(i.pai)
        fora |= {f.id for f in self.grafo.filhos(i.id)}
        return fora

    def _por_palavra(self, i: Ideia, excluir: set[str]) -> list[tuple[Ideia, float]]:
        from eco.grafo.semelhanca import texto_da_ideia

        ativas = {x.id: x for x in self.grafo.listar_ideias()}
        achadas = [k for k in self.sem._por_palavra(texto_da_ideia(i), 10) if k != i.id and k not in excluir and k in ativas]
        return [(ativas[k], 0.0) for k in achadas[:MAX_POR_PALAVRA]]

    # --- uma ideia -------------------------------------------------------------------------------------------------
    def ligar_ideia(self, ideia_id: str, resumo: ResumoLigacao | None = None) -> ResumoLigacao:
        r = resumo or ResumoLigacao()
        i = self.grafo.ideia(ideia_id)
        excluir = self._excluir(i)
        candidatas = self.sem.candidatas(i.id, MAX_CANDIDATAS, MIN_CANDIDATA, excluir=excluir)
        candidatas += self._por_palavra(i, excluir | {c.id for c, _ in candidatas})
        if candidatas:
            usuario = f"NOVA: {_descrever(i, self.grafo)}\n\nEXISTENTES:\n" + "\n".join(
                f"{n}. {_descrever(c, self.grafo)}" for n, (c, _s) in enumerate(candidatas, 1))
            resp = self.completador.completar(PROMPT, usuario)
            self.grafo.somar_gasto(resp.custo)
            r.custo += resp.custo
            with self.grafo.como("ligação automática"):
                for d in interpretar(resp.texto, len(candidatas)):
                    outra, _sim = candidatas[d["n"] - 1]
                    confianca = CONFIANCA[d["confianca"]]
                    status = "ativa" if confianca >= ALTA else "revisar" if confianca >= MEDIA else "sugestao"
                    if status == "revisar" and self.exigir_aprovacao:
                        status = "sugestao"
                    de = i.id if d["sentido"] == "nova" else outra.id if d["sentido"] == "existente" else ""
                    _rot, _sentido, tipo = RELACOES[d["relacao"]]
                    feita = self.grafo.ligar_classificada(i.id, outra.id, d["relacao"], confianca, d["motivo"], status, de,
                                                          tipo, 0.4 * confianca)
                    if feita is not None:
                        r.ativas += feita.status == "ativa"
                        r.revisar += feita.status == "revisar"
                        r.sugestoes += feita.status == "sugestao"
        self.grafo.definir_marca(f"ligacao:{i.id}", _marca(i))
        r.analisadas += 1
        return r

    # --- uma rodada ------------------------------------------------------------------------------------------------
    def executar(self, ids: list[str] | None = None, limite: int = MAX_POR_RODADA) -> ResumoLigacao:
        """Liga as ideias indicadas, ou as pendentes (novas ou com texto mudado), até `limite` por rodada e até o teto
        de gasto do grafo. Sem o modelo de semelhança não há candidatas: nada é gasto."""
        r = ResumoLigacao()
        if not self.sem.com_modelo:
            return r
        alvo = [self.grafo.ideia(x) for x in ids] if ids else self.pendentes()
        for i in alvo[:limite]:
            if self.teto_usd is not None and self.grafo.gasto_do_mes() >= self.teto_usd:
                r.parou_por_teto = True
                break
            try:
                self.ligar_ideia(i.id, r)
            except Exception as erro:  # uma resposta ruim não derruba as outras
                r.erros.append(f"{i.nome}: {type(erro).__name__}: {erro}")
        return r


def ligador_padrao(cfg, banco, registro=None) -> Ligador:
    """O ligador da Eco: Haiku do grafo, gasto na categoria grafo-ligacao, teto mensal do grafo e a opção D6."""
    from eco.grafo.analise import CompletadorClaude
    from eco.uso import GRAFO_LIGACAO

    class Completador(CompletadorClaude):
        def completar(self, sistema: str, usuario: str):
            r = self.cliente.messages.create(model=self.modelo, max_tokens=1200, system=sistema,
                                             messages=[{"role": "user", "content": usuario}], output_config={"effort": "low"})
            from eco.grafo.analise import Resposta
            from eco.uso import extrair

            uso = extrair(self.modelo, r)
            if self.registro is not None:
                self.registro.registrar(uso, categoria=GRAFO_LIGACAO)
            return Resposta("".join(getattr(b, "text", "") for b in r.content), uso.custo)

    return Ligador(Grafo(banco), Completador(cfg, registro=registro), exigir_aprovacao=cfg.ligacao_aprovar,
                   teto_usd=cfg.teto_grafo_usd)
