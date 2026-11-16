"""Teste de login do Outlook de trabalho (Microsoft 365) com o app que você registrou no Entra ID.

Uso:  py -m uv run --with msal python scripts/testar_outlook.py <ID-DO-APLICATIVO>

Abre o navegador para você entrar com o e-mail de trabalho. Só LÊ (permissão Mail.Read): confirma se a empresa deixa o app
entrar e mostra quantas mensagens a Graph devolveu, sem imprimir remetente, assunto nem texto. Nada é gravado em disco
(o token fica só na memória deste teste).
"""

from __future__ import annotations

import json
import sys
import urllib.request

ESCOPOS = ["https://graph.microsoft.com/Mail.Read"]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    import msal

    app = msal.PublicClientApplication(sys.argv[1], authority="https://login.microsoftonline.com/organizations")
    resultado = app.acquire_token_interactive(ESCOPOS, prompt="select_account")
    if "access_token" not in resultado:
        print("FALHOU:", resultado.get("error"), "-", (resultado.get("error_description") or "").splitlines()[0])
        print("Se aparecer 'AADSTS65001' ou 'AADSTS90094' ou 'precisa de aprovação do administrador', a empresa bloqueou o app.")
        return 1
    conta = resultado.get("id_token_claims", {})
    print("Login ok. Domínio da conta:", (conta.get("preferred_username") or "?").split("@")[-1], "| tenant:", conta.get("tid", "?")[:8] + "…")
    pedido = urllib.request.Request(
        "https://graph.microsoft.com/v1.0/me/messages?$top=5&$select=id",
        headers={"Authorization": "Bearer " + resultado["access_token"]},
    )
    with urllib.request.urlopen(pedido, timeout=20) as r:
        n = len(json.load(r).get("value", []))
    print(f"Leitura ok: a Graph devolveu {n} mensagem(ns) da caixa de entrada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
