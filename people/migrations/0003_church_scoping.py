from django.db import migrations, models
import django.db.models.deletion


def preencher_church(apps, schema_editor):
    Church = apps.get_model("core", "Church")
    igreja = Church.objects.order_by("pk").first()
    if igreja is None:
        return
    for nome_model in ("Department", "Family", "Person", "Tag"):
        Model = apps.get_model("people", nome_model)
        Model.objects.filter(church__isnull=True).update(church=igreja)


def preencher_church_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_church"),
        ("people", "0002_family_tag_person_pipeline_stage_person_family_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="department",
            name="church",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AddField(
            model_name="family",
            name="church",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AddField(
            model_name="person",
            name="church",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AddField(
            model_name="tag",
            name="church",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.RunPython(preencher_church, preencher_church_reverse),
        migrations.AlterField(
            model_name="department",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AlterField(
            model_name="family",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AlterField(
            model_name="person",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AlterField(
            model_name="tag",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AlterField(
            model_name="department",
            name="name",
            field=models.CharField(max_length=100, verbose_name="Nome"),
        ),
        migrations.AlterField(
            model_name="tag",
            name="name",
            field=models.CharField(max_length=50, verbose_name="Nome"),
        ),
        migrations.AddConstraint(
            model_name="department",
            constraint=models.UniqueConstraint(fields=["church", "name"], name="unique_department_name_per_church"),
        ),
        migrations.AddConstraint(
            model_name="tag",
            constraint=models.UniqueConstraint(fields=["church", "name"], name="unique_tag_name_per_church"),
        ),
    ]
