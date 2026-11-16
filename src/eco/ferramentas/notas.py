"""Anotações e metas em arquivos Markdown locais.

pasta/notas/AAAA-MM-DD.md  o bloco de notas do dia (aba Bloco): a Eco acrescenta uma linha com a hora ("anota no bloco"); o dono
                           edita a página inteira à mão
pasta/metas.md             lista de metas: "- [ ] meta (tópico: Estudos) (prazo: AAAA-MM-DD)"; tópico e prazo são opcionais
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


def salvar_anotacao(pasta: Path, texto: str, agora: datetime | None = None) -> str:
    texto = texto.strip()
    if not texto:
        raise ValueError("A anotação está vazia.")
    agora = agora or datetime.now()
    arquivo = pasta / "notas" / f"{agora:%Y-%m-%d}.md"
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    novo = not arquivo.exists()
    with arquivo.open("a", encoding="utf-8") as f:
        if novo:
            f.write(f"# Notas de {agora:%d/%m/%Y}\n\n")
        f.write(f"- {agora:%H:%M} {texto.replace(chr(10), chr(10) + '  ')}\n")
    return f"Anotado no bloco de notas de {agora:%d/%m/%Y}."


def _nota_do_dia(pasta: Path, dia: date) -> Path:
    return pasta / "notas" / f"{dia:%Y-%m-%d}.md"


def dias_com_nota(pasta: Path) -> list[date]:
    """Dias que têm página no bloco, do mais recente para o mais antigo."""
    dias = []
    for arq in (pasta / "notas").glob("*.md") if (pasta / "notas").is_dir() else []:
        try:
            dias.append(date.fromisoformat(arq.stem))
        except ValueError:
            continue
    return sorted(dias, reverse=True)


def ler_dia(pasta: Path, dia: date) -> str:
    arq = _nota_do_dia(pasta, dia)
    return arq.read_text(encoding="utf-8", errors="replace") if arq.exists() else ""


def salvar_dia(pasta: Path, dia: date, texto: str, base: str | None = None) -> str:
    """Grava a página inteira do dia (a que o dono editou) e devolve o texto que ficou no arquivo.

    `base` é o que a página tinha quando foi carregada ou salva pela última vez. Se o arquivo cresceu desde então (a Eco anotou
    por voz enquanto o dono digitava), o que ela acrescentou é mantido no fim. Página vazia apaga o arquivo do dia. O cabeçalho
    "# Notas de dd/mm/aaaa" é recolocado se faltar."""
    texto = texto.replace("\r\n", "\n").strip("\n")
    arq = _nota_do_dia(pasta, dia)
    if base is not None:
        atual = ler_dia(pasta, dia)
        if atual != base and atual.startswith(base):
            texto = texto + "\n" + atual[len(base) :].strip("\n")
    if not texto.strip():
        arq.unlink(missing_ok=True)
        return ""
    if not texto.lstrip().startswith("#"):
        texto = f"# Notas de {dia:%d/%m/%Y}\n\n" + texto
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(texto + "\n", encoding="utf-8")
    return ler_dia(pasta, dia)


def _metas(pasta: Path) -> Path:
    return pasta / "metas.md"


_META = re.compile(r"^- \[( |x)\] (.+?)(?: \(tópico: ([^)]+)\))?(?: \(prazo: (\d{4}-\d{2}-\d{2})\))?\s*$")


@dataclass(frozen=True)
class Meta:
    titulo: str
    topico: str = ""  # Leitura, Investimentos, Estudos... vazio se o dono ainda não disse
    prazo: date | None = None
    concluida: bool = False


def _meta_da_linha(linha: str) -> Meta | None:
    m = _META.match(linha)
    if not m:
        return None
    try:
        prazo = date.fromisoformat(m.group(4)) if m.group(4) else None
    except ValueError:
        prazo = None
    return Meta(m.group(2).strip(), (m.group(3) or "").strip(), prazo, m.group(1) == "x")


def _linha_da_meta(m: Meta) -> str:
    return (
        f"- [{'x' if m.concluida else ' '}] {m.titulo}"
        + (f" (tópico: {m.topico})" if m.topico else "")
        + (f" (prazo: {m.prazo:%Y-%m-%d})" if m.prazo else "")
    )


def metas(pasta: Path) -> list[Meta]:
    """Todas as metas (abertas e concluídas), na ordem do arquivo."""
    arquivo = _metas(pasta)
    if not arquivo.exists():
        return []
    return [m for l in arquivo.read_text(encoding="utf-8").splitlines() if (m := _meta_da_linha(l))]


def _limpar_topico(topico: str | None) -> str:
    return " ".join((topico or "").replace("(", " ").replace(")", " ").split())


def definir_meta(pasta: Path, titulo: str, prazo: str | None = None, topico: str | None = None) -> str:
    titulo = titulo.strip()
    if not titulo:
        raise ValueError("A meta está sem título.")
    if prazo:
        date.fromisoformat(prazo)  # levanta ValueError se não for AAAA-MM-DD
    topico = _limpar_topico(topico)
    arquivo = _metas(pasta)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    novo = not arquivo.exists()
    with arquivo.open("a", encoding="utf-8") as f:
        if novo:
            f.write("# Metas\n\n")
        f.write(_linha_da_meta(Meta(titulo, topico, date.fromisoformat(prazo) if prazo else None)) + "\n")
    return f"Meta registrada: {titulo}" + (f", tópico {topico}" if topico else "") + (f", prazo {prazo}." if prazo else ".")


def listar_metas(pasta: Path) -> str:
    """Metas abertas, uma por linha: 'título [Tópico] (prazo: AAAA-MM-DD)'. Sem [..] = sem tópico ainda."""
    abertas = [m for m in metas(pasta) if not m.concluida]
    if not abertas:
        return "Nenhuma meta aberta."
    return "\n".join(m.titulo + (f" [{m.topico}]" if m.topico else "") + (f" (prazo: {m.prazo:%Y-%m-%d})" if m.prazo else "") for m in abertas)


def metas_com_prazo(pasta: Path) -> list[tuple[str, date]]:
    """Metas abertas que têm prazo: [(título, data)]."""
    return [(m.titulo, m.prazo) for m in metas(pasta) if not m.concluida and m.prazo]


def progresso_por_topico(pasta: Path) -> dict[str, tuple[int, int]]:
    """{tópico: (concluídas, total)}; metas sem tópico entram em 'Sem tópico'. Base da revisão semanal e do painel."""
    saida: dict[str, list[int]] = {}
    for m in metas(pasta):
        par = saida.setdefault(m.topico or "Sem tópico", [0, 0])
        par[1] += 1
        par[0] += 1 if m.concluida else 0
    return {k: (v[0], v[1]) for k, v in saida.items()}


def definir_topico_meta(pasta: Path, trecho: str, topico: str) -> str:
    """Põe (ou troca) o tópico de UMA meta aberta, achada por um trecho do título."""
    topico = _limpar_topico(topico)
    if not topico:
        raise ValueError("Preciso do nome do tópico.")
    arquivo = _metas(pasta)
    if not arquivo.exists():
        raise ValueError("Ainda não há metas registradas.")
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    alvo = trecho.strip().lower()
    achadas = [i for i, l in enumerate(linhas) if (m := _meta_da_linha(l)) and not m.concluida and alvo and alvo in m.titulo.lower()]
    if not achadas:
        raise ValueError(f"Não encontrei meta aberta com '{trecho}'.")
    if len(achadas) > 1:
        raise ValueError(f"Mais de uma meta combina com '{trecho}'. Seja mais específico.")
    meta = _meta_da_linha(linhas[achadas[0]])
    assert meta is not None
    linhas[achadas[0]] = _linha_da_meta(Meta(meta.titulo, topico, meta.prazo, False))
    arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return f"Meta '{meta.titulo}' agora está no tópico {topico}."


def concluir_meta(pasta: Path, trecho: str) -> str:
    arquivo = _metas(pasta)
    if not arquivo.exists():
        raise ValueError("Ainda não há metas registradas.")
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    alvo = trecho.strip().lower()
    achadas = [i for i, l in enumerate(linhas) if l.startswith("- [ ] ") and alvo in l.lower()]
    if not achadas:
        raise ValueError(f"Não encontrei meta aberta com '{trecho}'.")
    if len(achadas) > 1:
        raise ValueError(f"Mais de uma meta combina com '{trecho}'. Seja mais específico.")
    linhas[achadas[0]] = "- [x] " + linhas[achadas[0]][6:]
    arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return "Meta marcada como concluída."


def marcar_meta(pasta: Path, titulo: str, feita: bool = True) -> None:
    """Marca como concluída (ou reabre) a meta com exatamente esse título. O painel usa; por voz vale `concluir_meta`."""
    arquivo = _metas(pasta)
    linhas = arquivo.read_text(encoding="utf-8").splitlines() if arquivo.exists() else []
    alvo = " ".join(titulo.split()).lower()
    for i, l in enumerate(linhas):
        m = _meta_da_linha(l)
        if m and m.concluida != feita and " ".join(m.titulo.split()).lower() == alvo:
            linhas[i] = _linha_da_meta(Meta(m.titulo, m.topico, m.prazo, feita))
            arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
            return
    raise ValueError(f"Não encontrei a meta '{titulo}' (o arquivo pode ter sido editado à mão).")
