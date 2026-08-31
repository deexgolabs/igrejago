from django.db import migrations, models
import django.db.models.deletion


def preencher_church(apps, schema_editor):
    Church = apps.get_model("core", "Church")
    igreja = Church.objects.order_by("pk").first()
    if igreja is None:
        return
    for nome_model in ("Transaction", "Budget", "RecurringPledge", "Donation"):
        Model = apps.get_model("finance", nome_model)
        Model.objects.filter(church__isnull=True).update(church=igreja)


def preencher_church_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_church"),
        ("finance", "0003_donation_recurringpledge"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="budget",
            name="unique_budget_per_month",
        ),
        migrations.AddField(
            model_name="transaction",
            name="church",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AddField(
            model_name="budget",
            name="church",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AddField(
            model_name="recurringpledge",
            name="church",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AddField(
            model_name="donation",
            name="church",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.RunPython(preencher_church, preencher_church_reverse),
        migrations.AlterField(
            model_name="transaction",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AlterField(
            model_name="budget",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AlterField(
            model_name="recurringpledge",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AlterField(
            model_name="donation",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="core.church", verbose_name="Igreja"
            ),
        ),
        migrations.AddConstraint(
            model_name="budget",
            constraint=models.UniqueConstraint(
                fields=["church", "category", "year", "month"], name="unique_budget_per_month"
            ),
        ),
    ]
