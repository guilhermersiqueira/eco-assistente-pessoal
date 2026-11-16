"""Estado da importação de conversas, guardado no banco da Eco (M2).

Três tabelas novas, só acrescentadas (nada que já existe é alterado, então não há migração):
- `importacoes`: uma linha por arquivo de export importado (nome, hash do arquivo, data, quantas conversas, custo);
- `importacao_conversas`: uma linha por conversa do claude.ai, com o hash do texto e o estado da análise. É o que permite
  retomar depois de uma falha e importar de novo só o que é novo ou mudou;
- `importacao_propostas`: os fatos que o extrator propôs, cada um ligado à conversa de origem. A chave do texto é única:
  um fato que o dono rejeitou (ou que já existe como proposta) não volta, mesmo vindo de outra conversa.

Estados da conversa: pendente, analisada, sem_fatos, ignorada (curta) e erro. Da proposta: pendente, aprovada, rejeitada
e duplicada (já existia na memória).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from eco.chat.armazenamento import Banco, _sem_acento
from eco.importacao.modelo import Conversa

ESQUEMA = """
CREATE TABLE IF NOT EXISTS importacoes (
    id TEXT PRIMARY KEY, arquivo TEXT NOT NULL, hash TEXT NOT NULL, criado_em TEXT NOT NULL,
    conversas INTEGER NOT NULL DEFAULT 0, custo REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS importacao_conversas (
    uuid TEXT PRIMARY KEY, titulo TEXT NOT NULL, criada_em TEXT NOT NULL DEFAULT '', atualizada_em TEXT NOT NULL DEFAULT '',
    hash TEXT NOT NULL, estado TEXT NOT NULL DEFAULT 'pendente', custo REAL NOT NULL DEFAULT 0, erro TEXT NOT NULL DEFAULT '',
    importacao_id TEXT REFERENCES importacoes(id) ON DELETE SET NULL, registrada_em TEXT NOT NULL, analisada_em TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS importacao_propostas (
    id TEXT PRIMARY KEY, chave TEXT NOT NULL UNIQUE,
    conversa_uuid TEXT NOT NULL REFERENCES importacao_conversas(uuid) ON DELETE CASCADE,
    texto TEXT NOT NULL, trecho TEXT NOT NULL DEFAULT '', estado TEXT NOT NULL DEFAULT 'pendente', motivo TEXT NOT NULL DEFAULT '',
    ideia_id TEXT NOT NULL DEFAULT '', criado_em TEXT NOT NULL, decidido_em TEXT NOT NULL DEFAULT '',
    conferida_em TEXT NOT NULL DEFAULT '', conflito_ideia_id TEXT NOT NULL DEFAULT '', conflito_texto TEXT NOT NULL DEFAULT '',
    conflito_relacao TEXT NOT NULL DEFAULT '', conflito_motivo TEXT NOT NULL DEFAULT '',
    duracao TEXT NOT NULL DEFAULT '', validade TEXT NOT NULL DEFAULT '');
"""
# Colunas que chegaram depois (conferência da M5; duração e validade da M4b), acrescentadas se a tabela já existia sem elas.
COLUNAS_PROPOSTAS = ("conferida_em", "conflito_ideia_id", "conflito_texto", "conflito_relacao", "conflito_motivo", "duracao", "validade")
ESTADOS_CONVERSA = ("pendente", "analisada", "sem_fatos", "ignorada", "erro")
FEITAS = ("analisada", "sem_fatos", "ignorada")  # não precisam ser analisadas de novo (se o texto não mudar)
ESTADOS_PROPOSTA = ("pendente", "aprovada", "rejeitada", "duplicada")


def chave_do_fato(texto: str) -> str:
    """Igual para o mesmo fato escrito com outras maiúsculas, acentos, espaços ou ponto final."""
    base = " ".join(_sem_acento(texto).split()).strip(" .")
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Registro:
    uuid: str
    titulo: str
    criada_em: str
    atualizada_em: str
    hash: str
    estado: str
    custo: float
    erro: str


@dataclass(frozen=True)
class Proposta:
    id: str
    conversa_uuid: str
    conversa_titulo: str
    conversa_data: str  # quando a conversa começou (vira a data da lembrança)
    texto: str
    trecho: str  # a frase do dono que justifica o fato
    estado: str
    motivo: str
    ideia_id: str  # a lembrança criada (aprovada) ou a que já existia e é igual (duplicada)
    criado_em: str
    decidido_em: str
    conferida_em: str = ""  # quando a conferência com a memória rodou (vazio: ainda não)
    conflito_ideia_id: str = ""  # a lembrança que esta proposta contradiz ou atualiza
    conflito_texto: str = ""  # o texto dela na hora da conferência, para mostrar lado a lado
    conflito_relacao: str = ""  # "contradiz" ou "atualiza"
    conflito_motivo: str = ""
    duracao: str = ""  # permanente, duradoura ou temporaria (vazio: o extrator não disse); vai para a lembrança ao aprovar
    validade: str = ""  # AAAA-MM-DD, só em fato temporário com data limite

    @property
    def com_conflito(self) -> bool:
        return bool(self.conflito_ideia_id)


class Importacoes:
    def __init__(self, banco: Banco) -> None:
        self.banco = banco
        with banco._lock, banco._con:
            banco._con.executescript(ESQUEMA)
        tem = {l["name"] for l in banco._consultar("PRAGMA table_info(importacao_propostas)")}
        for coluna in COLUNAS_PROPOSTAS:
            if coluna not in tem:
                banco._executar(f"ALTER TABLE importacao_propostas ADD COLUMN {coluna} TEXT NOT NULL DEFAULT ''")

    # --- importações (um arquivo de export) ------------------------------------------------------------------------
    def registrar_importacao(self, arquivo: str, hash_do_arquivo: str, conversas: int = 0) -> str:
        id_ = uuid.uuid4().hex
        self.banco._executar("INSERT INTO importacoes (id, arquivo, hash, criado_em, conversas) VALUES (?,?,?,?,?)",
                             (id_, arquivo, hash_do_arquivo, self.banco._relogio(), conversas))
        return id_

    def importacoes(self) -> list[dict]:
        return [dict(l) for l in self.banco._consultar("SELECT * FROM importacoes ORDER BY criado_em DESC, rowid DESC")]

    # --- conversas -------------------------------------------------------------------------------------------------
    def _linha(self, uuid_: str):
        achadas = self.banco._consultar("SELECT * FROM importacao_conversas WHERE uuid = ?", (uuid_,))
        return achadas[0] if achadas else None

    def situacao(self, uuid_: str, hash_: str) -> str:
        """nova (nunca vista), mudou (o texto mudou desde a última vez), feita (já tratada) ou retomar (ficou pendente ou
        deu erro). Só consulta, não grava."""
        l = self._linha(uuid_)
        if l is None:
            return "nova"
        if l["hash"] != hash_:
            return "mudou"
        return "feita" if l["estado"] in FEITAS else "retomar"

    def registrar_conversa(self, c: Conversa, importacao_id: str = "", estado: str = "pendente") -> str:
        """Grava a conversa no estado dado se for nova ou tiver mudado; se já foi vista com o mesmo texto, não mexe (a
        análise continua de onde parou). Devolve a situação de antes da gravação."""
        if estado not in ESTADOS_CONVERSA:
            raise ValueError(f"Estado de conversa desconhecido: {estado}")
        antes = self.situacao(c.uuid, c.hash)
        agora = self.banco._relogio()
        if antes == "nova":
            self.banco._executar(
                "INSERT INTO importacao_conversas (uuid, titulo, criada_em, atualizada_em, hash, estado, importacao_id, registrada_em,"
                " analisada_em) VALUES (?,?,?,?,?,?,?,?,?)",
                (c.uuid, c.titulo, c.criada_em, c.atualizada_em, c.hash, estado, importacao_id or None, agora,
                 agora if estado in FEITAS else ""))
        elif antes == "mudou":
            self.banco._executar(
                "UPDATE importacao_conversas SET titulo = ?, criada_em = ?, atualizada_em = ?, hash = ?, estado = ?, erro = '',"
                " importacao_id = ?, analisada_em = ? WHERE uuid = ?",
                (c.titulo, c.criada_em, c.atualizada_em, c.hash, estado, importacao_id or None,
                 agora if estado in FEITAS else "", c.uuid))
        return antes

    def definir_estado(self, uuid_: str, estado: str, custo: float = 0.0, erro: str = "") -> None:
        """Muda o estado da análise e soma o custo desta tentativa ao da conversa."""
        if estado not in ESTADOS_CONVERSA:
            raise ValueError(f"Estado de conversa desconhecido: {estado}")
        if self._linha(uuid_) is None:
            raise ValueError("Essa conversa não está registrada na importação.")
        self.banco._executar(
            "UPDATE importacao_conversas SET estado = ?, custo = custo + ?, erro = ?, analisada_em = ? WHERE uuid = ?",
            (estado, custo, erro[:300], self.banco._relogio() if estado in FEITAS else "", uuid_))

    def conversa(self, uuid_: str) -> Registro | None:
        l = self._linha(uuid_)
        return self._registro(l) if l is not None else None

    @staticmethod
    def _registro(l) -> Registro:
        return Registro(l["uuid"], l["titulo"], l["criada_em"], l["atualizada_em"], l["hash"], l["estado"], l["custo"], l["erro"])

    def pendentes(self) -> list[Registro]:
        """O que falta analisar (pendente ou com erro), da conversa mais recente para a mais antiga."""
        linhas = self.banco._consultar(
            "SELECT * FROM importacao_conversas WHERE estado IN ('pendente', 'erro') ORDER BY atualizada_em DESC, rowid")
        return [self._registro(l) for l in linhas]

    def contagens(self) -> dict[str, int]:
        n = {e: 0 for e in ESTADOS_CONVERSA}
        for l in self.banco._consultar("SELECT estado, COUNT(*) AS n FROM importacao_conversas GROUP BY estado"):
            n[l["estado"]] = l["n"]
        return n

    def custo_total(self) -> float:
        return float(self.banco._consultar("SELECT COALESCE(SUM(custo), 0) AS c FROM importacao_conversas")[0]["c"])

    # --- propostas -------------------------------------------------------------------------------------------------
    def propor(self, conversa_uuid: str, texto: str, trecho: str = "", duracao: str = "", validade: str = "") -> str | None:
        """Guarda um fato proposto. Devolve o id, ou None se o mesmo fato já foi proposto (em qualquer estado: o que o
        dono rejeitou não volta). Duração ou validade inválidas são guardadas vazias."""
        from datetime import date

        from eco.grafo.dados import DURABILIDADES

        texto = " ".join((texto or "").split())
        if not texto:
            raise ValueError("O fato proposto está vazio.")
        if self._linha(conversa_uuid) is None:
            raise ValueError("Essa conversa não está registrada na importação.")
        duracao = duracao if duracao in DURABILIDADES else ""
        try:
            validade = date.fromisoformat(validade).isoformat() if validade else ""
        except ValueError:
            validade = ""
        id_ = uuid.uuid4().hex
        c = self.banco._executar(
            "INSERT OR IGNORE INTO importacao_propostas (id, chave, conversa_uuid, texto, trecho, criado_em, duracao, validade)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (id_, chave_do_fato(texto), conversa_uuid, texto, " ".join((trecho or "").split())[:400], self.banco._relogio(),
             duracao, validade))
        return id_ if c.rowcount else None

    _SELECAO = ("SELECT p.*, c.titulo AS titulo, c.criada_em AS criada_em_conversa FROM importacao_propostas p "
                "JOIN importacao_conversas c ON c.uuid = p.conversa_uuid")

    def propostas(self, estado: str = "pendente") -> list[Proposta]:
        linhas = self.banco._consultar(self._SELECAO + " WHERE p.estado = ? ORDER BY c.criada_em DESC, p.rowid", (estado,))
        return [self._da_linha(l) for l in linhas]

    def proposta(self, id_: str) -> Proposta | None:
        linhas = self.banco._consultar(self._SELECAO + " WHERE p.id = ?", (id_,))
        return self._da_linha(linhas[0]) if linhas else None

    def _da_linha(self, l) -> Proposta:
        return Proposta(l["id"], l["conversa_uuid"], l["titulo"], l["criada_em_conversa"], l["texto"], l["trecho"], l["estado"],
                        l["motivo"], l["ideia_id"], l["criado_em"], l["decidido_em"], l["conferida_em"], l["conflito_ideia_id"],
                        l["conflito_texto"], l["conflito_relacao"], l["conflito_motivo"], l["duracao"], l["validade"])

    def sem_conferencia(self) -> list[Proposta]:
        """As propostas pendentes que ainda não foram conferidas com a memória, na ordem em que apareceram."""
        linhas = self.banco._consultar(self._SELECAO + " WHERE p.estado = 'pendente' AND p.conferida_em = '' ORDER BY p.rowid")
        return [self._da_linha(l) for l in linhas]

    def marcar_conferida(self, id_: str, ideia_id: str = "", texto: str = "", relacao: str = "", motivo: str = "") -> None:
        """Registra que a conferência rodou. Com `ideia_id`, anota o conflito (a lembrança que esta proposta contradiz ou
        atualiza) para a revisão mostrar os dois lado a lado."""
        c = self.banco._executar(
            "UPDATE importacao_propostas SET conferida_em = ?, conflito_ideia_id = ?, conflito_texto = ?, conflito_relacao = ?,"
            " conflito_motivo = ? WHERE id = ?", (self.banco._relogio(), ideia_id, texto[:400], relacao, motivo[:300], id_))
        if not c.rowcount:
            raise ValueError("Não achei essa proposta.")

    def decidir(self, id_: str, estado: str, motivo: str = "", ideia_id: str = "") -> None:
        """Aprovada, rejeitada ou duplicada. `ideia_id` é a lembrança criada ao aprovar."""
        if estado not in ESTADOS_PROPOSTA or estado == "pendente":
            raise ValueError(f"Decisão desconhecida: {estado}")
        c = self.banco._executar("UPDATE importacao_propostas SET estado = ?, motivo = ?, ideia_id = ?, decidido_em = ? WHERE id = ?",
                                 (estado, motivo[:300], ideia_id, self.banco._relogio(), id_))
        if not c.rowcount:
            raise ValueError("Não achei essa proposta.")

    def origem_da_ideia(self, ideia_id: str) -> Proposta | None:
        """A proposta aprovada que virou esta lembrança (para dizer ao dono de qual conversa ela veio), se houver."""
        linhas = self.banco._consultar(self._SELECAO + " WHERE p.ideia_id = ? AND p.estado = 'aprovada' ORDER BY p.rowid DESC LIMIT 1",
                                       (ideia_id,))
        return self._da_linha(linhas[0]) if linhas else None

    def editar_texto(self, id_: str, novo_texto: str) -> None:
        """O dono reescreve o fato antes de aprovar. Só vale para proposta pendente."""
        novo_texto = " ".join((novo_texto or "").split())
        if not novo_texto:
            raise ValueError("O texto novo está vazio.")
        p = self.proposta(id_)
        if p is None:
            raise ValueError("Não achei essa proposta.")
        if p.estado != "pendente":
            raise ValueError("Só dá para editar uma proposta que ainda está pendente.")
        outra = self.banco._consultar("SELECT id FROM importacao_propostas WHERE chave = ? AND id != ?", (chave_do_fato(novo_texto), id_))
        if outra:
            raise ValueError("Já existe uma proposta com esse mesmo texto.")
        # texto novo, conferência nova: o resultado da conferência anterior não vale mais
        self.banco._executar(
            "UPDATE importacao_propostas SET texto = ?, chave = ?, conferida_em = '', conflito_ideia_id = '', conflito_texto = '',"
            " conflito_relacao = '', conflito_motivo = '' WHERE id = ?", (novo_texto, chave_do_fato(novo_texto), id_))
