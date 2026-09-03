"""Migração de DADO (não só schema): pra cada `Church` que já tinha uma
instância Evolution conectada (`whatsapp_instance` preenchido), cria UM
`WhatsAppInstance` copiando os valores — o NOME da instância é copiado
IDÊNTICO (nunca gerado de novo aqui), porque é o nome real já registrado
no servidor Evolution compartilhado; renomear desconectaria quem já
escaneou o QR code. Igreja sem `whatsapp_instance` preenchido (nunca
conectou) não ganha nada — só cria a própria instância na primeira vez
que clicar em "Adicionar número".

Usa o model histórico (`apps.get_model`), não o `notifications.models`
atual — por isso monta TODOS os campos na mão aqui em vez de confiar no
`save()` do model real (o histórico não carrega os métodos Python
customizados, só os campos)."""

import secrets

from django.db import migrations


def migrar_instancias(apps, schema_editor):
    Church = apps.get_model("core", "Church")
    WhatsAppInstance = apps.get_model("notifications", "WhatsAppInstance")

    for church in Church.objects.exclude(whatsapp_instance=""):
        WhatsAppInstance.objects.create(
            church=church,
            name="WhatsApp da igreja",
            whatsapp_instance=church.whatsapp_instance,
            whatsapp_instance_token=church.whatsapp_instance_token,
            webhook_secret=church.whatsapp_webhook_secret or secrets.token_hex(32),
            send_interval_seconds=church.whatsapp_send_interval_seconds,
            batch_size=church.whatsapp_batch_size,
            max_retries=church.whatsapp_max_retries,
            is_default=True,
        )


def reverter(apps, schema_editor):
    # Reversível de forma simples: apaga as instâncias que essa
    # migração criou (todas nascem is_default=True e "WhatsApp da
    # igreja" — mesma condição usada pra criar).
    WhatsAppInstance = apps.get_model("notifications", "WhatsAppInstance")
    WhatsAppInstance.objects.filter(name="WhatsApp da igreja", is_default=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0010_whatsappinstance_whatsappmessage_instance"),
        ("core", "0019_church_whatsapp_max_instancias_and_more"),
    ]

    # Sem isso, o `dependencies` acima só garante "depois do 0019" — nada
    # impede o executor de migração de aplicar toda a cadeia da `core`
    # (inclusive o 0020, que REMOVE `whatsapp_instance`/`whatsapp_instance_token`/
    # `whatsapp_webhook_secret`) antes de rodar esta migração de dado, já
    # que não há dependência cruzada forçando a ordem entre apps
    # diferentes além da declarada. Descoberto ao rodar a suíte: toda
    # criação de banco de teste quebrava com "Cannot resolve keyword
    # 'whatsapp_instance' into field" — o histórico de migração já tinha
    # o campo removido antes desta rodar.
    run_before = [
        ("core", "0020_remove_church_whatsapp_instance_and_more"),
    ]

    operations = [
        migrations.RunPython(migrar_instancias, reverter),
    ]
