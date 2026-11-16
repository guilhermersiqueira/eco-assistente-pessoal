"""Exportar e importar o grafo em JSON (requisitos 19.6.11 e 19.6.12 do dono).

Exportar: todas as linhas e colunas das tabelas do grafo (ideias, ligações, origens, dias do bloco, ideias que o dono
apagou e as marcas de análise), com a versão do formato. As colunas vêm do próprio banco, então campos novos entram
sozinhos. O arquivo vai para `<pasta de dados>/exportacoes/grafo-AAAAMMDD-HHMMSS.json`.

Importar: nada é gravado antes de
1. validar o arquivo (formato, versão, campos obrigatórios, ligações e pais que apontam para ideias que existem,
   hierarquia sem círculos);
2. comparar com o grafo atual: ideias novas, iguais, em conflito (mesmo id, conteúdo diferente) e duplicadas (id novo,
   mas o mesmo nome no mesmo espaço e origem de uma ideia que já existe: ficam de fora, para não duplicar);
3. o dono confirmar a frase com esse resumo (a ferramenta passa pela confirmação de voz ou da janela).
Por padrão só ACRESCENTA: conflitos ficam como estão. `substituir_conflitos=True` troca o conteúdo atual pelo do
arquivo, e a frase de confirmação diz isso. Antes de gravar, um backup conferido do banco (`backup.antes_de`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from eco.grafo.dados import Grafo, chave_da_ideia

FORMATO = "eco-grafo"
VERSAO = 1
# tabelas do grafo, na ordem de gravação (as que dependem de outras vêm depois)
TABELAS = ("ideias", "ligacoes", "ideia_fontes", "ideia_dias", "ideias_ignoradas", "analises")
OBRIGATORIOS = {"ideias": ("id", "nome", "origem"), "ligacoes": ("id", "a", "b")}


def exportar(grafo: Grafo, agora: datetime | None = None) -> dict[str, Any]:
    dados: dict[str, Any] = {"formato": FORMATO, "versao": VERSAO, "exportado_em": (agora or datetime.now()).isoformat(timespec="seconds")}
    for t in TABELAS:
        dados[t] = [dict(l) for l in grafo.banco._consultar(f"SELECT * FROM {t} ORDER BY rowid")]  # noqa: S608
    dados["resumo"] = {"ideias": len(dados["ideias"]), "ligacoes": len(dados["ligacoes"])}
    return dados


def exportar_para_arquivo(grafo: Grafo, pasta: Path, agora: datetime | None = None) -> Path:
    momento = agora or datetime.now()
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"grafo-{momento:%Y%m%d-%H%M%S}.json"
    n = 2
    while destino.exists():
        destino = pasta / f"grafo-{momento:%Y%m%d-%H%M%S}-{n}.json"
        n += 1
    destino.write_text(json.dumps(exportar(grafo, momento), ensure_ascii=False, indent=1), encoding="utf-8")
    return destino


def ultima_exportacao(pasta: Path) -> Path | None:
    arquivos = sorted(pasta.glob("grafo-*.json")) if pasta.exists() else []
    return arquivos[-1] if arquivos else None


# --- importar -------------------------------------------------------------------------------------------------------
@dataclass
class Comparacao:
    novas: list[dict] = field(default_factory=list)
    iguais: int = 0
    conflitos: list[dict] = field(default_factory=list)  # do arquivo, com o mesmo id de uma que existe
    duplicadas: list[dict] = field(default_factory=list)  # do arquivo, mesmo nome/espaço/origem de outra id
    ligacoes_novas: list[dict] = field(default_factory=list)
    ligacoes_conflito: list[dict] = field(default_factory=list)
    ligacoes_puladas: int = 0  # apontam para uma ideia que ficou de fora (duplicada)


def ler_arquivo(caminho: Path) -> dict[str, Any]:
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as erro:
        raise ValueError(f"Não consegui ler '{caminho.name}': {erro}") from None
    except json.JSONDecodeError as erro:
        raise ValueError(f"'{caminho.name}' não é um JSON válido (linha {erro.lineno}).") from None
    validar(dados)
    return dados


def validar(dados: Any) -> None:
    """Levanta ValueError dizendo o primeiro problema encontrado."""
    if not isinstance(dados, dict) or dados.get("formato") != FORMATO:
        raise ValueError("O arquivo não é uma exportação do grafo da Eco.")
    if not isinstance(dados.get("versao"), int) or dados["versao"] > VERSAO:
        raise ValueError(f"Versão do arquivo ({dados.get('versao')}) mais nova do que esta Eco entende ({VERSAO}).")
    for t in TABELAS:
        if not isinstance(dados.get(t, []), list):
            raise ValueError(f"A parte '{t}' do arquivo está estragada.")
    ids: set[str] = set()
    for i in dados.get("ideias", []):
        if not isinstance(i, dict) or any(not i.get(c) for c in OBRIGATORIOS["ideias"]):
            raise ValueError("Há uma ideia sem id, nome ou origem no arquivo.")
        if i["id"] in ids:
            raise ValueError(f"A ideia {i['id']} aparece duas vezes no arquivo.")
        ids.add(i["id"])
    for l in dados.get("ligacoes", []):
        if not isinstance(l, dict) or any(not l.get(c) for c in OBRIGATORIOS["ligacoes"]):
            raise ValueError("Há uma ligação sem id ou sem as duas pontas no arquivo.")
        if l["a"] not in ids or l["b"] not in ids:
            raise ValueError(f"A ligação {l['id']} aponta para uma ideia que não está no arquivo.")
    pais = {i["id"]: i.get("pai") for i in dados.get("ideias", [])}
    for i in pais:
        vistos, atual = set(), i
        while atual is not None:
            if atual in vistos:
                raise ValueError("A hierarquia do arquivo tem um círculo (uma ideia acaba dentro dela mesma).")
            vistos.add(atual)
            atual = pais.get(atual) if pais.get(atual) in pais else None


def _sem_horarios(linha: dict) -> dict:
    return {k: v for k, v in linha.items() if k not in ("criado_em", "atualizado_em")}


def comparar(grafo: Grafo, dados: dict[str, Any]) -> Comparacao:
    c = Comparacao()
    atuais = {l["id"]: dict(l) for l in grafo.banco._consultar("SELECT * FROM ideias")}
    por_chave = {(chave_da_ideia(l["nome"]), l["espaco_id"], l["origem"]): l["id"] for l in atuais.values()}
    espacos = {l["id"] for l in grafo.banco._consultar("SELECT id FROM espacos")}
    fora: set[str] = set()
    for i in dados.get("ideias", []):
        espaco = i.get("espaco_id") if i.get("espaco_id") in espacos else None  # como vai ficar ao gravar
        if i["id"] in atuais:
            if _sem_horarios({k: i.get(k) for k in atuais[i["id"]]}) == _sem_horarios(atuais[i["id"]]):
                c.iguais += 1
            else:
                c.conflitos.append(i)
        elif (chave_da_ideia(i["nome"]), espaco, i["origem"]) in por_chave:
            c.duplicadas.append(i)
            fora.add(i["id"])
        else:
            c.novas.append(i)
    lig_atuais = {(l["a"], l["b"]): dict(l) for l in grafo.banco._consultar("SELECT * FROM ligacoes")}
    for l in dados.get("ligacoes", []):
        if l["a"] in fora or l["b"] in fora:
            c.ligacoes_puladas += 1
        elif (l["a"], l["b"]) not in lig_atuais:
            c.ligacoes_novas.append(l)
        elif _sem_horarios({k: l.get(k) for k in lig_atuais[(l["a"], l["b"])] if k != "id"}) != _sem_horarios(
            {k: v for k, v in lig_atuais[(l["a"], l["b"])].items() if k != "id"}
        ):
            c.ligacoes_conflito.append(l)
    return c


def frase(c: Comparacao, substituir: bool, nome: str) -> str:
    partes = [f"Importar {nome}: {len(c.novas)} ideias novas e {len(c.ligacoes_novas)} ligações novas."]
    if c.iguais:
        partes.append(f"{c.iguais} já estão iguais.")
    if c.duplicadas:
        partes.append(f"{len(c.duplicadas)} repetem ideias que já existem e ficam de fora.")
    if c.conflitos or c.ligacoes_conflito:
        n = len(c.conflitos) + len(c.ligacoes_conflito)
        partes.append(f"ATENÇÃO: vou SUBSTITUIR {n} itens que estão diferentes no grafo atual." if substituir
                      else f"{n} itens estão diferentes no grafo atual e ficam como estão.")
    partes.append("Antes, faço um backup do banco.")
    return " ".join(partes)


@dataclass
class PlanoImportacao:
    frase: str
    executar: Callable[[], str]


def _colunas(grafo: Grafo, tabela: str) -> list[str]:
    return [l[1] for l in grafo.banco._con.execute(f"PRAGMA table_info({tabela})")]


def planejar_importacao(grafo: Grafo, caminho: Path, substituir_conflitos: bool = False, fazer_backup=None) -> PlanoImportacao:
    dados = ler_arquivo(caminho)
    c = comparar(grafo, dados)

    def agir() -> str:
        if fazer_backup is not None:
            fazer_backup()
        b = grafo.banco
        espacos = {l["id"] for l in b._consultar("SELECT id FROM espacos")}
        conversas = {l["id"] for l in b._consultar("SELECT id FROM conversas")}
        cols = {t: _colunas(grafo, t) for t in TABELAS}

        def inserir(tabela: str, linha: dict, trocar: bool = False) -> None:
            campos = [k for k in cols[tabela] if k in linha]
            verbo = "INSERT OR REPLACE" if trocar else "INSERT OR IGNORE"
            b._con.execute(f"{verbo} INTO {tabela} ({', '.join(campos)}) VALUES ({', '.join('?' * len(campos))})",  # noqa: S608
                           [linha[k] for k in campos])

        sem_espaco = 0
        antes_dos_conflitos = {i["id"]: grafo._linha_crua(i["id"]) for i in c.conflitos} if substituir_conflitos else {}
        with b._lock, b._con:
            novas = c.novas + (c.conflitos if substituir_conflitos else [])
            for i in novas:  # primeiro sem pai (o pai pode vir depois na lista); o pai entra numa 2ª passada
                linha = dict(i, pai=None)
                if linha.get("espaco_id") and linha["espaco_id"] not in espacos:
                    linha["espaco_id"] = None
                    sem_espaco += 1
                inserir("ideias", linha, trocar=i in c.conflitos)
            for i in novas:
                if i.get("pai"):
                    b._con.execute("UPDATE ideias SET pai = ? WHERE id = ? AND EXISTS (SELECT 1 FROM ideias WHERE id = ?)",
                                   (i["pai"], i["id"], i["pai"]))
            for l in c.ligacoes_novas + (c.ligacoes_conflito if substituir_conflitos else []):
                if l in c.ligacoes_conflito:
                    b._con.execute("DELETE FROM ligacoes WHERE a = ? AND b = ?", (l["a"], l["b"]))
                inserir("ligacoes", l)
            fora = {i["id"] for i in c.duplicadas}
            for f in dados.get("ideia_fontes", []):
                if f.get("conversa_id") in conversas and f.get("ideia_id") not in fora:
                    inserir("ideia_fontes", f)
            for d in dados.get("ideia_dias", []):
                if d.get("ideia_id") not in fora:
                    inserir("ideia_dias", d)
            for g in dados.get("ideias_ignoradas", []):
                inserir("ideias_ignoradas", g)
        with grafo.como(f"importação de {caminho.name}"):  # histórico (19.6.4): o que entrou e o que foi trocado
            for i in c.novas:
                grafo._registrar("importar", i["id"], depois=grafo._linha_crua(i["id"]))
            for id_, antes in antes_dos_conflitos.items():
                grafo._registrar("substituir", id_, antes=antes, depois=grafo._linha_crua(id_))
        texto = f"Importei {len(c.novas)} ideias novas e {len(c.ligacoes_novas)} ligações."
        if substituir_conflitos and (c.conflitos or c.ligacoes_conflito):
            texto += f" Substituí {len(c.conflitos) + len(c.ligacoes_conflito)} itens diferentes."
        if sem_espaco:
            texto += f" {sem_espaco} ideias eram de espaços que não existem aqui e ficaram no Geral."
        return texto

    return PlanoImportacao(frase(c, substituir_conflitos, caminho.name), agir)
