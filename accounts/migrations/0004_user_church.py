from django.db import migrations, models
import django.db.models.deletion


def preencher_church(apps, schema_editor):
    """Toda conta existente (inclusive as `is_staff` de hoje) fica ligada
    à igreja única já existente — vira "dono técnico DAQUELA igreja", não
    super-admin de plataforma sozinha. Um super-admin de verdade
    (`church=None`) é criado manualmente depois."""
    User = apps.get_model("accounts", "User")
    Church = apps.get_model("core", "Church")
    igreja = Church.objects.order_by("pk").first()
    if igreja is not None:
        User.objects.filter(church__isnull=True).update(church=igreja)


def preencher_church_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_church"),
        ("accounts", "0003_totpdevice"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="church",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name="users", to="core.church", verbose_name="Igreja",
            ),
        ),
        migrations.RunPython(preencher_church, preencher_church_reverse),
    ]
