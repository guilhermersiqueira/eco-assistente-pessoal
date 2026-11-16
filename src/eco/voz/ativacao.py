"""Palavra de ativação ("Ei Eco") detectada localmente pelo Porcupine, ou Enter no teclado."""

from __future__ import annotations

from pathlib import Path

from eco.config import Config


class AtivadorTeclado:
    def aguardar(self, cancelar=None) -> bool:
        input("\nAperte Enter e fale (Ctrl+C para sair)... ")
        return True

    def fechar(self) -> None:
        pass


class AtivadorPalavra:
    def __init__(self, cfg: Config) -> None:
        import pvporcupine

        opcoes = {"access_key": cfg.porcupine_chave, "keyword_paths": [str(cfg.porcupine_ppn)]}
        if cfg.porcupine_modelo_pt:
            opcoes["model_path"] = str(cfg.porcupine_modelo_pt)
        self._p = pvporcupine.create(**opcoes)

    def aguardar(self, cancelar=None) -> bool:
        """True quando ouviu a palavra; False se `cancelar()` ficou verdadeiro (ex.: Eco silenciada)."""
        import sounddevice as sd

        n = self._p.frame_length
        with sd.InputStream(samplerate=self._p.sample_rate, channels=1, dtype="int16", blocksize=n) as fluxo:
            while True:
                if cancelar and cancelar():
                    return False
                dados, _ = fluxo.read(n)
                if self._p.process(dados[:, 0].tolist()) >= 0:
                    return True

    def fechar(self) -> None:
        self._p.delete()


def resolver_modelo_wakeword(valor: str) -> Path:
    """Aceita o caminho de um arquivo .onnx treinado ou o nome de um modelo que já vem no openWakeWord."""
    import openwakeword

    caminho = Path(valor).expanduser()
    if caminho.is_file():
        return caminho
    pasta = Path(openwakeword.__file__).parent / "resources" / "models"
    prontos = sorted(p for p in pasta.glob("*.onnx") if p.stem.rsplit("_v", 1)[0] not in ("embedding_model", "melspectrogram", "silero_vad"))
    for p in prontos:
        if p.stem.rsplit("_v", 1)[0] == valor:
            return p
    nomes = ", ".join(p.stem.rsplit("_v", 1)[0] for p in prontos)
    raise ValueError(f"Modelo de palavra de ativação '{valor}' não encontrado. Use um caminho .onnx ou um destes nomes: {nomes}.")


class AtivadorOpenWakeWord:
    """Palavra de ativação detectada localmente pelo openWakeWord (sem conta nem chave)."""

    TAXA = 16_000
    BLOCO = 1280  # 80 ms, o tamanho que o openWakeWord espera

    def __init__(self, cfg: Config) -> None:
        from openwakeword.model import Model

        self._modelo = Model(wakeword_model_paths=[str(resolver_modelo_wakeword(cfg.wakeword_modelo or ""))])
        self._limiar = cfg.wakeword_limiar

    def aguardar(self, cancelar=None) -> bool:
        import sounddevice as sd

        with sd.InputStream(samplerate=self.TAXA, channels=1, dtype="int16", blocksize=self.BLOCO) as fluxo:
            while True:
                if cancelar and cancelar():
                    return False
                dados, _ = fluxo.read(self.BLOCO)
                pontos = self._modelo.predict(dados[:, 0])
                if max(pontos.values(), default=0.0) >= self._limiar:
                    self._modelo.reset()  # limpa o histórico para o mesmo som não disparar de novo
                    return True

    def fechar(self) -> None:
        pass


def frase_detectada(texto: str, frase: str) -> bool:
    """True se o texto reconhecido tem a palavra-chave da frase de ativação (a última: 'eco' em 'ei eco').
    O 'ei' não entra: o modelo pequeno do Vosk não o reconhece, e 'eco' sozinha é rara na fala comum."""
    palavras = frase.lower().split()
    return bool(palavras) and palavras[-1] in texto.lower().split()


class AtivadorVosk:
    """Palavra de ativação por reconhecimento offline (Vosk) restrito à frase, sem treino nenhum."""

    TAXA = 16_000
    BLOCO = 4000  # 250 ms

    def __init__(self, cfg: Config) -> None:
        import json

        from vosk import KaldiRecognizer, Model, SetLogLevel

        if not cfg.vosk_modelo.is_dir():
            raise FileNotFoundError(f"Modelo Vosk não encontrado em {cfg.vosk_modelo}. Veja o CLAUDE.md para baixar.")
        SetLogLevel(-1)
        self._modelo = Model(str(cfg.vosk_modelo))
        self._frase = cfg.vosk_frase
        palavra = cfg.vosk_frase.split()[-1]
        self._gramatica = json.dumps(list(dict.fromkeys([cfg.vosk_frase, palavra, "[unk]"])))
        self._KaldiRecognizer = KaldiRecognizer

    def aguardar(self, cancelar=None) -> bool:
        import json

        import sounddevice as sd

        rec = self._KaldiRecognizer(self._modelo, self.TAXA, self._gramatica)
        with sd.RawInputStream(samplerate=self.TAXA, channels=1, dtype="int16", blocksize=self.BLOCO) as fluxo:
            while True:
                if cancelar and cancelar():
                    return False
                dados, _ = fluxo.read(self.BLOCO)
                if rec.AcceptWaveform(bytes(dados)):
                    texto = json.loads(rec.Result()).get("text", "")
                else:
                    texto = json.loads(rec.PartialResult()).get("partial", "")
                if frase_detectada(texto, self._frase):
                    return True

    def fechar(self) -> None:
        pass


def criar_ativador(cfg: Config) -> AtivadorTeclado | AtivadorPalavra | AtivadorOpenWakeWord | AtivadorVosk:
    if cfg.modo == "teclado":
        return AtivadorTeclado()
    if (cfg.wakeword_modelo or "").lower() == "vosk":
        return AtivadorVosk(cfg)
    if cfg.wakeword_modelo:
        return AtivadorOpenWakeWord(cfg)
    if not (cfg.porcupine_chave and cfg.porcupine_ppn):
        print("Palavra de ativação não configurada (ECO_WAKEWORD_MODELO, ou PORCUPINE_ACCESS_KEY e ECO_PORCUPINE_PPN): usando o teclado.")
        return AtivadorTeclado()
    return AtivadorPalavra(cfg)


def testar_palavra(cfg: Config) -> None:
    """Diagnóstico: mostra o microfone, o volume e a pontuação da palavra ao vivo. Rode com
    `py -m uv run python -m eco.voz.ativacao`. Não usa nenhuma API nem gasta nada."""
    import time

    import numpy as np
    import sounddevice as sd
    from dataclasses import replace

    if not cfg.wakeword_modelo:
        cfg = replace(cfg, wakeword_modelo="hey_jarvis")
    ativador = AtivadorOpenWakeWord(cfg)
    print("Microfone padrão:", sd.query_devices(kind="input")["name"])
    print(f"Modelo: {cfg.wakeword_modelo} | limiar: {cfg.wakeword_limiar}")
    print("Fale a palavra de ativação. A linha mostra o volume e a MAIOR pontuação dos últimos 0,5 s. Ctrl+C sai.\n")
    inicio, melhor, volume = time.time(), 0.0, 0.0
    try:
        with sd.InputStream(samplerate=ativador.TAXA, channels=1, dtype="int16", blocksize=ativador.BLOCO) as fluxo:
            while True:
                dados, _ = fluxo.read(ativador.BLOCO)
                volume = max(volume, float(np.sqrt(np.mean((dados.astype(np.float32) / 32768.0) ** 2))))
                pontos = ativador._modelo.predict(dados[:, 0])
                ponto = max(pontos.values(), default=0.0)
                melhor = max(melhor, ponto)
                if ponto >= ativador._limiar:
                    print(f"\n>>> DETECTADO (pontuação {ponto:.2f})")
                    ativador._modelo.reset()
                if time.time() - inicio >= 0.5:
                    barra = "#" * min(30, int(volume * 300))
                    print(f"volume {barra:<30} | pontuação máx {melhor:.2f}   ", end="\r")
                    inicio, melhor, volume = time.time(), 0.0, 0.0
    except KeyboardInterrupt:
        print("\nTeste encerrado.")


if __name__ == "__main__":
    from eco.config import carregar_config

    testar_palavra(carregar_config())
