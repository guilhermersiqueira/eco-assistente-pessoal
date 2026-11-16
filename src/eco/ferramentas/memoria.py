"""Memória de longo prazo sobre o dono: pasta/memoria.md, uma lembrança por linha.

    - Vitor cursa a USP. (09/11/2026)
    - [fixo] O dono se chama Vitor. (09/11/2026)

O conteúdo é enviado ao Claude a cada pedido (por isso `ler` limita o tamanho) e fica na pasta de dados, que está no
OneDrive. O dono pode abrir e editar o arquivo à mão. Lembrança marcada `[fixo]` nunca é cortada quando a memória passa
do limite; as outras entram da mais recente para a mais antiga até encher. No grafo, cada lembrança é um nó
(`eco.grafo.fatos`), e editar, fixar ou apagar por lá chega aqui.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LIMITE_CARACTERES = 4000
AVISAR_A_PARTIR = 0.85  # fração do limite a partir da qual a Eco avisa que a memória está enchendo
_LINHA = re.compile(r"^- (\[fixo\] )?(.*?)(?: \((\d{2}/\d{2}/\d{4})\))?\s*$")


@dataclass(frozen=True)
class Fato:
    texto: str
    data: str = ""  # dd/mm/aaaa, vazio se a linha não tem
    fixo: bool = False


def _arquivo(pasta: Path) -> Path:
    return pasta / "memoria.md"


def _normalizar(texto: str) -> str:
    return " ".join(texto.split())


def _fato_da_linha(linha: str) -> Fato | None:
    m = _LINHA.match(linha)
    if not linha.startswith("- ") or not m or not m.group(2).strip():
        return None
    return Fato(m.group(2).strip(), m.group(3) or "", bool(m.group(1)))


def _linha(f: Fato) -> str:
    return f"- {'[fixo] ' if f.fixo else ''}{f.texto}" + (f" ({f.data})" if f.data else "")


def _linhas(pasta: Path) -> list[str]:
    arquivo = _arquivo(pasta)
    return arquivo.read_text(encoding="utf-8").splitlines() if arquivo.exists() else []


def fatos(pasta: Path) -> list[Fato]:
    """Todas as lembranças, na ordem do arquivo."""
    return [f for f in map(_fato_da_linha, _linhas(pasta)) if f]


def _salvar(pasta: Path, linhas: list[str]) -> None:
    _arquivo(pasta).write_text("\n".join(linhas) + "\n", encoding="utf-8")


def _indice(linhas: list[str], texto: str) -> int:
    """Posição da linha da lembrança com exatamente esse texto (a primeira, se houver repetidas)."""
    alvo = _normalizar(texto)
    for i, l in enumerate(linhas):
        f = _fato_da_linha(l)
        if f and _normalizar(f.texto) == alvo:
            return i
    raise ValueError("Não encontrei essa lembrança (o arquivo pode ter sido editado à mão).")


def _indice_por_trecho(linhas: list[str], trecho: str) -> int:
    alvo = trecho.strip().lower()
    achadas = [i for i, l in enumerate(linhas) if (f := _fato_da_linha(l)) and alvo and alvo in f.texto.lower()]
    if not achadas:
        raise ValueError(f"Não encontrei nenhuma lembrança com '{trecho}'.")
    if len(achadas) > 1:
        raise ValueError(f"Mais de uma lembrança combina com '{trecho}'. Seja mais específico.")
    return achadas[0]


def usado(pasta: Path) -> int:
    """Caracteres que a memória inteira ocuparia no prompt (sem cortes)."""
    return len("\n".join(_linha(Fato(f.texto, f.data)) for f in fatos(pasta)))


def aviso_de_limite(pasta: Path, limite: int = LIMITE_CARACTERES) -> str:
    """Frase para a Eco repassar quando a memória está enchendo ou já passou do limite (vazio se há folga)."""
    n = usado(pasta)
    if n > limite:
        return (
            f" Atenção: a memória passou do limite ({n} de {limite} caracteres) e as lembranças mais antigas que não são fixas já não vão para "
            "a conversa. Sugira ao dono juntar ou resumir lembranças parecidas (editar_lembranca) ou esquecer o que não importa mais."
        )
    if n >= limite * AVISAR_A_PARTIR:
        return f" Aviso: a memória está em {n} de {limite} caracteres. Se o dono quiser, sugira juntar lembranças parecidas antes que encha."
    return ""


def lembrar(pasta: Path, fato: str, agora: datetime | None = None) -> str:
    fato = _normalizar(fato)
    if not fato:
        raise ValueError("Não há nada para lembrar.")
    agora = agora or datetime.now()
    arquivo = _arquivo(pasta)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    novo = not arquivo.exists()
    with arquivo.open("a", encoding="utf-8") as f:
        if novo:
            f.write("# Memória da Eco\n\n")
        f.write(_linha(Fato(fato, f"{agora:%d/%m/%Y}")) + "\n")
    return "Guardado." + aviso_de_limite(pasta)


def _selecionadas(lista: list[Fato], limite: int) -> list[Fato]:
    """O que cabe no limite: as fixas sempre; as outras da mais recente para a mais antiga, até encher."""
    total = sum(len(_linha(Fato(f.texto, f.data))) + 1 for f in lista if f.fixo)
    mantidas = {i for i, f in enumerate(lista) if f.fixo}
    for i in range(len(lista) - 1, -1, -1):
        if i in mantidas:
            continue
        total += len(_linha(Fato(lista[i].texto, lista[i].data))) + 1
        if total > limite:
            break
        mantidas.add(i)
    return [f for i, f in enumerate(lista) if i in mantidas]


def ler(pasta: Path, limite: int = LIMITE_CARACTERES) -> str:
    """O texto que vai para o Claude: cabe no limite, com as lembranças fixas sempre dentro (sem a marca [fixo])."""
    return "\n".join(_linha(Fato(f.texto, f.data)) for f in _selecionadas(fatos(pasta), limite))


def fora_do_prompt(pasta: Path, limite: int = LIMITE_CARACTERES) -> set[str]:
    """Textos das lembranças que não cabem no limite e por isso não vão para a conversa."""
    lista = fatos(pasta)
    dentro = {f.texto for f in _selecionadas(lista, limite)}
    return {f.texto for f in lista if f.texto not in dentro}


def esquecer(pasta: Path, trecho: str) -> str:
    if not _arquivo(pasta).exists():
        raise ValueError("Ainda não guardei nada.")
    linhas = _linhas(pasta)
    del linhas[_indice_por_trecho(linhas, trecho)]
    _salvar(pasta, linhas)
    return "Esquecido."


def apagar(pasta: Path, texto: str) -> None:
    """Apaga a lembrança com exatamente esse texto (a tela do grafo usa)."""
    linhas = _linhas(pasta)
    del linhas[_indice(linhas, texto)]
    _salvar(pasta, linhas)


def _trocar(pasta: Path, i: int, linhas: list[str], novo: Fato) -> None:
    linhas[i] = _linha(novo)
    _salvar(pasta, linhas)


def editar(pasta: Path, texto: str, novo_texto: str) -> Fato:
    """Troca o texto da lembrança (mantém a data e se é fixa). Devolve a lembrança já editada."""
    novo_texto = _normalizar(novo_texto)
    if not novo_texto:
        raise ValueError("O texto novo está vazio.")
    linhas = _linhas(pasta)
    i = _indice(linhas, texto)
    antiga = _fato_da_linha(linhas[i])
    assert antiga is not None
    novo = Fato(novo_texto, antiga.data, antiga.fixo)
    _trocar(pasta, i, linhas, novo)
    return novo


def fixar(pasta: Path, texto: str, fixo: bool = True) -> Fato:
    linhas = _linhas(pasta)
    i = _indice(linhas, texto)
    antiga = _fato_da_linha(linhas[i])
    assert antiga is not None
    novo = Fato(antiga.texto, antiga.data, fixo)
    _trocar(pasta, i, linhas, novo)
    return novo


def editar_por_trecho(pasta: Path, trecho: str, novo_texto: str) -> str:
    linhas = _linhas(pasta)
    f = _fato_da_linha(linhas[_indice_por_trecho(linhas, trecho)])
    assert f is not None
    editar(pasta, f.texto, novo_texto)
    return "Lembrança atualizada." + aviso_de_limite(pasta)


def fixar_por_trecho(pasta: Path, trecho: str, fixo: bool = True) -> str:
    linhas = _linhas(pasta)
    f = _fato_da_linha(linhas[_indice_por_trecho(linhas, trecho)])
    assert f is not None
    fixar(pasta, f.texto, fixo)
    return "Lembrança fixada: nunca será cortada." if fixo else "Lembrança desafixada."
