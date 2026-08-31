from django.db import migrations, models
import django.db.models.deletion


def preencher_church(apps, schema_editor):
    Church = apps.get_model("core", "Church")
    igreja = Church.objects.order_by("pk").first()
    if igreja is None:
        return
    Event = apps.get_model("events", "Event")
    Registration = apps.get_model("events", "Registration")
    Event.objects.filter(church__isnull=True).update(church=igreja)
    Registration.objects.filter(church__isnull=True).update(church=igreja)


def preencher_church_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_church"),
        ("events", "0002_event_brand_color_event_extra_info_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="church",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AddField(
            model_name="registration",
            name="church",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.RunPython(preencher_church, preencher_church_reverse),
        migrations.AlterField(
            model_name="event",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AlterField(
            model_name="registration",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AlterField(
            model_name="event",
            name="slug",
            field=models.SlugField(blank=True, max_length=220, verbose_name="Slug"),
        ),
        migrations.AddConstraint(
            model_name="event",
            constraint=models.UniqueConstraint(fields=["church", "slug"], name="unique_event_slug_per_church"),
        ),
    ]
