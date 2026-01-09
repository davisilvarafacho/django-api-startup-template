from django.core.exceptions import ObjectDoesNotExist
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from rest_framework.serializers import PrimaryKeyRelatedField, RelatedField

default_error_messages = {
    "required": _("This field is required."),
    "does_not_exist": _("{model_name} com ID '{pk_value}' não encontrado."),
    "incorrect_type": _("Tipo incorreto. Esperava um int, mas recebeu: {data_type}."),
}

def get_json_label(x):
    type_mapping = {
        'int': 'int',
        'str': 'string',
        'float': 'number',
        'bool': 'boolean',
        'list': 'array',
        'tuple': 'array',
        'dict': 'object',
        'NoneType': 'null'
    }
    return type_mapping.get(type(x).__name__, 'unknown')


def to_internal_value(self, data):
    if self.pk_field is not None:
        data = self.pk_field.to_internal_value(data)
    queryset = self.get_queryset()

    model_verbose = capfirst(queryset.model._meta.verbose_name)

    try:
        if isinstance(data, bool):
            raise TypeError()
        return queryset.get(pk=data)
    except ObjectDoesNotExist:
        # passa o verbose_name para a mensagem de erro
        self.fail("does_not_exist", model_name=model_verbose, pk_value=data)
    except (TypeError, ValueError):
        self.fail("incorrect_type", data_type=get_json_label(data))


RelatedField.default_error_messages = default_error_messages
RelatedField.to_internal_value = to_internal_value
RelatedField._sign = "base"

PrimaryKeyRelatedField.default_error_messages = default_error_messages
PrimaryKeyRelatedField.to_internal_value = to_internal_value
PrimaryKeyRelatedField._sign = "base"
