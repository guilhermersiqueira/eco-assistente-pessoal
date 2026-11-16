"""Leitura do export de conversas do claude.ai (M1).

Formato observado em 15/11/2026: o pedido de exportação devolve um manifesto (`manifest-*.json`, que só lista os arquivos)
e, por categoria, arquivos `.zip` numerados (`conversations-000.zip`, `memories-000.zip`, `light_metadata-000.zip`,
`frames-000.zip`). Dentro de `conversations-NNN.zip` há um `conversations.json`: uma lista de conversas
(`uuid`, `name`, `summary`, `created_at`, `updated_at`, `account`, `chat_messages`); cada mensagem tem `sender`
("human" ou "assistant"), `text`, `created_at`, `content` (blocos: texto, ferramentas, raciocínio, imagens), `attachments`
e `files`. Também aceitamos o formato antigo (um `.zip` único com `conversations.json`) e o `conversations.json` solto.

O que entra na `Conversa`: só as mensagens com texto em `text`, do dono ("human") e da IA ("assistant"). Ficam de fora o
raciocínio da IA, as chamadas de ferramentas, os blocos injetados pelo sistema e o conteúdo extraído de anexos (são
documentos colados ou enviados, quase sempre de terceiros). Nada é extraído para o disco e os arquivos nunca são
alterados; o manifesto é lido só pelos nomes, os endereços de download dele não são usados.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from eco.importacao.modelo import ASSISTENTE, DONO, Conversa, Mensagem

JANELA_SEGUNDOS = 24 * 3600  # partes de um mesmo export são baixadas juntas (o link expira em 24 h)
MAX_BYTES_JSON = 500 * 1024 * 1024  # recusa um conversations.json maior que isso (zip malformado ou enorme)
NOME_DO_JSON = "conversations.json"
SENDERS = {"human": DONO, "assistant": ASSISTENTE}


class ExportInvalido(ValueError):
    """O arquivo não é um export de conversas que a Eco consiga ler (a frase serve para mostrar ao dono)."""


@dataclass
class Export:
    """Os arquivos encontrados de um export de conversas."""

    arquivos: list[Path]
    manifesto: Path | None = None
    faltando: list[str] = field(default_factory=list)  # partes que o manifesto lista e não estão na pasta
    avisos: list[str] = field(default_factory=list)
    desatualizado: bool = False  # há um manifesto bem mais novo cujos arquivos ainda não foram baixados

    @property
    def baixado_em(self) -> datetime:
        return datetime.fromtimestamp(max(a.stat().st_mtime for a in self.arquivos))


@dataclass
class Leitura:
    conversas: list[Conversa]
    arquivos: list[Path]
    hash: str  # dos bytes dos arquivos lidos (identifica este export no registro das importações)
    invalidas: int = 0  # conversas do arquivo que não deu para ler
    avisos: list[str] = field(default_factory=list)


# --- localizar -------------------------------------------------------------------------------------------------------
def pastas_padrao() -> list[Path]:
    return [Path.home() / "Downloads"]


def _tem_conversas(zip_: Path) -> bool:
    try:
        with zipfile.ZipFile(zip_) as z:
            return any(Path(n).name == NOME_DO_JSON for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def _candidatos(pasta: Path) -> list[Path]:
    if not pasta.is_dir():
        return []
    achados = list(pasta.glob("conversations-*.zip"))
    if (pasta / NOME_DO_JSON).is_file():
        achados.append(pasta / NOME_DO_JSON)
    achados += [p for p in pasta.glob("data-*.zip") if _tem_conversas(p)]  # formato antigo: um zip só
    return [p for p in achados if p.is_file()]


def _ler_manifesto(caminho: Path) -> list[str] | None:
    """Os nomes dos arquivos de conversas que o manifesto diz que existem. Só os nomes: os links não são usados."""
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        itens = dados["data_files"]
        return [str(i["filename"]) for i in itens if isinstance(i, dict) and i.get("category") == "conversations" and i.get("filename")]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def localizar_export(pastas: list[Path] | None = None) -> Export | None:
    """O export de conversas mais recente nas pastas (por padrão, Downloads). Se for um export em partes, junta as partes
    baixadas na mesma janela de 24 h. Não abre nada além dos nomes (e do manifesto, se houver). None se não achar."""
    achados = [p for pasta in (pastas if pastas is not None else pastas_padrao()) for p in _candidatos(pasta)]
    if not achados:
        return None
    novo = max(achados, key=lambda p: p.stat().st_mtime)
    if novo.name.startswith("conversations-") and novo.suffix == ".zip":
        arquivos = sorted((p for p in novo.parent.glob("conversations-*.zip") if p.is_file()
                           and abs(p.stat().st_mtime - novo.stat().st_mtime) <= JANELA_SEGUNDOS), key=lambda p: p.name)
    else:
        arquivos = [novo]
    export = Export(arquivos)
    manifestos = [m for m in novo.parent.glob("manifest-*.json")
                  if m.is_file() and abs(m.stat().st_mtime - novo.stat().st_mtime) <= JANELA_SEGUNDOS]
    if manifestos:
        export.manifesto = max(manifestos, key=lambda m: m.stat().st_mtime)
        esperados = _ler_manifesto(export.manifesto)
        if esperados is None:
            export.avisos.append(f"Não consegui ler o manifesto {export.manifesto.name}; conferi só os arquivos que achei.")
        else:
            export.faltando = [n for n in esperados if not (novo.parent / n).is_file()]
    todos = [m for m in novo.parent.glob("manifest-*.json") if m.is_file()]
    if todos:
        recente = max(todos, key=lambda m: m.stat().st_mtime)
        if recente.stat().st_mtime > novo.stat().st_mtime + JANELA_SEGUNDOS:
            export.desatualizado = True
            export.avisos.append(
                f"Há um manifesto de {datetime.fromtimestamp(recente.stat().st_mtime):%d/%m/%Y}, mas os arquivos de conversas "
                f"encontrados são de {datetime.fromtimestamp(novo.stat().st_mtime):%d/%m/%Y}. Baixe os arquivos do export novo "
                "(os links do manifesto expiram em 24 horas).")
    return export


# --- ler -------------------------------------------------------------------------------------------------------------
def _hash_dos_arquivos(arquivos: list[Path]) -> str:
    h = hashlib.sha1()
    for a in sorted(arquivos, key=lambda p: p.name):
        h.update(a.name.encode("utf-8"))
        with a.open("rb") as f:
            for bloco in iter(lambda: f.read(1 << 20), b""):
                h.update(bloco)
    return h.hexdigest()


def _bytes_do_json(caminho: Path) -> bytes:
    if caminho.suffix.lower() == ".json":
        if caminho.stat().st_size > MAX_BYTES_JSON:
            raise ExportInvalido(f"{caminho.name} é grande demais para ler ({caminho.stat().st_size // (1 << 20)} MB).")
        return caminho.read_bytes()
    try:
        with zipfile.ZipFile(caminho) as z:
            entradas = [i for i in z.infolist() if Path(i.filename).name == NOME_DO_JSON]
            if not entradas:
                raise ExportInvalido(f"{caminho.name} não tem o arquivo {NOME_DO_JSON} (é outra parte do export?).")
            if entradas[0].file_size > MAX_BYTES_JSON:
                raise ExportInvalido(f"{NOME_DO_JSON} em {caminho.name} é grande demais para ler.")
            with z.open(entradas[0]) as f:
                dados = f.read(MAX_BYTES_JSON + 1)  # não confia no tamanho declarado no cabeçalho do zip
    except zipfile.BadZipFile as erro:
        raise ExportInvalido(f"{caminho.name} não é um .zip válido (o download pode ter ficado incompleto).") from erro
    except OSError as erro:
        raise ExportInvalido(f"Não consegui abrir {caminho.name}: {erro}") from erro
    if len(dados) > MAX_BYTES_JSON:
        raise ExportInvalido(f"{NOME_DO_JSON} em {caminho.name} é grande demais para ler.")
    return dados


def _titulo(d: dict, mensagens: list[Mensagem], criada_em: str) -> str:
    """O nome da conversa; se não tem (a maioria no export observado), o resumo, o começo da primeira mensagem do dono ou a data."""
    for chave, limite in (("name", 120), ("summary", 80)):
        t = " ".join(str(d.get(chave) or "").split())
        if t:
            return t[:limite]
    for m in mensagens:
        if m.autor == DONO:
            return " ".join(m.texto.split())[:60]
    return f"Conversa de {criada_em[:10]}" if criada_em else "Conversa sem título"


def _converter(d: object) -> Conversa | None:
    if not isinstance(d, dict):
        return None
    uuid_ = d.get("uuid")
    brutas = d.get("chat_messages")
    if not isinstance(uuid_, str) or not uuid_.strip() or not isinstance(brutas, list):
        return None
    mensagens: list[Mensagem] = []
    for m in brutas:
        if not isinstance(m, dict) or m.get("sender") not in SENDERS:
            continue
        texto = m.get("text")
        if isinstance(texto, str) and texto.strip():  # mensagens só de ferramenta (texto vazio) não são conversa
            mensagens.append(Mensagem(SENDERS[m["sender"]], texto.strip(), str(m.get("created_at") or "")))
    if all(m.quando for m in mensagens):
        mensagens.sort(key=lambda m: m.quando)  # estável: a ordem do arquivo já é a da conversa
    criada, atualizada = str(d.get("created_at") or ""), str(d.get("updated_at") or "")
    return Conversa(uuid_.strip(), _titulo(d, mensagens, criada), criada, atualizada, tuple(mensagens))


def ler_export(arquivos: list[Path]) -> Leitura:
    """Lê as partes do export e devolve as conversas (sem repetir: se a mesma aparece em duas partes, vale a mais
    recente). Lança `ExportInvalido` se um arquivo está corrompido ou não é de conversas."""
    if not arquivos:
        raise ExportInvalido("Nenhum arquivo de conversas para ler.")
    por_id: dict[str, Conversa] = {}
    invalidas = 0
    for caminho in arquivos:
        try:
            dados = json.loads(_bytes_do_json(caminho).decode("utf-8"))
        except UnicodeDecodeError as erro:
            raise ExportInvalido(f"{NOME_DO_JSON} em {caminho.name} não está em UTF-8.") from erro
        except json.JSONDecodeError as erro:
            raise ExportInvalido(f"{NOME_DO_JSON} em {caminho.name} está corrompido (linha {erro.lineno}).") from erro
        if not isinstance(dados, list):
            raise ExportInvalido(f"{NOME_DO_JSON} em {caminho.name} tem um formato inesperado (esperava uma lista de conversas).")
        for item in dados:
            c = _converter(item)
            if c is None:
                invalidas += 1
            elif c.uuid not in por_id or c.atualizada_em > por_id[c.uuid].atualizada_em:
                por_id[c.uuid] = c
    avisos = []
    if invalidas:
        avisos.append(f"{invalidas} conversa(s) do arquivo estavam fora do formato e foram ignoradas.")
    if not por_id:
        avisos.append("O arquivo não tem nenhuma conversa.")
    return Leitura(list(por_id.values()), list(arquivos), _hash_dos_arquivos(list(arquivos)), invalidas, avisos)
