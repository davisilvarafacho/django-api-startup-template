"""Validação de formato do corpo de alteração de metadata (não toca o banco)."""

from apps.api.metadata.serializers import MetadataAlteracaoSerializer


def test_aceita_valores_texto_e_nulos():
    serializer = MetadataAlteracaoSerializer(data={"dados": {"erp_id": "X-1", "nota": None}})

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["dados"] == {"erp_id": "X-1", "nota": None}


def test_aceita_documento_vazio():
    serializer = MetadataAlteracaoSerializer(data={"dados": {}})

    assert serializer.is_valid(), serializer.errors


def test_exige_o_campo_dados():
    serializer = MetadataAlteracaoSerializer(data={})

    assert not serializer.is_valid()
    assert "dados" in serializer.errors


def test_rejeita_valor_numerico():
    serializer = MetadataAlteracaoSerializer(data={"dados": {"tentativas": 3}})

    assert not serializer.is_valid()
    assert "dados" in serializer.errors


def test_rejeita_valor_aninhado():
    serializer = MetadataAlteracaoSerializer(data={"dados": {"erp": {"id": "X-1"}}})

    assert not serializer.is_valid()
    assert "dados" in serializer.errors


def test_rejeita_dados_que_nao_sao_objeto():
    serializer = MetadataAlteracaoSerializer(data={"dados": ["erp_id"]})

    assert not serializer.is_valid()
    assert "dados" in serializer.errors
