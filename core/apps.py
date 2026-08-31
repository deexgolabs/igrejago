from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from core.signals import register_audit_log
        from cells.models import Cell
        from events.models import Event
        from finance.models import Transaction
        from people.models import Person

        for model in (Person, Event, Transaction, Cell):
            register_audit_log(model)
