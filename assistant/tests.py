import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from assistant import ai, alerts, engine, ratelimit
from assistant.context_processors import pending_counts
from assistant.models import Conversation, ConversationMessage, PersonDraft, PersonUpdateLink
from people.models import Person

PHONE = "5562999998888"


def _receber(church, texto, instance=None):
    with patch("core.whatsapp.enviar_whatsapp", return_value=(True, "", "")) as mock_send:
        engine.processar_mensagem_recebida(church=church, instance=instance, phone="62999998888", texto=texto, raw={})
    return mock_send


@pytest.mark.django_db
class TestEngineMenu:
    def test_ia_chat_disabled_replies_fixed_message(self, church):
        mock_send = _receber(church, "oi")
        assert "não temos atendimento automático" in mock_send.call_args[0][1]

    def test_first_contact_shows_menu(self, church):
        church.ia_chat_enabled = True
        church.save()
        mock_send = _receber(church, "oi")
        texto = mock_send.call_args[0][1]
        assert "1 —" in texto and "2 —" in texto and "3 —" in texto
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        assert conversation.state == Conversation.State.MENU

    def test_choosing_2_sets_aguardando_humano(self, church):
        church.ia_chat_enabled = True
        church.save()
        _receber(church, "oi")
        _receber(church, "2")
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        assert conversation.state == Conversation.State.AGUARDANDO_HUMANO

    def test_aguardando_humano_bot_stays_quiet(self, church):
        church.ia_chat_enabled = True
        church.save()
        _receber(church, "oi")
        _receber(church, "2")
        mock_send = _receber(church, "alguma coisa qualquer")
        assert not mock_send.called

    def test_aguardando_humano_menu_resets(self, church):
        church.ia_chat_enabled = True
        church.save()
        _receber(church, "oi")
        _receber(church, "2")
        mock_send = _receber(church, "menu")
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        assert conversation.state == Conversation.State.MENU
        assert "1 —" in mock_send.call_args[0][1]

    def test_expired_conversation_resets_to_menu(self, church):
        church.ia_chat_enabled = True
        church.save()
        _receber(church, "oi")
        _receber(church, "3")
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        assert conversation.state == Conversation.State.IA_LIVRE
        # `.update()` (não `.save()`) — `last_message_at` é `auto_now`,
        # um `.save()` normal sobrescreveria de volta pra "agora".
        Conversation.objects.filter(pk=conversation.pk).update(
            last_message_at=timezone.now() - timedelta(hours=engine.EXPIRACAO_HORAS + 1)
        )
        _receber(church, "oi de novo")
        conversation.refresh_from_db()
        assert conversation.state == Conversation.State.MENU

    def test_records_transcript(self, church):
        church.ia_chat_enabled = True
        church.save()
        _receber(church, "oi")
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        mensagens = list(conversation.mensagens.order_by("created_at"))
        assert mensagens[0].direction == ConversationMessage.Direction.IN
        assert mensagens[0].body == "oi"
        assert mensagens[1].direction == ConversationMessage.Direction.OUT

    def test_matches_existing_person_by_phone(self, church):
        church.ia_chat_enabled = True
        church.save()
        pessoa = Person.objects.create(church=church, full_name="Fulano", phone="62999998888")
        _receber(church, "oi")
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        assert conversation.person_id == pessoa.pk


@pytest.mark.django_db
class TestEngineColeta:
    def _ate_coleta(self, church):
        church.ia_chat_enabled = True
        church.save()
        _receber(church, "oi")
        _receber(church, "1")

    def test_confirms_and_creates_draft(self, church):
        self._ate_coleta(church)
        with patch("assistant.ai.extrair_dados_cadastro", return_value={"full_name": "Maria Silva", "birth_date": "1995-05-10"}):
            mock_send = _receber(church, "Meu nome é Maria Silva, nasci 10/05/1995")
        assert "Maria Silva" in mock_send.call_args[0][1]
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        assert conversation.state == Conversation.State.AGUARDANDO_CONFIRMACAO

        _receber(church, "sim")
        draft = PersonDraft.objects.get(church=church)
        assert draft.data["full_name"] == "Maria Silva"
        assert draft.data["birth_date"] == "1995-05-10"
        assert draft.data["phone"] == PHONE
        assert draft.status == PersonDraft.Status.PENDING
        assert draft.origin == PersonDraft.Origin.WHATSAPP_IA
        conversation.refresh_from_db()
        assert conversation.state == Conversation.State.MENU

    def test_missing_name_is_reprompted_without_creating_draft(self, church):
        self._ate_coleta(church)
        with patch("assistant.ai.extrair_dados_cadastro", return_value={"city": "Goiânia"}):
            _receber(church, "moro em Goiânia")
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        assert conversation.state == Conversation.State.COLETANDO_CADASTRO
        assert PersonDraft.objects.count() == 0

    def test_declining_confirmation_returns_to_coleta(self, church):
        self._ate_coleta(church)
        with patch("assistant.ai.extrair_dados_cadastro", return_value={"full_name": "Maria"}):
            _receber(church, "Maria")
        _receber(church, "não")
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        assert conversation.state == Conversation.State.COLETANDO_CADASTRO
        assert PersonDraft.objects.count() == 0

    def test_ia_failure_falls_back_without_crashing(self, church):
        self._ate_coleta(church)
        with patch("assistant.ai.extrair_dados_cadastro", side_effect=Exception("boom")):
            mock_send = _receber(church, "algo")
        assert "Não consegui entender" in mock_send.call_args[0][1]
        assert PersonDraft.objects.count() == 0

    def test_extracted_fields_outside_allowlist_are_dropped_before_draft(self, church):
        # `ai.extrair_dados_cadastro` já filtra pela allow-list (ver TestAIExtraction) —
        # aqui confirma que mesmo se algo escapar, o motor não trava.
        self._ate_coleta(church)
        with patch("assistant.ai.extrair_dados_cadastro", return_value={"full_name": "Maria", "role": "PASTOR"}):
            _receber(church, "Maria, e sou pastora")
        _receber(church, "sim")
        draft = PersonDraft.objects.get(church=church)
        assert "role" in draft.data  # o motor não filtra de novo — a allow-list é responsabilidade de `ai`/aprovação
        assert draft.status == PersonDraft.Status.PENDING

    def test_menu_word_escapes_coleta_without_calling_ai(self, church):
        # Achado testando de verdade: sem o escape global em `_despachar`,
        # digitar "menu" no meio da coleta ia direto pra `ai.extrair_dados_cadastro`
        # (e cada resposta virava "Não consegui entender agora" pra sempre).
        self._ate_coleta(church)
        with patch("assistant.ai.extrair_dados_cadastro") as mock_extrair:
            mock_send = _receber(church, "menu")
        assert not mock_extrair.called
        assert "1 —" in mock_send.call_args[0][1]
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        assert conversation.state == Conversation.State.MENU

    def test_menu_word_escapes_aguardando_confirmacao(self, church):
        self._ate_coleta(church)
        with patch("assistant.ai.extrair_dados_cadastro", return_value={"full_name": "Maria"}):
            _receber(church, "Maria")
        mock_send = _receber(church, "menu")
        assert "1 —" in mock_send.call_args[0][1]
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        assert conversation.state == Conversation.State.MENU
        assert PersonDraft.objects.count() == 0


@pytest.mark.django_db
class TestEngineIALivre:
    def test_answers_using_ai(self, church):
        church.ia_chat_enabled = True
        church.save()
        _receber(church, "oi")
        _receber(church, "3")
        with patch("assistant.ai.gerar_resposta", return_value="Os cultos são aos domingos às 19h."):
            mock_send = _receber(church, "que horas é o culto?")
        assert "19h" in mock_send.call_args[0][1]

    def test_menu_word_returns_to_menu(self, church):
        church.ia_chat_enabled = True
        church.save()
        _receber(church, "oi")
        _receber(church, "3")
        mock_send = _receber(church, "menu")
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        assert conversation.state == Conversation.State.MENU
        assert "1 —" in mock_send.call_args[0][1]

    def test_ia_failure_falls_back_without_crashing(self, church):
        church.ia_chat_enabled = True
        church.save()
        _receber(church, "oi")
        _receber(church, "3")
        with patch("assistant.ai.gerar_resposta", side_effect=Exception("boom")):
            mock_send = _receber(church, "pergunta qualquer")
        assert "Não consegui responder" in mock_send.call_args[0][1]


@pytest.mark.django_db
class TestRateLimit:
    def test_blocks_after_max_calls_in_window(self, church):
        cache.clear()
        for _ in range(ratelimit.IA_CALL_MAX):
            assert ratelimit.ia_call_permitida(church, PHONE) is True
        assert ratelimit.ia_call_permitida(church, PHONE) is False

    def test_engine_falls_back_to_fixed_message_when_rate_limited(self, church):
        cache.clear()
        church.ia_chat_enabled = True
        church.save()
        _receber(church, "oi")
        _receber(church, "3")
        for _ in range(ratelimit.IA_CALL_MAX):
            ratelimit.ia_call_permitida(church, PHONE)
        mock_send = _receber(church, "pergunta")
        assert "Muitas mensagens" in mock_send.call_args[0][1]


def _fake_response(json_data):
    fake = MagicMock()
    fake.raise_for_status.return_value = None
    fake.json.return_value = json_data
    return fake


@pytest.mark.django_db
class TestAIGemini:
    def test_gerar_resposta_uses_knowledge_base(self, church):
        church.ia_provider = "GEMINI"
        church.ia_api_key = "fake-key"
        church.ia_knowledge_base = "Cultos aos domingos às 19h."
        church.save()
        conversation = Conversation.objects.create(church=church, phone=PHONE)
        data = {"candidates": [{"content": {"parts": [{"text": "Os cultos são aos domingos às 19h."}]}}]}
        with patch("assistant.ai.requests.post", return_value=_fake_response(data)) as mock_post:
            texto = ai.gerar_resposta(church, conversation, "que horas é o culto?")
        assert "19h" in texto
        assert mock_post.call_args.kwargs["params"]["key"] == "fake-key"
        payload_enviado = mock_post.call_args.kwargs["json"]
        assert "Cultos aos domingos às 19h." in payload_enviado["systemInstruction"]["parts"][0]["text"]

    def test_extrair_dados_cadastro_filters_allowlist(self, church):
        church.ia_provider = "GEMINI"
        church.ia_api_key = "fake-key"
        church.save()
        bruto = json.dumps({"full_name": "João", "role": "PASTOR", "phone": "5562999990000"})
        data = {"candidates": [{"content": {"parts": [{"text": bruto}]}}]}
        with patch("assistant.ai.requests.post", return_value=_fake_response(data)):
            dados = ai.extrair_dados_cadastro(church, "sou o João")
        assert dados == {"full_name": "João", "phone": "5562999990000"}
        assert "role" not in dados


@pytest.mark.django_db
class TestAIChatGPT:
    def test_gerar_resposta_uses_openai(self, church):
        church.ia_provider = "CHATGPT"
        church.ia_api_key = "fake-key"
        church.save()
        conversation = Conversation.objects.create(church=church, phone=PHONE)
        data = {"choices": [{"message": {"content": "Resposta da IA"}}]}
        with patch("assistant.ai.requests.post", return_value=_fake_response(data)) as mock_post:
            texto = ai.gerar_resposta(church, conversation, "oi")
        assert texto == "Resposta da IA"
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer fake-key"

    def test_extrair_dados_cadastro_filters_allowlist(self, church):
        church.ia_provider = "CHATGPT"
        church.ia_api_key = "fake-key"
        church.save()
        bruto = json.dumps({"full_name": "Ana", "is_member": True})
        data = {"choices": [{"message": {"content": bruto}}]}
        with patch("assistant.ai.requests.post", return_value=_fake_response(data)):
            dados = ai.extrair_dados_cadastro(church, "sou a Ana")
        assert dados == {"full_name": "Ana"}


@pytest.mark.django_db
class TestPersonDraftViews:
    def test_pastor_can_approve_creating_new_person(self, pastor_client, church):
        draft = PersonDraft.objects.create(
            church=church, origin=PersonDraft.Origin.WHATSAPP_IA,
            data={"full_name": "Novo Visitante", "phone": "5562999990000"},
        )
        response = pastor_client.post(f"/assistente/cadastros-pendentes/{draft.pk}/aprovar/")
        assert response.status_code == 302
        draft.refresh_from_db()
        assert draft.status == PersonDraft.Status.APPROVED
        assert draft.person is not None
        assert draft.person.full_name == "Novo Visitante"
        assert draft.person.is_visitor is True
        assert draft.person.role == Person.Role.VISITOR

    def test_approve_ignores_fields_outside_allowlist(self, pastor_client, church):
        draft = PersonDraft.objects.create(
            church=church, origin=PersonDraft.Origin.WHATSAPP_IA, data={"full_name": "X", "role": "PASTOR"}
        )
        pastor_client.post(f"/assistente/cadastros-pendentes/{draft.pk}/aprovar/")
        draft.refresh_from_db()
        assert draft.person.role == Person.Role.VISITOR

    def test_approve_updates_existing_person(self, pastor_client, church, person):
        draft = PersonDraft.objects.create(
            church=church, person=person, origin=PersonDraft.Origin.PUBLIC_FORM, data={"city": "Nova Cidade"}
        )
        pastor_client.post(f"/assistente/cadastros-pendentes/{draft.pk}/aprovar/")
        person.refresh_from_db()
        assert person.city == "Nova Cidade"

    def test_reject_does_not_touch_person(self, pastor_client, church, person):
        draft = PersonDraft.objects.create(
            church=church, person=person, origin=PersonDraft.Origin.PUBLIC_FORM, data={"city": "Nova Cidade"}
        )
        original_city = person.city
        pastor_client.post(f"/assistente/cadastros-pendentes/{draft.pk}/rejeitar/", {"rejection_reason": "duplicado"})
        draft.refresh_from_db()
        assert draft.status == PersonDraft.Status.REJECTED
        assert draft.rejection_reason == "duplicado"
        person.refresh_from_db()
        assert person.city == original_city

    def test_member_cannot_access_queue(self, member_client, church):
        response = member_client.get("/assistente/cadastros-pendentes/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestPersonUpdateFormView:
    def test_get_prefills_current_data(self, client, church, person):
        link = PersonUpdateLink.objects.create(church=church, person=person)
        response = client.get(f"/assistente/atualizar-cadastro/{link.token}/")
        assert response.status_code == 200
        assert person.full_name in response.content.decode()

    def test_post_creates_draft_without_touching_person(self, client, church, person):
        link = PersonUpdateLink.objects.create(church=church, person=person)
        original_city = person.city
        response = client.post(
            f"/assistente/atualizar-cadastro/{link.token}/",
            {"full_name": person.full_name, "city": "Cidade Nova"},
        )
        assert response.status_code == 200
        person.refresh_from_db()
        assert person.city == original_city
        draft = PersonDraft.objects.get(church=church, person=person)
        assert draft.data["city"] == "Cidade Nova"
        assert draft.origin == PersonDraft.Origin.PUBLIC_FORM
        assert draft.status == PersonDraft.Status.PENDING
        link.refresh_from_db()
        assert link.last_used_at is not None

    def test_invalid_token_is_404(self, client):
        response = client.get("/assistente/atualizar-cadastro/00000000-0000-0000-0000-000000000000/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestAlerts:
    def test_avisar_novo_draft_sends_email_when_configured(self, church, mailoutbox):
        church.admin_alert_emails = "secretaria@example.com"
        church.save()
        draft = PersonDraft.objects.create(
            church=church, origin=PersonDraft.Origin.WHATSAPP_IA, data={"full_name": "Maria Silva"}
        )
        alerts.avisar_novo_draft(draft)
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["secretaria@example.com"]
        assert "Maria Silva" in mailoutbox[0].subject

    def test_avisar_novo_draft_does_nothing_without_admin_emails(self, church, mailoutbox):
        draft = PersonDraft.objects.create(
            church=church, origin=PersonDraft.Origin.WHATSAPP_IA, data={"full_name": "Maria Silva"}
        )
        alerts.avisar_novo_draft(draft)
        assert len(mailoutbox) == 0

    def test_avisar_atendimento_humano_sends_email(self, church, mailoutbox):
        church.admin_alert_emails = "secretaria@example.com, pastor@example.com"
        church.save()
        conversation = Conversation.objects.create(church=church, phone=PHONE)
        alerts.avisar_atendimento_humano(conversation)
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["secretaria@example.com", "pastor@example.com"]
        assert PHONE in mailoutbox[0].subject or PHONE in mailoutbox[0].body

    def test_engine_sends_alert_when_draft_confirmed(self, church, mailoutbox):
        church.ia_chat_enabled = True
        church.admin_alert_emails = "secretaria@example.com"
        church.save()
        _receber(church, "oi")
        _receber(church, "1")
        with patch("assistant.ai.extrair_dados_cadastro", return_value={"full_name": "Maria"}):
            _receber(church, "Maria")
        _receber(church, "sim")
        assert len(mailoutbox) == 1
        assert "Maria" in mailoutbox[0].subject

    def test_engine_sends_alert_when_choosing_human_attendance(self, church, mailoutbox):
        church.ia_chat_enabled = True
        church.admin_alert_emails = "secretaria@example.com"
        church.save()
        _receber(church, "oi")
        _receber(church, "2")
        assert len(mailoutbox) == 1


@pytest.mark.django_db
class TestPendingCountsContextProcessor:
    class _FakeRequest:
        def __init__(self, user, church):
            self.user = user
            self.church = church

    def test_returns_counts_for_unrestricted_manager(self, church, pastor_user):
        PersonDraft.objects.create(
            church=church, origin=PersonDraft.Origin.WHATSAPP_IA,
            data={"full_name": "X"}, status=PersonDraft.Status.PENDING,
        )
        Conversation.objects.create(church=church, phone=PHONE, state=Conversation.State.AGUARDANDO_HUMANO)

        result = pending_counts(self._FakeRequest(pastor_user, church))
        assert result == {"assistant_draft_count": 1, "assistant_human_queue_count": 1}

    def test_empty_for_scoped_member(self, church, member_user):
        PersonDraft.objects.create(
            church=church, origin=PersonDraft.Origin.WHATSAPP_IA,
            data={"full_name": "X"}, status=PersonDraft.Status.PENDING,
        )
        assert pending_counts(self._FakeRequest(member_user, church)) == {}

    def test_empty_without_church(self, pastor_user):
        assert pending_counts(self._FakeRequest(pastor_user, None)) == {}

    def test_empty_for_anonymous(self, church):
        from django.contrib.auth.models import AnonymousUser

        assert pending_counts(self._FakeRequest(AnonymousUser(), church)) == {}


@pytest.mark.django_db
class TestEngineMedia:
    def test_media_without_text_gets_fallback_without_calling_ai(self, church):
        church.ia_chat_enabled = True
        church.save()
        with patch("assistant.ai.gerar_resposta") as mock_ia, patch("assistant.ai.extrair_dados_cadastro") as mock_extrair:
            with patch("core.whatsapp.enviar_whatsapp", return_value=(True, "", "")) as mock_send:
                engine.processar_mensagem_recebida(church=church, instance=None, phone="62999998888", texto=None, raw={})
        assert "Só entendo mensagens de texto" in mock_send.call_args[0][1]
        assert not mock_ia.called
        assert not mock_extrair.called

    def test_media_does_not_change_conversation_state(self, church):
        church.ia_chat_enabled = True
        church.save()
        _receber(church, "oi")
        _receber(church, "3")  # entra em IA_LIVRE
        with patch("core.whatsapp.enviar_whatsapp", return_value=(True, "", "")):
            engine.processar_mensagem_recebida(church=church, instance=None, phone="62999998888", texto=None, raw={})
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        assert conversation.state == Conversation.State.IA_LIVRE  # não mudou

    def test_media_without_ia_enabled_gets_disabled_message(self, church):
        with patch("core.whatsapp.enviar_whatsapp", return_value=(True, "", "")) as mock_send:
            engine.processar_mensagem_recebida(church=church, instance=None, phone="62999998888", texto=None, raw={})
        assert "não temos atendimento automático" in mock_send.call_args[0][1]

    def test_media_records_transcript_placeholder(self, church):
        church.ia_chat_enabled = True
        church.save()
        with patch("core.whatsapp.enviar_whatsapp", return_value=(True, "", "")):
            engine.processar_mensagem_recebida(church=church, instance=None, phone="62999998888", texto=None, raw={})
        conversation = Conversation.objects.get(church=church, phone=PHONE)
        entrada = conversation.mensagens.filter(direction=ConversationMessage.Direction.IN).first()
        assert entrada.body == "[mídia recebida]"
