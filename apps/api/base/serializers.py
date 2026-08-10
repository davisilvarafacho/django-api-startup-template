from rest_framework import serializers

import serpy


class BaseModelSerializer(serializers.ModelSerializer):
    def __init__(self, instance=None, data=serializers.empty, **kwargs):
        ignore_read_only = kwargs.pop("ignore_read_only", [])
        ignore_internal = kwargs.pop("ignore_internal", [])

        additional_read_only = kwargs.pop("additional_read_only", [])
        additional_internal = kwargs.pop("additional_internal", [])
        additional_write_only = kwargs.pop("additional_write_only", [])

        super().__init__(instance, data, **kwargs)

        model = self.Meta.model

        read_only_fields = model.get_read_only_fields()
        read_only_fields.extend(additional_read_only)

        internal_fields = model.get_internal_fields()
        internal_fields.extend(additional_internal)

        write_only_fields = model.get_write_only_fields()
        write_only_fields.extend(additional_write_only)

        for field_name in internal_fields:
            if field_name in self.fields and field_name not in ignore_internal:
                self.fields.pop(field_name)

        for field_name in read_only_fields:
            if field_name in self.fields and field_name not in ignore_read_only:
                self.fields[field_name].read_only = True

        for field_name in write_only_fields:
            if field_name in self.fields:
                self.fields[field_name].write_only = True


class BaseModelSerpySerializer(serpy.Serializer):
    id = serpy.IntField()
