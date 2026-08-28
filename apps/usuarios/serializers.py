from rest_framework import serializers


class EmailTokenSerializer(serializers.Serializer):
    token = serializers.CharField(write_only=True, trim_whitespace=False)


class EmailResendSerializer(serializers.Serializer):
    email = serializers.EmailField()


class EmailChangeSerializer(serializers.Serializer):
    email = serializers.EmailField()
