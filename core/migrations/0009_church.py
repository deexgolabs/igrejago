import django.utils.timezone
from django.db import migrations, models
from django.utils.text import slugify


def preencher_slug(apps, schema_editor):
    Church = apps.get_model("core", "Church")
    for church in Church.objects.all():
        if church.slug:
            continue
        base = slugify(church.name) or "igreja"
        slug = base
        suffix = 1
        while Church.objects.filter(slug=slug).exclude(pk=church.pk).exists():
            suffix += 1
            slug = f"{base}-{suffix}"
        church.slug = slug
        if not church.whatsapp_instance:
            church.whatsapp_instance = f"igreja-{slug}"
        church.save(update_fields=["slug", "whatsapp_instance"])


def preencher_slug_reverse(apps, schema_editor):
    pass  # nada a desfazer — os campos em si somem no reverse do AddField/RemoveField desta migração


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_churchconfig_admin_alert_emails_and_more"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="ChurchConfig",
            new_name="Church",
        ),
        migrations.AlterModelOptions(
            name="church",
            options={"ordering": ["name"], "verbose_name": "Igreja", "verbose_name_plural": "Igrejas"},
        ),
        migrations.AddField(
            model_name="church",
            name="slug",
            field=models.SlugField(blank=True, default="", max_length=170, verbose_name="Slug"),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="church",
            name="slug",
            field=models.SlugField(blank=True, max_length=170, unique=True, verbose_name="Slug"),
        ),
        migrations.AddField(
            model_name="church",
            name="status",
            field=models.CharField(
                choices=[("trial", "Em teste"), ("ativo", "Ativo"), ("suspenso", "Suspenso")],
                default="ativo", max_length=10, verbose_name="Status",
            ),
        ),
        migrations.AddField(
            model_name="church",
            name="plano",
            field=models.CharField(blank=True, max_length=20, verbose_name="Plano"),
        ),
        migrations.AddField(
            model_name="church",
            name="trial_expira_em",
            field=models.DateField(blank=True, null=True, verbose_name="Trial expira em"),
        ),
        migrations.AddField(
            model_name="church",
            name="gateway_customer_id",
            field=models.CharField(blank=True, max_length=100, verbose_name="ID do cliente no gateway"),
        ),
        migrations.AddField(
            model_name="church",
            name="gateway_subscription_id",
            field=models.CharField(blank=True, max_length=100, verbose_name="ID da assinatura no gateway"),
        ),
        migrations.AddField(
            model_name="church",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True, default=django.utils.timezone.now, verbose_name="Criada em"
            ),
            preserve_default=False,
        ),
        migrations.RemoveField(
            model_name="church",
            name="whatsapp_api_key",
        ),
        migrations.RemoveField(
            model_name="church",
            name="whatsapp_api_url",
        ),
        migrations.RunPython(preencher_slug, preencher_slug_reverse),
    ]
