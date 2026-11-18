"""Configuração do Eco.

As chaves (ANTHROPIC_API_KEY, OPENAI_API_KEY, PORCUPINE_ACCESS_KEY) vêm do arquivo
.env em %USERPROFILE%\\.eco\\.env, fora do OneDrive. Defina ECO_ENV_FILE para usar outro caminho.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PASTA_ECO = Path.home() / ".eco"


@dataclass(frozen=True)
class Config:
    pasta_dados: Path
    modelo_padrao: str
    modelo_forte: str
    esforco_padrao: str
    esforco_forte: str
    silencio_seg: float
    max_gravacao_seg: float
    espera_fala_seg: float
    limiar_voz: float
    conversa_seg: float
    modelo_chat: str
    esforco_chat: str
    lembretes: bool
    lembrete_antecedencia_min: int
    modelo_stt: str
    modelo_tts: str
    voz_tts: str
    estilo_voz: str | None
    velocidade_tts: float | None
    visual: bool
    orbe_tamanho: int
    orbe_tela_cheia: bool
    orbe_tela_escala: float
    orbe_tom: float
    particulas: float
    sons: bool
    modo: str
    porcupine_chave: str | None
    porcupine_ppn: Path | None
    porcupine_modelo_pt: Path | None
    wakeword_modelo: str | None
    wakeword_limiar: float
    vosk_modelo: Path
    vosk_frase: str
    max_pesquisas: int
    voltas_max: int
    turnos_historico: int
    orcamento_usd: float
    modelo_grafo: str = "claude-haiku-5-5"  # análise das conversas para o grafo de ideias
    teto_grafo_usd: float = 1.0  # gasto mensal máximo só com a análise do grafo
    ligacao_automatica: bool = True  # E6: liga ideias novas às existentes (Haiku, ~US$ 0,0002 por ideia; ECO_LIGACAO_AUTO)
    ligacao_aprovar: bool = False  # D6: média confiança vira só sugestão, esperando o dono (ECO_LIGACAO_APROVAR=sim)
    memoria_conferir: bool = True  # E7: ao guardar, confere repetição e contradição com o Haiku (ECO_MEMORIA_CONFERIR)
    higiene_dias: float = 7  # E7: revisão automática da memória a cada tantos dias (0 desliga; ECO_HIGIENE_DIAS)
    grafo_limite_mb: float = 500  # E7 (19.6.23): tamanho do banco a partir do qual a Eco sugere arquivar (ECO_GRAFO_LIMITE_MB)
    importar_teto_usd: float = 2.0  # importação das conversas do claude.ai: gasto máximo por importação (ECO_IMPORTAR_TETO_USD)
    grafo_vista: str = "orbe"  # vista padrão da aba Grafo: "orbe" (esfera 3D) ou "2d"
    grafo_qualidade: str = "medio"  # baixo, medio ou alto (cai sozinho se a tela ficar lenta)
    # Preço da OpenAI por minuto de áudio, para a estimativa do `eco gasto` e do orçamento (que soma Claude e OpenAI).
    # Transcrição: estimativa oficial da página de preços da OpenAI (gpt-4o-mini-transcribe, US$ 0,003/min, 11/2026).
    # Fala: a página não dá valor por minuto (cobra US$ 12 por milhão de tokens de áudio); 0,017 vem da calibração de 17/11/2026
    # (painel da OpenAI: US$ 0,98 no total de 11 a 17/11, menos ~US$ 0,09 de transcrição, para 51,7 min de fala).
    backup_horas: float = 24  # backup automático do eco.db a cada tantas horas (0 desliga)
    backup_manter: int = 14  # quantas cópias automáticas guardar (manuais e de antes de operações nunca são apagadas)
    filtro_voz: bool = True  # Silero VAD descarta gravação sem voz antes da transcrição (ECO_FILTRO_VOZ=nao desliga)
    preco_transcricao_min: float = 0.003
    preco_fala_min: float = 0.017
    fundo: str = "medio"  # partículas de fundo da janela principal: desligado, baixo, medio ou alto


def _caminho_opcional(nome: str) -> Path | None:
    valor = os.environ.get(nome, "").strip()
    return Path(valor) if valor else None


ESTILO_VOZ_PADRAO = (
    "Fale de forma natural e calorosa, como uma amiga próxima conversando: ritmo variado, "
    "pausas curtas, entonação expressiva e um sorriso na voz. Nada de tom de leitura, de locutor "
    "ou de atendimento automático."
)


def _estilo_voz(valor: str | None) -> str | None:
    """Vazio usa o estilo padrão; 'nenhum' desliga a instrução de estilo."""
    valor = (valor or "").strip()
    if valor.lower() == "nenhum":
        return None
    return valor or ESTILO_VOZ_PADRAO


TAMANHOS_ORBE = {"pequeno": 320, "medio": 480, "médio": 480, "grande": 640}


def _tamanho_orbe(valor: str) -> tuple[int, bool]:
    """'pequeno', 'medio', 'grande', 'tela' ou um número de pixels. Devolve (lado, tela cheia)."""
    v = valor.strip().lower()
    if v == "tela":
        return TAMANHOS_ORBE["medio"], True
    if v in TAMANHOS_ORBE:
        return TAMANHOS_ORBE[v], False
    return max(160, min(960, int(v))), False


def _nivel_qualidade(valor: str | None, padrao: str, desligavel: bool = False) -> str:
    """'baixo', 'medio' ou 'alto' (aceita acento); com `desligavel`, também 'desligado' (ou nao/0)."""
    v = (valor or "").strip().lower().replace("é", "e")
    if desligavel and v in ("desligado", "nao", "não", "0", "false", "off"):
        return "desligado"
    return v if v in ("baixo", "medio", "alto") else padrao


def carregar_config() -> Config:
    load_dotenv(Path(os.environ.get("ECO_ENV_FILE") or PASTA_ECO / ".env"))
    env = os.environ.get
    return Config(
        pasta_dados=Path(env("ECO_DADOS_DIR") or Path.home() / "OneDrive" / "Documentos" / "Eco"),
        modelo_padrao=env("ECO_MODELO_PADRAO", "claude-haiku-5-5"),
        modelo_forte=env("ECO_MODELO_FORTE", "claude-sonnet-5-5"),
        esforco_padrao=env("ECO_ESFORCO_PADRAO", "low"),
        esforco_forte=env("ECO_ESFORCO_FORTE", "medium"),
        silencio_seg=float(env("ECO_SILENCIO_SEG", "1.0")),
        max_gravacao_seg=float(env("ECO_MAX_GRAVACAO_SEG", "30")),
        espera_fala_seg=float(env("ECO_ESPERA_FALA_SEG", "6")),
        limiar_voz=float(env("ECO_LIMIAR_VOZ", "0.015")),
        lembretes=env("ECO_LEMBRETES", "sim").strip().lower() not in ("nao", "não", "0", "false"),
        lembrete_antecedencia_min=int(env("ECO_LEMBRETE_MIN", "10")),
        modelo_chat=env("ECO_MODELO_CHAT", "claude-haiku-5-5"),
        esforco_chat=env("ECO_ESFORCO_CHAT", "low"),
        conversa_seg=float(env("ECO_CONVERSA_SEG", "12")),  # após responder, escuta sem "ei eco" por tanto tempo; 0 desliga
        modelo_stt=env("ECO_MODELO_STT", "gpt-4o-mini-transcribe"),
        modelo_tts=env("ECO_MODELO_TTS", "gpt-4o-mini-tts"),
        voz_tts=env("ECO_VOZ_TTS", "alloy"),
        estilo_voz=_estilo_voz(env("ECO_ESTILO_VOZ")),
        velocidade_tts=float(env("ECO_VELOCIDADE_TTS")) if env("ECO_VELOCIDADE_TTS") else None,
        visual=env("ECO_VISUAL", "sim").strip().lower() not in ("nao", "não", "0", "false"),
        orbe_tamanho=_tamanho_orbe(env("ECO_ORBE_TAMANHO", "medio"))[0],
        orbe_tela_cheia=_tamanho_orbe(env("ECO_ORBE_TAMANHO", "medio"))[1],
        orbe_tela_escala=max(0.3, min(1.2, float(env("ECO_ORBE_TELA", "0.7")))),
        orbe_tom=float(env("ECO_ORBE_TOM", "148")),
        particulas=float(env("ECO_PARTICULAS", "1.0")),
        grafo_vista="2d" if env("ECO_GRAFO_VISTA", "orbe").strip().lower() == "2d" else "orbe",
        grafo_qualidade=_nivel_qualidade(env("ECO_GRAFO_QUALIDADE", "medio"), "medio"),
        fundo=_nivel_qualidade(env("ECO_FUNDO", "medio"), "medio", desligavel=True),
        filtro_voz=env("ECO_FILTRO_VOZ", "sim").strip().lower() not in ("nao", "não", "0", "false"),
        backup_horas=float(env("ECO_BACKUP_HORAS", "24")),
        backup_manter=int(env("ECO_BACKUP_MANTER", "14")),
        preco_transcricao_min=float(env("ECO_PRECO_TRANSCRICAO_MIN", "0.003")),
        preco_fala_min=float(env("ECO_PRECO_FALA_MIN", "0.017")),
        sons=env("ECO_SONS", "sim").strip().lower() not in ("nao", "não", "0", "false"),
        modo=env("ECO_MODO", "voz"),
        porcupine_chave=env("PORCUPINE_ACCESS_KEY") or None,
        porcupine_ppn=_caminho_opcional("ECO_PORCUPINE_PPN"),
        porcupine_modelo_pt=_caminho_opcional("ECO_PORCUPINE_MODELO_PT"),
        wakeword_modelo=(env("ECO_WAKEWORD_MODELO") or "").strip() or None,
        wakeword_limiar=float(env("ECO_WAKEWORD_LIMIAR", "0.5")),
        vosk_modelo=Path(env("ECO_VOSK_MODELO") or PASTA_ECO / "modelos" / "vosk-model-small-pt-0.3"),
        vosk_frase=(env("ECO_VOSK_FRASE") or "ei eco").strip().lower(),
        max_pesquisas=int(env("ECO_MAX_PESQUISAS", "3")),
        voltas_max=int(env("ECO_VOLTAS_MAX", "8")),
        turnos_historico=int(env("ECO_TURNOS_HISTORICO", "6")),
        orcamento_usd=float(env("ECO_ORCAMENTO_USD", "9")),  # ~R$ 50; ajuste conforme o câmbio
        modelo_grafo=env("ECO_MODELO_GRAFO", "claude-haiku-5-5"),
        teto_grafo_usd=float(env("ECO_GRAFO_TETO_USD", "1")),
        ligacao_automatica=env("ECO_LIGACAO_AUTO", "sim").strip().lower() not in ("nao", "não", "0", "false"),
        ligacao_aprovar=env("ECO_LIGACAO_APROVAR", "nao").strip().lower() in ("sim", "1", "true"),
        memoria_conferir=env("ECO_MEMORIA_CONFERIR", "sim").strip().lower() not in ("nao", "não", "0", "false"),
        higiene_dias=float(env("ECO_HIGIENE_DIAS", "7")),
        grafo_limite_mb=float(env("ECO_GRAFO_LIMITE_MB", "500")),
        importar_teto_usd=float(env("ECO_IMPORTAR_TETO_USD", "2")),
    )
