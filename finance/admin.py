from django.contrib import admin

from finance.models import Budget, ContaContabil, Donation, RecurringPledge, Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "type", "category", "amount", "person", "payment_method", "conta_contabil", "conta_contrapartida")
    list_filter = ("type", "category", "payment_method")
    search_fields = ("description", "person__full_name")
    date_hierarchy = "date"


@admin.register(ContaContabil)
class ContaContabilAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "tipo", "parent", "saldo_inicial", "is_active")
    list_filter = ("tipo", "is_active")
    search_fields = ("code", "name")


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ("category", "year", "month", "target_amount")
    list_filter = ("year", "month", "category")


@admin.register(RecurringPledge)
class RecurringPledgeAdmin(admin.ModelAdmin):
    list_display = ("person", "monthly_amount", "due_day", "active")
    list_filter = ("active",)
    search_fields = ("person__full_name",)


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = ("person", "amount", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("person__full_name",)
