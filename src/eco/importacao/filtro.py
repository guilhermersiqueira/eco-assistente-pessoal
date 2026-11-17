"""Quais conversas vale analisar, como montar o texto de cada uma e quanto vai custar (M3). Tudo local, sem chamar a IA.

- Conversa curta (poucas mensagens do dono ou quase nenhum texto dele) não tem o que extrair e é ignorada.
- O texto da análise traz as mensagens do dono por inteiro e só um começo das respostas da assistente, como contexto:
  os fatos que interessam vêm do que o dono escreveu, e as respostas longas são o que mais encarece.
- Conversa grande é dividida em no máximo duas partes (as mesmas 12.000 letras por parte que a análise do grafo usa); se
  passar de duas, ficam a primeira e a última e o meio é omitido.
- `planejar` junta tudo: o que analisar (a conversa mais recente primeiro, para o teto de custo cortar as mais antigas), o
  que ignorar e quanto custa, antes de qualquer envio à API.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eco.grafo.analise import CARACTERES_POR_TOKEN, Estimativa
from eco.importacao.estado import Importacoes
from eco.importacao.modelo import DONO, Conversa
from eco.uso import PRECOS, calcular_custo

MIN_MENSAGENS_DONO = 4
MIN_CARACTERES_DONO = 300
MAX_CARACTERES_PARTE = 12_000
MAX_PARTES = 2
MAX_CARACTERES_ASSISTENTE = 300  # da fala da assistente entra só o começo
# Calibrados no ensaio real de 15/11/2026 (10 conversas, 14 chamadas): a estimativa anterior (2.500 letras de prompt e 500
# tokens de resposta) dava a metade do gasto. O prompt leva as lembranças atuais e os fatos já propostos; a resposta tem
# até 8 fatos com trecho, duração e validade.
TAMANHO_PROMPT = 8_000  # letras estimadas além do texto da conversa: prompt, lembranças atuais e já propostos
SAIDA_POR_PARTE = 1_000  # tokens de resposta estimados por chamada
AVISO_OMITIDO = "[trecho do meio da conversa omitido]"


def motivo_para_ignorar(c: Conversa) -> str:
    """Vazio se vale analisar; senão, o motivo em uma frase."""
    do_dono = c.do_dono()
    if c.da_memoria:  # um assunto da memória do claude.ai já é um resumo: basta ter algum item
        return "" if do_dono else "assunto da memória sem itens"
    if len(do_dono) < MIN_MENSAGENS_DONO:
        return f"conversa curta ({len(do_dono)} mensagem(ns) do dono)"
    if sum(len(m.texto.strip()) for m in do_dono) < MIN_CARACTERES_DONO:
        return "quase sem texto do dono"
    return ""


def _cabecalho(c: Conversa) -> str:
    data = f" ({c.criada_em[:10]})" if c.criada_em else ""
    if c.da_memoria:
        return (f"Memória do claude.ai sobre: {' '.join(c.titulo.split()) or 'sem título'}{data} "
                "(cada linha \"Dono:\" é um resumo do que o dono disse em várias conversas)")
    return f"Conversa: {' '.join(c.titulo.split()) or 'sem título'}{data}"


def _linhas(c: Conversa) -> list[str]:
    saida = []
    for m in c.mensagens:
        texto = " ".join(m.texto.split())
        if not texto:
            continue
        if m.autor == DONO:
            saida.append(f"Dono: {texto}")
        else:
            corte = texto[:MAX_CARACTERES_ASSISTENTE] + ("…" if len(texto) > MAX_CARACTERES_ASSISTENTE else "")
            saida.append(f"Assistente: {corte}")
    return saida


def dividir(c: Conversa) -> list[str]:
    """Os textos a enviar (de 1 a MAX_PARTES), cada um com o cabeçalho e no máximo MAX_CARACTERES_PARTE letras."""
    cab = _cabecalho(c)
    espaco = MAX_CARACTERES_PARTE - len(cab) - 1 - len(AVISO_OMITIDO) - 1
    partes: list[list[str]] = [[]]
    tamanho = 0
    for linha in _linhas(c):
        linha = linha[:espaco]  # uma única mensagem enorme (um texto colado) não pode estourar a parte
        if partes[-1] and tamanho + len(linha) + 1 > espaco:
            partes.append([])
            tamanho = 0
        partes[-1].append(linha)
        tamanho += len(linha) + 1
    if len(partes) > MAX_PARTES:
        partes = [partes[0], [AVISO_OMITIDO, *partes[-1]]]
    return [cab + "\n" + "\n".join(p) for p in partes if p]


def estimar(partes: list[str], modelo: str) -> Estimativa:
    """Tokens e custo de enviar estas partes (uma chamada por parte). Modelo fora da tabela de preços: custo 0."""
    entrada = sum(int((len(p) + TAMANHO_PROMPT) / CARACTERES_POR_TOKEN) for p in partes)
    saida = SAIDA_POR_PARTE * len(partes)
    return Estimativa(len(partes), entrada, saida, calcular_custo(modelo, entrada, saida) if modelo in PRECOS else 0.0)


@dataclass
class Plano:
    analisar: list[Conversa] = field(default_factory=list)  # da mais recente para a mais antiga
    ignoradas: list[tuple[Conversa, str]] = field(default_factory=list)  # (conversa, motivo)
    ja_feitas: int = 0  # vistas antes, com o mesmo texto
    estimativa: Estimativa = Estimativa(0, 0, 0, 0.0)
    teto_usd: float = 0.0
    cabem: int = 0  # quantas conversas de `analisar` cabem no teto, na ordem
    preco_conhecido: bool = True

    @property
    def passa_do_teto(self) -> bool:
        return self.cabem < len(self.analisar)

    def frase(self) -> str:
        total = len(self.analisar) + len(self.ignoradas) + self.ja_feitas
        partes = [f"O arquivo tem {total} conversa(s): {len(self.analisar)} para analisar"]
        if self.ja_feitas:
            partes.append(f"{self.ja_feitas} já importada(s) antes")
        if self.ignoradas:
            partes.append(f"{len(self.ignoradas)} ignorada(s) por serem curtas")
        texto = ", ".join(partes) + "."
        if not self.analisar:
            return texto + " Nada novo para analisar."
        if not self.preco_conhecido:
            return texto + " O modelo escolhido não está na tabela de preços, então não consigo estimar o custo."
        texto += f" Estimativa: cerca de US$ {self.estimativa.custo_usd:.4f} em {self.estimativa.chamadas} chamada(s)."
        if self.passa_do_teto:
            texto += f" Isso passa do teto de US$ {self.teto_usd:.2f}: cabem {self.cabem} conversa(s), as mais recentes primeiro."
        else:
            texto += f" Cabe no teto de US$ {self.teto_usd:.2f}."
        return texto


def planejar(conversas: list[Conversa], estado: Importacoes, modelo: str, teto_usd: float) -> Plano:
    """O que a importação faria com estas conversas e quanto custaria. Só lê o estado, não grava nada."""
    plano = Plano(teto_usd=teto_usd, preco_conhecido=modelo in PRECOS)
    candidatas: list[Conversa] = []
    for c in conversas:
        if estado.situacao(c.uuid, c.hash) == "feita":
            plano.ja_feitas += 1
        elif motivo := motivo_para_ignorar(c):
            plano.ignoradas.append((c, motivo))
        else:
            candidatas.append(c)
    plano.analisar = sorted(candidatas, key=lambda c: c.atualizada_em or c.criada_em, reverse=True)
    todas: list[str] = []
    acumulado = 0.0
    for c in plano.analisar:
        partes = dividir(c)
        todas += partes
        acumulado += estimar(partes, modelo).custo_usd
        if teto_usd <= 0 or acumulado <= teto_usd or not plano.preco_conhecido:
            plano.cabem += 1
    plano.estimativa = estimar(todas, modelo)
    return plano
