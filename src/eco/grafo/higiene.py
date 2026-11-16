"""Higiene da memória (etapa E7): requisitos 19.6.1, 19.6.3, 19.6.5, 19.6.8, 19.6.14, 19.6.21, 19.6.23 e 19.6.25.

Princípio: a Eco SUGERE, o dono decide. Nada é apagado; o que sai vai para o arquivo (com histórico) e só depois do
"sim". Ideias protegidas nunca entram em proposta automática.

1. Na escrita (`verificar_escrita`, chamado pela ferramenta `lembrar`): procura lembranças parecidas (local) e, se houver,
   o Haiku diz se a nova é IGUAL (só reforça a existente, sem duplicar), CONTRADIZ ou ATUALIZA uma existente (com
   certeza alta a Eco pergunta antes de guardar; com média guarda e deixa a contradição como pendência) ou é nova.
2. Na revisão (`revisar`, semanal por padrão e quando o dono pede), propostas guardadas na tabela `propostas`:
   - arquivar: validade vencida, temporária parada há 60 dias, ou versão antiga que outra "substitui" (local, sem custo);
   - contradicao: duas lembranças ligadas por "contradiz" (local);
   - fundir: duas ideias da mesma origem com a mesma informação (candidatas locais; o Haiku confirma e escreve o texto
     fundido);
   - agrupar: 3 ou mais ideias ligadas entre si, sem pai, que caberiam sob um mesmo assunto (o Haiku dá o nome).
   E marca "revisar" as ligações automáticas cujas ideias mudaram depois de ligadas (19.6.25; nunca remove).
   Proposta recusada não volta (a mesma, para as mesmas ideias).
3. Aplicar uma proposta preserva tudo: fundir copia origens, dias e ligações para a ideia que fica e arquiva a outra.
O gasto vai para o teto do grafo, nas categorias "grafo-escrita" e "grafo-higiene" (19.6.20).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from eco.grafo.dados import Grafo, Ideia, _par

ESQUEMA = """
CREATE TABLE IF NOT EXISTS propostas (
    id TEXT PRIMARY KEY, tipo TEXT NOT NULL, chave TEXT NOT NULL UNIQUE, ideias TEXT NOT NULL, motivo TEXT NOT NULL DEFAULT '',
    dados TEXT NOT NULL DEFAULT '{}', estado TEXT NOT NULL DEFAULT 'pendente', criado_em TEXT NOT NULL,
    decidido_em TEXT NOT NULL DEFAULT '', autor TEXT NOT NULL DEFAULT '');
"""
DIAS_TEMPORARIA = 60  # temporária sem uso nem mudança há tanto tempo vira proposta de arquivar
MIN_DUPLICATA = 0.05  # semelhança acima do normal do acervo para perguntar se é a mesma informação
MAX_PARES_DUPLICATA = 8
MIN_GRUPO, MAX_GRUPOS = 3, 3
MIN_ESCRITA = 0.015
NOMES_TIPO = {"arquivar": "arquivar", "fundir": "juntar", "agrupar": "agrupar", "contradicao": "contradição"}


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _data(texto: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(texto)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _json(texto: str) -> Any:
    m = re.search(r"\{.*\}", texto or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _texto(i: Ideia) -> str:
    return " ".join((i.resumo if i.origem == "fato" else (f"{i.nome}: {i.resumo}" if i.resumo else i.nome)).split())[:400]


# --- escrita -------------------------------------------------------------------------------------------------------
PROMPT_ESCRITA = """O dono de uma assistente quer guardar uma NOVA lembrança. Compare com as EXISTENTES numeradas e diga, para cada uma:
- "igual": diz a mesma coisa (mesmo que com outras palavras); guardar a nova seria duplicar;
- "contradiz": as duas não podem ser verdade ao mesmo tempo sobre o mesmo fato (ex.: trabalha às 8h x trabalha às 9h);
- "atualiza": a nova é a versão mais recente do mesmo fato (ex.: "mudou o horário para 9h" diante de "trabalha às 8h");
- "nenhuma": qualquer outro caso (assunto parecido, complemento, outra coisa).
Nomes ou palavras em comum não bastam. "confianca": "alta" só quando é evidente; senão "media" ou "baixa".
Responda só com JSON: {"itens": [{"n": 1, "relacao": "igual", "confianca": "alta", "motivo": "..."}]} com as que não são "nenhuma"."""


@dataclass
class Veredito:
    tipo: str  # nova | igual | conflito | pendencia
    existente: Ideia | None = None
    relacao: str = ""  # igual | contradiz | atualiza
    motivo: str = ""
    custo: float = 0.0

    def mensagem(self, novo: str) -> str:
        if self.tipo == "conflito" and self.existente is not None:
            verbo = "parece ser a versão nova de" if self.relacao == "atualiza" else "contradiz"
            return (f"NÃO GUARDEI AINDA. O que o dono disse ({novo}) {verbo} uma lembrança que já existe: "
                    f"\"{self.existente.resumo}\" (lembrança guardada em {self.existente.criado_em[:10]}). Motivo: {self.motivo}. "
                    "Pergunte ao dono, numa frase, se a nova substitui a antiga (então chame substituir_lembranca com um "
                    "trecho da antiga e o fato novo) ou se as duas valem (então chame lembrar de novo com mesmo_assim=true).")
        return ""


# --- propostas -----------------------------------------------------------------------------------------------------
@dataclass
class Proposta:
    id: str
    tipo: str
    ideias: list[str]
    motivo: str
    dados: dict
    estado: str
    criado_em: str


@dataclass
class ResumoRevisao:
    novas: dict = field(default_factory=dict)  # tipo -> quantas
    ligacoes_para_revisar: int = 0
    custo: float = 0.0
    automatica: bool = False
    parou_por_teto: bool = False
    erros: list[str] = field(default_factory=list)

    def frase(self, pendentes: int) -> str:
        partes = [f"{n} para {NOMES_TIPO.get(t, t)}" for t, n in self.novas.items() if n]
        texto = ("Revisei a memória: " + (", ".join(partes) + " (propostas novas)" if partes else "nenhuma proposta nova")
                 + (f"; {self.ligacoes_para_revisar} ligação(ões) marcada(s) para revisar porque as ideias mudaram"
                    if self.ligacoes_para_revisar else "") + f". Há {pendentes} proposta(s) esperando a sua decisão.")
        return texto + (" Parei no teto de gasto do grafo." if self.parou_por_teto else "")


PROMPT_DUPLICATAS = """Você revisa a memória pessoal do dono de uma assistente. Para cada PAR numerado, diga se as duas ideias guardam a MESMA informação (redundância: uma pode ser juntada à outra sem perder nada) ou não. Assunto parecido não basta: tem que ser a mesma informação.
Para os pares que são a mesma informação, escreva "texto": uma frase única que junte as duas sem perder nenhum detalhe, no mesmo estilo (terceira pessoa).
Responda só com JSON: {"pares": [{"n": 1, "mesma": true, "texto": "...", "motivo": "..."}]} (pode omitir os que não são)."""

PROMPT_GRUPOS = """Você organiza o grafo de ideias do dono de uma assistente. Cada GRUPO numerado tem ideias ligadas entre si e sem um assunto acima delas.
Para cada grupo, diga se elas cabem sob um mesmo assunto concreto (ex.: "Aprendizado de Python") e dê um nome curto a ele. Se o grupo for só uma mistura, "agrupar": false.
Responda só com JSON: {"grupos": [{"n": 1, "agrupar": true, "nome": "...", "motivo": "..."}]}"""


class Higiene:
    def __init__(self, grafo: Grafo, completador: Any = None, semelhanca=None, memorias=None,
                 teto_usd: float | None = None) -> None:
        self.grafo, self.completador, self.memorias, self.teto_usd = grafo, completador, memorias, teto_usd
        if semelhanca is None:
            from eco.grafo.semelhanca import Semelhanca

            semelhanca = Semelhanca(grafo)
        self.sem = semelhanca
        with grafo.banco._lock, grafo.banco._con:
            grafo.banco._con.executescript(ESQUEMA)

    def _pode_gastar(self) -> bool:
        return self.completador is not None and (self.teto_usd is None or self.grafo.gasto_do_mes() < self.teto_usd)

    def _perguntar(self, sistema: str, usuario: str) -> tuple[Any, float]:
        r = self.completador.completar(sistema, usuario)
        self.grafo.somar_gasto(r.custo)
        return _json(r.texto), r.custo

    # --- 1. escrita (19.6.3) ---------------------------------------------------------------------------------------
    def verificar_escrita(self, texto: str) -> Veredito:
        """Antes de guardar uma lembrança: igual (reforça), conflito (pergunta), pendência (guarda e anota) ou nova."""
        texto = " ".join((texto or "").split())
        candidatas = [(i, s) for i, s in self.sem.parecidas_com_texto(texto, origem="fato", limite=4, minimo=MIN_ESCRITA)
                      if not i.arquivada]
        for i, _s in candidatas:  # igual letra por letra: nem precisa perguntar
            if i.resumo.strip().lower() == texto.lower():
                return Veredito("igual", i, "igual", "o mesmo texto")
        if not candidatas or not self._pode_gastar():
            return Veredito("nova")
        usuario = f"NOVA: {texto}\n\nEXISTENTES:\n" + "\n".join(
            f"{n}. {_texto(i)} (guardada em {i.criado_em[:10]})" for n, (i, _s) in enumerate(candidatas, 1))
        dados, custo = self._perguntar(PROMPT_ESCRITA, usuario)
        itens = []
        for x in (dados or {}).get("itens", []) if isinstance(dados, dict) else []:
            try:
                n = int(x.get("n"))
            except (TypeError, ValueError, AttributeError):
                continue
            rel, conf = str(x.get("relacao", "")), str(x.get("confianca", "")).replace("é", "e")
            if 1 <= n <= len(candidatas) and rel in ("igual", "contradiz", "atualiza") and conf in ("alta", "media", "baixa"):
                itens.append((n, rel, conf, str(x.get("motivo", ""))[:200]))
        ordem = {"igual": 0, "atualiza": 1, "contradiz": 2}
        # certeza alta primeiro; depois igual, atualiza, contradiz; empate: a lembrança mais recente (é a que vale agora)
        recente = {n: candidatas[n - 1][0].criado_em for n, *_ in itens}
        itens.sort(key=lambda x: recente[x[0]], reverse=True)
        for n, rel, conf, motivo in sorted(itens, key=lambda x: (x[2] != "alta", ordem[x[1]])):
            existente = candidatas[n - 1][0]
            if rel == "igual" and conf in ("alta", "media"):
                return Veredito("igual", existente, rel, motivo, custo)
            if rel in ("contradiz", "atualiza") and conf == "alta" and not existente.protegido:
                return Veredito("conflito", existente, rel, motivo, custo)
            if rel in ("contradiz", "atualiza") and conf in ("alta", "media"):
                return Veredito("pendencia", existente, rel, motivo, custo)
        return Veredito("nova", custo=custo)

    def reforcar(self, i: Ideia) -> None:
        """A informação já existia: a lembrança ganha um uso e fica atual (sem criar outra)."""
        agora = self.grafo.banco._relogio()
        self.grafo.banco._executar("UPDATE ideias SET usos = usos + 1, ultimo_uso = ?, atualizado_em = ? WHERE id = ?",
                                   (agora, agora, i.id))
        self.grafo._registrar("reforcar", i.id, depois={"motivo": "o dono disse de novo"})

    def anotar_contradicao(self, nova: Ideia, existente: Ideia, motivo: str) -> None:
        self._propor("contradicao", [existente.id, nova.id], f"\"{nova.resumo}\" x \"{existente.resumo}\": {motivo}", {})

    # --- 2. propostas ----------------------------------------------------------------------------------------------
    def _propor(self, tipo: str, ideias: list[str], motivo: str, dados: dict) -> bool:
        chave = tipo + ":" + ",".join(sorted(ideias))
        if self.grafo.banco._consultar("SELECT 1 FROM propostas WHERE chave = ?", (chave,)):
            return False  # já proposta (pendente, aceita ou recusada): não repete
        self.grafo.banco._executar(
            "INSERT INTO propostas (id, tipo, chave, ideias, motivo, dados, criado_em, autor) VALUES (?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, tipo, chave, json.dumps(ideias), motivo, json.dumps(dados, ensure_ascii=False),
             self.grafo.banco._relogio(), self.grafo.autor),
        )
        return True

    def propostas(self, estado: str = "pendente") -> list[Proposta]:
        linhas = self.grafo.banco._consultar("SELECT * FROM propostas WHERE estado = ? ORDER BY criado_em, rowid", (estado,))
        ativas = {i.id for i in self.grafo.listar_ideias()}
        saida = []
        for l in linhas:
            p = Proposta(l["id"], l["tipo"], json.loads(l["ideias"]), l["motivo"], json.loads(l["dados"]), l["estado"], l["criado_em"])
            if estado != "pendente" or all(i in ativas for i in p.ideias if p.tipo != "agrupar") and any(i in ativas for i in p.ideias):
                saida.append(p)
        return saida

    def descrever(self, p: Proposta) -> str:
        nomes = []
        for i in p.ideias:
            try:
                nomes.append(self.grafo.ideia(i).nome)
            except KeyError:
                nomes.append("(apagada)")
        if p.tipo == "arquivar":
            return f"arquivar \"{nomes[0]}\" ({p.motivo})"
        if p.tipo == "fundir":
            return f"juntar \"{nomes[0]}\" e \"{nomes[1]}\" em: \"{p.dados.get('texto', '')}\" ({p.motivo})"
        if p.tipo == "agrupar":
            return f"pôr {', '.join(nomes)} dentro de um assunto novo, \"{p.dados.get('nome', '')}\" ({p.motivo})"
        return f"contradição entre \"{nomes[0]}\" e \"{nomes[1]}\" ({p.motivo}); diga qual vale ou se as duas valem"

    def listar(self) -> str:
        lista = self.propostas()
        if not lista:
            return "Não há propostas de limpeza da memória."
        return "Propostas de limpeza da memória:\n" + "\n".join(f"{n}. {self.descrever(p)}" for n, p in enumerate(lista, 1))

    def proposta_numero(self, numero: int) -> Proposta:
        lista = self.propostas()
        if not 1 <= numero <= len(lista):
            raise ValueError(f"Não há proposta número {numero} (há {len(lista)}).")
        return lista[numero - 1]

    # --- revisão (19.6.1, 5, 8, 14, 25) -----------------------------------------------------------------------------
    def revisar(self, automatica: bool = False, usar_ia: bool = True, agora: datetime | None = None) -> ResumoRevisao:
        r = ResumoRevisao(automatica=automatica)
        agora = agora or _agora()
        autor = "revisão automática da memória" if automatica else "revisão da memória pedida pelo dono"
        with self.grafo.como(autor):
            r.novas["arquivar"] = self._expiradas(agora) + self._substituidas()
            r.novas["contradicao"] = self._contradicoes()
            r.ligacoes_para_revisar = self._ligacoes_que_mudaram()
            if usar_ia and self.sem.com_modelo:
                for etapa, tipo in ((self._duplicatas, "fundir"), (self._grupos, "agrupar")):
                    if not self._pode_gastar():
                        r.parou_por_teto = self.completador is not None
                        break
                    try:
                        n, custo = etapa()
                        r.novas[tipo] = n
                        r.custo += custo
                    except Exception as erro:  # resposta ruim ou rede: o resto da revisão vale
                        r.erros.append(f"{tipo}: {type(erro).__name__}: {erro}")
        self.grafo.definir_marca("higiene:ultima", agora.isoformat(timespec="seconds"))
        return r

    def precisa_revisar(self, dias: float, agora: datetime | None = None) -> bool:
        if dias <= 0:
            return False
        ultima = _data(self.grafo.marca("higiene:ultima") or "")
        return ultima is None or (agora or _agora()) - ultima >= timedelta(days=dias)

    def _livres(self) -> list[Ideia]:
        return [i for i in self.grafo.listar_ideias() if not i.protegido]

    def _expiradas(self, agora: datetime) -> int:
        n = 0
        hoje = agora.date()
        for i in self._livres():
            motivo = ""
            if i.validade:
                try:
                    if date.fromisoformat(i.validade) < hoje:
                        motivo = f"a validade ({date.fromisoformat(i.validade):%d/%m/%Y}) passou"
                except ValueError:
                    pass
            if not motivo and i.durabilidade == "temporaria":
                datas = [d for d in (_data(i.atualizado_em), _data(i.ultimo_uso)) if d]
                if datas and agora - max(datas) > timedelta(days=DIAS_TEMPORARIA):
                    motivo = f"é temporária e não é usada nem muda há mais de {DIAS_TEMPORARIA} dias"
            if motivo:
                n += self._propor("arquivar", [i.id], motivo, {})
        return n

    def _substituidas(self) -> int:
        """Ligação "substitui" (A substitui B): propõe arquivar B, a versão antiga."""
        livres = {i.id: i for i in self._livres()}
        ativas = {i.id: i for i in self.grafo.listar_ideias()}
        n = 0
        for l in self.grafo.listar_ligacoes():
            if l.relacao == "substitui" and l.de in (l.a, l.b) and l.status != "rejeitada":
                velha = l.b if l.de == l.a else l.a
                if velha in livres and l.de in ativas:
                    n += self._propor("arquivar", [velha], f"\"{ativas[l.de].nome}\" é a versão mais nova", {})
        return n

    def _contradicoes(self) -> int:
        ativas = {i.id for i in self.grafo.listar_ideias()}
        n = 0
        for l in self.grafo.listar_ligacoes():
            if l.relacao == "contradiz" and l.a in ativas and l.b in ativas:
                n += self._propor("contradicao", [l.a, l.b], l.motivo or "as duas dizem coisas incompatíveis", {})
        return n

    def _ligacoes_que_mudaram(self) -> int:
        """19.6.25: ligação automática cuja ideia mudou de texto depois de ligada fica "revisar" (não sai)."""
        n = 0
        for l in self.grafo.listar_ligacoes():
            if l.origem == "manual" or l.status != "ativa":
                continue
            mudou = self.grafo.banco._consultar(
                "SELECT 1 FROM historico WHERE ideia_id IN (?, ?) AND acao IN ('atualizar', 'renomear') AND quando > ? LIMIT 1",
                (l.a, l.b, l_criado(self.grafo, l.id)),
            )
            if mudou:
                self.grafo.banco._executar("UPDATE ligacoes SET status = 'revisar' WHERE id = ?", (l.id,))
                self.grafo._registrar("revisar", ligacao_id=l.id, antes={"status": "ativa"},
                                      depois={"status": "revisar", "motivo": "uma das ideias mudou depois da ligação"})
                n += 1
        return n

    def _duplicatas(self) -> tuple[int, float]:
        livres = {i.id: i for i in self._livres()}
        pares, vistos = [], set()
        for i in livres.values():
            for outra, nota in self.sem.candidatas(i.id, 3, MIN_DUPLICATA):
                par = frozenset((i.id, outra.id))
                if outra.id in livres and outra.origem == i.origem and par not in vistos:
                    vistos.add(par)
                    pares.append((nota, i, outra))
        ja = {frozenset(json.loads(l["ideias"])) for l in self.grafo.banco._consultar("SELECT ideias FROM propostas WHERE tipo = 'fundir'")}
        pares = [p for p in sorted(pares, key=lambda p: -p[0]) if frozenset((p[1].id, p[2].id)) not in ja][:MAX_PARES_DUPLICATA]
        if not pares:
            return 0, 0.0
        usuario = "\n\n".join(f"PAR {n}:\nA: {_texto(a)}\nB: {_texto(b)}" for n, (_s, a, b) in enumerate(pares, 1))
        dados, custo = self._perguntar(PROMPT_DUPLICATAS, usuario)
        n_novas = 0
        for x in (dados or {}).get("pares", []) if isinstance(dados, dict) else []:
            try:
                k = int(x.get("n"))
            except (TypeError, ValueError, AttributeError):
                continue
            if 1 <= k <= len(pares) and x.get("mesma") is True and str(x.get("texto", "")).strip():
                _s, a, b = pares[k - 1]
                n_novas += self._propor("fundir", [a.id, b.id], str(x.get("motivo", "guardam a mesma informação"))[:200],
                                        {"texto": " ".join(str(x["texto"]).split())[:600]})
        return n_novas, custo

    def _grupos(self) -> tuple[int, float]:
        livres = {i.id: i for i in self._livres() if i.origem != "fato" and not i.pai and i.nivel != "tronco"}
        com_filhos = {i.pai for i in self.grafo.listar_ideias() if i.pai}
        livres = {k: v for k, v in livres.items() if k not in com_filhos}
        vizinhos: dict[str, set] = {}
        for l in self.grafo.listar_ligacoes():
            if l.a in livres and l.b in livres:
                vizinhos.setdefault(l.a, set()).add(l.b)
                vizinhos.setdefault(l.b, set()).add(l.a)
        grupos, vistos = [], set()
        for k in vizinhos:
            if k in vistos:
                continue
            pilha, comp = [k], set()
            while pilha:
                x = pilha.pop()
                if x not in comp:
                    comp.add(x)
                    pilha += list(vizinhos.get(x, ()))
            vistos |= comp
            if MIN_GRUPO <= len(comp) <= 12:
                grupos.append(sorted(comp))
        ja = {l["chave"] for l in self.grafo.banco._consultar("SELECT chave FROM propostas WHERE tipo = 'agrupar'")}
        grupos = [g for g in grupos if "agrupar:" + ",".join(sorted(g)) not in ja][:MAX_GRUPOS]
        if not grupos:
            return 0, 0.0
        usuario = "\n\n".join(f"GRUPO {n}:\n" + "\n".join(f"- {_texto(livres[i])}" for i in g) for n, g in enumerate(grupos, 1))
        dados, custo = self._perguntar(PROMPT_GRUPOS, usuario)
        n_novas = 0
        for x in (dados or {}).get("grupos", []) if isinstance(dados, dict) else []:
            try:
                k = int(x.get("n"))
            except (TypeError, ValueError, AttributeError):
                continue
            nome = " ".join(str(x.get("nome", "")).split())[:60]
            if 1 <= k <= len(grupos) and x.get("agrupar") is True and nome:
                n_novas += self._propor("agrupar", grupos[k - 1], str(x.get("motivo", ""))[:200], {"nome": nome})
        return n_novas, custo

    # --- 3. decisões do dono -----------------------------------------------------------------------------------------
    def recusar(self, p: Proposta) -> str:
        self.grafo.banco._executar("UPDATE propostas SET estado = 'recusada', decidido_em = ? WHERE id = ?",
                                   (self.grafo.banco._relogio(), p.id))
        return "Combinado, deixei como está e não vou propor isso de novo."

    def aplicar(self, p: Proposta, opcao: str = "") -> str:
        """Executa a proposta aceita pelo dono. `opcao` só na contradição: "primeira", "segunda" ou "ambas"."""
        g = self.grafo
        with g.como("voz"):
            if p.tipo == "arquivar":
                i = g.ideia(p.ideias[0])
                g.arquivar(i.id, p.motivo)
                self._espelhar(i)
                texto = f"Arquivei \"{i.nome}\". Dá para restaurar depois."
            elif p.tipo == "fundir":
                texto = self._fundir(g.ideia(p.ideias[0]), g.ideia(p.ideias[1]), p.dados["texto"], p.motivo)
            elif p.tipo == "agrupar":
                membros = [g.ideia(i) for i in p.ideias if i in {x.id for x in g.listar_ideias()}]
                novo = g.criar_ideia(p.dados["nome"], membros[0].espaco_id if membros else None, "manual",
                                     resumo=p.motivo, nivel="tronco")
                for m in membros:
                    if m.pai is None:
                        g.definir_pai(m.id, novo.id)
                texto = f"Criei o assunto \"{novo.nome}\" e pus {len(membros)} ideias dentro dele."
            else:
                a, b = g.ideia(p.ideias[0]), g.ideia(p.ideias[1])
                if opcao not in ("primeira", "segunda", "ambas"):
                    raise ValueError(f"Diga qual vale: a primeira (\"{a.resumo or a.nome}\"), a segunda (\"{b.resumo or b.nome}\") ou as duas.")
                if opcao == "ambas":
                    texto = "Certo, as duas continuam valendo."
                else:
                    fica, sai = (a, b) if opcao == "primeira" else (b, a)
                    g.arquivar(sai.id, f"contradizia \"{fica.nome}\"; o dono ficou com essa")
                    self._espelhar(sai)
                    texto = f"Fiquei com \"{fica.resumo or fica.nome}\" e arquivei a outra."
        g.banco._executar("UPDATE propostas SET estado = 'aceita', decidido_em = ? WHERE id = ?", (g.banco._relogio(), p.id))
        return texto

    def _espelhar(self, i: Ideia) -> None:
        if i.origem == "fato" and self.memorias is not None:
            self.memorias.espelhar()  # a lembrança arquivada sai da cópia memoria.md

    def _fundir(self, a: Ideia, b: Ideia, texto: str, motivo: str) -> str:
        """Fica a mais antiga (ou a protegida) com o texto juntado; a outra é arquivada. Origens, dias e ligações da que
        sai passam para a que fica; o histórico guarda as duas versões."""
        g = self.grafo
        fica, sai = (a, b) if (a.protegido, b.criado_em) >= (b.protegido, a.criado_em) else (b, a)
        if fica.origem == "fato" and self.memorias is not None:
            self.memorias.editar(fica.id, texto)
        else:
            g.atualizar_ideia(fica.id, resumo=texto)
        con = g.banco._con
        with g.banco._lock, con:
            con.execute("INSERT OR IGNORE INTO ideia_fontes (ideia_id, conversa_id) SELECT ?, conversa_id FROM ideia_fontes WHERE ideia_id = ?", (fica.id, sai.id))
            con.execute("INSERT OR IGNORE INTO ideia_dias (ideia_id, dia) SELECT ?, dia FROM ideia_dias WHERE ideia_id = ?", (fica.id, sai.id))
            con.execute("UPDATE ideias SET usos = usos + ? WHERE id = ?", (sai.usos, fica.id))
        for l in g.listar_ligacoes():
            if sai.id in (l.a, l.b):
                outro = l.b if l.a == sai.id else l.a
                if outro != fica.id and g.ligacao_do_par(fica.id, outro) is None:
                    x, y = _par(fica.id, outro)
                    g.banco._executar(
                        "INSERT INTO ligacoes (id, a, b, motivo, origem, ativa, criado_em, tipo, forca, relacao, confianca, status, criado_por, de)"
                        " VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex, x, y, l.motivo, l.origem, g.banco._relogio(), l.tipo, l.forca, l.relacao, l.confianca,
                         l.status, l.criado_por, fica.id if l.de == sai.id else l.de),
                    )
        for filho in g.filhos(sai.id):
            g.definir_pai(filho.id, fica.id)
        g.arquivar(sai.id, f"juntada em \"{fica.nome}\"")
        g._registrar("fundir", fica.id, antes={"de": sai.id, "resumo_antigo": sai.resumo}, depois={"resumo": texto, "motivo": motivo})
        self._espelhar(sai)
        return f"Juntei as duas em \"{fica.nome}\": {texto}"


def l_criado(grafo: Grafo, ligacao_id: str) -> str:
    l = grafo.banco._consultar("SELECT criado_em FROM ligacoes WHERE id = ?", (ligacao_id,))
    return l[0]["criado_em"] if l else ""


def completador_padrao(cfg, registro, categoria: str, max_tokens: int = 1500):
    """Haiku do grafo, sem raciocínio longo, com o gasto na categoria certa (19.6.20)."""
    from eco.grafo.analise import CompletadorClaude, Resposta
    from eco.uso import extrair

    class Completador(CompletadorClaude):
        def completar(self, sistema: str, usuario: str):
            r = self.cliente.messages.create(model=self.modelo, max_tokens=max_tokens, system=sistema,
                                             messages=[{"role": "user", "content": usuario}], output_config={"effort": "low"})
            uso = extrair(self.modelo, r)
            if self.registro is not None:
                self.registro.registrar(uso, categoria=categoria)
            return Resposta("".join(getattr(b, "text", "") for b in r.content), uso.custo)

    return Completador(cfg, registro=registro)


def higiene_padrao(cfg, banco, memorias=None, registro=None, categoria: str | None = None) -> Higiene:
    from eco.uso import GRAFO_HIGIENE

    return Higiene(Grafo(banco), completador_padrao(cfg, registro, categoria or GRAFO_HIGIENE), memorias=memorias,
                   teto_usd=cfg.teto_grafo_usd)
