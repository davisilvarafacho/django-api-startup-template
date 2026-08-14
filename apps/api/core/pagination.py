from rest_framework.pagination import PageNumberPagination, _positive_int
from rest_framework.response import Response


class CustomPagination(PageNumberPagination):
    page_size_query_param = "size"

    def get_paginated_response(self, data):
        return Response(
            {
                "total": self.page.paginator.count,
                "proxima": self.get_next_link(),
                "anterior": self.get_previous_link(),
                "resultados": data,
            }
        )

    def get_paginated_response_schema(self, schema):
        """Descreve no OpenAPI o envelope que `get_paginated_response()` devolve.

        O default do DRF documenta `count`/`next`/`previous`/`results`, nomes que
        esta classe não usa — sem este override o schema publicado descreveria um
        corpo que a API nunca devolve, em **todo** endpoint paginado.

        Args:
            schema: Schema da lista de resultados, montado pelo drf-spectacular.

        Returns:
            O schema do objeto que envolve a lista.
        """
        return {
            "type": "object",
            "required": ["total", "resultados"],
            "properties": {
                "total": {
                    "type": "integer",
                    "example": 123,
                },
                "proxima": {
                    "type": "string",
                    "nullable": True,
                    "format": "uri",
                    "example": f"http://api.example.org/accounts/?{self.page_query_param}=4",
                },
                "anterior": {
                    "type": "string",
                    "nullable": True,
                    "format": "uri",
                    "example": f"http://api.example.org/accounts/?{self.page_query_param}=2",
                },
                "resultados": schema,
            },
        }

    def get_page_size(self, request):
        if self.page_size_query_param:
            try:
                request_page_size = request.query_params[self.page_size_query_param]
                if request_page_size == "all":
                    return 7**10

                return _positive_int(request_page_size, strict=True, cutoff=self.max_page_size)
            except (KeyError, ValueError):
                pass

        return self.page_size
