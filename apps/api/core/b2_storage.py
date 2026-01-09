import mimetypes
from io import BytesIO
from urllib.parse import urljoin

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import File
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible

from b2sdk.v2 import B2Api, B2HttpApiConfig, InMemoryAccountInfo
from b2sdk.v2.exception import (
    B2Error,
    FileNotPresent,
)


@deconstructible
class BackblazeB2Storage(Storage):
    """Storage backend para Backblaze B2 usando b2sdk.

    Settings necessárias:
    - B2_APPLICATION_KEY_ID
    - B2_APPLICATION_KEY
    - B2_BUCKET_NAME
    - B2_BUCKET_ID (opcional, melhora performance)
    - B2_ENDPOINT_URL (opcional, para usar endpoint customizado)
    """

    def __init__(self, **kwargs):
        try:
            self.application_key_id = kwargs.get('application_key_id', settings.B2_APPLICATION_KEY_ID)
            self.application_key = kwargs.get('application_key', settings.B2_APPLICATION_KEY)
            self.bucket_name = kwargs.get('bucket_name', settings.B2_BUCKET_NAME)
        except AttributeError as e:
            raise ImproperlyConfigured(f"Missing Backblaze B2 configuration: {str(e)}") from e

        self.bucket_id = kwargs.get('bucket_id', getattr(settings, 'B2_BUCKET_ID', None))
        self.endpoint_url = kwargs.get('endpoint_url', getattr(settings, 'B2_ENDPOINT_URL', None))
        self._bucket = None
        self._api = None

    @property
    def api(self):
        """Lazy initialization da API do B2."""
        if self._api is None:
            info = InMemoryAccountInfo()

            # Configura API com timeout customizado se necessário
            config = B2HttpApiConfig()

            self._api = B2Api(info, api_config=config)
            self._api.authorize_account(
                "production",
                self.application_key_id,
                self.application_key
            )
        return self._api

    @property
    def bucket(self):
        """Lazy initialization do bucket."""
        if self._bucket is None:
            if self.bucket_id:
                self._bucket = self.api.get_bucket_by_id(self.bucket_id)
            else:
                self._bucket = self.api.get_bucket_by_name(self.bucket_name)
        return self._bucket

    def _clean_name(self, name):
        """Remove leading slashes do nome do arquivo."""
        return name.lstrip('/')

    def _open(self, name, mode='rb'):
        """Abre um arquivo do B2."""
        name = self._clean_name(name)

        try:
            download_dest = BytesIO()
            self.bucket.download_file_by_name(name).save(download_dest)
            download_dest.seek(0)
            return File(download_dest, name)
        except FileNotPresent:
            raise FileNotFoundError(f"File not found: {name}")
        except B2Error as e:
            raise IOError(f"Error opening file {name}: {str(e)}")

    def _save(self, name, content):
        """Salva um arquivo no B2."""
        name = self._clean_name(name)

        # Detecta content type
        content_type, _ = mimetypes.guess_type(name)
        if content_type is None:
            content_type = 'application/octet-stream'

        # Prepara file info
        file_info = {}

        try:
            # Se content for um File do Django, pega os bytes
            if hasattr(content, 'read'):
                content.seek(0)
                file_data = content.read()
            else:
                file_data = content

            # Upload do arquivo
            self.bucket.upload_bytes(
                data_bytes=file_data,
                file_name=name,
                content_type=content_type,
                file_infos=file_info
            )

            return name

        except B2Error as e:
            raise IOError(f"Error saving file {name}: {str(e)}")

    def delete(self, name):
        """Deleta um arquivo do B2."""
        name = self._clean_name(name)

        try:
            # Lista versões do arquivo (B2 mantém versionamento)
            file_versions = self.bucket.ls(name, latest_only=False, recursive=False)

            for file_version, _ in file_versions:
                if file_version.file_name == name:
                    self.api.delete_file_version(
                        file_version.id_,
                        file_version.file_name
                    )

        except FileNotPresent:
            pass  # Arquivo já não existe
        except B2Error as e:
            raise IOError(f"Error deleting file {name}: {str(e)}")

    def exists(self, name):
        """Verifica se um arquivo existe no B2."""
        name = self._clean_name(name)

        try:
            # Tenta listar o arquivo específico
            file_versions = list(self.bucket.ls(
                name,
                latest_only=True,
                recursive=False
            ))

            # Verifica se algum arquivo com esse nome existe
            for file_version, _ in file_versions:
                if file_version.file_name == name:
                    return True
            return False

        except B2Error:
            return False

    def listdir(self, path):
        """Lista diretórios e arquivos em um path."""
        path = self._clean_name(path)
        if path and not path.endswith('/'):
            path += '/'

        directories = set()
        files = []

        try:
            for file_version, folder_name in self.bucket.ls(
                    path,
                    latest_only=True,
                    recursive=False
            ):
                if folder_name:
                    # É um diretório
                    dir_name = folder_name.rstrip('/').split('/')[-1]
                    directories.add(dir_name)
                else:
                    # É um arquivo
                    file_name = file_version.file_name
                    if path:
                        file_name = file_name[len(path):]
                    if '/' not in file_name:  # Apenas arquivos diretos, não subpastas
                        files.append(file_name)

            return list(directories), files

        except B2Error as e:
            raise IOError(f"Error listing directory {path}: {str(e)}")

    def size(self, name):
        """Retorna o tamanho do arquivo em bytes."""
        name = self._clean_name(name)

        try:
            file_info = self.bucket.get_file_info_by_name(name)
            return file_info.size
        except FileNotPresent:
            raise FileNotFoundError(f"File not found: {name}")
        except B2Error as e:
            raise IOError(f"Error getting file size {name}: {str(e)}")

    def url(self, name):
        """Retorna a URL pública do arquivo."""
        name = self._clean_name(name)

        # URL pública do B2
        download_url = self.bucket.get_download_url(name)

        # Se tiver endpoint customizado, usa ele
        if self.endpoint_url:
            return urljoin(self.endpoint_url, name)

        return download_url

    def get_accessed_time(self, name):
        """B2 não suporta accessed time."""
        raise NotImplementedError("Backblaze B2 doesn't support accessed time")

    def get_created_time(self, name):
        """Retorna quando o arquivo foi criado."""
        name = self._clean_name(name)

        try:
            file_info = self.bucket.get_file_info_by_name(name)
            return file_info.upload_timestamp / 1000  # B2 retorna em milliseconds
        except FileNotPresent:
            raise FileNotFoundError(f"File not found: {name}")
        except B2Error as e:
            raise IOError(f"Error getting file creation time {name}: {str(e)}")

    def get_modified_time(self, name):
        """Retorna quando o arquivo foi modificado (mesmo que created no B2)."""
        return self.get_created_time(name)
