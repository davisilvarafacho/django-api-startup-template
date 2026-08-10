from rest_framework import serializers

import serpy


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
    InternalFieldsSerializerMixin,
    ReadOnlyFieldsSerializerMixin,
    WriteOnlyFieldsSerializerMixin,
    serializers.ModelSerializer,
):
    pass


class BaseModelSerpySerializer(serpy.Serializer):
    id = serpy.IntField()
