"""Armazenamento local do chat em SQLite (espaços, conversas, mensagens, arquivos de contexto).

Por que SQLite e não JSON: são dados ligados entre si (conversa -> mensagens, espaço -> conversas), o histórico
cresce, a busca por palavra em todas as conversas é nativa (FTS5, ignorando acentos) e mover uma conversa de
espaço é trocar uma coluna, sem copiar mensagens. Os ids são texto (uuid) e as datas ISO em UTC, prontos para
ligações futuras (o grafo de ideias vai referenciar estes ids).
"""

from __future__ import annotations

import re
import sqlite3
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

GERAL = None  # "área geral": conversas sem espaço
TODOS = "*"  # só para busca: procurar em todos os espaços e na área geral
COR_PADRAO = "#7ED9AB"  # = tema.CORES_ESPACOS[0]; espaços antigos mantêm a cor que já tinham
TITULO_PADRAO = "Nova conversa"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS espacos (
    id TEXT PRIMARY KEY, nome TEXT NOT NULL, cor TEXT NOT NULL,
    instrucoes TEXT NOT NULL DEFAULT '', criado_em TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS arquivos_contexto (
    id TEXT PRIMARY KEY, espaco_id TEXT NOT NULL REFERENCES espacos(id) ON DELETE CASCADE,
    nome TEXT NOT NULL, conteudo TEXT NOT NULL, criado_em TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversas (
    id TEXT PRIMARY KEY, espaco_id TEXT REFERENCES espacos(id) ON DELETE SET NULL,
    titulo TEXT NOT NULL, criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mensagens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversa_id TEXT NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    papel TEXT NOT NULL CHECK (papel IN ('user', 'assistant')),
    conteudo TEXT NOT NULL, interrompida INTEGER NOT NULL DEFAULT 0, criado_em TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_conversas_espaco ON conversas(espaco_id, atualizado_em);
CREATE INDEX IF NOT EXISTS ix_mensagens_conversa ON mensagens(conversa_id, id);
CREATE VIRTUAL TABLE IF NOT EXISTS mensagens_fts USING fts5(
    conteudo, content='mensagens', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS mensagens_ai AFTER INSERT ON mensagens BEGIN
    INSERT INTO mensagens_fts(rowid, conteudo) VALUES (new.id, new.conteudo); END;
CREATE TRIGGER IF NOT EXISTS mensagens_ad AFTER DELETE ON mensagens BEGIN
    INSERT INTO mensagens_fts(mensagens_fts, rowid, conteudo) VALUES ('delete', old.id, old.conteudo); END;
CREATE TRIGGER IF NOT EXISTS mensagens_au AFTER UPDATE ON mensagens BEGIN
    INSERT INTO mensagens_fts(mensagens_fts, rowid, conteudo) VALUES ('delete', old.id, old.conteudo);
    INSERT INTO mensagens_fts(rowid, conteudo) VALUES (new.id, new.conteudo); END;
"""


@dataclass(frozen=True)
class Espaco:
    id: str
    nome: str
    cor: str
    instrucoes: str
    criado_em: str


@dataclass(frozen=True)
class Arquivo:
    id: str
    espaco_id: str
    nome: str
    conteudo: str
    criado_em: str


@dataclass(frozen=True)
class Conversa:
    id: str
    espaco_id: str | None
    titulo: str
    criado_em: str
    atualizado_em: str


@dataclass(frozen=True)
class Mensagem:
    id: int
    conversa_id: str
    papel: str
    conteudo: str
    interrompida: bool
    criado_em: str


@dataclass(frozen=True)
class Resultado:
    conversa_id: str
    espaco_id: str | None
    titulo: str
    trecho: str  # pedaço da mensagem com a palavra entre [colchetes]; vazio se achou só pelo título


def _sem_acento(texto: str) -> str:
    base = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in base if unicodedata.category(c) != "Mn")


def _agora_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Banco:
    def __init__(self, arquivo: Path | str, relogio: Callable[[], str] = _agora_utc) -> None:
        self._relogio = relogio
        self.arquivo = None if str(arquivo) == ":memory:" else Path(arquivo)  # para o backup antes de operações grandes
        self._lock = threading.RLock()  # o chat gera a resposta numa thread; uma conexão só, protegida
        if str(arquivo) != ":memory:":
            Path(arquivo).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(arquivo), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys = ON")
        with self._lock, self._con:
            self._con.executescript(ESQUEMA)

    def fechar(self) -> None:
        with self._lock:
            self._con.close()

    def _consultar(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._con.execute(sql, args).fetchall()

    def _executar(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock, self._con:
            return self._con.execute(sql, args)

    # --- espaços ------------------------------------------------------------------------------------
    def criar_espaco(self, nome: str, cor: str = COR_PADRAO, instrucoes: str = "") -> Espaco:
        nome = nome.strip()
        if not nome:
            raise ValueError("O espaço precisa de um nome.")
        if any(_sem_acento(e.nome) == _sem_acento(nome) for e in self.listar_espacos()):
            raise ValueError(f"Já existe um espaço chamado '{nome}'.")
        id_ = uuid.uuid4().hex
        self._executar(
            "INSERT INTO espacos (id, nome, cor, instrucoes, criado_em) VALUES (?, ?, ?, ?, ?)",
            (id_, nome, cor, instrucoes.strip(), self._relogio()),
        )
        return self.espaco(id_)

    def espaco(self, id_: str) -> Espaco:
        linhas = self._consultar("SELECT * FROM espacos WHERE id = ?", (id_,))
        if not linhas:
            raise KeyError(f"Espaço inexistente: {id_}")
        return Espaco(**dict(linhas[0]))

    def listar_espacos(self) -> list[Espaco]:
        return [Espaco(**dict(l)) for l in self._consultar("SELECT * FROM espacos ORDER BY nome COLLATE NOCASE")]

    def atualizar_espaco(self, id_: str, nome: str | None = None, cor: str | None = None, instrucoes: str | None = None) -> Espaco:
        atual = self.espaco(id_)
        novo_nome = (nome if nome is not None else atual.nome).strip()
        if not novo_nome:
            raise ValueError("O espaço precisa de um nome.")
        if any(e.id != id_ and _sem_acento(e.nome) == _sem_acento(novo_nome) for e in self.listar_espacos()):
            raise ValueError(f"Já existe um espaço chamado '{novo_nome}'.")
        self._executar(
            "UPDATE espacos SET nome = ?, cor = ?, instrucoes = ? WHERE id = ?",
            (novo_nome, cor or atual.cor, (atual.instrucoes if instrucoes is None else instrucoes.strip()), id_),
        )
        return self.espaco(id_)

    def excluir_espaco(self, id_: str) -> None:
        """Apaga o espaço e os arquivos de contexto dele. As conversas NÃO se perdem: voltam para a área geral."""
        self._executar("DELETE FROM espacos WHERE id = ?", (id_,))

    # --- arquivos de contexto -----------------------------------------------------------------------
    def adicionar_arquivo(self, espaco_id: str, nome: str, conteudo: str) -> Arquivo:
        self.espaco(espaco_id)
        id_ = uuid.uuid4().hex
        self._executar(
            "INSERT INTO arquivos_contexto (id, espaco_id, nome, conteudo, criado_em) VALUES (?, ?, ?, ?, ?)",
            (id_, espaco_id, nome, conteudo, self._relogio()),
        )
        return next(a for a in self.listar_arquivos(espaco_id) if a.id == id_)

    def listar_arquivos(self, espaco_id: str) -> list[Arquivo]:
        linhas = self._consultar("SELECT * FROM arquivos_contexto WHERE espaco_id = ? ORDER BY criado_em, nome", (espaco_id,))
        return [Arquivo(**dict(l)) for l in linhas]

    def remover_arquivo(self, arquivo_id: str) -> None:
        self._executar("DELETE FROM arquivos_contexto WHERE id = ?", (arquivo_id,))

    # --- conversas ----------------------------------------------------------------------------------
    def criar_conversa(self, espaco_id: str | None = GERAL, titulo: str = TITULO_PADRAO) -> Conversa:
        if espaco_id is not None:
            self.espaco(espaco_id)
        id_, agora = uuid.uuid4().hex, self._relogio()
        self._executar(
            "INSERT INTO conversas (id, espaco_id, titulo, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?)",
            (id_, espaco_id, titulo, agora, agora),
        )
        return self.conversa(id_)

    def conversa(self, id_: str) -> Conversa:
        linhas = self._consultar("SELECT * FROM conversas WHERE id = ?", (id_,))
        if not linhas:
            raise KeyError(f"Conversa inexistente: {id_}")
        return Conversa(**dict(linhas[0]))

    def listar_conversas(self, espaco_id: str | None = GERAL) -> list[Conversa]:
        """Conversas de um espaço (ou da área geral, com `None`), da mais recente para a mais antiga."""
        if espaco_id is None:
            linhas = self._consultar("SELECT * FROM conversas WHERE espaco_id IS NULL ORDER BY atualizado_em DESC, rowid DESC")
        else:
            linhas = self._consultar(
                "SELECT * FROM conversas WHERE espaco_id = ? ORDER BY atualizado_em DESC, rowid DESC", (espaco_id,)
            )
        return [Conversa(**dict(l)) for l in linhas]

    def renomear_conversa(self, id_: str, titulo: str) -> None:
        titulo = titulo.strip()
        if not titulo:
            raise ValueError("O título não pode ficar vazio.")
        self._executar("UPDATE conversas SET titulo = ? WHERE id = ?", (titulo, id_))

    def mover_conversa(self, conversa_id: str, espaco_id: str | None) -> Conversa:
        """Muda a conversa de espaço (ou para a área geral). As mensagens ficam intactas: só muda a coluna."""
        if espaco_id is not None:
            self.espaco(espaco_id)
        self.conversa(conversa_id)
        self._executar("UPDATE conversas SET espaco_id = ? WHERE id = ?", (espaco_id, conversa_id))
        return self.conversa(conversa_id)

    def excluir_conversa(self, id_: str) -> None:
        self._executar("DELETE FROM conversas WHERE id = ?", (id_,))

    # --- mensagens ----------------------------------------------------------------------------------
    def adicionar_mensagem(self, conversa_id: str, papel: str, conteudo: str, interrompida: bool = False) -> Mensagem:
        self.conversa(conversa_id)
        agora = self._relogio()
        with self._lock, self._con:
            cur = self._con.execute(
                "INSERT INTO mensagens (conversa_id, papel, conteudo, interrompida, criado_em) VALUES (?, ?, ?, ?, ?)",
                (conversa_id, papel, conteudo, int(interrompida), agora),
            )
            self._con.execute("UPDATE conversas SET atualizado_em = ? WHERE id = ?", (agora, conversa_id))
            return Mensagem(cur.lastrowid, conversa_id, papel, conteudo, interrompida, agora)

    def mensagens(self, conversa_id: str) -> list[Mensagem]:
        linhas = self._consultar("SELECT * FROM mensagens WHERE conversa_id = ? ORDER BY id", (conversa_id,))
        return [Mensagem(l["id"], l["conversa_id"], l["papel"], l["conteudo"], bool(l["interrompida"]), l["criado_em"]) for l in linhas]

    # --- busca --------------------------------------------------------------------------------------
    def buscar(self, texto: str, espaco_id: str | None = TODOS, limite: int = 40) -> list[Resultado]:
        """Procura por palavra (início de palavra, sem diferenciar acento nem maiúscula) nas mensagens e nos títulos.
        `espaco_id`: TODOS (padrão), None (área geral) ou o id de um espaço. Uma linha por conversa."""
        palavras = re.findall(r"\w+", texto, re.UNICODE)
        if not palavras:
            return []
        filtro, args = "", ()
        if espaco_id is None:
            filtro = "AND c.espaco_id IS NULL"
        elif espaco_id != TODOS:
            filtro, args = "AND c.espaco_id = ?", (espaco_id,)
        achados: dict[str, Resultado] = {}
        alvo = [_sem_acento(p) for p in palavras]
        escopo = (
            self.listar_conversas(None) + [c for e in self.listar_espacos() for c in self.listar_conversas(e.id)]
            if espaco_id == TODOS
            else self.listar_conversas(espaco_id)
        )
        for c in escopo:  # títulos primeiro
            if all(p in _sem_acento(c.titulo) for p in alvo):
                achados[c.id] = Resultado(c.id, c.espaco_id, c.titulo, "")
        consulta = " ".join(f'"{p}"*' for p in palavras)
        linhas = self._consultar(
            f"""SELECT c.id AS cid, c.espaco_id AS eid, c.titulo AS titulo,
                       snippet(mensagens_fts, 0, '[', ']', '…', 12) AS trecho
                FROM mensagens_fts
                JOIN mensagens m ON m.id = mensagens_fts.rowid
                JOIN conversas c ON c.id = m.conversa_id
                WHERE mensagens_fts MATCH ? {filtro}
                ORDER BY rank LIMIT ?""",
            (consulta, *args, limite),
        )
        for l in linhas:
            if l["cid"] not in achados:
                achados[l["cid"]] = Resultado(l["cid"], l["eid"], l["titulo"], l["trecho"])
            elif not achados[l["cid"]].trecho:
                achados[l["cid"]] = Resultado(l["cid"], l["eid"], l["titulo"], l["trecho"])
        return list(achados.values())[:limite]
