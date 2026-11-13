from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("kwizzes", "0004_shes_a_witch")]

    operations = [
        migrations.AddField(
            model_name="kwiz",
            name="player_screen",
            field=models.CharField(
                choices=[
                    ("full", "The kwestion and its answers"),
                    ("answers", "Only the answers"),
                    ("shapes", "Only the answer shapes"),
                ],
                default="full",
                help_text="How much of each kwestion players see on their phones.",
                max_length=10,
            ),
        ),
    ]
