"""A Eco lê as conversas (e a memória, as notas e as metas) e extrai ideias e ligações para o grafo.

Só analisa o que mudou desde a última vez (marca por fonte), respeita o que o dono apagou ou desligou e para ao
atingir o teto mensal de gasto do grafo. A chamada à IA fica atrás de `Completador`, separada do resto.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Protocol

from eco.chat.armazenamento import Banco
from eco.config import Config
from eco.ferramentas import memoria
from eco.grafo import fatos as fatos_do_grafo
from eco.grafo.dados import Grafo, chave_da_ideia, marca_de_texto
from eco.uso import GRAFO_ATUALIZACAO, PRECOS, RegistroUso, calcular_custo, extrair

MAX_CARACTERES_CONVERSA = 12_000  # o que de uma conversa vai para a análise (começo e fim, se for maior)
MAX_IDEIAS_EXISTENTES = 60  # nomes de ideias do espaço mostrados ao modelo, para ele reaproveitar
MAX_IDEIAS_POR_FONTE = 8
MAX_LIGACOES_POR_FONTE = 12
MIN_CARACTERES = 60  # conversa menor que isso não tem o que extrair
CARACTERES_POR_TOKEN = 3.5

PROMPT = """Você organiza o grafo de ideias de uma assistente pessoal. Recebe um texto (uma conversa, a memória, notas ou metas do dono) e devolve as ideias principais dele e como se ligam.

Regras:
- Devolva de 2 a {max_ideias} ideias, cada uma com nome específico de 2 a 5 palavras e um resumo de uma frase.
- Organize em hierarquia: "tronco" é um tópico amplo (ex.: "Investimentos"), "galho" um subtópico (ex.: "Ações") e "folha" um item concreto (ex.: "BOVA11"). Em "pai", ponha o nome da ideia logo acima (da sua lista ou das já existentes); tronco não tem pai. Prefira pendurar ideias novas em troncos e galhos que já existem.
- "tipo" é "entidade" quando a ideia é uma pessoa, um projeto ou um nicho; senão "ideia".
- Tarefas rotineiras (lavar louça, pagar conta, comprar pão) NÃO viram ideias. Só tarefas importantes, como "Entrevista de emprego na X".
- Se uma ideia já existe na lista "Ideias já existentes", use exatamente o mesmo nome em vez de inventar outro.
- Ligações (além da hierarquia, que já sai do "pai"): só quando houver relação de fato. "tipo": "causal" quando uma causa, depende ou decide a outra; "semelhante" quando tratam de assuntos parecidos, com "forca" de 0.1 a 0.4; "mencao" quando uma só cita a outra de passagem. Não ligue só porque dividem uma palavra. Cada ligação tem um motivo curto. Pode ligar ideias novas a ideias já existentes.
- Tudo em português do Brasil.
- Responda SOMENTE com JSON, sem texto antes ou depois, neste formato:
{{"ideias": [{{"nome": "...", "resumo": "...", "nivel": "tronco|galho|folha", "pai": "nome ou vazio", "tipo": "ideia|entidade", "dias": ["AAAA-MM-DD"]}}], "ligacoes": [{{"de": "nome da ideia", "para": "nome da ideia", "tipo": "causal|semelhante|mencao", "forca": 0.3, "motivo": "..."}}]}}"""

TIPOS_DA_IA = {"causal": "causal", "semelhante": "semantica", "semantica": "semantica", "mencao": "lateral", "lateral": "lateral"}

PROMPT_FATOS = """Você organiza a memória de uma assistente pessoal. Recebe lembranças numeradas sobre o dono.

Regras:
- Para cada lembrança, dê um título curto de 2 a 4 palavras que a identifique (ex.: "Curso na USP", "Namorada Isabela"), sem repetir títulos.
- Ligue duas lembranças só quando houver relação de fato (a mesma pessoa, o mesmo plano ou decisão, uma explica a outra). Não ligue só porque dividem uma palavra, como o nome do dono. Cada ligação tem um motivo curto e um "tipo": "causal" (uma causa ou explica a outra), "semelhante" (mesmo assunto, com "forca" de 0.1 a 0.4) ou "mencao" (só cita de passagem).
- Tudo em português do Brasil.
- Responda SOMENTE com JSON, sem texto antes ou depois, neste formato:
{"titulos": [{"n": 1, "titulo": "..."}], "ligacoes": [{"de": 1, "para": 2, "tipo": "causal|semelhante|mencao", "forca": 0.3, "motivo": "..."}]}"""

MAX_FATOS_ANALISADOS = 60

DICAS_FONTE = {
    "notas": "O texto são as páginas do bloco de notas do dono, uma por dia, cada uma sob um título '## AAAA-MM-DD'. Um assunto que aparece em mais de um dia é UMA ideia só; em cada ideia, o campo \"dias\" lista os dias (AAAA-MM-DD) em que ela aparece.",
    "metas": "O texto é a lista de metas do dono. Agrupe-as em ideias por tópico (ex.: Leitura, Investimentos, Estudos).",
    "conversa": "O texto é uma conversa entre o dono e a Eco.",
}


@dataclass
class Resposta:
    texto: str
    custo: float = 0.0


class Completador(Protocol):
    def completar(self, sistema: str, usuario: str) -> Resposta: ...


class CompletadorClaude:
    """Chamada simples (sem streaming) ao modelo do grafo, que por padrão é o mais barato."""

    def __init__(self, cfg: Config, cliente: Any | None = None, registro: RegistroUso | None = None, modelo: str | None = None) -> None:
        import anthropic

        self.cfg, self.registro = cfg, registro
        self.modelo = modelo or cfg.modelo_grafo
        self.cliente = cliente or anthropic.Anthropic()

    def completar(self, sistema: str, usuario: str) -> Resposta:
        r = self.cliente.messages.create(
            model=self.modelo,
            max_tokens=4500,  # cabem até ~14 ideias com nível, pai e ligações; mais curto cortava o JSON no meio
            system=sistema,
            messages=[{"role": "user", "content": usuario}],
            output_config={"effort": "low"},
        )
        uso = extrair(self.modelo, r)
        if self.registro is not None:
            self.registro.registrar(uso, categoria=GRAFO_ATUALIZACAO)
        texto = "".join(getattr(b, "text", "") for b in r.content)
        return Resposta(texto, uso.custo)


@dataclass(frozen=True)
class Pendente:
    fonte: str  # "conversa:<id>", "fatos", "notas" ou "metas"
    tipo: str
    titulo: str
    texto: str
    marca: str
    espaco_id: str | None = None
    conversa_id: str | None = None


@dataclass(frozen=True)
class Estimativa:
    chamadas: int
    tokens_entrada: int
    tokens_saida: int
    custo_usd: float


@dataclass
class Resumo:
    analisadas: int = 0
    ideias_novas: int = 0
    ligacoes_novas: int = 0
    custo: float = 0.0
    erros: list[str] = field(default_factory=list)
    parou_por_teto: bool = False
    interrompido: bool = False


MAX_IDEIAS_TEXTO_LONGO = 14  # memória, notas e metas juntam vários assuntos: cabem mais ideias que numa conversa


def interpretar(texto: str, max_ideias: int = MAX_IDEIAS_POR_FONTE) -> dict[str, list]:
    """Extrai o JSON da resposta do modelo (aceita cerca de código e texto em volta). Devolve listas válidas."""
    t = texto.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    ini, fim = t.find("{"), t.rfind("}")
    if ini < 0 or fim <= ini:
        raise ValueError("A resposta não veio em JSON.")
    dados = json.loads(t[ini : fim + 1])
    ideias = [
        {
            "nome": " ".join(str(i.get("nome", "")).split()),
            "resumo": str(i.get("resumo", "")).strip(),
            "dias": [str(d).strip() for d in i.get("dias", []) if isinstance(d, str)] if isinstance(i.get("dias"), list) else [],
            "nivel": str(i.get("nivel") or "").strip().lower(),
            "pai": " ".join(str(i.get("pai") or "").split()),
            "tipo": "entidade" if str(i.get("tipo") or "").strip().lower() == "entidade" else "ideia",
        }
        for i in dados.get("ideias", [])
        if isinstance(i, dict) and str(i.get("nome", "")).strip()
    ]
    ligacoes = [
        {"de": str(l.get("de", "")).strip(), "para": str(l.get("para", "")).strip(), "motivo": str(l.get("motivo", "")).strip(), **_tipo_e_forca(l)}
        for l in dados.get("ligacoes", [])
        if isinstance(l, dict) and l.get("de") and l.get("para")
    ]
    return {"ideias": ideias[:max_ideias], "ligacoes": ligacoes[: max_ideias + 4]}


def _tipo_e_forca(l: dict) -> dict:
    """Tipo da ligação no vocabulário do grafo; sem tipo conhecido, vale como semelhante no limite (era o antigo)."""
    tipo = TIPOS_DA_IA.get(_sem_acento_simples(str(l.get("tipo") or "")), "semantica")
    try:
        forca = float(l.get("forca", 0.4))
    except (TypeError, ValueError):
        forca = 0.4
    return {"tipo": tipo, "forca": max(0.0, min(0.4, forca))}


def _sem_acento_simples(t: str) -> str:
    return t.strip().lower().replace("ç", "c").replace("ã", "a").replace("â", "a").replace("á", "a")


def _vale_a_ligacao(l: dict) -> bool:
    from eco.grafo.pesos import LIMIAR_SEMANTICO

    return l["tipo"] != "semantica" or l["forca"] >= LIMIAR_SEMANTICO


def interpretar_fatos(texto: str) -> dict[str, list]:
    """JSON da análise das lembranças: títulos por número e ligações entre números."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    ini, fim = t.find("{"), t.rfind("}")
    if ini < 0 or fim <= ini:
        raise ValueError("A resposta não veio em JSON.")
    dados = json.loads(t[ini : fim + 1])

    def numero(v: Any) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    titulos = [{"n": numero(i.get("n")), "titulo": " ".join(str(i.get("titulo", "")).split())} for i in dados.get("titulos", []) if isinstance(i, dict)]
    ligacoes = [
        {"de": numero(l.get("de")), "para": numero(l.get("para")), "motivo": str(l.get("motivo", "")).strip(), **_tipo_e_forca(l)}
        for l in dados.get("ligacoes", [])
        if isinstance(l, dict)
    ]
    return {"titulos": titulos, "ligacoes": ligacoes}


def _cortar(texto: str, limite: int = MAX_CARACTERES_CONVERSA) -> str:
    if len(texto) <= limite:
        return texto
    metade = limite // 2
    return texto[:metade] + "\n[...]\n" + texto[-metade:]


class Analisador:
    def __init__(self, banco: Banco, grafo: Grafo, completador: Completador, cfg: Config) -> None:
        self.banco, self.grafo, self.completador, self.cfg = banco, grafo, completador, cfg
        self.ligador = None  # E6: `grafo.ligacao.Ligador`; liga as ideias novas no fim da rodada

    # --- o que falta analisar ------------------------------------------------------------------------
    def pendentes(self) -> list[Pendente]:
        saida: list[Pendente] = []
        nomes = {e.id: e.nome for e in self.banco.listar_espacos()}
        for espaco_id in [None, *nomes]:
            for c in self.banco.listar_conversas(espaco_id):
                ms = self.banco.mensagens(c.id)
                if not ms:
                    continue
                marca = str(ms[-1].id)
                fonte = f"conversa:{c.id}"
                if self.grafo.marca(fonte) == marca:
                    continue
                texto = "\n".join(f"{'Dono' if m.papel == 'user' else 'Eco'}: {m.conteudo}" for m in ms)
                if len(texto) < MIN_CARACTERES:
                    continue
                saida.append(Pendente(fonte, "conversa", c.titulo, _cortar(texto), marca, espaco_id, c.id))
        for tipo, texto in self._textos_externos().items():
            if len(texto) >= MIN_CARACTERES and self.grafo.marca(tipo) != marca_de_texto(texto):
                saida.append(Pendente(tipo, tipo, {"notas": "Notas", "metas": "Metas"}[tipo], _cortar(texto), marca_de_texto(texto)))
        saida.extend(self._pendente_de_fatos())
        return saida

    def _pendente_de_fatos(self) -> list[Pendente]:
        """As lembranças da memória viram nós (sem custo) e a Claude só dá títulos e liga as parecidas, quando algo mudou."""
        from eco.grafo.memorias import Memorias

        mem = Memorias(self.grafo, self.cfg.pasta_dados)
        mem.inicializar()  # a memória mora no banco (D2); na primeira vez, sai do memoria.md
        lista = mem.fatos()[:MAX_FATOS_ANALISADOS]
        if len(lista) < 2:
            return []
        texto = "\n".join(f"{n}. {f.texto}" for n, f in enumerate(lista, 1))
        marca = marca_de_texto(texto)
        if self.grafo.marca("fatos") == marca:
            return []
        return [Pendente("fatos", "fatos", "Memória", texto, marca)]

    def _textos_externos(self) -> dict[str, str]:
        pasta = self.cfg.pasta_dados
        metas = pasta / "metas.md"
        textos = {"metas": metas.read_text(encoding="utf-8", errors="replace") if metas.exists() else ""}
        notas: list[str] = []
        for i in range(30):  # as notas dos últimos 30 dias
            arq = pasta / "notas" / f"{date.today() - timedelta(days=i):%Y-%m-%d}.md"
            if arq.exists():
                linhas = arq.read_text(encoding="utf-8", errors="replace").splitlines()
                corpo = "\n".join(l for l in linhas if not l.startswith("# "))
                notas.append(f"## {arq.stem}\n{corpo.strip()}")
        textos["notas"] = "\n".join(notas)
        return {k: v.strip() for k, v in textos.items()}

    def estimar(self, pendentes: list[Pendente]) -> Estimativa:
        entrada = saida = 0
        for p in pendentes:
            entrada += int((len(p.texto) + len(PROMPT) + 80 * MAX_IDEIAS_EXISTENTES) / CARACTERES_POR_TOKEN)
            saida += 600
        preco = self.cfg.modelo_grafo in PRECOS
        custo = calcular_custo(self.cfg.modelo_grafo, entrada, saida) if preco else 0.0
        return Estimativa(len(pendentes), entrada, saida, custo)

    # --- analisar -----------------------------------------------------------------------------------
    def _usuario(self, p: Pendente) -> str:
        if p.tipo == "fatos":
            return p.texto
        atuais = self.grafo.ideias_do_espaco(p.espaco_id) if p.tipo == "conversa" else self.grafo.ideias_da_origem(p.tipo)
        # o modelo reaproveita os nomes (e pendura nos troncos e galhos) em vez de duplicar; troncos e galhos vão primeiro
        ordem = {"tronco": 0, "galho": 1}
        atuais = sorted(atuais, key=lambda i: ordem.get(i.nivel, 2))[:MAX_IDEIAS_EXISTENTES]
        lista = "\n".join(f"- {i.nome}" + (f" ({i.nivel})" if i.nivel else "") for i in atuais) or "(nenhuma ainda)"
        escopo = ""
        if p.tipo == "conversa" and p.espaco_id:
            escopo = f"Espaço da conversa: {self.banco.espaco(p.espaco_id).nome}.\n"
        return f"{DICAS_FONTE[p.tipo]}\n{escopo}\nIdeias já existentes:\n{lista}\n\nTexto ({p.titulo}):\n{p.texto}"

    def analisar(self, p: Pendente) -> tuple[int, int, float]:
        """Analisa uma fonte e grava ideias e ligações. Devolve (ideias novas, ligações novas, custo)."""
        if p.tipo == "fatos":
            return self._analisar_fatos(p)
        limite = MAX_IDEIAS_POR_FONTE if p.tipo == "conversa" else MAX_IDEIAS_TEXTO_LONGO
        resp = self.completador.completar(PROMPT.format(max_ideias=limite), self._usuario(p))
        self.grafo.somar_gasto(resp.custo)
        dados = interpretar(resp.texto, limite)
        origem = "chat" if p.tipo == "conversa" else p.tipo
        antes = len(self.grafo.listar_ideias())
        por_nome: dict[str, str] = {}
        dias_do_texto = set(re.findall(r"^## (\d{4}-\d{2}-\d{2})$", p.texto, re.M)) if p.tipo == "notas" else set()
        for i in dados["ideias"]:
            if self.grafo.ignorada(i["nome"], p.espaco_id):
                continue
            ideia = self.grafo.criar_ideia(i["nome"], p.espaco_id, origem, i["resumo"], nivel=i["nivel"], tipo=i["tipo"])
            por_nome[chave_da_ideia(i["nome"])] = ideia.id
            if p.conversa_id:
                self.grafo.adicionar_fonte(ideia.id, p.conversa_id)
            if p.tipo == "notas":
                for dia in i.get("dias", []):
                    if dia in dias_do_texto:  # só dias que de fato estavam no texto enviado (o modelo pode errar)
                        self.grafo.adicionar_dia(ideia.id, dia)
        novas_ligacoes = 0
        atuais = self.grafo.ideias_do_espaco(p.espaco_id) if origem == "chat" else self.grafo.ideias_da_origem(origem)
        existentes = {chave_da_ideia(i.nome): i.id for i in atuais}
        for i in dados["ideias"]:  # hierarquia: só preenche quem ainda não tem pai (não desfaz o que o dono arrumou)
            filho = por_nome.get(chave_da_ideia(i["nome"]))
            pai = por_nome.get(chave_da_ideia(i["pai"])) or existentes.get(chave_da_ideia(i["pai"])) if i["pai"] else None
            if filho and pai and filho != pai and self.grafo.ideia(filho).pai is None:
                try:
                    self.grafo.definir_pai(filho, pai)
                except ValueError:  # o modelo propôs um círculo: ignora
                    pass
        for l in dados["ligacoes"]:
            a = por_nome.get(chave_da_ideia(l["de"])) or existentes.get(chave_da_ideia(l["de"]))
            b = por_nome.get(chave_da_ideia(l["para"])) or existentes.get(chave_da_ideia(l["para"]))
            if a and b and a != b and _vale_a_ligacao(l):
                criada = self.grafo.ligar(a, b, l["motivo"], "auto", l["tipo"], l["forca"])
                novas_ligacoes += 1 if criada is not None else 0
        self.grafo.definir_marca(p.fonte, p.marca)
        return len(self.grafo.listar_ideias()) - antes, novas_ligacoes, resp.custo

    def _analisar_fatos(self, p: Pendente) -> tuple[int, int, float]:
        """Dá título curto a cada lembrança (sem passar por cima de um título que o dono mudou) e liga as relacionadas."""
        resp = self.completador.completar(PROMPT_FATOS, p.texto)
        self.grafo.somar_gasto(resp.custo)
        dados = interpretar_fatos(resp.texto)
        por_texto: dict[str, list] = {}
        for no in self.grafo.ideias_da_origem(fatos_do_grafo.ORIGEM):
            por_texto.setdefault(no.resumo, []).append(no)
        nos = []  # na mesma ordem das lembranças numeradas no texto enviado
        from eco.grafo.memorias import Memorias

        for f in Memorias(self.grafo, self.cfg.pasta_dados).fatos()[:MAX_FATOS_ANALISADOS]:
            lista = por_texto.get(f.texto)
            nos.append(lista.pop(0) if lista else None)
        for t in dados["titulos"]:
            if 1 <= t["n"] <= len(nos) and nos[t["n"] - 1] is not None and t["titulo"]:
                no = nos[t["n"] - 1]
                if no.nome == fatos_do_grafo.titulo_provisorio(no.resumo):  # só troca o provisório
                    self.grafo.atualizar_ideia(no.id, nome=self._titulo_livre(t["titulo"], no.id))
        novas = 0
        for l in dados["ligacoes"]:
            a, b = (nos[l["de"] - 1] if 1 <= l["de"] <= len(nos) else None), (nos[l["para"] - 1] if 1 <= l["para"] <= len(nos) else None)
            if a and b and a.id != b.id and _vale_a_ligacao(l):
                novas += 1 if self.grafo.ligar(a.id, b.id, l["motivo"], "auto", l["tipo"], l["forca"]) is not None else 0
        self.grafo.definir_marca(p.fonte, p.marca)
        return 0, novas, resp.custo

    def _titulo_livre(self, titulo: str, id_: str) -> str:
        usados = {chave_da_ideia(i.nome) for i in self.grafo.ideias_da_origem(fatos_do_grafo.ORIGEM) if i.id != id_}
        nome, n = titulo, 2
        while chave_da_ideia(nome) in usados:
            nome, n = f"{titulo} ({n})", n + 1
        return nome

    def executar(
        self,
        pendentes: list[Pendente],
        ao_progresso: Callable[[int, int, str], None] | None = None,
        parar: threading.Event | None = None,
    ) -> Resumo:
        with self.grafo.como("análise do grafo"):  # o histórico e a origem das ideias dizem que veio da análise
            return self._executar(pendentes, ao_progresso, parar)

    def _executar(self, pendentes, ao_progresso, parar) -> Resumo:
        r = Resumo()
        self.grafo.limpar_orfas()
        for n, p in enumerate(pendentes, 1):
            if parar is not None and parar.is_set():
                r.interrompido = True
                break
            if self.grafo.gasto_do_mes() >= self.cfg.teto_grafo_usd:
                r.parou_por_teto = True
                break
            if ao_progresso:
                ao_progresso(n, len(pendentes), p.titulo)
            try:
                ideias, ligacoes, custo = self.analisar(p)
                r.analisadas += 1
                r.ideias_novas += ideias
                r.ligacoes_novas += ligacoes
                r.custo += custo
            except Exception as e:  # uma fonte ruim (JSON inválido, rede) não derruba as outras
                r.erros.append(f"{p.titulo}: {e}")
        r.ligacoes_novas += self.grafo.ligar_iguais_entre_espacos()
        if self.ligador is not None and not r.interrompido and not r.parou_por_teto:
            rl = self.ligador.executar()
            r.ligacoes_novas += rl.ativas + rl.revisar
            r.custo += rl.custo
            r.parou_por_teto = r.parou_por_teto or rl.parou_por_teto
            r.erros += [f"ligação: {e}" for e in rl.erros]
        return r
