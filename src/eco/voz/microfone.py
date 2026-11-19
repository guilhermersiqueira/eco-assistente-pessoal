"""Aviso de microfone no mudo.

Com o microfone no mudo do Windows (tecla de microfone do notebook, ícone de som), a Eco não ouve o "Ei Eco" e
parecia simplesmente quebrada (18/11/2026). Aqui uma thread pergunta ao Windows, a cada poucos segundos, se o microfone
padrão está no mudo ou com o volume zerado, e avisa uma vez quando fica assim e outra quando volta.

Por que perguntar ao Windows e não olhar o som: o microfone do dono tem supressão de ruído (Intel Smart Sound), que
entrega zeros nos momentos quietos mesmo ligado; pelo som, uma sala silenciosa pareceria mudo.

`estado_do_windows` fala com o Core Audio do Windows por COM, só com ctypes (sem dependência nova).
`VigiaDoMicrofone` é pura: recebe o estado e diz se houve mudança que merece aviso (testável sem Windows).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

INTERVALO_S = 5.0
MUDO, VOLUME_ZERO, OK = "mudo", "volume_zero", "ok"

AVISO_TITULO = "A Eco não consegue ouvir"
AVISOS = {
    MUDO: "O microfone está no mudo. Tire o mudo (tecla de microfone do teclado ou ícone de som perto do relógio) para "
    "chamar a Eco.",
    VOLUME_ZERO: "O volume do microfone está em zero. Aumente em Configurações → Sistema → Som → Entrada para chamar a Eco.",
}
LEGENDAS = {MUDO: "Microfone no mudo: não consigo ouvir o “Ei Eco”.", VOLUME_ZERO: "Microfone com volume zero: não consigo ouvir."}
LEGENDA_VOLTOU = "Microfone ligado de novo. Pode me chamar."


def estado_do_windows() -> str | None:
    """MUDO, VOLUME_ZERO ou OK para o microfone padrão do Windows; None se não deu para saber (sem microfone, fora do
    Windows, erro do COM)."""
    try:
        import ctypes
        import uuid
        from ctypes import HRESULT, POINTER, byref, c_float, c_int, c_void_p
    except Exception:
        return None
    try:
        ole32 = ctypes.windll.ole32
    except AttributeError:  # fora do Windows
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("bytes", ctypes.c_ubyte * 16)]

    def guid(texto: str) -> GUID:
        g = GUID()
        ctypes.memmove(byref(g), uuid.UUID(texto).bytes_le, 16)
        return g

    def metodo(obj: c_void_p, indice: int, *argtypes):
        tabela = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        funcao = ctypes.WINFUNCTYPE(HRESULT, c_void_p, *argtypes)(tabela[indice])
        return lambda *a: funcao(obj, *a)

    ole32.CoInitializeEx(None, 0)  # já iniciado nesta thread devolve S_FALSE; em outro modo, segue assim mesmo
    enumerador, aparelho, volume = c_void_p(), c_void_p(), c_void_p()
    try:
        ole32.CoCreateInstance(
            byref(guid("BCDE0395-E52F-467C-8E3D-C4579291692E")), None, 23,  # MMDeviceEnumerator, CLSCTX_ALL
            byref(guid("A95664D2-9614-4F35-A746-DE8DB63617E6")), byref(enumerador),  # IMMDeviceEnumerator
        )
        if not enumerador:
            return None
        # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eCapture=1, eConsole=0)
        metodo(enumerador, 4, c_int, c_int, POINTER(c_void_p))(1, 0, byref(aparelho))
        # IMMDevice::Activate(IAudioEndpointVolume)
        iid = guid("5CDF2C82-841E-4546-9722-0CF74078229A")
        metodo(aparelho, 3, POINTER(GUID), c_int, c_void_p, POINTER(c_void_p))(byref(iid), 23, None, byref(volume))
        mudo, nivel = c_int(0), c_float(1.0)
        metodo(volume, 15, POINTER(c_int))(byref(mudo))  # IAudioEndpointVolume::GetMute
        metodo(volume, 9, POINTER(c_float))(byref(nivel))  # IAudioEndpointVolume::GetMasterVolumeLevelScalar
    except OSError:  # sem microfone padrão (E_NOTFOUND) ou o Windows recusou: não dá para saber
        return None
    finally:
        for obj in (volume, aparelho, enumerador):
            if obj:
                try:
                    metodo(obj, 2)()  # Release
                except Exception:
                    pass
    if mudo.value:
        return MUDO
    if nivel.value <= 0.005:
        return VOLUME_ZERO
    return OK


class VigiaDoMicrofone:
    """Lembra o último estado e diz quando avisar: ao ficar mudo (ou volume zero) e ao voltar. Um aviso por vez."""

    def __init__(self) -> None:
        self.estado: str | None = None
        self.avisado: str | None = None  # o problema já avisado e ainda não resolvido

    def observar(self, estado: str | None) -> tuple[str, str] | None:
        """("problema", MUDO/VOLUME_ZERO), ("voltou", OK) ou None. Estado None (não deu para saber) não muda nada."""
        if estado is None:
            return None
        self.estado = estado
        if estado in AVISOS:
            if self.avisado != estado:
                self.avisado = estado
                return ("problema", estado)
            return None
        if self.avisado is not None:
            self.avisado = None
            return ("voltou", OK)
        return None


def vigiar(painel, consultar: Callable[[], str | None] = estado_do_windows, intervalo: float = INTERVALO_S,
           parar: threading.Event | None = None) -> threading.Thread:
    """Thread que consulta o microfone e avisa pelo painel (balão do Windows e legenda da orbe) e pelo log."""
    vigia = VigiaDoMicrofone()

    def laco() -> None:
        while parar is None or not parar.is_set():
            try:
                mudanca = vigia.observar(consultar())
            except Exception:
                mudanca = None
            if mudanca is not None:
                tipo, estado = mudanca
                try:
                    if tipo == "problema":
                        print(f"[microfone] {LEGENDAS[estado]}")
                        painel.notificar(AVISO_TITULO, AVISOS[estado])
                        painel.legenda(LEGENDAS[estado], False)
                    else:
                        print("[microfone] ligado de novo")
                        painel.legenda(LEGENDA_VOLTOU, False)
                except Exception:  # a janela pode estar fechando
                    pass
            if parar is not None:
                parar.wait(intervalo)
            else:
                time.sleep(intervalo)

    t = threading.Thread(target=laco, daemon=True, name="eco-microfone")
    t.vigia = vigia  # para os testes e para quem quiser saber o último estado
    t.start()
    return t
