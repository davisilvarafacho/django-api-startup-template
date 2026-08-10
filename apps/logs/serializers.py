import serpy

from apps.api.base.serializers import BaseModelSerpySerializer


class LogAlteracaoSerpySerializer(BaseModelSerpySerializer):
    user = serpy.MethodField()
    action = serpy.IntField()
    model = serpy.StrField()
    # Nulo quando a PK do objeto auditado não é numérica; nesse caso só
    # `object_pk` identifica o registro.
    object_id = serpy.IntField(required=False)
    object_repr = serpy.StrField()
    # `Field` cru: o diff é um JSON arbitrário, sem forma declarável.
    changes = serpy.Field(required=False)
    created_at = serpy.MethodField()

    def get_user(self, obj):
        if obj.user:
            return {
                "id": obj.user.id,
                "nome": obj.user.get_full_name(),
                "email": obj.user.email,
            }
        return None

    def get_created_at(self, obj):
        return obj.created_at.isoformat()
