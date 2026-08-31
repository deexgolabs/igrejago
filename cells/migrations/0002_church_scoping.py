from django.db import migrations, models
import django.db.models.deletion


def preencher_church(apps, schema_editor):
    Church = apps.get_model("core", "Church")
    igreja = Church.objects.order_by("pk").first()
    if igreja is None:
        return
    for nome_model in ("Cell", "CellMeeting"):
        Model = apps.get_model("cells", nome_model)
        Model.objects.filter(church__isnull=True).update(church=igreja)


def preencher_church_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_church"),
        ("cells", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="cell",
            name="church",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AddField(
            model_name="cellmeeting",
            name="church",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.RunPython(preencher_church, preencher_church_reverse),
        migrations.AlterField(
            model_name="cell",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AlterField(
            model_name="cellmeeting",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
    ]
