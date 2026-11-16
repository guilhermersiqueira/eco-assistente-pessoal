# Eco

Assistente pessoal por voz e texto, em português do Brasil, que roda no seu computador. Ela conversa (Claude), ouve e fala
(OpenAI), lê e escreve e-mails, agenda e tarefas do Google, guarda notas e metas e mantém uma **memória própria**
(um grafo de ideias no seu disco) que ela consulta nas conversas. Tem uma janela com um orbe animado, painel, bloco de notas
e uma aba de grafo para você revisar e gerenciar o que ela sabe.

> Feita para **Windows 10/11**. Os dados e as chaves ficam só na sua máquina (`%USERPROFILE%\.eco`); nada é enviado a um
> servidor do projeto. Você paga o uso das suas próprias contas de API.

## O que você precisa

- Windows, com microfone e caixas de som (para o modo voz; o modo texto dispensa).
- [Python 3.14](https://www.python.org/downloads/) e o gerenciador [uv](https://docs.astral.sh/uv/).
- Uma chave da **Anthropic** (obrigatória) e uma da **OpenAI** (só para voz: transcrição e fala).
- Opcional: uma conta Google (Gmail, Agenda e Tasks).

## Instalação

```bash
git clone <URL-DO-REPOSITORIO> eco
cd eco
uv sync
```

Crie a pasta `%USERPROFILE%\.eco`, copie o `.env.example` para `%USERPROFILE%\.eco\.env` e preencha ao menos
`ANTHROPIC_API_KEY`. **Nunca** coloque chaves reais no repositório (o `.gitignore` já ignora `.env`).

## Uso

```bash
uv run eco texto               # conversar digitando
uv run eco                     # modo voz (precisa da chave da OpenAI)
uv run eco orbe                # demonstração do orbe, sem API
uv run eco gasto               # gasto estimado do mês
uv run eco backup              # copia, confere e testa a restauração do banco
uv run eco modelo baixar       # baixa o modelo local de busca por semelhança (~490 MB)
uv run eco autorizar-google    # autoriza Gmail, Agenda e Tasks (uma vez)
```

### Google (opcional)

Cada pessoa usa as próprias credenciais: no Google Cloud Console, crie um projeto, ative as APIs do Gmail, Calendar e
Tasks, crie um "ID do cliente OAuth" do tipo *Aplicativo para computador*, baixe o JSON e salve como
`%USERPROFILE%\.eco\google_credentials.json`. Depois rode `uv run eco autorizar-google`.

### Palavra de ativação (opcional)

Sem configuração, a Eco usa o teclado (Enter para falar). Para chamá-la por voz há três opções: openWakeWord (sem conta),
Vosk (offline, sem treino) ou Picovoice Porcupine. Veja os comentários do `.env.example`.

## Memória e privacidade

- A memória (`eco.db` e `memoria.md`) fica em `%USERPROFILE%\.eco` ou na pasta de `ECO_DADOS_DIR`. Faça backup com
  `eco backup`; o banco também é copiado sozinho todo dia.
- Para as respostas, trechos da conversa e da memória são enviados à Anthropic (e o áudio à OpenAI). Leia as políticas dos
  dois serviços.
- A Eco pede o seu "sim" antes de ações com efeito externo (enviar e-mail, criar ou apagar eventos).
- **Importar conversas do claude.ai:** na aba Grafo > Gerenciar, a Eco lê o export das suas conversas, propõe fatos e só
  guarda os que você aprovar um a um.

## Desenvolvimento

```bash
uv run pytest -q
```

Os testes não chamam APIs nem tocam nos seus dados reais. Os scripts `tests/avaliacao_*.py` com `--gastar` usam a API de
verdade e custam centavos. O manual de uso para o usuário final está em `src/eco/ajuda.md`.

## Licença

[MIT](LICENSE).
