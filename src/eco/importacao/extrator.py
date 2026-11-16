"""Extração de fatos das conversas importadas (M4).

Para cada conversa a Eco manda ao modelo (Haiku por padrão) o texto montado por `filtro.dividir`, junto com as lembranças
que já tem, e recebe fatos curtos sobre o dono, cada um com a frase dele que o sustenta. Nada é gravado na memória aqui:
os fatos viram propostas (`estado.Importacoes`) para o dono aprovar item a item.

Cuidados:
- o prompt exclui estudo de faculdade, tarefas pontuais, dados de terceiros e o que a IA disse sozinha;
- a frase de apoio ("trecho") só é mantida se realmente aparece no que o dono escreveu; senão fica vazia, para o dono
  ver que não há citação;
- uma falha na conversa (rede, resposta torta) a deixa em "erro" e a importação segue; depois de 3 erros seguidos ela
  para, para não gastar à toa;
- o teto de custo é conferido antes de cada conversa, com a estimativa; ao atingir, para e o resto fica para depois;
- só depois de todas as partes de uma conversa voltarem é que os fatos viram propostas (conversa meio lida não propõe).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from eco.chat.armazenamento import _sem_acento
from eco.grafo.dados import DURABILIDADES
from eco.importacao import filtro
from eco.importacao.estado import Importacoes, chave_do_fato
from eco.importacao.modelo import Conversa

MAX_FATOS_POR_PARTE = 8
MIN_CARACTERES_FATO = 12
MAX_CARACTERES_FATO = 300
MAX_CARACTERES_TRECHO = 400
MAX_LEMBRANCAS_NO_PROMPT = 60
MAX_CARACTERES_LEMBRANCA = 200
MAX_ERROS_SEGUIDOS = 3
CONFERE_TRECHO = 0.7  # fração das palavras do trecho que precisa estar nas mensagens do dono

PROMPT = """Você lê uma conversa entre o dono de uma assistente pessoal e outra IA e extrai FATOS DURADOUROS sobre o dono, para a memória de longo prazo da assistente.

Devolva apenas:
- fatos pessoais: quem ele é, rotina, preferências, pessoas próximas, planos e decisões de vida;
- projetos e ideias dele: que projeto existe, o que é e para quem, em uma frase.

Só devolva o que o dono ESCREVEU ou deixou claro sobre si mesmo. Não deduza: um interesse não sai de uma palavra solta, e uma pergunta, um cálculo ou um exemplo que ele pediu não é um fato nem uma meta dele.

NÃO devolva:
- estudo de faculdade (matérias, provas, trabalhos de aula) nem o que é só o curso em que ele estuda;
- tarefas pontuais e perguntas do dia a dia (receitas, dúvidas técnicas, o código ou o texto que ele pediu);
- requisitos, pedidos de funcionalidade e detalhes técnicos de um software ou de uma planilha (basta dizer que o projeto existe e para que serve);
- coisas que só a IA disse: use apenas o que o dono escreveu ou confirmou;
- dados de terceiros (e-mails, telefones, documentos de outras pessoas) e dados sensíveis (senhas, números de documentos, números de contas);
- o que já consta nas "LEMBRANÇAS ATUAIS" ou nos "JÁ PROPOSTOS", mesmo com outras palavras.

Regras:
- JUNTE fatos do mesmo assunto em um só quando couberem numa frase (uma carteira de investimentos é um fato, não dez);
- um fato por item, curto e específico, em terceira pessoa e com o MESMO sujeito das lembranças atuais: se elas dizem "Vitor", escreva "Vitor", nunca "o dono"; se não houver lembranças, comece por "O dono";
- "trecho": copie, quase literalmente, a frase do dono que sustenta o fato;
- "duracao": "permanente" (não muda, como o nome de um parente), "duradoura" (vale por meses ou anos, como um projeto ou um hábito) ou "temporaria" (passa logo, como um processo seletivo em andamento ou uma prova da semana). Para "temporaria", se a conversa der uma data limite, ponha em "validade" (AAAA-MM-DD); senão deixe vazio;
- no máximo @MAX@ fatos; se não houver nenhum claro, devolva a lista vazia;
- tudo em português do Brasil.
Responda SOMENTE com JSON, sem texto antes ou depois, neste formato:
{"fatos": [{"texto": "...", "trecho": "...", "duracao": "duradoura", "validade": ""}]}""".replace("@MAX@", str(MAX_FATOS_POR_PARTE))


@dataclass(frozen=True)
class FatoProposto:
    texto: str
    trecho: str = ""
    duracao: str = ""  # permanente, duradoura ou temporaria; vazio se o modelo não disse (ou disse algo inválido)
    validade: str = ""  # AAAA-MM-DD


@dataclass
class Resumo:
    analisadas: int = 0
    com_fatos: int = 0
    sem_fatos: int = 0
    propostas: int = 0  # fatos novos guardados para revisão
    repetidas: int = 0  # já propostas antes (inclusive as que o dono rejeitou)
    custo: float = 0.0
    erros: list[str] = field(default_factory=list)
    parou_por_teto: bool = False
    parou_por_erros: bool = False
    interrompido: bool = False
    restantes: int = 0  # conversas que ficaram sem ser analisadas por uma parada

    def frase(self) -> str:
        texto = (f"Analisei {self.analisadas} conversa(s): {self.propostas} fato(s) para você revisar, em {self.com_fatos} delas; "
                 f"{self.sem_fatos} sem nenhum fato. Custo: US$ {self.custo:.4f}.")
        if self.repetidas:
            texto += f" {self.repetidas} fato(s) já tinham sido propostos antes."
        if self.erros:
            texto += f" {len(self.erros)} conversa(s) deram erro e ficam para a próxima vez."
        if self.parou_por_teto:
            texto += f" Parei no teto de custo; {self.restantes} conversa(s) ficaram para depois."
        if self.parou_por_erros:
            texto += f" Parei depois de {MAX_ERROS_SEGUIDOS} erros seguidos; {self.restantes} conversa(s) ficaram para depois."
        if self.interrompido:
            texto += f" Interrompido; {self.restantes} conversa(s) ficaram para depois."
        return texto


def _normalizar(texto: str) -> str:
    return " ".join(_sem_acento(texto).split())


def _palavras(texto: str) -> list[str]:
    return re.findall(r"\w+", _normalizar(texto))


def _do_dono(parte: str) -> str:
    return " ".join(l[len("Dono: "):] for l in parte.splitlines() if l.startswith("Dono: "))


def _trecho_confere(trecho: str, parte: str) -> bool:
    """O trecho aparece (quase literalmente) no que o dono escreveu nesta parte."""
    dono = _normalizar(_do_dono(parte))
    alvo = _normalizar(trecho)
    if not alvo:
        return False
    if alvo in dono:
        return True
    palavras = _palavras(trecho)
    if len(palavras) < 4:
        return False
    conhecidas = set(_palavras(dono))
    return sum(p in conhecidas for p in palavras) / len(palavras) >= CONFERE_TRECHO


def interpretar(texto: str, parte: str = "") -> list[FatoProposto]:
    """Os fatos da resposta do modelo (aceita cerca de código e texto em volta). Descarta o que está vazio, curto ou
    longo demais e repetido na mesma resposta. Se a `parte` é dada, o trecho que não aparece nela fica vazio."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (texto or "").strip())
    ini, fim = t.find("{"), t.rfind("}")
    if ini < 0 or fim <= ini:
        raise ValueError("A resposta não veio em JSON.")
    dados = json.loads(t[ini : fim + 1])
    itens = dados.get("fatos", []) if isinstance(dados, dict) else []
    saida: list[FatoProposto] = []
    vistos: set[str] = set()
    for item in itens if isinstance(itens, list) else []:
        if isinstance(item, str):
            item = {"texto": item}
        if not isinstance(item, dict):
            continue
        fato = " ".join(str(item.get("texto") or "").split())
        if not MIN_CARACTERES_FATO <= len(fato) <= MAX_CARACTERES_FATO or chave_do_fato(fato) in vistos:
            continue
        vistos.add(chave_do_fato(fato))
        trecho = " ".join(str(item.get("trecho") or "").split())[:MAX_CARACTERES_TRECHO]
        if parte and not _trecho_confere(trecho, parte):
            trecho = ""
        duracao = _sem_acento(str(item.get("duracao") or "")).strip()
        if duracao not in DURABILIDADES:
            duracao = ""
        try:
            validade = date.fromisoformat(str(item.get("validade") or "").strip()).isoformat() if duracao == "temporaria" else ""
        except ValueError:
            validade = ""
        saida.append(FatoProposto(fato, trecho, duracao, validade))
        if len(saida) >= MAX_FATOS_POR_PARTE:
            break
    return saida


def lembrancas_da_memoria(memorias: Any) -> list[str]:
    """Os textos das lembranças atuais (a Eco passa ao modelo para ele não repetir o que já sabe)."""
    return [f.texto for f in memorias.fatos()]


class Extrator:
    def __init__(self, completador: Any, estado: Importacoes, modelo: str, teto_usd: float = 0.0,
                 lembrancas: Callable[[], list[str]] | None = None) -> None:
        self.completador, self.estado, self.modelo, self.teto_usd = completador, estado, modelo, teto_usd
        self.lembrancas = lembrancas or (lambda: [])

    @staticmethod
    def _lista(textos: list[str], mais_recentes: bool = False) -> str:
        escolhidos = textos[-MAX_LEMBRANCAS_NO_PROMPT:] if mais_recentes else textos[:MAX_LEMBRANCAS_NO_PROMPT]
        return "\n".join(f"- {t[:MAX_CARACTERES_LEMBRANCA]}" for t in escolhidos) or "(nenhuma)"

    def _usuario(self, parte: str, lembrancas: list[str], propostos: list[str] | None = None) -> str:
        return (f"LEMBRANÇAS ATUAIS:\n{self._lista(lembrancas)}\n\nJÁ PROPOSTOS:\n{self._lista(propostos or [], True)}"
                f"\n\nCONVERSA:\n{parte}")

    def _extrair(self, partes: list[str], lembrancas: list[str],
                 propostos: list[str] | None = None) -> tuple[list[FatoProposto], float]:
        """Os fatos de todas as partes (sem repetir) e o custo. Se uma parte falha, a exceção sobe com o custo já gasto."""
        fatos: list[FatoProposto] = []
        vistos: set[str] = set()
        custo = 0.0
        for parte in partes:
            novos: list[FatoProposto] = []
            for tentativa in range(2):
                try:
                    r = self.completador.completar(PROMPT, self._usuario(parte, lembrancas, propostos))
                    custo += r.custo
                    novos = interpretar(r.texto, parte)
                    break
                except ValueError as erro:  # JSON torto (aspas soltas no texto, por exemplo): vale tentar mais uma vez
                    if tentativa == 1:
                        raise _Falha(f"{type(erro).__name__}: {erro}", custo) from erro
                except Exception as erro:
                    raise _Falha(f"{type(erro).__name__}: {erro}", custo) from erro
            for f in novos:
                if chave_do_fato(f.texto) not in vistos:
                    vistos.add(chave_do_fato(f.texto))
                    fatos.append(f)
        return fatos, custo

    def executar(self, conversas: list[Conversa], importacao_id: str = "",
                 ao_progresso: Callable[[int, int, str, Resumo], None] | None = None,
                 parar: Callable[[], bool] | None = None) -> Resumo:
        """Analisa as conversas na ordem dada. `parar()` devolvendo True interrompe entre uma conversa e outra."""
        resumo = Resumo()
        lembrancas = self.lembrancas()
        # o que já foi proposto (inclusive o que o dono rejeitou) vai junto, para o modelo não repetir; cresce a cada conversa
        propostos = [p.texto for estado in ("rejeitada", "pendente") for p in self.estado.propostas(estado)]
        seguidos = 0
        total = len(conversas)
        for n, c in enumerate(conversas):
            restantes = total - n
            if parar is not None and parar():
                resumo.interrompido, resumo.restantes = True, restantes
                break
            partes = filtro.dividir(c)
            if not partes:  # sem texto nenhum: nada a enviar
                self.estado.registrar_conversa(c, importacao_id, estado="ignorada")
                continue
            previsto = filtro.estimar(partes, self.modelo).custo_usd
            if self.teto_usd > 0 and resumo.custo + previsto > self.teto_usd:
                resumo.parou_por_teto, resumo.restantes = True, restantes
                break
            self.estado.registrar_conversa(c, importacao_id)
            try:
                fatos, custo = self._extrair(partes, lembrancas, propostos)
            except _Falha as f:
                resumo.custo += f.custo
                resumo.erros.append(f"{c.titulo}: {f}")
                self.estado.definir_estado(c.uuid, "erro", custo=f.custo, erro=str(f))
                if ao_progresso is not None:
                    ao_progresso(n + 1, total, c.titulo, resumo)
                seguidos += 1
                if seguidos >= MAX_ERROS_SEGUIDOS:
                    resumo.parou_por_erros, resumo.restantes = True, restantes - 1
                    break
                continue
            seguidos = 0
            resumo.custo += custo
            novas = 0
            for f in fatos:
                if self.estado.propor(c.uuid, f.texto, f.trecho, f.duracao, f.validade):
                    novas += 1
                    propostos.append(f.texto)
            resumo.propostas += novas
            resumo.repetidas += len(fatos) - novas
            resumo.analisadas += 1
            if novas:
                resumo.com_fatos += 1
            else:
                resumo.sem_fatos += 1
            self.estado.definir_estado(c.uuid, "analisada" if novas else "sem_fatos", custo=custo)
            if ao_progresso is not None:
                ao_progresso(n + 1, total, c.titulo, resumo)
        return resumo


class _Falha(Exception):
    def __init__(self, mensagem: str, custo: float) -> None:
        super().__init__(mensagem)
        self.custo = custo
