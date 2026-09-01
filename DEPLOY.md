# Deploy — Church CRM

Guia rápido para colocar em produção. Funciona em qualquer VPS Linux
(DigitalOcean, Contabo etc.) ou no PythonAnywhere — os dois caminhos estão
aqui.

## 1. Checklist antes de ir ao ar

- [ ] `SECRET_KEY` trocada por uma chave nova e secreta (`.env`, não commitada)
- [ ] `DEBUG=False`
- [ ] `ALLOWED_HOSTS` com o domínio real
- [ ] Banco de dados: SQLite serve para uma igreja pequena/média; para mais
      tráfego, configure PostgreSQL (`DB_ENGINE=postgresql` no `.env`)
- [ ] E-mail configurado (`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`) — sem
      isso, "esqueci minha senha" não envia nada de verdade
- [ ] `python manage.py check --deploy` sem avisos que você não entenda
- [ ] Backup rodando (`manage.py backup_banco`, ver seção 4)
- [ ] Multi-tenência: pelo menos uma `Church` criada (ver seção 1.1) — o
      sistema não funciona com zero igrejas cadastradas

## 1.1. Criando uma igreja (multi-tenência)

Um único deploy atende várias igrejas — cada uma é uma linha em `Church`,
isolada automaticamente do resto (ver `README.md`, seção "Multi-tenência").
**Existe cadastro público self-service** em `/cadastro-igreja/` (Fase 2)
— nasce em trial de 30 dias, sem precisar de você. Pra criar uma manualmente
mesmo assim (ex.: conta de teste, migração de dado existente), pelo Django
admin (`/admin/core/church/add/`) ou via shell:

```bash
venv/bin/python manage.py shell -c "
from core.models import Church
from accounts.models import User
church = Church.objects.create(name='Igreja Nova')
User.objects.create_user(username='pastor-novo', password='TROQUE-ISSO', role=User.Role.PASTOR, church=church)
"
```

`slug` e `whatsapp_instance` são gerados sozinhos a partir do nome no
`save()`. Um usuário **sem** igreja (`church=None`, `is_staff=True`) é o
dono da plataforma — vê e gerencia TODAS as igrejas pelo Django admin,
sem filtro nenhum; não crie isso por engano numa conta pensada pra ser de
uma igreja só.

Uma igreja criada pelo cadastro público (ou por você, se preencher
`trial_expira_em`) vence o trial sozinha via `manage.py expirar_trials`
(cron diário — ver seção 5 abaixo) — sem esse cron agendado, o trial
nunca vence automaticamente, só se você suspender manualmente.

## 2. VPS genérico (Ubuntu + Nginx + Gunicorn)

```bash
# No servidor
sudo apt install python3-venv nginx
git clone <seu-repositorio> church-crm
cd church-crm
python3 -m venv venv
venv/bin/pip install -r requirements.txt gunicorn
cp .env.example .env   # edite com os valores reais
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py createsuperuser
```

Rode com Gunicorn atrás de um Nginx fazendo proxy reverso (Nginx cuida do
HTTPS via Let's Encrypt/certbot — é por isso que `SECURE_SSL_REDIRECT`
fica desligado por padrão no `settings.py`, o proxy já força HTTPS antes
de chegar no Django):

```bash
venv/bin/gunicorn church_crm.wsgi:application --bind 127.0.0.1:8000
```

Sirva `/static/` e `/media/` direto pelo Nginx (mais rápido que passar
pelo Django) ou confie no Whitenoise (já configurado, funciona sem Nginx
servir estáticos, só um pouco mais lento).

Systemd (opcional, pra manter o Gunicorn rodando sozinho) e configuração
de Nginx seguem o padrão de qualquer app Django — nada específico deste
projeto.

## 3. PythonAnywhere (mais simples, sem servidor pra administrar)

1. Suba o código (git clone ou upload manual)
2. Crie um virtualenv e instale `requirements.txt`
3. Na aba **Web**, aponte o WSGI file pra `church_crm.wsgi.application`
4. Configure `ALLOWED_HOSTS` com `seuusuario.pythonanywhere.com`
5. Mapeie `/static/` e `/media/` nas configurações de "Static files" da aba Web
6. PythonAnywhere já força HTTPS na borda — não precisa mexer em
   `SECURE_SSL_REDIRECT`

## 4. Backup

```bash
python manage.py backup_banco --keep 14
```

Copia `db.sqlite3` **e** zipa a pasta `media/` (fotos de pessoas, capas de
evento, anexos de formulário, comprovantes de doação) pra `backups/`, com
timestamp, mantendo só os 14 mais recentes de cada um — um restore do
banco sozinho, sem a mídia junto, deixaria tudo isso com link quebrado.
Agende via cron (`crontab -e`):

```
0 3 * * * cd /caminho/church-crm && venv/bin/python manage.py backup_banco
```

Em Postgres, use `pg_dump` diretamente pro banco (`--no-media` pula a
parte de mídia se quiser separar os dois; sem a flag, `backup_banco`
recusa fazer o backup do banco em Postgres mas ainda cuida da mídia).

## 5. WhatsApp — fila de mensagens e lembretes

Nada é enviado na hora que uma campanha/mensagem avulsa/lembrete é criado
— tudo vira uma `WhatsAppMessage` PENDING. Quem manda de verdade é o
`processar_fila_whatsapp` — **processa a fila de TODAS as igrejas
cadastradas, uma de cada vez**, um por um dentro de cada igreja,
esperando o intervalo configurado (`Church.whatsapp_send_interval_seconds`,
padrão 6s, por igreja) entre cada envio. **Esse intervalo importa de
verdade** — mandar uma campanha inteira de uma vez, sem pausa, é o jeito
mais comum de um número real ser marcado como spam e banido pelo
WhatsApp. Um único cron desses cobre todas as igrejas do deploy — não
precisa de um cron por igreja.

```
# processa a fila de TODAS as igrejas a cada minuto (pega o que estiver pendente/vencido)
* * * * * cd /caminho/church-crm && venv/bin/python manage.py processar_fila_whatsapp

# enfileira os lembretes do dia de TODAS as igrejas (aniversário + reunião de célula), 1x/dia
0 8 * * * cd /caminho/church-crm && venv/bin/python manage.py enviar_lembretes

# checa se o WhatsApp de CADA igreja continua conectado a cada 20min —
# avisa por e-mail (Church.admin_alert_emails, em Configurações) se
# caiu, uma vez só por queda, por igreja
*/20 * * * * cd /caminho/church-crm && venv/bin/python manage.py verificar_conexao_whatsapp

# suspende quem passou do trial de 30 dias sem assinar (Fase 2), 1x/dia
0 3 * * * cd /caminho/church-crm && venv/bin/python manage.py expirar_trials
```

Sem `EVOLUTION_API_URL`/`EVOLUTION_API_KEY` no `.env` da plataforma
(seção 5.1) — ou sem a instância daquela igreja criada ainda — a fila só
imprime cada mensagem no log do cron — nada é enviado de verdade, mas o
fluxo continua 100% testável. Mensagens que falham são reenviadas
automaticamente até `whatsapp_max_retries` tentativas (configurável por
igreja em `/configuracoes/`).

### 5.1. Self-hosting da Evolution API no Contabo

A Evolution API é open-source e conecta a um número de WhatsApp real via
QR code (sem aprovação de conta comercial da Meta). Rodando num VPS
Contabo separado (ou na mesma máquina do Django, em containers distintos):

```bash
# no VPS Contabo, com Docker instalado
docker run -d \
  --name evolution-api \
  -p 8080:8080 \
  -e AUTHENTICATION_API_KEY=troque-por-uma-chave-forte \
  -e DATABASE_ENABLED=false \
  -v evolution_instances:/evolution/instances \
  atendai/evolution-api:latest
```

Coloque um Nginx + Let's Encrypt na frente pra expor com HTTPS (ex.:
`https://evolution.suaigreja.com`).

Essa parte é **só do dono do sistema** — a igreja (Pastor/Admin/Líder)
nunca vê nem preenche nada disso; `ChurchConfigForm` (o form por trás de
`/configuracoes/`) nem inclui esses campos. O servidor é **único,
compartilhado por TODAS as igrejas** do deploy — configure no `.env` da
plataforma, uma vez só:

```
EVOLUTION_API_URL=https://evolution.suaigreja.com
EVOLUTION_API_KEY=o-mesmo-valor-de-AUTHENTICATION_API_KEY-acima
```

Cada igreja só tem sua PRÓPRIA instância nesse servidor compartilhado —
`Church.whatsapp_instance` é gerado sozinho a partir do slug da igreja
(ex.: `igreja-igreja-nova`), não precisa digitar nada. Pra criar a
instância da primeira vez, use os botões "Criar/recriar instância" e
"Ver QR code" no painel de CADA igreja no Django admin
(`/admin/core/church/<id>/change/`, exige `is_staff` — a Evolution
devolve o token da instância, que o admin já salva sozinho em
`whatsapp_instance_token`). A partir daí, o dia a dia da igreja é só a
tela `/mensagens/whatsapp/`: um botão **Conectar** (mostra o QR code na
hora — reaproveita a instância já criada) e **Desconectar**, sem nenhum
campo técnico visível. Se o número cair (ex.: trocou de aparelho), a
igreja mesma resolve clicando em Conectar de novo — só precisa voltar ao
dono/admin se a própria instância no servidor Evolution precisar ser
recriada do zero.

### 5.2. Confirmação de entrega (webhook)

Isso já é automático: ao clicar em "Criar/recriar instância" (Django
admin), `core.whatsapp.criar_instancia()` gera sozinho um
`Church.whatsapp_webhook_secret` (se a igreja ainda não tiver um) e
já embute a configuração do webhook — URL própria
(`https://seudominio.com/mensagens/webhook/evolution/`, calculada de
`request.build_absolute_uri`) + o evento `messages.update` + o cabeçalho
`X-Webhook-Secret` — na própria chamada de criação da instância. Não
precisa mexer no painel da Evolution API manualmente. O segredo é único
por igreja porque a URL do webhook é a mesma pra todas — é ele que diz
de qual igreja é o evento; a Evolution API não assina os webhooks por
conta própria, então sem um segredo que bata com alguma igreja, toda
chamada é rejeitada.

**Nota de honestidade**: o formato da resposta da API (criação de
instância, QR code, status, e o payload do webhook) foi **confirmado ao
vivo** contra um servidor Evolution API v2.3.7 real (deploy próprio via
Docker Compose numa Contabo VPS, ver histórico do projeto) — criação,
QR code e status bateram com o parsing já existente sem precisar de
ajuste nenhum; só o payload do webhook (`messages.update`) veio num
formato diferente do documentado (`data.keyId`/`data.status` direto, não
aninhado em `key`/`update`) e foi corrigido em
`notifications/views.py::WhatsAppWebhookView` depois de capturar um
evento real. Se sua versão do servidor responder diferente ainda assim,
a tela de conexão mostra a resposta crua da API na mensagem de aviso pra
você ajustar `core/whatsapp.py`/`notifications/views.py::WhatsAppWebhookView`
se precisar.

### 5.3. Cobrança automática de assinatura (Fase 4)

Diferente do Mercado Pago de CADA igreja (`Church.mercadopago_access_token`,
usado só pra receber pagamento de evento/doação daquela igreja), a
cobrança de assinatura usa a conta do **DONO da plataforma** — uma só,
pra todas as igrejas. Crie um Access Token de produção no
[painel de desenvolvedor do Mercado Pago](https://www.mercadopago.com.br/developers/panel)
e preencha no `.env` da plataforma:

```
PLATFORM_MERCADOPAGO_ACCESS_TOKEN=seu-token-de-producao
```

Sem isso configurado, `/assinatura/` continua mostrando os planos, mas
tentar assinar mostra erro — o resto do sistema funciona normal
(cobrança continua manual, direto no Django admin em `Church.status`/
`plano`).

Configure no mesmo painel um webhook (Notificações IPN/Webhooks) pra:

```
https://seudominio.com/assinatura/webhook/mercadopago/
```

com o tópico `preapproval` habilitado. Não precisa de segredo/cabeçalho
customizado aqui — o webhook identifica a igreja/plano pelo
`external_reference` (`CHURCH-<id>-<plano>`) que a própria plataforma
embutiu ao criar a assinatura, e sempre reconsulta a API antes de mudar
`Church.status` (nunca confia no corpo do POST).

**Nota de honestidade**: mesmo padrão das outras integrações de gateway
deste projeto — a API de Preapproval foi implementada seguindo a
documentação pública do Mercado Pago, mas nunca chamada com uma
credencial de produção real; só o caminho de erro (token inválido) foi
verificado ao vivo.

## 6. Monitoramento e endpoints extras

- **Health check**: `GET /health/` — sem autenticação, confirma processo +
  banco acessíveis (`{"status": "ok", "database": true}`, HTTP 503 se o
  banco falhar). Aponte o monitor de uptime (UptimeRobot, o healthcheck do
  Docker, etc.) pra cá.
- **Rastreamento de erros (Sentry, opcional)**: defina `SENTRY_DSN` no
  `.env` e instale `sentry-sdk` (`pip install sentry-sdk`, comentado em
  `requirements.txt`). Sem `SENTRY_DSN`, nada é importado/inicializado —
  zero impacto se você não usar.
- **Notificações push (opcional)**: gere um par de chaves VAPID (ex.: `npx
  web-push generate-vapid-keys` ou a lib Python `py-vapid`) e preencha
  `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY`/`VAPID_CLAIMS_EMAIL` no `.env`,
  além de instalar `pywebpush` (comentado em `requirements.txt`). Sem
  isso, o botão "Ativar notificações" do Portal do Membro nem aparece
  (`vapid_public_key` vazio no template).
- **Rate limit de login/formulários públicos**: usa o cache do Django
  (`LocMemCache`, por processo). Com um único worker (o cenário normal
  pra uma igreja num VPS pequeno) o limite funciona certinho; com vários
  workers/gunicorn cada um conta separado, então o limite real vira
  `limite × nº de workers` — troque por Redis (`django-redis` em
  `CACHES`) se isso importar no seu deploy.

## 7. Depois de qualquer deploy novo

```bash
git pull
venv/bin/pip install -r requirements.txt
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart gunicorn   # ou reload na aba Web do PythonAnywhere
```
