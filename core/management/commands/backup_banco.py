"""Backup do banco (só SQLite — em Postgres, use `pg_dump` direto, fora do
Django) e da pasta `media/` (fotos de pessoas, capas de evento, anexos de
formulário, comprovantes de doação — tudo que não está no banco em si),
com rotação: mantém só os N mais recentes de cada um. Pensado pra rodar
1x/dia via cron/Task Scheduler, igual o `enviar_lembretes`.

Sem o backup de mídia, um restore do banco sozinho deixaria todo mundo com
"pessoa sem foto"/"formulário com link de anexo quebrado" — os dois juntos
é que realmente recuperam o estado do sistema."""

import shutil
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Copia db.sqlite3 e zipa media/ para backups/, mantendo só os --keep mais recentes de cada."

    def add_arguments(self, parser):
        parser.add_argument("--keep", type=int, default=14, help="Quantos backups manter de cada tipo (padrão: 14).")
        parser.add_argument("--no-media", action="store_true", help="Pula o backup da pasta media/.")

    def handle(self, *args, **options):
        backups_dir = settings.BASE_DIR / "backups"
        backups_dir.mkdir(exist_ok=True)
        keep = options["keep"]

        # Em Postgres, avisa e segue pro backup de mídia em vez de abortar
        # o comando inteiro — os dois backups são independentes, e `media/`
        # não depende do engine do banco.
        if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
            self.stdout.write(self.style.WARNING(
                "Banco não é SQLite — pulando backup do banco (use pg_dump diretamente)."
            ))
        else:
            self._backup_database(backups_dir, keep)

        if not options["no_media"]:
            self._backup_media(backups_dir, keep)

    def _backup_database(self, backups_dir, keep):
        db_path = Path(settings.DATABASES["default"].get("NAME"))
        if not db_path.exists():
            raise CommandError(f"Banco não encontrado em {db_path}")

        # Microssegundos incluídos de propósito: duas execuções no mesmo
        # segundo (manual, logo em seguida uma da outra) gerariam o MESMO
        # nome de arquivo com só "%Y%m%d-%H%M%S" — a segunda sobrescreveria
        # a primeira em silêncio em vez de virar um backup novo.
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = backups_dir / f"db-{timestamp}.sqlite3"
        shutil.copy2(db_path, destination)
        self.stdout.write(self.style.SUCCESS(f"Backup do banco salvo em {destination}"))
        self._rotate(backups_dir, "db-*.sqlite3", keep)

    def _backup_media(self, backups_dir, keep):
        media_root = Path(settings.MEDIA_ROOT)
        if not media_root.exists() or not any(media_root.iterdir()):
            self.stdout.write("Pasta media/ vazia ou inexistente — nada para arquivar.")
            return

        # Microssegundos incluídos de propósito: duas execuções no mesmo
        # segundo (manual, logo em seguida uma da outra) gerariam o MESMO
        # nome de arquivo com só "%Y%m%d-%H%M%S" — a segunda sobrescreveria
        # a primeira em silêncio em vez de virar um backup novo.
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination_base = backups_dir / f"media-{timestamp}"
        archive_path = shutil.make_archive(str(destination_base), "zip", root_dir=media_root)
        self.stdout.write(self.style.SUCCESS(f"Backup de media/ salvo em {archive_path}"))
        self._rotate(backups_dir, "media-*.zip", keep)

    def _rotate(self, backups_dir, pattern, keep):
        existing = sorted(backups_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        for old_backup in existing[keep:]:
            old_backup.unlink()
            self.stdout.write(f"Removido backup antigo: {old_backup.name}")
