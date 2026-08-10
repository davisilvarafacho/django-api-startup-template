from collections.abc import Mapping

from django.core.exceptions import FieldDoesNotExist

from rest_framework import serializers

import serpy


def _forbidden_model_write_names(serializer):
    model = serializer.Meta.model
    configured_names = set(model.get_forbidden_internal_write_fields())
    write_names = set(configured_names)

    for field_name in configured_names:
        try:
            write_names.add(model._meta.get_field(field_name).attname)
        except FieldDoesNotExist:
            continue

    return write_names


def _forbidden_input_names(serializer):
    forbidden_sources = _forbidden_model_write_names(serializer)
    input_names = set(forbidden_sources)

    for field_name, field in serializer.fields.items():
        source_root = field.source.split(".", 1)[0]
        if source_root in forbidden_sources:
            input_names.add(field_name)

    return input_names


def _without_fields(data, field_names):
    if not isinstance(data, Mapping):
        return data

    present_fields = set(data).intersection(field_names)
    if not present_fields:
        return data

    filtered_data = data.copy()
    for field_name in present_fields:
        filtered_data.pop(field_name, None)
    return filtered_data


class ForbiddenInternalWriteFieldsSerializerMixin:
    def to_internal_value(self, data):
        filtered_data = _without_fields(data, _forbidden_input_names(self))
        validated_data = super().to_internal_value(filtered_data)
        return _without_fields(validated_data, _forbidden_model_write_names(self))

    def save(self, **kwargs):
        forbidden_model_names = _forbidden_model_write_names(self)
        forbidden_kwarg_names = forbidden_model_names | _forbidden_input_names(self)
        filtered_kwargs = _without_fields(kwargs, forbidden_kwarg_names)

        if hasattr(self, "_validated_data"):
            self._validated_data = _without_fields(self._validated_data, forbidden_model_names)

        return super().save(**filtered_kwargs)


class InternalFieldsSerializerMixin:
    def __init__(self, *args, **kwargs):
        ignore_internal = kwargs.pop("ignore_internal", ())
        additional_internal = kwargs.pop("additional_internal", ())
        super().__init__(*args, **kwargs)

        internal_fields = [*self.Meta.model.get_internal_fields(), *additional_internal]
        for field_name in internal_fields:
            if field_name in self.fields and field_name not in ignore_internal:
                self.fields.pop(field_name)


class ReadOnlyFieldsSerializerMixin:
    def __init__(self, *args, **kwargs):
        ignore_read_only = kwargs.pop("ignore_read_only", ())
        additional_read_only = kwargs.pop("additional_read_only", ())
        super().__init__(*args, **kwargs)

        read_only_fields = [*self.Meta.model.get_read_only_fields(), *additional_read_only]
        for field_name in read_only_fields:
            if field_name in self.fields and field_name not in ignore_read_only:
                self.fields[field_name].read_only = True


class WriteOnlyFieldsSerializerMixin:
    def __init__(self, *args, **kwargs):
        additional_write_only = kwargs.pop("additional_write_only", ())
        super().__init__(*args, **kwargs)

        write_only_fields = [*self.Meta.model.get_write_only_fields(), *additional_write_only]
        for field_name in write_only_fields:
            if field_name in self.fields:
                self.fields[field_name].write_only = True


class BaseModelSerializer(
    ForbiddenInternalWriteFieldsSerializerMixin,
    InternalFieldsSerializerMixin,
    ReadOnlyFieldsSerializerMixin,
    WriteOnlyFieldsSerializerMixin,
    serializers.ModelSerializer,
):
    pass


class BaseModelSerpySerializer(serpy.Serializer):
    id = serpy.IntField()
