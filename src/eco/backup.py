"""Backup do banco da Eco (`~/.eco/eco.db`: conversas, espaços e grafo) e conferência da restauração.

- `fazer_backup` copia o banco pela API de backup do SQLite: a cópia sai consistente mesmo com a Eco aberta escrevendo.
- `conferir` compara a cópia com o original tabela por tabela (quantidade de linhas e um resumo do conteúdo) e roda o
  `integrity_check` do SQLite na cópia.
- `restaurar` grava a cópia num destino. Por segurança, nunca sobrescreve um arquivo que já existe: para voltar o banco
  da Eco, feche a Eco e restaure num caminho novo, ou apague o atual de propósito antes.

O original nunca é alterado por este módulo. Linha de comando: `eco backup` (faz, confere e testa a restauração) e
`eco backup registro` (as últimas execuções).

Backup automático (requisito 19.6.13 do dono): `backup_automatico` faz uma cópia quando a última automática tem mais de
`ECO_BACKUP_HORAS` (padrão 24 h), confere a integridade e guarda só as `ECO_BACKUP_MANTER` (padrão 14) automáticas mais
novas; backups manuais e os de antes de uma operação (`antes_de`) nunca são apagados. Cada execução fica registrada em
`backups/registro.jsonl`.
"""

from __future__ import annotations

import hashlib
import sqlite3
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from eco.config import PASTA_ECO

BANCO = PASTA_ECO / "eco.db"
PASTA_BACKUPS = PASTA_ECO / "backups"


@dataclass
class Conferencia:
    ok: bool
    tabelas: dict[str, int] = field(default_factory=dict)  # tabela: linhas (da cópia)
    problemas: list[str] = field(default_factory=list)


def _abrir_leitura(arquivo: Path) -> sqlite3.Connection:
    """Conexão só de leitura (o arquivo não é tocado nem criado se faltar)."""
    if not arquivo.exists():
        raise FileNotFoundError(f"Banco não encontrado: {arquivo}")
    return sqlite3.connect(f"file:{arquivo.as_posix()}?mode=ro", uri=True)


def _tabelas(con: sqlite3.Connection) -> list[str]:
    """Tabelas comuns (as internas do SQLite e as sombras do FTS5 ficam de fora: são refeitas pelo próprio índice)."""
    nomes = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    virtuais = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND sql LIKE 'CREATE VIRTUAL%'")}
    sombras = {n for n in nomes for v in virtuais if n.startswith(v + "_")}
    return [n for n in nomes if not n.startswith("sqlite_") and n not in sombras]


def _resumo(con: sqlite3.Connection, tabela: str) -> tuple[int, str]:
    """(linhas, hash do conteúdo em ordem estável)."""
    h = hashlib.sha256()
    n = 0
    for linha in con.execute(f'SELECT * FROM "{tabela}" ORDER BY 1'):  # noqa: S608 (nome vem do próprio sqlite_master)
        h.update(repr(linha).encode("utf-8"))
        n += 1
    return n, h.hexdigest()


def fazer_backup(origem: Path = BANCO, pasta: Path = PASTA_BACKUPS, motivo: str = "manual", agora: datetime | None = None) -> Path:
    """Cópia consistente do banco em `pasta/eco-AAAAMMDD-HHMMSS-motivo.db`. Devolve o caminho."""
    pasta.mkdir(parents=True, exist_ok=True)
    marca = (agora or datetime.now()).strftime("%Y%m%d-%H%M%S")
    destino = pasta / f"eco-{marca}-{motivo}.db"
    n = 1
    while destino.exists():
        n += 1
        destino = pasta / f"eco-{marca}-{motivo}-{n}.db"
    fonte = _abrir_leitura(origem)
    copia = sqlite3.connect(str(destino))
    try:
        fonte.backup(copia)
    finally:
        copia.close()
        fonte.close()
    return destino


def conferir(copia: Path, original: Path = BANCO) -> Conferencia:
    """Confere se a cópia tem exatamente as mesmas tabelas e o mesmo conteúdo do original, e se está íntegra."""
    c, o = _abrir_leitura(copia), _abrir_leitura(original)
    resultado = Conferencia(ok=True)
    try:
        integridade = c.execute("PRAGMA integrity_check").fetchone()[0]
        if integridade != "ok":
            resultado.problemas.append(f"integridade da cópia: {integridade}")
        tc, to = _tabelas(c), _tabelas(o)
        if tc != to:
            resultado.problemas.append(f"tabelas diferentes: cópia {tc}, original {to}")
        for t in to:
            if t not in tc:
                continue
            nc, hc = _resumo(c, t)
            no, ho = _resumo(o, t)
            resultado.tabelas[t] = nc
            if (nc, hc) != (no, ho):
                resultado.problemas.append(f"{t}: cópia {nc} linhas, original {no} linhas" + (" (conteúdo diferente)" if nc == no else ""))
    finally:
        c.close()
        o.close()
    resultado.ok = not resultado.problemas
    return resultado


def restaurar(copia: Path, destino: Path) -> Path:
    """Grava a cópia em `destino` (que não pode existir) usando a API de backup do SQLite."""
    if destino.exists():
        raise FileExistsError(f"{destino} já existe; restauro só em caminho novo, para não perder nada sem querer.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    fonte = _abrir_leitura(copia)
    alvo = sqlite3.connect(str(destino))
    try:
        fonte.backup(alvo)
    finally:
        alvo.close()
        fonte.close()
    return destino


def backups(pasta: Path = PASTA_BACKUPS) -> list[Path]:
    """Backups existentes, do mais novo para o mais antigo."""
    return sorted(pasta.glob("eco-*.db"), reverse=True) if pasta.exists() else []


# --- automático, antes de operações e registro ----------------------------------------------------------------------
_NOME = re.compile(r"^eco-(\d{8}-\d{6})-(.+?)(?:-\d+)?\.db$")


def _quando_e_motivo(arquivo: Path) -> tuple[datetime, str] | None:
    m = _NOME.match(arquivo.name)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%d-%H%M%S"), m.group(2)


def integridade(copia: Path) -> Conferencia:
    """Só a cópia: `integrity_check` e as tabelas legíveis (para o automático, com a Eco escrevendo no original)."""
    r = Conferencia(ok=True)
    c = _abrir_leitura(copia)
    try:
        estado = c.execute("PRAGMA integrity_check").fetchone()[0]
        if estado != "ok":
            r.problemas.append(f"integridade da cópia: {estado}")
        for t in _tabelas(c):
            r.tabelas[t] = c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]  # noqa: S608
    finally:
        c.close()
    r.ok = not r.problemas
    return r


def registrar(pasta: Path, dados: dict) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    with (pasta / "registro.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(dados, ensure_ascii=False) + "\n")


def registros(pasta: Path = PASTA_BACKUPS, ultimos: int = 20) -> list[dict]:
    arquivo = pasta / "registro.jsonl"
    if not arquivo.exists():
        return []
    linhas = []
    for texto in arquivo.read_text(encoding="utf-8").splitlines()[-ultimos:]:
        try:
            linhas.append(json.loads(texto))
        except json.JSONDecodeError:
            continue
    return linhas


def _fazer_e_registrar(origem: Path, pasta: Path, motivo: str, agora: datetime | None) -> Path:
    momento = agora or datetime.now()
    try:
        copia = fazer_backup(origem, pasta, motivo, momento)
        c = integridade(copia)
        registrar(pasta, {"quando": momento.isoformat(timespec="seconds"), "motivo": motivo, "arquivo": copia.name,
                          "ok": c.ok, "tabelas": c.tabelas, "problemas": c.problemas})
        if not c.ok:
            raise RuntimeError("O backup saiu com problema de integridade: " + "; ".join(c.problemas))
        return copia
    except Exception as erro:
        registrar(pasta, {"quando": momento.isoformat(timespec="seconds"), "motivo": motivo, "ok": False, "erro": f"{type(erro).__name__}: {erro}"})
        raise


def podar(pasta: Path, manter: int) -> list[Path]:
    """Apaga as cópias AUTOMÁTICAS mais antigas além de `manter`. Manuais e de antes de operações ficam sempre."""
    automaticas = sorted((a for a in backups(pasta) if (q := _quando_e_motivo(a)) and q[1] == "automatico"),
                         key=lambda a: _quando_e_motivo(a)[0], reverse=True)
    apagadas = automaticas[max(0, manter):]
    for a in apagadas:
        a.unlink(missing_ok=True)
    return apagadas


def backup_automatico(origem: Path = BANCO, pasta: Path = PASTA_BACKUPS, horas: float = 24, manter: int = 14,
                      agora: datetime | None = None) -> Path | None:
    """Faz a cópia se a última automática tem mais de `horas`. Devolve o caminho (ou None se não era hora)."""
    if horas <= 0 or not origem.exists():
        return None
    momento = agora or datetime.now()
    ultimas = [q[0] for a in backups(pasta) if (q := _quando_e_motivo(a)) and q[1] == "automatico"]
    if ultimas and momento - max(ultimas) < timedelta(hours=horas):
        return None
    copia = _fazer_e_registrar(origem, pasta, "automatico", momento)
    podar(pasta, manter)
    return copia


def antes_de(operacao: str, origem: Path = BANCO, pasta: Path = PASTA_BACKUPS) -> Path:
    """Cópia conferida antes de uma operação importante (importar o grafo, migrar o banco...). Nunca é podada."""
    seguro = re.sub(r"[^a-z0-9]+", "-", operacao.lower()).strip("-") or "operacao"
    return _fazer_e_registrar(origem, pasta, f"antes-{seguro}", None)


def comando_backup(argumentos: list[str] | None = None) -> int:
    """`eco backup`: faz o backup, confere com o original e testa a restauração numa pasta temporária.
    `eco backup registro`: mostra as últimas execuções."""
    import tempfile

    if argumentos and argumentos[0] == "registro":
        linhas = registros()
        if not linhas:
            print("Nenhum backup registrado ainda.")
        for l in linhas:
            estado = "OK" if l.get("ok") else "FALHOU"
            print(f"{l.get('quando')}  {l.get('motivo', '?'):22s} {estado}  {l.get('arquivo') or l.get('erro', '')}")
        return 0

    copia = fazer_backup()
    print(f"Backup: {copia}")
    c = conferir(copia)
    print("Conferência da cópia:", "OK" if c.ok else "FALHOU")
    for t, n in c.tabelas.items():
        print(f"  {t}: {n} linhas")
    for p in c.problemas:
        print("  problema:", p)
    with tempfile.TemporaryDirectory() as pasta:
        restaurado = restaurar(copia, Path(pasta) / "restaurado.db")
        r = conferir(restaurado)
        print("Teste de restauração:", "OK" if r.ok else "FALHOU")
        for p in r.problemas:
            print("  problema:", p)
    return 0 if c.ok and r.ok else 1
