"""Ações no computador (Windows): abrir apps e sites, volume/brilho/mídia e arquivos na pasta pessoal.

Regras de segurança (decididas com o dono em 10/11/2026):
- Abrir app/site, volume, brilho, mídia, listar e buscar arquivos saem direto. Criar pasta, copiar e renomear passam pela
  confirmação (a frase lida de volta vem de `Plano.frase`). Mover e apagar arquivos foram RETIRADOS a pedido do dono
  (12/11/2026, decisão D1); as travas abaixo continuam valendo para o que ficou.
- Arquivos: só dentro da pasta pessoal do usuário (o caminho é resolvido antes, então atalho/junção que sai dela é recusado);
  pastas ocultas (começam com ponto), `AppData` e `NTUSER*` ficam de fora. Pastas diretamente na pasta pessoal ou na raiz do
  OneDrive (Documentos, Downloads...) não podem ser renomeadas, só o que há dentro delas.
- Nunca sobrescreve: se o destino existe, recusa.
- Abrir site só aceita http e https. Sem controle de mouse e teclado (ficou para depois).
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import unicodedata
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

SEM_JANELA = 0x08000000  # CREATE_NO_WINDOW


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def _exigir_windows() -> None:
    if sys.platform != "win32":
        raise ValueError("Essa ação só funciona no Windows.")


def _powershell(comando: str, limite: float = 30.0) -> str:
    _exigir_windows()
    pronto = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + comando
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", pronto],
        capture_output=True, text=True, encoding="utf-8", timeout=limite, creationflags=SEM_JANELA,
    )
    return r.stdout.strip()


# --- apps e sites ----------------------------------------------------------------------------------------------
_apps: list[tuple[str, str]] | None = None


def _apps_instalados(recarregar: bool = False) -> list[tuple[str, str]]:
    """(nome, AppID) de tudo que aparece no menu Iniciar, inclusive apps da Microsoft Store."""
    global _apps
    if _apps is None or recarregar:
        bruto = _powershell("Get-StartApps | ConvertTo-Json -Compress")
        dados = json.loads(bruto) if bruto else []
        if isinstance(dados, dict):
            dados = [dados]
        _apps = [(d["Name"], d["AppID"]) for d in dados if d.get("Name") and d.get("AppID")]
    return _apps


def escolher_app(nome: str, apps: list[tuple[str, str]]) -> tuple[str, str]:
    """O app que melhor combina com o nome falado (igual, depois começa com, depois contém). Empate vira erro com as opções."""
    alvo = _sem_acento(nome).strip()
    if not alvo:
        raise ValueError("Preciso do nome do app.")
    for combina in (lambda n: n == alvo, lambda n: n.startswith(alvo), lambda n: alvo in n):
        por_nome: dict[str, tuple[str, str]] = {}
        for n, i in sorted(apps):  # o mesmo app pode aparecer duas vezes (versão clássica e da Store): vale a primeira
            if combina(_sem_acento(n)):
                por_nome.setdefault(_sem_acento(n), (n, i))
        achados = sorted(por_nome.values(), key=lambda x: (len(x[0]), x[0]))
        if len(achados) == 1:
            return achados[0]
        if len(achados) > 1:
            nomes = ", ".join(n for n, _ in achados[:5])
            raise ValueError(f"Mais de um app combina com '{nome}': {nomes}. Qual deles?")
    raise ValueError(f"Não achei nenhum app chamado '{nome}' no menu Iniciar.")


def abrir_app(nome: str) -> str:
    _exigir_windows()
    try:
        achado = escolher_app(nome, _apps_instalados())
    except ValueError as erro:
        if "Não achei" not in str(erro):
            raise
        achado = escolher_app(nome, _apps_instalados(recarregar=True))  # app recém-instalado
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{achado[1]}"], creationflags=SEM_JANELA)
    return f"Abrindo {achado[0]}."


def abrir_site(alvo: str) -> str:
    alvo = alvo.strip()
    if not alvo or any(c.isspace() for c in alvo):
        raise ValueError("Preciso de um endereço de site, sem espaços (ex.: usp.edu.br).")
    if "://" not in alvo:
        alvo = "https://" + alvo
    u = urlparse(alvo)
    if u.scheme not in ("http", "https") or not u.hostname or "@" in u.netloc or "." not in u.hostname:
        raise ValueError("Só abro endereços http ou https de sites (ex.: https://usp.edu.br).")
    webbrowser.open(alvo)
    return f"Abrindo {u.hostname}."


# --- volume, mídia e brilho ------------------------------------------------------------------------------------
TECLAS = {"mudo": 0xAD, "volume_menos": 0xAE, "volume_mais": 0xAF, "proxima": 0xB0, "anterior": 0xB1, "pausar_tocar": 0xB3}
PASSO_VOLUME = 2  # cada tecla de volume do Windows muda 2%


def _tecla(vk: int) -> None:
    _exigir_windows()
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # soltar


def _clamp(v: float, minimo: int = 0, maximo: int = 100) -> int:
    return max(minimo, min(maximo, round(v)))


def brilho_atual() -> int:
    saida = _powershell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness")
    try:
        return int(saida.split()[0])
    except (ValueError, IndexError):
        raise ValueError("Este monitor não deixa o Windows mudar o brilho (comum em monitor externo).") from None


def _definir_brilho(valor: int) -> None:
    _powershell(
        "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
        f"Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1;Brightness={valor}}} | Out-Null"
    )


ACOES_SOM_E_TELA = (
    "volume_mais", "volume_menos", "volume_definir", "mudo", "proxima", "anterior", "pausar_tocar", "brilho_mais", "brilho_menos", "brilho_definir",
)


def ajustar(acao: str, valor: float | None = None) -> str:
    """`valor`: em % (volume e brilho). Mais/menos sem valor mudam 10%."""
    if acao in ("volume_mais", "volume_menos"):
        v = 10 if valor is None else abs(valor)
        for _ in range(max(1, round(v / PASSO_VOLUME))):
            _tecla(TECLAS[acao])
        return f"Volume {'aumentado' if acao == 'volume_mais' else 'diminuído'} em cerca de {round(v)}%."
    if acao == "volume_definir":
        if valor is None:
            raise ValueError("Diga o volume de 0 a 100.")
        alvo = _clamp(valor)
        for _ in range(100 // PASSO_VOLUME):  # zera e sobe: o Windows não deixa ler o volume atual por tecla
            _tecla(TECLAS["volume_menos"])
        for _ in range(round(alvo / PASSO_VOLUME)):
            _tecla(TECLAS["volume_mais"])
        return f"Volume em cerca de {alvo}%."
    if acao in ("mudo", "proxima", "anterior", "pausar_tocar"):
        _tecla(TECLAS[acao])
        return {"mudo": "Alternei o mudo.", "proxima": "Próxima faixa.", "anterior": "Faixa anterior.", "pausar_tocar": "Pausei ou retomei a mídia."}[acao]
    if acao in ("brilho_mais", "brilho_menos"):
        passo = 10 if valor is None else abs(valor)
        novo = _clamp(brilho_atual() + (passo if acao == "brilho_mais" else -passo), 5)
        _definir_brilho(novo)
        return f"Brilho em {novo}%."
    if acao == "brilho_definir":
        if valor is None:
            raise ValueError("Diga o brilho de 0 a 100.")
        novo = _clamp(valor, 5)  # nunca zera: tela apagada demais confunde
        _definir_brilho(novo)
        return f"Brilho em {novo}%."
    raise ValueError(f"Ação desconhecida: {acao}.")


# --- arquivos --------------------------------------------------------------------------------------------------
HOME: Path | None = None  # os testes trocam
OCULTAS = {"appdata"}
PULAR_NA_BUSCA = {"node_modules", "__pycache__", ".git", ".venv", "venv", "site-packages"}
_ALIAS = {
    "documentos": ["OneDrive/Documentos", "Documents", "Documentos"],
    "documents": ["OneDrive/Documentos", "Documents", "Documentos"],
    "downloads": ["Downloads"],
    "transferencias": ["Downloads"],
    "area de trabalho": ["OneDrive/Área de Trabalho", "Desktop", "Área de Trabalho"],
    "desktop": ["OneDrive/Área de Trabalho", "Desktop", "Área de Trabalho"],
    "imagens": ["OneDrive/Imagens", "Pictures", "Imagens"],
    "pictures": ["OneDrive/Imagens", "Pictures", "Imagens"],
    "musicas": ["Music", "Músicas"],
    "videos": ["Videos", "Vídeos"],
}


def casa() -> Path:
    return (HOME or Path.home()).resolve()


def resolver(texto: str) -> Path:
    """Caminho dito pelo dono (absoluto, relativo à pasta pessoal ou começando por Documentos/Downloads/...) já validado."""
    t = (texto or "").strip().strip('"').strip("'")
    if not t:
        raise ValueError("Preciso do caminho do arquivo ou da pasta.")
    base = casa()
    partes = [p for p in t.replace("\\", "/").split("/") if p]
    if t.startswith("~"):
        p = base.joinpath(*partes[1:]) if partes and partes[0] == "~" else base
    elif Path(t).is_absolute():
        p = Path(t)
    else:
        candidatos = _ALIAS.get(_sem_acento(partes[0])) if partes else None
        raiz = next((base / c for c in (candidatos or []) if (base / c).exists()), None)
        p = raiz.joinpath(*partes[1:]) if raiz else base.joinpath(*partes)
    resolvido = p.resolve()
    try:
        rel = resolvido.relative_to(base)
    except ValueError:
        raise ValueError("Só mexo em arquivos dentro da sua pasta pessoal.") from None
    if any(x.startswith(".") or x.lower() in OCULTAS for x in rel.parts) or (len(rel.parts) == 1 and rel.parts[0].lower().startswith("ntuser")):
        raise ValueError("Essa pasta guarda configurações de programas ou do sistema; não mexo nela.")
    return resolvido


def _protegida(p: Path) -> bool:
    b = casa()
    return p == b or p.parent == b or (p.parent.parent == b and p.parent.name.lower() == "onedrive")


def _exigir_mutavel(p: Path) -> None:
    if not p.exists():
        raise ValueError(f"Não encontrei '{p.name}'.")
    if _protegida(p):
        raise ValueError(f"'{p.name}' é uma pasta principal; mexo só no que está dentro dela, não nela.")


def listar(caminho: str, limite: int = 60) -> str:
    p = resolver(caminho)
    if not p.is_dir():
        raise ValueError(f"'{p.name}' não é uma pasta.")
    itens = sorted((x for x in p.iterdir() if not x.name.startswith(".")), key=lambda x: (not x.is_dir(), x.name.lower()))
    if not itens:
        return f"A pasta {p.name} está vazia."
    linhas = [x.name + ("/" if x.is_dir() else "") for x in itens[:limite]]
    extra = f"\n... e mais {len(itens) - limite}" if len(itens) > limite else ""
    return f"Pasta {p.name} ({len(itens)} itens):\n" + "\n".join(linhas) + extra


def buscar(nome: str, dentro: str | None = None, limite: int = 15, segundos: float = 6.0) -> str:
    """Arquivos e pastas cujo nome tem todas as palavras de `nome`, a partir de `dentro` (padrão: pasta pessoal)."""
    palavras = _sem_acento(nome).split()
    if not palavras:
        raise ValueError("Preciso de um nome ou parte dele para procurar.")
    raiz = resolver(dentro) if dentro else casa()
    fim = time.monotonic() + segundos
    achados: list[Path] = []
    for pasta, dirs, arquivos in os.walk(raiz):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in OCULTAS and d.lower() not in PULAR_NA_BUSCA]
        for n in [*dirs, *arquivos]:
            if all(w in _sem_acento(n) for w in palavras):
                achados.append(Path(pasta) / n)
        if len(achados) >= limite or time.monotonic() > fim:
            break
    if not achados:
        return f"Não achei nada com '{nome}' no nome."
    return "\n".join(str(a.relative_to(casa())) for a in achados[:limite]) + ("\n(a busca parou no limite; refine o nome)" if len(achados) >= limite else "")


@dataclass
class Plano:
    frase: str  # o que a Eco lê de volta antes do "sim"
    executar: Callable[[], str]


def _nome_de(p: Path) -> str:
    return f"{p.name}" + (" (pasta)" if p.is_dir() else "")


def planejar_criar_pasta(caminho: str) -> Plano:
    p = resolver(caminho)
    if p.exists():
        raise ValueError(f"'{p.name}' já existe.")
    return Plano(f"Criar a pasta {p.name} dentro de {p.parent.name}.", lambda: (p.mkdir(parents=True), f"Pasta {p.name} criada.")[1])


def planejar_copiar(origem: str, destino: str) -> Plano:
    o = resolver(origem)
    if not o.exists():
        raise ValueError(f"Não encontrei '{o.name}'.")
    d = resolver(destino)
    if not d.is_dir():
        raise ValueError(f"A pasta de destino '{d.name}' não existe.")
    if o.is_dir() and (d == o or o in d.parents):
        raise ValueError("Não dá para copiar uma pasta para dentro dela mesma.")
    alvo = d / o.name
    if alvo.exists():
        raise ValueError(f"Já existe '{o.name}' em {d.name}; não sobrescrevo.")

    def agir() -> str:
        shutil.copytree(o, alvo) if o.is_dir() else shutil.copy2(o, alvo)
        return f"Copiei {o.name} para {d.name}."

    return Plano(f"Copiar {_nome_de(o)}, de {o.parent.name}, para {d.name}.", agir)


def planejar_renomear(origem: str, novo_nome: str) -> Plano:
    o = resolver(origem)
    _exigir_mutavel(o)
    novo = (novo_nome or "").strip()
    if not novo or novo in (".", "..") or any(c in novo for c in '/\\:*?"<>|') or novo.startswith("."):
        raise ValueError("O novo nome está vazio ou tem caracteres que o Windows não aceita.")
    alvo = o.with_name(novo)
    if alvo.exists():
        raise ValueError(f"Já existe '{novo}' nessa pasta; não sobrescrevo.")
    return Plano(f"Renomear {_nome_de(o)} para {novo}.", lambda: (o.rename(alvo), f"Renomeei para {novo}.")[1])


ACOES_ARQUIVO = ("criar_pasta", "copiar", "renomear")
RETIRADAS = ("mover", "apagar")


def planejar(acao: str, caminho: str, destino: str | None = None) -> Plano:
    if acao in RETIRADAS:
        raise ValueError("Mover e apagar arquivos não fazem mais parte da Eco (retirados a pedido do dono). "
                         "Diga que ele pode fazer isso pelo Explorador de Arquivos.")
    if acao == "criar_pasta":
        return planejar_criar_pasta(caminho)
    if acao in ("copiar", "renomear"):
        if not (destino or "").strip():
            raise ValueError({"renomear": "Diga o novo nome.", "copiar": "Diga para qual pasta copiar."}[acao])
        return {"copiar": planejar_copiar, "renomear": planejar_renomear}[acao](caminho, destino)
    raise ValueError(f"Ação desconhecida: {acao}.")
