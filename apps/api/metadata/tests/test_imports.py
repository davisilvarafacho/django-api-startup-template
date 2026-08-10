from apps.api.base.models import Base
from apps.api.metadata.models import Metadata


def test_metadata_importa_base_pelo_modulo_canonico():
    assert issubclass(Metadata, Base)
