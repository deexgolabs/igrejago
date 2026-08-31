"""Modelos prontos pra começar um formulário sem montar do zero —
`CustomFormFromStarterView` copia um destes num `CustomForm` real
(inativo até a igreja revisar/ativar). Não é um model, é só um catálogo
fixo em código; se crescer muito vale virar dado, mas por enquanto um
dict resolve."""

STARTER_TEMPLATES = {
    "oracao": {
        "title": "Pedido de Oração",
        "form_defaults": {},
        "fields": [
            {"label": "Nome", "field_type": "NAME", "required": True, "is_name_field": True},
            {"label": "WhatsApp (opcional)", "field_type": "PHONE", "required": False, "is_phone_field": True},
            {"label": "Seu pedido de oração", "field_type": "TEXTAREA", "required": True},
        ],
    },
    "batismo": {
        "title": "Inscrição para Batismo",
        "form_defaults": {},
        "fields": [
            {"label": "Nome completo", "field_type": "NAME", "required": True, "is_name_field": True},
            {"label": "WhatsApp", "field_type": "PHONE", "required": True, "is_phone_field": True},
            {"label": "Data de nascimento", "field_type": "BIRTH_DATE", "required": True},
            {"label": "Já é membro da igreja?", "field_type": "YES_NO", "required": True},
        ],
    },
    "cadastro": {
        "title": "Atualização de Cadastro",
        "form_defaults": {"sync_to_person": True},
        "fields": [
            {"label": "Nome completo", "field_type": "NAME", "required": True, "is_name_field": True},
            {"label": "WhatsApp", "field_type": "PHONE", "required": True, "is_phone_field": True},
            {"label": "E-mail", "field_type": "EMAIL", "required": False},
            {"label": "Data de nascimento", "field_type": "BIRTH_DATE", "required": False},
            {"label": "Endereço", "field_type": "ADDRESS", "required": False},
            {"label": "Cidade", "field_type": "CITY", "required": False},
            {"label": "Estado", "field_type": "STATE", "required": False},
        ],
    },
}
