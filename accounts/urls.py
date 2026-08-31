from django.contrib.auth import views as auth_views
from django.urls import path

from accounts.views import (
    RateLimitedLoginView,
    TOTPDisableView,
    TOTPSetupView,
    TOTPStatusView,
    TOTPVerifyView,
)

app_name = "accounts"

urlpatterns = [
    path("login/", RateLimitedLoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("2fa/", TOTPStatusView.as_view(), name="totp_status"),
    path("2fa/configurar/", TOTPSetupView.as_view(), name="totp_setup"),
    path("2fa/desativar/", TOTPDisableView.as_view(), name="totp_disable"),
    path("2fa/verificar/", TOTPVerifyView.as_view(), name="totp_verify"),
    path(
        "senha/esqueci/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/password_reset_form.html",
            email_template_name="accounts/password_reset_email.html",
            subject_template_name="accounts/password_reset_subject.txt",
            success_url="/accounts/senha/esqueci/enviado/",
        ),
        name="password_reset",
    ),
    path(
        "senha/esqueci/enviado/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "senha/redefinir/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url="/accounts/senha/redefinir/concluido/",
        ),
        name="password_reset_confirm",
    ),
    path(
        "senha/redefinir/concluido/",
        auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"),
        name="password_reset_complete",
    ),
]
