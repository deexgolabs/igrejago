from django.db import migrations, models
import django.db.models.deletion


def preencher_church(apps, schema_editor):
    AuditLog = apps.get_model("core", "AuditLog")
    Church = apps.get_model("core", "Church")
    igreja = Church.objects.order_by("pk").first()
    if igreja is not None:
        AuditLog.objects.filter(church__isnull=True).update(church=igreja)


def preencher_church_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_church"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="church",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE,
                to="core.church", verbose_name="Igreja",
            ),
        ),
        migrations.RunPython(preencher_church, preencher_church_reverse),
        migrations.AlterField(
            model_name="auditlog",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja",
            ),
        ),
    ]
