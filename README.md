# LinkFlow — MVP local

Plataforma para organizar a busca de emprego: perfil profissional, importação de currículo em PDF, cadastro de vagas,
análise de compatibilidade com IA, currículo/carta direcionados (com exportação em PDF) e central de candidaturas.

Roda 100% no seu computador. Dados em SQLite (`data/linkflow.db`).

## Como iniciar

Requisitos: Python 3.10+.

```bash
cd linkflow-ai
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env   (opcional)
python run.py
```

Abra **http://127.0.0.1:5000**, clique em *Criar conta* e comece pelo perfil (ou *Importar currículo*).
Para encerrar: `Ctrl+C`. Os dados permanecem ao reiniciar.

Testes automatizados (não precisam de internet nem de chave de IA):

```bash
python tests/test_e2e.py
python tests/test_ai_providers.py
python tests/test_mail.py
python tests/test_apply.py
python tests/test_portability.py
python tests/test_quick.py
```

## Ranking, pacote e candidatura

- **Ranking:** lista as vagas pela compatibilidade com o seu perfil. “Analisar vagas sem análise” calcula o índice das que faltam
  (com IA real, em lotes de 3 por clique para limitar custo e tempo).
- **Montar pacote:** com um clique gera currículo e carta direcionados à vaga (só com dados do seu perfil), salva em Documentos e marca
  a vaga como “Pronta para candidatura”.
- **Enviar candidatura:** na página da candidatura você revisa a mensagem e os anexos (PDFs) e envia pelo SMTP da sua própria conta.
  Um destinatário por envio, só após marcar a confirmação, bloqueio de reenvio duplicado em 24h e limite diário
  (`MAX_SENDS_PER_DAY`, padrão 10). Não há envio automático nem em lote.
- **Candidatura automática em LinkedIn/portais não existe** (sem API para candidatos e proibido pelos termos). Nesses casos use o botão
  “Abrir vaga original” e anexe os PDFs gerados.

## Currículo por link da vaga

Em **Currículo por link**, cole o link de uma vaga pública: o LinkFlow lê a página (dados estruturados da vaga ou texto), cadastra a vaga
e monta currículo e carta direcionados, usando só os dados do seu perfil. Você revisa, edita, baixa o PDF ou envia por e-mail.
Uma requisição por link informado, com bloqueio de endereços internos. LinkedIn e sites com login não são lidos automaticamente:
nesses casos o app pede para você colar a descrição. Cargo e empresa lidos automaticamente podem ser corrigidos em “Editar vaga”.

## Publicar na Vercel (produção)

O app usa Postgres quando `DATABASE_URL` existe (na Vercel use a integração **Neon** do Marketplace, que preenche a variável sozinha)
e SQLite quando não existe. As tabelas são criadas automaticamente no primeiro acesso.

Variáveis no projeto da Vercel: `SECRET_KEY` (obrigatória, string aleatória longa), `DATABASE_URL`, `ALLOWED_EMAILS`
(e-mails que podem criar conta; vazio = qualquer pessoa) e, se quiser IA real, `ANTHROPIC_API_KEY` ou `OPENAI_API_KEY`.
Não troque a `SECRET_KEY` depois de conectar um e-mail: a senha de app salva é criptografada com ela.

Na Vercel o disco é efêmero: PDFs enviados são lidos e descartados (só o texto extraído vai para o banco) e o limite de upload é 4 MB.

## Modo claro / escuro

Botão **Claro/Escuro** no menu lateral (e no canto da tela de login). A escolha fica salva no navegador; o padrão é escuro.

## E-mails das empresas (IMAP, somente leitura)

Em **E-mails**, conecte sua caixa (Gmail, Outlook, Yahoo, iCloud ou outro servidor IMAP) usando uma **senha de aplicativo**
(a senha normal não funciona; instruções na própria tela). Depois clique em **Sincronizar agora**.

- Lê os últimos 90 dias sem apagar, mover, marcar como lido ou enviar nada (pasta aberta em modo readonly, `BODY.PEEK`).
- Guarda **só** as mensagens que casam com alguma vaga sua: nome do remetente, domínio (do e-mail ou do link da vaga),
  assunto ou, em e-mails de LinkedIn/Gupy/Indeed etc., o texto. O resto da caixa é descartado.
- As mensagens aparecem na caixa do LinkFlow, na página de cada vaga e num contador de não lidas no menu.
  O vínculo pode ser corrigido manualmente. O status da vaga **não muda sozinho**: você decide.
- O corpo é exibido como texto puro (sem links ativos nem imagens remotas).
- A senha fica criptografada (Fernet) com chave derivada da `SECRET_KEY` local (`data/.secret_key`). Isso protege contra quem só
  lê o arquivo do banco; quem tiver a pasta `data/` inteira consegue decifrar. Revogue a senha de app no provedor se perder o computador.
- Limites: o casamento é heurístico (nomes de empresa muito genéricos podem errar), e só a primeira conexão real com o seu provedor
  confirma que está tudo certo (os testes usam um servidor IMAP simulado).

## Configurar a API de IA

Edite o arquivo `.env` (copiado de `.env.example`) e preencha **uma** das chaves, depois reinicie `python run.py`:

| Provedor | Variáveis |
|---|---|
| Anthropic (Claude) | `ANTHROPIC_API_KEY=sk-ant-...` e, opcionalmente, `ANTHROPIC_MODEL=` |
| OpenAI | `OPENAI_API_KEY=sk-...` e, opcionalmente, `OPENAI_MODEL=` |
| Gratuito (Groq, Gemini, OpenRouter) | `OPENAI_API_KEY=<chave>`, `OPENAI_BASE_URL=<url base compatível>` e `OPENAI_MODEL=<modelo>` |

- Se as duas estiverem preenchidas, a Anthropic é usada.
- O provedor ativo aparece na barra lateral. Sem chave, aparece **“Modo demonstrativo (simulado)”**.
- Os prompts proíbem a IA de inventar experiências/qualificações (`linkflow/services/ai.py`).
- Se a chamada à API falhar (chave inválida, sem rede, resposta malformada), o app **não esconde o erro**: mostra
  o motivo e entrega um resultado marcado como **SIMULADO**.

### O que é o modo demonstrativo

Sem chave, não há IA. Ele compara palavras-chave entre o seu perfil e a descrição da vaga (vocabulário embutido de
~150 termos de tecnologia/negócios + itens em lista da descrição). Todo resultado é rotulado **SIMULADO**.
Currículo e carta saem de modelo fixo, usando **somente** dados que você cadastrou. Não é uma análise semântica.

## Estrutura

```
run.py                      ponto de entrada local
app.py / vercel.json        ponto de entrada e configuração da Vercel
linkflow/
  __init__.py               fábrica da app, tratamento de erros
  config.py  db.py  security.py
  blueprints/               auth · main (dashboard) · profile · jobs · apply · documents · mail
  services/
    mailbox.py              IMAP somente leitura + vínculo e-mail ↔ vaga
    ai.py                   provedores Anthropic/OpenAI + modo demonstrativo
    resume_parser.py        PDF → texto → perfil (heurístico, sem IA)
    pdf_export.py           texto → PDF (reportlab)
    profile_data.py         leitura/escrita do perfil
templates/  static/         interface (CSS/JS próprios, gráficos em SVG)
tests/                      testes ponta a ponta
```

## O que funciona hoje

- Cadastro/login (senha com hash, sessão, proteção CSRF); dados isolados por usuário.
- Dashboard: totais, candidaturas, pendentes, entrevistas, gráfico semanal, funil por status, atividades recentes.
- Perfil completo (pessoais, experiências, formação, cursos, habilidades, pretensão, modalidade), com persistência.
- Importação de PDF com texto selecionável → formulário preenchido → você revisa/edita → salva.
- Vagas: cadastro, edição, exclusão, busca e filtros, link original (abre em nova aba).
- Análise de compatibilidade (0–100%, atendidos, não encontrados, pontos fortes/melhoria, sugestões).
- Geração de currículo e carta, edição antes de salvar, exportação em PDF.
- E-mails das empresas ligados às vagas (IMAP, somente leitura) e tema claro/escuro.
- Central de candidaturas com os 7 status; alteração por seletor ou arrastando cartões; histórico por vaga.

## Limitações conhecidas

- **Análise e geração por IA real dependem da sua chave.** O código dos provedores foi testado apenas com respostas
  HTTP simuladas, não contra as APIs reais (não havia chave nem rede no ambiente de desenvolvimento). Teste com a sua chave.
- **Importação de PDF:** a extração é por regras. Funciona melhor em currículos de coluna única com títulos de seção
  comuns (Experiência, Formação, Cursos, Habilidades). Layouts em colunas/tabelas podem sair bagunçados — por isso
  existe a etapa de revisão. PDFs escaneados (imagem) não são suportados (sem OCR).
- **PDF exportado:** usa fontes padrão (Helvetica), então caracteres fora do alfabeto latino podem não aparecer.
- **LinkedIn:** apenas armazena o link da vaga e abre o original. Não há login, scraping nem envio automático
  de candidaturas (não existe API oficial pública para isso). Você se candidata manualmente no site.
- Sem recuperação de senha. Há limite de tentativas de login, mas não há migrações versionadas de banco (só criação de tabelas).
